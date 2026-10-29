"""TLS — 다른 기기·PC와 통신할 때 암호화한다 (관제 8080·8766, 연산 서버 8765, 체험·조작 서버 8443).

KISA 개발보안 가이드: 설계 SR2-8(중요정보 전송), 구현 2-5(암호화되지 않은 중요정보)·2-11(인증서 유효성 검증).

- **인증서**: 자체 서명(RSA 2048, 1년)을 `logs/certs/`(이 계정만)에 만든다. 없거나, 30일 안에 만료되거나, 지금 이 PC의
  주소가 빠져 있으면 다시 만든다. `REFLEXGUARD_TLS_CERT`·`REFLEXGUARD_TLS_KEY`로 내 인증서(mkcert·사내 CA 등)를 줄 수도 있다.
- **서버**: TLS 1.2 이상. 같은 PC(루프백)에서 온 평문은 그대로 받고(PC 밖으로 나가지 않으니 — `run.sh`처럼 한 PC에서 쓰는 흐름은
  그대로), 다른 기기에서 온 평문은 화면(HTTP)이면 https://로 넘기고 줄 통신(8766·8765)이면 끊는다.
- **클라이언트**(로봇·연산 서버 → 관제, 휠체어 PC → 연산 서버): 주소가 `tls://host:port`면 TLS로 붙고, 자체 서명이라 CA 대신
  서버 공개키(SubjectPublicKeyInfo)의 SHA-256 지문을 고정값과 비교한다(핀 고정 — 인증서를 다시 만들어도 열쇠가 같으면 같다). 고정값은 `REFLEXGUARD_TLS_PIN`(쉼표로 여럿), 같은 PC면
  `logs/certs/server.crt`에서 자동. 맞지 않으면 붙지 않는다 → 판단 없음 → 안전 정지.

    python -m common.tls        # 이 PC 인증서의 지문 (다른 PC에 REFLEXGUARD_TLS_PIN으로 준다)
"""

from __future__ import annotations

import hashlib
import hmac
import io
import ipaddress
import os
import re
import select
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from common.auth import ROOT, private_dir

CERT_DIR = Path(os.environ.get("REFLEXGUARD_CERT_DIR") or ROOT / "logs" / "certs")  # 시험은 다른 폴더로
PIN_ENV_VAR = "REFLEXGUARD_TLS_PIN"
RENEW_BEFORE_S = 30 * 86400   # 만료가 이보다 가까우면 다시 만든다
HANDSHAKE_S = 10.0


def lan_addresses() -> list[str]:
    """이 PC의 내부망 주소들 (다른 기기에서 열 주소·인증서에 넣을 주소)."""
    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.168.0.1", 9))  # 실제로 보내지는 않는다 — 나가는 쪽 주소만 알아낸다
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def _openssl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["openssl", *args], capture_output=True, text=True)


def public_names() -> list[str]:
    """이 PC를 부를 이름·주소 (설정 REFLEXGUARD_PUBLIC_NAMES — setup.sh config가 정한다). 인증서에 넣는다."""
    return [n for n in re.split(r"[\s,]+", os.environ.get("REFLEXGUARD_PUBLIC_NAMES", "")) if n]


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def _subject_names(addresses: list[str]) -> list[str]:
    names = ["localhost", "127.0.0.1"]
    for name in [*addresses, *public_names()]:
        if name not in names:
            names.append(name)
    return names


def _usable(cert: Path, names: list[str]) -> bool:
    if not cert.exists() or _openssl("x509", "-in", str(cert), "-noout", "-checkend", str(RENEW_BEFORE_S)).returncode != 0:
        return False
    listed = _openssl("x509", "-in", str(cert), "-noout", "-ext", "subjectAltName").stdout
    return all((f"IP Address:{n}" if _is_ip(n) else f"DNS:{n}") in listed for n in names)


