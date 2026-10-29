"""거리 센서 → 커넥톰 입력: 2D 거리 센서(라이다 같은 것)가 잰 거리로 카메라 루밍과 같은 꼴의 입력(LoomingInfo)을 만든다.

위험 인식은 거리 센서가, 판단·개입은 같은 커넥톰(LPLC2 → DNp01, HS)과 같은 공유 제어가 한다 (config.reflex_sensor = "range").
카메라 루밍이 재는 것은 칸마다의 확장률 = 다가오는 빠르기 ÷ 거리 = 1/충돌까지 시간(1/초)이다. 거리 센서는 빛살마다 거리를
바로 알므로 같은 양을 이렇게 만든다:

1. 빛살마다 다가오는 빠르기: 직전 측정과의 거리 차 ÷ 간격. 그 사이 돈 만큼 직전 측정을 돌려 놓고 비교한다(제자리 회전은 다가옴이
   아니다). 거리가 range_max_closing_mps보다 빨리 줄면 다가온 것이 아니라 다른 것이 빛살 앞으로 들어온 것이라 0으로 둔다
   (지나가던 사람의 가장자리, 모퉁이 뒤에서 나온 벽). 처음 보이거나 멀어지는 것도 0.
2. 확장률 = 다가오는 빠르기 ÷ 거리. 정면 벽에 v로 다가가면 어느 빛살이든 v/Z — 카메라와 같다. 옆으로 나란한 벽은 같은 각도의
   빛살이 늘 같은 거리라 0이다.
3. 휠체어 통로로 거르기: 맞은 자리가 진행 방향에서 옆으로 looming_course_inner_m 안이면 그대로, outer_m 밖이면 0 (카메라는
   거리를 '빠르기 ÷ 확장률'로 짐작하지만 거리 센서는 바로 안다 — 서 있어도 거른다).
4. 칸에 펼치기: 카메라와 같은 칸(looming_grid_rows × cols, 같은 몸 기준 방향)에 넣는다. 거리 센서는 높이를 모르므로, 맞은 것이
   바닥부터 range_object_top_m까지 서 있다고 보고 그 높이가 보이는 위아래각만큼 칸을 채운다(가까울수록 여러 줄). 칸 값은
   그 칸의 가로각 안에 든 빛살들의 평균(맞은 높이가 칸을 덮는 비율을 곱해서).
5. 옆 흐름(HS 입력): 앞으로 v로 갈 때 θ 방향 거리 r의 점이 옆으로 흐르는 빠르기 v·sin θ / r (도/초, + 오른쪽으로) — 가까운
   옆벽일수록 빠르다. 카메라처럼 회전으로 생긴 흐름은 넣지 않는다.
6. 카메라와 같은 지수 평활(looming_smooth_tau).

그래서 커넥톰의 수용장 가중치·이득·디코더·공유 제어를 하나도 바꾸지 않고 입력만 바뀐다. 감속 보정(at_intended_speed·
for_braking)도 같은 양이라 그대로 쓴다 (wheelchair/pipeline.py).
"""

from __future__ import annotations

import math

import numpy as np

from common.config import CONFIG, Config
from wheelchair.looming.detector import LoomingDetector, LoomingInfo

_EPS = 1e-9


