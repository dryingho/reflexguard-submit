"""관제 대시보드(dashboard/server.py)에 붙는 연결 — Webots 컨트롤러와 커넥톰 연산 서버가 쓴다.

    link = DashboardLink("127.0.0.1:8766", role="webots", name="reflexguard", hello={...})
    link.send({"type": "state", ...})          # 막히지 않는다: 보낼 줄에 넣고 뒤의 스레드가 보낸다
    for command in link.take_commands(): ...   # 대시보드가 보낸 명령 ({"command": "connectome", "value": "off"} 등)

대시보드가 꺼져 있거나 끊겨도 로봇은 그대로 돈다(관제만 빠진다). 뒤에서 계속 다시 접속하고, 접속할 때마다 hello를 다시 보낸다.
보낼 줄이 넘치면(대시보드가 느리거나 끊김) 오래된 상태부터 버린다 — 사건(event)은 되도록 남긴다.
통신: TCP 위에 한 줄에 JSON 하나 (dashboard/server.py 맨 위 설명). 토큰(REFLEXGUARD_TOKEN 또는 logs/token — common/auth.py),
장치 열쇠로 붙는 PC면 그 열쇠(common/device_auth.py)를 hello에 싣는다.
"""

from __future__ import annotations

import collections
import json
import os
import socket
import threading
import time
from typing import Any

from common.auth import client_token
from common.device_auth import CommandGuard, device_key, own_device
from common.tls import connect, split_address
from common.config import CONFIG, Config

ENV_VAR = "REFLEXGUARD_DASHBOARD"
_MAX_QUEUE = 500
PING_S = 30.0  # 보낼 것이 이만큼 없으면 ping — 관제가 조용한 접속을 닫지 않게 (dashboard/server.py AGENT_IDLE_S)


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
        self.host, self.port, self.tls = split_address(address)  # tls://host:port면 TLS + 인증서 지문 확인 (common/tls.py)
        self.address = address
        self.cfg = cfg
        device_name, own_key = own_device()  # 장치 열쇠로 붙는 PC면 그 이름·열쇠 (common/device_auth.py)
        if device_name:
            name = device_name  # 열쇠는 이 이름에만 통한다
        self.hello = {"type": "hello", "role": role, "name": name, **(hello or {})}
        token = client_token()
        if own_key:
            self.hello["device_key"] = own_key
        elif token:
            self.hello["token"] = token
        # 받은 명령 확인 (서명·대상·접속 nonce·순번). 자격증명이 아예 없으면 서버도 인증을 쓰지 않는 것 (시험) — 확인하지 않는다
        command_key = own_key or (device_key(token, role, name) if token else "")
        self.guard = CommandGuard(command_key, name) if command_key else None
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
                sock = connect(self.address, timeout=self.cfg.dashboard_connect_timeout_s)
                sock.settimeout(None)
            except OSError:
                time.sleep(self.cfg.dashboard_retry_s)
                continue
            self._sock = sock
            self.connected = True
            if self.guard is not None:
                self.hello["conn"] = self.guard.new_connection()  # 이번 접속의 nonce — 명령에 그대로 돌아와야 한다
            reader = threading.Thread(target=self._read, args=(sock,), name="dashboard-read", daemon=True)
            reader.start()
            try:
                sock.sendall((json.dumps(self.hello, default=_json_default) + "\n").encode())
                last_sent = time.monotonic()
                while not self._stop and self.connected:
                    with self._cond:
                        while not self._outbox and not self._stop and self.connected and time.monotonic() - last_sent < PING_S:
                            self._cond.wait(0.5)
                        batch = b"".join(self._outbox) or (b'{"type":"ping"}\n' if time.monotonic() - last_sent >= PING_S else b"")
                        self._outbox.clear()
                    if batch:
                        sock.sendall(batch)
                        last_sent = time.monotonic()
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
                    why = self.guard.check(message) if self.guard is not None else ""
                    if why:  # 위조·재전송·다른 장치 앞 명령 — 따르지 않고 관제에 알린다
                        self.send({"type": "event", "event": {"type": "command_rejected", "why": why,
                                                              "command": str(message.get("command"))[:40]}})
                        continue
                    with self._cond:
                        self._commands.append(message)
        except (OSError, ValueError):
            pass
        self.connected = False
        with self._cond:
            self._cond.notify_all()