def certificate(addresses: list[str] | None = None, cert_dir: Path = CERT_DIR, name: str = "server") -> tuple[Path, Path]:
    """(인증서, 키) 경로. 환경 변수로 준 내 인증서가 먼저, 아니면 자체 서명을 (필요하면 새로) 만든다.
    인증서에는 localhost·이 PC의 내부망 주소·설정의 이름(REFLEXGUARD_PUBLIC_NAMES)을 넣는다. 다시 만들 때(주소가 바뀌었거나
    만료가 가까움) **열쇠는 그대로 쓴다** — 다른 PC가 고정한 공개키 지문이 바뀌지 않게."""
    given_cert, given_key = os.environ.get("REFLEXGUARD_TLS_CERT", ""), os.environ.get("REFLEXGUARD_TLS_KEY", "")
    if given_cert and given_key:
        return Path(given_cert), Path(given_key)
    names = _subject_names(lan_addresses() if addresses is None else addresses)
    cert, key = cert_dir / f"{name}.crt", cert_dir / f"{name}.key"
    if _usable(cert, names) and key.exists():
        return cert, key
    private_dir(cert_dir.parent)
    private_dir(cert_dir)
    alt = ",".join(f"IP:{n}" if _is_ip(n) else f"DNS:{n}" for n in names)
    keep = ["-key", str(key)] if key.exists() else ["-newkey", "rsa:2048", "-nodes", "-keyout", str(key)]
    subprocess.run(["openssl", "req", "-x509", "-new", *keep, "-days", "365", "-out", str(cert),
                    "-subj", f"/CN=reflexguard-{name}", "-addext", f"subjectAltName={alt}"],
                   check=True, capture_output=True)
    key.chmod(0o600)
    return cert, key


def server_context(cert: Path, key: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2  # 파이썬 기본과 같지만 코드에 박아 둔다 (KISA 설계 SR2-6)
    context.num_tickets = 0  # 세션 재개를 쓰지 않는다 — 손잡기 뒤에 따로 오가는 메시지를 없앤다 (LockedTLS와 함께)
    context.load_cert_chain(cert, key)
    return context


class LockedTLS:
    """TLS 소켓을 읽는 스레드와 쓰는 스레드가 함께 써도 안전하게 감싼다. OpenSSL의 SSL 객체는 동시에 쓰면 안 된다 — 실제로
    읽기 스레드가 세션 티켓을 처리하는 사이 다른 스레드가 쓰면 그 메시지가 사라져 접속이 멈추는 일이 있었다.
    읽기는 데이터가 올 때까지 잠금 없이 기다리고(select), 실제 읽기·쓰기만 잠금 안에서 한다 (읽기는 막히지 않게 non-blocking).
    소켓처럼 쓴다: sendall·recv·recv_into·makefile·settimeout·close… (나머지는 안의 SSLSocket으로 넘긴다)."""

    is_tls = True

    def __init__(self, sock: ssl.SSLSocket) -> None:
        self._sock = sock
        self._lock = threading.Lock()
        self._timeout = sock.gettimeout()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)

    def settimeout(self, value: float | None) -> None:
        self._timeout = value  # 기다리는 시간은 여기서 센다 (안의 소켓은 읽을 때 non-blocking)

    def gettimeout(self) -> float | None:
        return self._timeout

    def _wait(self, deadline: float | None) -> None:
        while not self._sock.pending():
            wait = 0.5 if deadline is None else max(0.0, min(0.5, deadline - time.monotonic()))
            if select.select([self._sock], [], [], wait)[0]:
                return
            if deadline is not None and time.monotonic() >= deadline:
                raise socket.timeout("timed out")

    def _read(self, call: Any, *args: Any) -> Any:
        deadline = None if self._timeout is None else time.monotonic() + self._timeout
        while True:
            self._wait(deadline)
            with self._lock:
                self._sock.settimeout(0.0)
                try:
                    return call(*args)
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                    continue  # 레코드가 덜 왔다 — 잠금을 풀고 다시 기다린다

    def recv(self, size: int, flags: int = 0) -> bytes:
        return self._read(self._sock.recv, size)

    def recv_into(self, buffer: Any, size: int = 0, flags: int = 0) -> int:
        return self._read(self._sock.recv_into, buffer, size)

    def sendall(self, data: bytes, flags: int = 0) -> None:
        with self._lock:
            self._sock.settimeout(self._timeout if self._timeout is not None else 30.0)  # 상대가 안 읽으면 30초 뒤 포기
            self._sock.sendall(data)

    def send(self, data: bytes, flags: int = 0) -> int:
        self.sendall(data)
        return len(data)

    def makefile(self, mode: str = "rb", buffering: int | None = None, **_: Any) -> Any:
        raw = socket.SocketIO(self, mode)  # type: ignore[arg-type]
        if buffering == 0:
            return raw
        size = buffering if buffering and buffering > 0 else io.DEFAULT_BUFFER_SIZE
        return io.BufferedReader(raw, size) if "r" in mode else io.BufferedWriter(raw, size)

    def _decref_socketios(self) -> None:  # socket.SocketIO가 닫을 때 부른다 — 닫는 것은 close()가 한다
        pass

    def close(self) -> None:
        self._sock.close()


