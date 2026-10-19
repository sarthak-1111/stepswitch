"""Recorded runs for the replay claim (C-014) and the replay tests.

Each scenario records one run of deterministic fake workers on a small project through
`run_task`, as `ssw run` does. The models are scripted (`workers.fake.ScriptedModel`); the
tools, worktrees, gates and trajectory are the real ones.

* calc-fix: two workers fix the calc-bug toy repository. The first edit is wrong (add()
  multiplies), the tests fail, the worker reads the file again and fixes it, the tests pass,
  the gates pass and the review approves.
* failover: the same task, but the first call to the first worker fails (the server is
  overloaded), so the router fails over to the other worker.
* relay: the first worker hands the task to the second at work step 3 with a HANDOFF brief,
  which a tiny summarizer writes.
* time-budget: every model call takes 100 s on a fake clock and the task may take 450 s, so it
  stops part of the way through with the wrong edit made.
* best-of-2: two candidates of different families solve a seeded task (C-013's add-numbers);
  their tests and reviews decide the winner.
* science: a science-track task with a golden case (C-013's spectrum-scale).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Request, ToolCallPart, ToolResultPart, Usage
from stepswitch.core.orchestrator import run_task
from stepswitch.core.task import Budgets, TaskResult, TaskSpec
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, FakeClock, ScriptedModel
from tests.claims.disagreement_fixture import SEEDED
from tests.claims.toy import TOY_INSTRUCTION, toy_repo

SCENARIOS = ("calc-fix", "failover", "relay", "time-budget", "best-of-2", "science")
TURN_USAGE = Usage(input_tokens=300, output_tokens=30)
ANCHOR_RE = re.compile(r"^(\d+):([0-9A-Za-z]+)\|(.*)$", re.MULTILINE)
WRONG = "    return a * b"
RIGHT = "    return a + b"


def _transcript(request: Request) -> tuple[list[str], list[str]]:
    """The tool calls made so far and their results' texts, in order."""
    calls: list[str] = []
    results: list[str] = []
    for message in request.messages:
        for part in message.content:
            if isinstance(part, ToolCallPart):
                calls.append(part.name)
            elif isinstance(part, ToolResultPart):
                results.append(part.text())
    return calls, results


def _add_line(read: str) -> tuple[str, str] | None:
    """The anchor and text of add()'s return line in a `read` result."""
    in_add = False
    for match in ANCHOR_RE.finditer(read):
        text = match.group(3)
        if text.startswith("def "):
            in_add = text.startswith("def add(")
        elif in_add and text.strip().startswith("return"):
            return f"{match.group(1)}:{match.group(2)}", text
    return None


def calc_fixer(request: Request, _index: int) -> Action:
    """Fixes calc-bug the long way: a wrong edit first, then the fix. It decides from the
    transcript and the file it read, so another worker can take over at any step."""
    system = request.system_text()
    if not request.tools:
        if "Step: review" in system:
            return Action(text="APPROVE. add() returns the sum now and the tests pass.")
        if "Step: plan" in system:
            return Action(text="1. Read calc.py.\n2. Fix add().\n3. Run the tests.")
        return Action(text="Fixed add() in calc.py; the tests pass.")
    calls, results = _transcript(request)
    last = calls[-1] if calls else None
    if last is None:
        return Action(tool="grep", arguments={"pattern": "def add"})
    if last in ("grep", "finish"):
        return Action(tool="read", arguments={"path": "calc.py"})
    if last == "read":
        line = _add_line(results[-1])
        if line is None:
            return Action(tool="read", arguments={"path": "calc.py"})
        anchor, text = line
        if text == RIGHT:
            return Action(tool="test", arguments={})
        new = WRONG if text != WRONG else RIGHT
        edit = {"op": "replace", "start": anchor, "text": new}
        return Action(tool="edit", arguments={"path": "calc.py", "edits": [edit]})
    if last == "edit":
        return Action(tool="test", arguments={})
    if last == "test" and "failed" in results[-1]:
        return Action(tool="read", arguments={"path": "calc.py"})
    return Action(tool="finish", arguments={"summary": "Fixed add() in calc.py."})


