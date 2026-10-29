"""웹 요청의 출처 확인 — 관제 대시보드와 체험·조작 서버가 같이 쓴다.

다른 사이트의 스크립트가 사용자 브라우저를 빌려 명령을 보내거나 웹소켓을 여는 것(크로스사이트 요청 위조,
KISA 개발보안 가이드 구현 1-11 · 설계 SR1-6)을 막는다. 브라우저는 POST와 웹소켓에 `Origin`을 반드시 싣고
페이지 스크립트가 이를 바꿀 수 없으므로, `Origin`이 이 서버 주소(`Host`)와 같을 때만 받는다.

`Origin`이 없는 요청(curl·로봇 같은 브라우저 밖 도구)은 통과시킨다 — 그런 도구는 사용자 브라우저를 빌린 위조가
아니므로 출처가 아니라 인증(토큰)으로 막는다.

보안 헤더(security_headers·page_csp)도 여기 있다: CSP(화면의 인라인 스크립트만 해시로 허용), 틀 넣기 금지, MIME 추측 금지 등.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping
from urllib.parse import urlparse


def same_origin(headers: Mapping[str, str]) -> bool:
    """브라우저가 보낸 Origin이 이 서버(Host)와 같으면 참. Origin이 없으면 참, `null`이거나 다른 주소면 거짓."""
    origin = headers.get("Origin")
    if origin is None:
        return True
    host = (headers.get("Host") or "").strip().lower()
    parsed = urlparse(origin.strip())
    return bool(host) and parsed.scheme in ("http", "https") and parsed.netloc.lower() == host


def is_json(headers: Mapping[str, str]) -> bool:
    """본문이 JSON이라고 밝혔는가. 다른 사이트의 폼은 `application/json`을 보낼 수 없다 (보내려면 브라우저가 먼저 허락을 묻는다)."""
    return (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() == "application/json"


# ----- 보안 헤더 (KISA 설계 SR1-5 웹 서비스 요청 및 결과 검증 — 화면에 스크립트가 끼어들거나, 다른 사이트가 화면을 틀에 넣는 것 막기) -----
BASE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' ws: wss:; "
            "media-src 'self' blob: mediastream:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
_INLINE_SCRIPT = re.compile(rb"<script>(.*?)</script>", re.S)
_NONE = "'none'"


def page_csp(html: bytes) -> str:
    """화면 하나의 CSP: 그 화면에 들어 있는 인라인 스크립트만 해시로 허용한다 (다른 스크립트·외부 스크립트는 막힘)."""
    hashes = " ".join(f"'sha256-{base64.b64encode(hashlib.sha256(body).digest()).decode()}'"
                      for body in _INLINE_SCRIPT.findall(html))
    return f"{BASE_CSP}; script-src {hashes or _NONE}"


def security_headers(csp: str | None, https: bool, permissions: str) -> list[tuple[str, str]]:
    """모든 응답에 붙일 헤더. csp가 없으면 스크립트 없는 기본 CSP (API·로그인 화면)."""
    headers = [
        ("Content-Security-Policy", csp or f"{BASE_CSP}; script-src {_NONE}"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        # same-origin: 다른 사이트로는 주소를 보내지 않는다. no-referrer면 브라우저가 폼 POST의 Origin을 null로 보내
        # 같은 화면의 로그인까지 '다른 사이트'로 거부된다 (Fetch 표준 — same_origin 확인)
        ("Referrer-Policy", "same-origin"),
        ("Cross-Origin-Opener-Policy", "same-origin"),
        ("Permissions-Policy", permissions),
    ]
    if https:
        headers.append(("Strict-Transport-Security", "max-age=86400"))
    return headers


# ----- Host 허용 목록 (KISA 구현 1-11 — DNS 리바인딩: 남의 이름이 이 PC를 가리키게 해 브라우저를 속이는 것을 막는다) -----
# (만든 시각, 이름들). 처음엔 '만든 적 없음'(-inf) — 0.0이면 켜진 지 60초 안(monotonic < 60)에 빈 목록을 그대로 써 모두 421이 됐다
_HOSTS_CACHE: tuple[float, frozenset[str]] = (float("-inf"), frozenset())


def allowed_hosts() -> frozenset[str]:
    """이 서버를 부를 수 있는 이름: localhost·루프백·이 PC 이름(.local)·내부망 주소·설정의 이름(REFLEXGUARD_PUBLIC_NAMES)·
    REFLEXGUARD_ALLOWED_HOSTS(쉼표로 더함). 1분 동안 기억한다 (주소가 바뀌면 다시 센다)."""
    global _HOSTS_CACHE
    import os
    import socket
    import time
    from common.tls import lan_addresses, public_names
    if time.monotonic() - _HOSTS_CACHE[0] < 60:
        return _HOSTS_CACHE[1]
    name = socket.gethostname().lower()
    hosts = {"localhost", "127.0.0.1", "::1", name, f"{name}.local", *lan_addresses(), *(n.lower() for n in public_names()),
             *(h.strip().lower() for h in os.environ.get("REFLEXGUARD_ALLOWED_HOSTS", "").split(",") if h.strip())}
    _HOSTS_CACHE = (time.monotonic(), frozenset(hosts))
    return _HOSTS_CACHE[1]


def host_name(header: str) -> str:
    """Host 헤더 → 이름 (포트·IPv6 괄호를 뺀다)."""
    header = header.strip().lower()
    if header.startswith("["):
        return header[1:header.find("]")] if "]" in header else header
    name, sep, port = header.rpartition(":")
    return name if sep and port.isdigit() else header


def host_allowed(headers: Mapping[str, str]) -> bool:
    """Host가 없으면(브라우저 밖 도구) 통과, 있으면 허용 목록에 있어야 한다."""
    header = headers.get("Host")
    return header is None or host_name(header) in allowed_hosts()