def is_secure(sock: Any) -> bool:
    """TLS로 오간 접속인가 (LockedTLS·SSLSocket)."""
    return bool(getattr(sock, "is_tls", False)) or isinstance(sock, ssl.SSLSocket)


def _der(data: bytes, at: int) -> tuple[int, int, int]:
    """DER 항목 하나: (태그, 내용 시작, 끝)."""
    tag, length, at = data[at], data[at + 1], at + 2
    if length & 0x80:
        count = length & 0x7F
        length, at = int.from_bytes(data[at:at + count], "big"), at + count
    return tag, at, at + length


def public_key_info(der_cert: bytes) -> bytes:
    """인증서(DER)에서 공개키 정보(SubjectPublicKeyInfo) 부분 — 인증서를 다시 만들어도 열쇠가 같으면 같다."""
    _, inner, _ = _der(der_cert, 0)            # Certificate
    _, at, _ = _der(der_cert, inner)           # tbsCertificate
    tag, _, end = _der(der_cert, at)
    if tag == 0xA0:                            # [0] 판 번호
        at = end
    for _ in range(5):                         # 일련번호, 서명 방식, 발급자, 유효 기간, 주체
        at = _der(der_cert, at)[2]
    return der_cert[at:_der(der_cert, at)[2]]


def fingerprint(der_cert: bytes) -> str:
    """고정값으로 쓰는 지문: 공개키 정보의 SHA-256 (인증서 전체가 아니라 — 주소가 바뀌어 다시 만들어도 같다)."""
    return "sha256:" + hashlib.sha256(public_key_info(der_cert)).hexdigest()


def file_fingerprint(cert: Path) -> str:
    return fingerprint(ssl.PEM_cert_to_DER_cert(cert.read_text(encoding="ascii")))


def fetch_fingerprint(address: str, timeout: float = 5.0) -> str:
    """서버에 TLS로 붙어 그 공개키 지문을 받아 온다 (확인 없이 — setup.sh config가 보여 주고 사람이 서버 쪽 값과 대조한다)."""
    host, port, _ = split_address(address)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as secure:
            return fingerprint(secure.getpeercert(binary_form=True) or b"")


def split_address(address: str) -> tuple[str, int, bool]:
    """'host:port' 또는 'tls://host:port' → (host, port, tls)."""
    tls = address.startswith("tls://")
    host, _, port = address.removeprefix("tls://").rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"address must be host:port or tls://host:port, got {address!r}")
    return host.strip("[]"), int(port), tls


def client_pins() -> list[str]:
    """믿을 서버 인증서 지문들: REFLEXGUARD_TLS_PIN(쉼표로 여럿) + 이 PC의 인증서(있으면)."""
    pins = [p.strip().lower() for p in os.environ.get(PIN_ENV_VAR, "").split(",") if p.strip()]
    for cert in (Path(os.environ.get("REFLEXGUARD_TLS_CERT", "") or CERT_DIR / "server.crt"),):
        try:
            pins.append(file_fingerprint(cert))
        except (OSError, ValueError):
            pass
    return pins


class PinMismatch(ssl.SSLError):
    """서버 인증서 지문이 고정값과 다르다 (가짜 서버·중간자 의심)."""