class Flaky:
    """A model whose first `failures` calls fail as an overloaded server would."""

    def __init__(self, inner: ScriptedModel, failures: int = 1) -> None:
        self.inner = inner
        self.failures = failures
        self.calls = 0

    def complete(self, request: Request) -> Any:
        self.calls += 1
        if self.calls <= self.failures:
            raise ModelError("overloaded", "the server is busy")
        return self.inner.complete(request)


def writer(_request: Request, _index: int) -> Action:
    state = {
        "done": ["Read calc.py and changed add()."],
        "decisions": [{"what": "Fix add() in place", "why": "The tests call add()."}],
        "open_questions": [],
    }
    return Action(text=json.dumps(state))


def spec_of(name: str, *, tier: str = "t2", roles: tuple[str, ...] = ()) -> WorkerSpec:
    return WorkerSpec(
        id=f"fake/{name}",
        endpoint=name,
        harness="fake",
        model=name,
        tier=tier,
        family=name,
        local=True,
        roles=roles,
        tool_protocol="native",
    )


def calc_catalog(
    *, flaky: bool = False, summarizer: bool = False, clock: FakeClock | None = None
) -> Catalog:
    """alpha and beta, both of which fix calc-bug; optionally the tiny brief writer."""
    seconds = 100.0 if clock is not None else 0.0
    workers = []
    for name in ("alpha", "beta"):
        model: Any = ScriptedModel(
            calc_fixer, usage=TURN_USAGE, model=name, seconds_per_step=seconds, clock=clock
        )
        if flaky and name == "alpha":
            model = Flaky(model)
        workers.append(Worker(spec_of(name), model))
    if summarizer:
        tiny = ScriptedModel(writer, usage=Usage(input_tokens=400, output_tokens=60), model="tiny")
        workers.append(Worker(spec_of("tiny", tier="cpu", roles=("handoff_writer",)), tiny))
    return Catalog(workers)


@dataclass
class Recorded:
    """A recorded run, and how to make the catalog it ran with afresh (calls at zero)."""

    name: str
    root: Path
    result: TaskResult
    catalog: Callable[[], Catalog]
    clock: FakeClock | None = None


def record(name: str, dest: Path) -> Recorded:
    """Record scenario `name` in a new project under `dest`."""
    if name in ("best-of-2", "science"):
        task = next(
            t
            for t in SEEDED
            if t.name == ("add-numbers" if name == "best-of-2" else "spectrum-scale")
        )
        repo = task.make(dest / name)
        spec = TaskSpec(
            instruction=task.instruction,
            repo=repo,
            track=task.track,  # type: ignore[arg-type]
            max_steps=12,
            best_of=2 if name == "best-of-2" else 1,
        )
        result = run_task(spec, root=repo, catalog=task.catalog(repo))
        return Recorded(name, repo, result, lambda: task.catalog(repo))
    repo = toy_repo("calc-bug", dest / name)
    clock = FakeClock() if name == "time-budget" else None
    options: dict[str, Any] = {}
    if name == "relay":
        options = {"relay": ["fake/alpha", "fake/beta"], "relay_step": 3}
    if name in ("relay", "time-budget"):
        options["workers"] = ["fake/alpha", "fake/beta"]
    if clock is not None:
        options["budgets"] = Budgets(wall_seconds=450)

    def catalog() -> Catalog:
        return calc_catalog(
            flaky=name == "failover",
            summarizer=name == "relay",
            clock=FakeClock() if clock is not None else None,
        )

    spec = TaskSpec(instruction=TOY_INSTRUCTION, repo=repo, max_steps=16, **options)
    used = calc_catalog(flaky=name == "failover", summarizer=name == "relay", clock=clock)
    result = run_task(spec, root=repo, catalog=used, clock=clock)
    return Recorded(name, repo, result, catalog, clock)


def model_calls(catalog: Catalog) -> int:
    """How often the catalog's models were asked."""
    return sum(int(getattr(worker.model, "calls", 0)) for worker in catalog)
