"""카메라 자기 보정: 주행하며 카메라 값을 스스로 맞춘다 (화각 · 무늬 기준 · 잡음 바닥), 숙인 각은 중력으로.

루밍(다가옴) 측정은 배율 찾기라 밝기·흐림·프레임 간격에는 스스로 맞지만, 카메라의 기하(화각·숙인 각)와 잡음 수준은
설정값을 그대로 믿는다. 이 값이 실제와 다르면 회전 되돌리기·바닥 빼기·통로 거르기·칸 방향이 어긋난다
(휴대폰·새 카메라는 화각을 모르고, 손에 든 휴대폰은 숙인 각이 늘 바뀐다). 그래서 주행 중 측정으로 고친다.

- 화각: 거의 서서 돌 때(속도 ≤ 0.3 m/s, 한 영상에 1~8도), 수평선 띠가 옆으로 밀린 화소 s와 몸이 돈 각도 θ(바퀴·자이로)로
  초점거리 f = s·cos(숙인 각)/tan θ → 화각 = 2·atan(가로/2 ÷ f). 수평선 위 정면은 다가오는 것이 있어도 퍼지지 않는 점이라
  도는 몫만 남는다.
- 무늬 기준: 멈춰 있을 때 연속 영상 차이로 영상 잡음 σ를 재어 3σ (기본보다 낮추지 않는다 — 잡음을 무늬로 믿지 않게).
  변화가 적은 조각(낮은 쪽 25%)으로 재서, '서 있음'으로 알려 놓고 걸어도 움직임을 잡음으로 배우지 않는다.
- 잡음 바닥: 멈춰 있는데도 칸 대부분에 루밍이 늘 잡히면(깜박이는 조명 등) 습관화(looming_floor_margin)를 켠다.
- 숙인 각: 영상만으로는 높이와 맞바뀌어 믿을 수 없었다(Webots 녹화로 시작값에 따라 3~31도). 가속도계(중력 방향)가 있으면
  그 값을 measured()로 받는다 — 휴대폰은 조작 화면이 보낸다. 높이는 설정값 그대로.
- 바닥: 앞으로 갈 때(속도 > 0.3 m/s, 거의 곧장) 줄마다 바닥 빼기 전 확장률(정면 ±camera_autocal_ground_azimuth 안 칸의 중간값 —
  옆 벽은 빼고)에 '수평선 위 0, 아래 직선'
  s·max(h − v, 0)을 맞춘다 (v = 줄의 세로 위치 ÷ 초점거리, 수평선 위 줄은 거의 0이어야, 무릎 등이 가린 아래 줄은 빼 본다). 높이·숙인 각·속도를 따로 풀지 않고 이 두 값만 재므로 맞바뀌지
  않는다. 잘 맞은(R² ≥ camera_autocal_ground_fit) 최근 camera_autocal_ground_samples개의 중간값을 LoomingDetector.
  set_ground_line으로 건다. 바닥이 남아 위험도가 오른 때도 배워야 하므로 quiet를 기다리지 않는다 — 대신 정면 벽·차 옆면처럼
  줄마다 비슷한 것은 이 모양에 맞지 않아 걸러진다.
- 속도 (카메라로 짐작): 같은 바닥 맞춤의 기울기 s = 속도 × cos²(숙인 각) ÷ 높이이고 숙인 각은 수평선에서 나오므로, 높이(설정값)만
  가정하면 속도 = s × 높이 ÷ cos²(atan h). 바닥이 보이는데 전혀 퍼지지 않으면 서 있음(0). 짐작을 지수 평균으로 이어 가고,
  camera_speed_hold_s 넘게 잴 수 없으면(바닥에 무늬 없음 등) 모른다(None) — 바퀴가 없는 휴대폰·영상 파일이 쓴다
  (WheelchairPipeline.camera_speed).

안전: 위험도가 낮고 개입이 없을 때(quiet)만 배운다. 값마다 camera_autocal_samples개 장면의 중간값으로 정하고,
camera_autocal_*_range 안에서 camera_autocal_step만큼씩, camera_autocal_interval_s보다 드물게 바꾼다.
"""

from __future__ import annotations

import collections
import math
from typing import Any

import numpy as np

from common.config import CONFIG, Config

_EPS = 1e-9
FIELDS = ("fov_deg", "camera_pitch_deg", "camera_height", "looming_zoom_min_std", "looming_floor_margin")


