"""The built-in agent loop: step types, gates, escalation, review, budgets (ADR 0015).

The loop runs with scripted workers, a stub toolbox and stub gates, so every path is exact.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Request, ToolSpec, Usage
from stepswitch.core.budget import BudgetTracker
from stepswitch.core.task import Budgets, TaskSpec
from stepswitch.ledger import Ledger
from stepswitch.router.escalation import Ladder
from stepswitch.router.features import TaskFeatures
from stepswitch.router.rules import parse_rules
from stepswitch.state import open_project_state
from stepswitch.trajectory.record import Trajectory, read_events
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.builtin_agent import REPEAT_HINT, STUCK_STEPS, TOOL_NAMES, AgentLoop
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, FakeClock, ScriptedModel
from stepswitch.workers.tools import ToolOutput


class StubToolbox:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tests_pass = True

    def specs(self, names: Sequence[str] | None = None) -> list[ToolSpec]:
        return [ToolSpec(name=n) for n in (*TOOL_NAMES, "finish")]

    def execute(self, name: str, arguments: Mapping[str, Any] | str) -> ToolOutput:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        self.calls.append((name, args))
        if name == "read":
            return ToolOutput(text="1:ab|def add(a, b):\n2:cd|    return a - b")
        if name == "edit":
            return ToolOutput(text="--- a/calc.py\n+++ b/calc.py", changed_files=["calc.py"])
        if name == "test":
            ok = self.tests_pass
            return ToolOutput(
                text="3 passed" if ok else "1 failed",
                is_error=not ok,
                data={"exit_code": 0 if ok else 1},
            )
        return ToolOutput(text=f"{name} ok")


@dataclass
class StubVerdict:
    passed: bool
    failed_gate: str | None = None
    report: str = ""
    results: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "failed_gate": self.failed_gate}


class StubGates:
    def __init__(self, outcomes: Sequence[bool]) -> None:
        self.outcomes = list(outcomes)
        self.runs = 0

    def __call__(self, repo: Path, **kwargs: Any) -> StubVerdict:
        passed = self.outcomes[min(self.runs, len(self.outcomes) - 1)]
        self.runs += 1
        if passed:
            return StubVerdict(True)
        return StubVerdict(False, "impacted_tests", "FAILED tests/test_calc.py::test_add")


FIXER = [
    Action(tool="grep", arguments={"pattern": "def add"}),
    Action(tool="read", arguments={"path": "calc.py"}),
    Action(tool="edit", arguments={"path": "calc.py", "edits": []}),
    Action(tool="test", arguments={}),
    Action(tool="finish", arguments={"summary": "Fixed add."}),
]


def fixer_script(texts: Mapping[str, str] | None = None) -> Callable[[Request, int], Action]:
    """Tool turns follow FIXER by how many tool results the transcript has; text steps answer
    by the `Step:` line of their prompt."""
    answers = {"plan": "1. read calc.py", "review": "APPROVE", "summarize": "Fixed add()."}
    answers.update(texts or {})

    def script(request: Request, index: int) -> Action:
        if not request.tools:
            step = request.system_text().split("Step: ", 1)[-1].split("\n", 1)[0]
            return Action(text=answers.get(step, "ok"))
        done = sum(1 for m in request.messages if m.role == "tool")
        return FIXER[min(done, len(FIXER) - 1)]

    return script


def worker(worker_id: str, tier: str, script: Any, **spec: Any) -> Worker:
    model = ScriptedModel(script, usage=Usage(input_tokens=100, output_tokens=20))
    endpoint, _, name = worker_id.partition("/")
    return Worker(WorkerSpec(id=worker_id, endpoint=endpoint, model=name, tier=tier, **spec), model)


def make_loop(
    tmp_path: Path,
    workers: list[Worker],
    gates: StubGates,
    *,
    budgets: Budgets | None = None,
    rules: list[Any] | None = None,
    toolbox: StubToolbox | None = None,
    **spec_fields: Any,
) -> AgentLoop:
    db = open_project_state(tmp_path)
    task_id = "t-test"
    catalog = Catalog(workers)
    clock = FakeClock()
    spec = TaskSpec(
        instruction="Fix add().", repo=tmp_path, budgets=budgets or Budgets(), **spec_fields
    )
    return AgentLoop(
        spec=spec,
        task_id=task_id,
        workdir=tmp_path,
        base=None,
        catalog=catalog,
        toolbox=toolbox or StubToolbox(),
        trajectory=Trajectory(tmp_path, task_id, db=db),
        ledger=Ledger(db, task_id),
        budget=BudgetTracker(spec.budgets, clock=clock),
        features=TaskFeatures(),
        ladder=Ladder.for_levels(catalog.levels()),
        gates=gates,
        rules=rules or [],
        clock=clock,
        diff=lambda: "--- a/calc.py\n+++ b/calc.py\n-    return a - b\n+    return a + b\n",
    )


def events(loop: AgentLoop, kind: str) -> list[dict[str, Any]]:
    return [e for e in read_events(loop.trajectory.run_dir) if e["type"] == kind]


def test_happy_path_plans_works_verifies_reviews_and_summarizes(tmp_path: Path) -> None:
    loop = make_loop(tmp_path, [worker("a/m", "t2", fixer_script())], StubGates([True]))
    outcome = loop.run()
    assert (outcome.status, outcome.stop_reason) == ("done", None)
    assert outcome.summary == "Fixed add()."
    assert outcome.review is not None and outcome.review["approved"]
    steps = events(loop, "step")
    assert [s["step_type"] for s in steps] == [
        "plan",
        "navigate",
        "read",
        "edit",
        "run",
        "run",
        "review",
        "summarize",
    ]
    assert len(loop.ledger.rows) == len(steps) == loop.step
    assert loop.budget.tokens == 120 * len(steps)
    assert [e["name"] for e in events(loop, "tool")] == ["grep", "read", "edit", "test"]
    assert events(loop, "gates")[0]["verdict"]["passed"] is True
    assert "Plan:\n1. read calc.py" in loop.messages[0].text_content()
    assert loop.messages[-1].role == "tool"  # the finish call got its result


def test_rules_switch_endpoints_between_steps_with_reason_and_cost(tmp_path: Path) -> None:
    rules = parse_rules(
        [
            {
                "name": "text-on-a",
                "when": {"step_type": ["plan", "review", "summarize"]},
                "to": {"endpoint": "a"},
            },
            {
                "name": "work-on-b",
                "when": {"step_type": ["navigate", "read", "edit", "run", "triage"]},
                "to": {"endpoint": "b"},
            },
        ]
    )
    workers = [worker("a/m", "t2", fixer_script()), worker("b/m", "t2", fixer_script())]
    loop = make_loop(tmp_path, workers, StubGates([True]), rules=rules)
    assert loop.run().status == "done"
    switches = events(loop, "switch")
    assert [(s["from_endpoint"], s["to_endpoint"]) for s in switches] == [("a", "b"), ("b", "a")]
    for switch in switches:
        assert switch["reason"] and set(switch["cost"]) == {"seconds", "tokens"}
    assert loop.endpoints == ["a", "b"] and loop.switches == 2


def test_gate_failures_escalate_after_k_attempts(tmp_path: Path) -> None:
    low = worker("cpu/tiny", "cpu", fixer_script())
    high = worker("gpu/big", "t3", fixer_script())
    gates = StubGates([False, False, True])
    loop = make_loop(tmp_path, [low, high], gates, review=False)
    outcome = loop.run()
    assert outcome.status == "done" and gates.runs == 3
    escalations = events(loop, "escalation")
    assert len(escalations) == 1 and escalations[0]["from"] == "T0"
    assert loop.budget.escalations == 1
    workers_used = [s["worker"] for s in events(loop, "step")]
    assert "cpu/tiny" in workers_used and workers_used[-2] == "gpu/big"
    # failures go back to the worker as the finish call's result
    failed = [m for m in loop.messages if m.role == "tool" and m.tool_results()[0].is_error]
    assert len(failed) == 2
    assert "FAILED tests/test_calc.py::test_add" in failed[0].tool_results()[0].text()


def test_top_of_the_ladder_fails_the_task(tmp_path: Path) -> None:
    loop = make_loop(
        tmp_path, [worker("a/m", "t2", fixer_script())], StubGates([False]), gate_attempts=2
    )
    outcome = loop.run()
    assert (outcome.status, outcome.stop_reason) == ("failed", "gates_failed")
    assert outcome.verdict == {"passed": False, "failed_gate": "impacted_tests"}


def test_escalation_budget_stops_before_the_ladder_top(tmp_path: Path) -> None:
    workers = [worker(f"w/{t}", t, fixer_script()) for t in ("cpu", "t2", "t3")]
    loop = make_loop(tmp_path, workers, StubGates([False]), budgets=Budgets(escalations=1))
    outcome = loop.run()
    assert (outcome.status, outcome.stop_reason) == ("stopped", "budget_escalations")
    assert loop.budget.escalations == 1


def test_text_only_answers_count_as_finish(tmp_path: Path) -> None:
    talker = worker("a/m", "t2", lambda request, index: Action(text="All done, I think."))
    loop = make_loop(tmp_path, [talker], StubGates([True]), review=False)
    outcome = loop.run()
    assert outcome.status == "done"
    nudges = [
        m for m in loop.messages if m.role == "user" and "Continue the task" in m.text_content()
    ]
    assert len(nudges) == 1


def test_model_errors_take_the_worker_out_and_reroute(tmp_path: Path) -> None:
    class Broken:
        def complete(self, request: Request) -> Any:
            raise ModelError("server", "boom")

    broken = Worker(
        WorkerSpec(
            id="x/broken",
            endpoint="x",
            model="b",
            tier="t2",
            roles=("planner", "editor", "reviewer", "summarizer"),
        ),
        Broken(),
    )
    loop = make_loop(tmp_path, [broken, worker("y/ok", "t2", fixer_script())], StubGates([True]))
    assert loop.run().status == "done"
    errors = events(loop, "model_error")
    assert len(errors) == 1 and errors[0]["worker"] == "x/broken"
    assert "x/broken" in loop.failed


def test_all_workers_failing_ends_with_model_error(tmp_path: Path) -> None:
    class Broken:
        def complete(self, request: Request) -> Any:
            raise ModelError("connection", "down")

    loop = make_loop(
        tmp_path, [Worker(WorkerSpec(id="x/b", tier="t2"), Broken())], StubGates([True])
    )
    outcome = loop.run()
    assert (outcome.status, outcome.stop_reason) == ("failed", "model_error")
    assert "down" in outcome.detail


def test_no_progress_is_detected(tmp_path: Path) -> None:
    reader = worker(
        "a/m", "t2", lambda request, index: Action(tool="read", arguments={"path": "calc.py"})
    )
    loop = make_loop(tmp_path, [reader], StubGates([True]), max_steps=100)
    outcome = loop.run()
    assert (outcome.status, outcome.stop_reason) == ("failed", "stuck")
    assert loop.step <= STUCK_STEPS + 3


def test_review_issues_send_the_work_back_once(tmp_path: Path) -> None:
    script = fixer_script({"review": "ISSUES\n- add a test"})
    loop = make_loop(tmp_path, [worker("a/m", "t2", script)], StubGates([True]))
    outcome = loop.run()
    assert outcome.status == "done"
    reviews = events(loop, "review")
    assert len(reviews) == 1 and not reviews[0]["approved"]
    assert any("A reviewer found issues" in m.text_content() for m in loop.messages)
    assert len(events(loop, "gates")) == 2


@pytest.mark.parametrize(
    ("budgets", "reason"),
    [(Budgets(tokens=300), "budget_tokens"), (Budgets(gpu_seconds=0.5), "budget_gpu_seconds")],
)
def test_budgets_stop_at_step_boundaries(tmp_path: Path, budgets: Budgets, reason: str) -> None:
    reader = Worker(
        WorkerSpec(id="a/m", endpoint="a", model="m", tier="t2", gpu=True),
        ScriptedModel(
            lambda r, i: Action(tool="read", arguments={"path": "calc.py"}),
            usage=Usage(input_tokens=100, output_tokens=20),
            gpu_seconds_per_step=0.2,
        ),
    )
    loop = make_loop(tmp_path, [reader], StubGates([True]), budgets=budgets)
    outcome = loop.run()
    assert (outcome.status, outcome.stop_reason) == ("stopped", reason)
    assert events(loop, "budget")[0]["reason"] == reason


def test_max_steps_stops(tmp_path: Path) -> None:
    reader = worker("a/m", "t2", lambda r, i: Action(tool="grep", arguments={"pattern": str(i)}))
    loop = make_loop(tmp_path, [reader], StubGates([True]), max_steps=5)
    assert (loop.run().stop_reason, loop.step) == ("max_steps", 5)


def test_science_track_without_a_t1_worker_has_no_worker_for_code(tmp_path: Path) -> None:
    loop = make_loop(
        tmp_path, [worker("cpu/tiny", "cpu", fixer_script())], StubGates([True]), track="science"
    )
    outcome = loop.run()
    assert (outcome.status, outcome.stop_reason) == ("failed", "no_worker")
    assert "T0 models never edit code" in outcome.detail


def test_a_repeated_call_with_the_same_result_gets_a_hint(tmp_path: Path) -> None:
    tester = worker(
        "a/m", "t2", lambda request, index: Action(tool="test", arguments={"selector": "-k add"})
    )
    toolbox = StubToolbox()
    toolbox.tests_pass = False
    loop = make_loop(tmp_path, [tester], StubGates([True]), toolbox=toolbox, max_steps=100)
    outcome = loop.run()
    results = [m.content[0] for m in loop.messages if m.role == "tool"]
    texts = [part.content[0].text for part in results]  # type: ignore[union-attr]
    assert texts[0] == "1 failed"  # the first result is passed on as it is
    assert all(text.startswith("1 failed\n\n") and REPEAT_HINT in text for text in texts[1:])
    # the hint is advice only: the run still ends as stuck
    assert (outcome.status, outcome.stop_reason) == ("failed", "stuck")
    tools = events(loop, "tool")
    assert [t["repeated"] for t in tools] == [False] + [True] * (len(tools) - 1)


def test_a_bare_finish_word_counts_as_finish(tmp_path: Path) -> None:
    talker = worker("a/m", "t2", lambda request, index: Action(text="Finish."))
    loop = make_loop(tmp_path, [talker], StubGates([True]), review=False)
    assert loop.run().status == "done"
    assert not [m for m in loop.messages if "Continue the task" in m.text_content()]


def test_context_overflow_caps_the_worker_instead_of_failing_it(tmp_path: Path) -> None:
    class SmallContext:
        """Refuses prompts over 4,000 characters, like a server with a small context."""

        def __init__(self) -> None:
            self.inner = ScriptedModel(
                fixer_script(), usage=Usage(input_tokens=10, output_tokens=5)
            )

        def complete(self, request: Request) -> Any:
            size = len(request.system_text()) + sum(len(m.text_content()) for m in request.messages)
            if not request.tools and size > 4000:
                raise ModelError("too_large", "request exceeds the available context size")
            return self.inner.complete(request)

    small = Worker(WorkerSpec(id="a/m", endpoint="a", model="m", tier="t2"), SmallContext())
    loop = make_loop(tmp_path, [small], StubGates([True]))
    loop.diff = lambda: "+changed line\n" * 400  # a review prompt of about 5,600 characters
    outcome = loop.run()
    assert outcome.status == "done"
    assert "a/m" not in loop.failed and loop.context_caps["a/m"] > 0
    errors = events(loop, "model_error")
    assert errors and {e["kind"] for e in errors} == {"too_large"}
    # the review was retried with the prompt cut in half and then succeeded
    assert outcome.review is not None and outcome.review["approved"]
