"""MaleCNS 축소 커넥톰(LPLC2→DNp01) LIF 뇌 (기획서 ② 커넥톰 기반 회피 반사).

본편 ReflexGuard(zip)의 brain_server/model.py(LIF 회로)와 decoder/decoder.py(히스테리시스 디코더)를
1단계 프로토타입에 맞춰 numpy만으로 이식했다. 실제 MaleCNS v1.0에서 뽑은 축소 회로
(LPLC2 185개 → DNp01 2개, 185간선, model_version "malecns-v1.0-lplc2-gf-lif-v1")를
`connectome/data/malecns-circuit.json`에서 읽어 1ms 단위 LIF 스파이킹으로 돌린다.

    칸별 루밍 → LPLC2마다 자기 수용장 안의 칸만 읽음 → LIF 스파이킹(커넥톰 연결 그대로) → DNp01(좌/우) 발화율
              → escape(위험도)·turn(회피 방향) → 히스테리시스 디코더 → STOP/LEFT/RIGHT/NONE

LPLC2마다 시야의 어디를 보는지(수용장 중심·퍼짐)는 `connectome/data/malecns-lplc2-rf.json`에서 읽는다. 같은 MaleCNS 데이터에서
기둥 뉴런 → T4/T5 → LPLC2 연결을 따라 뽑았다 (connectome/extract_malecns_lplc2_rf.py). 그래서 LPLC2들이 서로 다른 입력을 받고,
LPLC2 → DNp01 시냅스 수(위쪽을 보는 LPLC2일수록 많다)가 '어디서 다가오느냐에 따른 반응 세기'로 나타난다.
(칸별 루밍이 없는 입력 — 예: 테스트용 가짜 — 이면 예전처럼 왼쪽/오른쪽 LPLC2 전체에 같은 값을 넣는다.)

커넥톰 데이터 출처: HHMI Janelia·케임브리지대·MRC LMB·구글 리서치, MaleCNS v1.0, CC-BY-4.0.
(본편은 서버에서 mTLS로 돌리지만, 이 단계에서는 같은 계산을 기기 안에서 직접 수행한다.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

from common.config import CONFIG, Config

_CIRCUIT_PATH = Path(__file__).resolve().parent / "data" / "malecns-circuit.json"
_RF_PATH = Path(__file__).resolve().parent / "data" / "malecns-lplc2-rf.json"
_HS_PATH = Path(__file__).resolve().parent / "data" / "malecns-hs.json"


def _unit(azimuth_deg: np.ndarray, elevation_deg: np.ndarray) -> np.ndarray:
    """(가로각 +오른쪽, 세로각 +위) → 단위 벡터. 마지막 축이 (앞, 오른쪽, 위)."""
    az, el = np.radians(azimuth_deg), np.radians(elevation_deg)
    return np.stack([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)], axis=-1)


class Signal(str, Enum):
    """디코더가 내는 이산 신호 (기획서 표 7의 판단 기준)."""

    STOP = "STOP"   # 위험: 정지
    LEFT = "LEFT"   # 회피: 왼쪽으로
    RIGHT = "RIGHT"  # 회피: 오른쪽으로
    NONE = "NONE"   # 안전/주의


@dataclass(frozen=True)
class BrainDecision:
    """한 스텝의 커넥톰 판단 결과."""

    escape: float          # 위험도 0~1 (DNp01 좌/우 발화율의 큰 쪽)
    signal: Signal         # 히스테리시스 디코더 결과
    turn_left: float       # 왼쪽으로 돌라는 세기 0~1 (오른쪽에서 접근할 때 커진다)
    turn_right: float      # 오른쪽으로 돌라는 세기 0~1
    top_neurons: list[tuple[str, str, float]]  # (뉴런 id, 종류, 발화율) 상위 — 판단 근거(기획서 ④)
    model_version: str
    # HS 회로(옆 흐름 → HS → DNa02·DNp15): 왼쪽·오른쪽이 얼마나 가까운지 0~1 (가까운 벽일수록 그쪽 화면이 빨리 흐른다)
    near_left: float = 0.0
    near_right: float = 0.0


class Circuit:
    """축소 커넥톰의 LIF 시뮬레이션. 막전위·불응기 상태를 들고 매 스텝 dt_ms(ms)만큼 전진한다."""

    def __init__(self, cfg: Config = CONFIG) -> None:
        self.cfg = cfg
        data = json.loads(_CIRCUIT_PATH.read_text(encoding="utf-8"))
        self.model_version: str = data["model_version"]
        nodes, edges = data["nodes"], data["edges"]
        self.ids = [n["id"] for n in nodes]
        self.types = [n["cell_type"] for n in nodes]
        sides = [n["side"] for n in nodes]
        index = {nid: i for i, nid in enumerate(self.ids)}
        self.size = len(nodes)

        # 간선(LPLC2 → DNp01, 시냅스 수)로 연결 가중치 행렬을 만든다 (model.py와 동일)
        pre = np.array([index[e["source"]] for e in edges], dtype=np.int64)
        post = np.array([index[e["target"]] for e in edges], dtype=np.int64)
        count = np.array([e["synapses"] for e in edges], dtype=np.float64)
        weights = np.zeros((self.size, self.size))
        weights[post, pre] = count
        weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1.0)  # 들어오는 시냅스 합으로 정규화
        self.weights = weights * cfg.brain_synaptic_gain

        # 좌/우 루밍이 자극하는 LPLC2, 출력을 읽을 DNp01
        self.left_in = np.array([float(t == "LPLC2" and s == "L") for t, s in zip(self.types, sides)])
        self.right_in = np.array([float(t == "LPLC2" and s == "R") for t, s in zip(self.types, sides)])
        self.output = {s: i for i, (t, s) in enumerate(zip(self.types, sides)) if t == "DNp01"}

        # LPLC2 수용장: 로봇 기준 방향(가로각 +오른쪽, 세로각 +위)과 퍼짐(도). 겹눈 앞뒤각은 눈의 맨 앞 기둥에서 잰 값이라,
        # 앞쪽 겹침만큼 빼고 오른쪽 눈은 +, 왼쪽 눈은 -로 바꾼다.
        rf = {n["id"]: n for n in json.loads(_RF_PATH.read_text(encoding="utf-8"))["neurons"]}
        self.rf_index = np.array([i for i, t in enumerate(self.types) if t == "LPLC2" and self.ids[i] in rf], dtype=np.int64)
        neurons = [rf[self.ids[i]] for i in self.rf_index]
        sign = np.array([1.0 if n["side"] == "R" else -1.0 for n in neurons])
        azimuth = sign * (np.array([n["azimuth_deg"] for n in neurons]) - cfg.lplc2_eye_overlap_deg)
        elevation = np.array([n["elevation_deg"] for n in neurons])
        self.rf_direction = _unit(azimuth, elevation)                                     # (LPLC2 수, 3)
        self.rf_sigma = np.radians(np.maximum([n["spread_deg"] for n in neurons], 5.0))   # (LPLC2 수,)
        self.rf_azimuth, self.rf_elevation = azimuth, elevation
        self._rf_weights: dict[bytes, np.ndarray] = {}
        self.reset()

    def receptive_field_weights(self, cell_angles: np.ndarray) -> np.ndarray:
        """(LPLC2 수, 칸 수) 가중치: LPLC2가 각 칸의 루밍을 얼마나 받는지.

        칸 방향은 카메라 기준 [가로각(+오른쪽), 세로각(+위)]이고, 카메라 정면이 이 뇌의 정면이다.
        가중치 = 가우스 수용장 × 칸이 차지하는 시야 넓이 / 수용장 전체 넓이 —
        수용장 전체가 같은 루밍으로 덮이면 합이 1, 일부만 카메라에 보이면 보이는 만큼만 받는다.
        """
        key = cell_angles.tobytes()
        if key not in self._rf_weights:
            horizontal, vertical = cell_angles[..., 0], cell_angles[..., 1]
            directions = _unit(horizontal, vertical).reshape(-1, 3)                       # (칸 수, 3)
            # 칸 하나가 차지하는 시야 넓이(라디안²): 이웃 칸 가운데 사이 간격
            dx = np.abs(np.gradient(horizontal, axis=1)) if horizontal.shape[1] > 1 else np.full(horizontal.shape, 10.0)
            dy = np.abs(np.gradient(vertical, axis=0)) if vertical.shape[0] > 1 else np.full(vertical.shape, 10.0)
            area = (np.radians(dx) * np.radians(dy) * np.cos(np.radians(vertical))).reshape(-1)
            angle = np.arccos(np.clip(self.rf_direction @ directions.T, -1.0, 1.0))    # (LPLC2 수, 칸 수)
            sigma = self.rf_sigma[:, None]
            self._rf_weights[key] = np.exp(-0.5 * (angle / sigma) ** 2) * area[None, :] / (2.0 * np.pi * sigma ** 2)
        return self._rf_weights[key]

    def reset(self) -> None:
        self.voltage = np.zeros(self.size)
        self.refractory = np.zeros(self.size)
        self.spikes = np.zeros(self.size)
        self.rates = np.zeros(self.size)  # 발화율(Hz): brain_rate_window_ms 시간창의 지수 이동 평균

    def step(self, left_looming: float, right_looming: float, dt_ms: int) -> tuple[float, float, np.ndarray]:
        """좌/우 루밍(0~1)으로 왼쪽·오른쪽 LPLC2 전체를 같은 세기로 자극한다 (칸별 루밍이 없을 때)."""
        drive = self.cfg.brain_input_gain * (self.left_in * left_looming + self.right_in * right_looming)
        return self.run(drive, dt_ms)

    def step_cells(self, cells: np.ndarray, cell_angles: np.ndarray, dt_ms: int) -> tuple[float, float, np.ndarray]:
        """칸별 루밍(0~1)을 LPLC2마다 자기 수용장 안의 칸만큼 받아 자극한다."""
        drive = np.zeros(self.size)
        drive[self.rf_index] = self.cfg.brain_input_gain * (
            self.receptive_field_weights(cell_angles) @ np.asarray(cells, dtype=np.float64).reshape(-1))
        return self.run(drive, dt_ms)

    def run(self, drive: np.ndarray, dt_ms: int) -> tuple[float, float, np.ndarray]:
        """뉴런별 입력 전류로 dt_ms 동안 LIF를 돌리고, DNp01 좌/우 정규화 발화율과 전체 발화율을 돌려준다.

        발화율은 '이번 dt_ms 동안 몇 번 발화했나'로 세지 않고, 1ms마다 갱신하는 고정 시간창(brain_rate_window_ms)의
        이동 평균으로 잰다. 프레임 단위로 세면 짧은 프레임(예: 17 ms)에서는 발화 한두 번 차이로 값이 크게 뛰어
        (0 → 59 → 118Hz) 같은 상황에서도 프레임 간격에 따라 정지 신호가 나오거나 안 나왔다.
        """
        cfg = self.cfg
        window = max(cfg.brain_rate_window_ms, 1.0)
        for _ in range(max(1, int(dt_ms))):
            current = drive + self.weights @ self.spikes
            self.voltage = self.voltage + (current - self.voltage) / cfg.brain_tau_ms
            active = self.refractory <= 0
            self.voltage = self.voltage * active
            spike = (self.voltage >= 1.0).astype(np.float64)
            self.refractory = np.maximum(self.refractory - 1.0, 0.0) * (1.0 - spike) + cfg.brain_refractory_ms * spike
            self.voltage = self.voltage * (1.0 - spike)
            self.spikes = spike
            self.rates += (spike * 1000.0 - self.rates) / window  # 1ms마다: 발화하면 1000Hz 쪽으로, 아니면 0 쪽으로
        rates = self.rates.copy()  # 발화율(Hz)
        left = min(1.0, float(rates[self.output["L"]]) / cfg.brain_output_rate_hz)
        right = min(1.0, float(rates[self.output["R"]]) / cfg.brain_output_rate_hz)
        return left, right, rates

    def top_neurons(self, rates: np.ndarray, limit: int = 10) -> list[tuple[str, str, float]]:
        """발화율 상위 뉴런 목록 (판단 근거 기록용)."""
        order = sorted(range(self.size), key=lambda i: (-float(rates[i]), i))[:limit]
        return [(self.ids[i], self.types[i], float(rates[i])) for i in order if rates[i] > 0.0]


class HSCircuit:
    """HS 세포(HSN·HSE·HSS, 좌우): 자기 수용장의 앞→뒤 흐름을 모아, 하행 뉴런(DNa02·DNp15)으로 보내는 만큼 '그쪽이 가깝다'를 낸다.

    수용장은 MaleCNS에서 뽑은 지도(T4a·T5a 입력이 시야의 어디에 몰려 있는지, 10도 칸)를 카메라 칸에 옮긴 것이다
    (connectome/data/malecns-hs.json, connectome/extract_malecns_hs.py). 카메라는 정면 ±45도라 수용장의 14~28%만 보이므로, 보이는 부분 안에서
    가중 평균한다. HS는 활동 전위 없이 막전위가 오르내리는 세포라 LIF 대신 연속값(앞→뒤 흐름 / hs_flow_norm_deg, -1~1)으로 둔다.
    하행 뉴런 출력 = 같은 쪽 HS들의 (+) 활동을 HS → DNa02·DNp15 시냅스 수로 가중 평균 (0~1).
    한계: HS ↔ 하행 뉴런은 상당 부분 전기 시냅스인데 커넥톰에는 화학 시냅스만 있다. '가까운 쪽에서 멀어지는 방향'으로 쓰는 것은
    이 프로젝트의 선택이다 (초파리의 HS 경로는 원래 몸이 돌 때 따라 돌아 시야를 안정시키는 데 쓰인다).
    """

    def __init__(self, cfg: Config = CONFIG) -> None:
        self.cfg = cfg
        data = json.loads(_HS_PATH.read_text(encoding="utf-8"))
        self.neurons = data["neurons"]
        self.sides = np.array([1.0 if n["side"] == "R" else -1.0 for n in self.neurons])  # 오른쪽 눈 +1
        self.to_dn = np.array([float(sum(n["to_dn"].values())) for n in self.neurons])
        self._weights: dict[bytes, np.ndarray] = {}
        self.activity = np.zeros(len(self.neurons))

    def field_weights(self, cell_angles: np.ndarray) -> np.ndarray:
        """(HS 수, 칸 수): HS마다 카메라 칸의 흐름을 얼마나 받는지 (보이는 부분 안에서 합 1)."""
        key = cell_angles.tobytes()
        if key not in self._weights:
            horizontal = cell_angles[..., 0].reshape(-1)
            vertical = cell_angles[..., 1].reshape(-1)
            sigma = max(self.cfg.hs_rf_sigma_deg, 1.0)
            weights = np.zeros((len(self.neurons), horizontal.size))
            for index, (neuron, side) in enumerate(zip(self.neurons, self.sides)):
                for azimuth, elevation, share in neuron["rf_bins"]:
                    cam_h = side * (azimuth - self.cfg.lplc2_eye_overlap_deg)  # 눈 기준(앞 끝에서 뒤로) → 카메라 기준(+오른쪽)
                    distance2 = (horizontal - cam_h) ** 2 + (vertical - elevation) ** 2
                    weights[index] += share * np.exp(-0.5 * distance2 / sigma ** 2)
            total = weights.sum(axis=1, keepdims=True)
            self._weights[key] = np.divide(weights, total, out=np.zeros_like(weights), where=total > 1e-9)
        return self._weights[key]

    def step(self, flow: np.ndarray | None, cell_angles: np.ndarray | None) -> tuple[float, float]:
        """칸별 가로 흐름(도/초, +오른쪽) → (왼쪽 가까움, 오른쪽 가까움) 0~1."""
        if flow is None or cell_angles is None:
            self.activity[:] = 0.0
            return 0.0, 0.0
        # 앞→뒤 흐름: 오른쪽 눈은 오른쪽으로, 왼쪽 눈은 왼쪽으로 흐르는 것
        progressive = self.field_weights(cell_angles) @ np.asarray(flow, dtype=np.float64).reshape(-1) * self.sides
        self.activity = np.clip(progressive / max(self.cfg.hs_flow_norm_deg, 1e-9), -1.0, 1.0)
        drive = np.maximum(self.activity, 0.0) * self.to_dn
        near = {}
        for label, sign in (("L", -1.0), ("R", 1.0)):
            mask = self.sides == sign
            near[label] = float(drive[mask].sum() / max(self.to_dn[mask].sum(), 1e-9))
        return near["L"], near["R"]

    def top(self) -> list[tuple[str, str, float]]:
        """판단 근거용: HS 활동(-100~100)."""
        return [(n["id"], f"{n['type']}_{n['side']}", round(float(a) * 100.0, 1))
                for n, a in zip(self.neurons, self.activity) if abs(a) > 0.01]


class Decoder:
    """DNp01 출력을 STOP/LEFT/RIGHT/NONE로 바꾼다. 히스테리시스로 떨림을 막는다 (decoder.py 이식)."""

    def __init__(self, cfg: Config = CONFIG) -> None:
        self.cfg = cfg
        self.signal = Signal.NONE

    def update(self, escape: float, turn_left: float, turn_right: float) -> Signal:
        c = self.cfg
        if escape >= c.decode_stop_on or (self.signal == Signal.STOP and escape > c.decode_stop_off):
            self.signal = Signal.STOP
            return self.signal
        difference = turn_left - turn_right
        if difference >= c.decode_turn_on:
            self.signal = Signal.LEFT
        elif difference <= -c.decode_turn_on:
            self.signal = Signal.RIGHT
        elif self.signal == Signal.LEFT and difference > c.decode_turn_off:
            pass  # 왼쪽 회피 유지
        elif self.signal == Signal.RIGHT and difference < -c.decode_turn_off:
            pass  # 오른쪽 회피 유지
        else:
            self.signal = Signal.NONE
        return self.signal


class MaleCNSBrain:
    """루밍 → 커넥톰(LIF) → 디코더를 묶은 뇌. ReflexModule이 이 결과(위험도·신호)로 공유 제어한다.

    들어오는 카메라의 정면이 이 뇌의 정면이다. 후진용 뇌(뒤 카메라)는 '뒤를 바라보는 두 번째 초파리'처럼 뒤 카메라 영상을
    자기 정면 시야로 받는다 — 후진할 때는 뒤가 진행 방향이고, 공유 제어도 '뒤를 앞으로 본' 기준으로 판단한다.
    (초파리 한 마리의 눈에 앞·뒤 카메라를 함께 붙이면 바로 뒤가 사각지대라 후진 중 뒤의 벽을 거의 보지 못한다.)
    """

    def __init__(self, cfg: Config = CONFIG) -> None:
        self.cfg = cfg
        self.circuit = Circuit(cfg)
        self.decoder = Decoder(cfg)
        self.hs = HSCircuit(cfg)  # 옆 흐름 → HS → DNa02·DNp15: 어느 쪽이 가까운지

    def step(self, looming: Any, dt_ms: int) -> BrainDecision:
        """루밍을 0~1로 정규화해 LPLC2를 자극하고, 커넥톰 판단을 돌려준다.

        칸별 루밍(LoomingInfo.cells)이 있으면 LPLC2마다 자기 수용장 안의 칸을 읽는다. 없으면 좌/우 값 하나씩으로 자극한다.
        """
        norm = max(self.cfg.looming_norm, 1e-9)
        cells = getattr(looming, "cells", None)
        angles = getattr(looming, "cell_angles", None)
        if cells is not None and angles is not None:
            inputs = np.clip(np.asarray(cells, dtype=np.float64) / norm, 0.0, 1.0)
            left_dn, right_dn, rates = self.circuit.step_cells(inputs, angles, dt_ms)
        else:
            left_in = 0.0 if looming is None else min(1.0, max(0.0, looming.left / norm))
            right_in = 0.0 if looming is None else min(1.0, max(0.0, looming.right / norm))
            left_dn, right_dn, rates = self.circuit.step(left_in, right_in, dt_ms)
        escape = max(left_dn, right_dn)
        # 오른쪽 LPLC2(오른쪽 접근)가 세면 왼쪽으로 돌아야 한다 → turn_left는 오른쪽 출력에 대응
        turn_left, turn_right = right_dn, left_dn
        signal = self.decoder.update(escape, turn_left, turn_right)
        near_left, near_right = self.hs.step(getattr(looming, "flow", None), angles)
        return BrainDecision(escape, signal, turn_left, turn_right,
                             self.circuit.top_neurons(rates), self.circuit.model_version, near_left, near_right)

    def reset(self) -> None:
        self.circuit.reset()
        self.decoder.signal = Signal.NONE
        self.hs.activity[:] = 0.0
