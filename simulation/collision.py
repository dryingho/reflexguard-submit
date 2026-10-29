"""벽·장애물 충돌 → 멈춤 → 처음부터 다시 시작.

에이전트가 벽이나 움직이는 장애물(사람·공)에 부딪히면(Agent.apply_command가 True) 그 자리에서 collision_restart_delay_s 동안 멈춰
충돌 장면을 보여 준 뒤, 시작 위치·방향으로 되돌리고 루밍·커넥톰·공유 제어 상태를 새로 시작한다.
멈춰 있는 동안에는 사용자 입력을 무시한다. Webots 컨트롤러가 이 규칙을 쓴다.

    if crash.due(t): ...처음부터 다시...; crash.restarted()
    if crash.frozen: user_command = ControlCommand()
    hit = agent.apply_command(command, world, dt)
    if crash.hit(hit, t): ...충돌 알림·기록...
"""

from __future__ import annotations

from typing import Any

from common.control import ControlCommand
from simulation.agent import Agent
from simulation.world import World

_EPS = 1e-9


class CollisionRestart:
    """충돌 횟수와 '멈춰 있다가 다시 시작할 시각'을 들고 있는다. 시각은 시뮬레이션 시각(초)."""

    def __init__(self, delay_s: float) -> None:
        self.delay_s = max(0.0, delay_s)
        self.count = 0                       # 지금까지의 충돌 수
        self.restart_at: float | None = None  # 멈춰 있는 중이면 다시 시작할 시각

    @property
    def frozen(self) -> bool:
        """충돌 뒤 멈춰 있는 중인지 (이 동안 사용자 입력을 무시한다)."""
        return self.restart_at is not None

    def hit(self, collided: bool, t: float) -> bool:
        """이번 이동의 충돌 여부를 반영한다. 새 충돌이면 멈춤을 시작하고 True (멈춰 있는 중의 접촉은 세지 않는다)."""
        if not collided or self.frozen:
            return False
        self.count += 1
        self.restart_at = t + self.delay_s
        return True

    def due(self, t: float) -> bool:
        """멈춤이 끝나 처음부터 다시 시작할 때인지."""
        return self.restart_at is not None and t >= self.restart_at - _EPS

    def restarted(self) -> None:
        self.restart_at = None


def move_to_start(agent: Agent, world: World, start_angle_deg: float) -> None:
    """에이전트를 맵의 시작 위치·방향으로 되돌린다."""
    agent.x, agent.y = world.start
    agent.angle_deg = start_angle_deg


def collision_cause(applied: ControlCommand) -> str:
    """부딪힌 순간 실제로 적용된 움직임 (기록·알림용): reverse / forward / strafe / turn."""
    if applied.forward < 0.0:
        return "reverse"
    if applied.forward > 0.0:
        return "forward"
    if applied.strafe != 0.0:
        return "strafe"
    return "turn"


def collision_target(contact: Any) -> dict[str, Any]:
    """무엇에 부딪혔는지: {"with": "wall"} 또는 {"with": 장애물 종류(adult/child/ball), "object": 장애물 이름}."""
    if contact is None or contact == "wall":
        return {"with": "wall"}
    return {"with": getattr(contact, "kind", "object"), "object": getattr(contact, "name", "")}


def collision_fields(crash: CollisionRestart, agent: Agent, user: ControlCommand, applied: ControlCommand,
                     stage: Any, connectome: bool, safety: bool = False) -> dict[str, Any]:
    """충돌 기록(로그의 collision 줄, 웹 화면의 collision: 메시지)에 넣을 값. 부딪힌 것은 agent.contact에서 읽는다."""
    return {
        "count": crash.count,
        **collision_target(getattr(agent, "contact", None)),
        "restart_in": crash.delay_s,
        "cause": collision_cause(applied),
        "pose": {"x": round(agent.x, 3), "y": round(agent.y, 3), "angle": round(agent.angle_deg, 1)},
        "user": {"forward": round(user.forward, 2), "turn": round(user.turn, 2), "strafe": round(user.strafe, 2)},
        "applied": {"forward": round(applied.forward, 2), "turn": round(applied.turn, 2), "strafe": round(applied.strafe, 2)},
        "stage": stage if isinstance(stage, str) else "safe",  # 그 순간 공유 제어 단계
        "connectome": bool(connectome),
        "safety": bool(safety),  # 안전 스위치(판단 없을 때 수동 주행 허용)
    }
