"""커넥톰 연산 서버: 칸별 루밍을 받아 MaleCNS 축소 커넥톰(LPLC2→DNp01) LIF와 디코더를 돌리고 판단을 돌려준다.

Webots 컨트롤러(또는 다른 카메라를 쓰는 로봇)는 카메라 영상에서 루밍만 재서 보내고(숫자 수십 개), 커넥톰 계산은 이 서버가 맡는다.
원본 ReflexGuard(mrgrit/ReflexGuard)의 brain_server와 같은 역할이다. 다른 PC와는 TLS(지문 고정)와 등록된 클라이언트 인증서(mTLS)로 붙는다 — common/tls.py, common/mtls.py.
접속은 토큰으로 확인한다 (REFLEXGUARD_TOKEN 또는 logs/token — common/auth.py, KISA 구현 2-1).

실행:  python brain_server/server.py                      (같은 PC에서만 접속: 127.0.0.1:8765)
       python brain_server/server.py --host 0.0.0.0       (다른 PC에서도 접속 — 127.0.0.1이 아니면 TLS. 같은 네트워크 안에서만 쓰세요)
컨트롤러 쪽:  REFLEXGUARD_BRAIN_SERVER=127.0.0.1:8765 webots simulation/webots/worlds/road.wbt   (wheelchair/brain_client.py)
관제:  REFLEXGUARD_DASHBOARD=127.0.0.1:8766 (또는 --dashboard) 이면 관제 대시보드(dashboard/server.py)에 접속한 뇌들의
       상태(계산 횟수·평균 계산 시간·마지막 판단)와 '루밍 동작 보기'의 뉴런 발화율(LPLC2·DNp01 — 초당 REPORT_HZ번,
       접속마다 장치 이름·주소와 함께. 대시보드는 기록 파일에 쓰지 않는다), 접속·끊김 사건을 보내고, '모든 뇌 상태 초기화' 명령을 받는다.

통신: TCP 위에 한 줄에 JSON 하나 (UTF-8, 줄바꿈으로 끝). 접속 하나 = 뇌 하나(막전위·디코더 상태를 따로 갖는다).
    클라이언트 → 서버
        {"type": "step", "seq": 7, "dt_ms": 32, "cells": [[...], ...], "flow": [[...], ...], "angles": [[[가로, 세로], ...], ...]}
            칸별 루밍(1/초), 칸별 옆 흐름(도/초, HS 회로 입력, 선택), 칸마다 카메라 각도(도).
            angles는 접속 뒤 처음 한 번(또는 바뀔 때)만 보내면 된다.
            칸별 루밍이 없으면 "left"·"right"(좌/우 루밍 하나씩)를, 루밍이 아예 없으면(첫 프레임) 둘 다 빼고 보낸다.
        {"type": "reset"}                      뇌 상태를 비운다 (충돌 뒤 다시 시작, 커넥톰 다시 연결)
        {"type": "auth", "token": "<토큰>"}     접속하자마자 첫 줄 (서버가 토큰을 쓰면 — 언제나 쓴다. 틀리면 error 뒤 끊는다)
        {"type": "auth", "role": "webots", "name": "<장치 이름>", "device_key": "<장치 열쇠>"}   휠체어 PC는 토큰 대신 장치 열쇠로
    서버 → 클라이언트
        {"type": "hello", "protocol": 1, "model": "<모델 버전>"}     인증을 통과하자마자 한 번
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

from common.auth import CONTROL, WeakToken, load_token, role_for  # noqa: E402
from common.device_auth import check_device  # noqa: E402
from common.mtls import check_peer, enable as enable_mtls, handle_enroll  # noqa: E402
from common.limits import ConnectionLimit, FailureLock  # noqa: E402
from common.tls import TLSServerMixin, certificate, file_fingerprint, lan_addresses, server_context  # noqa: E402
from common.config import CONFIG  # noqa: E402
from common.dashboard_link import DashboardLink, dashboard_address  # noqa: E402
from connectome.brain import MaleCNSBrain  # noqa: E402

PROTOCOL = 1
DEFAULT_PORT = 8765
MAX_LINE = 256 * 1024     # 한 줄의 최대 길이(바이트) — 이보다 길면 접속을 끊는다
REPORT_HZ = 5.0            # 관제로 상태 요약·뉴런 발화율을 보내는 초당 횟수
MAX_CONNECTIONS = 16       # 동시 접속(=뇌) 수 상한 — 접속마다 회로 하나를 메모리에 올린다 (KISA 구현 5-2)
IDLE_S = 120.0             # 이만큼 요청이 없으면 접속(뇌)을 닫는다 — 자리를 붙잡아 두지 못하게. 휠체어 PC는 다시 붙는다
FIELDS = {                 # 요청 종류마다 받는 칸 — 모르는 칸이 있으면 거부한다 (KISA 구현 1-15·5-5)
    "step": {"type", "seq", "dt_ms", "cells", "flow", "angles", "left", "right"},
    "reset": {"type"},
    "auth": {"type", "token", "role", "name", "device_key"},
}
AUTH_WAIT_S = 10.0         # 접속 뒤 토큰(auth 줄)을 기다리는 시간
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

    def __init__(self, peer: str = "", device: str = "", watched: bool = False) -> None:
        self.brain = MaleCNSBrain(CONFIG)
        self.angles: np.ndarray | None = None
        self.peer = peer
        self.device = device    # 장치 열쇠로 붙었으면 그 장치 이름 (대시보드가 로봇 카드와 맞춘다)
        self.watched = watched  # 관제가 있으면 계산마다 뉴런 발화율을 남겨 둔다 (Circuit.view)
        self.view: dict[str, Any] | None = None
        self.since = time.strftime("%H:%M:%S")
        self.steps = 0
        self.compute_ms_total = 0.0
        self.last: dict[str, Any] = {}
        self.reset_requested = False  # 관제에서 '초기화'를 눌렀다 → 이 접속의 스레드가 다음 요청 전에 비운다

    def status(self) -> dict[str, Any]:
        return {"peer": self.peer, "device": self.device, "since": self.since, "steps": self.steps,
                "avg_ms": round(self.compute_ms_total / self.steps, 3) if self.steps else None,
                "escape": self.last.get("escape"), "signal": self.last.get("signal")}

    def step(self, request: dict[str, Any]) -> dict[str, Any]:
        seq = request.get("seq")
        if seq is not None and (not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 2**53):
            raise BadRequest("seq: integer")
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
        if self.watched:
            self.view = self.brain.circuit.view()
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
        failures: FailureLock = self.server.auth_failures  # type: ignore[attr-defined]
        if failures.locked(self.client_address[0]):  # 토큰을 거듭 틀린 주소 (KISA 구현 2-16)
            return
        verdict = self._authenticated()
        if verdict is not True:
            if verdict is False:  # 틀린 토큰만 센다 — 붙었다 그냥 끊은 것(포트 확인 등)은 세지 않는다
                failures.failed(self.client_address[0])
                self.server.log(f"refused {peer}: bad token")  # type: ignore[attr-defined]
            return
        failures.succeeded(self.client_address[0])
        self.connection.settimeout(IDLE_S)  # 조용한 접속은 닫는다 (세션 수명)
        session = Session(peer, self.device, self.server.dashboard is not None)  # type: ignore[attr-defined]
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
                    unknown = set(request) - FIELDS.get(str(kind), set())
                    if kind in FIELDS and unknown:
                        raise BadRequest(f"unknown field {sorted(unknown)[0]!r}"[:80])
                    if kind == "step":
                        self._send(session.step(request))
                    elif kind == "reset":
                        session.brain.reset()
                    elif kind == "auth":  # 토큰을 쓰지 않는 서버(시험용)에 클라이언트가 실어 보낸 토큰 — 할 일 없음
                        pass
                    else:
                        raise BadRequest(f"unknown type {kind!r}")
                except BadRequest as error:  # 우리가 쓴 짧은 설명만 돌려준다
                    self._send({"type": "error", "message": str(error)[:200]})
                except (json.JSONDecodeError, UnicodeDecodeError):  # 파서 내부 문구는 돌려주지 않는다 (KISA 구현 4-1)
                    self._send({"type": "error", "message": "bad json"})
        except (ConnectionError, OSError):
            pass
        finally:
            self.server.remove_session(session)  # type: ignore[attr-defined]

    def _authenticated(self) -> bool | None:
        """첫 줄이 맞는 토큰의 auth면 참, 틀렸으면 거짓, 아무것도 오지 않았으면(끊김·10초 넘김) None.
        서버가 토큰을 쓰지 않으면 언제나 참. 장치 열쇠로 붙으면 그 장치 이름을 self.device에 남긴다."""
        self.device = ""
        token = self.server.token  # type: ignore[attr-defined]
        if not token:
            return True
        try:
            self.connection.settimeout(AUTH_WAIT_S)
            line = self.rfile.readline(MAX_LINE + 1)
            self.connection.settimeout(None)
            if not line:
                return None
            message = json.loads(line)
        except OSError:
            return None
        except ValueError:
            message = None
        if isinstance(message, dict) and message.get("type") == "enroll":  # 클라이언트 인증서 등록 (common/mtls.py)
            try:
                self._send(handle_enroll(message, token))
            except OSError:
                pass
            return None
        by_token = isinstance(message, dict) and role_for(token, str(message.get("token", ""))) == CONTROL
        by_device = isinstance(message, dict) and check_device(token, str(message.get("role", "")), str(message.get("name", "")),
                                                               str(message.get("device_key", "")))
        if isinstance(message, dict) and message.get("type") == "auth" and (by_token or by_device):
            problem = check_peer(self.connection, self.client_address, str(message.get("name", "")), by_device)
            if problem:  # 다른 PC의 TLS 접속은 등록된 클라이언트 인증서까지 (mTLS — KISA 구현 2-11)
                try:
                    self._send({"type": "error", "message": problem})
                except OSError:
                    pass
                return None
            self.device = str(message.get("name", ""))[:80] if by_device else ""
            return True
        try:
            self._send({"type": "error", "message": "bad token"})
        except OSError:
            pass
        return False

    def _send(self, message: dict[str, Any]) -> None:
        self.wfile.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        self.wfile.flush()


class BrainServer(TLSServerMixin, ConnectionLimit, socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    max_connections = MAX_CONNECTIONS

    def __init__(self, address: tuple[str, int], quiet: bool = False, dashboard: DashboardLink | None = None,
                 token: str = "") -> None:
        super().__init__(address, Handler)
        self.token = token  # 비면 인증 없음 (시험용). main()은 언제나 토큰을 준다
        self.auth_failures = FailureLock()
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
        """관제: 초당 REPORT_HZ번 상태를 보내고, 받은 명령을 처리한다 (대시보드가 있을 때만 도는 스레드).
        상태 요약(sessions)은 늘 싣고, 접속마다 마지막 계산의 뉴런 발화율(neurons — 루밍 동작 보기)을 함께 싣는다."""
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
                neurons = [{"peer": session.peer, "device": session.device, "view": session.view}
                           for session in self.sessions if session.view is not None]
            self.dashboard.send({"type": "state", "sessions": sessions, "total_steps": total, "neurons": neurons})
            time.sleep(1.0 / REPORT_HZ)

    def log(self, text: str) -> None:
        if not self.quiet:
            with self._lock:
                print(f"[brain-server] {text}", flush=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ReflexGuard connectome (MaleCNS LPLC2->DNp01 LIF) server")
    parser.add_argument("--host", default="127.0.0.1", help="받을 주소 (기본 127.0.0.1: 같은 PC만. 0.0.0.0: 모든 곳)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--tls", action=argparse.BooleanOptionalAction, default=None,
                        help="TLS로 받는다 (기본: 127.0.0.1이 아닌 주소로 받을 때 켬). 같은 PC의 평문은 그대로 (common/tls.py)")
    parser.add_argument("--dashboard", default="", help="관제 대시보드 host:port (기본: 환경 변수 REFLEXGUARD_DASHBOARD)")
    args = parser.parse_args(argv)
    dash_address = args.dashboard or dashboard_address(CONFIG)
    dashboard = None
    if dash_address:
        model = MaleCNSBrain(CONFIG).circuit.model_version
        dashboard = DashboardLink(dash_address, "brain", f"brain-server@{socket.gethostname()}",
                                  hello={"model": model, "listen": f"{args.host}:{args.port}"})
    try:
        token = load_token(create=True)  # 환경 변수 또는 logs/token (없으면 만든다)
    except WeakToken as error:
        raise SystemExit(f"[brain-server] 오류: {error}") from error
    use_tls = (args.host not in ("127.0.0.1", "localhost", "::1")) if args.tls is None else args.tls
    with BrainServer((args.host, args.port), dashboard=dashboard, token=token) as server:
        if use_tls:  # 다른 PC와는 암호화해서 (KISA 설계 SR2-8)
            cert, key = certificate(lan_addresses())
            server.tls = enable_mtls(server_context(cert, key))  # 다른 PC는 등록된 클라이언트 인증서까지 (mTLS)
            server.log(f"listening on {args.host}:{args.port} (token required, TLS — same-PC plain allowed)")
            for address in lan_addresses():
                server.log(f"other PCs: REFLEXGUARD_TLS_PIN={file_fingerprint(cert)} ... --server tls://{address}:{args.port}")
        else:
            server.log(f"listening on {args.host}:{args.port} (token required, no encryption — this PC only)")
        if dashboard is not None:
            server.log(f"dashboard: {dash_address}")
            threading.Thread(target=server.report_forever, name="dashboard-report", daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.log("stopped")


if __name__ == "__main__":
    main(sys.argv[1:])
