"""공유 제어 (휠체어 PC): 사용자 명령 + 커넥톰 판단 → 실제로 바퀴에 낼 명령 (감속·회피·정지·복귀·옆벽 피하기).

기획서의 ③ 공유 제어(표 7). 평소에는 사용자가 운전하고, 커넥톰(connectome.brain)이 위험하다고 할 때만 개입한다.
거리 센서는 쓰지 않고 커넥톰 출력(위험도·회피 방향·옆벽 가까움)만으로 정한다 — 단계별 규칙은 아래 ReflexModule 설명.

    command = ControlCommand(forward=…, turn=…)        # 사용자 명령 (키보드·조이스틱)
    override = ReflexModule(cfg).process(command, decision, dt)   # decision: MaleCNSBrain.step()의 판단 (없으면 None)
    applied = command if override is None else override
"""

from __future__ import annotations

import math
from typing import Any

from common.config import CONFIG, Config
from common.control import ControlCommand

_EPS = 1e-9


class ReflexModule:
    """공유 제어: 커넥톰 판단(BrainDecision)만으로 감속·회피·정지한다. 기획서 표 7의 위험 단계를 따른다.

    거리 센서(벽까지의 거리)는 쓰지 않는다. 개입할지, 얼마나 줄일지, 어느 쪽으로 돌지 모두 커넥톰 출력으로 정한다.

        안전: 위험도(escape) < decode_stop_off, 디코더 신호 없음 → 개입 안 함(None), 사용자가 그대로 운전.
        주의: 위험도가 decode_stop_off 이상(신호 없음) → 위험도만큼 전진을 줄인다(decode_stop_on에서 0).
        회피: 디코더 신호 LEFT/RIGHT → 그쪽으로 돌며(reflex_avoid_turn) 감속한다. 속도는 최대 reflex_avoid_max_speed,
              위험도가 높을수록 reflex_avoid_min_speed까지 줄인다(_avoid_speed). 정지 신호라도 위험이 한쪽에서 오면
              멈추지 않고 같은 방식으로 비켜 간다(reflex_avoid_first, 회피 우선).
        위험: 디코더 신호 STOP → 멈춘다. 아직 위험한 동안은 회전까지 막고(아래), 위험이 사라지면 회전은 사용자 뜻대로 둬서
              스스로 돌아 나올 수 있게 한다.
              STOP을 낸 뒤에는 사용자가 전진을 놓거나, 전진을 누른 채 방향을 reflex_stop_release_turn_deg(20도)
              이상 돌릴 때까지 정지를 유지한다. 루밍은 움직여야 생기는 신호라 멈추면 위험도가 떨어져 STOP이
              풀리는데, 같은 방향으로 다시 출발하면 조금씩 다가가다 결국 부딪히기 때문이다.
              그리고 정지한 뒤 커넥톰이 아직 위험하다고 하는 동안(신호가 NONE이 아니거나 위험도가 decode_stop_off 이상)은
              조이스틱을 놓았다 다시 밀어도 전진·후진·회전을 모두 막는다(reflex_lock_until_safe, stage "stop", locked).
              위험이 사라진 뒤에야 위 규칙(전진을 놓거나 20도 돌리기)으로 운전 권한을 돌려준다.
    위험이 사라지면 None을 돌려줘 운전 권한을 사용자에게 되돌린다.
    이 모듈은 앞 카메라가 보는 방향으로 가는 움직임(전진)만 판단하고, 후진·정지 중에는 개입하지 않는다
    (후방 카메라가 없으므로 후진은 판단하지 않는다).

    뇌 판단이 없으면(brain=None: 커넥톰 연결 끊김·장애) 안전 정지한다(fail-safe): 전진·후진·평행이동·회전을 모두 막는다.
    단, manual_without_brain(웹 화면의 '안전 스위치')을 켜면 판단이 없을 때 개입하지 않고 사용자 명령 그대로 둔다
    (수동 주행, stage "manual"). 판단이 있을 때는 이 값과 무관하게 평소대로 동작한다.
    """

    def __init__(self, cfg: Config = CONFIG) -> None:
        self.cfg = cfg
        self.stage = "safe"   # 마지막 판단 단계(설명·기록용): safe / slow / avoid / return / stop / manual
        self.manual_without_brain = False  # True면 뇌 판단이 없을 때 막지 않고 수동 주행을 허용한다 (안전 스위치)
        self.holding_stop = False  # 커넥톰이 STOP을 낸 뒤, 사용자가 전진을 놓을 때까지 정지를 유지하는 중
        self.locked = False        # 정지한 뒤 아직 위험해서 모든 조작을 막는 중 (reflex_lock_until_safe)
        self.turned_while_stopped = 0.0  # 정지를 유지하는 동안 사용자가 돌린 각도(도, +시계 방향)
        self.avoid_dir = 0.0             # 정지 대신 비켜 가는 중이면 꺾는 방향(-1 왼쪽, +1 오른쪽), 아니면 0
        self.avoid_turned = 0.0          # 비켜 가며 지금까지 돈 각도(도)
        self.heading_offset = 0.0        # 반사가 꺾어서 원래 방향에서 벗어난 각도(도, +시계 방향) — 위험이 사라지면 되돌린다
        self.calm_s = 0.0                # 회피가 끝난 뒤 위험 없이 지난 시간(초)
        self.veering = False             # HS 회로가 한쪽 벽이 가깝다고 해서 반대쪽으로 떼어 놓는 중
        self.offset_by_veer = False      # 지금 heading_offset이 옆벽 피하기로 생긴 것인지 (되돌리기를 더 빨리 한다)
        self.veer_turned = 0.0           # 이번 옆벽 피하기에서 반사가 꺾은 각도(도, +시계 방향)

    def process(self, command: ControlCommand, brain: Any, dt: float) -> ControlCommand | None:
        """사용자 명령과 커넥톰 판단(BrainDecision)을 받아 대신 적용할 명령을 반환한다 (안전하면 None).

        판단(_decide) 뒤에, 비켜 가느라(회피·옆벽 피하기) 꺾은 만큼을 기억해 두었다가 위험이 사라지면 원래 방향으로
        되돌린다(stage "return"). 옆벽 피하기 뒤에는 reflex_veer_return_delay_s, 회피 뒤에는 reflex_return_delay_s를 기다린다.
        사용자가 직접 꺾거나, 전진을 놓거나, 멈추면 되돌리기를 그만둔다(사용자의 뜻이 우선).
        마지막으로 옆 흐름 속도 조절(_pace)로 가까운 것 옆을 지날 때 속도 상한을 둔다 (켜 두었을 때만).
        """
        return self._pace(command, brain, self._steer(command, brain, dt))

    def _pace(self, command: ControlCommand, brain: Any, out: ControlCommand | None) -> ControlCommand | None:
        """옆 흐름 속도 조절 (reflex_pace_*, 기본 꺼짐): HS 회로의 좌우 가까움 중 큰 쪽이 reflex_pace_near부터 reflex_pace_full까지
        오르는 동안 전진 상한을 1에서 reflex_pace_min_speed까지 낮춘다 (stage "slow").

        꿀벌·파리는 좁고 복잡한 곳에서 옆으로 흐르는 시야(광학 흐름)가 빨라지면 스스로 느려진다 — 가까운 것일수록 빨리 흐르므로
        가까운 기둥·모퉁이 옆을 지날 때 저절로 느려진다. 감속 한계가 있으면 가려진 곳(기둥 뒤·모퉁이)에서 튀어나오는 사람을
        보는 순간 이미 서는 데 필요한 거리 안이기 쉬운데, 그런 곳을 미리 느리게 지나가게 한다.
        흐름은 '원한 속도였다면'으로 되돌린 값(at_intended_speed)이라, 느려져도 가까움이 줄지 않아 오르내리지 않는다."""
        cfg = self.cfg
        if (cfg.reflex_pace_near <= 0.0 or brain is None or command.forward <= 0.0
                or self.stage in ("stop", "manual")):
            return out
        nearest = max(float(getattr(brain, "near_left", 0.0)), float(getattr(brain, "near_right", 0.0)))
        span = max(cfg.reflex_pace_full - cfg.reflex_pace_near, _EPS)
        share = min(max((nearest - cfg.reflex_pace_near) / span, 0.0), 1.0)
        cap = command.forward * (1.0 - (1.0 - cfg.reflex_pace_min_speed) * share)
        base = out or command
        if base.forward <= cap:
            return out
        if self.stage == "safe":
            self.stage = "slow"
        return ControlCommand(forward=cap, turn=base.turn, strafe=base.strafe)

    def _steer(self, command: ControlCommand, brain: Any, dt: float) -> ControlCommand | None:
        """감속·회피·정지(_decide) + 옆벽 피하기(_keep_off_walls) + 방향 되돌리기 (process 설명)."""
        out = self._keep_off_walls(command, brain, self._decide(command, brain, dt), dt)
        cfg = self.cfg
        if not cfg.reflex_return_heading or brain is None or command.forward <= 0.0 or self.stage == "stop":
            self.heading_offset, self.calm_s = 0.0, 0.0
            return out
        if self.stage in ("avoid", "veer"):  # 반사가 꺾은 부분(사용자가 꺾은 것은 빼고)을 기억한다. 떼어 놓는 중에는 되돌리지 않는다
            if self.heading_offset == 0.0 or self.stage == "avoid":
                self.offset_by_veer = self.stage == "veer"
            self.heading_offset += (out.turn - command.turn) * cfg.turn_speed_deg * dt
            self.calm_s = 0.0
            return out
        if abs(command.turn) > 0.1:  # 사용자가 직접 방향을 잡는다 → 되돌리지 않는다
            self.heading_offset, self.calm_s = 0.0, 0.0
            return out
        if abs(self.heading_offset) < 0.5:
            self.heading_offset = 0.0
            return out
        self.calm_s += dt
        if self.calm_s < (cfg.reflex_veer_return_delay_s if self.offset_by_veer else cfg.reflex_return_delay_s):
            return out
        base = out or command                    # 감속 중이면 감속한 속도 그대로
        step_deg = cfg.reflex_return_turn * cfg.turn_speed_deg * dt
        if step_deg >= abs(self.heading_offset):  # 마지막 조각: 딱 남은 만큼만 돈다
            turn = -self.heading_offset / max(cfg.turn_speed_deg * dt, _EPS)
            self.heading_offset = 0.0
        else:
            turn = -math.copysign(cfg.reflex_return_turn, self.heading_offset)
            self.heading_offset += turn * cfg.turn_speed_deg * dt
        self.stage = "return"
        return ControlCommand(forward=base.forward, turn=turn, strafe=base.strafe)

    def _near_side(self, brain: Any) -> float:
        """HS 회로(옆 흐름)로 본 '피할 방향': 왼쪽이 뚜렷이 가까우면 +1(오른쪽으로), 오른쪽이면 -1, 모르면 0."""
        cfg = self.cfg
        near_left = float(getattr(brain, "near_left", 0.0))
        near_right = float(getattr(brain, "near_right", 0.0))
        if max(near_left, near_right) < cfg.reflex_near_min or abs(near_left - near_right) < cfg.reflex_near_margin:
            return 0.0
        return 1.0 if near_left > near_right else -1.0

    def _keep_off_walls(self, command: ControlCommand, brain: Any, out: ControlCommand | None,
                        dt: float = 0.0) -> ControlCommand | None:
        """옆벽 피하기 (stage "veer"): HS 회로가 한쪽이 가깝다고 하면 반대쪽으로 살짝 꺾고 조금 감속한다.

        LPLC2 → DNp01은 다가오는 것에 반응해서, 나란히 붙어 달리는 벽은 거의 보지 못한다(스치는 충돌). HS 회로는 옆 흐름으로
        그것을 본다. 한쪽이 뚜렷이(reflex_near_margin 이상) 더 가까울 때만 켠다 — 양쪽이 비슷하게 가까운 좁은 복도 가운데서는
        꺾을 쪽이 없는데 속도만 줄였다. 정지·회피 중이면 그대로 둔다.
        """
        cfg = self.cfg
        if brain is None or command.forward <= 0.0 or self.stage not in ("safe", "slow"):
            self.veering, self.veer_turned = False, 0.0
            return out
        near_left = float(getattr(brain, "near_left", 0.0))
        near_right = float(getattr(brain, "near_right", 0.0))
        nearest = max(near_left, near_right)
        lopsided = abs(near_left - near_right) >= (cfg.reflex_near_margin * 0.5 if self.veering else cfg.reflex_near_margin)
        self.veering = lopsided and nearest >= (cfg.reflex_near_off if self.veering else cfg.reflex_near_on)
        if not self.veering:
            self.veer_turned = 0.0
            return out
        strength = min(1.0, (nearest - cfg.reflex_near_off) / max(cfg.reflex_near_full - cfg.reflex_near_off, _EPS))
        bias = cfg.reflex_veer_turn * max(0.0, strength) * (near_left - near_right) / max(nearest, _EPS)
        # 한 번에 꺾는 총량은 reflex_veer_max_deg까지 — 그 뒤로는 그 방향을 유지하며 비스듬히 벽에서 멀어진다
        # (계속 꺾으면 좁은 복도에서 반대쪽 벽으로 향했다). 되돌아 꺾는 쪽은 막지 않는다
        if abs(self.veer_turned) >= cfg.reflex_veer_max_deg and bias * self.veer_turned > 0.0:
            bias = 0.0
        self.veer_turned += bias * cfg.turn_speed_deg * dt
        base = out or command
        self.stage = "veer"
        return ControlCommand(forward=base.forward * cfg.reflex_veer_speed,
                              turn=max(-1.0, min(1.0, base.turn + bias)), strafe=base.strafe)

    def _decide(self, command: ControlCommand, brain: Any, dt: float) -> ControlCommand | None:
        """커넥톰 판단만으로 정하는 개입: 안전 / 감속 / 회피 / 정지 (위 클래스 설명)."""
        cfg = self.cfg
        if brain is None:  # 뇌 판단이 없다(커넥톰 연결 끊김·장애)
            self.holding_stop = False
            if self.manual_without_brain:  # 안전 스위치: 개입하지 않고 사용자 명령 그대로 (수동 주행)
                self.stage = "manual"
                return None
            # 안전 정지(fail-safe): 판단 없이는 어떤 움직임도 허용하지 않는다 (전진·후진·평행이동·회전 모두)
            self.stage = "stop"
            moving = command.forward != 0.0 or command.turn != 0.0 or command.strafe != 0.0
            return ControlCommand() if moving else None
        # 정지한 뒤 아직 위험하다 → 조이스틱을 놓았다 다시 밀어도, 후진·회전도 막는다 (위험이 사라질 때까지)
        self.locked = cfg.reflex_lock_until_safe and self.holding_stop and self._dangerous(brain)
        if self.locked:
            self.stage = "stop"
            self.avoid_dir = 0.0
            return ControlCommand()
        if command.forward <= 0.0:
            self.stage = "safe"
            self.holding_stop = False        # 전진을 놓았다 → 정지 유지를 푼다
            self.avoid_dir = 0.0
            return None

        escape = float(brain.escape)         # 커넥톰 위험도 0~1
        signal = brain.signal                # "STOP"/"LEFT"/"RIGHT"/"NONE" (str Enum)
        if cfg.reflex_avoid_first and not self.holding_stop and escape < cfg.reflex_hard_stop:
            if signal == "STOP" and self.avoid_dir == 0.0:
                go_left = float(getattr(brain, "turn_left", 0.0))   # 오른쪽에서 다가올수록 커진다 → 왼쪽으로 피하라
                go_right = float(getattr(brain, "turn_right", 0.0))
                lean = (go_left - go_right) / max(go_left + go_right, _EPS)
                away = self._near_side(brain)  # HS 회로가 한쪽 벽이 가깝다고 하면 그 반대쪽이 먼저 (붙은 벽 쪽으로 꺾지 않게)
                if away != 0.0:
                    self.avoid_dir, self.avoid_turned = away, 0.0
                elif abs(lean) >= cfg.reflex_avoid_side_ratio:  # 위험이 한쪽에서 온다 → 멈추지 않고 반대쪽으로 비켜 간다
                    self.avoid_dir, self.avoid_turned = (-1.0 if lean > 0 else 1.0), 0.0
            if self.avoid_dir != 0.0:
                # 충분히 돌 때까지(reflex_avoid_hold_deg) 이어서 꺾는다. 사용자가 같은 쪽으로 더 세게 꺾으면 그 세기로
                turn = self.avoid_dir * max(cfg.reflex_avoid_turn, self.avoid_dir * command.turn)
                self.avoid_turned += abs(turn) * cfg.turn_speed_deg * dt
                if self.avoid_turned >= cfg.reflex_avoid_hold_deg and signal != "STOP":
                    self.avoid_dir = 0.0                 # 다 돌았고 정면 위험도 풀렸다 → 아래 평소 규칙으로
                else:
                    self.stage = "avoid"
                    return ControlCommand(forward=command.forward * self._avoid_speed(escape), turn=turn,
                                          strafe=command.strafe)
        elif escape >= cfg.reflex_hard_stop:
            self.avoid_dir = 0.0                         # 너무 가깝다 → 비켜 가기를 멈추고 정지
        if signal == "STOP":
            if not self.holding_stop:
                self.turned_while_stopped = 0.0
            self.holding_stop = True
        elif self.holding_stop and cfg.reflex_stop_release_turn_deg > 0.0:
            # 정지한 채 방향을 충분히 돌렸으면(그리고 커넥톰이 더는 STOP이 아니면) W를 떼지 않아도 다시 출발한다
            self.turned_while_stopped += command.turn * cfg.turn_speed_deg * dt
            if abs(self.turned_while_stopped) >= cfg.reflex_stop_release_turn_deg:
                self.holding_stop = False
        if self.holding_stop:
            self.stage = "stop"
            if cfg.reflex_lock_until_safe and self._dangerous(brain):  # 막 멈춘 순간부터 — 위험한 동안은 돌지도 못한다
                self.locked = True
                return ControlCommand()
            return ControlCommand(forward=0.0, turn=command.turn, strafe=command.strafe)
        if signal == "NONE" and escape < cfg.decode_stop_off:
            self.stage = "safe"              # 위험하지 않다 → 운전 권한을 사용자에게
            return None

        # 위험도가 해제 기준이면 그대로, 정지 기준이면 0 — 그 사이는 비례해서 줄인다
        keep = 1.0 - (escape - cfg.decode_stop_off) / max(cfg.decode_stop_on - cfg.decode_stop_off, _EPS)
        keep = max(0.0, min(1.0, keep))
        if signal in ("LEFT", "RIGHT"):
            self.stage = "avoid"
            turn = -cfg.reflex_avoid_turn if signal == "LEFT" else cfg.reflex_avoid_turn
            away = self._near_side(brain)
            if away != 0.0:  # 옆벽이 뚜렷이 가까우면 그 반대쪽으로 (디코더가 붙은 벽 쪽을 가리키는 경우를 바로잡는다)
                turn = away * cfg.reflex_avoid_turn
            return ControlCommand(forward=command.forward * self._avoid_speed(escape), turn=turn, strafe=command.strafe)
        self.stage = "slow"
        return ControlCommand(forward=command.forward * keep, turn=command.turn, strafe=command.strafe)

    def _dangerous(self, brain: Any) -> bool:
        """커넥톰이 아직 위험하다고 하는지: 디코더 신호가 있거나(정지·회피) 위험도가 정지 해제 기준(decode_stop_off) 이상."""
        return brain.signal != "NONE" or float(brain.escape) >= self.cfg.decode_stop_off

    def _avoid_speed(self, escape: float) -> float:
        """비켜 가는 동안의 속도 비율: 위험도가 높을수록 느리게 (reflex_avoid_min_speed ~ reflex_avoid_max_speed)."""
        cfg = self.cfg
        ramp = 1.0 - (escape - cfg.decode_stop_off) / max(cfg.reflex_hard_stop - cfg.decode_stop_off, _EPS)
        return max(cfg.reflex_avoid_min_speed, min(cfg.reflex_avoid_max_speed, ramp))
