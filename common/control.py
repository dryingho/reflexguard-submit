"""제어 명령: 사용자(키보드·조이스틱) 명령과, 공유 제어가 바꿔 바퀴에 내는 명령의 공통 형식.

휠체어 PC(wheelchair.shared_control)·시뮬레이션(simulation.agent)·작동 기록(wheelchair.reflex_log)이 같이 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# 관제의 원격 속도 제한(%) — 관제 서버가 받는 값이자 휠체어 PC가 따르는 값 (100 = 제한 없음)
SPEED_LIMITS = ("100", "80", "60", "40", "20")


@dataclass(frozen=True)
class ControlCommand:
    """한 프레임의 제어 명령. 각 값은 -1.0 ~ +1.0 (0 = 입력 없음).

    키보드 입력을 에이전트에 바로 넣지 않고 이 객체를 거치게 해서,
    다음 단계의 '공유 제어'가 중간에서 감속·정지·회피 명령으로 바꿀 수 있게 한다.
    """

    forward: float = 0.0  # +1 전진 / -1 후진
    turn: float = 0.0     # +1 우회전 / -1 좌회전
    strafe: float = 0.0   # +1 오른쪽 / -1 왼쪽 평행이동

    def limited(self, cap: float) -> "ControlCommand":
        """이동(전진·후진·옆)의 크기를 cap(0~1)까지로. 회전은 그대로 — 막다른 곳에서 돌아 나올 수 있게."""
        cap = min(1.0, max(0.0, cap))
        if cap >= 1.0:
            return self
        return replace(self, forward=min(cap, max(-cap, self.forward)), strafe=min(cap, max(-cap, self.strafe)))
