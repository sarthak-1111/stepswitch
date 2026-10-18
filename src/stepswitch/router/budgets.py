"""Per-role budgets (SPEC §13 TK10): output tokens and reasoning effort per step type.

Planning needs much reasoning, summaries little. Effort is only sent to workers known to reason
(`WorkerSpec.reasoning`); the compatibility layer maps it to each format's setting or reports
that it was dropped. A CPU-tier worker's output is capped lower, as in Phase 2.

Work steps (navigate, read, edit, run, triage) all use one effort, the edit step's. They share
one transcript, and a reasoning model's chat template can render the effort into the prompt's
first system block: an effort that changed from step to step would change the prompt from its
first token and lose the server's prefix cache at every change (seen on the Phase 5 bench,
ADR 0024). Plan, review, summary and text steps are prompts of their own and keep their own
effort. The SPEC's "low for navigation" is therefore not applied.

Projects override the defaults per step type; for work steps, only the edit step's effort
counts:

    [budgets.roles.edit]
    max_output_tokens = 3000
    effort = "high"

The unoptimized baseline (`ssw bench run --baseline`) uses `BASELINE`: the Phase 2 output sizes
and no effort setting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
CPU_OUTPUT_CAP = 1024


@dataclass(frozen=True)
class RoleBudget:
    max_output_tokens: int
    effort: str | None = None


# Work-step types are predictions (a model may edit in a step predicted as `read`), so their
# output caps keep the Phase 2 size: a lower cap saves nothing a model does not generate and can
# cut a tool call short. What differs per work step is the reasoning effort.
WORK_STEPS = frozenset({"navigate", "read", "edit", "run", "triage"})
# The step whose effort every work step uses (see the module docstring).
WORK_EFFORT_STEP = "edit"
DEFAULTS: dict[str, RoleBudget] = {
    "plan": RoleBudget(1200, "high"),
    **{step: RoleBudget(2048, "medium") for step in sorted(WORK_STEPS)},
    "review": RoleBudget(600, "medium"),
    "summarize": RoleBudget(300, "low"),
    "write-text": RoleBudget(600, "low"),
}
# Phase 2: every work step 2048 tokens (CPU 1024), plan and review 400, summary 200; no effort.
BASELINE: dict[str, RoleBudget] = {
    **{step: RoleBudget(2048) for step in ("navigate", "read", "edit", "run", "triage")},
    "plan": RoleBudget(400),
    "review": RoleBudget(400),
    "summarize": RoleBudget(200),
    "write-text": RoleBudget(400),
}
TEXT_STEPS = frozenset({"plan", "review", "summarize", "write-text"})


def _overrides(config: Any) -> Mapping[str, Any]:
    budgets = getattr(config, "budgets", None)
    extra = getattr(budgets, "model_extra", None) or {}
    roles = extra.get("roles") if isinstance(extra, Mapping) else None
    return roles if isinstance(roles, Mapping) else {}


def role_budget(
    step_type: str, tier: str | None, config: Any = None, *, optimize: bool = True
) -> RoleBudget:
    """The budget of one step on a worker of `tier`."""
    table = DEFAULTS if optimize else BASELINE
    budget = table.get(step_type, RoleBudget(2048, "medium" if optimize else None))
    if optimize:
        roles = _overrides(config)
        override = roles.get(step_type)
        if isinstance(override, Mapping):
            tokens = override.get("max_output_tokens")
            effort = override.get("effort")
            budget = replace(
                budget,
                max_output_tokens=int(tokens)
                if isinstance(tokens, int) and tokens > 0
                else budget.max_output_tokens,
                effort=str(effort) if effort in EFFORTS else budget.effort,
            )
        if step_type in WORK_STEPS:
            shared = roles.get(WORK_EFFORT_STEP)
            effort = shared.get("effort") if isinstance(shared, Mapping) else None
            budget = replace(
                budget,
                effort=str(effort) if effort in EFFORTS else DEFAULTS[WORK_EFFORT_STEP].effort,
            )
    if tier == "cpu" and step_type not in TEXT_STEPS:
        budget = replace(budget, max_output_tokens=min(budget.max_output_tokens, CPU_OUTPUT_CAP))
    return budget
