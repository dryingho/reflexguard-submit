"""mTLS — 다른 PC에서 오는 기계 접속(관제 8766, 연산 서버 8765)은 클라이언트 인증서도 내야 한다. KISA 구현 2-11, 설계 SR2-1.

CA를 따로 두지 않는다: 장치마다 자기 열쇠·자체 서명 인증서(logs/certs/client.*, CN = 장치 이름)를 만들고, 서버에 **장치 열쇠
(또는 서버 토큰)로 인증해 한 번 등록**한다 (`python -m common.mtls enroll tls://서버:포트` — setup.sh config가 부른다).
서버는 등록된 인증서만 믿는다(logs/certs/clients/, 0600). 관제 PC와 연산 서버 PC가 달라도 각자에 등록하면 된다.

    다른 PC에서 오는 TLS 접속:  인증서 없음 → 등록(enroll)만 받는다
                                 등록된 인증서 → 인증서 이름(CN)이 hello·auth의 이름과 같아야 한다 (장치 열쇠·토큰 확인은 그대로 더한다)
    같은 PC(루프백)·평문:       예전처럼 (인증서 없이)
    장치 세대를 올리면(python -m common.device_auth <이름> --rotate) 그 장치의 예전 인증서는 다시 켤 때부터 믿지 않는다.

브라우저 화면(8080·8443)은 클라이언트 인증서를 쓰기 어려워 토큰·쿠키로 지킨다 — 이 모듈은 기계 접속에만 쓴다.
"""

from __future__ import annotations

import json
import re
import ssl
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from common import tls
from common.auth import private_dir
from common.device_auth import epochs

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,79}")
_contexts: list[ssl.SSLContext] = []   # 등록을 받으면 새 인증서를 바로 믿게 할 서버 문맥들
_lock = threading.Lock()


def clients_dir() -> Path:
    return tls.CERT_DIR / "clients"


def client_paths() -> tuple[Path, Path]:
    return tls.CERT_DIR / "client.crt", tls.CERT_DIR / "client.key"


def ensure_client_cert(name: str) -> tuple[Path, Path]:
    """이 PC(장치)의 클라이언트 인증서 — CN = 장치 이름. 이름이 바뀌었거나 만료가 가까우면 다시 만든다(열쇠는 그대로)."""
    cert, key = client_paths()
    if cert.exists() and key.exists() and cert_name(cert.read_text(encoding="ascii")) == name and \
            subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-checkend", str(tls.RENEW_BEFORE_S)],
                           capture_output=True).returncode == 0:
        return cert, key
    private_dir(tls.CERT_DIR.parent)
    private_dir(tls.CERT_DIR)
    keep = ["-key", str(key)] if key.exists() else ["-newkey", "rsa:2048", "-nodes", "-keyout", str(key)]
    subprocess.run(["openssl", "req", "-x509", "-new", *keep, "-days", "365", "-out", str(cert), "-subj", f"/CN={name}",
                    "-addext", "extendedKeyUsage=clientAuth"], check=True, capture_output=True)
    key.chmod(0o600)
    return cert, key


def cert_name(pem: str) -> str:
    """PEM 인증서의 CN (없거나 못 읽으면 빈 문자열)."""
    result = subprocess.run(["openssl", "x509", "-noout", "-subject", "-nameopt", "RFC2253"], input=pem, capture_output=True,
                            text=True)
    match = re.search(r"CN=([^,]+)", result.stdout) if result.returncode == 0 else None
    return match.group(1).strip() if match else ""


def peer_name(sock: Any) -> str:
    """TLS 접속의 확인된 클라이언트 인증서 CN (없으면 빈 문자열)."""
    try:
        info = sock.getpeercert()
    except (AttributeError, ValueError, OSError):
        return ""
    for field in (info or {}).get("subject", ()):
        for key, value in field:
            if key == "commonName":
                return str(value)
    return ""


def _stored(role: str, name: str) -> Path:
    return clients_dir() / f"{role}__{name}__e{epochs().get(name, 1)}.pem"


def register(role: str, name: str, pem: str) -> str:
    """등록 (부르는 쪽이 장치 열쇠·토큰을 이미 확인했다). 잘못이면 그 까닭, 되면 빈 문자열."""
    if not NAME.fullmatch(role) or not NAME.fullmatch(name) or len(pem) > 8192 or "BEGIN CERTIFICATE" not in pem:
        return "bad certificate"
    if cert_name(pem) != name:
        return "certificate name must be the device name"
    try:
        ssl.PEM_cert_to_DER_cert(pem.strip())
    except ValueError:
        return "bad certificate"
    private_dir(clients_dir())
    path = _stored(role, name)
    for old in clients_dir().glob(f"{role}__{name}__e*.pem"):  # 같은 장치의 예전 등록은 지운다
        old.unlink(missing_ok=True)
    path.write_text(pem.strip() + "\n", encoding="ascii")
    path.chmod(0o600)
    with _lock:
        for context in _contexts:
            context.load_verify_locations(cadata=pem)
    return ""


