"""The harness loop (`stepswitch.workers.harness_loop`, ADR 0029): `ssw run --harness` end to end.

A stand-in for the Claude Code harness (`tests/fixtures/harness/fake_claude.py`) talks to the
task's own proxy over HTTP in the messages format, as the real harness does; scripted workers
answer behind the proxy. The tests check the whole loop: the proxy bound to the task records
every request as a step of it, gates decide, a failed check sends the harness back with the
report in its resumed session, budgets stop it, and the user's credentials never reach it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from stepswitch.compat.ir import Request, Usage
from stepswitch.core.orchestrator import run_task
from stepswitch.core.task import Budgets, TaskSpec
from stepswitch.state import open_project_state
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, ScriptedModel
from tests.claims.toy import TOY_INSTRUCTION, toy_repo

FAKE_HARNESS = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "fake_claude.py"
APPROVE = json.dumps({"approve": True, "issues": []})


def edit(old: str, new: str) -> Action:
    tail = "\n\n\ndef sub"
    return Action(
        tool="Edit",
        arguments={"file_path": "calc.py", "old_string": old + tail, "new_string": new + tail},
    )


def coder(*, wrong_first: bool = False, always_wrong: bool = False, stuck: bool = False) -> Worker:
    """A scripted worker behind the proxy: it fixes add() (after a wrong first try if asked)."""

    def script(request: Request, _index: int) -> Action:
        if not request.tools:
            return Action(text=APPROVE)  # the review, a text step of the built-in agent
        last = request.messages[-1]
        if stuck:
            return edit("no such text", "anything")
        if last.tool_results():
            return Action(text="Fixed add().")
        retry = "checks failed" in last.text_content()
        if always_wrong or (wrong_first and not retry):
            return edit("return a - b" if not retry else "return a * b", "return a * b")
        return edit("return a * b" if retry else "return a - b", "return a + b")

    model = ScriptedModel(script, usage=Usage(input_tokens=20, output_tokens=5))
    spec = WorkerSpec(
        id="fake/coder", endpoint="fake", model="coder", harness="fake", tier="t2", context=65536
    )
    return Worker(spec, model)


def project(tmp_path: Path) -> Path:
    repo = toy_repo("calc-bug", tmp_path / "repo")
    local = repo / ".stepswitch"
    local.mkdir(exist_ok=True)
    (local / "local.toml").write_text(
        f"[harness.claude-code]\ncommand = ['{sys.executable}', '{FAKE_HARNESS}']\n",
        encoding="utf-8",
    )
    return repo


def events(result: Any) -> list[dict[str, Any]]:
    path = Path(result.run_dir) / "events.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def spec_for(repo: Path, **fields: Any) -> TaskSpec:
    return TaskSpec(instruction=TOY_INSTRUCTION, repo=repo, harness="claude-code", **fields)


def test_a_harness_run_is_one_task_through_its_own_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "the-user's-own-key")  # never reaches the harness
    result = run_task(spec_for(repo), root=repo, catalog=Catalog([coder()]))
    assert result.status == "done", result.summary
    assert result.changed_files == ["calc.py"] and "+    return a + b" in result.diff
    assert result.summary == "Fixed add()." and result.endpoints == ["fake"]
    assert result.steps == 3  # two harness requests through the proxy, then the review
    assert result.review is not None and result.review["approved"]
    kinds = [event["type"] for event in events(result)]
    for kind in ("harness", "harness_start", "harness_event", "harness_end", "gates", "review"):
        assert kind in kinds, kind
    steps = [e for e in events(result) if e["type"] == "step"]
    assert [e["step"] for e in steps] == [1, 2, 3]
    assert all(e.get("proxy", {}).get("format") == "messages" for e in steps[:2])
    decisions = [e for e in events(result) if e["type"] == "decision"]
    assert decisions[1]["rule"] == "open turn"  # the tool loop stays on its worker
    transcript = Path(result.run_dir) / "harness" / "attempt-1.jsonl"
    lines = [json.loads(line) for line in transcript.read_text("utf-8").splitlines()]
    assert lines[0]["subtype"] == "init" and lines[-1]["type"] == "result"
    db = open_project_state(repo)
    rows = db.execute(
        "SELECT step, data FROM ledger WHERE task_id = ? ORDER BY step", (result.task_id,)
    ).fetchall()
    db.close()
    assert [row[0] for row in rows] == [1, 2, 3]
    assert [json.loads(row[1]).get("proxy") for row in rows[:2]] == [True, True]
    # The harness's state (its config dir, its sessions) is gone with the run.
    assert not (repo / ".stepswitch" / "harness" / result.task_id).exists()
    text = transcript.read_text("utf-8")
    assert "the-user's-own-key" not in text
    # The prompt gives the test command as the harness's shell takes it (forward slashes),
    # to be run as written: harnesses allow it by prefix.
    [start] = [e for e in events(result) if e["type"] == "harness_start"]
    assert "-m pytest" in start["prompt"] and chr(92) not in start["prompt"]
    assert "with this command, exactly as written" in start["prompt"]


def test_a_failed_check_sends_the_harness_back_in_its_session(tmp_path: Path) -> None:
    repo = project(tmp_path)
    spec = spec_for(repo, review=False)
    result = run_task(spec, root=repo, catalog=Catalog([coder(wrong_first=True)]))
    assert result.status == "done", result.summary
    assert "+    return a + b" in result.diff
    starts = [e for e in events(result) if e["type"] == "harness_start"]
    assert [e["resume"] for e in starts] == [False, True]
    assert "checks failed" in starts[1]["prompt"]
    gates = [e for e in events(result) if e["type"] == "gates"]
    assert [g["verdict"]["passed"] for g in gates] == [False, True]


def test_failed_checks_escalate_and_end_on_the_last_rung(tmp_path: Path) -> None:
    repo = project(tmp_path)
    spec = spec_for(repo, review=False, gate_attempts=1)
    result = run_task(spec, root=repo, catalog=Catalog([coder(always_wrong=True)]))
    assert result.status == "failed" and result.stop_reason == "gates_failed"
    assert "no higher rung" in result.summary


def test_the_budget_stops_the_harness(tmp_path: Path) -> None:
    repo = project(tmp_path)
    spec = spec_for(repo, review=False, budgets=Budgets(tokens=100))
    result = run_task(spec, root=repo, catalog=Catalog([coder(stuck=True)]))
    assert result.status == "stopped" and result.stop_reason == "budget_tokens"
    assert result.tokens["input_tokens"] + result.tokens["output_tokens"] >= 100


def test_harness_runs_cannot_be_replayed(tmp_path: Path) -> None:
    from stepswitch.trajectory.replay import ReplayError, replay_run

    repo = project(tmp_path)
    first = run_task(spec_for(repo, review=False), root=repo, catalog=Catalog([coder()]))
    assert first.status == "done"
    with pytest.raises(ReplayError, match="cannot be replayed"):
        replay_run(Path(str(first.run_dir)), repo)
