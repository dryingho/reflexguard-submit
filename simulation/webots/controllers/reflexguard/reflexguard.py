"""ReflexGuard Webots 컨트롤러.

Webots가 이 파일을 실행한다 (월드 파일의 Robot 노드에 controller "reflexguard"로 지정되어 있다).

한 스텝의 흐름 (시험 도구 tools/goal_trials.py의 run()과 같은 순서)
    웹 조작 화면의 키·조이스틱 → ControlCommand → ① 루밍 감지 → ② 커넥톰 뇌 → ③ 공유 제어 → 에이전트 이동
    → 벽·장애물에 부딪혔으면 멈췄다가 처음부터 다시 (simulation/collision.py)
    → 도착지('G')에 닿았으면 걸린 시간·커넥톰 개입 수를 알리고, 잠깐 멈췄다가 처음부터 다시 (simulation/goal.py)
    → Webots 로봇의 위치·방향 갱신 → '돌고 있음' 신호, 카메라 영상, 흐름 상태, 회피 반사·충돌 알림을 웹 조작 화면으로 전송

회피 반사가 개입한 구간은 logs/reflex_<날짜-시각>.jsonl에 기록하고 Webots 콘솔에도 한 줄씩 찍는다 (wheelchair/reflex_log.py).

이동과 벽 충돌은 simulation/agent.py가 계산하고(물리 엔진을 쓰지 않는다), Webots는 그 결과를 3D로 그린다.
맵은 Robot 노드의 controllerArgs로 고른다 (simulation/webots/worlds/road.wbt는 "road" — simulation/scenes.py). 사람·공은 simulation/actors.py가 움직이고,
컨트롤러가 월드의 ACTOR_<이름> 노드를 그 위치로 옮긴다.
"""

from __future__ import annotations

import base64
import math
import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # 프로젝트 폴더의 패키지(common, wheelchair, connectome, simulation)를 불러오기 위해

from simulation.actors import KINDS, Crowd
from simulation.gait import Gait, roll_angle
from common.control import SPEED_LIMITS, ControlCommand
from simulation.agent import Agent, forward_speed, turned_deg
from connectome.brain import MaleCNSBrain
from wheelchair.brain_client import RemoteBrain, brain_server_address
from common.dashboard_link import DashboardLink, dashboard_address
from simulation.collision import CollisionRestart, collision_fields, move_to_start
from simulation.range_scan import RangeScanner
from common.config import CONFIG, Config
from simulation.goal import Trip
from wheelchair.pipeline import WheelchairPipeline
from wheelchair.looming.calibration import change_fields
from wheelchair.reflex_log import DEFAULT_LOG_DIR, ReflexMonitor, describe_event, flow_snapshot, thresholds, to_json
from simulation.webots_bridge import (
    COLLISION_PREFIX,
    FRAME_PREFIX,
    READY_MESSAGE,
    REFLEX_PREFIX,
    STATE_PREFIX,
    WebInput,
    NewFrames,
    cells_message,
    frame_from_bgra,
    webots_pose,
)
from simulation.scenes import DEFAULT_SCENE, SCENES, get_scene

WORLDS_DIR = Path(__file__).resolve().parents[2] / "worlds"  # 웹 화면에서 맵을 고르면 이 폴더의 월드를 불러온다


def console_url(robot_name: str) -> str:
    """웹 조작 화면의 주소. Webots가 쓰는 포트는 실행 정보 폴더 이름(…/1234/)에서 알아낸다."""
    port = Path(os.environ.get("WEBOTS_INSTANCE_PATH", "")).name
    port = port if port.isdigit() else "1234"
    return f"http://localhost:{port}/robot_windows/reflexguard/reflexguard.html?name={robot_name}"


def actor_pose(actor: Any, world: Any) -> tuple[list[float], list[float]]:
    """장애물의 격자 좌표 → Webots 노드의 (translation, rotation). 로봇과 같은 변환(webots_pose): y를 뒤집고, 방향은 -각도."""
    return [actor.x, world.height - actor.y, 0.0], [0.0, 0.0, 1.0, -math.radians(actor.heading_deg)]


