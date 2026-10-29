"""어택랩 클라이언트 티어 — 사용자(휠체어) 시뮬레이션 + 커넥톰 통신.

Webots 없이, 녹화해 둔 주행 영상(attacklab/client/data/webots_*.npz)을 휠체어 PC 파이프라인에 흘려 넣어 실제 로봇처럼 행동한다:
  ① 루밍 연산 → ② 커넥톰(연산 서버에 TCP로 물어봄 = '커넥톰 통신') → ③ 공유 제어
그리고 관제 대시보드에 'webots' 역할로 접속해 상태·사건을 보낸다 (common/dashboard_link.py).
보내는 상태와 받은 명령의 처리는 Webots 컨트롤러(simulation/webots/controllers/reflexguard/reflexguard.py)와 같은 꼴이다:
  상태 — 흐름·스위치·속도 제한·카메라 보정·루밍 동작 보기(loom, 그 버전 코드에 있을 때) 등
  명령 — 커넥톰·안전 스위치 켜기/끄기, 비상 정지, 속도 제한, 처음부터 다시 (위험 인식·맵 바꾸기는 녹화라 받지 않는다)
그래서 대시보드에서 온 명령(정상이든 위조든)이 로봇을 실제로 멈추고, 그 결과가 관제 화면·감사 로그에 보인다.

이 티어는 어택랩에서 '정상 트래픽'을 만들어 준다: 서버(대시보드) 티어로 상태가 계속 흐르고, 연산 서버와도 통신이 오간다.
학습자는 이 정상 트래픽 옆에서 자기 요청을 보내 보고, 감사 로그에서 둘을 비교한다.

환경 변수
  REFLEXGUARD_DASHBOARD        관제 대시보드 접속 주소 (예: proxy:8766 — 어택랩에서는 감사 프록시를 거친다)
  REFLEXGUARD_BRAIN_SERVER     커넥톰 연산 서버 주소 (예: 127.0.0.1:8765)
  REFLEXGUARD_DASHBOARD_TOKEN  대시보드 토큰 (패치를 켠 뒤 '정상 클라이언트'가 통과하려면 맞춰 준다)
  LAB_SCENE                    장면 이름 (기본 hospital_busy) — 미니맵·개입 위치가 그 맵으로 보인다
  LAB_ROBOT_NAME               로봇 이름 (기본 wheelchair-01)
  LAB_CLIP                     쓸 녹화 (wall|person|sidewalk, 기본 person)
  LAB_CODE_ROOT                휠체어 PC 코드를 불러올 폴더 (기본 저장소 루트 = 조치 후 본편, 고정본이면 attacklab/vulnerable)
"""

from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]  # 저장소 루트 (녹화가 있는 곳)
sys.path.insert(0, os.environ.get("LAB_CODE_ROOT") or str(ROOT))

from common.config import CONFIG  # noqa: E402
from common.control import ControlCommand  # noqa: E402

SPEED_LIMITS = ("100", "80", "60", "40", "20")  # 대시보드가 고르는 속도 제한(%) — common/control.py (고정본에는 없다)
from common.dashboard_link import DashboardLink, dashboard_address  # noqa: E402
from connectome.brain import MaleCNSBrain  # noqa: E402
from simulation.scenes import SCENES  # noqa: E402
from wheelchair.brain_client import RemoteBrain, brain_server_address  # noqa: E402
from wheelchair.pipeline import WheelchairPipeline  # noqa: E402
from wheelchair.reflex_log import ReflexMonitor, flow_snapshot, thresholds  # noqa: E402


def load_clip(name: str) -> dict:
    with np.load(ROOT / "attacklab" / "client" / "data" / f"webots_{name}.npz") as clip:
        return {key: clip[key] for key in clip.files}


class Switches:
    """대시보드 명령으로 바뀌는 스위치 — Webots 컨트롤러와 같은 명령·같은 사건(note)을 쓴다."""

    def __init__(self, pipe: WheelchairPipeline, brain: object, monitor: ReflexMonitor) -> None:
        self.pipe, self.brain, self.monitor = pipe, brain, monitor
        self.safety = False
        self.estop = False
        self.speed_limit = 100
        self.restart = False  # '처음부터 다시' — 녹화를 처음부터

    def apply(self, order: dict, t: float) -> list[dict]:
        """명령 하나 → 남길 사건들 (바뀐 것이 없으면 빈 목록)."""
        name, value = order.get("command"), order.get("value")
        if name == "connectome" and value in ("on", "off") and (value == "on") != self.pipe.connectome_on:
            self.pipe.connectome_on = value == "on"
            if self.pipe.connectome_on and hasattr(self.brain, "reset"):
                self.brain.reset()  # 다시 켜면 회로 상태(막전위·디코더)를 비우고 새로 시작한다
            return [self.monitor.note("connectome", t, on=self.pipe.connectome_on)]
        if name == "safety" and value in ("on", "off") and (value == "on") != self.safety:
            self.safety = value == "on"
            return [self.monitor.note("safety", t, on=self.safety)]
        if name == "estop" and value in ("on", "off") and (value == "on") != self.estop:
            self.estop = value == "on"
            return [self.monitor.note("estop", t, on=self.estop)]
        if name == "speed_limit" and value in SPEED_LIMITS and int(value) != self.speed_limit:
            self.speed_limit = int(value)
            self.pipe.speed_limit = self.speed_limit / 100  # 고정본 코드는 이 값을 쓰지 않는다 (속도 제한이 없던 때)
            return [self.monitor.note("speed_limit", t, percent=self.speed_limit)]
        if name == "restart":
            self.restart = True
        return []


