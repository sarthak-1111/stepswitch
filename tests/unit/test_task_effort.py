"""Reasoning effort follows the task's difficulty (ADR 0037, SPEC §13 TK10).

In the Phase 11 rerun the plan step ran at high effort on every task. It produced 35% of the
router's output tokens, although the classifier rated 34 of the 42 task runs as difficulty 2.
From Phase 12 the plan step's effort and the work steps' effort follow the task's difficulty
band (easy 1-2, medium 3, hard 4-5, as the learning router's bands). Work steps keep one effort
within a task, so the prompt prefix stays cacheable (ADR 0024). An escalation raises it one
level, up to high. A project's explicit effort still wins, and the unoptimized baseline sends
none.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stepswitch.router.budgets import WORK_STEPS, role_budget
from stepswitch.router.features import TaskFeatures
from tests.unit.test_agent_context import (
    FINISH,
    SMALL,
    FileToolbox,
    build,
    events,
    read,
    scripted,
    sequence,
)
from tests.unit.test_builtin_agent import StubGates


@pytest.mark.parametrize(
    ("difficulty", "plan", "work"),
    [
        (1, "low", "low"),
        (2, "low", "low"),
        (3, "medium", "medium"),
        (4, "high", "high"),
        (5, "high", "high"),
    ],
)
def test_plan_and_work_effort_follow_the_difficulty_band(
    difficulty: int, plan: str, work: str
) -> None:
    assert role_budget("plan", "t3", difficulty=difficulty).effort == plan
    assert {role_budget(step, "t3", difficulty=difficulty).effort for step in WORK_STEPS} == {work}
    # Review, summary and text steps keep their own effort.
    assert role_budget("review", "t3", difficulty=difficulty).effort == "medium"
    assert role_budget("summarize", "t3", difficulty=difficulty).effort == "low"


def test_an_escalation_raises_the_work_effort_up_to_high() -> None:
    assert role_budget("edit", "t3", difficulty=2, raised=1).effort == "medium"
    assert role_budget("read", "t3", difficulty=2, raised=2).effort == "high"
    assert role_budget("edit", "t3", difficulty=2, raised=5).effort == "high"
    assert role_budget("edit", "t3", difficulty=5, raised=1).effort == "high"
    assert role_budget("plan", "t3", difficulty=2, raised=1).effort == "low"  # planned once


def test_an_explicit_effort_and_the_baseline_are_left_alone() -> None:
    roles = {"edit": {"effort": "max"}, "plan": {"effort": "medium"}}
    config = SimpleNamespace(budgets=SimpleNamespace(model_extra={"roles": roles}))
    assert role_budget("plan", "t3", config, difficulty=5).effort == "medium"
    assert {role_budget(s, "t3", config, difficulty=1, raised=1).effort for s in WORK_STEPS} == {
        "max"
    }
    assert role_budget("plan", "t3", difficulty=4, optimize=False).effort is None
    assert role_budget("edit", "t3", difficulty=4, optimize=False).effort is None
    # Without a difficulty the defaults stand.
    assert role_budget("plan", "t3").effort == "high"
    assert role_budget("edit", "t3").effort == "medium"


@pytest.mark.parametrize(("difficulty", "plan", "work"), [(2, "low", "low"), (4, "high", "high")])
def test_the_agent_sends_one_work_effort_per_task(
    tmp_path: Path, difficulty: int, plan: str, work: str
) -> None:
    thinker = scripted("a/m", "t2", sequence([read("calc.py"), FINISH]), reasoning=True)
    loop = build(tmp_path, [thinker], FileToolbox(SMALL))
    loop.features = TaskFeatures(difficulty=difficulty)
    assert loop.run().status == "done"
    requests = thinker.model.requests  # type: ignore[union-attr]
    assert requests[0].effort == plan and not requests[0].tools
    assert {r.effort for r in requests if r.tools} == {work}


def test_an_escalation_raises_the_effort_for_the_rest_of_the_task(tmp_path: Path) -> None:
    # Two failed gates on the lower rung escalate; the higher rung's worker then passes. One
    # worker per rung, so the requests before and after the escalation are told apart.
    first = scripted(
        "cpu/small", "cpu", sequence([read("calc.py"), FINISH, FINISH]), reasoning=True
    )
    second = scripted("gpu/large", "t3", sequence([read("calc.py"), FINISH]), reasoning=True)
    loop = build(
        tmp_path,
        [first, second],
        FileToolbox(SMALL),
        StubGates([False, False, True]),
        task={"gate_attempts": 2},
    )
    loop.features = TaskFeatures(difficulty=2)
    outcome = loop.run()
    assert outcome.status == "done", outcome
    before = [r.effort for r in first.model.requests if r.tools]  # type: ignore[union-attr]
    after = [r.effort for r in second.model.requests if r.tools]  # type: ignore[union-attr]
    assert before and set(before) == {"low"}
    assert after and set(after) == {"medium"}
    # Each step records the effort it ran at.
    steps = events(loop, "step")
    assert steps[0]["step_type"] == "plan" and steps[0]["effort"] == "low"
    work = [e["effort"] for e in steps if e["step_type"] in WORK_STEPS]
    assert work[0] == "low" and work[-1] == "medium"
