"""Per-role budgets (TK10): output tokens and effort per step type."""

from __future__ import annotations

from types import SimpleNamespace

from stepswitch.router.budgets import BASELINE, CPU_OUTPUT_CAP, RoleBudget, role_budget


def test_navigation_is_low_effort_and_planning_high() -> None:
    assert role_budget("navigate", "t3").effort == "low"
    assert role_budget("run", "t3").effort == "low"
    assert role_budget("plan", "t3").effort == "high"
    assert role_budget("edit", "t3").effort == "medium"


def test_cpu_workers_get_a_smaller_output_cap_for_work_steps() -> None:
    assert role_budget("edit", "cpu").max_output_tokens == CPU_OUTPUT_CAP
    assert (
        role_budget("plan", "cpu").max_output_tokens == role_budget("plan", "t3").max_output_tokens
    )


def test_projects_override_per_step_type() -> None:
    budgets = SimpleNamespace(
        model_extra={"roles": {"edit": {"max_output_tokens": 3000, "effort": "high"}}}
    )
    config = SimpleNamespace(budgets=budgets)
    assert role_budget("edit", "t3", config) == RoleBudget(3000, "high")
    bad = SimpleNamespace(
        budgets=SimpleNamespace(
            model_extra={"roles": {"edit": {"max_output_tokens": -1, "effort": "extreme"}}}
        )
    )
    assert role_budget("edit", "t3", bad) == role_budget("edit", "t3")


def test_the_baseline_is_phase_two() -> None:
    assert role_budget("edit", "t3", optimize=False) == BASELINE["edit"] == RoleBudget(2048)
    assert role_budget("plan", "t3", optimize=False) == RoleBudget(400)
    assert role_budget("edit", "cpu", optimize=False).max_output_tokens == 1024
    assert all(budget.effort is None for budget in BASELINE.values())
