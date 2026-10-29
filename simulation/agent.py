"""시뮬레이션 속 휠체어: 제어 명령 적용, dt 기반 이동(감속 한계), 원형 충돌 처리.

실제 휠체어에서는 이 부분을 바퀴 모터가 맡는다. 제어 명령(ControlCommand)은 common/control.py에 있다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from common.config import CONFIG
from common.control import ControlCommand
from simulation.world import World

_EPS = 1e-9                # 부동소수점 오차 허용치
_MAX_PUSH_ITERATIONS = 4   # 한 번의 이동에서 벽 밀어내기를 반복하는 최대 횟수


def normalize_angle_deg(angle_deg: float) -> float:
    """각도를 0 이상 360 미만으로 정규화한다."""
    angle = angle_deg % 360.0
    # 아주 작은 음수는 반올림 때문에 % 결과가 정확히 360.0이 될 수 있어 따로 처리
    return 0.0 if angle >= 360.0 else angle


def turned_deg(before_deg: float, after_deg: float) -> float:
    """before → after로 돈 각도(도, +시계 방향). -180 ~ +180으로 맞춘다 (359 → 1이면 +2)."""
    return (after_deg - before_deg + 180.0) % 360.0 - 180.0


def forward_speed(before_x: float, before_y: float, agent: "Agent", dt: float) -> float:
    """(before_x, before_y) → 지금 자리로 보는 방향을 따라 간 속도(칸/초 = m/s). 루밍 감지의 바닥 보정에 쓴다."""
    if dt <= 0.0:
        return 0.0
    heading = math.radians(agent.angle_deg)
    return ((agent.x - before_x) * math.cos(heading) + (agent.y - before_y) * math.sin(heading)) / dt


def _clamp_unit(value: float) -> float:
    """값을 -1 ~ +1 범위로 자른다."""
    return max(-1.0, min(1.0, value))


def _toward(current: float, target: float, max_drop: float) -> float:
    """속도 current → target. 같은 쪽으로 빨라지면 바로 target, 줄이거나 반대로 가면 한 번에 max_drop까지만 (0이면 바로)."""
    if max_drop <= 0.0 or (current * target >= 0.0 and abs(target) >= abs(current)):
        return target
    if current > target:
        return max(target, current - max_drop)
    return min(target, current + max_drop)


@dataclass
class Agent:
    """원형 충돌체 하나. 위치 (x, y)는 칸 단위, angle_deg는 바라보는 방향(0 = +x, 시계 방향 +)."""

    x: float
    y: float
    angle_deg: float = CONFIG.start_angle_deg
    radius: float = CONFIG.agent_radius
    move_speed: float = CONFIG.move_speed
    turn_speed_deg: float = CONFIG.turn_speed_deg
    contact: Any = field(default=None, repr=False, compare=False)  # 마지막 이동에서 부딪힌 것: "wall" 또는 장애물 객체
    brake_decel: float = 0.0   # 줄일 때의 감속 한계(m/s²). 0이면 명령대로 바로 선다 (config.brake_decel)
    speed: float = 0.0         # 지금 앞(+)·뒤(-)로 가는 속도(m/s)
    side_speed: float = 0.0    # 지금 오른쪽(+)·왼쪽(-)으로 가는 속도(m/s)

    def apply_command(self, command: ControlCommand, world: World, dt: float, obstacles: Sequence[Any] = ()) -> bool:
        """제어 명령을 dt초 동안 적용한다 (회전 → 이동 → 충돌 처리).

        이동량이 '속도 × dt'라서 프레임 속도(PC 성능)와 무관하게 같은 속도로 움직인다.
        obstacles: 움직이는 장애물(actors.Actor 등 x, y, radius를 가진 원). 벽처럼 막고 부딪힘으로 센다.
        반환: 이번 이동에서 벽·장애물에 부딪혔으면 True (파고들어 밀려났거나 막힘). 벽에 붙어 제자리 회전만 하면 False.
        부딪힌 것은 self.contact에 남는다.
        """
        forward = _clamp_unit(command.forward)
        turn = _clamp_unit(command.turn)
        strafe = _clamp_unit(command.strafe)

        # 1) 회전
        self.angle_deg = normalize_angle_deg(self.angle_deg + turn * self.turn_speed_deg * dt)

        # 2) 이동량: 바라보는 방향(전진) 성분 + 그 오른쪽(평행이동) 성분
        magnitude = math.hypot(forward, strafe)
        if magnitude > 1.0:  # 전진과 평행이동을 같이 눌러도 더 빨라지지 않게
            forward /= magnitude
            strafe /= magnitude
        # 줄이는 쪽은 brake_decel까지만 (빨라지는 쪽은 바로) — 0이면 명령한 속도 그대로
        self.speed = _toward(self.speed, forward * self.move_speed, self.brake_decel * dt)
        self.side_speed = _toward(self.side_speed, strafe * self.move_speed, self.brake_decel * dt)
        angle = math.radians(self.angle_deg)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        dx = (cos_a * self.speed - sin_a * self.side_speed) * dt  # 오른쪽 방향 벡터 = (-sin, cos)
        dy = (sin_a * self.speed + cos_a * self.side_speed) * dt

        # 3) 이동 + 충돌 처리
        self.contact = None
        hit = self._move(dx, dy, world, obstacles)
        if hit:
            self.speed = self.side_speed = 0.0  # 부딪혀 멈췄다
        return hit

    def _move(self, dx: float, dy: float, world: World, obstacles: Sequence[Any] = ()) -> bool:
        """(dx, dy)만큼 이동하되 벽·장애물과 겹치지 않게 한다. 벽·장애물에 부딪혔으면 True.

        한 번에 멀리 움직이면 벽을 건너뛸 수 있으므로(터널링),
        반지름의 절반 이하 길이의 조각으로 나눠서 움직인다.
        """
        length = math.hypot(dx, dy)
        if length == 0.0:
            return False
        hit = False
        steps = math.ceil(length / (self.radius * 0.5))
        step_x, step_y = dx / steps, dy / steps
        for _ in range(steps):
            prev_x, prev_y = self.x, self.y
            self.x += step_x
            self.y += step_y
            overlap = self._deepest_overlap(world, obstacles)
            if overlap is not None:  # 이 조각에서 벽·장애물에 파고들었다 = 부딪힘
                hit = True
                self.contact = self.contact or overlap[3]
            if not self._push_out_of_walls(world, obstacles):
                # 겹침을 풀지 못한 경우(지름보다 좁은 틈 등): 이 조각은 취소하고 멈춘다
                self.x, self.y = prev_x, prev_y
                break
        return hit

    def _push_out_of_walls(self, world: World, obstacles: Sequence[Any] = ()) -> bool:
        """벽과 겹친 만큼 원을 밖으로 밀어낸다. 겹침이 모두 풀리면 True.

        평평한 벽에서는 벽면에 수직인 방향으로만 밀려나므로, 벽을 따라가는 방향의
        움직임은 그대로 남는다 → 벽에 비스듬히 부딪히면 벽을 따라 미끄러진다.
        """
        for _ in range(_MAX_PUSH_ITERATIONS):
            overlap = self._deepest_overlap(world, obstacles)
            if overlap is None:
                return True
            distance, near_x, near_y, _ = overlap
            if distance < _EPS:
                return False  # 중심이 벽 칸 안에 있어 밀어낼 방향을 정할 수 없음
            # 최근접점 → 중심 방향으로, 중심과의 거리가 정확히 반지름이 되도록 민다
            scale = (self.radius - distance) / distance
            self.x += (self.x - near_x) * scale
            self.y += (self.y - near_y) * scale
        return self._deepest_overlap(world, obstacles) is None

    def _deepest_overlap(self, world: World, obstacles: Sequence[Any] = ()) -> tuple[float, float, float, Any] | None:
        """원과 겹친 벽 칸·장애물 중 가장 깊이 겹친 것을 찾는다.

        반환: (원 중심 ~ 그것의 최근접점 거리, 최근접점 x, 최근접점 y, "wall" 또는 장애물). 겹친 것이 없으면 None.
        가장 깊은 것부터 처리해야 나란히 붙은 칸들의 이음매(모서리)에 걸리지 않고 매끄럽게 미끄러진다.
        """
        radius = self.radius
        deepest: tuple[float, float, float, Any] | None = None
        for cy in range(math.floor(self.y - radius), math.floor(self.y + radius) + 1):
            for cx in range(math.floor(self.x - radius), math.floor(self.x + radius) + 1):
                if not world.is_wall(cx, cy):
                    continue
                # 원 중심에서 이 칸(정사각형)까지 가장 가까운 점
                near_x = min(max(self.x, cx), cx + 1.0)
                near_y = min(max(self.y, cy), cy + 1.0)
                distance = math.hypot(self.x - near_x, self.y - near_y)
                if distance < radius - _EPS and (deepest is None or distance < deepest[0]):
                    deepest = (distance, near_x, near_y, "wall")
        for obstacle in obstacles:  # 원기둥 장애물: 최근접점은 장애물 둘레 위, 중심을 잇는 선 위의 점
            gap_x, gap_y = self.x - obstacle.x, self.y - obstacle.y
            centers = math.hypot(gap_x, gap_y)
            distance = centers - obstacle.radius
            if distance < radius - _EPS and (deepest is None or distance < deepest[0]):
                if centers < _EPS:
                    deepest = (0.0, self.x, self.y, obstacle)  # 중심이 겹침: 밀어낼 방향을 정할 수 없다
                elif distance <= _EPS:
                    # 중심이 장애물 안: 둘레 밖으로 밀어낼 수 있게 '장애물 반대편의 점'을 최근접점으로 준다
                    deepest = (_EPS * 2, self.x - gap_x / centers * _EPS * 2, self.y - gap_y / centers * _EPS * 2, obstacle)
                else:
                    scale = obstacle.radius / centers
                    deepest = (distance, obstacle.x + gap_x * scale, obstacle.y + gap_y * scale, obstacle)
        return deepest
