"""시뮬레이션의 거리 센서 (2D 라이다 같은 것): 맵(벽·기둥·건물·차)과 사람·공까지의 거리를 빛살마다 잰다.

실제 휠체어에서는 라이다가 이 값을 준다. 시뮬레이션에서는 맵 격자(simulation/world.py)와 장애물 원(simulation/actors.py)에
빛살을 쏴서 만든다 — 충돌 판정과 같은 모양이라, 거리 센서는 '부딪힐 것'을 빠짐없이 본다(높이는 모른다).

    scanner = RangeScanner(world, cfg)
    ranges, offsets = scanner.scan(agent, actors)   # 빛살마다 거리(m, 맞은 것이 없으면 range_max_m)와 몸 기준 각도(라디안, + 오른쪽)

쓰는 곳: Webots 컨트롤러·도착 시험의 '거리 센서 → 커넥톰'(wheelchair/range_sensor.py), 비교용 거리 센서 판단(tools/baselines.py).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from common.config import CONFIG, Config
from simulation.world import FLOOR, World

RES_M = 0.05  # 빛살을 따라 벽을 찾는 걸음(m)


class RangeScanner:
    """카메라와 같은 시야(fov_deg)에 range_step_deg마다 빛살을 쏜다 (모듈 설명)."""

    def __init__(self, world: World, cfg: Config = CONFIG) -> None:
        self.world, self.cfg = world, cfg
        half = cfg.fov_deg / 2.0
        self.offsets = np.radians(np.arange(-half, half + 1e-9, cfg.range_step_deg))  # 몸 방향 기준 (+ 시계 방향 = 오른쪽)
        self.steps = np.arange(RES_M, cfg.range_max_m + 1e-9, RES_M)

    def scan(self, agent: Any, actors: list[Any]) -> tuple[np.ndarray, np.ndarray]:
        """빛살마다 (거리, 몸 방향 기준 각도). 맞은 것이 없으면 range_max_m."""
        far = self.cfg.range_max_m
        angles = math.radians(agent.angle_deg) + self.offsets
        xs = agent.x + np.cos(angles)[:, None] * self.steps[None, :]
        ys = agent.y + np.sin(angles)[:, None] * self.steps[None, :]
        w, h = self.world.width, self.world.height
        cx = np.clip(np.floor(xs).astype(int), -1, w)
        cy = np.clip(np.floor(ys).astype(int), -1, h)
        blocked = self.world.cells(cx, cy) != FLOOR
        found = blocked.any(axis=1)
        first = blocked.argmax(axis=1)
        # 걸음(RES_M) 사이를 반으로 나눠 가며 벽 면을 1~2 mm까지 찾는다 — 5 cm 계단이면 32 ms에 6 cm 다가가는 동안
        # 거리가 5 cm·10 cm로 번갈아 줄어, 다가오는 빠르기(wheelchair/range_sensor.py)가 크게 출렁인다
        low = np.where(first > 0, self.steps[np.maximum(first - 1, 0)], 0.0)
        high = self.steps[first]
        cos, sin = np.cos(angles), np.sin(angles)
        for _ in range(5):
            middle = (low + high) / 2.0
            inside = self.world.cells(np.clip(np.floor(agent.x + cos * middle).astype(int), -1, w),
                                      np.clip(np.floor(agent.y + sin * middle).astype(int), -1, h)) != FLOOR
            high, low = np.where(inside, middle, high), np.where(inside, low, middle)
        ranges = np.where(found, high, far)
        for actor in actors:  # 원과 빛살이 만나는 가까운 쪽
            rx, ry = actor.x - agent.x, actor.y - agent.y
            along = rx * np.cos(angles) + ry * np.sin(angles)
            miss2 = rx * rx + ry * ry - along * along
            inside = actor.radius ** 2 - miss2
            hit = (inside >= 0.0) & (along > 0.0)
            distance = along - np.sqrt(np.maximum(inside, 0.0))
            ranges = np.where(hit & (distance > 0.0) & (distance < ranges), distance, ranges)
        return ranges, self.offsets.copy()