def enable(context: ssl.SSLContext) -> ssl.SSLContext:
    """서버 문맥이 클라이언트 인증서를 청하게 한다 (없어도 손잡기는 됨 — 등록·같은 PC를 위해). 등록된 것(지금 세대)만 믿는다."""
    context.verify_mode = ssl.CERT_OPTIONAL
    current = epochs()
    if clients_dir().is_dir():
        for path in sorted(clients_dir().glob("*.pem")):
            match = re.fullmatch(r"(.+)__(.+)__e(\d+)\.pem", path.name)
            if match and int(match.group(3)) == current.get(match.group(2), 1):
                context.load_verify_locations(cadata=path.read_text(encoding="ascii"))
    with _lock:
        _contexts.append(context)
    return context


def handle_enroll(message: dict[str, Any], master: str) -> dict[str, Any]:
    """서버 쪽 등록 처리: 장치 열쇠(그 이름) 또는 서버 토큰을 확인한 뒤 인증서를 등록하고 답을 돌려준다."""
    import hmac
    from common.device_auth import check_device
    role, name = str(message.get("role", "")), str(message.get("name", ""))
    by_device = check_device(master, role, name, str(message.get("device_key", "")))
    by_token = role == "server" and bool(master) and hmac.compare_digest(str(message.get("token", "")).encode(), master.encode())
    if not (by_device or by_token):
        return {"type": "error", "message": "bad credentials"}
    problem = register(role, name, str(message.get("pem", "")))
    return {"type": "error", "message": problem} if problem else {"type": "enrolled", "name": name}


def check_peer(sock: Any, client_address: Any, name: str, by_device: bool) -> str:
    """기계 접속의 클라이언트 인증서 확인. 괜찮으면 빈 문자열, 아니면 까닭.
    장치 열쇠로 붙으면 인증서 이름이 그 장치 이름과 같아야 하고, 서버 토큰이면 등록된 인증서이기만 하면 된다."""
    if not required(sock, client_address):
        return ""
    cn = peer_name(sock)
    if not cn:
        return "client certificate required (bash setup.sh config)"
    if by_device and cn != name:
        return "client certificate is for another device"
    return ""


def required(sock: Any, client_address: Any) -> bool:
    """이 접속이 클라이언트 인증서를 내야 하는가: 다른 PC에서 온 TLS 접속."""
    return tls.is_secure(sock) and not tls.is_loopback(client_address)


def enroll(address: str) -> str:
    """이 PC(장치)의 인증서를 서버에 등록한다. 장치 열쇠(REFLEXGUARD_DEVICE_NAME·KEY) 또는 서버 토큰으로 인증."""
    from common.auth import client_token
    from common.device_auth import DEVICE_ROLE, own_device
    device_name, device_key = own_device()
    name = device_name or __import__("socket").gethostname()
    cert, _ = ensure_client_cert(name)
    request: dict[str, Any] = {"type": "enroll", "role": DEVICE_ROLE if device_key else "server", "name": name,
                               "pem": cert.read_text(encoding="ascii")}
    if device_key:
        request["device_key"] = device_key
    else:
        request["token"] = client_token()
    sock = tls.connect(address, timeout=5.0, client_cert=False)  # 등록은 인증서 없이 (지문으로 서버는 확인한다)
    try:
        sock.settimeout(10.0)
        sock.sendall((json.dumps(request) + "\n").encode())
        reply = json.loads(sock.makefile("rb").readline() or b"{}")
    finally:
        sock.close()
    if reply.get("type") != "enrolled":
        raise RuntimeError(str(reply.get("message", "등록 실패")))
    return name


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 2 and args[0] == "enroll":
        try:
            name = enroll(args[1])
        except (OSError, RuntimeError, ValueError, ssl.SSLError) as error:
            raise SystemExit(f"등록하지 못함: {error}") from error
        print(f"{args[1]}에 이 PC의 클라이언트 인증서를 등록했습니다 (이름 {name})")
        return
    raise SystemExit("사용법: python -m common.mtls enroll tls://<서버>:<포트>")


if __name__ == "__main__":
    main()
