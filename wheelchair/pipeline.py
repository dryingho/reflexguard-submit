"""휠체어 PC의 한 사이클: 새 카메라 영상 → ① 루밍 연산 → 보정 → ② 커넥톰, 그리고 스텝마다 → ③ 공유 제어.

Webots 컨트롤러(simulation/webots/controllers/reflexguard/reflexguard.py), 도착 시험(tools/goal_trials.py),
단독 실행(wheelchair/run.py — 영상 파일·웹캠)이 모두 이것을 쓴다. 카메라와 바퀴가 무엇이든 넣는 것은 같다.

    pipe = WheelchairPipeline(cfg)                     # 커넥톰은 기기 안 (연산 서버면 brain=RemoteBrain(...))
    매 스텝:
        if 새 영상: pipe.see(frame)                    # ① 루밍 → 속도·감속 보정 → ② 커넥톰 (직전 영상 이후 움직임을 쓴다)
        (거리 센서로 위험을 인식하면: if 새 측정: pipe.sense_range(ranges, offsets) — 같은 꼴의 입력으로 같은 커넥톰)
        command = pipe.decide(user_command, dt)        # ③ 공유 제어 → 바퀴에 낼 명령
        ... 바퀴를 움직인다 ...
        pipe.moved(turn_deg, speed, dt, user_command, command)   # 바퀴(엔코더·IMU)가 알려 준 이번 스텝의 움직임

영상은 스텝보다 드물게 와도 된다(camera_period_ms). 그 사이에는 직전 판단을 그대로 쓰고, 움직임은 모아 둔다.
커넥톰 입력 센서(sensor): camera(카메라 루밍, 기본) / range(거리 센서 — wheelchair/range_sensor.py). use_sensor()로 바꾼다.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np

from common.config import CONFIG, Config
from common.control import ControlCommand
from connectome.brain import MaleCNSBrain
from wheelchair.looming.calibration import CameraCalibrator
from wheelchair.looming.detector import LoomingDetector, LoomingInfo, at_intended_speed, for_braking
from wheelchair.range_sensor import RangeLooming
from wheelchair.shared_control import ReflexModule

FILTERED_INPUT = 0.15  # 위험도 겹쳐 보기: 거르기 전 입력이 이만큼 넘는데 통로 밖이라 거른 칸을 따로 보인다


class WheelchairPipeline:
    """루밍 연산 · 커넥톰 · 공유 제어를 묶은 휠체어 PC (모듈 설명)."""

    def __init__(self, cfg: Config = CONFIG, brain: Any = None) -> None:
        self.cfg = cfg
        self.detector = LoomingDetector(cfg)
        self.ranger = RangeLooming(cfg)
        self.sensor = cfg.reflex_sensor  # 커넥톰 입력: camera / range
        self.brain = brain if brain is not None else MaleCNSBrain(cfg)  # step(looming, dt_ms) → BrainDecision 또는 None
        self.reflex = ReflexModule(cfg)
        self.connectome_on = True  # 끄면 판단이 없는 상태 → 공유 제어가 안전 정지(안전 스위치가 켜져 있으면 수동 주행)
        self.speed_limit = 1.0     # 관제의 원격 속도 제한(0~1) — 공유 제어가 고른 명령에 마지막으로 건다 (개입으로 세지 않는다)
        self.calibrator = CameraCalibrator(cfg)  # 카메라 자기 보정 — 바꾼 값은 detector.cfg에 (self.cfg는 처음 값 그대로)
        self.camera_changes: dict[str, float] = {}  # 마지막 영상에서 바꾼 카메라 값 (take_camera_changes로 가져간다)
        self.looming: LoomingInfo | None = None   # 마지막 영상의 루밍 (보정 뒤 — 커넥톰이 받은 그대로)
        self.decision: Any = None                 # 마지막 커넥톰 판단
        self.cell_inputs: np.ndarray | None = None    # 칸별 커넥톰(LPLC2) 입력 0~1
        self.cell_filtered: np.ndarray | None = None  # 커지고는 있지만 휠체어 통로 밖이라 거른 칸
        self.override: ControlCommand | None = None   # 마지막 스텝에 공유 제어가 바꾼 명령 (안 바꿨으면 None)
        # 바퀴가 없을 때(휴대폰·영상 파일) 앞으로 가는 속도를 카메라로 짐작해 쓴다 (CameraCalibrator.speed_estimate — 바닥이
        # 퍼지는 빠르기). 짐작할 수 없으면 moved()로 받은 속도(걸음 수로 정한 값·설정값)를 쓴다
        self.camera_speed = False
        self.speed_used = 0.0  # 마지막 영상에서 루밍에 쓴 속도(m/s)
        self._reset_motion()
        self._speed = 0.0
        self._user_forward = self._applied_forward = self._applied_turn = 0.0
        # 센서 워치독 (sense_stale_s·sense_frozen_*): 마지막 판단 뒤 흐른 시간(moved로 더한다), 직전 영상, 같은 영상이 이어진 시간
        self._decision_age = 0.0
        self._last_view: np.ndarray | None = None
        self._frozen_s = 0.0
        self.sense_stale = ""  # 마지막 decide에서 판단을 버린 까닭: "" / "old"(판단이 오래됨) / "frozen"(영상이 멈춤)
        self._stop_base = (cfg.decode_stop_on, cfg.decode_stop_off)  # 처음 정지·해제 기준 (set_stop_threshold가 비율을 지킨다)

    def set_stop_threshold(self, stop_on: float) -> None:
        """정지 기준(위험도가 이 값 이상이면 정지)을 바꾼다 — 풀리는 기준도 처음 설정의 비율 그대로 따라간다 (휴대폰 체험의
        민감도 슬라이더). 디코더와 공유 제어가 같은 값을 본다. 커넥톰 입력·회피 방향 판단은 그대로."""
        base = self._stop_base
        cfg = dataclasses.replace(self.cfg, decode_stop_on=stop_on, decode_stop_off=stop_on * base[1] / base[0])
        self.cfg = cfg
        self.reflex.cfg = cfg
        decoder = getattr(self.brain, "decoder", None)
        if decoder is not None:
            decoder.cfg = cfg

    def _reset_motion(self) -> None:
        self._turn_deg = self._distance = self._elapsed = 0.0
        self._nod_deg = self._roll_deg = 0.0

    def reset(self) -> None:
        """처음부터 다시 (충돌·도착 뒤): 공유 제어 상태를 새로. 루밍·커넥톰은 이어서 본다."""
        self.reflex = ReflexModule(self.cfg)
        self.override = None

    def use_sensor(self, sensor: str) -> None:
        """커넥톰 입력 센서를 바꾼다 (camera / range). 두 쪽 모두 직전 측정을 잊는다 — 오래된 영상·거리와 비교해
        가짜 루밍이 생기지 않게. 커넥톰·공유 제어 상태는 그대로."""
        if sensor not in ("camera", "range"):
            raise ValueError(f"sensor must be camera or range, got {sensor!r}")
        self.sensor = sensor
        self.detector = LoomingDetector(self.detector.cfg)  # 자기 보정으로 맞춘 카메라 값은 그대로
        self.ranger.reset()

    def moved(self, turn_deg: float, speed: float, dt: float, user: ControlCommand, applied: ControlCommand,
              nod_deg: float = 0.0, roll_deg: float = 0.0) -> None:
        """지난 스텝에 몸이 돈 각도(도, +시계 방향)·앞으로 간 속도(m/s)와 그때의 사용자·적용 명령 (바퀴 엔코더·IMU).
        nod_deg·roll_deg: 카메라가 숙여진(+아래로)·기울어진(+시계 방향) 각도 — 세 축 자이로가 있으면 (들고 걷는 휴대폰)."""
        self._turn_deg += turn_deg
        self._nod_deg += nod_deg
        self._roll_deg += roll_deg
        self._distance += speed * dt
        self._elapsed += dt
        self._decision_age += dt
        self._speed = speed
        self._user_forward, self._applied_forward, self._applied_turn = user.forward, applied.forward, applied.turn

    def see(self, frame: np.ndarray, dt: float | None = None) -> Any:
        """새 카메라 영상(H×W×3 RGB) → ① 루밍 → 보정 → ② 커넥톰. 판단(BrainDecision 또는 None)을 돌려준다.
        dt: 직전 영상과의 간격(초). 비우면 moved()로 모은 시간(처음이면 카메라 간격)."""
        cfg = self.cfg
        frame_dt = dt if dt is not None else (self._elapsed or cfg.camera_period_ms / 1000.0)
        turn, speed = self._turn_deg, self._speed_now(self._distance / frame_dt)
        self._watch_frames(frame, frame_dt)
        looming = self.detector.update(frame, frame_dt, turn, speed, self._nod_deg, self._roll_deg)
        decision = self._think(looming, frame_dt)
        self._calibrate(frame_dt, turn, speed)
        return decision

    def see_gray(self, gray: np.ndarray, frame_shape: tuple[int, int], dt: float | None = None) -> Any:
        """see()와 같되 이미 줄인 밝기 영상(녹화 파일 형식 — LoomingDetector.shrink_frame)을 받는다."""
        cfg = self.cfg
        frame_dt = dt if dt is not None else (self._elapsed or cfg.camera_period_ms / 1000.0)
        turn, speed = self._turn_deg, self._speed_now(self._distance / frame_dt)
        self._watch_frames(gray, frame_dt)
        looming = self.detector.update_gray(gray, frame_shape, frame_dt, turn, speed, self._nod_deg, self._roll_deg)
        decision = self._think(looming, frame_dt)
        self._calibrate(frame_dt, turn, speed)
        return decision

    def _watch_frames(self, view: np.ndarray, frame_dt: float) -> None:
        """센서 워치독: 움직이는 중에 새 영상이 직전 영상과 한 화소도 다르지 않으면 그 시간을 센다 (멈춘 카메라). 다른 영상이
        와야만 지운다 — 서 있는 동안 지우면, 멈춘 카메라로 선 뒤 바로 다시 출발해 0.5초마다 섰다 갔다 했다."""
        last = self._last_view
        same = last is not None and last.shape == view.shape and np.array_equal(last, view)
        self._last_view = np.array(view, copy=True)
        if not same:
            self._frozen_s = 0.0
        elif abs(self._speed) > self.cfg.sense_frozen_speed:
            self._frozen_s += frame_dt

    def same_view(self, dt: float) -> None:
        """센서가 측정을 줬지만 직전과 같다 (Webots: 카메라 간격 사이의 스텝, 아무것도 움직이지 않는 장면 — NewFrames). 장면이
        그대로이니 직전 판단도 그대로 맞다 → 판단 나이를 되돌린다 (서 있는 동안 '판단이 오래됨'으로 서면 영상이 바뀌지 않아 영영
        출발하지 못한다). 움직이는 중이면 멈춘 영상으로 센다 — 움직이면 영상은 바뀌어야 한다."""
        if abs(self._speed) > self.cfg.sense_frozen_speed:
            self._frozen_s += dt
        self._decision_age = 0.0

    def _speed_now(self, given: float) -> float:
        """이번 영상에 쓸 속도: camera_speed면 카메라로 짐작한 속도(지난 영상까지로 잰 것), 모르면 받은 속도."""
        estimate = self.calibrator.speed_estimate if self.camera_speed else None
        self.speed_used = float(estimate) if estimate is not None else given
        return self.speed_used

    def _calibrate(self, dt: float, turn: float, speed: float) -> None:
        """카메라 자기 보정: 위험도가 낮고 개입이 없을 때(quiet)만 배우고, 바꿀 값이 생기면 루밍 감지기에 건다."""
        decision = self.decision
        signal = getattr(decision, "signal", None)
        quiet = (decision is not None and self.override is None
                 and float(getattr(decision, "escape", 1.0)) < self.cfg.decode_stop_off
                 and str(getattr(signal, "value", signal)) == "NONE")
        changes = self.calibrator.observe(self.detector, dt, turn, speed, quiet)
        changed, ground = self.calibrator.take_ground()
        if changed and ground is not None:  # 영상에서 잰 바닥 (설정값으로 계산한 바닥과 확실히 다를 때만)
            self.detector.set_ground_line(*ground)
        elif changed:
            self.detector.clear_ground_line()
        if changes:
            self.detector.recalibrate(**changes)
            self.camera_changes = {**self.camera_changes, **changes}

    def set_camera(self, **values: float) -> None:
        """다른 센서로 잰 카메라 값(예: 가속도계의 숙인 각 camera_pitch_deg)을 건다 — 파이프라인을 새로 만들지 않고."""
        changes = self.calibrator.measured(**values)
        if changes:
            self.detector.recalibrate(**changes)

    def take_camera_changes(self) -> dict[str, float]:
        """지난번 이후 자기 보정으로 바꾼 카메라 값 (알림·기록용). 가져가면 비운다."""
        changes, self.camera_changes = self.camera_changes, {}
        return changes

    def adopt_camera(self, other: "WheelchairPipeline") -> None:
        """다른(앞) 파이프라인이 배운 카메라 보정을 이어받는다 — 처음부터 다시 시작해도 다시 배우지 않게."""
        self.calibrator = other.calibrator
        changes = {name: value for name, value in other.calibrator.values.items()
                   if getattr(self.detector.cfg, name) != value}
        if changes:
            self.detector.recalibrate(**changes)

    def sense_range(self, ranges: np.ndarray, offsets: np.ndarray, dt: float | None = None) -> Any:
        """새 거리 측정(빛살마다 거리 m, 몸 기준 각도 라디안 + 오른쪽) → 같은 꼴의 루밍 → 보정 → ② 커넥톰."""
        cfg = self.cfg
        frame_dt = dt if dt is not None else (self._elapsed or cfg.camera_period_ms / 1000.0)
        looming = self.ranger.update(ranges, offsets, frame_dt, self._turn_deg, self._distance / frame_dt)
        return self._think(looming, frame_dt, self.ranger)

    def _think(self, looming: LoomingInfo, frame_dt: float, source: Any = None) -> Any:
        cfg = self.cfg
        # 공유 제어가 속도를 줄였으면 루밍도 줄어 있다 → '사용자가 원한 속도였다면'으로 되돌린다.
        # 낸 속도: 감속 한계가 있으면 명령이 아니라 실제로 가던 속도 (멈추라 해도 미끄러지는 동안은 가고 있다)
        actual = self._speed / cfg.move_speed if cfg.brake_decel > 0.0 else self._applied_forward
        looming = at_intended_speed(looming, max(actual, 0.0), max(self._user_forward, 0.0), self._applied_turn, cfg)
        # 감속 한계가 있으면 서는 데 걸리는 만큼 일찍 (원한 속도와 실제 속도 중 빠른 쪽에서 서야 한다)
        looming = for_braking(looming, max(self._speed, self._user_forward * cfg.move_speed), cfg)
        self.looming = looming
        norm = max(cfg.looming_norm, 1e-9)
        cells = getattr(looming, "cells", None)
        if cells is not None:  # 커넥톰이 받는 그대로 (MaleCNSBrain.step과 같은 정규화)
            self.cell_inputs = np.clip(np.asarray(cells, dtype=float) / norm, 0.0, 1.0)
            source = source if source is not None else self.detector
            measured, gate = source.last_measured, source.last_gate
            if measured is not None and gate is not None:
                self.cell_filtered = (measured / norm >= FILTERED_INPUT) & (gate < 0.5)
        self.decision = self.brain.step(looming, round(frame_dt * 1000)) if self.connectome_on else None
        self._decision_age = 0.0
        self._reset_motion()
        return self.decision

    def looming_view(self) -> dict[str, Any] | None:
        """관제 대시보드의 '루밍 동작 보기': 마지막 영상에서 칸마다 잰 확장률(1/초, 통로로 거르기 전)·통로 가중치(0~1)·잰 방식
        (0 진행 방향 중심, 1 제자리 확대, 2 옆으로 움직임)·가로 흐름(줄인 영상 화소/영상, 옆으로 움직이는 칸만, +오른쪽)·
        커넥톰 입력(0~1)·통로 밖이라 거른 칸, 확장률을 커넥톰 입력 0~1로 바꾸는 기준(looming_norm). 커넥톰이 이 기기 안에 있으면
        LPLC2·DNp01 발화율도(Circuit.view — 연산 서버의 커넥톰이면 서버가 대시보드로 따로 보낸다). 첫 영상 전이면 None."""
        source = self.ranger if self.sensor == "range" else self.detector
        measured, gate = source.last_measured, source.last_gate
        if measured is None or gate is None:
            return None
        kind = np.zeros(measured.shape, dtype=int)
        for value, name in ((1, "last_local"), (2, "last_moving")):
            flags = getattr(source, name, None)
            if flags is not None and flags.shape == kind.shape:
                kind[flags] = value
        shift = getattr(source, "last_cell_shift", None)
        shift = np.where(kind == 2, shift, 0.0) if shift is not None and shift.shape == kind.shape else np.zeros(kind.shape)
        inputs = self.cell_inputs if self.cell_inputs is not None and self.cell_inputs.shape == kind.shape else np.zeros(kind.shape)
        filtered = self.cell_filtered if self.cell_filtered is not None and self.cell_filtered.shape == kind.shape \
            else np.zeros(kind.shape, dtype=bool)
        view: dict[str, Any] = {"rows": int(kind.shape[0]), "cols": int(kind.shape[1]),
                                "input": [round(float(v), 2) for v in inputs.ravel()], "filtered": [int(v) for v in filtered.ravel()],
                                "rate": [round(float(v), 2) for v in np.nan_to_num(measured).ravel()],
                                "gate": [round(float(v), 2) for v in np.broadcast_to(gate, measured.shape).ravel()],
                                "kind": [int(v) for v in kind.ravel()], "shift": [round(float(v), 1) for v in shift.ravel()],
                                "norm": self.cfg.looming_norm}
        circuit = getattr(self.brain, "circuit", None)
        if circuit is not None and hasattr(circuit, "view") and self.connectome_on:
            view["neurons"] = circuit.view()
        return view

    def _stale(self) -> str:
        """센서 워치독: 마지막 판단을 버려야 하는 까닭 — "frozen"(움직이는데 영상이 멈춤), "old"(판단이 오래됨), 아니면 ""."""
        cfg = self.cfg
        if cfg.sense_frozen_s > 0.0 and self._frozen_s >= cfg.sense_frozen_s - 1e-9:
            return "frozen"
        if cfg.sense_stale_s > 0.0 and self.decision is not None and self._decision_age > cfg.sense_stale_s + 1e-9:
            return "old"
        return ""

    def decide(self, user: ControlCommand, dt: float) -> ControlCommand:
        """③ 공유 제어: 사용자 명령 → 바퀴에 낼 명령 (위험할 때만 바꾼다). 커넥톰을 끈 동안은 판단 없음으로 본다."""
        decision = self.decision if self.connectome_on else None
        self.sense_stale = self._stale()
        if self.sense_stale:
            decision = None  # 오래된 판단·멈춘 영상으로 낸 판단은 쓰지 않는다 → 판단 없음과 같은 안전 정지
        self.override = self.reflex.process(user, decision, dt)
        return (user if self.override is None else self.override).limited(self.speed_limit)
