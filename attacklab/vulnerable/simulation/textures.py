"""Webots 월드에 쓰는 그림 (numpy로 만든다): 도착지 바닥 바둑판과 굴러가는 공의 줄무늬.

건물·차·사람·바닥 재질은 Webots 공식 에셋을 쓰므로 여기 없다 (simulation/world_assets.py).
simulation/build_webots_world.py가 이 그림을 simulation/webots/worlds/textures/에 PNG로 저장한다.
"""

from __future__ import annotations

import numpy as np

GOAL_SQUARES = 4  # 도착지 바닥: 한 칸(1 m)을 이만큼씩 가로·세로로 나눈 바둑판
GOAL_COLORS = ((64, 168, 96), (150, 214, 160))  # 바둑판 두 색 (진한 초록, 연한 초록) — 대비를 낮춰 바닥 무늬가 루밍으로 잡히지 않게


def goal_image(px_per_square: int = 16) -> np.ndarray:
    """도착지 한 칸(1 m)의 바둑판 그림 (RGB)."""
    n = GOAL_SQUARES * px_per_square
    yy, xx = np.mgrid[0:n, 0:n] // px_per_square
    return np.array(GOAL_COLORS, dtype=np.uint8)[(xx + yy) % 2]


def ball_texture(color: tuple[int, int, int]) -> np.ndarray:
    """공에 감을 그림 (RGB, 64 × 128): 세로 줄무늬 — 구에 감으면 수박 무늬가 되어 구르는 것이 보인다."""
    image = np.empty((64, 128, 3), dtype=np.uint8)
    image[:] = 240
    stripe = 128 // 6
    for k in range(0, 128, stripe):
        if (k // stripe) % 2 == 0:
            image[:, k:k + stripe] = color
    return image