class CameraCalibrator:
    """LoomingDetector의 측정(last_pair·last_measured)을 지켜보며 카메라 값을 추정하고, 바꿀 값을 돌려준다."""

    def __init__(self, cfg: Config = CONFIG) -> None:
        self.cfg = cfg
        self.values = {name: float(getattr(cfg, name)) for name in FIELDS}
        size = max(cfg.camera_autocal_samples * 10, 50)
        self._fov: collections.deque = collections.deque(maxlen=size)    # 장면마다 잰 화각(도)
        self._noise: collections.deque = collections.deque(maxlen=size)  # 멈춰 있을 때 영상 잡음 σ
        self._still: collections.deque = collections.deque(maxlen=size)  # 멈춰 있을 때 칸별 루밍의 중간값(1/초)
        self._since_change = math.inf
        self._since_sample = math.inf  # 지난 화각·잡음 관찰 뒤 지난 시간(초) — camera_autocal_sample_s
        self._user_floor = float(cfg.looming_floor_margin) > 0.0  # 사람이 정한 잡음 바닥은 건드리지 않는다
        self.pitch_source = "config"  # 숙인 각을 어디서: config(설정값) / gravity(가속도계)
        self._ground: collections.deque = collections.deque(maxlen=max(cfg.camera_autocal_ground_samples, 1))  # (기울기/속도, 수평선)
        self.ground: tuple[float, float] | None = None  # 지금 쓰는 잰 바닥 (1 m/s당 기울기, 수평선 v) — None이면 설정값으로 계산한 바닥
        self.ground_measured: tuple[float, float] | None = None  # 최근 잰 바닥 (쓰든 안 쓰든 — 화면·기록용)
        self._ground_changed = False
        self.speed_estimate: float | None = None  # 카메라로 짐작한 속도(m/s) — 모르면 None
        self._speed_age = math.inf                # 마지막으로 잰 뒤 지난 시간(초)

    # ----- 바깥에서 보는 값 -----
    def state(self) -> dict[str, Any]:
        """화면·기록용: 지금 쓰는 값과 배우는 정도 (장면 수 / 필요한 수)."""
        need = self.cfg.camera_autocal_samples
        return {"on": bool(self.cfg.camera_autocal), "fov": round(self.values["fov_deg"], 1),
                "pitch": round(self.values["camera_pitch_deg"], 1), "pitch_from": self.pitch_source,
                "height": round(self.values["camera_height"], 2),
                "min_std": round(self.values["looming_zoom_min_std"], 1),
                "floor": self.values["looming_floor_margin"] > 0.0,
                "samples": {"fov": min(len(self._fov), need), "noise": min(len(self._noise), need), "need": need},
                "ground": None if self.ground_measured is None else {
                    "slope": round(self.ground_measured[0], 3),
                    "horizon_deg": round(math.degrees(math.atan(self.ground_measured[1])), 1), "used": self.ground is not None}}

    def take_ground(self) -> tuple[bool, tuple[float, float] | None]:
        """(바뀌었나, 쓸 잰 바닥) — 바뀌었으면 잰 바닥은 LoomingDetector.set_ground_line에, None이면 clear_ground_line으로
        (설정값으로 계산한 바닥). 한 번 가져가면 '바뀌었나'는 거짓."""
        changed, self._ground_changed = self._ground_changed, False
        return changed, self.ground

    def measured(self, **values: float) -> dict[str, float]:
        """다른 센서로 잰 카메라 값(예: 가속도계의 숙인 각)을 받는다. 바뀐 값만 돌려준다 (루밍 감지기에 걸 것)."""
        changes = {}
        for name, value in values.items():
            if name not in FIELDS:
                raise ValueError(f"unknown camera value {name!r}")
            if abs(float(value) - self.values[name]) > 1e-9:
                changes[name] = float(value)
        if "camera_pitch_deg" in values:
            self.pitch_source = "gravity"
        self.values.update(changes)
        return changes

    # ----- 한 영상마다 -----
    def observe(self, detector: Any, dt: float, turn_deg: float, speed: float, quiet: bool) -> dict[str, float]:
        """이번 영상의 측정을 모으고, 바꿀 값이 있으면 {설정 이름: 새 값}을 돌려준다 (없으면 빈 dict).
        quiet: 위험도가 낮고 공유 제어가 개입하지 않음 — 이때만 배우고 바꾼다."""
        cfg = self.cfg
        self._since_change += max(dt, 0.0)
        rows = self._ground_rows(detector) if dt > 0.0 and abs(turn_deg) <= 1.0 else None
        self._observe_speed(rows, dt)
        if cfg.camera_autocal and cfg.camera_autocal_ground and rows is not None and speed > 0.3:
            self._observe_ground(rows, speed)  # quiet를 기다리지 않는다 (모듈 설명)
        if not cfg.camera_autocal or not quiet or dt <= 0.0:
            return {}
        pair = getattr(detector, "last_pair", None)
        if pair is None or getattr(detector, "_shape", None) is None:
            return {}
        prev, gray = pair
        turn = abs(turn_deg)
        self._since_sample += dt
        if self._since_sample >= cfg.camera_autocal_sample_s:
            if speed <= 0.3 and 1.0 <= turn <= 8.0:
                self._observe_rotation(detector, prev, gray, turn_deg)
                self._since_sample = 0.0
            elif speed <= 0.05 and turn <= 0.1:
                self._observe_still(detector, prev, gray)
                self._since_sample = 0.0
        if self._since_change < cfg.camera_autocal_interval_s:
            return {}
        changes = self._decide()
        if changes:
            self.values.update(changes)
            self._since_change = 0.0
        return changes

    def _observe_rotation(self, detector: Any, prev: np.ndarray, gray: np.ndarray, turn_deg: float) -> None:
        height, width = gray.shape
        pitch = math.radians(self.values["camera_pitch_deg"])
        focal = (width / 2.0) / math.tan(math.radians(self.values["fov_deg"]) / 2.0)
        horizon = int(round(height / 2.0 - focal * math.tan(pitch)))
        half = max(3, height // 10)
        top, bottom = max(0, horizon - half), min(height, horizon + half)
        if bottom - top < 3:
            return
        valid = getattr(detector, "_valid", None)
        rows = slice(top, bottom)
        usable = valid[rows].mean(axis=0) > 0.5 if valid is not None and valid.shape == gray.shape else np.ones(width, bool)
        fov = _rotation_fov(gray[rows].mean(axis=0), prev[rows].mean(axis=0), usable, pitch, math.radians(turn_deg),
                            self.cfg.camera_autocal_fov_range)
        if fov is not None:
            self._fov.append(fov)

    def _ground_rows(self, detector: Any) -> tuple[np.ndarray, np.ndarray] | None:
        """칸 줄마다 (세로 위치 v ÷ 초점거리, 바닥 빼기 전 확장률 — 정면 ±camera_autocal_ground_azimuth 안 믿은 칸의 분위, 없으면 NaN)."""
        raw = getattr(detector, "last_zoom_raw", None)
        shape = getattr(detector, "_shape", None)
        edges = getattr(detector, "_cell_edges", None)
        if raw is None or shape is None or edges is None:
            return None
        height, width = shape
        focal = (width / 2.0) / math.tan(math.radians(self.values["fov_deg"]) / 2.0)
        rows = edges[0]
        v = -(0.5 * (rows[:-1] + rows[1:]) - height / 2.0) / focal  # 칸 줄 가운데의 세로 위치 ÷ 초점거리 (+위)
        angles = getattr(detector, "_cell_angles", None)
        middle = (np.abs(angles[..., 0]) <= self.cfg.camera_autocal_ground_azimuth if angles is not None and angles.shape[:2] == raw.shape
                  else np.ones(raw.shape, dtype=bool))
        values = np.full(raw.shape[0], np.nan)
        for r in range(raw.shape[0]):
            cells = raw[r][middle[r] & np.isfinite(raw[r])]
            if len(cells) >= max(2, int(np.count_nonzero(middle[r])) // 2):
                values[r] = float(np.quantile(cells, self.cfg.camera_autocal_ground_quantile))
        return v, values

    def _observe_speed(self, rows: tuple[np.ndarray, np.ndarray] | None, dt: float) -> None:
        """카메라로 속도를 짐작한다 (모듈 설명). 잴 수 없는 장은 지난 짐작을 camera_speed_hold_s까지만 이어 간다."""
        cfg = self.cfg
        self._speed_age += max(dt, 0.0)
        measured = None
        if rows is not None:
            v, values = rows
            fit = _fit_ground(v, values, cfg.camera_autocal_ground_pitch, cfg.camera_autocal_ground_fit)
            if fit is not None:
                measured = fit[0] * self.values["camera_height"] / math.cos(math.atan(fit[1])) ** 2
            else:
                lower = values[(v < 0.0) & np.isfinite(values)]
                if len(lower) >= 2 and float(np.max(np.abs(lower))) < cfg.camera_speed_still:  # 바닥이 퍼지지 않는다 = 서 있음
                    measured = 0.0
        if measured is not None:
            measured = min(measured, cfg.camera_speed_max)
            self.speed_estimate = measured if self.speed_estimate is None else \
                self.speed_estimate + cfg.camera_speed_rate * (measured - self.speed_estimate)
            self._speed_age = 0.0
        elif self._speed_age > cfg.camera_speed_hold_s:
            self.speed_estimate = None

    def _observe_ground(self, rows: tuple[np.ndarray, np.ndarray], speed: float) -> None:
        v, values = rows
        fit = _fit_ground(v, values, self.cfg.camera_autocal_ground_pitch, self.cfg.camera_autocal_ground_fit)
        if fit is None:
            return
        slope, horizon = fit
        low, high = self.cfg.camera_autocal_ground_slope
        if not low <= slope / speed <= high:  # 말이 되는 카메라 높이가 아니다 (바닥이 거의 안 재였다 등)
            return
        self._ground.append((slope / speed, horizon))
        if len(self._ground) < self._ground.maxlen:
            return
        # 기울기·수평선은 서로 맞물려 흔들린다 (낮은 수평선 + 큰 기울기 ≈ 높은 수평선 + 작은 기울기) — 따로 중간값을 고르면 다른
        # 장면의 값끼리 짝지어진다. 최근 맞춤들이 그리는 줄마다 바닥 값의 중간값을 다시 맞춰 짝이 맞는 두 값을 얻는다
        profile = np.median([g[0] * np.maximum(g[1] - v, 0.0) for g in self._ground], axis=0)
        measured = _fit_hinge(v, profile, self.cfg.camera_autocal_ground_pitch, 0.0)
        if measured is None:
            return
        self.ground_measured = measured
        # 설정값 바닥과 확실히 다를 때만 쓴다 (아래쪽 칸 줄 — 가운데에서 화면 아래 끝 사이 — 에서 잰 바닥과 계산한 바닥의 상대 차이)
        pitch = math.radians(self.values["camera_pitch_deg"])
        theory = (math.cos(pitch) ** 2 / max(self.values["camera_height"], _EPS), math.tan(pitch))
        lower = v[v < 0.0]
        if not len(lower):
            return
        mine = measured[0] * np.maximum(measured[1] - lower, 0.0)
        calc = theory[0] * np.maximum(theory[1] - lower, 0.0)
        differ = float(np.mean(np.abs(mine - calc)) / max(float(np.mean(calc)), _EPS))
        switch_on, switch_off = self.cfg.camera_autocal_ground_differ
        use = differ >= switch_on or (self.ground is not None and differ >= switch_off)
        new = measured if use else None
        if new != self.ground:
            self.ground = new
            self._ground_changed = True

    def _observe_still(self, detector: Any, prev: np.ndarray, gray: np.ndarray) -> None:
        noise = _still_noise(gray - prev, getattr(detector, "_valid", None))
        if noise is not None:
            self._noise.append(noise)
        measured = getattr(detector, "last_measured", None)
        front = getattr(detector, "cell_front", None)
        if measured is not None and front is not None and np.any(front > 0):
            self._still.append(float(np.median(measured[front > 0])))

    # ----- 정하기 -----
    def _decide(self) -> dict[str, float]:
        cfg = self.cfg
        need = cfg.camera_autocal_samples
        step_fov, step_std = cfg.camera_autocal_step
        changes: dict[str, float] = {}
        if len(self._fov) >= need:
            target = _clamp(float(np.median(self._fov)), cfg.camera_autocal_fov_range)
            changes.update(_toward("fov_deg", self.values["fov_deg"], target, step_fov, 2.0))
        if len(self._noise) >= need:
            target = _clamp(3.0 * float(np.median(self._noise)), cfg.camera_autocal_min_std_range)
            changes.update(_toward("looming_zoom_min_std", self.values["looming_zoom_min_std"], target, step_std, 1.0))
        if len(self._still) >= need and not self._user_floor:
            noisy = float(np.median(self._still)) > cfg.camera_autocal_floor_level
            margin = cfg.camera_autocal_floor_margin if noisy else 0.0
            if margin != self.values["looming_floor_margin"]:
                changes["looming_floor_margin"] = margin
        return changes


def change_fields(changes: dict[str, float]) -> dict[str, Any]:
    """바뀐 카메라 값 → 사건 칸 (짧은 이름): fov·pitch·height·min_std·floor(켬/끔)."""
    names = {"fov_deg": "fov", "camera_pitch_deg": "pitch", "camera_height": "height", "looming_zoom_min_std": "min_std"}
    fields: dict[str, Any] = {names[name]: round(value, 1) for name, value in changes.items() if name in names}
    if "looming_floor_margin" in changes:
        fields["floor"] = changes["looming_floor_margin"] > 0.0
    return fields


def _still_noise(difference: np.ndarray, valid: np.ndarray | None, block: int = 8) -> float | None:
    """연속 영상 차이로 한 영상의 잡음 σ. 화면을 block×block 조각으로 나눠 조각마다 σ(중간 절댓값 ÷ 0.6745 ÷ √2)를 재고,
    낮은 쪽 25%의 값을 쓴다 — 정말 멈춘 카메라는 모든 조각이 잡음 수준이라 같고, '서 있음'으로 알려 놓고 걷거나 사람이
    지나가도 움직임이 거의 없는 조각(하늘·밋밋한 바닥)만 쓰게 되어 움직임을 잡음으로 배우지 않는다."""
    height, width = difference.shape
    rows, cols = height // block, width // block
    if rows * cols < 8:
        return None
    blocks = np.abs(difference[:rows * block, :cols * block]).reshape(rows, block, cols, block).transpose(0, 2, 1, 3)
    blocks = blocks.reshape(rows * cols, block * block)
    if valid is not None and valid.shape == difference.shape:
        keep = valid[:rows * block, :cols * block].reshape(rows, block, cols, block).min(axis=(1, 3)).ravel() > 0.5
        blocks = blocks[keep]
    if blocks.shape[0] < 8:
        return None
    sigmas = np.median(blocks, axis=1) / 0.6745 / math.sqrt(2.0)
    # 순수 잡음에서 조각 64화소의 σ 추정값 중 낮은 25%는 참값의 약 0.90배 (가우스 잡음으로 잰 값) → 되돌린다
    return float(np.percentile(sigmas, 25)) / 0.90


def _fit_ground(v: np.ndarray, values: np.ndarray, pitch_range: tuple[float, float], min_fit: float,
                bottom: int = 2) -> tuple[float, float] | None:
    """줄마다 확장률(values, 없으면 NaN)에 s·max(h − v, 0)을 맞춘다 — 수평선 h = tan(숙인 각)을 1도씩 바꿔 보며 s는 최소제곱.
    화면 아래를 무릎·팔걸이·손에 든 것이 차지하면 그 줄은 퍼지지 않으므로 아래 줄을 0~bottom줄 빼 보며 처음으로 잘 맞는 것을 쓴다
    (그래도 안 맞으면 가장 어긋난 줄 하나를 더 빼 본다).
    잘 맞음: R² ≥ min_fit, 수평선 아래에 값 있는 줄이 셋 이상, 위에 하나 이상이고 위 줄들은 거의 0(아래 평균의 25% 이하 — 수평선
    위에는 바닥이 없다), s > 0. 맞으면 (s, h), 아니면 None."""
    known = np.flatnonzero(np.isfinite(values))  # 위 줄부터
    for cut in range(bottom + 1):
        rows = known[:len(known) - cut] if cut else known
        if len(rows) < 4:
            return None
        fit = _fit_hinge(v[rows], values[rows], pitch_range, min_fit)
        if fit is None:  # 가장 어긋난 줄 하나를 빼고 다시 (잡음 한 줄)
            rough = _fit_hinge(v[rows], values[rows], pitch_range, 0.0)
            if rough is not None:
                residual = np.abs(values[rows] - rough[0] * np.maximum(rough[1] - v[rows], 0.0))
                rest = np.delete(rows, int(np.argmax(residual)))
                fit = _fit_hinge(v[rest], values[rest], pitch_range, min_fit) if len(rest) >= 4 else None
        if fit is not None:
            return fit
    return None


def _fit_hinge(v: np.ndarray, y: np.ndarray, pitch_range: tuple[float, float], min_fit: float) -> tuple[float, float] | None:
    spread = float(np.sum((y - y.mean()) ** 2))
    if spread <= _EPS:
        return None
    best = None
    for pitch in np.arange(pitch_range[0], pitch_range[1] + 0.5, 1.0):
        horizon = math.tan(math.radians(pitch))
        x = np.maximum(horizon - v, 0.0)
        above = v > horizon
        if np.count_nonzero(x) < 3 or not np.any(above):  # 수평선이 화면 안에 있어야 (위에 값 있는 줄)
            continue
        if float(np.mean(np.abs(y[above]))) > 0.25 * max(float(np.mean(y[~above])), _EPS):  # 수평선 위는 거의 0이어야
            continue
        slope = float(x @ y) / max(float(x @ x), _EPS)
        if slope <= 0.0:
            continue
        error = float(np.sum((y - slope * x) ** 2))
        if best is None or error < best[0]:
            best = (error, slope, horizon)
    if best is None or 1.0 - best[0] / spread < min_fit:
        return None
    return best[1], best[2]


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return min(max(value, bounds[0]), bounds[1])


def _toward(name: str, current: float, target: float, step: float, dead: float) -> dict[str, float]:
    """target과 dead보다 많이 다르면 step만큼만 다가간 값."""
    gap = target - current
    if abs(gap) <= dead:
        return {}
    return {name: round(current + math.copysign(min(abs(gap), step), gap), 3)}


def _rotation_fov(now: np.ndarray, before: np.ndarray, usable: np.ndarray, pitch: float, theta: float,
                  bounds: tuple[float, float]) -> float | None:
    """수평선 띠의 밝기 단면이 몸이 theta(라디안, +오른쪽)만큼 도는 동안 before → now가 된 것에 가장 잘 맞는 화각(도).

    화각 후보마다 이전 단면을 그 화각의 원근대로 돌려 놓고(수평선의 가로각 a인 열 x = f·tan a / cos(숙인 각) — 돈 뒤
    지금 열 x는 전에 a + θ 방향이던 열에서 왔다) 지금 단면과 정규화 상관 비용을 잰다. 가운데 반 열만, 쓸 수 있는 열만 비교.
    가장 좋은 후보가 범위 끝이거나 다른 후보보다 뚜렷이 낫지 않으면 None (무늬 없는 벽·하늘)."""
    width = now.size
    left, right = width // 4, width - width // 4
    columns = np.arange(left, right) + 0.5 - width / 2.0
    mask = usable[left:right]
    if mask.sum() < 8:
        return None

    def normal(values: np.ndarray) -> np.ndarray:
        picked = values[mask]
        return (values - picked.mean()) / max(float(picked.std()), 1e-6)

    target = normal(now[left:right])
    grid = np.arange(bounds[0], bounds[1] + 1e-9, 1.0)
    costs = np.full(grid.size, np.inf)
    positions = np.arange(width) + 0.5 - width / 2.0
    for index, fov in enumerate(grid):
        focal = (width / 2.0) / math.tan(math.radians(fov) / 2.0)
        scale = focal / math.cos(pitch)
        angle = np.arctan(columns / scale) + theta
        if np.any(np.abs(angle) >= math.pi / 2.0 - 1e-3):
            continue
        source = scale * np.tan(angle)
        if source.min() < positions[0] or source.max() > positions[-1]:
            continue  # 돌려 놓은 열이 화면 밖 — 이 후보로는 비교할 수 없다
        costs[index] = float(np.abs(target - normal(np.interp(source, positions, before)))[mask].mean())
    finite = np.isfinite(costs)
    if finite.sum() < 5:
        return None
    best = int(np.argmin(costs))
    if best == 0 or best == grid.size - 1 or not (np.isfinite(costs[best - 1]) and np.isfinite(costs[best + 1])):
        return None
    typical = float(np.median(costs[finite]))
    if typical - costs[best] < 0.2 * max(typical, _EPS):
        return None
    c0, c1, c2 = costs[best - 1], costs[best], costs[best + 1]
    curvature = c0 - 2.0 * c1 + c2
    refine = 0.5 * (c0 - c2) / curvature if curvature > _EPS else 0.0
    return float(grid[best] + np.clip(refine, -0.5, 0.5))