class RangeLooming:
    """연속한 거리 측정 → 칸별 확장률·옆 흐름 (모듈 설명)."""

    def __init__(self, cfg: Config = CONFIG) -> None:
        self.cfg = cfg
        camera = LoomingDetector(cfg)
        shape = camera.shrunk_shape((cfg.render_height, cfg.render_width))
        self.cell_angles, azimuth_edges, elevation_edges = camera.cell_bounds(shape)
        self._az_low, self._az_high = azimuth_edges[:, :-1, None], azimuth_edges[:, 1:, None]       # (rows, cols, 1)
        self._el_top, self._el_bottom = elevation_edges[:-1, :, None], elevation_edges[1:, :, None]  # 위 줄부터
        azimuth, elevation = self.cell_angles[..., 0], self.cell_angles[..., 1]
        roi = ((np.abs(azimuth) <= cfg.looming_roi_half_width_deg) & (np.abs(elevation) <= cfg.looming_roi_half_height_deg))
        self._roi_left, self._roi_right = roi & (azimuth <= 0.0), roi & (azimuth >= 0.0)  # 가운데 줄은 양쪽에
        self.last_measured: np.ndarray | None = None  # 마지막 측정의 칸별 확장률 (통로로 거르기 전, 평활 전)
        self.last_gate: np.ndarray | None = None      # 칸별 통로 가중치 0~1 (거른 칸 표시용)
        self.reset()

    def reset(self) -> None:
        """처음부터 (센서를 바꿨을 때 등): 직전 측정을 잊는다. 다음 측정은 비교할 것이 없어 0이다."""
        rows, cols = self.cell_angles.shape[:2]
        self._prev: tuple[np.ndarray, np.ndarray] | None = None
        self._smooth_cells = np.zeros((rows, cols))
        self._smooth_flow = np.zeros((rows, cols))
        self._smooth_left = self._smooth_right = 0.0

    def _spread(self, values: np.ndarray, ranges: np.ndarray, azimuth_deg: np.ndarray, hit: np.ndarray) -> np.ndarray:
        """빛살마다의 값 → 칸마다 (가로각 안 빛살들의 평균, 맞은 높이가 칸을 덮는 비율을 곱해서)."""
        cfg = self.cfg
        in_cell = (azimuth_deg >= self._az_low) & (azimuth_deg < self._az_high)          # (rows, cols, 빛살)
        distance = np.maximum(ranges, _EPS)
        floor = np.degrees(np.arctan2(-cfg.camera_height, distance))                     # 맞은 것의 바닥
        top = np.degrees(np.arctan2(cfg.range_object_top_m - cfg.camera_height, distance))  # 맞은 것의 위 끝
        cover = (np.minimum(top, self._el_top) - np.maximum(floor, self._el_bottom)) / np.maximum(self._el_top - self._el_bottom, _EPS)
        weight = in_cell * np.clip(cover, 0.0, 1.0) * hit
        return (weight * values).sum(axis=-1) / np.maximum(in_cell.sum(axis=-1), 1)

    def update(self, ranges: np.ndarray, offsets: np.ndarray, dt: float, turn_deg: float = 0.0,
               speed: float = 0.0) -> LoomingInfo:
        """ranges: 빛살마다 거리(m, 맞은 것 없으면 range_max_m 이상), offsets: 몸 기준 각도(라디안, + 오른쪽, 오름차순).
        dt: 직전 측정과의 간격(초), turn_deg: 그 사이 돈 각도(+ 시계 방향), speed: 그 사이 앞으로 간 속도(m/s)."""
        cfg = self.cfg
        ranges = np.asarray(ranges, dtype=float)
        offsets = np.asarray(offsets, dtype=float)
        prev, self._prev = self._prev, (ranges, offsets)
        if prev is None or dt <= 0.0:
            self._smooth_cells[:] = 0.0
            self._smooth_flow[:] = 0.0
            self._smooth_left = self._smooth_right = 0.0
            return LoomingInfo(0.0, math.inf, 0.0, 0.0, 0.0, self._smooth_cells.copy(), self.cell_angles,
                               self._smooth_flow.copy())

        far = cfg.range_max_m
        hit = ranges < far
        # 지금 θ 방향은 몸이 turn만큼 시계 방향으로 돌기 전에는 θ + turn 방향이었다 → 그 방향의 직전 거리와 비교
        before = np.interp(offsets + math.radians(turn_deg), prev[1], prev[0], left=np.nan, right=np.nan)
        closing = (before - ranges) / dt
        valid = hit & (before < far) & (closing <= cfg.range_max_closing_mps)  # nan(시야 밖)은 비교가 거짓이라 빠진다
        rate = cfg.range_gain * np.where(valid, np.maximum(closing, 0.0) / np.maximum(ranges, _EPS), 0.0)

        near, outer = cfg.looming_course_inner_m, cfg.looming_course_outer_m
        if outer > 0.0:
            if cfg.brake_decel > 0.0:  # 카메라와 같다: 서기까지 옆에서 걸어 들어올 수 있는 만큼 넓게
                extra = cfg.looming_brake_cross_mps * max(speed, 0.0) / cfg.brake_decel
                near, outer = near + extra, outer + extra
            lateral = np.abs(ranges * np.sin(offsets))
            gate = np.clip((outer - lateral) / max(outer - near, _EPS), 0.0, 1.0)
        else:
            gate = np.ones_like(ranges)

        azimuth = np.degrees(offsets)
        measured = self._spread(rate, ranges, azimuth, hit)
        raw_cells = self._spread(rate * gate, ranges, azimuth, hit)
        self.last_measured = measured
        self.last_gate = np.where(measured > _EPS, raw_cells / np.maximum(measured, _EPS), 1.0)
        flow = cfg.range_flow_gain * np.degrees(max(speed, 0.0) * np.sin(offsets) / np.maximum(ranges, _EPS))
        raw_flow = self._spread(flow, ranges, azimuth, hit)

        tau = cfg.looming_smooth_tau
        alpha = 1.0 if tau <= 0.0 else -math.expm1(-dt / tau)
        self._smooth_cells += alpha * (raw_cells - self._smooth_cells)
        self._smooth_flow += alpha * (raw_flow - self._smooth_flow)
        raw_left = float(raw_cells[self._roi_left].max(initial=0.0))
        raw_right = float(raw_cells[self._roi_right].max(initial=0.0))
        self._smooth_left += alpha * (raw_left - self._smooth_left)
        self._smooth_right += alpha * (raw_right - self._smooth_right)
        left, right = self._smooth_left, self._smooth_right
        strength = max(left, right)
        ttc = 1.0 / strength if strength > _EPS else math.inf
        direction = (right - left) / (right + left) if (right + left) > _EPS else 0.0
        return LoomingInfo(strength, ttc, left, right, direction, self._smooth_cells.copy(), self.cell_angles,
                           self._smooth_flow.copy())
