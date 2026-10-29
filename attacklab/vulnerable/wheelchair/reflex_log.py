"""회피 반사 작동 기록: 루밍 → 커넥톰 → 공유 제어의 흐름을 한 스텝씩 묶고, 반사가 개입한 구간을 기록한다.

Webots 컨트롤러(run())와 시험 도구(tools/goal_trials.py)가 매 스텝 같은 방법으로 부른다.

    snapshot = flow_snapshot(t, agent, looming, brain, reflex_module, user_command, command, override is not None, cfg)
    events = monitor.update(snapshot)   # 이번 스텝에 생긴 사건(start/stage/end) — 화면 알림·콘솔 출력에 쓴다

작동(에피소드): 공유 제어가 사용자 명령을 바꾼 스텝(override가 None이 아님)에서 시작하고,
reflex_log_release_s 동안 개입이 없으면 끝난다. 짧게 끊겼다 다시 켜지는 것은 한 번으로 묶는다.

로그 파일: logs/reflex_<날짜-시각>.jsonl — 한 줄에 JSON 하나. 첫 작동이 생길 때 만든다(작동이 없으면 파일도 없다).
    session  세션 정보: 어디서 돌렸는지, 커넥톰 버전, 판단 기준값
    start    반사가 켜진 순간의 흐름 전체 (루밍 → 커넥톰 → 공유 제어, 위치, 사용자·적용 명령)
    stage    작동 도중 단계가 바뀐 순간 (예: slow → avoid)
    connectome  커넥톰 연결을 켜거나 끈 순간 (on: true/false). 끄면 판단 없이 안전 정지한다(모든 이동을 막음)
    safety      안전 스위치를 켜거나 끈 순간 (on). 켜면 판단이 없을 때 수동 주행을 허용한다
    collision   벽·장애물에 부딪힌 순간: 몇 번째인지, 무엇에(with: wall/adult/child/ball, object: 장애물 이름),
                무엇을 하다 부딪혔는지(cause), 위치, 사용자·적용 명령, 공유 제어 단계
    restart     충돌 뒤(또는 도착 뒤) 처음 위치에서 다시 시작한 순간 (simulation/collision.py)
    arrival     도착지에 닿은 순간: 몇 번째 도착인지, 걸린 시간·거리, 그 주행에서 커넥톰이 개입한 횟수와 단계별 수,
                그 전에 부딪혀 다시 한 시도 수 (simulation/goal.py)
    sample   작동 도중 reflex_log_sample_s마다 남기는 흐름 (그래프 그리기용)
    end      작동 요약: 지속 시간, 거친 단계, 최대 위험도, 디코더 신호
요약 보기: python tools/reflex_log_summary.py
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TextIO

from common.control import ControlCommand
from common.config import CONFIG, Config

DEFAULT_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"  # 프로젝트 폴더의 logs/
TOP_NEURONS = 3  # 기록·화면에 남기는 반응 상위 뉴런 수 (판단 근거, 기획서 ④)


def _round(value: float, digits: int) -> float | None:
    """JSON에 넣을 숫자. 무한대·NaN은 JSON이 표현하지 못하므로 None으로 바꾼다."""
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


def _command(command: ControlCommand) -> dict[str, float | None]:
    return {"forward": _round(command.forward, 2), "turn": _round(command.turn, 2), "strafe": _round(command.strafe, 2)}


def to_json(record: dict[str, Any]) -> str:
    """한 줄짜리 JSON (ASCII만 — 웹 조작 화면으로 보내는 메시지와 로그 파일에 같이 쓴다)."""
    return json.dumps(record, separators=(",", ":"), allow_nan=False)


def flow_snapshot(t: float, agent: Any, looming: Any, brain: Any, reflex: Any,
                  user: ControlCommand, applied: ControlCommand, active: bool, cfg: Config = CONFIG,
                  connectome: bool = True) -> dict[str, Any]:
    """한 스텝의 흐름(① 루밍 → ② 커넥톰 → ③ 공유 제어)을 JSON으로 보낼 수 있는 dict로 묶는다.

    looming·brain은 없을 수 있다(첫 프레임, 뇌 장애, 커넥톰 연결을 끈 경우). 그때는 0으로 채운다.
    connectome: 커넥톰 연결 스위치. False면 일부러 끊은 것(brain=None) — 장애로 판단이 없는 것과 구분해 적는다.
    reflex는 ReflexModule: 판단 단계(stage: safe/slow/avoid/stop/manual)를 읽는다.
    단계를 알려 주지 않는 반사 모듈이면 개입했을 때 "override", 아니면 "safe"로 적는다.
    """
    stage = getattr(reflex, "stage", None)
    if stage is None or (active and stage == "safe"):
        stage = "override" if active else "safe"
    norm = max(cfg.looming_norm, 1e-9)
    left = float(getattr(looming, "left", 0.0))
    right = float(getattr(looming, "right", 0.0))
    signal = getattr(brain, "signal", "NONE")
    top = getattr(brain, "top_neurons", None) or []
    return {
        "t": _round(t, 3),
        "pose": {"x": _round(agent.x, 3), "y": _round(agent.y, 3), "angle": _round(agent.angle_deg, 1)},
        "looming": {  # ① 카메라 영상 확장률(1/초)과, 그것을 0~1로 맞춘 LPLC2 입력
            "left": _round(left, 3),
            "right": _round(right, 3),
            "input_left": _round(min(1.0, max(0.0, left / norm)), 3),
            "input_right": _round(min(1.0, max(0.0, right / norm)), 3),
            "ttc": _round(getattr(looming, "time_to_contact", math.inf), 2),
        },
        "brain": {  # ② 커넥톰 LIF(DNp01 발화율) → 위험도·회피 방향 → 디코더 신호
            "ok": brain is not None,          # 이번 스텝에 커넥톰 판단이 있었는지
            "connected": bool(connectome),    # 커넥톰 연결 스위치 (False면 판단을 일부러 끊은 상태)
            "model": getattr(brain, "model_version", None),
            "escape": _round(getattr(brain, "escape", 0.0), 3),
            "turn_left": _round(getattr(brain, "turn_left", 0.0), 3),
            "turn_right": _round(getattr(brain, "turn_right", 0.0), 3),
            "near_left": _round(getattr(brain, "near_left", 0.0), 3),    # HS 회로: 왼쪽이 얼마나 가까운지 0~1
            "near_right": _round(getattr(brain, "near_right", 0.0), 3),
            "signal": str(getattr(signal, "value", signal)),
            "top": [[str(nid), str(kind), _round(rate, 0)] for nid, kind, rate in list(top)[:TOP_NEURONS]],
        },
        "reflex": {"stage": stage, "active": bool(active),  # ③ 공유 제어
                   "locked": bool(getattr(reflex, "locked", False))},  # 정지한 뒤 아직 위험해서 모든 조작을 막는 중
        "user": _command(user),
        "applied": _command(applied),
    }


class ReflexMonitor:
    """반사 작동(에피소드)을 찾아 사건(start/stage/end)을 돌려주고, 로그 파일에 남긴다.

    log_dir=None이면 파일은 쓰지 않고 사건만 돌려준다. 파일을 쓰다 오류가 나도 주행은 멈추지 않는다(기록만 끈다).
    """

    def __init__(self, cfg: Config = CONFIG, log_dir: Path | None = DEFAULT_LOG_DIR,
                 meta: dict[str, Any] | None = None, wall_clock: Callable[[], datetime] = datetime.now) -> None:
        self.cfg = cfg
        self.log_dir = log_dir
        self.meta = dict(meta or {})
        self.wall_clock = wall_clock
        self.path: Path | None = None   # 만든 로그 파일 (첫 작동 전에는 None)
        self.error: str | None = None   # 로그 파일을 쓰지 못한 이유 (있으면 그 뒤로 파일은 쓰지 않는다)
        self.episode = 0                # 지금까지 시작된 작동 수 (진행 중이면 그 번호)
        self.in_episode = False
        self._file: TextIO | None = None
        self._reset_episode()

    def _reset_episode(self) -> None:
        self._start_t = 0.0
        self._last_active_t = 0.0
        self._last_stage = ""
        self._stages: list[str] = []
        self._signals: list[str] = []
        self._peak_escape = 0.0
        self._no_brain = False  # 커넥톰 판단 없이(연결 끊김·장애) 개입한 스텝이 있었는지
        self._next_sample_t = 0.0

    def update(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """한 스텝의 흐름을 반영한다. 이번 스텝에 생긴 사건들(start/stage/end)을 돌려준다(sample은 파일에만)."""
        t = float(snapshot["t"])
        active = bool(snapshot["reflex"]["active"])
        stage = str(snapshot["reflex"]["stage"])
        events: list[dict[str, Any]] = []

        if active:
            if not self.in_episode:
                self.episode += 1
                self.in_episode = True
                self._reset_episode()
                self._start_t = t
                self._next_sample_t = t + self.cfg.reflex_log_sample_s
                events.append(self._event("start", snapshot))
            elif stage != self._last_stage:
                events.append(self._event("stage", snapshot))
            if not self._stages or self._stages[-1] != stage:
                self._stages.append(stage)
            self._last_stage = stage
            self._last_active_t = t
            self._collect(snapshot)
        elif self.in_episode and t - self._last_active_t >= self.cfg.reflex_log_release_s:
            events.append(self._end(t, interrupted=False))

        for event in events:
            self._write(event)
        if self.in_episode and not events and t >= self._next_sample_t:
            self._write(self._event("sample", snapshot))
            self._next_sample_t = t + self.cfg.reflex_log_sample_s
        return events

    def note(self, kind: str, t: float, **fields: Any) -> dict[str, Any]:
        """작동이 아닌 사건(예: 커넥톰 연결 켜기·끄기)을 기록에 남기고 그 기록을 돌려준다."""
        record = {"type": kind, "time": self._now(), "t": _round(t, 3), **fields}
        self._write(record)
        return record

    def end_episode(self, collision: bool = False) -> list[dict[str, Any]]:
        """진행 중이던 작동을 지금 닫는다(예: 벽에 부딪혀 처음부터 다시 시작). 닫은 end 사건을 돌려준다."""
        if not self.in_episode:
            return []
        event = self._end(self._last_active_t, interrupted=False, collision=collision)
        self._write(event)
        return [event]

    def close(self) -> list[dict[str, Any]]:
        """기록을 마친다. 진행 중이던 작동이 있으면 '중간에 끝남'으로 닫는다. 그 사건을 돌려준다."""
        events = []
        if self.in_episode:
            events.append(self._end(self._last_active_t, interrupted=True))
            self._write(events[-1])
        if self._file is not None:
            self._file.close()
            self._file = None
        return events

    def _collect(self, snapshot: dict[str, Any]) -> None:
        """작동 중인 스텝의 값으로 요약(최대 위험도·디코더 신호)을 갱신한다."""
        escape = snapshot["brain"]["escape"] or 0.0
        self._peak_escape = max(self._peak_escape, escape)
        if not snapshot["brain"]["ok"]:
            self._no_brain = True
        signal = snapshot["brain"]["signal"]
        if signal != "NONE" and signal not in self._signals:
            self._signals.append(signal)

    def _event(self, kind: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {"type": kind, "episode": self.episode, "time": self._now(), **snapshot}

    def _end(self, t: float, interrupted: bool, collision: bool = False) -> dict[str, Any]:
        self.in_episode = False
        return {
            "type": "end",
            "episode": self.episode,
            "time": self._now(),
            "t": _round(t, 3),
            "start_t": _round(self._start_t, 3),
            "duration": _round(self._last_active_t - self._start_t, 3),
            "stages": list(self._stages),
            "signals": list(self._signals),
            "peak_escape": _round(self._peak_escape, 3),
            "no_brain": self._no_brain,  # 커넥톰 판단 없이 안전 정지한 적이 있음
            "interrupted": interrupted,  # 작동 도중 프로그램이 끝남
            "collision": collision,      # 작동 도중 벽에 부딪혀 처음부터 다시 시작함
        }

    def _now(self) -> str:
        return self.wall_clock().isoformat(timespec="milliseconds")

    def _write(self, record: dict[str, Any]) -> None:
        if self.log_dir is None or self.error is not None:
            return
        try:
            if self._file is None:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                stamp = self.wall_clock().strftime("%Y%m%d-%H%M%S")
                self.path = self.log_dir / f"reflex_{stamp}.jsonl"
                # 줄 단위로 바로 써서, 프로그램이 갑자기 꺼져도 그때까지의 기록은 남는다
                self._file = self.path.open("a", encoding="utf-8", buffering=1)
                self._file.write(to_json(self._session()) + "\n")
            self._file.write(to_json(record) + "\n")
        except OSError as error:
            self.error = str(error)
            self._file = None

    def _session(self) -> dict[str, Any]:
        return {"type": "session", "time": self._now(), **self.meta, "thresholds": thresholds(self.cfg)}


def thresholds(cfg: Config = CONFIG) -> dict[str, float]:
    """흐름 값을 해석할 때 필요한 판단 기준값 (로그의 session 기록, 웹 화면의 기준선 표시에 쓴다)."""
    return {
        "looming_norm": cfg.looming_norm,
        "decode_stop_on": cfg.decode_stop_on,
        "decode_stop_off": cfg.decode_stop_off,
        "decode_turn_on": cfg.decode_turn_on,
        "reflex_avoid_turn": cfg.reflex_avoid_turn,
        "reflex_log_release_s": cfg.reflex_log_release_s,
    }


def describe_event(event: dict[str, Any]) -> str:
    """사건 한 줄 설명 (콘솔 출력용. Webots 콘솔의 글자 깨짐을 피하려고 영문·숫자만 쓴다)."""
    if event["type"] == "collision":
        pose = event["pose"]
        target = event.get("with", "wall") + (f" {event['object']}" if event.get("object") else "")
        return (f"COLLISION #{event['count']} with {target} ({event['cause']}, stage {event['stage']}, "
                f"at x={pose['x']:.2f} y={pose['y']:.2f}) -> restart in {event['restart_in']:.1f}s")
    if event["type"] == "restart":
        if event.get("after") == "arrival":
            return "restart from the start position (after arriving at the goal)"
        return f"restart from the start position (after collision #{event['count']})"
    if event["type"] == "connectome":
        return ("connectome on" if event["on"]
                else "connectome off: no brain decision -> fail-safe stop (all movement) unless the safety switch is on")
    if event["type"] == "safety":
        return ("safety switch on: manual driving allowed without a brain decision" if event["on"]
                else "safety switch off: no movement without a brain decision")
    if event["type"] == "estop":
        return "EMERGENCY STOP from the dashboard" if event["on"] else "emergency stop released"
    if event["type"] == "map":
        return f"loading map {event['to']}"
    if event["type"] == "sensor":
        return ("danger sensing: range sensor -> connectome decides" if event["to"] == "range"
                else "danger sensing: camera looming -> connectome decides")
    if event["type"] == "arrival":
        stages = ", ".join(f"{stage} {n}" for stage, n in event["by_stage"].items() if n) or "none"
        return (f"ARRIVED at the goal #{event['count']}: {event['elapsed']:.1f}s, {event['distance']:.1f} m, "
                f"connectome interventions {event['interventions']} ({stages}), "
                f"failed attempts before {event['failed_attempts']} -> restart in {event['restart_in']:.1f}s")
    number = event["episode"]
    if event["type"] == "end":
        stages = " -> ".join(event["stages"]) or "-"
        signals = ",".join(event["signals"]) or "NONE"
        tail = ((" (no brain decision)" if event.get("no_brain") else "")
                + (" (collision)" if event.get("collision") else "")
                + (" (interrupted)" if event.get("interrupted") else ""))
        return (f"reflex #{number} end: {event['duration']:.2f}s, stages {stages}, signals {signals}, "
                f"peak escape {event['peak_escape']:.2f}{tail}")
    brain, reflex, applied = event["brain"], event["reflex"], event["applied"]
    verb = "start" if event["type"] == "start" else "stage"
    return (f"reflex #{number} {verb}: {reflex['stage']} (signal {brain['signal']}, escape {brain['escape']:.2f}, "
            f"applied forward {applied['forward']:+.2f} turn {applied['turn']:+.2f})")
