"""웹 로그인 — 관제 대시보드와 체험·조작 서버의 요청 처리기(BaseHTTPRequestHandler)에 섞어 쓴다.

    class Handler(WebAuthMixin, BaseHTTPRequestHandler):
        auth_title = "ReflexGuard 관제"
        def auth_token(self) -> str: return ...   # 빈 문자열이면 인증 없음

- `POST /login` (폼: token, next, csrf — 로그인 화면의 숨은 칸) → 맞으면 로그인 쿠키를 주고 next로, 틀리면 로그인 화면(401)
- `POST /logout` → 쿠키를 지우고 /로
- `?token=<값>`으로 연 주소 → 쿠키로 바꾸고 주소에서 토큰을 지운 곳으로 넘긴다 (주소창·화면 공유에 남지 않게)
- 모든 응답에 보안 헤더(CSP·틀 넣기 금지 등 — common/web_guard.py). 화면을 줄 때는 `self.csp = page_csp(본문)`
- `guard()` — 요청마다 맨 먼저: IP별 요청 속도(서버의 `rate`)와 인증 실패 잠금(서버의 `auth_failures`)을 본다.
  토큰을 틀리면(로그인 폼·?token=·X-Token) 실패로 세고, 잠긴 주소는 429 (KISA 구현 2-16)
"""

from __future__ import annotations

import hmac
import socket
import threading
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from common.auth import (csrf_token, login_page, logout_cookie, prelogin, prelogin_ok, request_role, role_for, safe_next,
                         session_cookie)
from common.tls import is_loopback, is_secure
from common.web_guard import host_allowed, same_origin, security_headers

LOGIN_BODY_MAX = 4096
REQUEST_DEADLINE_S = 15.0  # 요청 하나(머리·본문)를 이 안에 다 보내야 한다 — 1바이트씩 흘려 붙잡는 접속(slow-drip)을 끊는다


