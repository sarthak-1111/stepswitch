"""Retry an action with exponential backoff."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """How often to try an action and how long to wait between tries.

    The first retry waits `base_delay` seconds; each later wait is `factor` times the one
    before it.
    """

    attempts: int = 3
    base_delay: float = 0.5
    factor: float = 2.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")
        if self.base_delay < 0:
            raise ValueError("base_delay must not be negative")
        if self.factor < 1:
            raise ValueError("factor must be at least 1")

    def delays(self) -> list[float]:
        """The waits before the second and every later try, in seconds."""
        return [self.base_delay * self.factor**n for n in range(self.attempts - 1)]


def run_with_retries(
    action: Callable[[], T],
    policy: RetryPolicy,
    *,
    retry_on: tuple[type[BaseException], ...] = (OSError,),
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """The action's result, trying again after the policy's delays when it raises one of
    `retry_on`. When every try fails, the last failure is raised."""
    waits = policy.delays()
    for attempt in range(policy.attempts):
        try:
            return action()
        except retry_on:
            if attempt == policy.attempts - 1:
                raise
            sleep(waits[attempt])
    raise AssertionError("unreachable")
