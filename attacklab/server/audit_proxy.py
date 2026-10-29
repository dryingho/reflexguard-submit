"""어택랩 감사 프록시 — 서버(대시보드) 티어 앞에 서서 오가는 모든 것을 기록하고, 보안 패치를 켜고 끈다.

교육용입니다. 이 프록시는 공격 도구가 아니라 **방어·관찰 도구**입니다:
  - 들어오는 모든 접속·HTTP 요청·에이전트(로봇/연산 서버) 줄을 구조화된 로그(JSONL + 콘솔)로 남긴다 → 보안 테스트의 결과를 눈으로 본다.
  - 패치 토글(환경 변수 PATCH_*)로 인증·요청 속도 제한·본문 크기 제한·Origin 확인 같은 보호를 켜고 끈다 → 같은 테스트가
    패치 전/후에 어떻게 달라지는지 로그로 비교한다.
  - 프록시는 뒤의 대시보드(upstream)로 바이트를 그대로 넘긴다. 요청을 바꾸지 않고, 막을 때만 짧은 응답을 돌려준다.

환경 변수
  LAB_LISTEN_WEB     이 프록시가 받는 관제 화면 주소 (기본 0.0.0.0:8080)
  LAB_UPSTREAM_WEB   넘겨줄 대시보드 관제 화면      (기본 127.0.0.1:18080)
  LAB_LISTEN_AGENT   이 프록시가 받는 로봇·연산 서버 접속 (기본 0.0.0.0:8766)
  LAB_UPSTREAM_AGENT 넘겨줄 대시보드 접속 포트       (기본 127.0.0.1:18766)
  LAB_AUDIT_DIR      감사 로그 폴더 (기본 attacklab/logs)
  LAB_AUDIT_NAME     로그 파일 이름에 붙일 이름 (예: portal → audit-portal-<시각>.jsonl). 한 컨테이너에 프록시가 여럿일 때 나눠 본다
  패치(기본은 모두 꺼짐 — 일부러 취약한 상태):
  PATCH_REQUIRE_TOKEN=1   HTTP·에이전트에 올바른 토큰이 없으면 막는다 (PATCH_TOKEN 값과 비교, hmac)
  PATCH_TOKEN=<값>        기대하는 토큰 (REFLEXGUARD_DASHBOARD_TOKEN과 같게 두면 패치가 맞물린다)
  PATCH_RATE_LIMIT_RPS=<수> 한 IP가 초당 이만큼 넘게 보내면 429로 막는다 (토큰 버킷)
  PATCH_MAX_BODY=<바이트>   HTTP 본문이 이보다 크면 413으로 막는다
  PATCH_ALLOWED_HOSTS=<a,b> 요청 Host/Origin 호스트가 이 목록(쉼표)에 없으면 403 (비면 확인 안 함)
  PATCH_READ_ONLY=1       GET 외의 메서드(POST 등)를 모두 막는다 (명령 전송 차단)
"""

from __future__ import annotations

import datetime as dt
import hmac
import json
import os
import re
import socket
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

MAX_LINE = 256 * 1024          # 요청 머리(요청 줄 + 헤더)의 최대 길이
BODY_LOG_PREVIEW = 512         # 로그에 남길 본문 미리보기 바이트 수
AGENT_LINE_PREVIEW = 400       # 에이전트 줄 로그 미리보기
SECRET_KEYS = ("token", "password", "secret", "authorization", "cookie")  # 로그에서 가릴 값


def _addr(env: str, default: str) -> tuple[str, int]:
    host, _, port = os.environ.get(env, default).rpartition(":")
    return host or "0.0.0.0", int(port)  # nosec B104  # 컨테이너 안에서 받는 주소 (호스트에는 compose가 127.0.0.1로만 묶는다)


def _flag(env: str) -> bool:
    return os.environ.get(env, "").strip().lower() in ("1", "true", "yes", "on")


class Audit:
    """감사 로그: 한 줄 JSON으로 파일과 콘솔에 남긴다. 접속·요청 수를 센다 (대시보드처럼 보여 주려고)."""

    def __init__(self, directory: Path, name: str = "") -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"audit-{name + '-' if name else ''}{stamp}.jsonl"
        self._file = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self.counts: dict[str, int] = defaultdict(int)

    def log(self, kind: str, **fields: object) -> None:
        record = {"ts": dt.datetime.now().isoformat(timespec="milliseconds"), "kind": kind, **fields}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.counts[kind] += 1
            self._file.write(line + "\n")
            self._file.flush()
            print(line, flush=True)


