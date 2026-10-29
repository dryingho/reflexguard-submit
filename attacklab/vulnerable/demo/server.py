"""체험·조작 포털: 한 주소로 Webots 시뮬레이션 조작과 휴대폰(이 기기) 카메라 체험을 함께 쓴다.

    python -m demo.server --webots-port 1234 --https --public   # 이 PC·휴대폰 모두 https://<이 PC 주소>:8443 (주소 하나)
    python -m demo.server                                       # 이 PC에서만 http://localhost:8443 (카메라 체험만 되고, Webots가 켜져 있으면 조작도)
    (scripts/run.sh가 관제 서버·연산 서버·Webots와 함께 켠다)

화면은 Webots 웹 조작 화면(simulation/webots/plugins/robot_windows/reflexguard/reflexguard.html) 하나다. 머리줄의
'휴대폰 카메라' 스위치가 꺼져 있으면 Webots 시뮬레이션(영상·조이스틱·제어)을, 켜면 이 기기의 카메라를 쓴다.
- 시뮬레이션: 화면이 연결하는 웹소켓("/")을 이 서버가 Webots(127.0.0.1:--webots-port)로 그대로 이어 준다. 그래서 휴대폰도
  HTTPS 주소 하나로 시뮬레이션을 조작한다 (Webots 웹 서버에 직접 붙어도 된다 — http://localhost:1234/robot_windows/…).
- 카메라: 화면이 줄인 밝기 영상(가로 240 px 안팎)을 POST /api/frame으로 보내면, 휠체어 PC 파이프라인(루밍 → 커넥톰 →
  공유 제어)을 그대로 돌려 흐름 상태·칸별 입력·작동 사건을 돌려준다. 화면은 시뮬레이션 때와 같은 패널에 그린다.
- 브라우저는 HTTPS(또는 같은 PC의 localhost)에서만 카메라를 열어 준다. --https면 자체 인증서를 logs/demo_certs/에 만들어
  쓴다(openssl 필요). 처음 접속할 때 '안전하지 않음' 경고를 한 번 넘겨야 한다. 포트는 하나 — 같은 포트로 온 http://는 https://로 넘긴다.
- 영상은 메모리에서 계산만 하고 저장하지 않는다. 기기마다 파이프라인을 하나씩 둔다 (최대 MAX_SESSIONS개, SESSION_IDLE_S초 쉬면 지운다).
- 같은 와이파이(내부망)에서만 쓴다. 인증이 없으므로 인터넷에 열지 않는다 (시뮬레이션 조작도 열린다).
- 관제 대시보드 주소(--dashboard 또는 REFLEXGUARD_DASHBOARD)가 있으면 카메라를 쓰는 기기마다 'phone' 역할로 접속해 흐름 상태와
  회피 반사 작동 사건을 보낸다 (30초 쉬면 접속을 닫는다). 영상은 보내지 않는다.

    GET  /                       → /robot_windows/reflexguard/reflexguard.html?name=reflexguard (조작 화면)
    GET  /api/portal             {"camera": true, "webots": {"port", "up"}} — 화면이 '휴대폰 카메라' 스위치를 보일지 정한다
    GET  / (Upgrade: websocket)  Webots로 이어 준다
    POST /api/frame?session=<id>&dt=<초>&turn=<도>&speed=<m/s>&fov=<도>[&pitch=<도>&height=<m>]
         본문: 밝기 영상(바이트, 가로 × 세로), 헤더 X-Width · X-Height
         답: {"rows", "cols", "input": [칸별 커넥톰 입력 0~1], "filtered": [통로 밖이라 거른 칸 0/1], "escape", "signal",
              "turn_left", "turn_right", "near_left", "near_right", "stage", "ms",
              "state": 흐름 상태(reflex_log.flow_snapshot + th·phone), "cells": 위험도 겹쳐 보기(webots_bridge.cells_message 꼴),
              "events": 이번 영상에서 생긴 회피 반사 사건(start/stage/end)}
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import socket
import types
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # python demo/server.py로 바로 실행해도 프로젝트 패키지를 찾게

from common.config import CONFIG, Config  # noqa: E402
from common.control import ControlCommand  # noqa: E402
from common.dashboard_link import DashboardLink, dashboard_address  # noqa: E402
from wheelchair.pipeline import WheelchairPipeline  # noqa: E402
from simulation.webots_bridge import CELLS_PREFIX, cells_message  # noqa: E402
from wheelchair.reflex_log import ReflexMonitor, flow_snapshot, thresholds  # noqa: E402

PAGE = ROOT / "simulation" / "webots" / "plugins" / "robot_windows" / "reflexguard" / "reflexguard.html"  # 조작 화면
PAGE_PATH = "/robot_windows/reflexguard/reflexguard.html"  # 화면이 이 경로 앞부분으로 웹소켓 주소를 만든다 (Webots와 같은 규칙)
CERT_DIR = ROOT / "logs" / "demo_certs"
MAX_SESSIONS = 6          # 동시에 체험하는 휴대폰 수
SESSION_IDLE_S = 30.0     # 이만큼 영상이 없으면 그 휴대폰의 파이프라인을 지운다
MAX_PIXELS = 320 * 320    # 한 영상의 최대 화소 수 (줄인 밝기 영상)
DRAIN_BYTES = 1024 * 1024  # 거절하는 요청도 이만큼까지는 본문을 읽고 답한다 (안 읽고 닫으면 보내던 쪽이 '연결 끊김'만 받는다)
MIN_FRAME_S = 1.0 / 30.0  # 한 휴대폰이 이보다 자주 보내면 거절한다 (초당 30장)
WALK_SPEED_MAX = 3.0      # 걷는 속도로 받는 최대값(m/s)
NO_POSE = types.SimpleNamespace(x=0.0, y=0.0, angle_deg=0.0)  # 휴대폰은 맵 위치가 없다 (흐름 상태에서 위치는 뺀다)


def phone_config(fov: float, pitch: float, height: float, base: Config = CONFIG) -> Config:
    """휴대폰 카메라용 설정: 화각·숙인 각·높이, 몸 가리개 없음 (휴대폰에는 휠체어 몸이 보이지 않는다)."""
    return dataclasses.replace(base, fov_deg=fov, camera_pitch_deg=pitch, camera_height=height, body_mask_file="none")


class Session:
    """휴대폰 하나: 휠체어 PC 파이프라인, 마지막 영상 시각, (있으면) 관제 대시보드 접속과 반사 작동 찾기."""

    def __init__(self, key: tuple[float, float, float], link: DashboardLink | None = None) -> None:
        self.key = key
        self.pipe = WheelchairPipeline(phone_config(*key))
        self.lock = threading.Lock()
        self.last = 0.0
        self.started = time.monotonic()
        self.frames = 0
        self.link = link
        self.monitor = ReflexMonitor(self.pipe.cfg, log_dir=None)  # 사건만 찾는다 (파일은 관제 서버가 남긴다)
        self.next_state = 0.0

    def configure(self, key: tuple[float, float, float]) -> None:
        """화각 등 카메라 값이 바뀌면 파이프라인만 새로 (대시보드 접속·작동 번호는 그대로)."""
        self.key = key
        self.pipe = WheelchairPipeline(phone_config(*key))
        if self.link is not None:
            camera = {"fov": key[0], "pitch": key[1], "height": key[2]}
            self.link.update_hello(**camera)                # 다시 접속할 때
            self.link.send({"type": "hello", **camera})      # 지금 접속에도

    def report(self, now: float, speed: float, user: ControlCommand, applied: ControlCommand,
               ms: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """이번 영상의 흐름 상태와 반사 작동 사건(start/stage/end)을 돌려준다(화면이 그린다). 관제 대시보드가 있으면
        사건은 바로, 흐름 상태는 초당 dashboard_state_hz번 그쪽에도 보낸다."""
        pipe, t = self.pipe, now - self.started
        decision = pipe.decision if pipe.connectome_on else None
        snapshot = flow_snapshot(t, NO_POSE, pipe.looming, decision, pipe.reflex, user, applied,
                                 pipe.override is not None, pipe.cfg)
        snapshot.pop("pose")
        events = self.monitor.update(snapshot)
        for event in events:
            event.pop("pose", None)
        if self.link is None:
            return snapshot, events
        for event in events:
            self.link.send({"type": "event", "event": event})
        if now < self.next_state:
            return snapshot, events
        self.next_state = now + 1.0 / max(pipe.cfg.dashboard_state_hz, 1e-9)
        inputs = pipe.cell_inputs
        shift = pipe.detector.last_shift
        self.link.send({"type": "state", "t": round(t, 2), "flow": snapshot, "th": thresholds(pipe.cfg),
                        "phone": {"fov": self.key[0], "pitch": self.key[1], "height": self.key[2], "speed": speed,
                                  "fps": round(self.frames / max(t, 1e-9), 1), "ms": ms, "frames": self.frames,
                                  "shake": [round(float(shift[0]), 1), round(float(shift[1]), 1)]},
                        "cells": {"rows": int(inputs.shape[0]), "cols": int(inputs.shape[1]),
                                  "input": [round(float(v), 1) for v in inputs.ravel()]} if inputs is not None else None})
        return snapshot, events

    def close(self) -> None:
        if self.link is not None:
            for event in self.monitor.close():
                self.link.send({"type": "event", "event": event})
            self.link.flush()
            self.link.close()


class Demo:
    def __init__(self, dashboard: str = "") -> None:
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        self.dashboard = dashboard  # 관제 대시보드 "host:port" (비면 붙지 않는다)
        self.count = 0              # 지금까지 온 휴대폰 수 (대시보드에 보일 이름)

    def _link(self, client: str, key: tuple[float, float, float]) -> DashboardLink | None:
        if not self.dashboard:
            return None
        self.count += 1
        return DashboardLink(self.dashboard, "phone", f"휴대폰 {self.count}@{socket.gethostname()}",
                             hello={"client": client, "fov": key[0], "pitch": key[1], "height": key[2]})

    def session(self, sid: str, key: tuple[float, float, float], client: str = "") -> Session | None:
        """휴대폰의 세션 (카메라 값이 바뀌었으면 파이프라인만 새로). 자리가 없으면 None."""
        now = time.monotonic()
        with self.lock:
            idle = [k for k, s in self.sessions.items() if now - s.last > SESSION_IDLE_S]
            closing = [self.sessions.pop(k) for k in idle]
            current = self.sessions.get(sid)
            if current is None and len(self.sessions) < MAX_SESSIONS:
                current = self.sessions[sid] = Session(key, self._link(client, key))
        for old in closing:
            threading.Thread(target=old.close, daemon=True).start()  # 마지막 사건을 보내고 닫는다 (기다리지 않게)
        return current

    def close(self) -> None:
        with self.lock:
            sessions, self.sessions = list(self.sessions.values()), {}
        for session in sessions:
            session.close()

    def frame(self, sid: str, gray: np.ndarray, dt: float, turn: float, speed: float,
              key: tuple[float, float, float], client: str = "") -> tuple[int, dict[str, Any]]:
        session = self.session(sid, key, client)
        if session is None:
            return 503, {"error": "too many phones"}
        with session.lock:
            now = time.monotonic()
            if now - session.last < MIN_FRAME_S:
                return 429, {"error": "too fast"}
            session.last = now
            session.frames += 1
            if session.key != key:
                session.configure(key)
            started = time.perf_counter()
            pipe = session.pipe
            user = ControlCommand(forward=min(1.0, speed / max(pipe.cfg.move_speed, 1e-9)) if speed > 0 else 0.0)
            pipe.moved(turn, speed, dt, user, user)  # 휴대폰은 명령대로 움직이지 않는다 (사람이 들고 다닌다)
            pipe.see_gray(gray.astype(np.float32), gray.shape, dt)
            wish = user if speed > 0 else ControlCommand(forward=1.0)  # 서 있어도 '앞으로 가려 한다'고 보고 판단
            applied = pipe.decide(wish, dt)
            decision = pipe.decision
            inputs, filtered = pipe.cell_inputs, pipe.cell_filtered
            reply = {
                "rows": int(inputs.shape[0]) if inputs is not None else 0,
                "cols": int(inputs.shape[1]) if inputs is not None else 0,
                "input": [round(float(v), 2) for v in inputs.ravel()] if inputs is not None else [],
                "filtered": [int(v) for v in filtered.ravel()] if filtered is not None else [],
                "escape": round(float(getattr(decision, "escape", 0.0)), 3),
                "signal": str(getattr(getattr(decision, "signal", None), "value", "NONE")),
                "turn_left": round(float(getattr(decision, "turn_left", 0.0)), 3),
                "turn_right": round(float(getattr(decision, "turn_right", 0.0)), 3),
                "near_left": round(float(getattr(decision, "near_left", 0.0)), 3),
                "near_right": round(float(getattr(decision, "near_right", 0.0)), 3),
                "stage": pipe.reflex.stage,
                "ms": round(1000.0 * (time.perf_counter() - started), 1),
            }
            snapshot, events = session.report(now, speed, wish, applied, reply["ms"])
            shift = pipe.detector.last_shift
            reply["state"] = {**snapshot, "th": thresholds(pipe.cfg), "camera": "phone",
                              "phone": {"fov": key[0], "speed": speed, "ms": reply["ms"],
                                        "shake": [round(float(shift[0]), 1), round(float(shift[1]), 1)]}}
            reply["cells"] = json.loads(cells_message(inputs, pipe.detector.cell_front, filtered)[len(CELLS_PREFIX):])
            reply["events"] = events
            if pipe.reflex.stage == "stop" and reply["signal"] == "NONE":
                # 휠체어는 정지한 뒤 사용자가 전진을 놓을 때까지 정지를 붙잡지만, 휴대폰을 든 사람은 놓지 않는다 →
                # 커넥톰이 더는 위험하다고 하지 않으면 풀어 준다 (사용자가 W를 뗐다 다시 누르는 것과 같다)
                pipe.reset()
            return 200, reply


def _listening(port: int) -> bool:
    """127.0.0.1:port가 접속을 받는지 (Webots가 켜져 있는지)."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
        return True
    except OSError:
        return False


