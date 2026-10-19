"""Native bench runs (ADR 0034): a harness alone, on its own login, as a frontier baseline.

A `native:<harness>:<model>` contestant runs the harness once per task run, in a worktree of the
task's fresh repository. It gets the prompt a harness run's first attempt gets
(`harness_loop.first_prompt`): the instruction, the acceptance commands, the project's test
command and the track notes. Stepswitch does not route, gate, retry or review the run; the bench
scores its diff like any contestant's. The harness reads its own login. Its environment is still
`minimal_env`, without the user's keys (`HarnessAdapter.start_native`), and the run is isolated
from the user's and the enclosing project's customizations.

The run's usage is what the harness reports per model (`RunResult.models`), and every model of
a native run is hosted. A run that ended on an infrastructure error (an API or server error, a
usage limit, no final report) is marked `infra_error`; the comparison claims leave such task
runs out.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from stepswitch.bench.suites import BenchTask
from stepswitch.core.task import Budgets
from stepswitch.worktree import WorktreePool

PREFIX = "native:"
# What the bench prints before the first native run of a bench.
NOTICE = (
    "{title} runs on its own login for {names}: the task text goes to its hosted service, and"
    " the usage counts against that account."
)


@dataclass
class NativeResult:
    """What a native run did, as the bench row reports it."""

    status: str
    stop_reason: str | None
    wall_seconds: float
    diff: str = ""
    changed_files: list[str] = field(default_factory=list)
    models: dict[str, dict[str, int]] = field(default_factory=dict)
    turns: int = 0
    tool_calls: int = 0
    version: str | None = None
    model: str | None = None
    auth: str | None = None
    reported_cost: float | None = None
    infra_error: bool = False
    detail: str = ""


def parse(name: str) -> tuple[str, str]:
    """`native:<harness>:<model>` -> (harness, model); the harness must support native runs."""
    from stepswitch.workers.harness import adapters

    harness, _, model = name.removeprefix(PREFIX).partition(":")
    known = adapters()
    if harness not in known:
        raise ValueError(f"unknown harness in {name} (harnesses: {', '.join(sorted(known))})")
    if not known[harness].native:
        supported = ", ".join(sorted(n for n, a in known.items() if a.native))
        raise ValueError(f"{harness} has no native runs (harnesses with them: {supported})")
    if not model.strip():
        raise ValueError(f"{name} names no model: native:<harness>:<model>")
    return harness, model.strip()


def run_native(
    task: BenchTask,
    repo: Path,
    *,
    harness: str,
    model: str,
    acceptance: list[str],
    place: Path,
    config: Any,
    budgets: Budgets,
    keep: bool = False,
) -> NativeResult:
    """Run `harness` alone on `model` for one task run in a worktree of `repo`."""
    from stepswitch.core.track import track_policy
    from stepswitch.workers.harness import HarnessTask, get_adapter, harness_settings
    from stepswitch.workers.harness_loop import first_prompt

    adapter = get_adapter(harness)
    settings, _slots, _keep = harness_settings(config, harness)
    pool = WorktreePool(repo, place / "state")
    tree = pool.acquire(f"native-{task.id}")
    started = time.monotonic()
    try:
        policy = track_policy(task.track, tree.path, config)
        settings = replace(
            settings,
            test_command=list(policy.test_command) if policy.test_command else None,
            timeout_seconds=budgets.wall_seconds or settings.timeout_seconds,
            transcript=place / "native.jsonl" if keep else None,
            log=place / "native.stderr.log" if keep else None,
            isolated=True,
        )
        prompt = first_prompt(
            task.instruction, acceptance, settings.test_command, "\n\n".join(policy.notes)
        )
        handle = adapter.start_native(
            HarnessTask(prompt=prompt, instruction=task.instruction), tree.path, model, settings
        )
        start: dict[str, Any] = {}
        for event in handle.events():
            if event.kind == "start":
                start = dict(event.data)
        result = handle.result()
        wall = time.monotonic() - started
        return NativeResult(
            status=result.status,
            stop_reason=result.stop_reason,
            wall_seconds=round(wall, 3),
            diff=tree.diff(),
            changed_files=list(tree.changed_files()),
            models=dict(result.models),
            turns=result.turns,
            tool_calls=result.tool_calls,
            version=result.version,
            model=start.get("model"),
            auth=start.get("auth"),
            reported_cost=result.reported_cost,
            infra_error=result.infra_error,
            detail=result.detail[:500],
        )
    finally:
        tree.release()
        pool.close()