_SECRET_VALUE = re.compile(r'((?:token|password|secret)"?\s*[:=]\s*"?)[^"&\s,}]+', re.IGNORECASE)


def _redact_text(text: str) -> str:
    """주소·본문·줄에 섞인 비밀값(token=…, "token": "…")을 가린다 (로그인 폼·?token=·hello 줄)."""
    return _SECRET_VALUE.sub(r"\1<가림>", text)


def _redact(headers: dict[str, str]) -> dict[str, str]:
    """헤더에서 비밀값은 가린다 (로그에 토큰·암호를 그대로 남기지 않게)."""
    safe = {}
    for key, value in headers.items():
        # 이름에 비밀 단어가 들어가면 가린다 (X-Token·X-CSRF-Token·Proxy-Authorization·Set-Cookie처럼 앞뒤가 붙은 이름도)
        safe[key] = "<가림>" if any(word in key.lower() for word in SECRET_KEYS) else value[:200]
    return safe


class RateLimiter:
    """IP마다 토큰 버킷. rps가 0이면 제한 없음."""

    def __init__(self, rps: float) -> None:
        self.rps = rps
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        if self.rps <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(ip, (self.rps, now))
            tokens = min(self.rps, tokens + (now - last) * self.rps)
            if tokens < 1.0:
                self._state[ip] = (tokens, now)
                return False
            self._state[ip] = (tokens - 1.0, now)
            return True


class Patches:
    """패치 토글 모음 (환경 변수). 기본은 모두 꺼짐 = 일부러 취약한 상태."""

    def __init__(self) -> None:
        self.require_token = _flag("PATCH_REQUIRE_TOKEN")
        self.token = os.environ.get("PATCH_TOKEN", os.environ.get("REFLEXGUARD_DASHBOARD_TOKEN", ""))
        self.max_body = int(os.environ.get("PATCH_MAX_BODY", "0") or 0)
        self.read_only = _flag("PATCH_READ_ONLY")
        self.allowed_hosts = [h.strip() for h in os.environ.get("PATCH_ALLOWED_HOSTS", "").split(",") if h.strip()]
        self.web_rate = RateLimiter(float(os.environ.get("PATCH_RATE_LIMIT_RPS", "0") or 0))
        self.agent_rate = RateLimiter(float(os.environ.get("PATCH_RATE_LIMIT_RPS", "0") or 0))

    def active(self) -> list[str]:
        on = []
        if self.require_token:
            on.append("require_token")
        if self.max_body:
            on.append(f"max_body={self.max_body}")
        if self.read_only:
            on.append("read_only")
        if self.allowed_hosts:
            on.append(f"allowed_hosts={','.join(self.allowed_hosts)}")
        if self.web_rate.rps:
            on.append(f"rate_limit={self.web_rate.rps}/s")
        return on or ["(없음 — 취약)"]


def _token_ok(patches: Patches, provided: str) -> bool:
    return bool(patches.token) and hmac.compare_digest(provided, patches.token)


