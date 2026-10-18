"""The agent loop with the Phase 5 techniques (ADR 0024): read dedup, distillation with
`@output/<n>`, pruning, the rolling summary, HANDOFF briefs on switches, failovers and relays,
the T0 writer, per-role budgets and effort, the constrained reviewer, path scrubbing, the prefix
registry, and the Phase 2 baseline with all of it off.

Scripted workers, a dictionary-backed toolbox and stub gates make every path exact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Request, ToolSpec, Usage
from stepswitch.context import handoff
from stepswitch.context.settings import ContextSettings
from stepswitch.core.budget import BudgetTracker
from stepswitch.core.task import Budgets, TaskSpec
from stepswitch.ledger import Ledger
from stepswitch.router.escalation import Ladder
from stepswitch.router.features import TaskFeatures
from stepswitch.state import open_project_state
from stepswitch.trajectory.record import Trajectory, read_events
from stepswitch.verify import review as review_module
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.builtin_agent import (
    COMPACT_INTRO,
    HANDOFF_INTRO,
    NUDGE,
    TOOL_NAMES,
    AgentLoop,
)
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, FakeClock, ScriptedModel
from stepswitch.workers.tools import ToolOutput
from tests.unit.test_builtin_agent import StubGates

SCHEMA = Draft202012Validator(handoff.load_schema())
TEXTS = {"plan": "1. read calc.py", "review": "APPROVE", "summarize": "Fixed add()."}


class FileToolbox:
    """`read` and `edit` over a dict of files; `run` and `test` give scripted outputs."""

    max_output_chars = 6000

    def __init__(
        self,
        files: Mapping[str, str],
        *,
        run_output: str = "",
        tests: Sequence[tuple[bool, str]] = ((True, "tests passed: 3 passed"),),
    ) -> None:
        self.files = dict(files)
        self.run_output = run_output
        self.tests = list(tests)
        self.test_runs = 0
        self.test_command = ["python", "-m", "pytest", "-q"]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def specs(self, names: Sequence[str] | None = None) -> list[ToolSpec]:
        return [ToolSpec(name=n) for n in (*TOOL_NAMES, "finish")]

    def execute(self, name: str, arguments: Mapping[str, Any] | str) -> ToolOutput:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        self.calls.append((name, args))
        if name == "read":
            path = args["path"]
            text = self.files[path]
            lines = text.splitlines()
            start, end = int(args.get("start") or 1), int(args.get("end") or len(lines))
            body = "\n".join(f"{i}:ab|{lines[i - 1]}" for i in range(start, end + 1))
            digest = hashlib.sha256(text.encode()).hexdigest()
            data = {"tool": "read", "path": path, "start": start, "end": end}
            data.update(total_lines=len(lines), sha256=digest)
            return ToolOutput(f"{path} lines {start}-{end} of {len(lines)}\n{body}", data=data)
        if name == "edit":
            path = args["path"]
            self.files[path] += "\n# edited"
            data = {"tool": "edit", "path": path, "changed": True, "added": 1, "removed": 0}
            return ToolOutput(f"edited {path}: +1 -0", data=data, changed_files=[path])
        if name == "run":
            text = self.run_output
            data = {"tool": "run", "command": args.get("command"), "exit_code": 1}
            cut = len(text) > self.max_output_chars
            return ToolOutput(text[: self.max_output_chars], data=data, raw=text if cut else None)
        if name == "test":
            ok, text = self.tests[min(self.test_runs, len(self.tests) - 1)]
            self.test_runs += 1
            data = {"tool": "test", "ok": ok, "exit_code": 0 if ok else 1, "counts": {}}
            return ToolOutput(text, is_error=not ok, data=data)
        return ToolOutput(f"{name} ok", data={"tool": name})


def sequence(
    actions: Sequence[Action], texts: Mapping[str, str] | None = None
) -> Callable[[Request, int], Action]:
    """Work turns take the next action whichever worker answers; text steps answer by the
    `Step:` line of their prompt."""
    answers = {**TEXTS, **(texts or {})}
    state = {"next": 0}

    def script(request: Request, _index: int) -> Action:
        if not request.tools:
            step = request.system_text().split("Step: ", 1)[-1].split("\n", 1)[0]
            return Action(text=answers.get(step, "ok"))
        index = state["next"]
        state["next"] += 1
        return actions[min(index, len(actions) - 1)]

    return script


def scripted(worker_id: str, tier: str, script: Any, **spec: Any) -> Worker:
    model = ScriptedModel(script, usage=Usage(input_tokens=100, output_tokens=20))
    endpoint, _, name = worker_id.partition("/")
    return Worker(WorkerSpec(id=worker_id, endpoint=endpoint, model=name, tier=tier, **spec), model)


def build(
    tmp_path: Path,
    workers: list[Worker],
    toolbox: FileToolbox,
    gates: StubGates | None = None,
    *,
    task: Mapping[str, Any] | None = None,
    **extra: Any,
) -> AgentLoop:
    db = open_project_state(tmp_path)
    catalog = Catalog(workers)
    clock = FakeClock()
    spec = TaskSpec(instruction="Fix add().", repo=tmp_path, budgets=Budgets(), **(task or {}))
    return AgentLoop(
        spec=spec,
        task_id="t-ctx",
        workdir=tmp_path,
        base=None,
        catalog=catalog,
        toolbox=toolbox,
        trajectory=Trajectory(tmp_path, "t-ctx", db=db),
        ledger=Ledger(db, "t-ctx"),
        budget=BudgetTracker(spec.budgets, clock=clock),
        features=TaskFeatures(),
        ladder=Ladder.for_levels(catalog.levels()),
        gates=gates or StubGates([True]),
        clock=clock,
        diff=lambda: "--- a/calc.py\n+++ b/calc.py\n",
        **extra,
    )


def events(loop: AgentLoop, kind: str) -> list[dict[str, Any]]:
    return [e for e in read_events(loop.trajectory.run_dir) if e["type"] == kind]


def tool_texts(loop: AgentLoop) -> list[str]:
    return [m.content[0].content[0].text for m in loop.messages if m.role == "tool"]  # type: ignore[union-attr]


def read(path: str, **range_: int) -> Action:
    return Action(tool="read", arguments={"path": path, **range_})


FINISH = Action(tool="finish", arguments={"summary": "Fixed add."})
SMALL = {"calc.py": "def add(a, b):\n    return a - b\n"}
BIG = {name: "\n".join(f"line {i} of {name} " + "x" * 60 for i in range(80)) for name in "abc"}


def assert_valid(record: Mapping[str, Any]) -> None:
    assert not list(SCHEMA.iter_errors(record["brief"]))
    assert record["markdown"] == handoff.render_markdown(record["brief"])
    assert 0 < record["tokens"] <= handoff.MAX_TOKENS
    assert record["brief"]["provenance"]["tokenizer"] == record["tokenizer"]


# ------------------------------------------------------------------------------------------
# TK3, TK4, TK6
# ------------------------------------------------------------------------------------------


def test_an_unchanged_visible_read_is_answered_with_a_stub_once(tmp_path: Path) -> None:
    script = sequence([read("a"), read("a"), read("a"), FINISH])
    loop = build(tmp_path, [scripted("a/m", "t2", script)], FileToolbox(BIG))
    assert loop.run().status == "done"
    first, second, third = tool_texts(loop)[:3]
    assert "unchanged since the read in step 2" in second and "line 3 of a" not in second
    # Asked again right after the stub: the text is shown, never a second stub, and the
    # first copy is then a superseded result that pruning removes.
    assert "line 3 of a" in third
    assert first.startswith("[superseded: a was read again in step 4")
    assert loop.stats["dedup"] == 1
    assert [e["kind"] for e in events(loop, "context")] == ["dedup"]


def test_small_reads_are_never_stubbed(tmp_path: Path) -> None:
    script = sequence([read("calc.py"), read("calc.py"), FINISH])
    loop = build(tmp_path, [scripted("a/m", "t2", script)], FileToolbox(SMALL))
    assert loop.run().status == "done"
    assert all("return a - b" in text for text in tool_texts(loop)[:2])
    assert loop.stats["dedup"] == 0


def test_a_changed_file_is_read_in_full_again(tmp_path: Path) -> None:
    edit = Action(tool="edit", arguments={"path": "calc.py", "edits": []})
    script = sequence([read("calc.py"), edit, read("calc.py"), FINISH])
    loop = build(tmp_path, [scripted("a/m", "t2", script)], FileToolbox(SMALL))
    assert loop.run().status == "done"
    assert "# edited" in tool_texts(loop)[2] and loop.stats["dedup"] == 0


def test_long_outputs_are_distilled_and_the_raw_output_stays_readable(tmp_path: Path) -> None:
    log = "\n".join([f"compiling unit {i} ... ok" for i in range(600)] + ["E: calc.py:2: bad sum"])
    script = sequence(
        [
            Action(tool="run", arguments={"command": "make"}),
            read("@output/1", start=1, end=2),
            read("@output/7"),
            FINISH,
        ]
    )
    loop = build(tmp_path, [scripted("a/m", "t2", script)], FileToolbox(SMALL, run_output=log))
    assert loop.run().status == "done"
    distilled, shown, missing = tool_texts(loop)[:3]
    assert "@output/1" in distilled and "E: calc.py:2: bad sum" in distilled
    assert len(distilled) < 3000
    assert shown.startswith("@output/1 lines 1-2 of 601") and "compiling unit 1" in shown
    assert "no stored output @output/7" in missing
    assert loop.stats["distilled"] == 1


def test_superseded_reads_are_stubbed_in_batches(tmp_path: Path) -> None:
    edit = Action(tool="edit", arguments={"path": "a", "edits": []})
    script = sequence([read("a"), edit, read("a"), read("b"), read("c"), FINISH])
    loop = build(tmp_path, [scripted("a/m", "t2", script)], FileToolbox(BIG))
    assert loop.run().status == "done"
    texts = tool_texts(loop)
    assert texts[0].startswith("[superseded: a was read again in step")
    assert "line 3 of b" in texts[3] and "line 3 of c" in texts[4]
    prunes = events(loop, "prune")
    assert prunes and prunes[0]["tokens"] >= 800
    assert loop.stats["pruned"] >= 1


def test_the_rolling_summary_replaces_old_turns_at_sixty_percent(tmp_path: Path) -> None:
    script = sequence([read("a"), read("b"), read("c"), read("a", start=1, end=5), FINISH])
    settings = ContextSettings(window_tokens=4096)
    loop = build(tmp_path, [scripted("a/m", "t2", script)], FileToolbox(BIG), settings=settings)
    assert loop.run().status == "done"
    assert loop.compactions >= 1
    record = loop.handoffs[0]
    assert record["reason"] == "compaction" and record["to_worker"] == "a/m"
    assert_valid(record)
    assert loop.messages[0].text_content().startswith(COMPACT_INTRO)
    assert any("read b lines" in item for item in record["brief"]["state"]["done"])
    lines = (loop.trajectory.run_dir / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(loop.handoffs)


# ------------------------------------------------------------------------------------------
# TK7: briefs on relays, escalation switches and failovers
# ------------------------------------------------------------------------------------------


def test_the_relay_hands_the_task_over_with_a_brief(tmp_path: Path) -> None:
    script = sequence([read("calc.py"), read("calc.py", start=1, end=1), read("calc.py"), FINISH])
    workers = [scripted("a/m", "t2", script), scripted("b/m", "t2", script)]
    loop = build(tmp_path, workers, FileToolbox(SMALL), relay=("a/m", "b/m"), relay_step=3)
    assert loop.run().status == "done"
    steps = [(e["step_type"], e["worker"]) for e in events(loop, "step")]
    assert steps[:3] == [("plan", "a/m"), ("navigate", "a/m"), ("edit", "b/m")]
    assert {worker for _type, worker in steps[3:]} == {"b/m"}
    [record] = loop.handoffs
    assert record["reason"] == "relay" and record["from_worker"] == "a/m"
    assert record["to_worker"] == "b/m"
    assert_valid(record)
    assert record["brief"]["goal"] == "Fix add()."
    assert any("read calc.py" in item for item in record["brief"]["state"]["done"])
    assert any(f["path"] == "calc.py" for f in record["brief"]["files"])
    first = [m for m in workers[1].model.requests if m.tools][0]  # type: ignore[union-attr]
    assert first.messages[0].text_content().startswith(HANDOFF_INTRO)
    assert len(first.messages) == 1


def test_an_escalation_switch_above_the_threshold_sends_a_brief(tmp_path: Path) -> None:
    script = sequence([read("calc.py"), FINISH, FINISH, read("calc.py"), FINISH])
    workers = [scripted("s/m", "cpu", script), scripted("g/m", "t2", script)]
    settings = ContextSettings(handoff_threshold=10)
    loop = build(
        tmp_path,
        workers,
        FileToolbox(SMALL),
        StubGates([False, False, True]),
        settings=settings,
    )
    assert loop.run().status == "done"
    [record] = loop.handoffs
    assert record["reason"] == "switch"
    assert (record["from_worker"], record["to_worker"]) == ("s/m", "g/m")
    assert record["p_new"] > record["p_current"]
    assert_valid(record)
    assert any("verification failed" in item for item in record["brief"]["state"]["done"])
    assert record["brief"]["failing"][0]["check"] == "impacted_tests gate"
    assert events(loop, "switch")[0]["handoff"] is True


def test_a_failover_above_the_threshold_starts_from_a_brief(tmp_path: Path) -> None:
    script = sequence([read("calc.py"), read("calc.py", start=1, end=1), FINISH])
    calls = {"n": 0}

    def flaky(request: Request, index: int) -> Action:
        calls["n"] += 1
        if calls["n"] == 3:
            raise ModelError("server", "the server went away")
        return script(request, index)

    workers = [scripted("a/m", "t2", flaky), scripted("b/m", "t2", script)]
    loop = build(
        tmp_path, workers, FileToolbox(SMALL), settings=ContextSettings(handoff_threshold=10)
    )
    assert loop.run().status == "done"
    assert loop.failovers == 1
    [record] = loop.handoffs
    assert record["reason"] == "failover" and record["to_worker"] == "b/m"
    assert_valid(record)


def test_no_brief_below_the_threshold_and_none_in_the_baseline(tmp_path: Path) -> None:
    script = sequence([read("calc.py"), FINISH, FINISH, read("calc.py"), FINISH])
    workers = [scripted("s/m", "cpu", script), scripted("g/m", "t2", script)]
    loop = build(tmp_path, workers, FileToolbox(SMALL), StubGates([False, False, True]))
    assert loop.run().status == "done"
    assert loop.handoffs == [] and loop.switches == 1  # the default threshold is 24k tokens
    base = build(
        tmp_path / "base",
        [scripted("s/m", "cpu", sequence([read("calc.py"), FINISH, FINISH, FINISH]))]
        + [scripted("g/m", "t2", sequence([FINISH]))],
        FileToolbox(SMALL),
        StubGates([False, False, True]),
        optimize=False,
        settings=ContextSettings(handoff_threshold=10),
    )
    assert base.run().status == "done"
    assert base.handoffs == [] and base.records == []


# ------------------------------------------------------------------------------------------
# The T0 writer, scrubbing, budgets, the reviewer and the registry
# ------------------------------------------------------------------------------------------


def writer(answer: str) -> Worker:
    def script(request: Request, _index: int) -> Action:
        return Action(text=answer)

    return scripted("w/t0", "cpu", script, roles=("summarizer", "handoff_writer"))


def test_the_t0_writer_fills_the_state_and_is_accounted(tmp_path: Path) -> None:
    state = {
        "done": ["Read calc.py: add() subtracts."],
        "decisions": [{"what": "Change - to +", "why": "the test expects a sum"}],
        "open_questions": ["Are there other callers?"],
    }
    script = sequence([read("calc.py"), read("calc.py", start=1, end=1), read("calc.py"), FINISH])
    workers = [scripted("a/m", "t2", script), scripted("b/m", "t2", script)]
    summarizer = writer(json.dumps(state))
    loop = build(
        tmp_path,
        workers,
        FileToolbox(SMALL),
        relay=("a/m", "b/m"),
        relay_step=3,
        summarizer=summarizer,
    )
    assert loop.run().status == "done"
    [record] = loop.handoffs
    assert record["writer"]["writer"] == "model" and record["writer"]["worker"] == "w/t0"
    brief = record["brief"]
    assert brief["state"]["done"] == state["done"]
    assert brief["state"]["decisions"][-1] == state["decisions"][0]
    assert brief["state"]["decisions"][0]["what"] == "Follow the plan"
    assert brief["state"]["open_questions"] == state["open_questions"]
    request = summarizer.model.requests[0]  # type: ignore[union-attr]
    assert request.response_format is not None and request.response_format.type == "json_schema"
    rows = [row for row in loop.ledger.rows if (row.data or {}).get("purpose") == "handoff"]
    assert len(rows) == 1 and rows[0].endpoint == "w"
    assert [e["purpose"] for e in events(loop, "aux")] == ["handoff"]


def test_an_unusable_writer_answer_falls_back_to_the_work_log(tmp_path: Path) -> None:
    script = sequence([read("calc.py"), read("calc.py", start=1, end=1), read("calc.py"), FINISH])
    workers = [scripted("a/m", "t2", script), scripted("b/m", "t2", script)]
    loop = build(
        tmp_path,
        workers,
        FileToolbox(SMALL),
        relay=("a/m", "b/m"),
        relay_step=3,
        summarizer=writer("I cannot do that."),
    )
    assert loop.run().status == "done"
    [record] = loop.handoffs
    assert record["writer"]["writer"] == "fallback"
    assert any("read calc.py" in item for item in record["brief"]["state"]["done"])
    assert_valid(record)


def test_briefs_carry_no_absolute_paths(tmp_path: Path) -> None:
    failing = f"FAILED {tmp_path / 'tests' / 'test_calc.py'}::test_add - assert 3 == 4"
    test = Action(tool="test", arguments={})
    script = sequence([read("calc.py"), test, read("calc.py"), FINISH])
    workers = [scripted("a/m", "t2", script), scripted("b/m", "t2", script)]
    loop = build(
        tmp_path,
        workers,
        FileToolbox(SMALL, tests=[(False, failing), (True, "tests passed")]),
        relay=("a/m", "b/m"),
        relay_step=4,
        scrub_paths=[(str(tmp_path), ".")],
    )
    assert loop.run().status == "done"
    [record] = loop.handoffs
    text = json.dumps(record)
    assert str(tmp_path) not in text and tmp_path.as_posix() not in text
    assert "test_calc.py::test_add" in record["brief"]["failing"][0]["excerpt"]


EDIT = Action(tool="edit", arguments={"path": "calc.py", "text": "return a + b"})
TEST = Action(tool="test", arguments={})


def nudged(requests: Sequence[Request]) -> bool:
    return any(
        message.role == "user" and NUDGE in message.text_content()
        for request in requests
        for message in request.messages
    )


def test_a_text_answer_after_the_tests_pass_on_a_change_finishes(tmp_path: Path) -> None:
    done = Action(text="The fix is in and the tests pass.")
    worker = scripted("a/m", "t2", sequence([read("calc.py"), EDIT, TEST, done, FINISH]))
    loop = build(tmp_path, [worker], FileToolbox(SMALL))
    assert loop.run().status == "done"
    work = [r for r in worker.model.requests if r.tools]  # type: ignore[union-attr]
    # no nudge turn: the answer after the passing tests was taken as finish
    assert len(work) == 4 and not nudged(work)
    assert loop.finish_summary == "The fix is in and the tests pass."
    # the baseline nudges, as Phase 2 did
    plain = scripted("a/m", "t2", sequence([read("calc.py"), EDIT, TEST, done, FINISH]))
    base = build(tmp_path / "base", [plain], FileToolbox(SMALL), optimize=False)
    assert base.run().status == "done"
    base_work = [r for r in plain.model.requests if r.tools]  # type: ignore[union-attr]
    assert len(base_work) == 5 and nudged(base_work)


def test_a_text_answer_without_a_tested_change_is_still_nudged(tmp_path: Path) -> None:
    said = Action(text="The tests pass already; let me look at add().")
    # tests pass before any change: not a finished task
    early = scripted("a/m", "t2", sequence([TEST, said, read("calc.py"), EDIT, FINISH]))
    loop = build(tmp_path, [early], FileToolbox(SMALL))
    assert loop.run().status == "done"
    assert nudged([r for r in early.model.requests if r.tools])  # type: ignore[union-attr]
    # a change after the last passing test run: not verified yet
    late = scripted("a/m", "t2", sequence([EDIT, TEST, EDIT, said, FINISH]))
    loop = build(tmp_path / "late", [late], FileToolbox(SMALL))
    assert loop.run().status == "done"
    assert nudged([r for r in late.model.requests if r.tools])  # type: ignore[union-attr]


def test_role_budgets_and_effort_go_to_reasoning_workers_only(tmp_path: Path) -> None:
    script = sequence([read("calc.py"), FINISH])
    thinker = scripted("a/m", "t2", script, reasoning=True)
    loop = build(tmp_path, [thinker], FileToolbox(SMALL))
    assert loop.run().status == "done"
    requests = thinker.model.requests  # type: ignore[union-attr]
    plan, navigate = requests[0], requests[1]
    assert (plan.effort, plan.max_output_tokens) == ("high", 1200)
    assert (navigate.effort, navigate.max_output_tokens) == ("medium", 2048)
    # every work step of the transcript has one effort, so the prompt prefix stays cacheable
    assert {r.effort for r in requests if r.tools} == {"medium"}
    plain = scripted("a/m", "t2", sequence([read("calc.py"), FINISH]))
    loop = build(tmp_path / "plain", [plain], FileToolbox(SMALL))
    assert loop.run().status == "done"
    assert {r.effort for r in plain.model.requests} == {None}  # type: ignore[union-attr]
    base = scripted("a/m", "t2", sequence([read("calc.py"), FINISH]), reasoning=True)
    loop = build(tmp_path / "base", [base], FileToolbox(SMALL), optimize=False)
    assert loop.run().status == "done"
    sent = base.model.requests  # type: ignore[union-attr]
    assert {r.effort for r in sent} == {None}
    assert (sent[0].max_output_tokens, sent[1].max_output_tokens) == (400, 2048)


def test_the_reviewer_answers_under_a_json_schema(tmp_path: Path) -> None:
    issue = {"severity": "high", "file": "calc.py", "line": 2, "note": "add() still subtracts"}
    issues = json.dumps({"approve": False, "issues": [issue]})
    script = sequence(
        [read("calc.py"), FINISH, read("calc.py", start=1, end=1), FINISH],
        {"review": issues},
    )
    model = scripted("a/m", "t2", script)
    loop = build(tmp_path, [model], FileToolbox(SMALL))
    outcome = loop.run()
    assert outcome.status == "done"
    [review] = events(loop, "review")
    assert review["approved"] is False and review["constrained"] is True
    assert review["text"] == "- [high] calc.py:2: add() still subtracts"
    assert review["issues"] == [issue] and review["blocking"] is True
    sent = [r for r in model.model.requests if r.response_format is not None]  # type: ignore[union-attr]
    assert sent and sent[0].response_format.name == "review"  # type: ignore[union-attr]
    approved = review_module.parse('{"approve": true, "issues": []}')
    assert approved is not None and approved.approve and approved.constrained
    plain = review_module.parse("APPROVE")
    assert plain is not None and plain.approve and not plain.constrained
    assert review_module.parse('{"approve": "yes"}') is None


def test_the_prefix_registry_says_what_each_server_holds(tmp_path: Path) -> None:
    script = sequence([read("calc.py"), read("calc.py", start=1, end=1), FINISH])
    workers = [scripted("a/m", "t2", script), scripted("b/m", "t2", script)]
    loop = build(tmp_path, workers, FileToolbox(SMALL))
    assert loop.run().status == "done"
    decisions = [e for e in events(loop, "decision") if e["step_type"] in {"read", "edit"}]
    assert decisions
    last = decisions[-1]
    by_worker = {c["worker"]: c["estimate"]["switch_tokens"] for c in last["candidates"]}
    current = last["previous"]
    other = ({"a/m", "b/m"} - {current}).pop()
    assert by_worker[current] < by_worker[other]
    assert loop.registry.held[current].chain.tokens > 0
