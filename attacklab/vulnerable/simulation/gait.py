"""걷는 동작: 사람 모델(Webots 보행자 몸 부위로 조립, simulation/world_assets.py)의 팔·다리 관절 각도와 공이 굴러간 각도.

걸음새 계수는 Webots 보행자 컨트롤러(projects/humans/pedestrian/controllers/pedestrian/pedestrian.py,
Copyright Cyberbotics Ltd., Apache License 2.0)의 경험값을 그대로 쓴다 — 한 걸음 주기를 8단계로 나눈 관절 각도(라디안).

    gait = Gait(scale)                       # 모델 크기 배율 (어른 1.7/1.8, 아이 1.15/1.8)
    angles = gait.update(moved_m, dt)        # 이번 스텝에 걸은 거리 → {관절 이름: 각도}
"""

from __future__ import annotations

import math

JOINT_ORDER = ("LA", "LLA", "LH", "RA", "RLA", "RH", "LL", "LLL", "LF", "RL", "RLL", "RF")
_ANGLES = (
    (-0.52, -0.15, 0.58, 0.7, 0.52, 0.17, -0.36, -0.74),   # 왼팔
    (0.0, -0.16, -0.7, -0.38, -0.47, -0.3, -0.58, -0.21),  # 왼쪽 아래팔
    (0.12, 0.0, 0.12, 0.2, 0.0, -0.17, -0.25, 0.0),        # 왼손
    (0.52, 0.17, -0.36, -0.74, -0.52, -0.15, 0.58, 0.7),   # 오른팔
    (-0.47, -0.3, -0.58, -0.21, 0.0, -0.16, -0.7, -0.38),  # 오른쪽 아래팔
    (0.0, -0.17, -0.25, 0.0, 0.12, 0.0, 0.12, 0.2),        # 오른손
    (-0.55, -0.85, -1.14, -0.7, -0.56, 0.12, 0.24, 0.4),   # 왼다리
    (1.4, 1.58, 1.71, 0.49, 0.84, 0.0, 0.14, 0.26),        # 왼쪽 아래다리
    (0.07, 0.07, -0.07, -0.36, 0.0, 0.0, 0.32, -0.07),     # 왼발
    (-0.56, 0.12, 0.24, 0.4, -0.55, -0.85, -1.14, -0.7),   # 오른다리
    (0.84, 0.0, 0.14, 0.26, 1.4, 1.58, 1.71, 0.49),        # 오른쪽 아래다리
    (0.0, 0.0, 0.42, -0.07, 0.07, 0.07, -0.07, -0.36),     # 오른발
)
_STEPS = 8
_CYCLE_TO_DISTANCE = 0.22   # 한 단계(8분의 1주기) 동안 걷는 거리(m), 키 1.8 m 모델 기준
_SETTLE_S = 0.25            # 멈추면 이 시간상수로 차렷 자세가 된다


class Gait:
    """사람 하나의 걸음 상태: 걸은 거리(주기 위치)와 움직이는 정도(멈추면 0으로 줄어든다)."""

    def __init__(self, scale: float = 1.0) -> None:
        self.scale = max(scale, 0.1)
        self.distance = 0.0
        self.motion = 0.0

    def update(self, moved: float, dt: float) -> dict[str, float]:
        """이번 스텝에 걸은 거리(m)와 시간(초) → 관절 각도. 멈춰 있으면 차렷 자세 쪽으로 서서히 돌아간다."""
        self.distance += max(moved, 0.0)
        walking = 1.0 if moved > 1e-4 else 0.0
        if dt > 0.0:
            self.motion += (walking - self.motion) * (1.0 - math.exp(-dt / _SETTLE_S))
        phase = self.distance / (_CYCLE_TO_DISTANCE * self.scale)
        step = int(phase) % _STEPS
        ratio = phase - math.floor(phase)
        nxt = (step + 1) % _STEPS
        return {name: (row[step] * (1.0 - ratio) + row[nxt] * ratio) * self.motion
                for name, row in zip(JOINT_ORDER, _ANGLES)}


def roll_angle(distance: float, radius: float) -> float:
    """공이 distance(m) 굴러갔을 때 돈 각도(라디안)."""
    return distance / max(radius, 1e-6)