def main() -> None:
    scene_name = os.environ.get("LAB_SCENE", "hospital_busy")
    scene = SCENES.get(scene_name, SCENES["hospital_busy"])
    robot_name = os.environ.get("LAB_ROBOT_NAME", "wheelchair-01")
    clip = load_clip(os.environ.get("LAB_CLIP", "person"))
    dt = float(clip["dt"])

    dash = dashboard_address(CONFIG)
    maps = [[name, other.title or name] for name, other in SCENES.items()]
    link = DashboardLink(dash, "webots", f"{robot_name}@attacklab", hello={
        "map": scene.name, "maps": maps, "rows": list(scene.rows),
    }) if dash else None

    brain_addr = brain_server_address(CONFIG)
    brain = RemoteBrain(brain_addr) if brain_addr else MaleCNSBrain(CONFIG)
    pipe = WheelchairPipeline(CONFIG, brain)
    monitor = ReflexMonitor(CONFIG, log_dir=None)
    switches = Switches(pipe, brain, monitor)
    forward = ControlCommand(forward=1.0)

    print(f"[robot] 대시보드 {dash or '없음'} · 연산 서버 {brain_addr or '기기 안'} · 맵 {scene.name}", flush=True)
    sim_t = 0.0
    next_state = 0.0
    frames = clip["gray"]
    pose = clip["pose"]
    i = 0
    while True:  # 녹화를 되풀이해 끊임없는 '정상 트래픽'을 만든다
        x, y, angle = pose[i]
        agent = types.SimpleNamespace(x=float(x), y=float(y), angle_deg=float(angle))
        if link is not None:
            for order in link.take_commands():  # 대시보드에서 온 명령 (어택랩에서 관찰·테스트 대상)
                print(f"[robot] 대시보드 명령 수신: {order}", flush=True)
                for note in switches.apply(order, sim_t):
                    print(f"[robot] {note}", flush=True)
                    link.send({"type": "event", "event": note})
        if switches.restart:  # 처음 위치로 (녹화를 처음부터, 공유 제어 상태도 새로)
            switches.restart = False
            pipe.reset()
            i = 0
            continue
        # 비상 정지 중: 사용자 입력을 무시하고 멈춘다 — 녹화를 넘기지 않고 같은 자리·같은 영상에 서 있는다
        user = ControlCommand() if switches.estop else forward
        turn, speed = (0.0, 0.0) if switches.estop else (float(clip["turn"][i]), float(clip["speed"][i]))
        pipe.moved(turn, speed, dt, user, user)
        pipe.see_gray(frames[i].astype(np.float32), clip["frame_shape"], dt)
        applied = pipe.decide(user, dt)
        decision = pipe.decision if pipe.connectome_on else None
        snap = flow_snapshot(sim_t, agent, pipe.looming, decision, pipe.reflex, user, applied,
                             pipe.override is not None, CONFIG)
        if link is not None:
            for event in monitor.update(snap):
                link.send({"type": "event", "event": event})
            now = time.monotonic()
            if now >= next_state:
                next_state = now + 1.0 / max(CONFIG.dashboard_state_hz, 1e-9)
                state = {"type": "state", "t": round(sim_t, 2), "map": scene.name,
                         "connectome": pipe.connectome_on, "safety": switches.safety, "estop": switches.estop,
                         "sensor": "camera", "speed_limit": switches.speed_limit, "crash": {"count": 0, "frozen": False},
                         "th": thresholds(CONFIG), "flow": snap,
                         "server": brain.status() if hasattr(brain, "status") else {"remote": False}}
                # 최근 버전에만 있는 칸 (고정본 코드에는 없다 — 그때는 보내지 않는다)
                if hasattr(pipe, "calibrator"):
                    state["calibration"] = pipe.calibrator.state()
                if hasattr(pipe, "looming_view"):
                    state["loom"] = pipe.looming_view()  # 루밍 동작 보기 (대시보드는 기록 파일에는 쓰지 않는다)
                link.send(state)
        sim_t += dt
        if not switches.estop:
            i = (i + 1) % len(frames)
        time.sleep(dt)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
