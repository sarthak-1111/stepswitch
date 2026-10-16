"""Budget tracking (SPEC §7.4, claim C-020).

Budgets are checked at every step boundary. A step that has started is allowed to finish, so a
run overruns a budget by at most one step; hitting a budget ends the run with status `stopped`
and a reason naming the budget, never silently. A cancel request (the dashboard's cancel button,
SPEC §17) is read at the same points and stops the run with the reason `cancelled`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from stepswitch.core.task import Budgets, StopReason


@dataclass
class BudgetTracker:
    budgets: Budgets
    clock: Callable[[], float] = time.monotonic
    tokens: int = 0
    gpu_seconds: float = 0.0
    escalations: int = 0
    started: float = field(default=0.0)
    # True once someone asked the task to stop.
    cancelled: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        self.started = self.clock()

    def charge(self, *, tokens: int = 0, gpu_seconds: float = 0.0) -> None:
        self.tokens += max(0, tokens)
        self.gpu_seconds += max(0.0, gpu_seconds)

    def escalate(self) -> None:
        self.escalations += 1

    @property
    def wall_seconds(self) -> float:
        return self.clock() - self.started

    def exceeded(self) -> StopReason | None:
        """The first budget that is used up, checked in a fixed order after a cancel request."""
        if self.cancelled is not None and self.cancelled():
            return "cancelled"
        limits = self.budgets
        if limits.tokens is not None and self.tokens >= limits.tokens:
            return "budget_tokens"
        if limits.wall_seconds is not None and self.wall_seconds >= limits.wall_seconds:
            return "budget_wall_seconds"
        if limits.gpu_seconds is not None and self.gpu_seconds >= limits.gpu_seconds:
            return "budget_gpu_seconds"
        if limits.escalations is not None and self.escalations > limits.escalations:
            return "budget_escalations"
        return None

    def can_escalate(self) -> bool:
        limit = self.budgets.escalations
        return limit is None or self.escalations < limit

    def remaining(self) -> dict[str, float | int | None]:
        limits = self.budgets
        return {
            "tokens": None if limits.tokens is None else max(0, limits.tokens - self.tokens),
            "wall_seconds": None
            if limits.wall_seconds is None
            else max(0.0, limits.wall_seconds - self.wall_seconds),
            "gpu_seconds": None
            if limits.gpu_seconds is None
            else max(0.0, limits.gpu_seconds - self.gpu_seconds),
            "escalations": None
            if limits.escalations is None
            else max(0, limits.escalations - self.escalations),
        }

    def snapshot(self) -> dict[str, float | int]:
        return {
            "tokens": self.tokens,
            "wall_seconds": round(self.wall_seconds, 3),
            "gpu_seconds": round(self.gpu_seconds, 3),
            "escalations": self.escalations,
        }
