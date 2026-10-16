"""Escalation ladder and budgets (SPEC §7.3, §7.4)."""

from __future__ import annotations

import pytest

from stepswitch.core.budget import BudgetTracker
from stepswitch.core.task import Budgets
from stepswitch.router.escalation import CONFIDENCE_THRESHOLD, Ladder, start_level
from stepswitch.router.features import TaskFeatures
from stepswitch.workers.fake import FakeClock


def test_ladder_moves_up_one_available_rung_at_a_time() -> None:
    ladder = Ladder.for_levels([2, 0, 0, 3])
    assert (ladder.levels, ladder.level) == ([0, 2, 3], 0)
    move = ladder.escalate("lint gate failed twice")
    assert move == {
        "from_level": 0,
        "to_level": 2,
        "from": "T0",
        "to": "T3",
        "reason": "lint gate failed twice",
    }
    assert ladder.escalate("again") is not None
    assert not ladder.can_escalate()
    assert ladder.escalate("top") is None
    assert [m["to_level"] for m in ladder.history] == [2, 3]


def test_empty_ladder() -> None:
    ladder = Ladder.for_levels([])
    assert (ladder.levels, ladder.level, ladder.can_escalate()) == ([], 0, False)
    assert start_level(TaskFeatures(), ladder) == (0, None)


def test_start_level_triggers() -> None:
    ladder = Ladder.for_levels([0, 1, 2])
    assert start_level(TaskFeatures(), ladder) == (0, None)
    level, why = start_level(TaskFeatures(risk="high"), ladder)
    assert (level, why) == (1, "high-risk task")
    unsure = TaskFeatures(confidence=CONFIDENCE_THRESHOLD - 0.1, source="model:managed/tiny")
    level, why = start_level(unsure, ladder)
    assert level == 1 and why is not None and "confidence" in why
    # the heuristic's fixed confidence is not a trigger
    assert start_level(TaskFeatures(confidence=0.1, source="heuristic"), ladder)[0] == 0


def test_science_track_starts_above_t0() -> None:
    ladder = Ladder.for_levels([0, 1])
    level, why = start_level(TaskFeatures(), ladder, track="science")
    assert level == 1 and why is not None and "T0" in why
    # with nothing above T0 the task starts on T0; the router then refuses code steps
    assert start_level(TaskFeatures(), Ladder.for_levels([0]), track="science")[0] == 0


def test_budget_order_and_escalation_limit() -> None:
    clock = FakeClock()
    tracker = BudgetTracker(
        Budgets(tokens=100, wall_seconds=10.0, gpu_seconds=5.0, escalations=1), clock=clock
    )
    assert tracker.exceeded() is None
    assert tracker.can_escalate()
    tracker.escalate()
    assert not tracker.can_escalate()
    assert tracker.exceeded() is None  # reaching the limit is allowed, passing it is not
    tracker.charge(gpu_seconds=5.0)
    assert tracker.exceeded() == "budget_gpu_seconds"
    clock.advance(10.0)
    assert tracker.exceeded() == "budget_wall_seconds"
    tracker.charge(tokens=100)
    assert tracker.exceeded() == "budget_tokens"
    assert tracker.snapshot() == {
        "tokens": 100,
        "wall_seconds": 10.0,
        "gpu_seconds": 5.0,
        "escalations": 1,
    }
    assert tracker.remaining() == {
        "tokens": 0,
        "wall_seconds": 0.0,
        "gpu_seconds": 0.0,
        "escalations": 0,
    }


def test_unlimited_budgets_never_stop() -> None:
    tracker = BudgetTracker(Budgets(), clock=FakeClock())
    tracker.charge(tokens=10**9, gpu_seconds=1e9)
    for _ in range(100):
        tracker.escalate()
    assert tracker.exceeded() is None
    assert tracker.can_escalate()


@pytest.mark.parametrize("negative", [-5, -0.5])
def test_negative_charges_are_ignored(negative: float) -> None:
    tracker = BudgetTracker(Budgets(tokens=10), clock=FakeClock())
    tracker.charge(tokens=int(negative), gpu_seconds=negative)
    assert (tracker.tokens, tracker.gpu_seconds) == (0, 0.0)