def _shutdown(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _number(query: dict[str, list[str]], name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(query.get(name, [default])[0])
    except ValueError:
        return default
    return min(max(value, low), high) if value == value else default  # NaN이면 기본값


class Handler(BaseHTTPRequestHandler):
    server_version = "ReflexGuardPortal/1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — 요청마다 찍지 않는다
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self._send(status, json.dumps(data, separators=(",", ":")).encode(), "application/json")

    def _plain_on_https(self) -> bool:
        """HTTPS 포트로 http://가 왔다 → 같은 주소의 https://로 넘긴다 (주소 하나로 쓰게)."""
        if self.server.tls is None or isinstance(self.connection, ssl.SSLSocket):  # type: ignore[attr-defined]
            return False
        host = self.headers.get("Host", "") or f"localhost:{self.server.server_address[1]}"
        self.send_response(301)
        self.send_header("Location", f"https://{host}{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_GET(self) -> None:
        if self._plain_on_https():
            return
        path = urlparse(self.path).path
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._relay_to_webots()
        elif path in ("/", "/index.html"):  # 조작 화면으로 (주소 끝의 ?name=로 로봇을 고른다)
            self.send_response(302)
            self.send_header("Location", PAGE_PATH + "?name=reflexguard")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == PAGE_PATH:
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/portal":
            port = self.server.webots_port  # type: ignore[attr-defined]
            self._json(200, {"camera": True, "webots": {"port": port, "up": bool(port) and _listening(port)}})
        else:
            self._json(404, {"error": "not found"})

    def _relay_to_webots(self) -> None:
        """화면의 웹소켓을 Webots 웹 서버로 그대로 이어 준다 (요청 줄·헤더를 그대로 보내고, 그 뒤로는 양쪽 바이트를 옮긴다).
        Webots가 꺼져 있으면 503 — 화면은 2초마다 다시 연결을 시도한다."""
        port = self.server.webots_port  # type: ignore[attr-defined]
        try:
            upstream = socket.create_connection(("127.0.0.1", port), timeout=3.0) if port else None
        except OSError:
            upstream = None
        if upstream is None:
            self._json(503, {"error": "webots is not running"})
            return
        upstream.settimeout(None)
        headers = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items() if k.lower() != "host")
        upstream.sendall(f"GET {self.path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{headers}\r\n".encode("latin-1"))
        self.close_connection = True

        def down() -> None:  # Webots → 화면
            try:
                while chunk := upstream.recv(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, ValueError):
                pass
            finally:
                _shutdown(self.connection)

        thread = threading.Thread(target=down, name="webots-down", daemon=True)
        thread.start()
        try:  # 화면 → Webots
            while chunk := self.rfile.read1(65536):
                upstream.sendall(chunk)
        except (OSError, ValueError):
            pass
        finally:
            _shutdown(upstream)
            upstream.close()
            thread.join(timeout=2.0)

    def _refuse(self, status: int, message: str) -> None:
        """거절: 보내 온 본문을 DRAIN_BYTES까지 읽어 버리고 답한다. 그보다 크면 읽지 않고 답한 뒤 연결을 닫는다."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if 0 < length <= DRAIN_BYTES:
            self.rfile.read(length)
        else:
            self.close_connection = True
        self._json(status, {"error": message})

    def do_POST(self) -> None:
        if self.server.tls is not None and not isinstance(self.connection, ssl.SSLSocket):  # type: ignore[attr-defined]
            self._refuse(400, "use https")
            return
        url = urlparse(self.path)
        if url.path != "/api/frame":
            self._refuse(404, "not found")
            return
        query = parse_qs(url.query)
        try:
            width, height = int(self.headers.get("X-Width", "0")), int(self.headers.get("X-Height", "0"))
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._refuse(400, "bad size")
            return
        if not (16 <= width and 16 <= height and width * height <= MAX_PIXELS and length == width * height):
            self._refuse(413 if width * height > MAX_PIXELS else 400, "bad frame size")
            return
        gray = np.frombuffer(self.rfile.read(length), dtype=np.uint8).reshape(height, width)
        sid = query.get("session", [""])[0][:40] or self.client_address[0]
        key = (_number(query, "fov", 70.0, 30.0, 160.0), _number(query, "pitch", 0.0, -30.0, 45.0),
               _number(query, "height", 1.2, 0.3, 2.5))
        status, reply = self.server.demo.frame(  # type: ignore[attr-defined]
            sid, gray, _number(query, "dt", 1 / 15, 0.01, 1.0), _number(query, "turn", 0.0, -90.0, 90.0),
            _number(query, "speed", 0.0, 0.0, WALK_SPEED_MAX), key, self.client_address[0])
        self._json(status, reply)


def lan_addresses() -> list[str]:
    """이 PC의 내부망 주소들 (휴대폰에서 열 주소를 알려 주려고)."""
    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.168.0.1", 9))  # 실제로 보내지는 않는다 — 나가는 쪽 주소만 알아낸다
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def ensure_certificate(addresses: list[str]) -> tuple[Path, Path]:
    """자체 서명 인증서 (logs/demo_certs/). 없을 때만 openssl로 만든다."""
    cert, key = CERT_DIR / "demo.crt", CERT_DIR / "demo.key"
    if cert.exists() and key.exists():
        return cert, key
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    names = ",".join(["DNS:localhost", "IP:127.0.0.1"] + [f"IP:{a}" for a in addresses])
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365", "-keyout", str(key),
                    "-out", str(cert), "-subj", "/CN=reflexguard-demo", "-addext", f"subjectAltName={names}"],
                   check=True, capture_output=True)
    key.chmod(0o600)
    return cert, key


class PortalServer(ThreadingHTTPServer):
    """포트 하나로 HTTPS를 받는다. 같은 포트로 http://가 오면 https://로 넘긴다 (첫 바이트로 가린다 — TLS는 0x16).
    TLS 손잡기는 접속마다의 스레드에서 한다 (느린 기기 하나가 다른 접속을 막지 않게)."""

    daemon_threads = True
    tls: ssl.SSLContext | None = None

    def finish_request(self, request: Any, client_address: Any) -> None:
        if self.tls is not None:
            try:
                request.settimeout(10.0)
                first = request.recv(1, socket.MSG_PEEK)
                if first == b"\x16":
                    request = self.tls.wrap_socket(request, server_side=True)
                request.settimeout(None)
            except (OSError, ssl.SSLError):
                return
        super().finish_request(request, client_address)


def make_server(host: str, port: int, https: bool = False, dashboard: str = "", webots_port: int = 0,
                demo: "Demo | None" = None) -> ThreadingHTTPServer:
    server = PortalServer((host, port), Handler)
    server.demo = demo if demo is not None else Demo(dashboard)  # type: ignore[attr-defined]
    server.webots_port = webots_port  # type: ignore[attr-defined]  # 0이면 시뮬레이션 없이 카메라만
    if https:
        cert, key = ensure_certificate(lan_addresses())
        server.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.tls.load_cert_chain(cert, key)
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ReflexGuard 체험·조작 포털 (Webots 시뮬레이션 조작 + 이 기기 카메라)")
    parser.add_argument("--webots-port", type=int, default=0,
                        help="이어 줄 Webots 웹 서버 포트 (scripts/run.sh가 넘긴다). 0이면 시뮬레이션 없이 카메라만")
    parser.add_argument("--https", action="store_true", help="자체 인증서로 HTTPS (휴대폰 카메라에 필요 — 같은 포트로 온 http://는 https://로 넘긴다)")
    parser.add_argument("--public", action="store_true", help="같은 와이파이의 다른 기기에서도 접속 (0.0.0.0) — 인증 없음")
    parser.add_argument("--port", type=int, default=8443, help="포트 (기본 8443) — 이 PC와 휴대폰이 같은 주소를 쓴다")
    parser.add_argument("--dashboard", default=None,
                        help="관제 대시보드 host:port (기본: REFLEXGUARD_DASHBOARD) — 카메라를 쓰는 기기마다 상태·사건을 보낸다. 빈 값이면 붙지 않는다")
    args = parser.parse_args(argv)
    dashboard = dashboard_address() if args.dashboard is None else args.dashboard.strip()
    server = make_server("0.0.0.0" if args.public else "127.0.0.1", args.port, args.https, dashboard, args.webots_port)
    scheme = "https" if args.https else "http"
    addresses = lan_addresses() if args.public else []
    for address in addresses or ["localhost"]:
        print(f"[portal] 조작 화면 (이 PC·휴대폰 같은 주소): {scheme}://{address}:{args.port}", flush=True)
    if args.public and not args.https:
        print("[portal] 주의: 휴대폰 브라우저는 HTTPS가 아니면 카메라를 열지 않습니다 — --https를 붙이세요", flush=True)
    print(f"[portal] Webots: {f'127.0.0.1:{args.webots_port}로 이어 줍니다' if args.webots_port else '없음 (카메라만)'}", flush=True)
    if dashboard:
        print(f"[portal] 관제 대시보드: {dashboard} (카메라를 쓰는 기기마다 상태·사건을 보냅니다 — 영상은 보내지 않습니다)", flush=True)
    print("[portal] 영상은 저장하지 않습니다. 인증이 없으니 인터넷에 열지 마세요.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.demo.close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
