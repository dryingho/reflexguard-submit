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

from typing import Any

import numpy as np

from common.config import CONFIG, Config
from common.control import ControlCommand
from connectome.brain import MaleCNSBrain
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
        self.looming: LoomingInfo | None = None   # 마지막 영상의 루밍 (보정 뒤 — 커넥톰이 받은 그대로)
        self.decision: Any = None                 # 마지막 커넥톰 판단
        self.cell_inputs: np.ndarray | None = None    # 칸별 커넥톰(LPLC2) 입력 0~1
        self.cell_filtered: np.ndarray | None = None  # 커지고는 있지만 휠체어 통로 밖이라 거른 칸
        self.override: ControlCommand | None = None   # 마지막 스텝에 공유 제어가 바꾼 명령 (안 바꿨으면 None)
        self._reset_motion()
        self._speed = 0.0
        self._user_forward = self._applied_forward = self._applied_turn = 0.0

    def _reset_motion(self) -> None:
        self._turn_deg = self._distance = self._elapsed = 0.0

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
        self.detector = LoomingDetector(self.cfg)
        self.ranger.reset()

    def moved(self, turn_deg: float, speed: float, dt: float, user: ControlCommand, applied: ControlCommand) -> None:
        """지난 스텝에 몸이 돈 각도(도, +시계 방향)·앞으로 간 속도(m/s)와 그때의 사용자·적용 명령 (바퀴 엔코더·IMU)."""
        self._turn_deg += turn_deg
        self._distance += speed * dt
        self._elapsed += dt
        self._speed = speed
        self._user_forward, self._applied_forward, self._applied_turn = user.forward, applied.forward, applied.turn

    def see(self, frame: np.ndarray, dt: float | None = None) -> Any:
        """새 카메라 영상(H×W×3 RGB) → ① 루밍 → 보정 → ② 커넥톰. 판단(BrainDecision 또는 None)을 돌려준다.
        dt: 직전 영상과의 간격(초). 비우면 moved()로 모은 시간(처음이면 카메라 간격)."""
        cfg = self.cfg
        frame_dt = dt if dt is not None else (self._elapsed or cfg.camera_period_ms / 1000.0)
        looming = self.detector.update(frame, frame_dt, self._turn_deg, self._distance / frame_dt)
        return self._think(looming, frame_dt)

    def see_gray(self, gray: np.ndarray, frame_shape: tuple[int, int], dt: float | None = None) -> Any:
        """see()와 같되 이미 줄인 밝기 영상(녹화 파일 형식 — LoomingDetector.shrink_frame)을 받는다."""
        cfg = self.cfg
        frame_dt = dt if dt is not None else (self._elapsed or cfg.camera_period_ms / 1000.0)
        looming = self.detector.update_gray(gray, frame_shape, frame_dt, self._turn_deg, self._distance / frame_dt)
        return self._think(looming, frame_dt)

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
        self._reset_motion()
        return self.decision

    def decide(self, user: ControlCommand, dt: float) -> ControlCommand:
        """③ 공유 제어: 사용자 명령 → 바퀴에 낼 명령 (위험할 때만 바꾼다). 커넥톰을 끈 동안은 판단 없음으로 본다."""
        decision = self.decision if self.connectome_on else None
        self.override = self.reflex.process(user, decision, dt)
        return user if self.override is None else self.override
