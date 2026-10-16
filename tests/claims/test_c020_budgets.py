"""C-020: stops at the budget you set.

Proof (SPEC §18.5): runs with tiny budgets stop with the correct status and no overrun beyond
one step. Scope: per-task budgets for tokens, wall time, GPU-seconds and escalations (SPEC §7.4).

Each run uses the real orchestrator on the calc-bug toy repository with scripted fake workers,
so step costs are known exactly: a worker that keeps reading a file (never finishing) charges a
fixed number of tokens, seconds on a fake clock, or GPU-seconds per step; for escalations, workers
on three tiers keep declaring the task finished while the tests still fail, so the gates fail and
the escalation ladder moves up until its budget is spent. Budgets are checked at step
boundaries, so a run may finish the step that crosses the limit and no more: the overrun is at
most the largest single-step cost (for escalations, none: the ladder stops before escalating
past the limit).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stepswitch.compat.ir import Usage
from stepswitch.core.orchestrator import run_task
from stepswitch.core.task import Budgets, TaskSpec
from stepswitch.ledger import task_rows
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, FakeClock, ScriptedModel
from tests.claims import evidence
from tests.claims.toy import TOY_INSTRUCTION, toy_repo

pytestmark = [pytest.mark.claim("C-020")]

READ = Action(tool="read", arguments={"path": "calc.py"})
FINISH = Action(tool="finish", arguments={"summary": "Done."})


def looping_worker(
    clock: FakeClock, *, seconds: float = 0.0, gpu_seconds: float = 0.0, gpu: bool = False
) -> Worker:
    model = ScriptedModel(
        lambda request, index: READ,
        usage=Usage(input_tokens=80, output_tokens=20),
        seconds_per_step=seconds,
        gpu_seconds_per_step=gpu_seconds,
        clock=clock,
    )
    return Worker(WorkerSpec(id="fake/looper", harness="fake", tier="cpu", gpu=gpu), model)


def finishing_workers(clock: FakeClock) -> list[Worker]:
    workers = []
    for tier in ("cpu", "t2", "t3"):  # three rungs, so only the budget stops the ladder
        model = ScriptedModel(
            lambda request, index: FINISH,
            usage=Usage(input_tokens=50, output_tokens=10),
            clock=clock,
        )
        workers.append(
            Worker(WorkerSpec(id=f"fake/finisher-{tier}", harness="fake", tier=tier), model)
        )
    return workers


def step_costs(root: Path, task_id: str, kind: str) -> list[float]:
    rows = task_rows(root, task_id)
    if kind == "tokens":
        return [float(r["input_tokens"] + r["output_tokens"]) for r in rows]
    if kind == "wall_seconds":
        return [float(r["wall_seconds"]) for r in rows]
    if kind == "gpu_seconds":
        return [float(r["gpu_seconds"]) for r in rows]
    return [1.0]  # escalations: at most one per step


def run_case(tmp_path: Path, kind: str) -> dict[str, Any]:
    repo = toy_repo("calc-bug", tmp_path / kind)
    clock = FakeClock()
    if kind == "tokens":
        budgets, workers = Budgets(tokens=250), [looping_worker(clock)]
    elif kind == "wall_seconds":
        budgets, workers = Budgets(wall_seconds=25.0), [looping_worker(clock, seconds=10.0)]
    elif kind == "gpu_seconds":
        budgets = Budgets(gpu_seconds=10.0)
        workers = [looping_worker(clock, gpu_seconds=4.0, gpu=True)]
    else:
        budgets, workers = Budgets(escalations=1), finishing_workers(clock)
    spec = TaskSpec(instruction=TOY_INSTRUCTION, repo=repo, budgets=budgets, max_steps=60)
    result = run_task(spec, root=repo, catalog=Catalog(workers), clock=clock)
    limit = getattr(budgets, kind)
    used = result.budget[kind]
    costs = step_costs(repo, result.task_id, kind)
    largest = max(costs) if costs else 0.0
    return {
        "budget": kind,
        "limit": limit,
        "used": used,
        "overrun": max(0.0, used - limit),
        "largest_step_cost": largest,
        "steps": result.steps,
        "status": result.status,
        "stop_reason": result.stop_reason,
    }


def test_tiny_budgets_stop_with_status_and_bounded_overrun(tmp_path: Path) -> None:
    limits = evidence.thresholds("C-020")
    rows = [
        run_case(tmp_path, kind)
        for kind in ("tokens", "wall_seconds", "gpu_seconds", "escalations")
    ]
    evidence.write(
        "C-020", "budgets", {"runs": rows, "max_overrun_steps": limits["max_overrun_steps"]}
    )
    for row in rows:
        assert row["status"] == "stopped", row
        assert row["stop_reason"] == f"budget_{row['budget']}", row
        assert row["used"] >= row["limit"] or row["budget"] == "escalations", row
        if row["budget"] == "escalations":
            assert row["used"] == row["limit"], row  # escalated once, then stopped
        else:
            assert row["overrun"] <= limits["max_overrun_steps"] * row["largest_step_cost"], row