class WebAuthMixin:
    auth_title = "ReflexGuard"
    permissions = "camera=(), microphone=(), geolocation=()"  # Permissions-Policy (체험 서버는 카메라·동작 센서를 연다)
    csp: str | None = None  # 이번 응답의 CSP (화면이면 page_csp, 아니면 스크립트 없는 기본)
    headers: Any
    path: str
    connection: Any
    rfile: Any
    wfile: Any
    close_connection: bool
    client_address: Any
    server: Any

    def auth_token(self) -> str:
        raise NotImplementedError

    def handle_one_request(self) -> None:
        """요청마다 전체 시한을 둔다: 처리기의 timeout은 '한 번 받기'의 대기라, 조금씩 계속 보내면 끝나지 않는다.
        실시간 화면(SSE)·웹소켓 중계처럼 오래 가는 응답은 release_deadline()으로 시한을 푼다."""
        timer = threading.Timer(REQUEST_DEADLINE_S, self._drop_slow_request)
        timer.daemon = True
        self._deadline = timer
        timer.start()
        try:
            super().handle_one_request()  # type: ignore[misc]
        finally:
            timer.cancel()

    def release_deadline(self) -> None:
        deadline = getattr(self, "_deadline", None)
        if deadline is not None:
            deadline.cancel()

    def _drop_slow_request(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def end_headers(self) -> None:
        for name, value in security_headers(self.csp, self._secure(), self.permissions):
            self.send_header(name, value)  # type: ignore[attr-defined]
        self.csp = None  # keep-alive로 이어지는 다음 응답에는 기본 CSP
        super().end_headers()  # type: ignore[misc]

    def _secure(self) -> bool:
        return is_secure(self.connection)

    def redirect_plain(self) -> bool:
        """TLS로 연 서버에 다른 기기가 http://로 왔다 → 같은 주소의 https://로 넘기고 참 (같은 PC는 http 그대로)."""
        if getattr(self.server, "tls", None) is None or self._secure() or is_loopback(self.client_address):
            return False
        if not host_allowed(self.headers):  # 모르는 Host로는 넘기지 않는다 (guard가 거절한다)
            return False
        host = self.headers.get("Host", "") or f"localhost:{self.server.server_address[1]}"
        self.send_response(301)  # type: ignore[attr-defined]
        self.send_header("Location", f"https://{host}{self.path}")  # type: ignore[attr-defined]
        self.send_header("Content-Length", "0")  # type: ignore[attr-defined]
        self.end_headers()  # type: ignore[attr-defined]
        return True

    def _ip(self) -> str:
        return str(self.client_address[0])

    def _failed_login(self) -> None:
        failures = getattr(self.server, "auth_failures", None)
        if failures is not None:
            failures.failed(self._ip())

    def _logged_in(self) -> None:
        failures = getattr(self.server, "auth_failures", None)
        if failures is not None:
            failures.succeeded(self._ip())

    def guard(self) -> bool:
        """요청마다 맨 먼저 부른다. 인증에 거듭 실패해 잠긴 주소이거나 요청이 너무 잦으면 429로 답하고 거짓."""
        if getattr(self, "command", "") in ("GET", "HEAD"):
            self.release_deadline()  # 요청을 다 받았다 — 시한은 받는 동안만 (처리 시간은 재지 않는다)
        if not host_allowed(self.headers):  # 이 서버의 이름이 아닌 Host — DNS 리바인딩 (KISA 구현 1-11)
            self._plain(421, "이 서버의 주소가 아닙니다 (Host). 설정의 REFLEXGUARD_PUBLIC_NAMES를 확인하세요.")
            return False
        failures = getattr(self.server, "auth_failures", None)
        rate = getattr(self.server, "rate", None)
        if failures is not None and failures.locked(self._ip()):
            self._too_many("토큰을 여러 번 틀려 잠시 막았습니다. 1분 뒤 다시 하세요.")
            return False
        if rate is not None and not rate.allow(self._ip()):
            self._too_many("요청이 너무 잦습니다.")
            return False
        return True

    def _too_many(self, message: str) -> None:
        self._plain(429, message, retry_after=True)

    def _plain(self, status: int, message: str, retry_after: bool = False) -> None:
        body = message.encode("utf-8")
        self.close_connection = True  # 본문을 읽지 않으므로 이 연결은 닫는다
        self.send_response(status)  # type: ignore[attr-defined]
        self.send_header("Content-Type", "text/plain; charset=utf-8")  # type: ignore[attr-defined]
        if retry_after:
            self.send_header("Retry-After", "60")  # type: ignore[attr-defined]
        self.send_header("Content-Length", str(len(body)))  # type: ignore[attr-defined]
        self.send_header("Connection", "close")  # type: ignore[attr-defined]
        self.end_headers()  # type: ignore[attr-defined]
        self.wfile.write(body)

    def role(self) -> str | None:
        """이 요청의 권한: 'control' · 'view' · None(로그인 안 함). 틀린 X-Token은 인증 실패로 센다."""
        role = request_role(self.auth_token(), self.headers)
        if role is None and self.headers.get("X-Token"):
            self._failed_login()
        return role

    def csrf_value(self) -> str:
        """이 로그인(쿠키)의 CSRF 값 — 화면이 받아 X-CSRF-Token으로 싣는다."""
        return csrf_token(self.auth_token(), self.headers.get("Cookie", ""))

    def csrf_ok(self, form_value: str = "") -> bool:
        """상태를 바꾸는 요청의 CSRF 확인. 토큰을 쓰지 않는 서버(시험)·X-Token으로 온 도구(브라우저 밖)는 해당 없음."""
        token = self.auth_token()
        if not token or role_for(token, self.headers.get("X-Token", "")):
            return True
        expected = self.csrf_value()
        provided = self.headers.get("X-CSRF-Token", "") or form_value
        return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())

    def send_login(self, next_path: str | None = None, error: str = "", status: int = 401) -> None:
        token = self.auth_token()
        cookie, field = prelogin(token, self._secure(), self.headers.get("Cookie", "")) if token else ("", "")  # type: ignore[attr-defined]
        body = login_page(self.auth_title, self.path if next_path is None else next_path, error, field)
        self.send_response(status)  # type: ignore[attr-defined]
        if cookie:
            self.send_header("Set-Cookie", cookie)  # type: ignore[attr-defined]
        self.send_header("Content-Type", "text/html; charset=utf-8")  # type: ignore[attr-defined]
        self.send_header("Content-Length", str(len(body)))  # type: ignore[attr-defined]
        self.send_header("Cache-Control", "no-store")  # type: ignore[attr-defined]
        self.end_headers()  # type: ignore[attr-defined]
        self.wfile.write(body)

    def _redirect(self, location: str, cookie: str | None) -> None:
        self.send_response(303)  # type: ignore[attr-defined]
        self.send_header("Location", location)  # type: ignore[attr-defined]
        if cookie:
            self.send_header("Set-Cookie", cookie)  # type: ignore[attr-defined]
        self.send_header("Cache-Control", "no-store")  # type: ignore[attr-defined]
        self.send_header("Content-Length", "0")  # type: ignore[attr-defined]
        self.end_headers()  # type: ignore[attr-defined]

    def login_from_query(self) -> bool:
        """주소에 ?token=이 있으면 처리하고 참: 맞으면 쿠키를 주고 토큰을 뺀 주소로, 틀리면 로그인 화면."""
        url = urlparse(self.path)
        query = parse_qs(url.query, keep_blank_values=True)
        if "token" not in query:
            return False
        provided = query.pop("token")[0]
        clean = url.path + (f"?{urlencode(query, doseq=True)}" if query else "")
        token = self.auth_token()
        role = role_for(token, provided) if token else None
        if token and role is None:
            self._failed_login()
            self.send_login(clean, "토큰이 맞지 않습니다")
        else:
            self._logged_in()
            self._redirect(safe_next(clean), session_cookie(token, role, self._secure()) if role else None)
        return True

    def handle_login(self) -> None:
        """POST /login — 같은 화면에서 보낸 폼만 받는다."""
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= LOGIN_BODY_MAX:
            self.close_connection = True
            self.send_login("/", "잘못된 요청입니다", 400)
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        next_path = safe_next(form.get("next", ["/"])[0])
        if not same_origin(self.headers):
            self.send_login(next_path, "다른 사이트에서 보낸 로그인은 받지 않습니다", 403)
            return
        token = self.auth_token()
        if token and not prelogin_ok(token, self.headers.get("Cookie", ""), form.get("csrf", [""])[0]):
            self.send_login(next_path, "로그인 화면이 오래됐습니다. 다시 넣어 주세요.", 403)  # 로그인 전 CSRF (KISA 구현 1-11)
            return
        role = role_for(token, form.get("token", [""])[0]) if token else None
        if token and role is None:
            self._failed_login()
            self.send_login(next_path, "토큰이 맞지 않습니다")
            return
        self._logged_in()
        self._redirect(next_path, session_cookie(token, role, self._secure()) if role else None)

    def handle_logout(self) -> None:
        length = self.headers.get("Content-Length", "0")
        form: dict[str, list[str]] = {}
        if length.isdigit() and 0 < int(length) <= LOGIN_BODY_MAX:
            form = parse_qs(self.rfile.read(int(length)).decode("utf-8", "replace"))
        if not same_origin(self.headers) or not self.csrf_ok(form.get("csrf", [""])[0]):
            self.send_login("/", "다른 사이트에서 보낸 요청은 받지 않습니다", 403)
            return
        self._redirect("/", logout_cookie(self._secure()))