def def_nodes(node: Any) -> dict[str, Any]:
    """노드 아래(children을 따라)의 DEF 이름 → 노드. 사람 모델의 관절(ACTOR_<이름>_LA …)과 공의 ROLL을 찾는다."""
    found: dict[str, Any] = {}
    stack = [node]
    while stack:
        current = stack.pop()
        try:
            children = current.getField("children")
        except (KeyError, AttributeError):
            children = None
        if children is None:
            continue
        for index in range(children.getCount()):
            child = children.getMFNode(index)
            if child is None:
                continue
            name = child.getDef()
            if name:
                found[name] = child
            stack.append(child)
    return found


class ActorView:
    """Webots 월드의 장애물 노드 하나: 위치·방향, 그리고 사람이면 팔다리 관절, 공이면 굴러가는 회전."""

    def __init__(self, actor: Any, node: Any) -> None:
        self.actor = actor
        self.translation = node.getField("translation")
        self.rotation = node.getField("rotation")
        nodes = def_nodes(node)
        prefix = f"ACTOR_{actor.name}_"
        self.joints = {name[len(prefix):]: found.getField("rotation") for name, found in nodes.items()
                       if name.startswith(prefix) and name != f"{prefix}ROLL"}
        roll = nodes.get(f"{prefix}ROLL")
        self.roll = roll.getField("rotation") if roll is not None else None
        self.gait = Gait(KINDS[actor.kind][1] / 1.8)
        self.last = (actor.x, actor.y)
        self.rolled = 0.0

    def update(self, world: Any, dt: float) -> None:
        translation, rotation = actor_pose(self.actor, world)
        self.translation.setSFVec3f(translation)
        self.rotation.setSFRotation(rotation)
        moved = math.hypot(self.actor.x - self.last[0], self.actor.y - self.last[1])
        self.last = (self.actor.x, self.actor.y)
        if moved > 0.5:  # 처음부터 다시: 제자리로 옮겨진 것이지 걸은 것이 아니다
            moved = 0.0
        if self.roll is not None and moved > 0.0:
            self.rolled += moved
            self.roll.setSFRotation([0.0, 1.0, 0.0, roll_angle(self.rolled, self.actor.radius)])
        if self.joints and (moved > 0.0 or self.gait.motion > 0.01):
            for name, angle in self.gait.update(moved, dt).items():
                field = self.joints.get(name)
                if field is not None:
                    field.setSFRotation([0.0, 1.0, 0.0, angle])


PERF_REPORT_S = 10.0  # 속도 기록 간격(초, 실제 시간)


def speed_report(steps: int, think_s: float, elapsed_s: float, dt: float) -> str:
    """속도 기록 한 줄: 실제 시간 대비 배율, 한 스텝의 시간과 그 몫(Webots·카메라·전송 / 루밍·커넥톰·공유 제어)."""
    step_ms = elapsed_s / max(steps, 1) * 1000.0
    think_ms = think_s / max(steps, 1) * 1000.0
    ratio = steps * dt / elapsed_s if elapsed_s > 0 else 0.0
    text = (f"speed: x{ratio:.2f} of real time — one step {step_ms:.0f} ms "
            f"(Webots·camera·sending {max(0.0, step_ms - think_ms):.0f} ms, looming·connectome·control {think_ms:.0f} ms)")
    if ratio < 0.8:
        text += " — slow: try --camera-ms 64; without a GPU Webots draws the camera in software (llvmpipe), see docs/usage.md"
    return text


