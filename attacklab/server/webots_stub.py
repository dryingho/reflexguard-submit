"""어택랩 Webots 대역 — 체험·조작 서버(8443)가 이어 주는 웹소켓의 끝에 서서, 닿은 조작 명령을 모두 기록한다.

랩에는 Webots가 없다. 대신 이 대역이 Webots 웹 서버 자리(컨테이너 안 127.0.0.1)에 서서:
  - 웹소켓 접속마다 누가(Origin) 어떤 주소로 붙었는지 `webots-connect`로 남기고,
  - 조작 화면이 보내는 줄(`robot:reflexguard:keys:…`, `robot:reflexguard:connectome:off` 등)을 `webots-command`로 남긴다.
그래서 '다른 사이트에서 연 웹소켓이 Webots까지 닿았는가'를 로그로 확인할 수 있다. 시뮬레이션은 하지 않는다(영상 없음).

환경 변수
  LAB_STUB_LISTEN  받는 주소 (기본 127.0.0.1:11234)
  LAB_AUDIT_DIR    감사 로그 폴더 (기본 attacklab/logs) — 파일 이름은 audit-webots-<시각>.jsonl
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_proxy import Audit, _addr, _parse_head, _read_head, _redact  # noqa: E402

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455
MAX_FRAME = 256 * 1024     # 이보다 큰 프레임은 받지 않고 끊는다
COMMAND_PREVIEW = 300      # 로그에 남길 명령 길이


def _recv_exact(sock: socket.socket, n: int, buffered: bytearray) -> bytes | None:
    while len(buffered) < n:
        chunk = sock.recv(65536)
        if not chunk:
            return None
        buffered += chunk
    data = bytes(buffered[:n])
    del buffered[:n]
    return data


def _read_frame(sock: socket.socket, buffered: bytearray) -> tuple[int, bytes] | None:
    """프레임 하나 → (opcode, 풀린 데이터). 끊겼거나 너무 크면 None."""
    head = _recv_exact(sock, 2, buffered)
    if head is None:
        return None
    opcode, length = head[0] & 0x0F, head[1] & 0x7F
    if length == 126:
        raw = _recv_exact(sock, 2, buffered)
        length = struct.unpack("!H", raw)[0] if raw else -1
    elif length == 127:
        raw = _recv_exact(sock, 8, buffered)
        length = struct.unpack("!Q", raw)[0] if raw else -1
    if not 0 <= length <= MAX_FRAME:
        return None
    mask = _recv_exact(sock, 4, buffered) if head[1] & 0x80 else b""
    data = _recv_exact(sock, length, buffered)
    if mask is None or data is None:
        return None
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data


def _send_frame(sock: socket.socket, opcode: int, data: bytes) -> None:
    head = bytes([0x80 | opcode])
    if len(data) < 126:
        head += bytes([len(data)])
    elif len(data) < 65536:
        head += bytes([126]) + struct.pack("!H", len(data))
    else:
        head += bytes([127]) + struct.pack("!Q", len(data))
    sock.sendall(head + data)


def handle(client: socket.socket, peer: tuple[str, int], audit: Audit) -> None:
    src = f"{peer[0]}:{peer[1]}"
    head = _read_head(client)
    if not head:  # 붙었다 바로 끊은 접속 = 체험·조작 서버의 'Webots 켜져 있나' 확인. 기록하지 않는다
        return
    method, path, headers = _parse_head(head)
    lower = {k.lower(): v for k, v in headers.items()}
    key = lower.get("sec-websocket-key", "")
    if method != "GET" or lower.get("upgrade", "").lower() != "websocket" or not key:
        audit.log("webots-refused", src=src, method=method or "?", path=path[:200], detail="웹소켓 요청이 아님")
        client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        return
    # 웹소켓 접속 확인 값은 RFC 6455가 SHA-1로 정했다 (바꾸면 브라우저가 접속을 거부한다, 보안 용도 아님)
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode(), usedforsecurity=False).digest()).decode()  # nosemgrep: insecure-hash-algorithm-sha1
    client.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode())
    audit.log("webots-connect", src=src, path=path[:200], origin=lower.get("origin", "(없음)"),
              headers=_redact(headers))
    buffered = bytearray(head.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in head else b"")
    commands = 0
    while True:
        frame = _read_frame(client, buffered)
        if frame is None:
            break
        opcode, data = frame
        if opcode == 0x8:  # 닫기
            break
        if opcode == 0x9:  # ping → pong
            _send_frame(client, 0xA, data)
            continue
        if opcode == 0x1:
            commands += 1
            audit.log("webots-command", src=src, origin=lower.get("origin", "(없음)"),
                      command=data.decode("utf-8", "replace")[:COMMAND_PREVIEW])
    audit.log("webots-close", src=src, commands=commands)


def _guard(client: socket.socket, peer: tuple[str, int], audit: Audit) -> None:
    try:
        handle(client, peer, audit)
    except OSError:
        pass
    except Exception as error:  # 한 접속의 오류가 대역 전체를 멈추지 않게
        audit.log("error", src=f"{peer[0]}:{peer[1]}", detail=f"{type(error).__name__}: {error}")
    finally:
        client.close()


def main() -> None:
    audit = Audit(Path(os.environ.get("LAB_AUDIT_DIR", Path(__file__).resolve().parents[1] / "logs")), "webots")
    listen = _addr("LAB_STUB_LISTEN", "127.0.0.1:11234")
    server = socket.create_server(listen)
    audit.log("listen", label="Webots 대역(웹소켓)", on=f"{listen[0]}:{listen[1]}")
    while True:
        client, peer = server.accept()
        threading.Thread(target=_guard, args=(client, peer, audit), daemon=True).start()


if __name__ == "__main__":
    main()