def wrap_client(sock: socket.socket, host: str, client_cert: bool = True) -> LockedTLS:
    """TLS로 감싸고 서버 인증서 지문을 고정값과 비교한다. 다르면(또는 고정값이 없으면) 닫고 PinMismatch.
    이 PC의 클라이언트 인증서(logs/certs/client.* — common/mtls.py)가 있으면 낸다 (mTLS)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    mine, mine_key = CERT_DIR / "client.crt", CERT_DIR / "client.key"
    if client_cert and mine.exists() and mine_key.exists():
        context.load_cert_chain(mine, mine_key)
    context.check_hostname = False   # 자체 서명이라 이름·CA 대신 지문으로 확인한다 (아래)
    context.verify_mode = ssl.CERT_NONE
    secure = context.wrap_socket(sock, server_hostname=host)
    seen = fingerprint(secure.getpeercert(binary_form=True) or b"")
    if not any(hmac.compare_digest(seen, pin) for pin in client_pins()):
        secure.close()
        raise PinMismatch(f"server certificate {seen} is not pinned ({PIN_ENV_VAR})")
    return LockedTLS(secure)


def connect(address: str, timeout: float, client_cert: bool = True) -> socket.socket:
    """host:port면 평문, tls://host:port면 TLS(지문 확인, 있으면 클라이언트 인증서)로 붙는다."""
    host, port, tls = split_address(address)
    sock = socket.create_connection((host, port), timeout=timeout)
    if tls:
        try:
            sock = wrap_client(sock, host, client_cert)
        except (OSError, ssl.SSLError):
            sock.close()
            raise
    return sock


def is_loopback(client_address: Any) -> bool:
    host = str(client_address[0]) if client_address else ""
    return host.startswith(("127.", "::ffff:127.")) or host == "::1"


class TLSServerMixin:
    """socketserver에 섞어 쓴다: tls가 있으면 접속마다(그 스레드에서) TLS로 감싼다. 첫 바이트로 TLS(0x16)인지 가린다.
    평문은 같은 PC에서 왔거나 plain_allowed(화면: 처리기가 https로 넘김)면 그대로, 아니면(줄 통신) 끊는다."""

    tls: ssl.SSLContext | None = None
    plain_allowed = False

    def finish_request(self, request: Any, client_address: Any) -> None:
        if self.tls is not None:
            try:
                request.settimeout(HANDSHAKE_S)
                first = request.recv(1, socket.MSG_PEEK)
                if first == b"\x16":
                    request = LockedTLS(self.tls.wrap_socket(request, server_side=True))
                elif not (self.plain_allowed or is_loopback(client_address)):
                    return
                request.settimeout(None)
            except (OSError, ssl.SSLError):
                return
        super().finish_request(request, client_address)  # type: ignore[misc]


def main(argv: list[str] | None = None) -> None:
    """python -m common.tls            이 PC 인증서의 공개키 지문 (없으면 만든다)
       python -m common.tls --fetch tls://host:port   그 서버의 공개키 지문 (setup.sh config)"""
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--fetch"]:
        try:
            print(fetch_fingerprint(args[1]))
        except (OSError, ValueError, ssl.SSLError) as error:
            raise SystemExit(f"닿지 않음: {error}") from error
        return
    if args[:1] == ["--check"]:  # 설정대로 붙어 본다: 평문이면 닿는지, tls://면 지문까지 (setup.sh check)
        try:
            connect(args[1], timeout=3.0).close()
        except PinMismatch as error:
            raise SystemExit(f"지문 다름: {error}") from error
        except (OSError, ValueError, ssl.SSLError) as error:
            raise SystemExit(f"닿지 않음: {error}") from error
        print("ok")
        return
    cert, _ = certificate()
    print(f"인증서: {cert}")
    print(f"이름:   {', '.join(_subject_names(lan_addresses()))}")
    print(f"지문:   {file_fingerprint(cert)}")
    print(f"다른 PC에서: {PIN_ENV_VAR}={file_fingerprint(cert)}  (주소는 tls://<이 PC 이름이나 주소>:<포트>)")


if __name__ == "__main__":
    sys.exit(main())