def _pump(src: socket.socket, dst: socket.socket) -> None:
    """src에서 받은 바이트를 dst로 흘린다 (한 방향). 끝나면 dst를 닫아 상대도 알게 한다 (SSE·긴 응답도 됨)."""
    try:
        while chunk := src.recv(65536):
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _read_head(sock: socket.socket) -> bytes:
    """요청/응답의 머리(빈 줄까지)를 읽어 바이트로 돌려준다. 못 읽으면 b''."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > MAX_LINE:
            break
    return buf


def _parse_head(head: bytes) -> tuple[str, str, dict[str, str]]:
    """머리 바이트 → (메서드, 경로, 헤더dict). HTTP가 아니면 ("", "", {})."""
    try:
        text = head.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    except ValueError:
        return "", "", {}
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) < 2:
        return "", "", {}
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip()] = value.strip()
    return parts[0], parts[1], headers


def _refuse(client: socket.socket, status: str, message: str) -> None:
    body = json.dumps({"error": message, "lab": "요청이 패치 규칙에 막혔습니다"}, ensure_ascii=False).encode()
    client.sendall(f"HTTP/1.1 {status}\r\nContent-Type: application/json; charset=utf-8\r\n"
                   f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)


def handle_web(client: socket.socket, peer: tuple[str, int], upstream: tuple[str, int],
               audit: Audit, patches: Patches) -> None:
    ip = peer[0]
    head = _read_head(client)
    if not head:
        client.close()
        return
    method, path, headers = _parse_head(head)
    # 본문: Content-Length가 있으면 그만큼 더 읽는다 (머리를 읽다 먼저 들어온 부분 포함)
    already = head.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in head else b""
    try:
        length = int(headers.get("Content-Length", "0") or 0) if method else 0
    except ValueError:  # 숫자가 아닌 길이 — 본문 없이 그대로 넘겨 뒤의 서버가 어떻게 답하는지 보게 한다
        length = 0
    body = already
    while len(body) < length:
        chunk = client.recv(min(65536, length - len(body)))
        if not chunk:
            break
        body += chunk
    token = headers.get("X-Token", "")
    if not token and "token=" in path:
        token = path.split("token=", 1)[1].split("&", 1)[0]
    audit.log("http", src=f"{ip}:{peer[1]}", method=method or "?", path=_redact_text(path[:300]),
              status="수신", bytes=len(body), headers=_redact(headers),
              body=_redact_text(body[:BODY_LOG_PREVIEW].decode("latin-1", "replace")) if body else "")

    # --- 패치 적용 (막으면 로그에 남기고 upstream으로 보내지 않는다) ---
    def blocked(status: str, why: str) -> None:
        audit.log("blocked", src=ip, method=method, path=_redact_text(path[:200]), status=status, by=why)
        try:
            _refuse(client, status, why)
        except OSError:
            pass
        client.close()

    host = (headers.get("Origin") or headers.get("Host") or "").replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
    if not patches.web_rate.allow(ip):
        return blocked("429 Too Many Requests", "rate_limit")
    if patches.read_only and method not in ("GET", "HEAD"):
        return blocked("403 Forbidden", "read_only")
    if patches.max_body and len(body) > patches.max_body:
        return blocked("413 Payload Too Large", "max_body")
    if patches.allowed_hosts and host and host not in patches.allowed_hosts:
        return blocked("403 Forbidden", "allowed_hosts")
    if patches.require_token and not _token_ok(patches, token):
        return blocked("401 Unauthorized", "require_token")

    # --- upstream(대시보드)로 넘긴다 ---
    try:
        server = socket.create_connection(upstream, timeout=5)
        server.settimeout(None)  # 5초는 접속할 때만 — 조용한 연결(에이전트·SSE)이 끊기지 않게
    except OSError:
        audit.log("error", src=ip, path=_redact_text(path[:200]), detail="upstream(대시보드)에 닿지 않음")
        client.close()
        return
    server.sendall(head + body[len(already):] if length else head)
    resp_head = _read_head(server)
    status_line = resp_head.split(b"\r\n", 1)[0].decode("latin-1", "replace") if resp_head else ""
    audit.log("http-reply", src=ip, path=_redact_text(path[:200]), upstream=status_line)
    try:
        client.sendall(resp_head)
    except OSError:
        server.close()
        client.close()
        return
    # 양방향으로 흘린다 (SSE·keep-alive도 됨)
    threads = [threading.Thread(target=_pump, args=(server, client), daemon=True),
               threading.Thread(target=_pump, args=(client, server), daemon=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    server.close()
    client.close()


def handle_agent(client: socket.socket, peer: tuple[str, int], upstream: tuple[str, int],
                 audit: Audit, patches: Patches) -> None:
    """로봇·연산 서버 접속(TCP, 한 줄에 JSON 하나). 첫 줄(hello)을 보고 토큰·속도를 확인한 뒤 그대로 이어 준다."""
    ip = peer[0]
    if not patches.agent_rate.allow(ip):
        audit.log("blocked", src=ip, proto="agent", by="rate_limit")
        client.close()
        return
    first = b""
    client.settimeout(5.0)
    try:
        while b"\n" not in first and len(first) < MAX_LINE:
            chunk = client.recv(4096)
            if not chunk:
                break
            first += chunk
    except OSError:
        client.close()
        return
    client.settimeout(None)
    line = first.split(b"\n", 1)[0]
    try:
        hello = json.loads(line)
        role = hello.get("role", "?")
        provided = hello.get("token", "")
    except (ValueError, AttributeError):
        role, provided = "?", ""
    audit.log("agent-connect", src=f"{ip}:{peer[1]}", role=role, hello=_redact_text(line[:AGENT_LINE_PREVIEW].decode("latin-1", "replace")))
    if patches.require_token and not _token_ok(patches, provided):
        audit.log("blocked", src=ip, proto="agent", role=role, by="require_token")
        try:
            client.sendall(b'{"type":"error","message":"bad token"}\n')
        except OSError:
            pass
        client.close()
        return
    try:
        server = socket.create_connection(upstream, timeout=5)
        server.settimeout(None)  # 5초는 접속할 때만 — 조용한 연결(에이전트·SSE)이 끊기지 않게
    except OSError:
        audit.log("error", src=ip, proto="agent", detail="upstream(대시보드 접속 포트)에 닿지 않음")
        client.close()
        return
    server.sendall(first)

    def tap(src: socket.socket, dst: socket.socket, who: str) -> None:
        buf = b""
        try:
            while chunk := src.recv(65536):
                dst.sendall(chunk)
                buf += chunk
                while b"\n" in buf:
                    one, buf = buf.split(b"\n", 1)
                    if one.strip():
                        audit.log("agent-line", src=ip, role=role, dir=who,
                                  line=_redact_text(one[:AGENT_LINE_PREVIEW].decode("latin-1", "replace")))
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    threads = [threading.Thread(target=tap, args=(client, server, "로봇→서버"), daemon=True),
               threading.Thread(target=tap, args=(server, client, "서버→로봇"), daemon=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    server.close()
    client.close()
    audit.log("agent-close", src=ip, role=role)


def serve(listen: tuple[str, int], upstream: tuple[str, int], handler, audit: Audit, patches: Patches, label: str) -> None:
    server = socket.create_server(listen, reuse_port=False)
    audit.log("listen", label=label, on=f"{listen[0]}:{listen[1]}", upstream=f"{upstream[0]}:{upstream[1]}")
    while True:
        client, peer = server.accept()
        threading.Thread(target=_guard, args=(handler, client, peer, upstream, audit, patches), daemon=True).start()


def _guard(handler, client, peer, upstream, audit, patches) -> None:
    try:
        handler(client, peer, upstream, audit, patches)
    except Exception as error:  # 한 접속의 오류가 프록시 전체를 멈추지 않게
        audit.log("error", src=f"{peer[0]}:{peer[1]}", detail=f"{type(error).__name__}: {error}")
        try:
            client.close()
        except OSError:
            pass


def main() -> None:
    audit = Audit(Path(os.environ.get("LAB_AUDIT_DIR", Path(__file__).resolve().parents[1] / "logs")),
                  os.environ.get("LAB_AUDIT_NAME", ""))
    patches = Patches()
    role = os.environ.get("LAB_PROXY_ROLE", "server(대시보드) 감사 프록시")
    audit.log("start", role=role, patches=patches.active())
    print(f"[audit] {role} · 패치 상태: {', '.join(patches.active())}", file=sys.stderr, flush=True)
    # LISTEN 환경 변수가 "off"/빈 값이면 그 쪽은 켜지 않는다 (클라이언트 티어에서는 커넥톰 통신만 기록하는 식으로 재사용)
    jobs = []
    web_listen = os.environ.get("LAB_LISTEN_WEB", "0.0.0.0:8080")
    agent_listen = os.environ.get("LAB_LISTEN_AGENT", "0.0.0.0:8766")
    if web_listen and web_listen.lower() != "off":
        jobs.append(threading.Thread(target=serve, args=(_addr("LAB_LISTEN_WEB", web_listen),
                                                         _addr("LAB_UPSTREAM_WEB", "127.0.0.1:18080"),
                                                         handle_web, audit, patches, "관제 화면(HTTP)"), daemon=True))
    if agent_listen and agent_listen.lower() != "off":
        label = os.environ.get("LAB_AGENT_LABEL", "로봇·연산 서버 접속(TCP)")
        jobs.append(threading.Thread(target=serve, args=(_addr("LAB_LISTEN_AGENT", agent_listen),
                                                         _addr("LAB_UPSTREAM_AGENT", "127.0.0.1:18766"),
                                                         handle_agent, audit, patches, label), daemon=True))
    for job in jobs:
        job.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
