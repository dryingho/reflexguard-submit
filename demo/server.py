"""체험·조작 포털: 한 주소로 Webots 시뮬레이션 조작과 휴대폰(이 기기) 카메라 체험을 함께 쓴다.

    python -m demo.server --webots-port 1234 --https --public   # 이 PC·휴대폰 모두 https://<이 PC 주소>:8443 (주소 하나)
    python -m demo.server                                       # 이 PC에서만 http://localhost:8443 (카메라 체험만 — Webots 조작은 --webots-port를 줄 때)
    (scripts/run.sh가 관제 서버·연산 서버·Webots와 함께 켠다)

화면은 Webots 웹 조작 화면(simulation/webots/plugins/robot_windows/reflexguard/reflexguard.html) 하나다. 머리줄의
'휴대폰 카메라' 스위치가 꺼져 있으면 Webots 시뮬레이션(영상·조이스틱·제어)을, 켜면 이 기기의 카메라를 쓴다.
- 시뮬레이션: 화면이 연결하는 웹소켓("/")을 이 서버가 Webots(127.0.0.1:--webots-port)로 그대로 이어 준다. 그래서 휴대폰도
  HTTPS 주소 하나로 시뮬레이션을 조작한다 (Webots 웹 서버에 직접 붙어도 된다 — http://localhost:1234/robot_windows/…).
- 카메라: 화면이 줄인 밝기 영상(긴 쪽 240 px)을 POST /api/frame으로 보내면, 휠체어 PC 파이프라인(루밍 → 커넥톰 →
  공유 제어)을 그대로 돌려 흐름 상태·칸별 입력·작동 사건을 돌려준다. 화면은 시뮬레이션 때와 같은 패널에 그린다.
- 브라우저는 HTTPS(또는 같은 PC의 localhost)에서만 카메라를 열어 준다. --https면 자체 인증서를 logs/certs/에 만들어
  쓴다(openssl 필요, common/tls.py). 처음 접속할 때 '안전하지 않음' 경고를 한 번 넘겨야 한다 — 그 전에 브라우저의 인증서 보기에서
  공개키 SHA-256이 터미널에 나온 값과 같은지 확인한다 (인증서 전체 지문이 아니다). 포트는 하나 — 다른 기기가 같은 포트로 http://를 치면 https://로 넘긴다.
- 영상은 메모리에서 계산만 하고 저장하지 않는다. 기기마다 파이프라인을 하나씩 둔다 (최대 MAX_SESSIONS개, SESSION_IDLE_S초 쉬면 지운다).
- 같은 와이파이(내부망)에서만 쓴다. 조작 토큰으로 로그인해야 하지만, 인터넷에는 열지 않는다.
- 관제 대시보드 주소(--dashboard 또는 REFLEXGUARD_DASHBOARD)가 있으면 카메라를 쓰는 기기마다 'phone' 역할로 접속해 흐름 상태와
  회피 반사 작동 사건을 보낸다 (30초 쉬면 접속을 닫는다). 영상은 보내지 않는다.

    GET  /                       → /robot_windows/reflexguard/reflexguard.html?name=reflexguard (조작 화면)
    GET  /api/portal             {"camera": true|false, "webots": {"port", "up"}, "csrf": "…"} — 화면이 '휴대폰 카메라' 스위치를 보일지 정한다
                                 (CSRF 값은 POST의 X-CSRF-Token으로)
                                 (--no-camera로 켠 포털은 false — Webots 조작만, 카메라 영상은 받지 않는다)
    GET  / (Upgrade: websocket)  Webots로 이어 준다
    POST /api/frame?session=<id>&dt=<초>&turn=<도>&speed=<m/s>&fov=<도>[&pitch=<도>&height=<m>&nod=<도>&roll=<도>&auto=1]
    (fov: 휴대폰 카메라의 긴 쪽 화각 — 영상이 세로면 가로가 짧은 쪽이라 더 좁다. 서버가 영상 모양으로 가로 화각을 셈하고,
     정확한 값은 카메라 자기 보정이 제자리에서 돌 때 맞춘다. auto=1: 속도를 카메라로 짐작 — 잴 수 없으면 speed(걸음 수로 정한 값))
    (nod·roll: 직전 영상 이후 카메라가 숙여진(+아래로)·기울어진(+시계 방향) 각도 — 휴대폰 자이로의 나머지 두 축)
    [&stop=<위험도>] — 정지 기준 (화면의 민감도 슬라이더, 0.15~0.6 — 이 휴대폰에만)
    [&at=<ms>&total=<좌우>,<숙임>,<기울기>&first=1] — at: 이 장을 찍은 시각(ms, 화면의 시계), total: 카메라를 켠 뒤 돈 각도 합(도).
    주면 dt·turn·nod·roll 대신 앞서 처리한 장과의 차이로 셈한다 (화면은 답을 기다리지 않고 두 장까지 겹쳐 보낸다 — 순서가 바뀌어
    온 장은 409로 버린다). first=1: 카메라를 다시 켠 첫 장.
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
import math
import re
import socket
import types
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

from common.config import CONFIG, Config, handheld_profile  # noqa: E402
from common.control import ControlCommand  # noqa: E402
from common.dashboard_link import DashboardLink, dashboard_address  # noqa: E402
from wheelchair.pipeline import WheelchairPipeline  # noqa: E402
from wheelchair.looming.calibration import change_fields  # noqa: E402
from simulation.webots_bridge import CELLS_PREFIX, cells_message  # noqa: E402
from wheelchair.reflex_log import ReflexMonitor, flow_snapshot, thresholds  # noqa: E402
from common.auth import CONTROL, WeakToken, load_token  # noqa: E402
from common.limits import ConnectionLimit, FailureLock, RateLimiter  # noqa: E402
from common.tls import TLSServerMixin, certificate, is_loopback, file_fingerprint, lan_addresses, server_context  # noqa: E402
from common.web_auth import WebAuthMixin  # noqa: E402
from common.web_guard import page_csp, same_origin  # noqa: E402

PAGE = ROOT / "simulation" / "webots" / "plugins" / "robot_windows" / "reflexguard" / "reflexguard.html"  # 조작 화면
PAGE_PATH = "/robot_windows/reflexguard/reflexguard.html"  # 화면이 이 경로 앞부분으로 웹소켓 주소를 만든다 (Webots와 같은 규칙)
CERT_DIR = ROOT / "logs" / "certs"   # 관제·연산 서버와 같은 인증서 (common/tls.py)
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # 카메라 세션 id 모양 (조작 화면은 128비트 난수 32자)
MAX_SESSIONS = 6          # 동시에 체험하는 휴대폰 수
SESSION_IDLE_S = 30.0     # 이만큼 영상이 없으면 그 휴대폰의 파이프라인을 지운다
MAX_PIXELS = 320 * 320    # 한 영상의 최대 화소 수 (줄인 밝기 영상)
DRAIN_BYTES = 1024 * 1024  # 거절하는 요청도 이만큼까지는 본문을 읽고 답한다 (안 읽고 닫으면 보내던 쪽이 '연결 끊김'만 받는다)
MIN_FRAME_S = 1.0 / 30.0  # 한 휴대폰이 평균 이보다 자주 보내면 거절한다 (초당 30장)
FRAME_BURST = 2.0         # 그래도 이만큼은 붙어 와도 받는다 — 화면이 답을 기다리지 않고 두 장까지 겹쳐 보낸다 (도착이 몰릴 수 있다)
WALK_SPEED_MAX = 3.0      # 걷는 속도로 받는 최대값(m/s)
WALKING_MPS = 0.2         # 화면이 보낸 걷는 속도나 카메라로 짐작한 속도가 이보다 빠르면 걷는 중 — 정지 소리·진동은 걸을 때만
                          # (휴대폰은 서 있어도 '앞으로 가려 함'으로 판단해서, 가만히 든 휴대폰 앞으로 사람이 지나가도 울렸다)
STOP_RANGE = (0.15, 0.6)  # 화면의 민감도 슬라이더로 바꿀 수 있는 정지 기준(위험도) 범위 — 기본은 설정값(0.3)
PITCH_STEP_DEG = 2.0      # 가속도계 숙인 각이 이만큼 바뀌어야 루밍 감지기에 다시 건다 (손떨림마다 다시 만들지 않게)
NO_POSE = types.SimpleNamespace(x=0.0, y=0.0, angle_deg=0.0)  # 휴대폰은 맵 위치가 없다 (흐름 상태에서 위치는 뺀다)
WS_NOTE_REPEAT_S = 30.0   # 같은 웹소켓 기록 줄은 이만큼 지나야 다시 쓴다 (화면이 2초마다 다시 붙어도 기록이 넘치지 않게)
_ws_notes: dict[str, float] = {}
_ws_notes_lock = threading.Lock()


def ws_note(text: str) -> None:
    """웹소켓 중계에서 일어난 일을 포털 기록(logs/portal.log)에 한 줄로 — 프록시 뒤에서 Webots에 못 붙을 때 원인을 보려고.
    주소·토큰은 쓰지 않는다 (Host·Origin은 이 서버의 이름이라 그대로)."""
    now = time.monotonic()
    with _ws_notes_lock:
        if now - _ws_notes.get(text, -WS_NOTE_REPEAT_S) < WS_NOTE_REPEAT_S:
            return
        _ws_notes[text] = now
    print(f"[portal] 웹소켓: {text}", flush=True)


def horizontal_fov(long_side_fov: float, width: int, height: int) -> float:
    """긴 쪽 화각 → 이 영상(가로 width × 세로 height)의 가로 화각 (도). 가로로 길면 그대로, 세로로 길면 좁아진다."""
    ratio = width / max(width, height)
    return math.degrees(2.0 * math.atan(math.tan(math.radians(long_side_fov) / 2.0) * ratio))


def phone_config(fov: float, pitch: float, height: float, base: Config = CONFIG) -> Config:
    """휴대폰 카메라용 설정: 화각·숙인 각·높이, 몸 가리개 없음 (휴대폰에는 휠체어 몸이 보이지 않는다), 걸음 흔들림 보정 켬."""
    return handheld_profile(dataclasses.replace(base, fov_deg=fov, camera_pitch_deg=pitch, camera_height=height,
                                                body_mask_file="none"))


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
        self.allowance = FRAME_BURST  # 지금 받을 수 있는 장 수 (MIN_FRAME_S마다 하나씩, FRAME_BURST까지 찬다)
        self.last_at: float | None = None  # 마지막으로 처리한 장을 찍은 시각(ms, 화면의 시계) — at을 보낼 때
        self.last_total = (0.0, 0.0, 0.0)  # 그때까지 돈 각도 합 (좌우·숙임·기울기, 도)
        self.stop_on: float | None = None  # 화면 슬라이더로 정한 정지 기준 (없으면 설정값)

    def configure(self, key: tuple[float, float, float]) -> None:
        """화각 등 카메라 값이 바뀌면 파이프라인만 새로 (대시보드 접속·작동 번호는 그대로)."""
        self.key = key
        self.pipe = WheelchairPipeline(phone_config(*key))
        if self.stop_on is not None:
            self.pipe.set_stop_threshold(self.stop_on)
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
                        "calibration": pipe.calibrator.state(),
                        "phone": {"fov": self.key[0], "pitch": self.key[1], "height": self.key[2], "speed": speed,
                                  "fps": round(self.frames / max(t, 1e-9), 1), "ms": ms, "frames": self.frames,
                                  "shake": [round(float(shift[0]), 1), round(float(shift[1]), 1)]},
                        "cells": {"rows": int(inputs.shape[0]), "cols": int(inputs.shape[1]),
                                  "input": [round(float(v), 1) for v in inputs.ravel()]} if inputs is not None else None,
                        "loom": pipe.looming_view()})  # 루밍 동작 보기 (대시보드는 기록 파일에는 쓰지 않는다)
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
                             hello={"client": mask_ip(client), "fov": key[0], "pitch": key[1], "height": key[2]})

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
              key: tuple[float, float, float], client: str = "", nod: float = 0.0, roll: float = 0.0,
              auto_speed: bool = False, at: float | None = None, total: tuple[float, float, float] | None = None,
              first: bool = False, stop: float | None = None) -> tuple[int, dict[str, Any]]:
        """영상 한 장. at(찍은 시각, ms)·total(그때까지 돈 각도 합)을 주면 장 사이 시간·회전을 앞서 처리한 장과의 차이로 셈한다
        (dt·turn·nod·roll 대신) — 화면이 답을 기다리지 않고 겹쳐 보내도, 한 장이 늦게 오거나 빠져도 어긋나지 않는다. 앞서 처리한
        장보다 먼저 찍은 장은 버린다(409). first: 카메라를 다시 켠 첫 장 — 차이를 셈하지 않는다.
        stop: 화면 민감도 슬라이더의 정지 기준(위험도, STOP_RANGE로 자른다) — 이 휴대폰의 파이프라인에만 건다."""
        session = self.session(sid, key, client)
        if session is None:
            return 503, {"error": "too many phones"}
        with session.lock:
            now = time.monotonic()
            if at is not None and not first and session.last_at is not None and at <= session.last_at:
                return 409, {"error": "stale frame"}
            session.allowance = min(FRAME_BURST, session.allowance + (now - session.last) / max(MIN_FRAME_S, 1e-9))
            if session.allowance < 1.0:
                return 429, {"error": "too fast"}
            session.allowance -= 1.0
            session.last = now
            if at is not None:
                total = total or (0.0, 0.0, 0.0)
                if not first and session.last_at is not None:
                    dt = min(max((at - session.last_at) / 1000.0, 0.01), 1.0)
                    turn, nod, roll = (min(max(now_ - then, -limit), limit) for now_, then, limit
                                       in zip(total, session.last_total, (90.0, 45.0, 45.0)))
                else:
                    turn = nod = roll = 0.0
                session.last_at, session.last_total = at, total
            session.frames += 1
            if (session.key[0], session.key[2]) != (key[0], key[2]):
                session.configure(key)  # 화각·높이를 바꿨다 → 파이프라인 새로 (배운 보정도 처음부터)
            elif abs(session.key[1] - key[1]) >= PITCH_STEP_DEG:
                session.key = key  # 숙인 각(가속도계)만 바뀌었다 → 파이프라인은 그대로, 루밍 감지기에만 건다
                session.pipe.set_camera(camera_pitch_deg=key[1])
            if stop is not None:
                stop = min(max(stop, STOP_RANGE[0]), STOP_RANGE[1])
                if session.stop_on is None or abs(stop - session.stop_on) > 1e-6:
                    session.stop_on = stop
                    session.pipe.set_stop_threshold(stop)
            started = time.perf_counter()
            pipe = session.pipe
            pipe.camera_speed = auto_speed  # '내 움직임: 자동' — 카메라로 짐작, 잴 수 없으면 speed(걸음 수로 정한 값)
            user = ControlCommand(forward=min(1.0, speed / max(pipe.cfg.move_speed, 1e-9)) if speed > 0 else 0.0)
            pipe.moved(turn, speed, dt, user, user, nod_deg=nod, roll_deg=roll)  # 휴대폰은 명령대로 움직이지 않는다 (사람이 들고 다닌다)
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
            changes = pipe.take_camera_changes()
            if changes:  # 카메라 자기 보정이 값을 바꿨다 → 화면·관제 사건 기록에
                note = session.monitor.note("camera", now - session.started, **change_fields(changes))
                events.append(note)
                if session.link is not None:
                    session.link.send({"type": "event", "event": note})
            shift = pipe.detector.last_shift
            reply["state"] = {**snapshot, "th": thresholds(pipe.cfg), "camera": "phone", "calibration": pipe.calibrator.state(),
                              "phone": {"fov": key[0], "speed": speed, "ms": reply["ms"],
                                        "walking": max(speed, pipe.speed_used) > WALKING_MPS,
                                        "shake": [round(float(shift[0]), 1), round(float(shift[1]), 1)]}}
            reply["cells"] = json.loads(cells_message(inputs, pipe.detector.cell_front, filtered)[len(CELLS_PREFIX):])
            reply["events"] = events
            if pipe.reflex.stage == "stop" and reply["signal"] == "NONE":
                # 휠체어는 정지한 뒤 사용자가 전진을 놓을 때까지 정지를 붙잡지만, 휴대폰을 든 사람은 놓지 않는다 →
                # 커넥톰이 더는 위험하다고 하지 않으면 풀어 준다 (사용자가 W를 뗐다 다시 누르는 것과 같다)
                pipe.reset()
            return 200, reply


def mask_ip(address: str) -> str:
    """관제·기록에 보낼 휴대폰 주소는 끝자리를 가린다 (192.168.13.57 → 192.168.13.x). 기기 구분은 '휴대폰 N' 이름으로 한다.
    IP도 개인정보라 필요한 만큼만 남긴다 (KISA 설계 SR2-7)."""
    if address.count(".") == 3:
        return address.rsplit(".", 1)[0] + ".x"
    if ":" in address:
        return ":".join(address.split(":")[:3]) + ":x"
    return "x" if address else ""


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


def _triple(text: str) -> tuple[float, float, float] | None:
    """'a,b,c' → 세 수 (글자·NaN이거나 셋이 아니면 None, 너무 크면 자른다)."""
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        return None
    if len(values) != 3 or any(v != v for v in values):
        return None
    return (min(max(values[0], -1e6), 1e6), min(max(values[1], -1e6), 1e6), min(max(values[2], -1e6), 1e6))


class Handler(WebAuthMixin, BaseHTTPRequestHandler):
    server_version = "ReflexGuardPortal/1"
    auth_title = "ReflexGuard 조작"
    timeout = 30  # 한 번 받기를 이만큼 기다린다 (요청 전체 시한은 web_auth.REQUEST_DEADLINE_S 15초 — 웹소켓 중계는 _relay_to_webots에서 푼다)
    permissions = "camera=(self), accelerometer=(self), gyroscope=(self), microphone=(), geolocation=()"

    def auth_token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

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

    def do_GET(self) -> None:
        if self.redirect_plain() or not self.guard():  # guard: 요청 속도·인증 실패 잠금 (KISA 구현 2-16)
            return
        path = urlparse(self.path).path
        if self.login_from_query():  # ?token= → 로그인 쿠키로 바꾸고 주소에서 지운다
            return
        role = self.role()
        if role != CONTROL:  # 이 서버는 모두 조작(Webots·카메라)이라 조작 토큰만 (KISA 구현 2-1·2-2)
            if self.headers.get("Upgrade") or path.startswith("/api/"):
                if self.headers.get("Upgrade"):
                    ws_note("로그인 안 됨 (401) — 브라우저가 로그인 쿠키를 보내지 않았습니다 (다른 주소로 로그인했거나 프록시가 Cookie를 지움)"
                            if role is None else "보기 전용 토큰 (401) — 조작 토큰으로 로그인하세요")
                self._refuse(401, "login required")
            elif role is not None:
                self.send_login(error="보기 전용 토큰으로는 조작할 수 없습니다", status=403)
            else:
                self.send_login(PAGE_PATH + "?name=reflexguard" if path in ("/", "/index.html") else None)
            return
        if self.headers.get("Upgrade", "").lower() == "websocket":
            if not same_origin(self.headers):  # 다른 사이트가 연 웹소켓으로 Webots를 조작하지 못하게 (KISA 구현 1-11)
                ws_note(f"출처가 다름 (403) — Origin {self.headers.get('Origin')!r}, Host {self.headers.get('Host')!r}"
                        " (리버스 프록시가 Host를 바꾸면 이렇게 됩니다)")
                self._refuse(403, "cross-site websocket")
                return
            self._relay_to_webots()
        elif path in ("/", "/index.html"):  # 조작 화면으로 (주소 끝의 ?name=로 로봇을 고른다)
            self.send_response(302)
            self.send_header("Location", PAGE_PATH + "?name=reflexguard")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == PAGE_PATH:
            body = PAGE.read_bytes()
            self.csp = page_csp(body)  # 이 화면의 인라인 스크립트만 허용 (KISA 설계 SR1-5)
            self._send(200, body, "text/html; charset=utf-8")
        elif path == "/api/portal":
            port = self.server.webots_port  # type: ignore[attr-defined]
            self._json(200, {"camera": self.server.camera, "webots": {"port": port, "up": bool(port) and _listening(port)},  # type: ignore[attr-defined]
                             "csrf": self.csrf_value()})
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
            ws_note(f"Webots에 닿지 않음 (503) — 127.0.0.1:{port}에서 Webots가 받고 있지 않습니다" if port
                    else "Webots 없이 켠 포털 (503) — run.sh로 켜면 Webots에 이어 줍니다")
            self._json(503, {"error": "webots is not running"})
            return
        ws_note(f"Webots(127.0.0.1:{port})로 이어 줌 — Host {self.headers.get('Host')!r}")
        upstream.settimeout(None)
        self.release_deadline()  # 웹소켓 중계는 오래 간다
        self.connection.settimeout(None)  # 중계: 키를 누르지 않는 동안 조용해도 이어 둔다 (끊김은 양쪽 끝이 알린다)
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
        if self.server.tls is not None and not self._secure() and not is_loopback(self.client_address):  # type: ignore[attr-defined]
            self._refuse(400, "use https")
            return
        if not self.guard():
            return
        url = urlparse(self.path)
        if url.path == "/login":
            self.handle_login()
            return
        if url.path == "/logout":
            self.handle_logout()
            return
        if not same_origin(self.headers):  # 다른 사이트가 보낸 영상 (KISA 구현 1-11)
            self._refuse(403, "cross-site request")
            return
        if self.role() != CONTROL:
            self._refuse(401, "login required")
            return
        if not self.csrf_ok():  # 화면이 /api/portal로 받은 CSRF 값 (KISA 구현 1-11)
            self._refuse(403, "csrf")
            return
        if url.path != "/api/frame" or not self.server.camera:  # type: ignore[attr-defined]  # --no-camera: 카메라 영상은 받지 않는다
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
        session = query.get("session", [""])[0]
        if not SESSION_ID.fullmatch(session):  # 화면이 만든 난수 id가 꼭 있어야 한다 (IP로 묶으면 같은 공유기 뒤 기기끼리 섞인다)
            self._refuse(400, "bad session")
            return
        at = total = None
        if "at" in query:  # 찍은 시각과 돈 각도 합 — 화면이 겹쳐 보낼 때
            at, total = _number(query, "at", 0.0, 0.0, 1e12), _triple(query.get("total", ["0,0,0"])[0])
            if total is None:
                self._refuse(400, "bad total")
                return
        gray = np.frombuffer(self.rfile.read(length), dtype=np.uint8).reshape(height, width)
        sid = f"{self.client_address[0]}/{session}"  # 주소별로 나눈다 — 다른 기기가 id를 알아도 남의 세션에 끼어들지 못하게 (KISA 구현 1-15·6-1)
        # 화각은 긴 쪽으로 받아 이 영상의 가로 화각으로 (세로로 들면 좁다) — 가로·세로를 바꾸면 파이프라인을 새로 만든다
        fov = round(horizontal_fov(_number(query, "fov", 70.0, 30.0, 160.0), width, height), 1)
        key = (fov, _number(query, "pitch", 0.0, -30.0, 45.0), _number(query, "height", 1.2, 0.3, 2.5))
        status, reply = self.server.demo.frame(  # type: ignore[attr-defined]
            sid, gray, _number(query, "dt", 1 / 15, 0.01, 1.0), _number(query, "turn", 0.0, -90.0, 90.0),
            _number(query, "speed", 0.0, 0.0, WALK_SPEED_MAX), key, self.client_address[0],
            _number(query, "nod", 0.0, -45.0, 45.0), _number(query, "roll", 0.0, -45.0, 45.0),
            query.get("auto", ["0"])[0] == "1", at=at, total=total, first=query.get("first", ["0"])[0] == "1",
            stop=_number(query, "stop", 0.3, *STOP_RANGE) if "stop" in query else None)
        self._json(status, reply)


def ensure_certificate(addresses: list[str]) -> tuple[Path, Path]:
    """자체 서명 인증서 (logs/certs/ — 관제·연산 서버와 같은 것, common/tls.py). 없거나 낡으면 openssl로 만든다."""
    return certificate(addresses, cert_dir=CERT_DIR)


class PortalServer(TLSServerMixin, ConnectionLimit, ThreadingHTTPServer):
    """포트 하나로 HTTPS를 받는다. 같은 포트로 http://가 오면 https://로 넘긴다 (첫 바이트로 가린다 — TLS는 0x16).
    TLS 손잡기는 접속마다의 스레드에서 한다 (느린 기기 하나가 다른 접속을 막지 않게)."""

    daemon_threads = True
    plain_allowed = True  # 다른 기기의 http://는 처리기가 https://로 넘긴다
    max_connections = 64  # 웹소켓 중계·카메라 요청이 접속마다 스레드 하나 (KISA 구현 5-2)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rate = RateLimiter(rate=60.0, burst=120.0)  # IP마다 — 카메라가 초당 최대 30장 + 화면 요청
        self.auth_failures = FailureLock()


def make_server(host: str, port: int, https: bool = False, dashboard: str = "", webots_port: int = 0,
                demo: "Demo | None" = None, token: str = "", camera: bool = True) -> ThreadingHTTPServer:
    """token이 비면 인증 없음(시험용). main()은 언제나 토큰을 준다. camera=False면 Webots 조작만 (카메라 체험은 다른 포털에서)."""
    server = PortalServer((host, port), Handler)
    server.token = token  # type: ignore[attr-defined]
    server.demo = demo if demo is not None else Demo(dashboard)  # type: ignore[attr-defined]
    server.webots_port = webots_port  # type: ignore[attr-defined]  # 0이면 시뮬레이션 없이 카메라만
    server.camera = camera  # type: ignore[attr-defined]
    if https:
        server.tls = server_context(*ensure_certificate(lan_addresses()))  # TLS 1.2 이상 (common/tls.py)
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ReflexGuard 체험·조작 포털 (Webots 시뮬레이션 조작 + 이 기기 카메라)")
    parser.add_argument("--webots-port", type=int, default=0,
                        help="이어 줄 Webots 웹 서버 포트 (scripts/run.sh가 넘긴다). 0이면 시뮬레이션 없이 카메라만")
    parser.add_argument("--no-camera", action="store_true",
                        help="카메라 체험을 받지 않는다 — Webots 조작만 (휴대폰 체험은 서버 PC의 포털에서: run_server.sh — run_sim.sh가 이렇게 켠다)")
    parser.add_argument("--https", action="store_true", help="자체 인증서로 HTTPS (휴대폰 카메라에 필요 — 같은 포트로 온 http://는 https://로 넘긴다)")
    parser.add_argument("--public", action="store_true", help="같은 와이파이의 다른 기기에서도 접속 (0.0.0.0) — 토큰으로 로그인")
    parser.add_argument("--port", type=int, default=8443, help="포트 (기본 8443) — 이 PC와 휴대폰이 같은 주소를 쓴다")
    parser.add_argument("--dashboard", default=None,
                        help="관제 대시보드 host:port (기본: REFLEXGUARD_DASHBOARD) — 카메라를 쓰는 기기마다 상태·사건을 보낸다. 빈 값이면 붙지 않는다")
    args = parser.parse_args(argv)
    if args.no_camera and not args.webots_port:
        raise SystemExit("[portal] 오류: --no-camera는 Webots를 이어 줄 때만 (--webots-port) — 둘 다 없으면 할 일이 없습니다")
    dashboard = dashboard_address() if args.dashboard is None else args.dashboard.strip()
    try:
        token = load_token(create=True)  # 환경 변수 또는 logs/token (없으면 만든다)
    except WeakToken as error:
        raise SystemExit(f"[portal] 오류: {error}") from error
    server = make_server("0.0.0.0" if args.public else "127.0.0.1", args.port, args.https, dashboard, args.webots_port,  # nosec B104  # --public을 줄 때만 (기본 127.0.0.1)
                         token=token, camera=not args.no_camera)
    scheme = "https" if args.https else "http"
    if args.https:  # 경고를 넘기기 전에 브라우저의 인증서 보기(공개키 SHA-256)에서 이 값과 같은지 확인하게 (KISA 구현 2-11)
        print(f"[portal] 공개키 지문(SHA-256): {file_fingerprint(ensure_certificate(lan_addresses())[0])}", flush=True)
    addresses = lan_addresses() if args.public else []
    for address in addresses or ["localhost"]:
        print(f"[portal] 조작 화면 (이 PC·휴대폰 같은 주소): {scheme}://{address}:{args.port}", flush=True)
    if args.public and not args.https:
        print("[portal] 주의: 휴대폰 브라우저는 HTTPS가 아니면 카메라를 열지 않습니다 — --https를 붙이세요", flush=True)
    print(f"[portal] Webots: {f'127.0.0.1:{args.webots_port}로 이어 줍니다' if args.webots_port else '없음 (카메라만)'}", flush=True)
    if args.no_camera:
        print("[portal] 카메라 체험: 받지 않음 (--no-camera — Webots 조작만)", flush=True)
    if dashboard:
        print(f"[portal] 관제 대시보드: {dashboard} (카메라를 쓰는 기기마다 상태·사건을 보냅니다 — 영상은 보내지 않습니다)", flush=True)
    print("[portal] 영상은 저장하지 않습니다. 처음 열면 토큰을 묻습니다 (python -m common.auth로 확인). 인터넷에는 열지 마세요.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.demo.close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
