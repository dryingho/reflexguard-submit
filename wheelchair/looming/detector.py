"""루밍 연산 (휠체어 PC): 카메라 영상 → 칸별 확장률(다가오는 정도) → 커넥톰 입력.

기획서의 ① 루밍 감지. 휠체어 PC가 새 영상마다 부른다 (wheelchair/pipeline.WheelchairPipeline.see — Webots 컨트롤러·
tools/goal_trials.py·wheelchair/run.py가 쓴다). 카메라 영상과 내 움직임(회전·전진 속도)만 받으므로 실제 카메라에도 그대로 쓴다 —
카메라별 값은 common/config.py의 카메라 항목에 있다.

    카메라 영상 → LoomingDetector.update() → LoomingInfo (칸별 확장률·좌우 루밍·옆 흐름)
               → at_intended_speed() · for_braking()   (속도를 줄였을 때 · 감속 한계 보정)
               → connectome.brain.MaleCNSBrain.step()  (② 커넥톰 → 위험도·회피 방향)
               → wheelchair.shared_control.ReflexModule (③ 공유 제어 → 감속·회피·정지)

루밍 계산 방식
    커넥톰 입력인 칸별 확장률은 기본(looming_method = "zoom")으로 이전 영상을 키워 보는 배율 찾기(_zoom_cells)로 화면 전체에서
    재고, 통로 거르기(_on_course)로 거른다. 아래 밝기 기울기 방식은 좌/우 값과 looming_method = "gradient"에 쓴다.

    물체가 똑바로 다가오면 그 모습이 화면에서 바깥으로 '퍼진다'(확장). 이 확장률 k는
    밝기 보존식  I_t + k·(∇I·(화소 - 확장중심)) = 0  의 최소제곱해로 구한다.
    (실제로는 화면 전체가 한쪽으로 흐르는 성분 t도 같이 맞춘다: I_t + k·(∇I·(화소-중심)) + t·∇I = 0.
    제자리 회전은 화면을 옆으로 흘려 한쪽 절반에서 확장처럼 보이기 때문이다 — _sideways_flow 참고)
    평면 벽에 속도 v로 다가가면 k ≈ v / 거리 = 1 / (충돌까지 남은 시간)이 되어, k로 바로 위험도를 잴 수 있다.
    (제자리 회전은 화면이 옆으로 흐르기만 해 확장이 ~0 → 회전만으로는 반사가 켜지지 않는다.)

    이 방식은 연속 두 프레임 사이 움직임이 1~2픽셀 이내일 때 맞다. 벽에 가까우면 한 스텝(32ms) 사이에 무늬가 수십 픽셀씩
    커지고, 촘촘한 잔무늬(Webots 벽 텍스처)끼리 엉뚱하게 짝지어져 확장을 크게 덜 잡는다 — 정작 위험한 순간에 루밍이 사라진다.
    그래서 재기 전에 밝기 영상을 looming_downsample배로 줄인다(칸 평균). 움직임이 몇 픽셀로 줄고 잔무늬도 사라진다.
    (k는 비율이라 해상도와 무관하다. 실제 Webots 영상에서 1/4로 줄이면 1칸 앞 2칸/초일 때 이론값의 0.09배 → 약 0.75배)

    단, 확장은 화면 '중앙 영역'(진행 방향으로 보이는 곳)에서만 잰다. 좁은 복도에서 바로 옆·위·아래로
    스쳐 지나가는 벽은 가장자리에서 크게 확장하지만 충돌 코스가 아니므로, 재는 영역에서 빼야
    정면의 진짜 장애물에만 반응한다. 그 중앙 영역을 좌·우로 나눠 각각의 확장률을 재서 위험 방향도 낸다.
    좌/우 값은 프레임마다 튀므로 지수 평활로 떨림을 줄인다.
    (커넥톰은 칸별 값을 읽고, 좌/우 값은 칸별 값이 없을 때만 쓴다.)
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np

from common.config import CONFIG, Config

_EPS = 1e-9
# RGB → 밝기(그레이) 변환 가중치 (사람 눈의 민감도. 색 순서는 RGB)
_RGB_TO_GRAY = np.array([0.299, 0.587, 0.114], dtype=np.float32)


@dataclass(frozen=True)
class LoomingInfo:
    """한 스텝의 루밍 정보. 확장률의 단위는 1/초이고, 양수일수록 빠르게 다가오는 중이다."""

    strength: float         # 더 위험한 쪽의 영상 확장률 k (= max(left, right)). 0이면 다가오지 않음
    time_to_contact: float  # 충돌까지 남은 시간(초) ≈ 1/strength. 다가오지 않으면 math.inf
    left: float             # 중앙 영역(looming_roi_*)의 왼쪽 절반 확장률(1/초, 음수는 0으로 자름)
    right: float            # 중앙 영역의 오른쪽 절반 확장률
    direction: float        # 위험이 쏠린 가로 방향 -1(왼쪽) ~ 0(정면) ~ +1(오른쪽). 회피 방향 결정용
    # 화면 전체를 looming_grid_rows × looming_grid_cols 칸으로 나눈 칸별 확장률(1/초, 음수는 0, 평활)과
    # 각 칸 가운데의 방향 [가로각(+오른쪽), 세로각(+위)](도, 카메라 기준). 커넥톰이 LPLC2마다 자기 수용장 안의 칸을 읽는다.
    # (좌/우 값은 가운데 영역에서 재지만, 칸은 화면 전체다 — 위쪽을 보는 LPLC2가 DNp01에 시냅스를 훨씬 많이 보내므로
    #  벽 윗부분이 위쪽 시야로 퍼지는 것을 버리면 커넥톰이 정면 벽에 거의 반응하지 못한다.)
    cells: np.ndarray | None = None        # (rows, cols)
    cell_angles: np.ndarray | None = None  # (rows, cols, 2)
    # 칸별 가로 흐름(도/초, +오른쪽으로 흐름, 평활). 회전으로 생긴 흐름은 되돌린 뒤라 남는 것은 앞으로 가며 생기는 흐름 —
    # 가까운 것일수록 빠르다. 커넥톰의 HS 세포(앞→뒤 흐름을 모으는 소엽판 세포)가 '어느 쪽 벽이 가까운지'를 읽는다.
    flow: np.ndarray | None = None         # (rows, cols)


def _shifted(image: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """영상을 (dx, dy) 화소만큼 민 것 — 지금 화소 (x, y)에 이전의 (x - dx, y - dy)를 쌍선형으로."""
    if abs(dx) < 0.05 and abs(dy) < 0.05:
        return image
    height, width = image.shape
    sx = np.clip(np.arange(width, dtype=np.float32) - dx, 0.0, width - 1.0)
    sy = np.clip(np.arange(height, dtype=np.float32) - dy, 0.0, height - 1.0)
    ix, iy = np.minimum(np.floor(sx).astype(np.int64), width - 2), np.minimum(np.floor(sy).astype(np.int64), height - 2)
    wx, wy = (sx - ix).astype(image.dtype)[None, :], (sy - iy).astype(image.dtype)[:, None]
    rows0, rows1 = image[iy], image[iy + 1]
    top = rows0[:, ix] * (1.0 - wx) + rows0[:, ix + 1] * wx
    bottom = rows1[:, ix] * (1.0 - wx) + rows1[:, ix + 1] * wx
    return top * (1.0 - wy) + bottom * wy


def _axis_rotation(axis: np.ndarray, angle: float) -> np.ndarray:
    """축(단위 벡터) 둘레로 angle(라디안, 오른손 방향)만큼 도는 3×3 회전 행렬 (로드리게스 공식)."""
    x, y, z = axis
    k = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + math.sin(angle) * k + (1.0 - math.cos(angle)) * (k @ k)


def _cell_sums(values: np.ndarray, row_edges: np.ndarray, col_edges: np.ndarray, axes: tuple[int, int] = (-2, -1),
               dtype: type = np.float64) -> np.ndarray:
    """칸마다 합 (기본 float64). values의 axes 두 축이 (세로, 가로)이고(기본은 마지막 두 축), 다른 축(배율 후보·밀기 후보)은
    그대로 둔 채 한 번에 합한다.

    구간 합(np.add.reduceat)으로 줄마다·열마다 한 번씩만 지나간다 — 후보마다 적분 영상을 만들던 것보다 훨씬 빠르다.
    칸 경계가 고르지 않아도 되고, 화소보다 칸이 많아 빈 칸(시작 = 끝)이 생기면 그 칸은 0이다."""
    row_axis, col_axis = (axis % values.ndim for axis in axes)
    height, width = values.shape[row_axis], values.shape[col_axis]
    r0, c0 = row_edges[:-1], col_edges[:-1]
    if r0.size == 0 or c0.size == 0:
        shape = list(values.shape)
        shape[row_axis], shape[col_axis] = r0.size, c0.size
        return np.zeros(shape, dtype)
    # 빈 칸의 시작이 맨 끝(height·width)이면 reduceat이 받지 못한다 — 그 자리는 마지막 화소로 두고 아래에서 0으로 지운다
    sums = np.add.reduceat(values, np.minimum(r0, height - 1), axis=row_axis, dtype=dtype)
    sums = np.add.reduceat(sums, np.minimum(c0, width - 1), axis=col_axis)
    empty_rows, empty_cols = row_edges[1:] <= r0, col_edges[1:] <= c0
    if empty_rows.any():
        sums[(slice(None),) * row_axis + (empty_rows,)] = 0.0
    if empty_cols.any():
        sums[(slice(None),) * col_axis + (empty_cols,)] = 0.0
    return sums


class LoomingDetector:
    """연속한 카메라 프레임에서 영상 확장률(루밍)을 추정한다.

    상태: 직전 프레임의 밝기 영상. update()를 부를 때마다 갱신한다.
    첫 프레임이거나 dt가 0 이하면 비교할 직전 프레임이 없으므로 '다가오지 않음'을 반환한다.
    """

    def __init__(self, cfg: Config = CONFIG) -> None:
        self.cfg = cfg
        self._prev_gray: np.ndarray | None = None
        self._shape: tuple[int, int] | None = None
        # 확장 중심에서 각 화소까지의 변위(가로 rx, 세로 ry), 픽셀 단위.
        # np.gradient가 '픽셀당' 기울기를 주므로 변위도 픽셀로 맞춰야 k가 실제 확장률(1/초)이 된다.
        self._rx: np.ndarray | None = None  # shape (1, W)
        self._ry: np.ndarray | None = None  # shape (H, 1)
        self._roi: tuple[int, int, int, int, int] | None = None  # (y0, y1, x0, x1, 중앙영역 가로 중점)
        self._smooth_left = 0.0   # 지수 평활한 좌/우 확장률 (프레임 간 떨림 억제)
        self._smooth_right = 0.0
        self._cell_edges: tuple[np.ndarray, np.ndarray] | None = None  # 화면 전체를 나눈 칸 경계 (행, 열)
        self._cell_angles: np.ndarray | None = None
        self._smooth_cells: np.ndarray | None = None
        self._smooth_flow: np.ndarray | None = None
        self._deg_per_px: np.ndarray | None = None  # 칸 가운데 열에서 한 픽셀이 몇 도인지 (cols,)
        self._pixel_az: np.ndarray | None = None    # 화소마다 몸 기준 가로각·위아래각 (라디안, (H, W))
        self._pixel_el: np.ndarray | None = None
        self._valid: np.ndarray | None = None       # 몸이 보이지 않는 화소 1, 보이는 화소 0
        self.last_measured: np.ndarray | None = None  # 마지막 영상의 칸별 확장률(통로로 거르기 전, 평활 전)
        self.last_gate: np.ndarray | None = None      # 마지막 영상의 칸별 통로 가중치 0~1 (_on_course)
        self.last_shift = (0.0, 0.0)                  # 마지막 영상에서 되돌린 흔들림 (가로, 세로 화소) — _stabilize
        self.last_roll = 0.0                          # 마지막 영상에서 잰 기울기(도, +시계 방향) — _stabilize
        self._recent: list[np.ndarray] = []           # 최근 영상들의 칸별 루밍 (거른 뒤) — _persist
        # 카메라 자기 보정(calibration.py)이 보는 값: 돌려 놓기 전의 직전 영상과 지금 영상, 바닥 몫을 빼기 전 칸별 확장률(NaN = 못 잼)
        self.last_pair: tuple[np.ndarray, np.ndarray] | None = None
        self.last_zoom_raw: np.ndarray | None = None
        self.last_local = np.zeros((1, 1), dtype=bool)  # 지난 영상에서 제자리 확대(looming_local_center)로 잰 칸
        self._zoom_local_gather = None
        self._local_age = np.zeros((1, 1))  # 칸마다 제자리 확대가 이어진 시간(초)
        self.last_moving = np.zeros((1, 1), dtype=bool)  # 지난 영상에서 움직이는 물체 후보(looming_moving)로 잰 칸
        self.last_shrink_veto = np.zeros((1, 1), dtype=bool)  # 지난 영상에서 멀어지는 물체 둘레라 버린 '다가옴' 칸
        self._ground_line: tuple[float, float] | None = None  # 잰 바닥 직선 (1 m/s당 기울기, 수평선 v) — set_ground_line
        self.last_cell_shift = np.zeros((1, 1))           # 지난 영상의 칸별 가로 흐름(줄인 영상 화소, +오른쪽) — _cell_shifts
        self.last_saccade = False     # 지난 영상은 카메라를 휙 돌리는 중이라 루밍을 재지 않음 — _saccade
        self._saccade_left = 0.0      # 휙 돌기가 끝난 뒤에도 이만큼(초) 더 재지 않는다
        self._jump_s = 0.0            # 화면 대부분이 튀어 이어서 버린 시간(초) — _jumped
        self._moving_age = np.zeros((1, 1))

    def _ensure_fields(self, shape: tuple[int, int]) -> None:
        """프레임 크기에 맞는 확장장·중앙 ROI를 한 번만 만든다."""
        if self._shape == shape:
            return
        height, width = shape
        focal = (width / 2.0) / math.tan(math.radians(self.cfg.fov_deg) / 2.0)  # 픽셀 단위 초점거리 (줄인 영상 기준)
        # 카메라는 camera_pitch_deg만큼 숙여 있다 → 똑바로 갈 때 퍼져 나가는 중심(수평선 위의 진행 방향)은 화면 가운데보다 위
        focus_x = self.cfg.looming_focus_x * width
        focus_y = self.cfg.looming_focus_y * height - focal * math.tan(math.radians(self.cfg.camera_pitch_deg))
        cols = np.arange(width, dtype=np.float32) + 0.5
        rows = np.arange(height, dtype=np.float32) + 0.5
        self._rx = (cols - focus_x)[None, :]   # (1, W), 픽셀 단위
        self._ry = (rows - focus_y)[:, None]   # (H, 1), 픽셀 단위
        # 루밍을 재는 중앙 영역(진행 방향). 양옆·위아래로 스쳐 가는 벽을 재지 않으려고 가장자리를 뺀다.
        # 가운데 영역은 각도로 정한다 (시야각을 바꿔도 같은 범위를 잰다). 진행 방향(수평선)을 가운데에 둔다
        roi_w = min(width, max(2, 2 * int(round(focal * math.tan(math.radians(self.cfg.looming_roi_half_width_deg))))))
        roi_h = min(height, max(1, 2 * int(round(focal * math.tan(math.radians(self.cfg.looming_roi_half_height_deg))))))
        x0 = (width - roi_w) // 2
        y0 = min(max(int(round(focus_y - roi_h / 2.0)), 0), height - roi_h)
        self._roi = (y0, y0 + roi_h, x0, x0 + roi_w, roi_w // 2)
        # 화면 전체를 칸으로 나눈다. 칸 가운데가 몸 정면(수평)에서 몇 도 오른쪽·위쪽인지도 구해 둔다 (숙인 핀홀 카메라).
        # 커넥톰이 LPLC2·HS마다 자기 수용장(몸 기준 각도)의 칸을 읽으므로, 숙인 만큼 위아래 각을 바로잡아야 한다
        rows_n, cols_n = max(1, self.cfg.looming_grid_rows), max(1, self.cfg.looming_grid_cols)
        row_edges = np.linspace(0, height, rows_n + 1).round().astype(int)
        col_edges = np.linspace(0, width, cols_n + 1).round().astype(int)
        self._cell_edges = (row_edges, col_edges)
        cy = (row_edges[:-1] + row_edges[1:]) / 2.0
        cx = (col_edges[:-1] + col_edges[1:]) / 2.0
        horizontal, vertical = self._body_angles(cx[None, :], cy[:, None], width, height, focal)
        self._cell_angles = np.stack(np.broadcast_arrays(np.degrees(horizontal), np.degrees(vertical)), axis=-1)
        self._cell_front = ((np.abs(self._cell_angles[..., 0]) <= self.cfg.looming_cell_max_azimuth_deg)
                            & (self._cell_angles[..., 1] >= self.cfg.looming_cell_min_elevation_deg)
                            & (self._cell_angles[..., 1] <= self.cfg.looming_cell_max_elevation_deg)).astype(float)
        # 화소마다 몸 기준 방향 (회전 되돌리기에 쓴다)
        self._pixel_az, self._pixel_el = self._body_angles(cols[None, :], rows[:, None], width, height, focal)
        # 평평한 바닥이 1 m/s로 다가올 때 화소마다 보이는 확장률(1/초) — 수평선 아래만. 바닥은 앞으로 가면 늘 퍼져 나가므로
        # (무늬 있는 바닥에선 가까울수록 크게) 이만큼은 '다가오는 것'이 아니다. 숙인 핀홀 카메라: 화소 광선 d(광축 성분 1)의
        # 위 성분 d_z가 음수면 바닥 (-h/d_z 배 거리)에 닿고, 광축 방향 이동 속도 v·cos(pitch)로 k = v·cos(pitch)·(-d_z)/h
        pitch = math.radians(self.cfg.camera_pitch_deg)
        v_up = -(rows - height / 2.0) / focal
        down = np.maximum(math.sin(pitch) - v_up * math.cos(pitch), 0.0)  # = -d_z
        self._ground_rate = (math.cos(pitch) * down / max(self.cfg.camera_height, _EPS)).astype(np.float32)[:, None]
        self._ground_v = v_up.astype(np.float32)  # 줄마다 세로 위치 ÷ 초점거리 (+위) — 잰 바닥 직선(set_ground_line)을 펼 때
        if self._ground_line is not None:
            self._apply_ground_line()
        self._smooth_cells = np.zeros((rows_n, cols_n))
        self.floor = np.zeros((rows_n, cols_n))  # 칸별 잡음 바닥(1/초) — _habituate
        self._smooth_flow = np.zeros((rows_n, cols_n))
        offset = cx - width / 2.0
        self._deg_per_px = np.degrees(focal / (focal ** 2 + offset ** 2))  # d(atan(x/f))/dx
        self._shape = shape
        self._valid = None  # 몸이 보이지 않는 화소(1)·보이는 화소(0) — 원래 영상 크기를 알아야 해서 update()에서 만든다

    def cell_bounds(self, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """이 카메라(줄인 영상 크기 shape)의 칸 배치를 영상 없이: 칸 가운데 방향 (rows, cols, 2), 칸마다 가로각 경계
        (rows, cols + 1 — 그 줄 가운데 높이에서), 위아래각 경계 (rows + 1, cols — 그 열 가운데에서, 위 줄부터). 모두 도, 몸 기준.
        거리 센서 입력(wheelchair/range_sensor.py)을 카메라와 같은 칸에 펼칠 때 쓴다."""
        self._ensure_fields(shape)
        height, width = shape
        focal = (width / 2.0) / math.tan(math.radians(self.cfg.fov_deg) / 2.0)
        row_edges, col_edges = self._cell_edges
        cy = (row_edges[:-1] + row_edges[1:]) / 2.0
        cx = (col_edges[:-1] + col_edges[1:]) / 2.0
        azimuth, _ = self._body_angles(col_edges[None, :].astype(float), cy[:, None], width, height, focal)
        _, elevation = self._body_angles(cx[None, :], row_edges[:, None].astype(float), width, height, focal)
        return self._cell_angles, np.degrees(azimuth), np.degrees(elevation)

    @property
    def cell_front(self) -> np.ndarray | None:
        """재는 칸 1, 재지 않는 칸 0 (가로각·위아래각 범위 밖 — looming_cell_*). 첫 영상 전에는 None."""
        return self._cell_front if self._shape is not None else None

    def _camera_axes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """몸 좌표(x 앞, y 왼쪽, z 위)로 본 카메라의 앞·왼쪽·위 방향 (camera_pitch_deg만큼 숙임)."""
        pitch = math.radians(self.cfg.camera_pitch_deg)
        return (np.array([math.cos(pitch), 0.0, -math.sin(pitch)]), np.array([0.0, 1.0, 0.0]),
                np.array([math.sin(pitch), 0.0, math.cos(pitch)]))

    def _body_angles(self, x: np.ndarray, y: np.ndarray, width: int, height: int,
                     focal: float) -> tuple[np.ndarray, np.ndarray]:
        """화면 위치(픽셀) → 몸 기준 (가로각 +오른쪽, 위아래각 +위), 라디안."""
        forward, left, up = self._camera_axes()
        u = (x - width / 2.0) / focal                      # 오른쪽 +
        v = -(y - height / 2.0) / focal                    # 위 +
        d = forward + (-u)[..., None] * left + v[..., None] * up
        return np.arctan2(-d[..., 1], d[..., 0]), np.arctan2(d[..., 2], np.hypot(d[..., 0], d[..., 1]))

    def _ensure_mask(self, frame_shape: tuple[int, int], shape: tuple[int, int]) -> None:
        """몸(무릎·팔걸이)이 보이는 화소를 뺄 가중치 (줄인 영상 크기, 1 = 쓴다). 몸은 카메라에 붙어 있어 늘 같은 자리다.

        줄인 칸에 몸 화소가 하나라도 있으면 빼고, 경계의 밝기 기울기가 몸에 걸치지 않게 한 칸 더 넓힌다.
        """
        if self._valid is not None and self._valid.shape == shape:
            return
        frame_h, frame_w = frame_shape
        cfg = self.cfg
        if cfg.body_mask_file == "none":  # 몸이 보이지 않는 카메라 (또는 몸 마스크를 아직 안 만든 실제 영상)
            mask = np.zeros((frame_h, frame_w), dtype=bool)
        elif cfg.body_mask_file:  # 실제 카메라: 찍어서 만든 몸 마스크 (H×W, 참 = 몸)
            mask = np.load(cfg.body_mask_file).astype(bool)
            if mask.shape != (frame_h, frame_w):
                raise ValueError(f"body mask {mask.shape} does not match camera frame {(frame_h, frame_w)}")
        else:
            from wheelchair.body import body_overlay  # 몸 모양(wheelchair/body.py) — 카메라 영상 크기에 맞춰 그린다
            if (cfg.render_width, cfg.render_height) != (frame_w, frame_h):
                cfg = dataclasses.replace(cfg, render_width=frame_w, render_height=frame_h)
            mask = body_overlay(cfg)[1]
        height, width = shape
        n_y, n_x = frame_h // height, frame_w // width
        body = mask[:height * n_y, :width * n_x].reshape(height, n_y, width, n_x).any(axis=(1, 3))
        grown = body.copy()
        grown[1:] |= body[:-1]
        grown[:-1] |= body[1:]
        grown[:, 1:] |= grown[:, :-1].copy()
        grown[:, :-1] |= grown[:, 1:].copy()
        self._valid = (~grown).astype(np.float32)

    @staticmethod
    def _sideways_flow(it: np.ndarray, r: np.ndarray, grad_x: np.ndarray, grad_y: np.ndarray) -> tuple[float, float] | None:
        """화면 전체가 한쪽으로 흐르는 성분 (t_x, t_y)(픽셀/초, 제자리 회전 등). 무늬가 거의 없으면 None.
        I_t' = I_t + t_x·∂I/∂x + t_y·∂I/∂y 가 그 흐름을 뺀 밝기 변화다.

        중앙 영역 전체에서  I_t + k·r + t_x·∂I/∂x + t_y·∂I/∂y = 0  을 최소제곱으로 맞춰 전체 흐름 (t_x, t_y)를 구하고 뺀다.
        좌·우 무늬를 다 쓰므로 안정적이다. 다가오는 벽은 왼쪽은 왼쪽으로, 오른쪽은 오른쪽으로 퍼지므로 t로 설명되지 않는다.
        흐름을 빼지 않으면 돌 때 화면이 옆으로 흐르는 것이 한쪽 절반에서 '퍼지는 것'으로 잡혀,
        회피하려고 돌수록 가짜 루밍이 커지는 악순환이 생긴다.
        """
        columns = np.stack([r.ravel(), grad_x.ravel(), grad_y.ravel()], axis=1).astype(np.float64)
        normal = columns.T @ columns
        scale = float(np.trace(normal)) / 3.0
        if scale <= _EPS:
            return None  # 무늬가 거의 없으면(벽에 바짝 붙는 등) 잴 수 없다
        k_and_shift = np.linalg.solve(normal + np.eye(3) * (scale * 1e-6), -(columns.T @ it.ravel().astype(np.float64)))
        return float(k_and_shift[1]), float(k_and_shift[2])

    @staticmethod
    def _damped_rate(it: np.ndarray, r: np.ndarray, damping: float) -> float:
        """확장률 k = -Σ(I_t'·r) / (Σr² + λ).

        λ는 무늬가 거의 없는 곳의 값을 0 쪽으로 눌러 둔다 — 그런 곳은 몇 줄 안 되는 기울기로 재게 되어
        작은 오차가 크게 부풀기 때문이다. 무늬가 충분한 곳에는 거의 영향이 없다(약 4% 작아짐).
        """
        return -float(np.sum(it * r)) / (float(np.sum(r * r)) + damping)

    def _undo_rotation(self, prev: np.ndarray, turn_deg: float, nod_deg: float = 0.0, roll_deg: float = 0.0) -> np.ndarray:
        """이전 영상을, 그 뒤 로봇이 turn_deg(도, +시계 방향=우회전)만큼 돈 만큼 돌려 놓는다 (카메라 원근 그대로).
        nod_deg(+카메라가 더 아래로)·roll_deg(+뒤에서 볼 때 시계 방향 = 오른쪽이 내려감)도 함께 — 들고 걷는 휴대폰의 자이로,
        또는 영상으로 잰 흔들림(_stabilize).

        제자리 회전은 화면 전체를 옆으로 민다. 빨리 돌면 한 프레임에 십여 픽셀씩 밀려 밝기 기울기로는 잴 수 없고,
        좌·우 절반의 한쪽에서 '퍼지는 것'으로 잘못 잡힌다. 로봇이 얼마나 돌았는지는 직전 명령(실제 휠체어라면 바퀴·자이로)으로
        알 수 있으므로, 그만큼 이전 영상을 돌려 놓고 비교한다.
        핀홀 카메라에서 가로 각도 a인 열은 이전 화면의 각도 a+θ인 열에 해당하고, 세로 위치도 cos(a)/cos(a+θ)배가 된다
        (화면 가장자리일수록 작게 보이므로, 돌면 한쪽은 세로로 커지고 반대쪽은 작아진다 — 이것도 확장으로 착각하기 쉽다).
        """
        theta = math.radians(turn_deg)
        if abs(theta) < 1e-9 and abs(nod_deg) < 1e-9 and abs(roll_deg) < 1e-9:
            return prev
        height, width = prev.shape
        self._ensure_fields(prev.shape)
        focal = (width / 2.0) / math.tan(math.radians(self.cfg.fov_deg) / 2.0)  # 픽셀 단위 초점거리 (줄인 영상 기준)
        # 지금 화소의 몸 기준 방향(가로각 a, 위아래각 e)은 돌기 전에는 가로각 a+θ였다 → 그 방향을 숙인 카메라에 다시 투영한다.
        # (숙이지 않았으면 열은 tan(a+θ)로, 세로는 cos(a)/cos(a+θ)배로 옮겨지는 것과 같다)
        forward, left, up = self._camera_axes()
        # 숙임은 카메라 왼쪽 축, 기울기는 카메라 앞 축 둘레로 몸(카메라 받침)이 돈 것. 이전 영상에서의 방향 = 몸이 돈 회전 × 지금
        # 방향 (좌우 회전 θ는 몸 위 축 둘레로 -θ — 가로각을 θ만큼 더하는 것과 같다). 화소 방향(_pixel_dirs)은 영상 크기마다 한 번만
        # 만들고, 회전은 세 축(앞·왼쪽·위)에 미리 걸어 화소마다 내적 세 번으로 끝낸다
        spin = (_axis_rotation(np.array([0.0, 0.0, 1.0]), -theta) @ _axis_rotation(left, math.radians(nod_deg))
                @ _axis_rotation(forward, math.radians(roll_deg)))
        dirs = self._pixel_dirs()
        depth = np.maximum(dirs @ (spin.T @ forward), 1e-6)
        source_x = width / 2.0 - focal * (dirs @ (spin.T @ left)) / depth - 0.5   # 이전 영상에서 가져올 열 (H, W)
        source_y = height / 2.0 - focal * (dirs @ (spin.T @ up)) / depth - 0.5    # 이전 영상에서 가져올 행 (H, W)
        sx = np.clip(source_x, 0.0, width - 1.0)
        sy = np.clip(source_y, 0.0, height - 1.0)
        x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
        x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
        wx, wy = (sx - x0).astype(prev.dtype), (sy - y0).astype(prev.dtype)
        top = prev[y0, x0] * (1.0 - wx) + prev[y0, x1] * wx
        bottom = prev[y1, x0] * (1.0 - wx) + prev[y1, x1] * wx
        return top * (1.0 - wy) + bottom * wy

    def _pixel_dirs(self) -> np.ndarray:
        """화소마다 몸 기준 방향의 단위 벡터 (H, W, 3: 앞·왼쪽·위) — 화소 각도(_pixel_az·_pixel_el)가 바뀔 때만 다시 만든다."""
        el, az = self._pixel_el, self._pixel_az
        cached = getattr(self, "_dirs_for", None)
        if cached is None or cached[0] is not el or cached[1] is not az:
            self._dirs = np.stack(np.broadcast_arrays(np.cos(el) * np.cos(az), -np.cos(el) * np.sin(az), np.sin(el)), axis=-1)
            self._dirs_for = (el, az)
        return self._dirs

    def _cell_shifts(self, gray: np.ndarray, prev: np.ndarray, weight: np.ndarray | None = None) -> np.ndarray:
        """칸마다 이전 영상에서 지금 영상까지 가로로 몇 픽셀 흘렀는지 (+오른쪽). 무늬가 없어 알 수 없는 칸은 0.

        이전 영상을 -S~+S 픽셀씩 옆으로 밀어 보며 칸마다 밝기 차이(평균 절댓값)가 가장 작은 밀기를 고르고, 이웃 값으로
        포물선을 맞춰 소수점까지 잰다(블록 맞추기). 밝기 기울기 방식은 한 프레임에 몇 픽셀 이상 움직이면 크게 덜 잡는데,
        벽에 붙어 달리면 화면 가장자리가 한 프레임에 열 픽셀 넘게 흐르기 때문이다.
        weight: 쓸 화소(1)·뺄 화소(0, 내 몸). 지금 영상과 민 이전 영상 둘 다 쓸 수 있는 화소만 평균한다.
        """
        limit = max(1, int(self.cfg.hs_max_shift_px))
        height, width = gray.shape
        row_edges, col_edges = self._cell_edges
        padded = np.pad(prev, ((0, 0), (limit, limit)), mode="edge")
        if weight is None:
            weight = np.ones_like(gray)
        padded_weight = np.pad(weight, ((0, 0), (limit, limit)), mode="constant")
        # 밀기 후보 전부를 한 번에: 지금 영상의 x 열 ≈ 이전 영상의 x - shift 열 (오른쪽으로 shift만큼 흘렀다면).
        # 창(window) i는 x + i 열부터 — shift = limit - i
        windows = np.lib.stride_tricks.sliding_window_view(padded, width, axis=1)[:, ::-1]           # (H, 2L+1, W)
        weight_windows = np.lib.stride_tricks.sliding_window_view(padded_weight, width, axis=1)[:, ::-1]
        diff = np.abs(gray[:, None, :] - windows)                                                   # (H, 2L+1, W)
        if weight.min() >= 1.0:
            # 몸 가리개가 없다(휴대폰): 쓸 수 있는 화소는 민 이전 영상이 영상 안에 있는 열뿐이고 줄마다 같다 — 화소마다 곱하지
            # 않고 열 마스크만 곱하고, 쓸 수 있는 수는 (칸 높이 × 열 수)로 셈한다
            columns = weight_windows[0]                                                              # (2L+1, W)
            diff *= columns[None]
            usable = (np.diff(row_edges)[None, :, None]
                      * _cell_sums(columns[:, None, :], np.array([0, 1]), col_edges)[:, 0][:, None, :])  # (2L+1, 칸 줄, 칸 열)
        else:
            both = weight[:, None, :] * weight_windows
            diff *= both
            usable = _cell_sums(both.transpose(1, 0, 2), row_edges, col_edges)
        # 칸별 합은 (줄, 밀기, 열) 그대로 줄·열을 묶고 끝에 축만 바꾼다 (밀기 축을 앞으로 옮긴 사본을 만들지 않는다). 밝기 차이의
        # 합(칸 하나에 많아야 수십만)은 float32로도 충분하다
        costs = _cell_sums(diff, row_edges, col_edges, axes=(0, 2), dtype=np.float32).transpose(1, 0, 2) / np.maximum(usable, 1.0)
        best = np.argmin(costs, axis=0)
        rows, cols = np.indices(best.shape)
        c0 = costs[np.clip(best - 1, 0, 2 * limit), rows, cols]
        c1 = costs[best, rows, cols]
        c2 = costs[np.clip(best + 1, 0, 2 * limit), rows, cols]
        curvature = c0 - 2.0 * c1 + c2
        refine = np.where(curvature > _EPS, 0.5 * (c0 - c2) / np.maximum(curvature, _EPS), 0.0)
        shifts = best - limit + np.clip(refine, -0.5, 0.5)
        # 믿을 만한 칸만: 가장 좋은 밀기가 중간값보다 뚜렷이(hs_match_contrast) 좋아야 한다 (무늬 없는 하늘·벽면은 어디로 밀어도 같다)
        typical = np.median(costs, axis=0)
        confident = (typical - c1) > self.cfg.hs_match_contrast * np.maximum(typical, _EPS)
        # 찾는 범위 끝에 걸린 답은 믿지 않는다: 무늬가 가로로만 있는 칸(천장·바닥 띠)은 어디로 밀어도 비슷해서
        # 끝값으로 쏠리기 쉽다 (돌 때 ±16픽셀 = 300도/초짜리 가짜 흐름이 나왔다)
        inside = (best > 0) & (best < 2 * limit)
        enough = usable.min(axis=0) >= 0.5 * np.diff(row_edges)[:, None] * np.diff(col_edges)[None, :]  # 반 넘게 몸이면 안 잰다
        return np.where(confident & inside & enough, shifts, 0.0)

    def _zoom_cells(self, gray: np.ndarray, prev: np.ndarray, weight: np.ndarray, dt: float, speed: float,
                    shifts: np.ndarray | None = None) -> np.ndarray:
        """칸마다 확장률 k(1/초)를 '이전 영상을 얼마나 키우면 지금 영상과 가장 잘 맞나'로 잰다 (looming_method = "zoom").

        확장 중심(진행 방향)을 기준으로 이전 영상을 배율 s = exp(k·dt)로 키운 후보들을 만들고, 칸마다 밝기를 정규화한
        상관(NCC: 평균을 빼고 표준편차로 나눈 뒤의 일치도)이 가장 높은 k를 고른 뒤 이웃 후보로 포물선을 맞춰 소수점까지 잰다.
        - 한 프레임에 무늬가 많이 움직여도(가까운 물체, 긴 프레임 간격) 덜 잡지 않는다 — 밝기 기울기 방식의 약점
        - 밝기·대비가 바뀌어도(조명, 카메라 노출) 같은 값이 나온다 — 정규화한 상관이라서
        그래서 카메라·프레임 간격이 달라도 같은 이득을 쓸 수 있다.
        무늬가 거의 없는 칸, 가장 잘 맞는 후보가 다른 후보보다 뚜렷이 낫지 않은 칸은 0. 평평한 바닥이 퍼져 보이는 몫
        (looming_ground_comp × 속도 × 바닥 확장률)은 빼고, 음수는 0으로 자른다. 커넥톰에 넘기는 칸(_cell_front)만 잰다.
        """
        plan = self._zoom_plan(gray.shape, dt)
        (top, bottom, left, right), rates, samples, _, cell_front, cell_rows, cell_cols = plan
        rate_out = np.zeros(self._smooth_cells.shape)
        if not samples:
            return rate_out
        # 걸을 때는 줄어드는 쪽 후보가 뚜렷이(looming_zoom_shrink_margin) 더 잘 맞아야 고른다: 다가오는 물체 가장자리(가려지는 배경)는
        # 어떤 후보와도 잘 맞지 않는데, 줄어드는 쪽을 그냥 고르게 했더니 그런 칸이 0이 되어 사람에게 걸어갈 때 닿기 3.2 → 0.5초
        # 전으로 늦게 섰다. 거의 서 있을 때(0.2 m/s 이하)는 더 잘 맞기만 하면 — 멀어지는 판이 비켜 있으면 판 안쪽 칸도 덜 또렷하다
        shrink_margin = self.cfg.looming_zoom_shrink_margin if speed > 0.2 else 0.0
        a = gray[top:bottom, left:right].astype(np.float32)
        # 몸 화소와, 가장 크게 키웠을 때 몸이 옮겨 가는 화소는 모든 후보에서 뺀다 (후보마다 같은 화소로 비교)
        w = (weight[top:bottom, left:right] * samples[0]).astype(np.float32)
        r_edges, c_edges = cell_rows, cell_cols

        aw = a * w
        n = np.maximum(_cell_sums(w, r_edges, c_edges), 1.0)
        sa, saa = _cell_sums(np.stack([aw, aw * a]), r_edges, c_edges)
        var_a = np.maximum(saa - sa * sa / n, 0.0)
        # 배율 후보마다 키운 이전 영상 (쌍선형 보간 — 줄·열 이웃의 위치·가중치는 _zoom_plan이 미리 만들어 둔다)을
        # 한꺼번에 쌓아 칸별 합을 한 번에 구한다
        def costs(gather: tuple, pick: np.ndarray) -> np.ndarray:
            """고른 배율 후보(pick)마다 칸별 비용 1 - NCC (후보, 칸 줄, 칸 열)."""
            y0, y1, wy, x0, x1, wx = (part[pick] for part in gather)
            rows = prev[y0] + (prev[y1] - prev[y0]) * wy[:, :, None]       # 줄 보간 (후보, 세로, 영상 가로 전체)
            left_, right_ = np.take_along_axis(rows, x0[:, None, :], axis=2), np.take_along_axis(rows, x1[:, None, :], axis=2)
            b = left_ + (right_ - left_) * wx[:, None, :]                   # 열 보간 (후보, 세로, 가로)
            bw = b * w
            sb = _cell_sums(bw, r_edges, c_edges)
            sbb = _cell_sums(bw * b, r_edges, c_edges)
            sab = _cell_sums(aw * b, r_edges, c_edges)
            var_b = np.maximum(sbb - sb * sb / n, 0.0)
            return 1.0 - (sab - sa * sb / n) / np.sqrt(np.maximum(var_a * var_b, _EPS))

        area = np.diff(r_edges)[:, None] * np.diff(c_edges)[None, :]
        # 무늬가 있어야(칸 안 밝기 표준편차가 looming_zoom_min_std 이상, 반 넘게 쓸 수 있는 화소) 잰다
        textured = (var_a / n >= self.cfg.looming_zoom_min_std ** 2) & (n >= 0.5 * area)

        def fit(*gather: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
            """배율 후보들 중 칸마다 가장 잘 맞는 확장률·그 비용·후보 비용의 중간값·가장 큰 후보에 걸리지 않았는지.
            looming_zoom_coarse면 한 칸 걸러 거칠게 맞춘 뒤, 무늬 있는 칸에서 가장 잘 맞은 후보의 바로 옆 후보만 더 맞춘다 —
            그 칸들은 포물선 맞추기에 쓰는 이웃이 모두 채워진다(무늬 없는 칸은 어차피 믿지 않는다). 중간값은 거친 후보들로."""
            count, first = len(rates), self._zoom_regular
            if not self.cfg.looming_zoom_coarse or count - first < 5:
                table = costs(gather, np.arange(count))
                return self._pick_shrink(table, rates, np.median(table[first:], axis=0), shrink_margin)
            coarse = np.unique(np.concatenate([np.arange(first), np.arange(first, count, 2), [count - 1]]))
            table = np.full((count,) + n.shape, 4.0)  # 맞추지 않은 후보 (비용 1 - NCC는 0~2 — 그보다 나쁘게)
            table[coarse] = costs(gather, coarse)
            # 줄어드는 쪽 후보는 모두 거친 후보에 들어 있다 → 옆을 더 맞출 것은 간격 후보 중 가장 잘 맞은 것의 옆
            spaced = coarse[coarse >= first]
            best = spaced[np.argmin(table[spaced], axis=0)]
            near = np.unique(np.concatenate([best[textured] - 1, best[textured] + 1]))
            near = near[(near >= 0) & (near < count) & ~np.isin(near, coarse)]
            if near.size:
                table[near] = costs(gather, near)
            return self._pick_shrink(table, rates, np.median(table[spaced], axis=0), shrink_margin)

        rate, c1, typical, inside = fit(*self._zoom_gather)
        first = self._zoom_regular
        # 가장 잘 맞는 후보가 후보들의 중간값보다 looming_zoom_contrast만큼 나아야 믿는다 (고른 무늬·하늘은 어떤 배율도 비슷하다).
        # 무늬 조건(textured)은 위에서
        confident = (typical - c1) > self.cfg.looming_zoom_contrast * np.maximum(typical, _EPS)
        trusted = textured & confident
        if self.cfg.looming_zoom_reject_edge:
            # 가장 큰 후보에 걸린 답은 믿지 않는다: 확장률 상한(0.2초 뒤 충돌)을 넘는 참 루밍은 그 전에 이미 커 있다. 걸으며 위아래로
            # 출렁이면 발치 바닥·화면 귀퉁이가 한 영상에 몇 화소씩 밀리는데, 귀퉁이에서는 '가장 크게 키운 후보'가 그 밀림과 가장
            # 비슷해 한 영상만 상한(5/초)으로 튀었다
            trusted = trusted & inside
        self.last_moving = np.zeros(self._smooth_cells.shape, dtype=bool)
        if self.cfg.looming_moving and shifts is not None:
            # 움직이는 물체 가설(looming_moving): 칸의 무늬가 잰 가로 흐름만큼 옮겨 가며 커진다 — 골목에서 나오는 차처럼 옆으로
            # 움직이는 것은 진행 방향 중심으로 키운 어떤 후보와도 맞지 않아 '믿을 수 없음'으로 버려졌다
            cell_shift = shifts[cell_front].reshape(rate.shape)
            focal = (prev.shape[1] / 2.0) / math.tan(math.radians(self.cfg.fov_deg) / 2.0)
            sliding = np.abs(cell_shift) / max(dt, _EPS) / focal >= self.cfg.looming_moving_min_rate
            # 진행 방향 후보로 이미 믿고 잰 칸은 이 후보가 더 크게 잴 때만 바꾼다 — 더 작은 값으로 바꾸면 옆에서 뛰어나온 아이
            # (Webots 도로)에 늦게 섰다. 통로 거르기도 옆 거리가 지금 옆 거리보다 커지지 않으니(_moving_lateral) 덜 거를 뿐이다
            candidates = textured & sliding
            moving_rate, moving_c1, moving_typical, moving_inside = self._moving_fit(a, w, prev, rates, dt, shifts, candidates,
                                                                                    shrink_margin)
            moving = (candidates & (moving_rate > 0.0) & (~trusted | (moving_rate > rate))
                      & (moving_inside | (not self.cfg.looming_zoom_reject_edge))
                      & ((moving_typical - moving_c1) > self.cfg.looming_zoom_contrast * np.maximum(moving_typical, _EPS))
                      & (moving_c1 < (1.0 - self.cfg.looming_moving_margin) * c1))
            seen = np.zeros(self._smooth_cells.shape, dtype=bool)
            seen[cell_front] = moving.ravel()
            self._moving_age = self._held(self._moving_age, seen, dt)
            moving = moving & (self._moving_age >= self.cfg.looming_moving_hold_s - 1e-6)[cell_front].reshape(moving.shape)
            rate = np.where(moving, moving_rate, rate)
            trusted = trusted | moving
            self.last_moving[cell_front] = moving.ravel()
        self.last_local = np.zeros(self._smooth_cells.shape, dtype=bool)
        if self._zoom_local_gather is not None:
            # 제자리 확대 가설(looming_local_center): 칸 가운데를 중심으로 키운 후보가 진행 방향 중심보다 뚜렷이 잘 맞으면
            # 그 칸의 물체는 화면에서 자리를 지키며 커진다 = 같은 방향에서 다가온다(충돌 경로) — 옆에서 걸어 들어오는 사람
            local_rate, local_c1, local_typical, local_inside = fit(*self._zoom_local_gather)
            local = (textured & (local_rate > 0.0) & (local_inside | (not self.cfg.looming_zoom_reject_edge))
                     & ((local_typical - local_c1) > self.cfg.looming_zoom_contrast * np.maximum(local_typical, _EPS))
                     & (local_c1 < (1.0 - self.cfg.looming_local_margin) * c1))
            # 잠깐 맞는 것(흔들림·잡음)은 쓰지 않는다: 그 칸이나 이웃 칸(물체가 옮겨 갈 수 있다)에서 looming_local_hold_s 넘게
            # 이어져야 한다 — 다가오는 물체는 계속 커진다
            seen = np.zeros(self._smooth_cells.shape, dtype=bool)
            seen[cell_front] = local.ravel()
            self._local_age = self._held(self._local_age, seen, dt)
            local = local & (self._local_age >= self.cfg.looming_local_hold_s - 1e-6)[cell_front].reshape(local.shape)
            rate = np.where(local, local_rate, rate)
            trusted = trusted | local
            self.last_local[cell_front] = local.ravel()
            self.last_moving &= ~self.last_local
        reach = int(self.cfg.looming_shrink_veto_cells)
        self.last_shrink_veto = np.zeros(self._smooth_cells.shape, dtype=bool)
        if reach > 0 and first > 0 and speed <= 0.2:
            # 멀어지는 물체(줄어드는 쪽 후보가 가장 잘 맞은 칸) 둘레의 '다가옴'은 버린다: 물체가 줄어들며 드러난 뒤 배경은 이전 영상에
            # 없어서 어떤 배율과도 맞지 않는데, 그중 하나가 우연히 뚜렷하게 골라져 +2~4/초로 읽혔다. 가장 많이 줄인 후보에 걸린 칸은
            # 치지 않는다 — 다가오는 물체 가장자리(가려지는 배경)의 맞지 않는 칸이 그쪽 끝으로 몰려, 다가오는 물체를 지웠다.
            # 거의 서 있을 때(0.2 m/s 이하)만: 걸으며 흔들리면 다가오는 벽·사람 옆이 줄어드는 쪽에 맞아 정지가 0.5초 늦었다
            half = 0.5 * self.cfg.looming_zoom_step  # 고른 후보에서 포물선으로 옮겨 가는 폭
            shrinking = (textured & confident & (rate < float(rates[first]) - half) & (rate > float(rates[0]) + half))
            # 멀어지는 물체는 줄어드는 칸이 덩어리로 이어진다: 이웃 8칸 중 looming_shrink_veto_support칸 넘게 줄어드는 칸만 친다 —
            # 걸으며 흔들릴 때 다가오는 물체 옆에서 한두 칸이 우연히 줄어드는 쪽에 맞아, 그 둘레의 다가오는 물체를 지웠다
            padded = np.pad(shrinking, 1)
            rows, cols = shrinking.shape
            support = sum(padded[1 + di:rows + 1 + di, 1 + dj:cols + 1 + dj].astype(int)
                          for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj)
            shrinking = shrinking & (support >= int(self.cfg.looming_shrink_veto_support))
            if shrinking.any():
                near = np.zeros(shrinking.shape, dtype=bool)
                for i, j in np.argwhere(shrinking):
                    near[max(i - reach, 0):min(i + reach + 1, rows), max(j - reach, 0):min(j + reach + 1, cols)] = True
                veto = near & trusted & (rate > 0.0)
                rate = np.where(veto, 0.0, rate)
                self.last_shrink_veto[cell_front] = veto.ravel()
        raw = np.full(self._smooth_cells.shape, np.nan)
        raw[cell_front] = np.where(trusted, rate, np.nan).ravel()
        self.last_zoom_raw = raw  # 바닥 몫을 빼기 전 (카메라 자기 보정이 바닥이 퍼지는 빠르기를 잰다)
        rate = np.where(trusted, rate, 0.0)
        ground = self.cfg.looming_ground_comp * max(speed, 0.0)
        if ground > 0.0:  # 평평한 바닥이었다면 보였을 확장률 (칸 줄마다 평균)
            rate = rate - ground * self._zoom_ground[:, None]
        rate_out[cell_front] = np.maximum(rate, 0.0).ravel()
        return rate_out

    def _pick(self, costs: np.ndarray, rates: np.ndarray,
              typical: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """후보별 비용(후보, 칸…) → 칸마다 가장 잘 맞는 확장률(이웃 후보로 포물선을 맞춰 소수점까지)·그 비용·후보 비용의 중간값·
        가장 큰 후보에 걸리지 않았는지. typical: 중간값을 따로 준다 (거친 후보만 맞췄을 때)."""
        best = np.argmin(costs, axis=0)
        last = len(rates) - 1
        c0 = np.take_along_axis(costs, np.clip(best - 1, 0, last)[None], axis=0)[0]
        c1 = np.take_along_axis(costs, best[None], axis=0)[0]
        c2 = np.take_along_axis(costs, np.clip(best + 1, 0, last)[None], axis=0)[0]
        curvature = c0 - 2.0 * c1 + c2
        refine = np.where(curvature > _EPS, 0.5 * (c0 - c2) / np.maximum(curvature, _EPS), 0.0)
        return (rates[best] + np.clip(refine, -0.5, 0.5) * self.cfg.looming_zoom_step, c1,
                np.median(costs, axis=0) if typical is None else typical, best < last)

    def _pick_shrink(self, costs: np.ndarray, rates: np.ndarray, typical: np.ndarray,
                     margin: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """_pick과 같되, 앞에 붙은 줄어드는 쪽 후보(looming_zoom_shrink_rates)는 간격 후보 중 가장 잘 맞은 것보다 비용이
        margin(비율)만큼 낮을 때만 고른다 (가장 많이 줄인 후보는 고르지 않는다). 그렇지 않은 칸은 예전처럼 간격 후보에서 고른다."""
        first = self._zoom_regular
        spaced = self._pick(costs[first:], rates[first:], typical)
        if first == 0:
            return spaced
        whole = self._pick(costs, rates, typical)
        half = 0.5 * self.cfg.looming_zoom_step
        shrink = ((whole[0] < float(rates[first]) - half) & (whole[0] > float(rates[0]) + half)
                  & (whole[1] < (1.0 - margin) * spaced[1]))
        return tuple(np.where(shrink, a, b) for a, b in zip(whole, spaced))

    def _held(self, age: np.ndarray, seen: np.ndarray, dt: float) -> np.ndarray:
        """칸마다 seen이 그 칸이나 이웃 칸(물체가 옮겨 갈 수 있다)에서 이어진 시간(초). seen이 아니면 0."""
        if age.shape != seen.shape:
            age = np.zeros(seen.shape)
        padded = np.pad(age, 1)
        around = np.max([padded[1 + dy:1 + dy + seen.shape[0], 1 + dx:1 + dx + seen.shape[1]]
                         for dy in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0)
        return np.where(seen, around + dt, 0.0)

    def _moving_fit(self, a: np.ndarray, w: np.ndarray, prev: np.ndarray, rates: np.ndarray, dt: float, shifts: np.ndarray,
                    candidates: np.ndarray, shrink_margin: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """움직이는 물체 후보(looming_moving)를 후보 칸(candidates, 잰 영역의 칸 배열)에서만 잰다: 칸마다 잰 가로 흐름(shifts
        화소)만큼 옮긴 칸 가운데를 중심으로 가로로 키우고, 세로는 진행 방향 중심으로 키운다 (땅 위를 옆으로 움직이는 것).
        가만히 있는 것이라면 가로 흐름이 곧 진행 방향 중심으로 키운 만큼이라 진행 방향 후보와 같아진다 — 그래서 이 후보가
        뚜렷이 더 잘 맞는 칸만 움직이는 것으로 본다. 돌려주는 것은 _pick과 같다 (후보가 아닌 칸은 비용 1 = 맞지 않음)."""
        rate = np.zeros(candidates.shape)
        c1, typical, inside = np.ones(candidates.shape), np.ones(candidates.shape), np.zeros(candidates.shape, dtype=bool)
        cells = np.argwhere(candidates)
        if not len(cells):
            return rate, c1, typical, inside
        height, width = prev.shape
        focus_y, ry, px, col_center, row_index, col_index = self._zoom_maps
        pixels = [self._zoom_cell_pixels[i][j] for i, j in cells]
        index = np.concatenate(pixels)
        starts = np.cumsum([0] + [len(chunk) for chunk in pixels[:-1]])
        rows, cols = np.divmod(index, a.shape[1])
        now, weight = a.ravel()[index], w.ravel()[index]
        shift = shifts[row_index[rows], col_index[cols]]
        center, x, y = col_center[cols], px[0, cols], ry[rows, 0]
        scale = np.exp(rates * dt)[:, None]
        sx = np.clip(center - shift + (x - center) / scale - 0.5, 0.0, width - 1.0)
        sy = np.clip(focus_y + y / scale - 0.5, 0.0, height - 1.0)
        x0, y0 = np.floor(sx).astype(np.intp), np.floor(sy).astype(np.intp)
        x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
        wx, wy = (sx - x0).astype(np.float32), (sy - y0).astype(np.float32)
        b = (prev[y0, x0] * (1.0 - wx) + prev[y0, x1] * wx) * (1.0 - wy) + (prev[y1, x0] * (1.0 - wx) + prev[y1, x1] * wx) * wy

        def sums(values: np.ndarray) -> np.ndarray:
            return np.add.reduceat(values, starts, axis=-1)

        aw, bw = now * weight, b * weight
        n = np.maximum(sums(weight), 1.0)
        sa, saa, sb, sbb, sab = sums(aw), sums(aw * now), sums(bw), sums(bw * b), sums(aw * b)
        var_a, var_b = np.maximum(saa - sa * sa / n, 0.0), np.maximum(sbb - sb * sb / n, 0.0)
        table = 1.0 - (sab - sa * sb / n) / np.sqrt(np.maximum(var_a * var_b, _EPS))
        picked = self._pick_shrink(table, rates, np.median(table[self._zoom_regular:], axis=0), shrink_margin)
        for out, value in zip((rate, c1, typical, inside), picked):
            out[cells[:, 0], cells[:, 1]] = value
        return rate, c1, typical, inside

    def _zoom_plan(self, shape: tuple[int, int], dt: float) -> tuple:
        """배율 후보마다 이전 영상에서 가져올 화소(쌍선형 보간 좌표)를 프레임 간격마다 한 번만 만든다.
        커넥톰에 넘기는 칸(_cell_front)을 감싸는 영역만 잰다."""
        key = (shape, round(dt, 6))
        if getattr(self, "_zoom_key", None) == key:
            return self._zoom_cache
        height, width = shape
        row_edges, col_edges = self._cell_edges
        front_rows = np.flatnonzero(self._cell_front.any(axis=1))
        front_cols = np.flatnonzero(self._cell_front.any(axis=0))
        if front_rows.size == 0:
            self._zoom_key, self._zoom_cache = key, ((0, 0, 0, 0), np.zeros(0), [], (0, 0), None, None, None)
            self._zoom_regular = 0
            return self._zoom_cache
        r_lo, r_hi = int(front_rows[0]), int(front_rows[-1]) + 1
        c_lo, c_hi = int(front_cols[0]), int(front_cols[-1]) + 1
        top, bottom, left, right = int(row_edges[r_lo]), int(row_edges[r_hi]), int(col_edges[c_lo]), int(col_edges[c_hi])
        cell_rows = row_edges[r_lo:r_hi + 1] - top
        cell_cols = col_edges[c_lo:c_hi + 1] - left
        cell_front = np.zeros(self._smooth_cells.shape, dtype=bool)
        cell_front[r_lo:r_hi, c_lo:c_hi] = True
        focus_x = (width // 2 + 0.5) - float(self._rx[0, width // 2])
        focus_y = (height // 2 + 0.5) - float(self._ry[height // 2, 0])
        rx = self._rx[:, left:right]
        ry = self._ry[top:bottom, :]
        step = self.cfg.looming_zoom_step
        # 줄어드는 쪽 후보(looming_zoom_shrink_rates)를 앞에 붙인다 — 후보 간격 후보의 중간값(믿을 만한가)에는 넣지 않는다
        shrink = sorted({-abs(float(v)) for v in self.cfg.looming_zoom_shrink_rates if abs(float(v)) > 2.0 * step + 1e-9})
        rates = np.concatenate([shrink, np.arange(-2, int(round(self.cfg.looming_zoom_max_rate / step)) + 1) * step])
        self._zoom_regular = len(shrink)  # 여기부터가 후보 간격 후보
        valid = self._valid if self._valid is not None else np.ones(shape, np.float32)
        masked = bool((valid[top:bottom] < 1.0).any())  # 몸 가리개가 있다 (없으면 옮겨 간 몸 화소를 셀 것이 없다)
        scales = np.exp(rates * dt)

        def axis_samples(center: np.ndarray, offset: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            """후보마다 한 축(가로 또는 세로)의 쌍선형 보간 위치: 앞 이웃·뒤 이웃 번호와 뒤 이웃 가중치 (후보, 화소)."""
            at = np.clip(center + offset[None, :] / scales[:, None] - 0.5, 0.0, size - 1.0)
            near = np.floor(at).astype(np.intp)
            return near, np.minimum(near + 1, size - 1), (at - near).astype(np.float32)

        def moved_valid(gather: tuple) -> np.ndarray:
            """모든 후보에서 몸 화소가 옮겨 오지 않는 화소 (세로, 가로)."""
            common = np.ones((bottom - top, right - left), np.float32)
            if masked:
                y0, y1, _, x0, x1, _ = gather
                for k in range(len(rates)):
                    rows0, rows1 = valid[y0[k]], valid[y1[k]]
                    common *= (rows0[:, x0[k]] * rows0[:, x1[k]] * rows1[:, x0[k]] * rows1[:, x1[k]]).astype(np.float32)
            return common

        # 진행 방향 중심으로 키운 이전 영상은 가로·세로로 따로 옮겨진다 (열 위치는 가로 좌표만, 줄 위치는 세로 좌표만으로 정해진다).
        # 그래서 후보마다 줄 번호·열 번호만 두고 보간도 줄 → 열 차례로 한다 — 화소마다 네 이웃 위치를 두면 장 사이 시간이 바뀔
        # 때마다(휴대폰은 매 장) 후보 23개 × 화소 수만큼 새로 만들어야 해서 한 장에 100 ms 넘게 걸렸다
        self._zoom_gather = (*axis_samples(focus_y, ry[:, 0], height), *axis_samples(focus_x, rx[0], width))
        samples = [moved_valid(self._zoom_gather)]
        self._zoom_local_gather = None
        if self.cfg.looming_local_center:
            # 칸 가운데를 중심으로 키운 후보: 화소마다 그 칸의 가운데 (화소 좌표) — 이것도 줄·열로 나뉜다
            col_center = 0.5 * (col_edges[:-1] + col_edges[1:])[np.searchsorted(col_edges, np.arange(left, right), "right") - 1]
            row_center = 0.5 * (row_edges[:-1] + row_edges[1:])[np.searchsorted(row_edges, np.arange(top, bottom), "right") - 1]
            px = np.arange(left, right) + 0.5
            py = np.arange(top, bottom) + 0.5
            self._zoom_local_gather = (*axis_samples(row_center[None, :], py - row_center, height),
                                       *axis_samples(col_center[None, :], px - col_center, width))
            samples[0] = samples[0] * moved_valid(self._zoom_local_gather)
        # 움직이는 물체 후보(_moving_fit)가 쓰는 것: 진행 방향 세로 중심, 화소마다 세로 변위·가로 위치, 그 열의 칸 가운데, 화소의 칸 번호
        col_index = np.searchsorted(col_edges, np.arange(left, right), "right") - 1
        row_index = np.searchsorted(row_edges, np.arange(top, bottom), "right") - 1
        self._zoom_maps = (focus_y, ry, (np.arange(left, right) + 0.5)[None, :],
                           0.5 * (col_edges[:-1] + col_edges[1:])[col_index], row_index, col_index)
        region = np.arange((bottom - top) * (right - left)).reshape(bottom - top, right - left)
        self._zoom_cell_pixels = [[region[cell_rows[i]:cell_rows[i + 1], cell_cols[j]:cell_cols[j + 1]].ravel()
                                   for j in range(c_hi - c_lo)] for i in range(r_hi - r_lo)]
        ground_rows = np.array([float(np.mean(self._ground_rate[row_edges[i]:row_edges[i + 1]])) for i in range(r_lo, r_hi)])
        self._zoom_key = key
        self._zoom_cache = ((top, bottom, left, right), rates, samples, (r_hi - r_lo, c_hi - c_lo), cell_front,
                            cell_rows, cell_cols)
        self._zoom_ground = ground_rows
        return self._zoom_cache

    def _on_course(self, cells: np.ndarray, speed: float, lateral: np.ndarray | None = None) -> np.ndarray | float:
        """칸마다 '휠체어가 지나갈 통로 안으로 오는가'의 가중치 0~1 (looming_course_*). 각도가 아니라 거리(m)로 정한다 —
        같은 각도라도 가까우면 부딪히고 멀면 스쳐 가기 때문이다.

        다가오는 속도 v와 확장률 k로 거리 Z ≈ v/k를 알고, 칸 방향(가로각 a, 위 각 e)을 알면
        - 옆: 진행 방향에서 옆으로 떨어진 거리 Z·tan(a)가 몸 폭보다 크면 스쳐 지나갈 것(옆벽·지나칠 모서리)
        - 위: 높이 = 카메라 높이 + Z·tan(e)가 타고 있는 사람 머리보다 높으면 위로 지나갈 것(천장·조명·간판)
        이라 커넥톰에 넘기지 않는다. 가운데 칸(a≈0, e≈0)은 거리와 상관없이 늘 넘긴다.
        거의 서 있으면(0.2 m/s 이하) 거리를 모르므로 각도로: 정면은 1, 옆으로 갈수록 0 (looming_still_*).
        lateral: 옆 거리(m)를 따로 준다 — 움직이는 물체가 닿을 때의 옆 거리 (_moving_lateral)."""
        near, far = self.cfg.looming_course_inner_m, self.cfg.looming_course_outer_m
        if far <= 0.0:
            return 1.0
        if self.cfg.brake_decel > 0.0:
            # 감속 한계가 있으면 서기까지(v/a초) 옆에 있던 사람이 걸어 들어올 수 있다 → 그만큼 넓게 본다 (looming_brake_cross_mps)
            extra = self.cfg.looming_brake_cross_mps * max(speed, 0.0) / self.cfg.brake_decel
            near, far = near + extra, far + extra
        if speed <= 0.2:
            # 거의 서 있으면 거리를 잴 수 없다 → 정면일수록 크게 (서 있는 나에게 부딪힐 것은 정면에서 다가오는 것이다.
            # 옆에서 움직이는 사람이 제자리에서 돌 때 시야 안쪽으로 들어와 멈추는 일이 없게)
            inner, fade = self.cfg.looming_still_max_azimuth_deg, max(self.cfg.looming_still_fade_deg, _EPS)
            return np.clip((inner + fade - np.abs(self._cell_angles[..., 0])) / fade, 0.0, 1.0)
        distance = speed / np.maximum(cells, _EPS)
        if lateral is None:
            lateral = distance * np.abs(np.tan(np.radians(self._cell_angles[..., 0])))
        weight = np.clip((far - lateral) / max(far - near, _EPS), 0.0, 1.0)
        top, top_far = self.cfg.looming_course_top_m, self.cfg.looming_course_top_outer_m
        if top_far > 0.0:
            height = self.cfg.camera_height + distance * np.tan(np.radians(self._cell_angles[..., 1]))
            weight = weight * np.clip((top_far - height) / max(top_far - top, _EPS), 0.0, 1.0)
        return weight

    def _moving_lateral(self, cells: np.ndarray, shifts: np.ndarray, dt: float, speed: float) -> np.ndarray:
        """움직이는 물체가 지금부터 닿을 때까지 진행 방향에 가장 가까이 오는 옆 거리(m).
        지금 옆 거리 X = 거리 × tan(a). 물체의 방위 tan(a)가 초당 D만큼 바뀌고 확장률이 k면 닿을 때(1/k초 뒤)의 옆 거리는
        거리 × D / k다 (가만히 있는 것은 D = k·tan(a)라 지금과 같고, 같은 방위로 다가오면 0). 그 사이에 통로를 가로지르면
        (두 옆 거리의 부호가 다르면) 0 — 골목에서 나오는 차의 앞머리는 닿을 때쯤 통로를 지나 있겠지만 그 뒤의 차체가 길을 막는다.
        거리는 가만히 있는 것처럼 속도/k로 — 이쪽으로 다가오는 것은 실제보다 가깝게 잡혀 옆 거리도 작게 나온다(더 조심하는 쪽)."""
        width = self._shape[1]
        focal = (width / 2.0) / math.tan(math.radians(self.cfg.fov_deg) / 2.0)
        k = np.maximum(cells, _EPS)
        distance = speed / k
        now = distance * np.tan(np.radians(self._cell_angles[..., 0]))
        then = distance * (shifts / max(dt, _EPS) / focal) / k
        return np.where(now * then <= 0.0, 0.0, np.minimum(np.abs(now), np.abs(then)))

    def _stabilize(self, prev: np.ndarray, gray: np.ndarray, speed: float = 0.0) -> tuple[float, float, float]:
        """흔들림 보정 (looming_stabilize_*): 직전 영상이 지금 영상과 화면 전체로 위아래·좌우로 얼마나 밀렸는지, 얼마나 기울었는지
        재어 카메라가 돈 각도 (좌우 turn, 숙임 nod, 기울기 roll — 도, _undo_rotation과 같은 약속)로 돌려준다. 되돌리기는 밀기가
        아니라 그 각도만큼의 회전으로 한다 — 넓은 화각에서는 숙이면 화면 가장자리가 가운데보다 더 많이 움직이고(원근),
        걸으며 드는 휴대폰은 좌우로 기울기도 하기 때문이다 (밀기만 되돌리면 그 나머지가 칸마다 퍼짐·오므림으로 잡혀 헛정지가 났다).

        확장 중심(진행 방향) 둘레의 창에서, 중심의 왼쪽 반·오른쪽 반의 열 단면 밀림을 따로 잰다(세로는 위·아래 반의 행 단면).
        흔들림은 양쪽을 같은 쪽으로 밀고, 다가오는 것의 확장은 양쪽을 반대쪽으로 민다(중심에서 퍼지니까). 그래서 두 쪽이 같은
        쪽으로 밀렸을 때 그 공통 몫(작은 쪽)만 흔들림으로 보고, 반대쪽이면 0으로 둔다 — 진짜 루밍을 흔들림으로 지우지 않는다.
        (창 전체로 재면 가까운 벽의 확장 일부를 흔들림으로 잘못 재어 벽 앞 정지가 사라졌고, 두 쪽 평균은 아래쪽 바닥이 위쪽
        벽보다 훨씬 빨리 퍼지는 탓에 흔들림이 없는데도 세로 밀림을 0.5~2 화소 잡아 좁은 화각에서 오히려 헛정지를 냈다.)
        이웃 값으로 포물선을 맞춰 소수점까지 재고, 한쪽이라도 찾는 범위 끝에 걸리면 믿지 않는다.
        기울기: 창의 왼쪽 끝·오른쪽 끝 띠의 세로 밀림을 따로 재어, 공통 몫(숙임)을 뺀 나머지가 두 띠에서 반대쪽일 때만 그 작은
        쪽을 기울기로 본다 (확장은 진행 방향 높이에서 세로로 거의 밀지 않고, 밀어도 두 띠를 같은 쪽으로 민다).
        self.last_shift에 잰 (가로, 세로) 화소를, self.last_roll에 기울기(도)를 남긴다."""
        height, width = gray.shape
        # 찾는 범위는 영상 가로의 1/8까지 — 작은 영상에서 범위가 크면 반쪽 창이 좁아져 퍼지는 것을 밀림으로 잘못 잰다
        limit = min(int(math.ceil(self.cfg.looming_stabilize_px)), width // 8)
        self.last_shift, self.last_roll = (0.0, 0.0), 0.0
        if limit <= 0 or height < 4 * limit or width < 4 * limit:
            return 0.0, 0.0, 0.0
        fx = int(round(-float(self._rx[0, 0]) + 0.5))   # 확장 중심 (화소)
        fy = int(round(-float(self._ry[0, 0]) + 0.5))
        half_w = max(2 * limit, int(width * self.cfg.looming_stabilize_window / 2))
        half_h = max(2 * limit, int(height * self.cfg.looming_stabilize_window / 2))
        x0, x1 = max(limit, fx - half_w), min(width - limit, fx + half_w)
        y0, y1 = max(limit, fy - half_h), min(height - limit, fy + half_h)
        if x1 - x0 < 2 * limit or y1 - y0 < 2 * limit:
            return 0.0, 0.0, 0.0

        # 넓혀 찾기는 거의 서 있을 때만 (제자리에서 둘러보기): 걸을 때는 되풀이 무늬(벽돌) 벽에서 엉뚱한 밀림에 맞아 벽에 늦게 섰다
        wide = int(math.ceil(self.cfg.looming_stabilize_wide_px)) if speed <= 0.2 else 0

        def offset(now: np.ndarray, before: np.ndarray, start: int, stop: int, widen: bool = True) -> float | None:
            """단면의 [start, stop) 구간을 밀어 보며 가장 잘 맞는 밀기 (+면 지금이 그만큼 뒤로 밀렸다). -limit~+limit에서 찾고,
            끝에 걸리면 looming_stabilize_wide_px까지 넓혀(단면 길이 안에서) 한 번 더 찾는다 — 넓혀 찾은 답은 범위 끝 가까이(limit의
            3/4) 넘을 때만 쓴다 (한참 안쪽이면 좁게 찾을 때 이미 나왔어야 한다: 멀어지는 판의 가장자리에 엉뚱하게 맞은 답이었다).
            그래도 끝에 걸리면 None."""
            found = search(now, before, start, stop, limit)
            reach = min(wide, start, len(before) - stop)
            if found is None and widen and reach > limit:
                found = search(now, before, start, stop, reach)
                if found is not None and abs(found) < 0.75 * limit:
                    found = None
            return found

        def search(now: np.ndarray, before: np.ndarray, start: int, stop: int, limit: int) -> float | None:
            """[start, stop) 구간을 -limit~+limit 밀어 본다. 평균을 빼고 표준편차로 나눠 비교한다(밝기·대비가 바뀌어도 같게).
            찾는 범위 끝에 걸리면 None."""
            def normal(values: np.ndarray) -> np.ndarray:
                """마지막 축마다 평균을 빼고 표준편차로 나눈다."""
                return (values - values.mean(axis=-1, keepdims=True)) / np.maximum(values.std(axis=-1, keepdims=True), 1e-6)

            core = normal(now[start:stop])
            # 밀기 후보 전부를 한 번에: 밀기 s(-limit~+limit)의 창은 before[start - s:stop - s]
            windows = np.lib.stride_tricks.sliding_window_view(before[start - limit:stop + limit], stop - start)[::-1]
            costs = np.abs(core - normal(windows)).mean(axis=-1)
            best = int(np.argmin(costs))
            if best in (0, 2 * limit):
                return None
            c0, c1, c2 = costs[best - 1], costs[best], costs[best + 1]
            curvature = c0 - 2.0 * c1 + c2
            refine = 0.5 * (c0 - c2) / curvature if curvature > _EPS else 0.0
            return best - limit + float(np.clip(refine, -0.5, 0.5))

        def both_sides(now: np.ndarray, before: np.ndarray, low: int, center: int, high: int) -> float:
            """중심 양쪽 반이 함께 밀린 몫 — 같은 쪽이면 작은 쪽 크기, 반대쪽(확장)이면 0."""
            center = min(max(center, low + limit), high - limit)
            first, second = offset(now, before, low, center), offset(now, before, center, high)
            if first is None or second is None or first * second <= 0.0:
                return 0.0
            if max(abs(first), abs(second)) > limit and min(abs(first), abs(second)) < 0.75 * limit:
                # 넓혀 찾은 쪽이 있으면 다른 쪽도 범위 가까이(3/4 넘게) 밀렸을 때만 — 화면이 통째로 돈 것. 멀어지는 판에서는 한쪽만
                # 가장자리에 엉뚱하게 맞아 넓혀 찾은 답(10화소 남짓)과 좁게 찾은 답(6화소)이 섞였다
                return 0.0
            return math.copysign(min(abs(first), abs(second)), first)

        dx = both_sides(gray[y0:y1].mean(axis=0), prev[y0:y1].mean(axis=0), x0, fx, x1)
        dy = both_sides(gray[:, x0:x1].mean(axis=1), prev[:, x0:x1].mean(axis=1), y0, fy, y1)
        roll = 0.0
        band = max(2 * limit, (x1 - x0) // 4)
        if self.cfg.looming_stabilize_roll and x1 - x0 >= 2 * band:
            first = offset(gray[:, x0:x0 + band].mean(axis=1), prev[:, x0:x0 + band].mean(axis=1), y0, y1, False)
            second = offset(gray[:, x1 - band:x1].mean(axis=1), prev[:, x1 - band:x1].mean(axis=1), y0, y1, False)
            if first is not None and second is not None and (first - dy) * (second - dy) < 0.0:
                # 오른쪽 띠가 위로(−) 밀렸으면 카메라가 시계 방향으로 기울었다 (화면 속 세상은 반시계로 돈다). 0.15화소 밑은
                # 잡음으로 본다 (다가오는 것의 확장도 두 띠를 아주 조금 다르게 민다)
                lean = min(abs(first - dy), abs(second - dy))
                lean = math.copysign(lean, second - dy) if lean >= 0.15 else 0.0
                roll = -math.degrees(math.atan2(2.0 * lean, (x1 - x0) - band))
        self.last_shift, self.last_roll = (dx, dy), roll
        if abs(dx) < 0.05 and abs(dy) < 0.05 and abs(roll) < 0.02:
            return 0.0, 0.0, 0.0
        focal = (width / 2.0) / math.tan(math.radians(self.cfg.fov_deg) / 2.0)
        # 화면이 오른쪽(+dx)으로 밀렸으면 카메라는 왼쪽으로 돌았고, 아래(+dy)로 밀렸으면 위로 들렸다
        return -math.degrees(math.atan(dx / focal)), -math.degrees(math.atan(dy / focal)), roll

    def _persist(self, cells: np.ndarray) -> np.ndarray:
        """칸마다 최근 looming_persist_frames장 확장률의 중간값 (1 = 지금 값 그대로). 통로 거르기(_on_course)의 거리 = 속도/확장률을
        이 값으로 잰다. 걸음 출렁임은 화면 귀퉁이·가까운 옆벽에서 잘못된 맞춤을 만들어 확장률이 한 장만 4~5/초로 튀는데, 그러면
        옆벽이 '아주 가깝다(통로 안)'로 읽혀 거르기를 빠져나갔다. 다가오는 것은 여러 장 이어서 커지므로 중간값도 크다.
        커넥톰에 넘기는 값은 지금 값 그대로지만, 빨리 커지는 물체는 거르기를 한두 장 늦게 통과한다.
        대가: 옆에서 걸어 들어오는 작은 물체는 영상마다 이웃 칸으로 옮겨 다녀 칸별 중간값이 작고, 통로 밖으로 걸러진다.
        이웃 칸까지 보게 하면 그 사람은 다시 잡히지만(닿기 0.2초 전) 걸음 출렁임도 다시 빠져나갔다 (docs/connectome.md)."""
        frames = int(self.cfg.looming_persist_frames)
        if frames <= 1:
            return cells
        if self._recent and self._recent[-1].shape != cells.shape:
            self._recent.clear()
        self._recent.append(cells.copy())
        del self._recent[:-frames]
        return np.median(np.stack(self._recent), axis=0)

    def _habituate(self, cells: np.ndarray, dt: float) -> np.ndarray:
        """잡음 바닥 보정 (looming_floor_*): 칸마다 평소 루밍 수준(잡음 바닥)을 갱신하고, 그 margin배를 뺀 루밍을 돌려준다.

        바닥은 지금 값보다 낮으면 floor_rise_s로 천천히 오르고, 높으면 floor_fall_s로 빨리 내려간다 — 늘 깔려 있는 잡음
        (손 떨림, 자동 초점, 카메라 잡음, 바닥 무늬)의 높이를 따라간다. 바닥의 floor_event_ratio배 + floor_event를 넘는
        값은 실제로 다가오는 것이라 바닥을 올리지 않는다 (다가오는 사람·벽에 무뎌지지 않게)."""
        cfg = self.cfg
        floor = self.floor
        event = cells > cfg.looming_floor_event_ratio * floor + cfg.looming_floor_event
        rise = -math.expm1(-dt / max(cfg.looming_floor_rise_s, _EPS))
        fall = -math.expm1(-dt / max(cfg.looming_floor_fall_s, _EPS))
        step = np.where(cells > floor, np.where(event, 0.0, rise), fall)
        self.floor = floor + step * (cells - floor)
        return np.maximum(cells - cfg.looming_floor_margin * self.floor, 0.0)

    def _shrink(self, gray: np.ndarray) -> np.ndarray:
        """밝기 영상을 looming_downsample배로 줄인다(n×n 칸 평균). 줄인 뒤 한 변이 16픽셀보다 작아지면 덜 줄인다."""
        height, width = gray.shape
        n = max(1, min(int(self.cfg.looming_downsample), height // 16, width // 16))
        if n == 1:
            return gray
        h, w = height // n * n, width // n * n
        return gray[:h, :w].reshape(h // n, n, w // n, n).mean(axis=(1, 3))

    def recalibrate(self, **values: float) -> None:
        """카메라 값(fov_deg·camera_pitch_deg·camera_height·looming_zoom_min_std·looming_floor_margin)을 바꾼다 — 카메라 자기 보정.
        화면 칸·확장 중심·바닥 확장률·몸 마스크를 새 값으로 다시 만들되, 평활한 칸별 루밍·옆 흐름·잡음 바닥과 직전 영상은
        그대로 이어 간다 (값을 바꾸는 순간 위험도가 0으로 떨어지거나 첫 영상처럼 비교를 건너뛰지 않게)."""
        self.cfg = dataclasses.replace(self.cfg, **values)
        shape = self._shape
        kept = (self._smooth_cells, self.floor, self._smooth_flow) if shape is not None else None
        self._shape = None
        self._zoom_key = None
        self._valid = None
        if shape is not None and kept is not None:
            self._ensure_fields(shape)
            self._smooth_cells, self.floor, self._smooth_flow = (array.copy() for array in kept)

    def set_ground_line(self, slope: float, horizon: float) -> None:
        """잰 바닥(카메라 자기 보정): 1 m/s로 갈 때 줄마다 바닥이 퍼지는 빠르기 = slope × max(horizon − v, 0) (v = 줄의 세로 위치 ÷
        초점거리, +위). 설정값(높이·숙인 각)으로 계산한 바닥 대신 이것을 뺀다. 평평한 바닥이면 같은 꼴이다 —
        slope = cos²(숙인 각)/높이, horizon = tan(숙인 각)."""
        self._ground_line = (float(slope), float(horizon))
        if self._shape is not None:
            self._apply_ground_line()

    def clear_ground_line(self) -> None:
        """잰 바닥을 버리고 설정값(높이·숙인 각)으로 계산한 바닥으로 돌아간다."""
        self._ground_line = None
        if self._shape is not None:
            shape, self._shape = self._shape, None
            kept = (self._smooth_cells, self.floor, self._smooth_flow, self._valid)
            self._ensure_fields(shape)
            self._smooth_cells, self.floor, self._smooth_flow, self._valid = kept
            self._zoom_key = None

    def _apply_ground_line(self) -> None:
        slope, horizon = self._ground_line
        self._ground_rate = (slope * np.maximum(horizon - self._ground_v, 0.0)).astype(np.float32)[:, None]
        self._zoom_key = None  # 칸 줄마다의 바닥 몫(_zoom_ground)을 다시 만든다

    def update(self, frame: np.ndarray, dt: float, turn_deg: float = 0.0, speed: float = 0.0,
               nod_deg: float = 0.0, roll_deg: float = 0.0) -> LoomingInfo:
        """새 프레임(H×W×3, uint8, RGB)을 받아 루밍 정보를 계산한다.

        turn_deg: 이전 프레임 이후 로봇이 돈 각도(도, +시계 방향). 알려 주면 회전으로 화면이 옆으로 흐른 것을 먼저 되돌려,
                  도는 것을 다가오는 것으로 착각하지 않는다 (_undo_rotation).
        speed: 이전 프레임 이후 앞으로 간 속도(m/s, 자기 움직임 신호). 알려 주면 그 속도로 평평한 바닥이 퍼져 보이는 몫을
               빼고 잰다 (looming_ground_comp — 무늬 있는 바닥을 다가오는 것으로 착각하지 않게).
        nod_deg, roll_deg: 이전 프레임 이후 카메라가 숙여진 각도(+아래로)·기울어진 각도(+시계 방향) — 휴대폰·IMU 자이로가 있으면.
                  없으면 영상으로 잰다 (_stabilize).
        """
        return self.update_gray(self.shrink_frame(frame), frame.shape[:2], dt, turn_deg, speed, nod_deg, roll_deg)

    def shrunk_shape(self, frame_shape: tuple[int, int]) -> tuple[int, int]:
        """원래 영상 크기 (높이, 너비) → 줄인 영상 크기 (_shrink와 같다)."""
        height, width = int(frame_shape[0]), int(frame_shape[1])
        n = max(1, min(int(self.cfg.looming_downsample), height // 16, width // 16))
        return height // n, width // n

    def shrink_frame(self, frame: np.ndarray) -> np.ndarray:
        """카메라 영상(H×W×3 RGB) → 루밍을 재는 줄인 밝기 영상 (H/n × W/n, float32). 녹화해 두고 update_gray로 다시 쓸 수 있다."""
        return self._shrink(frame[:, :, :3].astype(np.float32) @ _RGB_TO_GRAY).astype(np.float32)

    def _saccade(self, rotation_dps: float, dt: float) -> bool:
        """카메라를 휙 돌리는 중인가 (looming_saccade_dps). 초파리도 몸을 휙 돌리는 동안(사케이드)은 운동 명령의 복사본으로
        시각 운동 신호를 눌러 둔다. 돈 만큼 되돌린 영상은 빨리 돌수록 원근 오차·새로 들어온 가장자리·번짐이 커서, 화면의 1할
        넘는 칸이 확장률 상한까지 튀었다 (휴대폰을 초당 300도로 둘러볼 때 — 자이로가 정확해도). 휴대폰은 카메라 영상이 자이로보다
        늦게 나와 그 차이만큼 더 어긋난다. 회전은 자이로(바퀴)가 잰 것만 본다 — 영상으로 잰 나머지 흔들림까지 더했더니 되풀이
        무늬(벽돌) 벽에 다가갈 때 확장을 흔들림으로 잘못 재어(자이로 30도/초에 240도/초) 벽을 보지 못했다. 그래서 자이로가 없으면
        켜지지 않는다."""
        limit = self.cfg.looming_saccade_dps
        if limit <= 0.0:
            self.last_saccade = False
            return False
        if rotation_dps > limit:
            self._saccade_left = self.cfg.looming_saccade_hold_s
            self.last_saccade = True
        elif self._saccade_left > 0.0:
            self._saccade_left -= dt
            self.last_saccade = True
        else:
            self.last_saccade = False
        return self.last_saccade

    def _jumped(self, cells: np.ndarray, dt: float) -> bool:
        """화면 대부분이 한 장 만에 튀었나 (looming_jump_fraction) — 자이로가 놓친 휙 돌기·번짐. 이어서 버리는 것은
        looming_jump_max_s초까지."""
        fraction = self.cfg.looming_jump_fraction
        front = self._cell_front > 0.0
        if fraction <= 0.0 or not front.any():
            return False
        jumped = (float(np.mean(cells[front] >= self.cfg.looming_jump_rate)) > fraction
                  and self._jump_s + dt <= self.cfg.looming_jump_max_s + 1e-9)
        self._jump_s = self._jump_s + dt if jumped else 0.0
        return jumped

    def _skip_measuring(self, dt: float) -> LoomingInfo:
        """휙 돌리는 동안: 루밍을 새로 재지 않고 지난 값을 0 쪽으로 내린다 (커넥톰 입력이 끊기듯 줄어든다). 통로 거르기의 최근
        값(_persist)에도 넣지 않는다 — 돌기가 끝난 뒤 튄 값이 중간값으로 남지 않게."""
        shape = self._smooth_cells.shape
        self.last_measured = np.zeros(shape)
        if self.last_gate is None or self.last_gate.shape != shape:
            self.last_gate = np.ones(shape)
        self.last_cell_shift = np.zeros(shape)
        self.last_moving = np.zeros(shape, dtype=bool)
        self.last_local = np.zeros(shape, dtype=bool)
        self.last_shrink_veto = np.zeros(shape, dtype=bool)
        tau = self.cfg.looming_smooth_tau
        keep = 0.0 if tau <= 0.0 else math.exp(-dt / tau)
        self._smooth_left *= keep
        self._smooth_right *= keep
        self._smooth_cells *= keep
        self._smooth_flow *= math.exp(-dt / max(self.cfg.hs_hold_decay_s, _EPS))
        left, right = self._smooth_left, self._smooth_right
        strength = max(left, right)
        ttc = 1.0 / strength if strength > _EPS else math.inf
        direction = (right - left) / (right + left) if (right + left) > _EPS else 0.0
        return LoomingInfo(strength, ttc, left, right, direction, self._smooth_cells.copy(), self._cell_angles,
                           self._smooth_flow.copy())

    def update_gray(self, gray: np.ndarray, frame_shape: tuple[int, int], dt: float, turn_deg: float = 0.0,
                    speed: float = 0.0, nod_deg: float = 0.0, roll_deg: float = 0.0) -> LoomingInfo:
        """update()와 같되, 이미 줄인 밝기 영상(shrink_frame)을 받는다. frame_shape: 원래 카메라 영상의 (높이, 너비) — 몸 마스크용."""
        gray = np.asarray(gray, dtype=np.float32)
        shape = gray.shape
        self._ensure_fields(shape)
        self._ensure_mask(tuple(int(v) for v in frame_shape[:2]), shape)
        prev = self._prev_gray
        self._prev_gray = gray
        self.last_pair = (prev, gray) if prev is not None and prev.shape == shape else None
        self.last_zoom_raw = None
        if prev is None or prev.shape != shape or dt <= 0.0:
            self._smooth_left = self._smooth_right = 0.0
            self._recent.clear()
            self._smooth_cells[:] = 0.0
            self._smooth_flow[:] = 0.0
            return LoomingInfo(0.0, math.inf, 0.0, 0.0, 0.0, self._smooth_cells.copy(), self._cell_angles,
                               self._smooth_flow.copy())

        # 그 사이 돈 만큼(자이로·바퀴) 이전 영상을 돌려 놓고, 그래도 남은 흔들림(손떨림·걸음·자이로 오차)을 그 위에서 재어
        # 처음 영상에서 한 번에 다시 돌려 놓는다 (두 번 보간하면 이전 영상만 흐려진다)
        # 넓은 화각에서는 창 가장자리가 가운데보다 많이 밀려 한 번 잰 각도가 조금 크다 → 되돌린 영상에서 남은 만큼을 다시 재어 더한다
        spin = (turn_deg, nod_deg, roll_deg)
        turned = self._undo_rotation(prev, *spin)
        first_shift, seen_roll = None, 0.0
        for _ in range(int(self.cfg.looming_stabilize_passes) if self.cfg.looming_stabilize_rotate else 0):
            extra = self._stabilize(turned, gray, speed)
            first_shift = first_shift or self.last_shift
            seen_roll += self.last_roll
            if not any(extra):
                break
            spin = tuple(a + b for a, b in zip(spin, extra))
            turned = self._undo_rotation(prev, *spin)
        self.last_shift, self.last_roll = first_shift or (0.0, 0.0), seen_roll
        if not self.cfg.looming_stabilize_rotate:  # 예전 방식: 잰 밀림만큼 이전 영상을 화면 전체로 똑같이 민다
            self._stabilize(turned, gray, speed)
            turned = _shifted(turned, *self.last_shift)
        prev = turned
        if self._saccade(math.sqrt(turn_deg ** 2 + nod_deg ** 2 + roll_deg ** 2) / dt, dt):
            return self._skip_measuring(dt)
        # 내 몸(무릎·팔걸이)은 카메라에 붙어 있어 화면에서 움직이지 않는다 → 그 화소와, 돌려 놓은 이전 영상에서 몸이 옮겨 간
        # 화소를 빼고 잰다 (돌 때 몸이 밀린 것처럼 보여 루밍·옆 흐름으로 잘못 잡히지 않게)
        # (몸 가리개가 없으면 — 휴대폰 — 옮겨 갈 몸 화소도 없다)
        weight = self._valid if self._valid.min() >= 1.0 else self._valid * (self._undo_rotation(self._valid, *spin) > 0.999)
        # 칸별 가로 흐름(도/초): HS 세포 입력. 돈 만큼 되돌린 이전 영상과 비교하므로 회전 흐름은 빠져 있다.
        # 빨리 도는 동안은 되돌린 영상의 원근 오차가 커서 옆 흐름으로 쓰지 않고 직전 값을 hs_hold_decay_s로 0 쪽으로 줄인다
        # (잰 밀림은 looming_moving에만 쓴다)
        turning_fast = abs(turn_deg) > self.cfg.hs_turn_limit_deg
        shifts = self._cell_shifts(gray, prev, weight) if (not turning_fast or self.cfg.looming_moving) else None
        self.last_cell_shift = shifts if shifts is not None else np.zeros(self._smooth_cells.shape)
        raw_flow = None if turning_fast else shifts * self._deg_per_px[None, :] / dt
        grad_y, grad_x = np.gradient(gray)          # np.gradient의 축 순서: (행=세로, 열=가로)
        grad_x, grad_y = grad_x * weight, grad_y * weight  # 몸 화소는 기울기 0 → 아래 계산에서 빠진다
        it = (gray - prev) / dt * weight            # 밝기의 시간 변화율
        r = grad_x * self._rx + grad_y * self._ry   # 확장장 위로의 투영 ∇I·(화소-중심)
        ground = self.cfg.looming_ground_comp * max(speed, 0.0)
        if ground > 0.0:
            it = it + (ground * self._ground_rate) * r  # 바닥이 퍼지는 만큼(I_t = -k·r)을 미리 되돌린다

        # 화면 전체가 한쪽으로 흐르는 성분은 가운데 영역에서 맞추고(좌·우 무늬를 다 써서 안정적) 화면 전체에서 뺀다.
        # 좌·우 값은 가운데 영역(진행 방향)에서 — 옆으로 스쳐 가는 벽은 재지 않는다. 칸은 화면 전체에서 잰다.
        y0, y1, x0, x1, mid = self._roi
        r = r.astype(np.float64)
        flow = self._sideways_flow(it[y0:y1, x0:x1], r[y0:y1, x0:x1], grad_x[y0:y1, x0:x1], grad_y[y0:y1, x0:x1])
        row_edges, col_edges = self._cell_edges
        raw_cells = np.zeros(self._smooth_cells.shape)
        raw_left = raw_right = 0.0
        if flow is not None:
            it = it + flow[0] * grad_x + flow[1] * grad_y
            it_roi, r_roi = it[y0:y1, x0:x1], r[y0:y1, x0:x1]
            total = float(np.sum(r_roi * r_roi))
            raw_left = max(self._damped_rate(it_roi[:, :mid], r_roi[:, :mid], 0.02 * total), 0.0)
            raw_right = max(self._damped_rate(it_roi[:, mid:], r_roi[:, mid:], 0.02 * total), 0.0)
            if self.cfg.looming_method == "zoom":
                raw_cells = self._zoom_cells(gray, prev, weight, dt, speed, shifts)
            else:
                cell_damping = 0.04 * float(np.sum(r * r)) / raw_cells.size  # 평균 칸 기준으로 좌·우 절반과 같은 비율
                for i in range(raw_cells.shape[0]):
                    rows = slice(row_edges[i], row_edges[i + 1])
                    for j in range(raw_cells.shape[1]):
                        cols = slice(col_edges[j], col_edges[j + 1])
                        raw_cells[i, j] = max(self._damped_rate(it[rows, cols], r[rows, cols], cell_damping), 0.0)
            raw_cells *= self._cell_front  # looming_cell_*로 자른 칸 (기본은 자르지 않음 — config 설명)
            if self._jumped(raw_cells, dt):
                self.last_saccade = True
                return self._skip_measuring(dt)
            # 통로 거르기는 최근 몇 장의 칸별 중간값으로: 한 장 튄 확장률은 '아주 가깝다'로 읽혀 거르기를 빠져나간다
            persisted = self._persist(raw_cells)
            gate = self._on_course(persisted, speed)
            if self.cfg.looming_moving and self.last_moving.any() and speed > 0.2 and shifts is not None:
                # 옆으로 움직이는 칸은 닿을 때의 옆 거리로 거른다 (길 쪽으로 들어오는 것은 지금 옆에 있어도 넘긴다)
                lateral = self._moving_lateral(persisted, shifts, dt, speed)
                gate = np.where(self.last_moving, self._on_course(persisted, speed, lateral), gate)
            if self.cfg.looming_local_center and self.last_local.any() and speed > 0.2:
                # 자리를 지키며 커지는 칸은 거리·각도와 상관없이 충돌 경로다 (같은 방위각으로 다가오는 물체) — 통로 거르기를 건너뛴다.
                # 거의 서 있을 때는 정면 각도 거르기를 그대로 둔다: 회전을 모르고(자이로 없이) 제자리에서 돌면 도는 화면이 제자리
                # 확대로 잡혀 헛정지가 났다
                gate = np.where(self.last_local, 1.0, gate)
            self.last_measured = raw_cells.copy()                        # 거르기 전 (위험도 겹쳐 보기: 거른 칸 표시)
            self.last_gate = np.broadcast_to(gate, raw_cells.shape).copy()
            raw_cells *= gate
            if self.cfg.looming_floor_margin > 0.0:
                raw_cells = self._habituate(raw_cells, dt)

        # 지수 평활: 한 프레임 튄 값이 바로 조향으로 이어지지 않게 한다
        tau = self.cfg.looming_smooth_tau
        alpha = 1.0 if tau <= 0.0 else -math.expm1(-dt / tau)  # 1 - exp(-dt/tau)
        self._smooth_left += alpha * (raw_left - self._smooth_left)
        self._smooth_right += alpha * (raw_right - self._smooth_right)
        self._smooth_cells += alpha * (raw_cells - self._smooth_cells)
        if raw_flow is not None:
            self._smooth_flow += alpha * (raw_flow - self._smooth_flow)
        else:  # 빨리 도는 중: 새로 재지 않되, 오래된 값이 남아 있지 않게 0 쪽으로 천천히 줄인다
            self._smooth_flow *= math.exp(-dt / max(self.cfg.hs_hold_decay_s, _EPS))
        left, right = self._smooth_left, self._smooth_right

        strength = max(left, right)
        ttc = 1.0 / strength if strength > _EPS else math.inf
        direction = (right - left) / (right + left) if (right + left) > _EPS else 0.0
        return LoomingInfo(strength, ttc, left, right, direction, self._smooth_cells.copy(), self._cell_angles,
                           self._smooth_flow.copy())


def at_intended_speed(info: LoomingInfo | None, actual: float, intended: float, turn: float,
                      cfg: Config = CONFIG) -> LoomingInfo | None:
    """루밍을 '사용자가 원한 속도로 갔다면 보였을 값'으로 바꾼다 (자기 움직임 신호, efference copy).

    루밍(확장률)은 '얼마나 빨리 다가오나'라서 속도에 비례한다. 공유 제어가 속도를 줄이면 루밍도 줄어 위험도가 정지 기준
    아래로 내려가고, 그러면 느린 속도로 기어가다 결국 부딪힌다. 속도를 얼마나 줄였는지는 로봇이 알고 있으므로
    측정한 루밍에 intended/actual을 곱해 되돌린다. actual이 아주 작을 때 잡음이 커지지 않게 최대 looming_speed_cap배까지만
    키우고, 그 사이 세게 돌았으면(turn > looming_speed_turn_limit) 되돌리지 않는다 — 회전 보정 뒤에 남은 작은 잡음까지 키워
    잠깐의 회피가 정지로 번지기 때문이다.
    actual·intended·turn은 이전 프레임과 지금 프레임 사이에 실제로 낸 속도, 사용자가 원한 속도(0~1, 보는 방향으로), 실제 회전이다.
    """
    if (info is None or intended <= 0.0 or actual <= 0.0 or actual >= intended
            or abs(turn) > cfg.looming_speed_turn_limit):
        return info
    factor = min(intended / actual, cfg.looming_speed_cap)
    cells = None if info.cells is None else info.cells * factor
    flow = None if info.flow is None else info.flow * factor  # 옆 흐름도 속도에 비례한다
    left, right = info.left * factor, info.right * factor
    strength = max(left, right)
    return dataclasses.replace(info, strength=strength, time_to_contact=1.0 / strength if strength > _EPS else math.inf,
                               left=left, right=right, cells=cells, flow=flow)


def for_braking(info: LoomingInfo | None, speed: float, cfg: Config = CONFIG) -> LoomingInfo | None:
    """감속 한계(cfg.brake_decel)가 있으면 루밍을 '서는 데 걸리는 만큼 일찍' 보이게 키운다.

    루밍(확장률)은 1/충돌까지 시간이다. 지금 보정은 바로 서는 조건에서 충돌까지 looming_brake_ttc_s(τ0)가 남았을 때 멈추게
    맞춰져 있다. speed(m/s)에서 brake_decel로 서려면 그 속도로 v/(2a)초 가는 만큼 더 미끄러지므로, τ0 + v/(2a)에서 같은
    위험도가 나오게 루밍을 (τ0 + v/2a)/τ0배(최대 looming_brake_cap) 한다. 거리 센서가 정지 거리에 제동 거리를 더하는 것과 같다.
    옆 흐름(HS 회로 — 옆벽 피하기)은 멈추는 판단이 아니라 그대로 둔다. 감속 한계가 없거나(0) 서 있으면 그대로 돌려준다.
    """
    if info is None or cfg.brake_decel <= 0.0 or speed <= 0.0:
        return info
    factor = min(1.0 + speed / (2.0 * cfg.brake_decel * max(cfg.looming_brake_ttc_s, 1e-3)), cfg.looming_brake_cap)
    cells = None if info.cells is None else info.cells * factor
    left, right = info.left * factor, info.right * factor
    strength = max(left, right)
    return dataclasses.replace(info, strength=strength, time_to_contact=1.0 / strength if strength > _EPS else math.inf,
                               left=left, right=right, cells=cells)
