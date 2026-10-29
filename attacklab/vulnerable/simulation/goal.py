"""도착지(맵의 'G' 칸)까지의 주행: 도착 판정, 걸린 시간·거리, 그동안 커넥톰이 개입한 횟수.

Webots 컨트롤러와 시험 도구(tools/goal_trials.py)가 매 스텝 같은 방법으로 부른다.

    trip = Trip(world, cfg)
    trip.count(events)                 # ReflexMonitor가 돌려준 사건(start/end) — 개입 한 번 = 작동(에피소드) 하나
    trip.moved(agent)                  # 지나온 거리
    if trip.arrived(agent):            # 이번 스텝에 도착지에 들어왔다
        trip.count(monitor.end_episode())   # 진행 중이던 작동을 닫아 단계별 수에 넣고
        record = trip.finish(t)             # 도착 기록(dict, 로그·화면 알림) — 멈춤 시작
    if trip.due(t): ...처음부터 다시...; trip.restarted(t)
    충돌로 처음부터 다시 시작할 때:  trip.collided() → (멈춤이 끝나면) trip.restarted(t)

시도(attempt): 시작 위치에서 출발해 도착하거나 부딪힐 때까지. 부딪히면 처음부터 다시 — 다음 시도가 된다.
도착하면 그 자리에서 goal_restart_delay_s 동안 멈춰 결과를 보여 준 뒤, 처음 위치에서 새 주행을 시작한다.
개입 수는 '이번 시도' 것만 센다(도착한 그 주행에서 커넥톰이 몇 번 끼어들었는지). 그 전에 부딪힌 시도 수는 따로 알려 준다.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from common.config import CONFIG, Config
from simulation.world import World

# 개입 한 번을 대표하는 단계: 그 작동에서 거친 단계 중 가장 센 것 (정지 > 회피 > 옆벽 피하기 > 감속)
SEVERITY = ("stop", "avoid", "veer", "slow")
_AVOID_LIKE = {"return": "avoid", "override": "avoid"}  # 복귀는 회피의 뒷부분


def strongest_stage(stages: Iterable[str]) -> str:
    """작동 하나가 거친 단계들 → 대표 단계 (SEVERITY 중 하나)."""
    seen = {_AVOID_LIKE.get(stage, stage) for stage in stages}
    return next((stage for stage in SEVERITY if stage in seen), "slow")


def goal_bearing(world: World, agent: Any) -> dict[str, float] | None:
    """도착지까지 거리(m)와 방향(도, 로봇 정면 기준 오른쪽 +, -180~180). 도착지가 없는 맵이면 None."""
    if world.goal is None:
        return None
    dx, dy = world.goal[0] - agent.x, world.goal[1] - agent.y
    bearing = (math.degrees(math.atan2(dy, dx)) - agent.angle_deg + 180.0) % 360.0 - 180.0
    return {"x": round(world.goal[0], 2), "y": round(world.goal[1], 2),
            "distance": round(math.hypot(dx, dy), 1), "bearing": round(bearing, 0)}


class Trip:
    """지금 주행(시도)의 기록과 도착 뒤 멈춤. 시각은 시뮬레이션 시각(초)."""

    def __init__(self, world: World, cfg: Config = CONFIG, t: float = 0.0) -> None:
        self.world = world
        self.cfg = cfg
        self.arrivals = 0             # 지금까지 도착한 수
        self.failed = 0               # 마지막 도착 뒤로 부딪혀 처음부터 다시 한 시도 수
        self.restart_at: float | None = None  # 도착해서 멈춰 있는 중이면 처음부터 다시 시작할 시각
        self._new_attempt(t)

    def _new_attempt(self, t: float) -> None:
        self.start_t = t
        self.distance = 0.0
        self.interventions = 0        # 이번 시도에서 커넥톰이 개입한 횟수 (작동 수)
        self.by_stage = {stage: 0 for stage in SEVERITY}
        self.reflex_s = 0.0           # 개입한 시간의 합(초)
        self.no_brain = 0             # 커넥톰 판단 없이 안전 정지한 작동 수 (연결 끊김 — 개입 수에 넣지 않는다)
        self._counted: set[int] = set()  # 개입으로 센 작동 번호 (끝 사건에서 단계를 나눌 때)
        self._last: tuple[float, float] | None = None

    @property
    def enabled(self) -> bool:
        return self.world.goal is not None

    @property
    def frozen(self) -> bool:
        """도착해서 멈춰 있는 중인지 (이 동안 사용자 입력을 무시한다)."""
        return self.restart_at is not None

    def count(self, events: Iterable[dict[str, Any]]) -> None:
        """ReflexMonitor 사건을 센다: start = 개입 한 번(수동 주행·판단 없는 안전 정지는 빼고), end = 단계별로 나눈다."""
        for event in events:
            if event.get("type") == "start" and not self.frozen:
                if event["reflex"]["stage"] == "manual":
                    continue
                if not event["brain"]["ok"]:
                    self.no_brain += 1
                    continue
                self.interventions += 1
                self._counted.add(event["episode"])
            elif event.get("type") == "end" and event.get("episode") in self._counted:
                self._counted.discard(event["episode"])
                self.by_stage[strongest_stage(event.get("stages", ()))] += 1
                self.reflex_s += float(event.get("duration") or 0.0)

    def moved(self, agent: Any) -> None:
        """지나온 거리를 더한다 (매 스텝, 이동 뒤에)."""
        here = (float(agent.x), float(agent.y))
        if self._last is not None and not self.frozen:
            self.distance += math.hypot(here[0] - self._last[0], here[1] - self._last[1])
        self._last = here

    def arrived(self, agent: Any) -> bool:
        """이번 스텝에 도착지에 들어왔는지 (도착해서 멈춰 있는 중이면 False)."""
        return self.enabled and not self.frozen and self.world.in_goal(agent.x, agent.y)

    def finish(self, t: float) -> dict[str, Any]:
        """도착: 멈춤을 시작하고 도착 기록을 돌려준다 (그 전에 진행 중이던 작동을 count로 닫아 둔다)."""
        self.arrivals += 1
        self.restart_at = t + self.cfg.goal_restart_delay_s
        return {
            "count": self.arrivals,
            "elapsed": round(t - self.start_t, 1),
            "distance": round(self.distance, 1),
            "interventions": self.interventions,
            "by_stage": dict(self.by_stage),
            "reflex_s": round(self.reflex_s, 1),
            "no_brain": self.no_brain,
            "failed_attempts": self.failed,   # 이번 도착 전에 부딪혀 처음부터 다시 한 수
            "restart_in": self.cfg.goal_restart_delay_s,
        }

    def progress(self, agent: Any, t: float) -> dict[str, Any] | None:
        """화면에 보여 줄 지금 주행 상태: 도착지까지 거리·방향, 걸린 시간, 개입 수. 도착지가 없으면 None."""
        bearing = goal_bearing(self.world, agent)
        if bearing is None:
            return None
        return {**bearing, "elapsed": round(t - self.start_t, 1), "interventions": self.interventions,
                "attempt": self.failed + 1, "arrived": self.frozen, "arrivals": self.arrivals}

    def collided(self) -> None:
        """이번 시도가 충돌로 끝났다 (처음부터 다시 하면 restarted)."""
        self.failed += 1

    def due(self, t: float) -> bool:
        """도착 뒤 멈춤이 끝나 처음부터 다시 시작할 때인지."""
        return self.restart_at is not None and t >= self.restart_at - 1e-9

    def restarted(self, t: float) -> None:
        """처음 위치에서 새 시도를 시작한다 (충돌 뒤든 도착 뒤든)."""
        if self.frozen:
            self.failed = 0  # 도착 뒤 새 주행: 실패 수를 비운다
        self.restart_at = None
        self._new_attempt(t)
