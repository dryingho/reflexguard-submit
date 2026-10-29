"""관제 대시보드(dashboard/server.py)에 붙는 연결 — Webots 컨트롤러와 커넥톰 연산 서버가 쓴다.

    link = DashboardLink("127.0.0.1:8766", role="webots", name="reflexguard", hello={...})
    link.send({"type": "state", ...})          # 막히지 않는다: 보낼 줄에 넣고 뒤의 스레드가 보낸다
    for command in link.take_commands(): ...   # 대시보드가 보낸 명령 ({"command": "connectome", "value": "off"} 등)

대시보드가 꺼져 있거나 끊겨도 로봇은 그대로 돈다(관제만 빠진다). 뒤에서 계속 다시 접속하고, 접속할 때마다 hello를 다시 보낸다.
보낼 줄이 넘치면(대시보드가 느리거나 끊김) 오래된 상태부터 버린다 — 사건(event)은 되도록 남긴다.
통신: TCP 위에 한 줄에 JSON 하나 (dashboard/server.py 맨 위 설명). 토큰(REFLEXGUARD_DASHBOARD_TOKEN)이 있으면 hello에 싣는다.
"""

from __future__ import annotations

import collections
import json
import os
import socket
import threading
import time
from typing import Any

from common.config import CONFIG, Config

ENV_VAR = "REFLEXGUARD_DASHBOARD"
TOKEN_ENV_VAR = "REFLEXGUARD_DASHBOARD_TOKEN"
_MAX_QUEUE = 500


def dashboard_address(cfg: Config = CONFIG) -> str:
    """대시보드 주소 ("host:port"). 환경 변수가 설정보다 우선. 비었으면 대시보드에 붙지 않는다."""
    return os.environ.get(ENV_VAR, "").strip() or cfg.dashboard.strip()


def _json_default(value: Any) -> Any:
    """numpy 수 등 JSON이 모르는 값을 바꾼다."""
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "value"):  # Enum (Signal)
        return value.value
    return str(value)


class DashboardLink:
    def __init__(self, address: str, role: str, name: str, hello: dict[str, Any] | None = None,
                 cfg: Config = CONFIG) -> None:
        host, _, port = address.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(f"dashboard address must be host:port, got {address!r}")
        self.host, self.port = host.strip("[]"), int(port)
        self.address = address
        self.cfg = cfg
        self.hello = {"type": "hello", "role": role, "name": name, **(hello or {})}
        token = os.environ.get(TOKEN_ENV_VAR, "")
        if token:
            self.hello["token"] = token
        self.connected = False
        self._outbox: collections.deque[bytes] = collections.deque()
        self._commands: collections.deque[dict[str, Any]] = collections.deque(maxlen=100)
        self._cond = threading.Condition()
        self._sock: socket.socket | None = None
        self._stop = False
        threading.Thread(target=self._run, name="dashboard-link", daemon=True).start()

    def update_hello(self, **fields: Any) -> None:
        """다시 접속할 때 보낼 hello를 바꾼다 (맵이 바뀌었을 때 등)."""
        self.hello.update(fields)

    def send(self, message: dict[str, Any]) -> None:
        try:
            data = (json.dumps(message, separators=(",", ":"), default=_json_default, allow_nan=False) + "\n").encode()
        except ValueError:  # NaN 등
            return
        with self._cond:
            if len(self._outbox) >= _MAX_QUEUE:
                # 넘치면 오래된 상태(state)부터 버린다
                for index, item in enumerate(self._outbox):
                    if item.startswith(b'{"type":"state"'):
                        del self._outbox[index]
                        break
                else:
                    self._outbox.popleft()
            self._outbox.append(data)
            self._cond.notify()

    def flush(self, timeout: float = 0.3) -> None:
        """보낼 줄이 빌 때까지 잠깐 기다린다 (끝나기 직전의 사건을 놓치지 않게). 끊겨 있으면 바로 돌아온다."""
        deadline = time.monotonic() + timeout
        while self.connected and self._outbox and time.monotonic() < deadline:
            time.sleep(0.01)

    def take_commands(self) -> list[dict[str, Any]]:
        with self._cond:
            commands = list(self._commands)
            self._commands.clear()
        return commands

    def close(self) -> None:
        self._stop = True
        with self._cond:
            self._cond.notify_all()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)  # 읽는 스레드가 makefile로 잡고 있어 close만으로는 접속이 닫히지 않는다
                sock.close()
            except OSError:
                pass

    # --- 뒤에서 접속·보내기·받기 ---
    def _run(self) -> None:
        while not self._stop:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self.cfg.dashboard_connect_timeout_s)
                sock.settimeout(None)
            except OSError:
                time.sleep(self.cfg.dashboard_retry_s)
                continue
            self._sock = sock
            self.connected = True
            reader = threading.Thread(target=self._read, args=(sock,), name="dashboard-read", daemon=True)
            reader.start()
            try:
                sock.sendall((json.dumps(self.hello, default=_json_default) + "\n").encode())
                while not self._stop and self.connected:
                    with self._cond:
                        while not self._outbox and not self._stop and self.connected:
                            self._cond.wait(0.5)
                        batch = b"".join(self._outbox)
                        self._outbox.clear()
                    if batch:
                        sock.sendall(batch)
            except OSError:
                pass
            self.connected = False
            try:
                sock.close()
            except OSError:
                pass
            self._sock = None
            if not self._stop:
                time.sleep(self.cfg.dashboard_retry_s)

    def _read(self, sock: socket.socket) -> None:
        try:
            for line in sock.makefile("rb"):
                if len(line) > 64 * 1024:
                    break
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(message, dict) and message.get("type") == "command" and isinstance(message.get("command"), str):
                    with self._cond:
                        self._commands.append(message)
        except (OSError, ValueError):
            pass
        self.connected = False
        with self._cond:
            self._cond.notify_all()
