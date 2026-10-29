"""움직이는 장애물(사람·공): 정해진 길을 오가거나, 로봇이 다가오면 숨어 있던 곳에서 튀어나온다.

Webots 컨트롤러와 시험 도구가 같은 코드로 움직이고 부딪힘을 판정한다. Webots는 그 위치를 3D로 그리기만 한다.

    crowd = Crowd(scene.actors)
    crowd.update(dt, agent)                                   # 장애물 먼저 움직이고
    hit = agent.apply_command(command, world, dt, crowd.actors)  # 로봇이 장애물과 겹치면 충돌
    view.update(world, dt)                                     # Webots 노드를 그 자리로 (컨트롤러의 ActorView)

장애물은 로봇과 겹치게 되는 걸음은 내딛지 않는다(사람은 멈춰 선 휠체어에 와서 부딪히지 않는다).
단 Crowd(bold_popouts=True)면 튀어나오는 사람(popout)은 움직이는 로봇을 봐주지 않는다 — 휠체어가 서 줄 거라 보고 그대로
나온다(멈춰 선 로봇은 여전히 피한다). 시험 도구의 현실 조건(tools/goal_trials.py --realistic)에서 쓴다.
그렇게 _GIVE_WAY_S초 넘게 막혀 있으면 오가는 사람(loop)은 돌아서 왔던 길로 가고, 튀어나온 사람(popout)은 그 자리를
끝점으로 삼아 잠깐 서 있다가 돌아간다 — 멈춰 선 로봇 앞에서 서로 기다리지 않게.
그래서 충돌은 로봇이 움직여서 장애물과 겹친 경우만 생긴다 — 커넥톰이 막았어야 하는 경우.

모드
- loop: path를 따라 끝까지 갔다가(끝에서 pause_s 쉬고) 되돌아오기를 반복한다. 점이 하나면 그 자리에 서 있다.
- popout: path[0](건물 뒤·골목 안처럼 안 보이는 곳)에서 기다리다가, 로봇이 trigger 원 안에 들어오면 path 끝까지
  speed로 나온다. 끝에서 pause_s 서 있다가 원래 자리로 돌아가고, 로봇이 trigger 원 밖(+1칸)으로 나가면 다시 준비한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

Color = tuple[int, int, int]

# 종류별 (반지름 m, 키 m). 반지름은 위에서 본 몸의 크기(충돌 판정), 키는 그림 크기
KINDS: dict[str, tuple[float, float]] = {
    "adult": (0.25, 1.7),
    "child": (0.2, 1.15),
    "ball": (0.2, 0.4),
}

_YIELD_GAP = 0.05  # 장애물이 로봇과 이만큼(칸)보다 가까워지는 걸음은 내딛지 않는다
_GIVE_WAY_S = 2.0  # 오가는 사람이 로봇에 이만큼(초) 막혀 있으면 돌아선다


@dataclass(frozen=True)
class ActorSpec:
    """장애물 하나의 정의 (simulation/scenes.py에 적는다). 좌표는 칸 단위, simulation/world.py와 같은 좌표계."""

    name: str                              # Webots 노드 이름(ACTOR_<name>)과 기록에 쓴다
    kind: str                              # KINDS의 열쇠: adult / child / ball
    path: tuple[tuple[float, float], ...]  # 지나가는 점들
    speed: float = 1.2                     # m/s (사람 걷기 1.2, 뛰기 2.5)
    mode: str = "loop"                     # loop / popout
    trigger: tuple[float, float, float] | None = None  # popout: (x, y, 반지름) — 로봇 중심이 이 원 안에 들어오면 출발
    pause_s: float = 1.0                   # 끝점에서 쉬는 시간(초)
    color: Color = (70, 110, 190)          # 윗옷(공은 줄무늬) 색


class Actor:
    """움직이는 장애물 하나의 상태. x, y, radius를 가져서 Agent.apply_command의 장애물로 그대로 쓴다."""

    def __init__(self, spec: ActorSpec) -> None:
        if spec.kind not in KINDS:
            raise ValueError(f"unknown actor kind: {spec.kind}")
        if spec.mode not in ("loop", "popout") or (spec.mode == "popout" and spec.trigger is None):
            raise ValueError(f"bad actor mode: {spec.mode}")
        self.spec = spec
        self.name = spec.name
        self.kind = spec.kind
        self.radius, self.height = KINDS[spec.kind]
        self.bold = False  # True면 움직이는 로봇에게 길을 비켜 주지 않는다 (Crowd(bold_popouts=True)의 popout)
        self.reset()

    def reset(self) -> None:
        """처음 상태로 (충돌 뒤 처음부터 다시 할 때)."""
        self.x, self.y = self.spec.path[0]
        self.target = 1 if len(self.spec.path) > 1 else 0  # 다음에 갈 점의 번호
        self.step = 1                                       # +1: path 순서대로, -1: 거꾸로
        self.state = "walk" if self.spec.mode == "loop" else "ready"
        self.wait = 0.0                                     # 남은 쉬는 시간(초)
        self.blocked_s = 0.0                                # 로봇에 막혀 서 있은 시간(초)
        self.heading_deg = self._heading_to(self.target)

    @property
    def moving(self) -> bool:
        return self.state in ("walk", "out", "back") and self.wait <= 0.0

    def _heading_to(self, index: int) -> float:
        tx, ty = self.spec.path[index]
        if (tx, ty) == (self.x, self.y):
            return getattr(self, "heading_deg", 0.0)
        return math.degrees(math.atan2(ty - self.y, tx - self.x)) % 360.0

    def _robot_inside(self, robot: Any, margin: float = 0.0) -> bool:
        tx, ty, radius = self.spec.trigger
        return math.hypot(robot.x - tx, robot.y - ty) <= radius + margin

    def update(self, dt: float, robot: Any) -> None:
        """dt초 진행한다. robot: x, y, radius를 가진 로봇(Agent)."""
        if self.state == "ready":
            if self._robot_inside(robot):
                self.state, self.step, self.target = "out", 1, min(1, len(self.spec.path) - 1)
            else:
                return
        if self.state == "hidden":  # 돌아와서, 로봇이 떠나기를 기다린다
            if not self._robot_inside(robot, margin=1.0):
                self.state = "ready"
            return
        if self.wait > 0.0:
            self.wait -= dt
            return
        if len(self.spec.path) < 2:
            return  # 서 있는 사람
        if not self._advance(self.spec.speed * dt, robot):
            self.blocked_s = 0.0
            return
        self.blocked_s += dt
        if self.blocked_s < _GIVE_WAY_S:
            return
        self.blocked_s = 0.0
        if self.state == "walk":    # 오래 막혔다 → 돌아서 왔던 길로
            self.step = -self.step
            self.target += self.step
        elif self.state == "out":   # 다 나오지 못했다 → 여기서 잠깐 서 있다가 돌아간다
            self.state, self.step, self.target = "back", -1, self.target - 1
            self.wait = self.spec.pause_s

    def _advance(self, distance: float, robot: Any) -> bool:
        """path를 따라 distance만큼 간다. 로봇과 겹치게 되는 걸음은 내딛지 않는다. 그래서 멈췄으면 True."""
        while distance > 1e-9:
            tx, ty = self.spec.path[self.target]
            gap = math.hypot(tx - self.x, ty - self.y)
            move = min(distance, gap)
            if gap > 1e-9:
                nx = self.x + (tx - self.x) / gap * move
                ny = self.y + (ty - self.y) / gap * move
                self.heading_deg = math.degrees(math.atan2(ty - self.y, tx - self.x)) % 360.0
                after = math.hypot(nx - robot.x, ny - robot.y)
                before = math.hypot(self.x - robot.x, self.y - robot.y)
                robot_moving = abs(getattr(robot, "speed", 0.0)) + abs(getattr(robot, "side_speed", 0.0)) > 0.05
                if after < self.radius + robot.radius + _YIELD_GAP and after < before and not (self.bold and robot_moving):
                    if self.state == "back" and before > 1e-6:
                        # 돌아가는 길이 로봇 쪽으로 비스듬하면 로봇 반대쪽으로 한 걸음 물러선다 (서로 기다리지 않게)
                        self.x += (self.x - robot.x) / before * move
                        self.y += (self.y - robot.y) / before * move
                        return False
                    return True  # 로봇 앞에서 멈춰 선다 (로봇이 비키면 다시 간다). 멀어지는 걸음은 막지 않는다
                self.x, self.y = nx, ny
            distance -= move
            if move >= gap - 1e-9:
                if not self._reached_point():
                    return False
        return False

    def _reached_point(self) -> bool:
        """지금 점(target)에 닿았다. 계속 걸어가면 True, 여기서 멈추면 False."""
        last = len(self.spec.path) - 1
        nxt = self.target + self.step
        if 0 <= nxt <= last:
            self.target = nxt
            return True
        if self.state == "walk":  # loop: 끝에서 쉬었다가 되돌아간다
            self.step = -self.step
            self.target += self.step
            self.wait = self.spec.pause_s
            return False
        if self.state == "out":   # popout: 다 나왔다 → 잠깐 서 있다가 돌아간다
            self.state, self.step, self.target = "back", -1, last - 1
            self.wait = self.spec.pause_s
            return False
        self.state = "hidden"     # 다 돌아왔다
        self.step, self.target = 1, min(1, last)
        return False


class Crowd:
    """장면의 장애물 전부."""

    def __init__(self, specs: Sequence[ActorSpec] = (), bold_popouts: bool = False) -> None:
        self.actors: list[Actor] = [Actor(spec) for spec in specs]
        for actor in self.actors:
            actor.bold = bold_popouts and actor.spec.mode == "popout"

    def update(self, dt: float, robot: Any) -> None:
        for actor in self.actors:
            actor.update(dt, robot)

    def reset(self) -> None:
        for actor in self.actors:
            actor.reset()

    def nearest(self, x: float, y: float) -> tuple[Actor | None, float]:
        """(x, y)에서 가장 가까운 장애물과 몸 가장자리까지의 거리."""
        best, best_gap = None, math.inf
        for actor in self.actors:
            gap = math.hypot(actor.x - x, actor.y - y) - actor.radius
            if gap < best_gap:
                best, best_gap = actor, gap
        return best, best_gap