def run(robot: Any, cfg: Config = CONFIG, clock: Callable[[], float] = time.monotonic,
        log_dir: Path | None = DEFAULT_LOG_DIR, scene_name: str = DEFAULT_SCENE) -> None:
    """컨트롤러 본체.

    robot: Webots의 Supervisor 객체.
    clock: 현재 시각(초)을 돌려주는 함수 — 웹 화면과의 통신이 끊겼는지 판단하는 데 쓴다.
           (테스트에서는 가짜 시계를 넣는다)
    log_dir: 회피 반사 작동 기록을 남길 폴더 (None이면 남기지 않는다).
    scene_name: 맵 이름 (scenes.SCENES — 월드 파일의 controllerArgs).
    """
    time_step = int(robot.getBasicTimeStep())  # 시뮬레이션 한 스텝(ms)
    dt = time_step / 1000.0
    camera = robot.getDevice("camera")
    # 카메라는 camera_period_ms마다 찍는다 (스텝의 배수). 운전 제어는 스텝마다, 루밍·커넥톰은 새 영상이 올 때 갱신한다
    frame_steps = max(1, round(cfg.camera_period_ms / time_step))
    camera.enable(frame_steps * time_step)
    width, height = camera.getWidth(), camera.getHeight()
    node = robot.getSelf()
    translation_field = node.getField("translation")
    rotation_field = node.getField("rotation")

    scene = get_scene(scene_name)
    world = scene.world()
    crowd = Crowd(scene.actors)  # 움직이는 장애물 (장면의 actors) — 월드 파일의 ACTOR_<이름> 노드를 옮겨 그린다
    actor_views = []  # 사람은 걷는 동작(팔다리 관절), 공은 굴러가는 회전까지 (simulation/gait.py)
    for actor in crowd.actors:
        actor_node = robot.getFromDef(f"ACTOR_{actor.name}")
        if actor_node is not None:
            actor_views.append(ActorView(actor, actor_node))
    agent = Agent(
        *world.start,
        angle_deg=cfg.start_angle_deg,
        radius=cfg.agent_radius,
        move_speed=cfg.move_speed,
        turn_speed_deg=cfg.turn_speed_deg,
        brake_decel=cfg.brake_decel,  # 0(기본)이면 바로 선다
    )
    web_input = WebInput(cfg.web_key_timeout, cfg.web_viewer_timeout)
    # 휠체어 PC (wheelchair/pipeline.py): ① 루밍 연산 → ② 커넥톰 → ③ 공유 제어. 이 컨트롤러는 카메라·바퀴 대신
    # Webots 카메라와 시뮬레이션 속 휠체어(agent)를 이어 줄 뿐이다.
    # ② 커넥톰: 서버 주소가 있으면 커넥톰 연산 서버(brain_server/server.py)에서, 없으면 이 컨트롤러 안에서 계산한다
    brain_address = brain_server_address(cfg)
    brain = RemoteBrain(brain_address, cfg) if brain_address else MaleCNSBrain(cfg)
    brain_status = brain.status if brain_address else (lambda: {"remote": False})
    if brain_address:
        print(f"[reflexguard] connectome server: {brain_address}", flush=True)
    pipe = WheelchairPipeline(cfg, brain)
    # 커넥톰 입력 센서(위험 인식): camera = 이 Webots 카메라의 루밍 / range = 거리 센서(2D 라이다 같은 것 — 맵과 사람·공까지
    # 빛살로 잰 거리, simulation/range_scan.py)로 같은 꼴의 입력을 만든다. 판단·개입은 둘 다 같은 커넥톰·공유 제어.
    # 시작 값은 config.reflex_sensor(REFLEXGUARD_SENSOR), 실행 중에는 웹 조작 화면·관제 대시보드에서 바꾼다
    sensor = cfg.reflex_sensor
    scanner = RangeScanner(world, cfg)
    monitor = ReflexMonitor(cfg, log_dir, meta={"source": "webots", "scene": scene.name,
                                                "brain_server": brain_address or None})
    sim_time = 0.0                           # 시뮬레이션 시각(초) — 기록의 t
    # 웹 조작 화면의 스위치 두 개. 컨트롤러를 다시 켜면 커넥톰은 켜짐, 안전 스위치는 꺼짐으로 시작한다(안전한 쪽).
    # - 커넥톰 연결: 끄면 판단이 아예 없는 상태(brain=None) → 공유 제어가 안전 정지(전진·후진·회전 모두 막음)
    # - 안전 스위치: 켜면 판단이 없을 때 막지 않고 수동 주행 허용 (ReflexModule.manual_without_brain)
    connectome_on = True
    safety_on = False
    crash = CollisionRestart(cfg.collision_restart_delay_s)  # 벽·장애물 충돌 → 멈춤 → 처음부터 다시
    trip = Trip(world, cfg)  # 도착지까지의 주행: 도착 판정, 걸린 시간, 커넥톰 개입 수 (도착지가 없는 맵이면 아무것도 안 한다)
    new_frames = NewFrames(every_step=frame_steps == 1)  # 카메라 간격이 스텝보다 길면 새로 찍힌 영상만 본다

    # 웹으로 보낼 영상을 JPEG로 만드는 데 쓰는 임시 파일.
    # Webots의 saveImage()는 Windows에서 한글이 든 경로를 열지 못하므로,
    # 작업 폴더를 임시 폴더로 옮기고 파일 이름만 넘긴다.
    os.chdir(tempfile.gettempdir())
    jpeg_name = f"reflexguard_frame_{os.getpid()}.jpg"
    frame_interval = 1.0 / cfg.web_fps  # 영상 한 장을 보내는 간격(초)
    next_frame_time = 0.0               # 다음 영상을 보낼 예정 시각
    next_ready_time = 0.0               # 다음 '돌고 있음' 신호를 보낼 시각
    state_interval = 1.0 / cfg.web_state_hz if cfg.web_state_hz > 0 else math.inf  # 흐름 상태를 보내는 간격(초)
    next_state_time = 0.0               # 다음 흐름 상태를 보낼 시각
    state_thresholds = thresholds(cfg)  # 화면이 위험도 기준선 등을 그릴 수 있게 흐름 상태에 같이 싣는다
    map_choices = [[name, other.title or name] for name, other in SCENES.items()]  # 화면의 맵 고르기 목록
    log_announced = False               # 로그 파일 위치를 콘솔에 알렸는지
    # 관제 대시보드 (dashboard/server.py): 주소가 있으면 상태·사건을 보내고 명령(커넥톰·안전 스위치·비상 정지·
    # 처음부터 다시·맵)을 받는다. 대시보드가 없거나 끊겨도 로봇은 그대로 돈다.
    estop = False                       # 대시보드의 비상 정지: 켜져 있는 동안 사용자 입력을 무시하고 멈춘다
    speed_limit = 100                   # 관제의 원격 속도 제한(%) — 이동 크기의 상한 (WheelchairPipeline.speed_limit)
    sense_stale = ""                    # 센서 워치독이 판단을 버린 까닭 (WheelchairPipeline.sense_stale) — 바뀔 때 사건으로 남긴다
    dash_address = dashboard_address(cfg)
    dashboard = DashboardLink(dash_address, "webots", f"{robot.getName()}@{socket.gethostname()}", hello={
        "map": scene.name, "maps": map_choices, "rows": list(scene.rows), "brain_server": brain_address or None,
        "goal": list(world.goal) if world.goal else None,
    }, cfg=cfg) if dash_address else None
    dash_interval = 1.0 / cfg.dashboard_state_hz if cfg.dashboard_state_hz > 0 else math.inf
    next_dash_time = 0.0

    def tell(event: dict[str, Any]) -> None:
        """사건을 대시보드로 (없으면 아무것도 안 한다)."""
        if dashboard is not None:
            dashboard.send({"type": "event", "event": event})

    if dash_address:
        print(f"[reflexguard] dashboard: {dash_address}", flush=True)
    print(f"[reflexguard] web console: {console_url(robot.getName())}", flush=True)

    # 속도 기록: PERF_REPORT_S마다 터미널에 '실제 시간의 몇 배로 도는지'와 한 스텝의 몫 (화면 없는 PC에서 느린 원인 찾기)
    perf_started, perf_steps, perf_think = time.perf_counter(), 0, 0.0

    try:
        while robot.step(time_step) != -1:
            now = clock()
            perf_steps += 1
            sim_time += dt

            # 1) 웹 조작 화면에서 온 메시지 → 눌린 키 + 조이스틱 → 사용자 명령
            message = robot.wwiReceiveText()
            while message:
                web_input.handle_message(message, now)
                message = robot.wwiReceiveText()
            user_command = web_input.command(now)
            # 대시보드 명령: 스위치·맵은 웹 화면과 같은 길로, 비상 정지·처음부터 다시는 여기서
            for order in (dashboard.take_commands() if dashboard is not None else []):
                name, value = order.get("command"), order.get("value")
                if name in ("connectome", "safety") and value in ("on", "off"):
                    web_input.handle_message(f"{name}:{value}", now)
                elif name == "sensor" and isinstance(value, str):
                    web_input.handle_message(f"sensor:{value}", now)
                elif name == "map" and isinstance(value, str):
                    web_input.handle_message(f"map:{value}", now)
                elif name == "estop" and value in ("on", "off") and (value == "on") != estop:
                    estop = value == "on"
                    note = monitor.note("estop", sim_time, on=estop)
                    print(f"[reflexguard] {describe_event(note)}", flush=True)
                    tell(note)
                elif name == "speed_limit" and value in SPEED_LIMITS and int(value) != speed_limit:
                    speed_limit = int(value)
                    pipe.speed_limit = speed_limit / 100
                    note = monitor.note("speed_limit", sim_time, percent=speed_limit)
                    print(f"[reflexguard] {describe_event(note)}", flush=True)
                    tell(note)
                elif name == "restart" and not crash.frozen:
                    crash.restart_at = sim_time  # 이번 스텝 끝에서 처음 위치로 (충돌 횟수는 늘지 않는다)
            request = web_input.take_switch_request("connectome")
            if request is not None and request != connectome_on:
                connectome_on = pipe.connectome_on = request
                if connectome_on:
                    brain.reset()  # 다시 연결하면 회로 상태(막전위·디코더)를 비우고 새로 시작한다
                note = monitor.note('connectome', sim_time, on=connectome_on)
                print(f"[reflexguard] {describe_event(note)}", flush=True)
                tell(note)
            request = web_input.take_switch_request("safety")
            if request is not None and request != safety_on:
                safety_on = request
                note = monitor.note('safety', sim_time, on=safety_on)
                print(f"[reflexguard] {describe_event(note)}", flush=True)
                tell(note)
            request = web_input.take_sensor_request()
            if request is not None and request != sensor:
                sensor = request
                pipe.use_sensor(sensor)  # 직전 측정을 잊고 다음 측정부터 새로 (커넥톰·공유 제어는 그대로)
                note = monitor.note("sensor", sim_time, to=sensor)
                print(f"[reflexguard] {describe_event(note)}", flush=True)
                tell(note)
            requested_map = web_input.take_map_request()
            if requested_map is not None and requested_map != scene.name and requested_map in SCENES:
                # 웹 화면에서 다른 맵을 골랐다 → 그 월드를 불러온다. Webots가 이 컨트롤러를 끝내고 새 월드의 컨트롤러를
                # (그 맵의 controllerArgs로) 시작하며, 열려 있던 웹 화면은 스스로 다시 연결된다.
                target = WORLDS_DIR / SCENES[requested_map].world_file
                print(f"[reflexguard] loading map '{requested_map}': {target}", flush=True)
                tell(monitor.note("map", sim_time, to=requested_map))
                if dashboard is not None:
                    dashboard.flush()
                robot.worldLoad(str(target))
                break
            if crash.frozen or trip.frozen or estop:
                user_command = ControlCommand()  # 충돌·도착 뒤 멈춰 있거나 비상 정지 중: 입력을 무시하고 멈춘다

            # 2) 카메라 프레임 (H × W × 3, uint8, RGB). camera_period_ms마다 새 영상 — Webots가 새로 찍은 영상일 때만 본다
            #    (카메라 간격이 스텝보다 길면 그 사이에는 직전 영상이 그대로 온다 — NewFrames)
            #    거리 센서로 위험을 인식할 때도 같은 간격으로 잰다 (카메라 영상은 웹 화면에 보여 주려고 그대로 받는다)
            image = camera.getImage()
            think_started = time.perf_counter()
            if new_frames.is_new(image):
                if sensor == "range":
                    pipe.sense_range(*scanner.scan(agent, crowd.actors))  # 3) 거리 → 같은 꼴의 루밍 → ② 커넥톰
                else:
                    frame = frame_from_bgra(image, width, height)
                    if frame is not None:
                        pipe.see(frame)  # 3) ① 루밍 연산 → 속도·감속 보정 → ② 커넥톰 (직전 영상 이후 움직임으로)
                camera_changes = pipe.take_camera_changes()
                if camera_changes:  # 카메라 자기 보정이 값을 바꿨다 (wheelchair/looming/calibration.py)
                    note = monitor.note("camera", sim_time, **change_fields(camera_changes))
                    print(f"[reflexguard] {describe_event(note)}", flush=True)
                    tell(note)
            elif image:
                pipe.same_view(dt)  # 영상은 왔지만 직전과 같다 — 센서 워치독에 (서 있으면 판단 그대로, 움직이면 멈춘 영상으로)
            # ③ 공유 제어(커넥톰 출력만으로 감속·회피·정지). 위험할 때만 사용자 명령을 대신한다.
            #    후방 카메라는 없다: 후진은 커넥톰이 판단하지 않는다 (ReflexModule은 전진만 판단)
            pipe.reflex.manual_without_brain = safety_on  # 처음부터 다시 만든 반사 모듈에도 스위치를 이어 준다
            command = pipe.decide(user_command, dt)
            if pipe.sense_stale != sense_stale:
                sense_stale = pipe.sense_stale
                note = monitor.note("sense_stale", sim_time, why=sense_stale or "ok")
                print(f"[reflexguard] {describe_event(note)}", flush=True)
                tell(note)
            perf_think += time.perf_counter() - think_started
            perf_elapsed = time.perf_counter() - perf_started
            if perf_elapsed >= PERF_REPORT_S:
                print(f"[reflexguard] {speed_report(perf_steps, perf_think, perf_elapsed, dt)}", flush=True)
                perf_started, perf_steps, perf_think = time.perf_counter(), 0, 0.0
            override = pipe.override
            brain_decision = pipe.decision if connectome_on else None

            # 흐름 기록: 반사가 켜지거나(start) 단계가 바뀌거나(stage) 꺼지면(end) 사건이 생긴다
            snapshot = flow_snapshot(sim_time, agent, pipe.looming, brain_decision, pipe.reflex,
                                     user_command, command, override is not None, cfg, connectome=connectome_on)
            events = monitor.update(snapshot)
            trip.count(events)  # 개입 한 번 = 작동(에피소드) 하나
            for event in events:
                print(f"[reflexguard] {describe_event(event)}", flush=True)
                tell(event)
            if monitor.path is not None and not log_announced:
                print(f"[reflexguard] reflex log: {monitor.path}", flush=True)
                log_announced = True

            # 4) 에이전트 이동(벽·장애물 충돌 포함) → 충돌이면 멈추고, 멈춤이 끝나면 처음부터 → Webots 로봇에 반영
            notices = []  # 웹 화면으로 보낼 충돌·다시 시작 사건
            if not crash.frozen:
                crowd.update(dt, agent, world)  # 장애물이 먼저 움직인다 (로봇과 겹치는 걸음은 내딛지 않고, 멈춰 선 로봇은 돌아서 간다)
            heading_before, x_before, y_before = agent.angle_deg, agent.x, agent.y
            collided = agent.apply_command(command, world, dt, crowd.actors)
            # 바퀴(엔코더·IMU) 대신: 이번 스텝에 돈 각도·앞으로 간 속도 → 루밍의 회전 되돌리기·바닥 보정·속도 보정
            pipe.moved(turned_deg(heading_before, agent.angle_deg), forward_speed(x_before, y_before, agent, dt), dt,
                       user_command, command)
            trip.moved(agent)
            if crash.hit(collided, sim_time):
                events += monitor.end_episode(collision=True)  # 진행 중이던 반사 작동은 '충돌로 끝남'으로 닫는다
                trip.collided()
                notices.append(monitor.note("collision", sim_time, **collision_fields(
                    crash, agent, user_command, command, pipe.reflex.stage, connectome_on, safety_on)))
            elif trip.arrived(agent):
                # 도착: 진행 중이던 작동을 닫아 단계별 수에 넣고, 걸린 시간·거리·커넥톰 개입 수를 알린다 → 잠깐 멈췄다가 처음부터
                closing = monitor.end_episode()
                trip.count(closing)
                events += closing
                notices.append(monitor.note("arrival", sim_time, **trip.finish(sim_time)))
            elif crash.due(sim_time) or trip.due(sim_time):
                # 처음부터 다시: 시작 위치로 옮기고 루밍·커넥톰·공유 제어 상태를 새로 만든다 (커넥톰 연결 스위치는 그대로).
                # 스텝 끝에서 옮기므로, 새 루밍 감지기는 다음 스텝의 '시작 위치 영상'부터 본다 (순간이동을 루밍으로 착각하지 않게)
                move_to_start(agent, world, cfg.start_angle_deg)
                crowd.reset()
                if brain_address:
                    brain.reset()               # 서버의 뇌 상태를 비운다 (접속은 그대로)
                else:
                    brain = MaleCNSBrain(cfg)
                previous = pipe
                pipe = WheelchairPipeline(cfg, brain)  # 다음에 새로 찍힌 영상부터 다시 본다
                pipe.adopt_camera(previous)             # 배운 카메라 보정은 그대로
                pipe.use_sensor(sensor)                 # 고른 센서는 그대로
                pipe.connectome_on = connectome_on
                pipe.speed_limit = speed_limit / 100    # 관제의 속도 제한도 그대로
                after = {"after": "arrival"} if trip.frozen else {}
                crash.restarted()
                trip.restarted(sim_time)
                notices.append(monitor.note("restart", sim_time, count=crash.count, **after))
            for notice in notices:
                print(f"[reflexguard] {describe_event(notice)}", flush=True)
                tell(notice)
            if dashboard is not None and now >= next_dash_time:
                # 관제용 상태: 흐름(루밍·커넥톰·공유 제어·위치)과 스위치·충돌·맵·연산 서버, 주변 사람·공 위치
                dashboard.send({"type": "state", "t": round(sim_time, 2), "map": scene.name,
                                "connectome": connectome_on, "safety": safety_on, "estop": estop, "sensor": sensor,
                                "speed_limit": speed_limit,
                                "crash": {"count": crash.count, "frozen": crash.frozen}, "server": brain_status(),
                                "trip": trip.progress(agent, sim_time), "calibration": pipe.calibrator.state(),
                                "th": state_thresholds, "flow": snapshot,
                                "actors": [[round(a.x, 2), round(a.y, 2), a.kind] for a in crowd.actors],
                                "loom": pipe.looming_view()})  # 루밍 동작 보기 (대시보드는 기록 파일에는 쓰지 않는다)
                next_dash_time = max(next_dash_time + dash_interval, now)
            translation, rotation = webots_pose(agent, world)
            translation_field.setSFVec3f(translation)
            rotation_field.setSFRotation(rotation)
            for view in actor_views:
                view.update(world, dt)

            # 5) '컨트롤러가 돌고 있음' 신호를 웹 조작 화면으로 전송 — 항상, web_ready_interval마다
            #    화면은 이 신호(또는 영상)를 받은 뒤에만 키를 보낸다. Webots는 월드를 다 열기 전에
            #    로봇 창 메시지를 받으면 비정상 종료하므로, 화면이 먼저 말을 걸면 안 되기 때문이다.
            #    (신호 하나가 50바이트쯤이라, 받는 화면이 없어 Webots 안에 쌓여도 하루에 8MB쯤이다.)
            if now >= next_ready_time:
                robot.wwiSendText(READY_MESSAGE)
                next_ready_time = now + cfg.web_ready_interval

            # 6) 회피 반사 알림(바로)과 흐름 상태(초당 web_state_hz번)를 웹 조작 화면으로 전송 — 화면이 열려 있을 때만
            if web_input.viewer_connected(now):
                for event in events:
                    robot.wwiSendText(REFLEX_PREFIX + to_json(event))
                for notice in notices:
                    robot.wwiSendText(COLLISION_PREFIX + to_json(notice))
                if now >= next_state_time:
                    crash_state = {"count": crash.count, "frozen": crash.frozen}
                    robot.wwiSendText(STATE_PREFIX + to_json({**snapshot, "th": state_thresholds, "crash": crash_state,
                                                              "safety": safety_on, "map": scene.name, "sensor": sensor,
                                                              "server": brain_status(), "estop": estop,
                                                              "speed_limit": speed_limit,
                                                              "maps": map_choices, "calibration": pipe.calibrator.state(),
                                                              "trip": trip.progress(agent, sim_time)}))
                    # 영상과 같은 방식: '예정 시각 + 간격'으로 잡아 스텝(32ms)과 안 맞아도 평균이 web_state_hz가 되게
                    next_state_time += state_interval
                    if next_state_time < now:
                        next_state_time = now + state_interval

            # 7) 카메라 영상을 웹 조작 화면으로 전송 — 화면이 열려 있을 때만, 초당 web_fps장까지
            #    (받는 화면이 없는데 계속 보내면 Webots 안에 메시지가 쌓인다)
            if image and web_input.viewer_connected(now) and now >= next_frame_time:
                if camera.saveImage(jpeg_name, cfg.web_jpeg_quality) == 0:
                    try:
                        with open(jpeg_name, "rb") as file:
                            robot.wwiSendText(cells_message(pipe.cell_inputs, pipe.detector.cell_front, pipe.cell_filtered))
                            robot.wwiSendText(FRAME_PREFIX + base64.b64encode(file.read()).decode("ascii"))
                    except OSError:
                        pass  # 파일을 못 읽으면(다른 프로그램이 잠깐 잡고 있는 경우 등) 이번 장만 건너뛴다
                # 다음 예정 시각은 '예정 시각 + 간격'으로 잡는다. 스텝 간격(32ms)이 영상 간격(1/15초)과
                # 딱 맞지 않아도 평균이 web_fps가 되게 하려는 것. 오래 쉬었으면 지금부터 다시 센다.
                next_frame_time += frame_interval
                if next_frame_time < now:
                    next_frame_time = now + frame_interval
    finally:
        if dashboard is not None:
            dashboard.flush()
            dashboard.close()
        if brain_address:
            brain.close()
        for event in monitor.close():  # 작동 도중 끝나면 '중간에 끝남'으로 닫는다
            print(f"[reflexguard] {describe_event(event)}", flush=True)
        try:  # 임시 영상 파일 정리
            os.remove(jpeg_name)
        except OSError:
            pass  # 파일이 없거나(영상을 보낸 적이 없음) 지금 지울 수 없으면 그대로 둔다


if __name__ == "__main__":
    from controller import Supervisor  # Webots가 제공하는 모듈 (Webots가 실행할 때만 있다)

    run(Supervisor(), scene_name=sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SCENE)
