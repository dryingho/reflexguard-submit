"""제어 명령: 사용자(키보드·조이스틱) 명령과, 공유 제어가 바꿔 바퀴에 내는 명령의 공통 형식.

휠체어 PC(wheelchair.shared_control)·시뮬레이션(simulation.agent)·작동 기록(wheelchair.reflex_log)이 같이 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlCommand:
    """한 프레임의 제어 명령. 각 값은 -1.0 ~ +1.0 (0 = 입력 없음).

    키보드 입력을 에이전트에 바로 넣지 않고 이 객체를 거치게 해서,
    다음 단계의 '공유 제어'가 중간에서 감속·정지·회피 명령으로 바꿀 수 있게 한다.
    """

    forward: float = 0.0  # +1 전진 / -1 후진
    turn: float = 0.0     # +1 우회전 / -1 좌회전
    strafe: float = 0.0   # +1 오른쪽 / -1 왼쪽 평행이동
