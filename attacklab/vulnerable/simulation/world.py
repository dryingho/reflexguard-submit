"""격자 맵(World): 텍스트로 정의한 2D 격자와 벽 판정.

좌표계: x = 열 방향(오른쪽 +), y = 행 방향(아래쪽 +). 1칸 = 1.0.
칸 (cx, cy)는 x가 cx~cx+1, y가 cy~cy+1 인 정사각형이다.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

# 칸 종류 코드 (grid 배열에 들어가는 값)
FLOOR = 0
WALL = 1
PILLAR = 2
BUILDING = 3  # 도로 맵: 상가 건물 벽
TRUCK = 4     # 도로 맵: 주차된 화물차 (칸마다 상자 하나)
TREE = 5      # 도로 맵: 가로수 (줄기 + 잎)
FURNITURE = 6  # 소품: 노점·벤치·병원 침대처럼 바닥에 놓인 막힌 물건 (Webots는 크기에 맞는 실제 모델을 놓는다)
CELL_KINDS = 7  # 칸 종류 수 (렌더러의 텍스처 배열 크기)

# 맵 문자 → 칸 종류. 여기에 없는 문자('.', 공백 등)는 전부 바닥으로 본다.
CELL_CODES: dict[str, int] = {"#": WALL, "O": PILLAR, "B": BUILDING, "T": TRUCK, "Y": TREE, "K": FURNITURE}
START_CHAR = "S"  # 에이전트 시작 위치(바닥 칸)
GOAL_CHAR = "G"   # 도착지(바닥 칸). 여러 칸이면 그 칸들 전체가 도착지 — 로봇 중심이 그 안에 들어오면 도착 (simulation/goal.py)

# 바닥 종류 코드 (floor 배열에 들어가는 값). 실내 맵('.')은 전부 INDOOR — 렌더러가 예전처럼 단색 바닥을 그린다.
INDOOR = 0
ASPHALT = 1      # '_' 차도
SIDEWALK = 2     # ',' 인도 보도블록
LANE_X = 3       # '-' 차도 + 가로 방향 중앙선(칸 위쪽 가장자리, 점선)
LANE_Y = 4       # '!' 차도 + 세로 방향 중앙선(칸 왼쪽 가장자리, 점선)
CROSS_X = 5      # '=' 가로로 난 차도를 건너는 횡단보도 (흰 줄이 가로로 길고, 세로로 번갈아)
CROSS_Y = 6      # '|' 세로로 난 차도를 건너는 횡단보도
GRASS = 7        # ';' 잔디 (공원)
FLOOR_KINDS = 8
FLOOR_CODES: dict[str, int] = {"_": ASPHALT, ",": SIDEWALK, "-": LANE_X, "!": LANE_Y, "=": CROSS_X, "|": CROSS_Y,
                               ";": GRASS}

# 기본 맵 — 병원 복도 느낌: 가로 복도 + 병실 2개(침대 'K') + 기둥 있는 로비 + 아래로 꺾이는 복도.
# 맵을 바꾸려면 이 문자열들을 고치거나, World(rows)에 다른 문자열 목록을 넘기면 된다.
DEFAULT_MAP: tuple[str, ...] = (
    "#########################",
    "#K....#....K#...........#",
    "#K....#....K#..O.....O..#",
    "#K....#....K#...........#",
    "###.#####.###...........#",
    "#.......................#",
    "#S......................#",
    "#########..##..O.....O..#",
    "#K......#..##...........#",
    "#K......#..##...........#",
    "#K......#..##########.###",
    "#..........#............#",
    "#.......#..#............#",
    "#.......#..#...######...#",
    "#########.............GG#",
    "#########.............GG#",
    "#########################",
)


class World:
    """텍스트 격자로 만든 맵."""

    def __init__(self, rows: Sequence[str] = DEFAULT_MAP, outdoor: bool = False) -> None:
        """rows: 맵의 각 행을 나타내는 문자열 목록 ('#' 벽, 'O' 기둥, 'S' 시작 위치, 그 외 바닥 — CELL_CODES·FLOOR_CODES).
        outdoor: 바깥 장면(천장 대신 하늘, 바닥 종류별 무늬)인지.
        """
        self.height: int = len(rows)
        self.width: int = max(len(row) for row in rows)
        self.outdoor = outdoor
        self.grid: np.ndarray = np.zeros((self.height, self.width), dtype=np.uint8)  # grid[cy, cx]
        self.floor: np.ndarray = np.zeros((self.height, self.width), dtype=np.uint8)  # floor[cy, cx] — 바닥 종류

        self.goal_mask: np.ndarray = np.zeros((self.height, self.width), dtype=bool)  # goal_mask[cy, cx] — 도착지 칸

        start: tuple[float, float] | None = None
        for cy, row in enumerate(rows):
            for cx, char in enumerate(row):
                if char == START_CHAR:
                    start = (cx + 0.5, cy + 0.5)  # 칸의 중앙
                elif char == GOAL_CHAR:
                    self.goal_mask[cy, cx] = True  # 바닥 칸 (바닥 무늬는 아래에서 이웃과 같게)
                else:
                    self.grid[cy, cx] = CELL_CODES.get(char, FLOOR)
                    self.floor[cy, cx] = FLOOR_CODES.get(char, INDOOR)
        if start is None:  # 'S'가 없는 맵이면 첫 번째 바닥 칸에서 시작
            cy, cx = np.argwhere(self.grid == FLOOR)[0]
            start = (float(cx) + 0.5, float(cy) + 0.5)
        self.start: tuple[float, float] = start
        # 시작·도착 칸의 바닥은 이웃 바닥과 같게 (인도 위에서 출발하면 인도)
        for sx, sy in [(int(start[0]), int(start[1]))] + [(int(cx), int(cy)) for cy, cx in np.argwhere(self.goal_mask)]:
            around = [int(self.floor[y, x]) for x, y in ((sx - 1, sy), (sx + 1, sy), (sx, sy - 1), (sx, sy + 1))
                      if 0 <= x < self.width and 0 <= y < self.height and self.grid[y, x] == FLOOR
                      and not self.goal_mask[y, x]]
            if around:
                self.floor[sy, sx] = max(set(around), key=around.count)
        goal_cells = np.argwhere(self.goal_mask)
        # 도착지 칸들의 가운데 (없으면 None — 도착 판정·안내를 하지 않는다)
        self.goal: tuple[float, float] | None = (
            (float(goal_cells[:, 1].mean()) + 0.5, float(goal_cells[:, 0].mean()) + 0.5) if len(goal_cells) else None)

        # 맵 바깥을 벽으로 취급하기 위해, 벽으로 한 겹 둘러싼 사본을 만들어 둔다 (레이캐스팅용)
        self._padded: np.ndarray = np.pad(self.grid, 1, constant_values=WALL)

    def in_goal(self, x: float, y: float) -> bool:
        """점 (x, y)가 도착지 칸 안인지."""
        cx, cy = math.floor(x), math.floor(y)
        return 0 <= cx < self.width and 0 <= cy < self.height and bool(self.goal_mask[cy, cx])

    def is_wall(self, cx: int, cy: int) -> bool:
        """칸 (cx, cy)가 막힌 칸(벽·기둥)인지. 맵 밖은 벽으로 취급한다."""
        if 0 <= cx < self.width and 0 <= cy < self.height:
            return bool(self.grid[cy, cx] != FLOOR)
        return True

    def cells(self, cx: np.ndarray, cy: np.ndarray) -> np.ndarray:
        """여러 칸의 종류 코드를 한 번에 조회한다 (레이캐스팅용).

        맵 바로 바깥 한 겹(-1 또는 width/height)까지 조회할 수 있고, 그곳은 벽이다.
        """
        return self._padded[cy + 1, cx + 1]

    def floors(self, cx: np.ndarray, cy: np.ndarray) -> np.ndarray:
        """여러 칸의 바닥 종류를 한 번에 조회한다 (바닥 그리기용). 맵 밖은 가장자리 칸의 바닥."""
        return self.floor[np.clip(cy, 0, self.height - 1), np.clip(cx, 0, self.width - 1)]
