"""커넥톰 연산 서버(brain_server/server.py)에 붙는 뇌 — MaleCNSBrain과 같은 step()/reset()으로 쓴다.

    brain = RemoteBrain("127.0.0.1:8765")       # 서버 주소: 설정 brain_server 또는 환경 변수 REFLEXGUARD_BRAIN_SERVER
    decision = brain.step(looming, dt_ms)        # BrainDecision, 또는 판단이 없으면 None (→ 공유 제어가 안전 정지)

스텝마다 루밍을 보내고 그 스텝의 답을 brain_server_wait_ms까지 기다린다. 늦으면 brain_server_stale_ms보다 오래되지 않은
직전 답을 쓰고, 그것도 없으면 None(판단 없음)을 돌려준다 — 서버가 꺼지거나 네트워크가 끊겨도 로봇이 판단 없이 달리지 않는다.
연결이 끊기면 뒤에서 계속 다시 접속한다. 받은 답은 그대로 믿지 않고 값의 범위를 확인한다.
"""

from __future__ import annotations

import json
import math
import os
import socket
import threading
import time
from typing import Any

import numpy as np

from connectome.brain import BrainDecision, Signal
from common.config import CONFIG, Config

ENV_VAR = "REFLEXGUARD_BRAIN_SERVER"


def brain_server_address(cfg: Config = CONFIG) -> str:
    """쓸 서버 주소 ("host:port"). 환경 변수가 설정보다 우선. 비었으면 서버를 쓰지 않는다(기기 안에서 계산)."""
    return os.environ.get(ENV_VAR, "").strip() or cfg.brain_server.strip()


def _split_address(address: str) -> tuple[str, int]:
    host, _, port = address.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"brain server address must be host:port, got {address!r}")
    return host.strip("[]"), int(port)


def _unit(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not finite")
    return min(1.0, max(0.0, number))


def decision_from_message(message: dict[str, Any]) -> BrainDecision:
    """서버의 decision 메시지 → BrainDecision (값 범위를 확인한다)."""
    top = [(str(item[0]), str(item[1]), float(item[2])) for item in message.get("top", [])[:10]]
    return BrainDecision(_unit(message["escape"]), Signal(message["signal"]), _unit(message["turn_left"]),
                         _unit(message["turn_right"]), top, str(message.get("model", "")),
                         _unit(message.get("near_left", 0.0)), _unit(message.get("near_right", 0.0)))


class RemoteBrain:
    """서버에서 계산하는 커넥톰 뇌. 접속·수신은 뒤의 스레드가 맡고, step()은 짧게 기다렸다 돌아온다."""

    def __init__(self, address: str, cfg: Config = CONFIG) -> None:
        self.cfg = cfg
        self.address = address
        self.host, self.port = _split_address(address)
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._cond = threading.Condition()
        self._seq = 0
        self._latest: tuple[int, BrainDecision, float] | None = None  # (seq, 판단, 받은 시각)
        self._angles_sent = False
        self._stop = False
        self.connected = False
        self.model = ""
        self.round_trip_ms: float | None = None  # 마지막 스텝의 왕복 시간
        self._sent_at: dict[int, float] = {}
        self._thread = threading.Thread(target=self._run, name="brain-client", daemon=True)
        self._thread.start()

    # --- MaleCNSBrain과 같은 쓰임 ---
    def step(self, looming: Any, dt_ms: int) -> BrainDecision | None:
        self._seq += 1
        seq = self._seq
        message: dict[str, Any] = {"type": "step", "seq": seq, "dt_ms": int(max(1, dt_ms))}
        cells = getattr(looming, "cells", None)
        angles = getattr(looming, "cell_angles", None)
        if cells is not None and angles is not None:
            message["cells"] = np.round(np.asarray(cells, dtype=np.float64), 4).tolist()
            flow = getattr(looming, "flow", None)
            if flow is not None:  # 칸별 옆 흐름(도/초) — 서버의 HS 회로 입력
                message["flow"] = np.round(np.asarray(flow, dtype=np.float64), 2).tolist()
            if not self._angles_sent:  # 칸 각도는 접속마다 처음 한 번만 (카메라가 같으면 바뀌지 않는다)
                message["angles"] = np.round(np.asarray(angles, dtype=np.float64), 3).tolist()
        elif looming is not None:
            message["left"], message["right"] = float(looming.left), float(looming.right)
        if self._send(message) and "angles" in message:
            self._angles_sent = True
        deadline = time.monotonic() + self.cfg.brain_server_wait_ms / 1000.0
        with self._cond:
            while (self._latest is None or self._latest[0] < seq) and self.connected:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            latest = self._latest
        if latest is None or time.monotonic() - latest[2] > self.cfg.brain_server_stale_ms / 1000.0:
            return None  # 판단 없음 → 공유 제어가 안전 정지 (안전 스위치가 켜져 있으면 수동 주행)
        return latest[1]

    def reset(self) -> None:
        self._send({"type": "reset"})
        with self._cond:
            self._latest = None

    def close(self) -> None:
        self._stop = True
        self._drop_connection()

    def status(self) -> dict[str, Any]:
        """웹 화면·기록용: 어느 서버에, 붙어 있는지, 응답 시간."""
        return {"remote": True, "address": self.address, "connected": self.connected,
                "rtt_ms": None if self.round_trip_ms is None else round(self.round_trip_ms, 1), "model": self.model}

    # --- 뒤에서 접속·수신 ---
    def _send(self, message: dict[str, Any]) -> bool:
        sock = self._sock
        if sock is None or not self.connected:
            return False
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with self._send_lock:
                if message.get("type") == "step":
                    self._sent_at[message["seq"]] = time.monotonic()
                    if len(self._sent_at) > 64:
                        self._sent_at.pop(next(iter(self._sent_at)))
                sock.sendall(data)
            return True
        except OSError:
            self._drop_connection()
            return False

    def _drop_connection(self) -> None:
        sock, self._sock = self._sock, None
        with self._cond:
            self.connected = False
            self._cond.notify_all()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _run(self) -> None:
        while not self._stop:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self.cfg.brain_server_connect_timeout_s)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # 작은 메시지를 바로 보낸다
                sock.settimeout(None)
            except OSError:
                time.sleep(self.cfg.brain_server_retry_s)
                continue
            self._sock = sock
            self._angles_sent = False  # 새 접속 = 새 뇌: 칸 각도를 다시 보낸다
            with self._cond:
                self.connected = True
            try:
                reader = sock.makefile("rb")
                for line in reader:
                    self._receive(line)
            except (OSError, ValueError):
                pass
            self._drop_connection()
            if not self._stop:
                time.sleep(self.cfg.brain_server_retry_s)

    def _receive(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(message, dict):
            return
        if message.get("type") == "hello":
            self.model = str(message.get("model", ""))
            return
        if message.get("type") != "decision" or not isinstance(message.get("seq"), int):
            return  # error 등은 무시한다 (그 스텝은 답이 없는 것으로 처리된다)
        try:
            decision = decision_from_message(message)
        except (KeyError, TypeError, ValueError, IndexError):
            return
        now = time.monotonic()
        sent = self._sent_at.pop(message["seq"], None)
        if sent is not None:
            self.round_trip_ms = (now - sent) * 1000.0
        with self._cond:
            if self._latest is None or message["seq"] > self._latest[0]:
                self._latest = (message["seq"], decision, now)
            self._cond.notify_all()
