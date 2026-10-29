"""커넥톰 연산 서버: 칸별 루밍을 받아 MaleCNS 축소 커넥톰(LPLC2→DNp01) LIF와 디코더를 돌리고 판단을 돌려준다.

Webots 컨트롤러(또는 다른 카메라를 쓰는 로봇)는 카메라 영상에서 루밍만 재서 보내고(숫자 수십 개), 커넥톰 계산은 이 서버가 맡는다.
본편 ReflexGuard의 brain_server와 같은 역할이다 (본편의 mTLS·서명·관제는 이 단계에 없다 — 암호화하지 않는다).

실행:  python brain_server/server.py                      (같은 PC에서만 접속: 127.0.0.1:8765)
       python brain_server/server.py --host 0.0.0.0       (다른 PC에서도 접속 — 같은 네트워크 안에서만 쓰세요. 암호화 없음)
컨트롤러 쪽:  REFLEXGUARD_BRAIN_SERVER=127.0.0.1:8765 webots simulation/webots/worlds/road.wbt   (wheelchair/brain_client.py)
관제:  REFLEXGUARD_DASHBOARD=127.0.0.1:8766 (또는 --dashboard) 이면 관제 대시보드(dashboard/server.py)에 1초마다 접속한 뇌들의
       상태(계산 횟수·평균 계산 시간·마지막 판단)와 접속·끊김 사건을 보내고, '모든 뇌 상태 초기화' 명령을 받는다.

통신: TCP 위에 한 줄에 JSON 하나 (UTF-8, 줄바꿈으로 끝). 접속 하나 = 뇌 하나(막전위·디코더 상태를 따로 갖는다).
    클라이언트 → 서버
        {"type": "step", "seq": 7, "dt_ms": 32, "cells": [[...], ...], "flow": [[...], ...], "angles": [[[가로, 세로], ...], ...]}
            칸별 루밍(1/초), 칸별 옆 흐름(도/초, HS 회로 입력, 선택), 칸마다 카메라 각도(도).
            angles는 접속 뒤 처음 한 번(또는 바뀔 때)만 보내면 된다.
            칸별 루밍이 없으면 "left"·"right"(좌/우 루밍 하나씩)를, 루밍이 아예 없으면(첫 프레임) 둘 다 빼고 보낸다.
        {"type": "reset"}                      뇌 상태를 비운다 (충돌 뒤 다시 시작, 커넥톰 다시 연결)
    서버 → 클라이언트
        {"type": "hello", "protocol": 1, "model": "<모델 버전>"}     접속하자마자 한 번
        {"type": "decision", "seq": 7, "escape": .., "signal": "STOP", "turn_left": .., "turn_right": .., "near_left": .., "near_right": ..,
         "top": [[뉴런 id, 종류, 발화율], ...], "model": "...", "compute_ms": ..}
        {"type": "error", "message": "..."}    잘못된 요청 (접속은 유지한다)
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import socketserver
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # python brain_server/server.py로 바로 실행해도 프로젝트 패키지를 찾게

from common.config import CONFIG  # noqa: E402
from common.dashboard_link import DashboardLink, dashboard_address  # noqa: E402
from connectome.brain import MaleCNSBrain  # noqa: E402

PROTOCOL = 1
DEFAULT_PORT = 8765
MAX_LINE = 256 * 1024     # 한 줄의 최대 길이(바이트) — 이보다 길면 접속을 끊는다
MAX_GRID = 64             # 칸 수 상한 (가로·세로 각각)


class BadRequest(ValueError):
    pass


def _grid(value: Any, depth: int, name: str) -> np.ndarray:
    """JSON 배열 → 유한한 숫자로 된 numpy 배열 (네트워크에서 온 값이므로 모양·값을 확인한다)."""
    try:
        array = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise BadRequest(f"{name}: not a number grid") from error
    if array.ndim != depth or not all(0 < n <= MAX_GRID for n in array.shape[:2]):
        raise BadRequest(f"{name}: bad shape {array.shape}")
    if not np.isfinite(array).all():
        raise BadRequest(f"{name}: not finite")
    return array


class Session:
    """접속 하나의 뇌. 칸 각도는 한 번 받으면 기억한다. 관제용으로 계산 횟수·시간·마지막 판단을 센다."""

    def __init__(self, peer: str = "") -> None:
        self.brain = MaleCNSBrain(CONFIG)
        self.angles: np.ndarray | None = None
        self.peer = peer
        self.since = time.strftime("%H:%M:%S")
        self.steps = 0
        self.compute_ms_total = 0.0
        self.last: dict[str, Any] = {}
        self.reset_requested = False  # 관제에서 '초기화'를 눌렀다 → 이 접속의 스레드가 다음 요청 전에 비운다

    def status(self) -> dict[str, Any]:
        return {"peer": self.peer, "since": self.since, "steps": self.steps,
                "avg_ms": round(self.compute_ms_total / self.steps, 3) if self.steps else None,
                "escape": self.last.get("escape"), "signal": self.last.get("signal")}

    def step(self, request: dict[str, Any]) -> dict[str, Any]:
        dt_ms = request.get("dt_ms")
        if not isinstance(dt_ms, int) or not 1 <= dt_ms <= 1000:
            raise BadRequest("dt_ms: integer 1..1000")
        if request.get("angles") is not None:
            self.angles = _grid(request["angles"], 3, "angles")
        looming: Any = None
        if request.get("cells") is not None:
            cells = _grid(request["cells"], 2, "cells")
            if self.angles is None or self.angles.shape[:2] != cells.shape:
                raise BadRequest("angles: send the camera angles of every cell first")
            flow = _grid(request["flow"], 2, "flow") if request.get("flow") is not None else None
            if flow is not None and flow.shape != cells.shape:
                raise BadRequest("flow: same shape as cells")
            looming = SimpleNamespace(cells=cells, cell_angles=self.angles, flow=flow)
        elif "left" in request or "right" in request:
            left, right = request.get("left", 0.0), request.get("right", 0.0)
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (left, right)):
                raise BadRequest("left/right: numbers")
            looming = SimpleNamespace(left=float(left), right=float(right))
        if self.reset_requested:
            self.reset_requested = False
            self.brain.reset()
        started = time.perf_counter()
        decision = self.brain.step(looming, dt_ms)
        compute_ms = (time.perf_counter() - started) * 1000
        self.steps += 1
        self.compute_ms_total += compute_ms
        self.last = {"escape": round(float(decision.escape), 3), "signal": decision.signal.value}
        return {
            "type": "decision", "seq": request.get("seq"),
            "escape": round(float(decision.escape), 4), "signal": decision.signal.value,
            "turn_left": round(float(decision.turn_left), 4), "turn_right": round(float(decision.turn_right), 4),
            "near_left": round(float(decision.near_left), 4), "near_right": round(float(decision.near_right), 4),
            "top": [[str(i), str(kind), round(float(rate), 2)] for i, kind, rate in decision.top_neurons],
            "model": decision.model_version, "compute_ms": round(compute_ms, 2),
        }


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        session = Session(peer)
        self.server.add_session(session)  # type: ignore[attr-defined]
        self._send({"type": "hello", "protocol": PROTOCOL, "model": session.brain.circuit.model_version})
        try:
            while True:
                line = self.rfile.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    self._send({"type": "error", "message": "line too long"})
                    break
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise BadRequest("not an object")
                    kind = request.get("type")
                    if kind == "step":
                        self._send(session.step(request))
                    elif kind == "reset":
                        session.brain.reset()
                    else:
                        raise BadRequest(f"unknown type {kind!r}")
                except (BadRequest, json.JSONDecodeError, UnicodeDecodeError) as error:
                    self._send({"type": "error", "message": str(error)[:200]})
        except (ConnectionError, OSError):
            pass
        finally:
            self.server.remove_session(session)  # type: ignore[attr-defined]

    def _send(self, message: dict[str, Any]) -> None:
        self.wfile.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        self.wfile.flush()


class BrainServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], quiet: bool = False, dashboard: DashboardLink | None = None) -> None:
        super().__init__(address, Handler)
        self.quiet = quiet
        self._lock = threading.Lock()
        self.sessions: list[Session] = []
        self.total_steps_closed = 0  # 끊긴 접속들이 했던 계산 횟수
        self.dashboard = dashboard

    def add_session(self, session: Session) -> None:
        with self._lock:
            self.sessions.append(session)
        self.log(f"connected {session.peer}")
        self._tell({"type": "session", "action": "connected", "peer": session.peer})

    def remove_session(self, session: Session) -> None:
        with self._lock:
            if session in self.sessions:
                self.sessions.remove(session)
                self.total_steps_closed += session.steps
        self.log(f"disconnected {session.peer} after {session.steps} steps")
        self._tell({"type": "session", "action": "disconnected", "peer": session.peer, "steps": session.steps})

    def _tell(self, event: dict[str, Any]) -> None:
        if self.dashboard is not None:
            self.dashboard.send({"type": "event", "event": event})

    def report_forever(self) -> None:
        """관제: 1초마다 상태를 보내고, 받은 명령을 처리한다 (대시보드가 있을 때만 도는 스레드)."""
        while self.dashboard is not None:
            for order in self.dashboard.take_commands():
                if order.get("command") == "reset_all":
                    with self._lock:
                        for session in self.sessions:
                            session.reset_requested = True
                        count = len(self.sessions)
                    self.log(f"reset all brains ({count}) by dashboard")
                    self._tell({"type": "reset_all", "count": count})
            with self._lock:
                sessions = [session.status() for session in self.sessions]
                total = self.total_steps_closed + sum(session.steps for session in self.sessions)
            self.dashboard.send({"type": "state", "sessions": sessions, "total_steps": total})
            time.sleep(1.0)

    def log(self, text: str) -> None:
        if not self.quiet:
            with self._lock:
                print(f"[brain-server] {text}", flush=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ReflexGuard connectome (MaleCNS LPLC2->DNp01 LIF) server")
    parser.add_argument("--host", default="127.0.0.1", help="받을 주소 (기본 127.0.0.1: 같은 PC만. 0.0.0.0: 모든 곳)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dashboard", default="", help="관제 대시보드 host:port (기본: 환경 변수 REFLEXGUARD_DASHBOARD)")
    args = parser.parse_args(argv)
    dash_address = args.dashboard or dashboard_address(CONFIG)
    dashboard = None
    if dash_address:
        model = MaleCNSBrain(CONFIG).circuit.model_version
        dashboard = DashboardLink(dash_address, "brain", f"brain-server@{socket.gethostname()}",
                                  hello={"model": model, "listen": f"{args.host}:{args.port}"})
    with BrainServer((args.host, args.port), dashboard=dashboard) as server:
        server.log(f"listening on {args.host}:{args.port} (no encryption)")
        if dashboard is not None:
            server.log(f"dashboard: {dash_address}")
            threading.Thread(target=server.report_forever, name="dashboard-report", daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.log("stopped")


if __name__ == "__main__":
    main(sys.argv[1:])
