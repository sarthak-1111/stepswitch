"""Replay and fork of recorded runs (SPEC §11, ADR 0027; claim C-014)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from stepswitch.cli import app
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Message, Request, Response, TextPart
from stepswitch.trajectory.record import put_blob, read_events
from stepswitch.trajectory.replay import (
    CandidateReplay,
    Compression,
    ModelCall,
    ReplayClock,
    ReplayError,
    Stream,
    find_run,
    load_recording,
    replay_run,
)
from stepswitch.workers.base import Worker
from stepswitch.workers.builtin_agent import _RecordedCompressor
from stepswitch.workers.fake import Action, ScriptedModel
from tests.claims import replay_fixture

STEP_FIELDS = ("step", "step_type", "worker", "tool_calls", "finish_reason", "usage")


def _of(run_dir: Path | str, kind: str, *fields: str) -> list[dict[str, Any]]:
    return [{f: e.get(f) for f in fields} for e in read_events(Path(run_dir)) if e["type"] == kind]


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> dict[str, replay_fixture.Recorded]:
    base = tmp_path_factory.mktemp("recorded")
    return {
        name: replay_fixture.record(name, base)
        for name in ("calc-fix", "failover", "relay", "time-budget", "best-of-2")
    }


def test_replay_reproduces_a_run_without_asking_any_model(
    recorded: dict[str, replay_fixture.Recorded],
) -> None:
    run = recorded["calc-fix"]
    assert run.result.status == "done"
    live = run.catalog()
    replayed = replay_run(run.result.task_id, run.root, catalog=live)
    report = replayed.comparison
    assert report["identical_diffs"] and report["identical_gate_results"], report
    assert report["departures"] == [] and report["fork"] is None
    calls = report["model_calls"]
    assert calls["from_recording"] == calls["recorded"] == run.result.steps and calls["live"] == 0
    assert replay_fixture.model_calls(live) == 0  # no model was asked
    assert report["tools_run_again"] == 4  # two edits and two test runs
    then, now = run.result.run_dir, replayed.result.run_dir
    assert _of(now, "step", *STEP_FIELDS) == _of(then, "step", *STEP_FIELDS)
    assert _of(now, "decision", "step", "chosen", "reason") == _of(
        then, "decision", "step", "chosen", "reason"
    )
    assert _of(now, "tool", "name", "arguments", "output") == _of(
        then, "tool", "name", "arguments", "output"
    )
    assert replayed.result.tokens == run.result.tokens  # the recorded usage is charged
    [start] = _of(now, "task_start", "replay_of", "from_step", "spec")
    assert start["replay_of"] == run.result.task_id and start["from_step"] is None
    assert start["spec"]["apply"] is False
    types = {e["type"] for e in read_events(Path(now))}
    assert "learning_update" not in types and "classify" in types


def test_replay_repeats_failovers_and_handoff_briefs(
    recorded: dict[str, replay_fixture.Recorded],
) -> None:
    failover = recorded["failover"]
    assert failover.result.failovers == 1
    replayed = replay_run(failover.result.task_id, failover.root, catalog=failover.catalog())
    assert replayed.comparison["identical_diffs"] and not replayed.comparison["departures"]
    fields = ("step", "worker", "kind", "message")
    assert _of(replayed.result.run_dir, "model_error", *fields) == _of(
        failover.result.run_dir, "model_error", *fields
    )
    assert replayed.result.failovers == 1

    relay = recorded["relay"]
    assert relay.result.handoffs == 1
    live = relay.catalog()
    replayed = replay_run(relay.result.task_id, relay.root, catalog=live)
    report = replayed.comparison
    assert report["identical_diffs"] and report["identical_gate_results"], report
    assert report["model_calls"]["from_recording"] == relay.result.steps + 1  # and the brief
    assert replay_fixture.model_calls(live) == 0

    def briefs(run_dir: str, task_id: str) -> list[Any]:
        # A brief names its task, and a replay is a run of its own.
        path = Path(run_dir) / "handoffs.jsonl"
        text = path.read_text(encoding="utf-8").replace(task_id, "<task>")
        keys = ("step", "reason", "to_worker", "tokens", "markdown")
        return [{k: json.loads(line)[k] for k in keys} for line in text.splitlines()]

    assert briefs(replayed.result.run_dir, replayed.result.task_id) == briefs(
        relay.result.run_dir, relay.result.task_id
    )
    assert _of(replayed.result.run_dir, "aux", "purpose", "worker", "usage") == _of(
        relay.result.run_dir, "aux", "purpose", "worker", "usage"
    )


def test_replay_stops_where_the_time_budget_stopped_the_recording(
    recorded: dict[str, replay_fixture.Recorded],
) -> None:
    run = recorded["time-budget"]
    assert run.result.stop_reason == "budget_wall_seconds" and run.result.diff
    replayed = replay_run(run.result.task_id, run.root, catalog=run.catalog())
    assert replayed.result.stop_reason == "budget_wall_seconds"
    assert replayed.result.steps == run.result.steps
    report = replayed.comparison
    assert report["identical_diffs"] and not report["departures"], report
    # the replay's clock counts the recorded 100 s per model call
    assert replayed.result.wall_seconds >= 100 * run.result.steps


def test_replay_reproduces_every_best_of_n_candidate(
    recorded: dict[str, replay_fixture.Recorded],
) -> None:
    run = recorded["best-of-2"]
    assert run.result.best_of == 2
    replayed = replay_run(run.result.task_id, run.root, catalog=run.catalog())
    report = replayed.comparison
    assert set(report["diffs"]) == {"diff.patch", "candidate-0.patch", "candidate-1.patch"}
    assert all(d["identical"] for d in report["diffs"].values())
    assert set(report["gate_results"]["replayed"]) == {"candidate 0", "candidate 1"}
    assert report["identical_gate_results"] and not report["departures"]
    assert replayed.result.winner == run.result.winner
    with pytest.raises(ReplayError, match="best-of-N"):
        replay_run(run.result.task_id, run.root, from_step=2, catalog=run.catalog())


def test_replay_fork_continues_live_on_another_worker(
    recorded: dict[str, replay_fixture.Recorded],
) -> None:
    run = recorded["calc-fix"]
    steps = _of(run.result.run_dir, "step", "step", "worker", "tool_calls")
    other = "fake/alpha" if steps[3]["worker"] != "fake/alpha" else "fake/beta"
    live = run.catalog()
    forked = replay_run(run.result.task_id, run.root, from_step=4, worker=other, catalog=live)
    report = forked.comparison
    assert report["fork"] == {"from_step": 4, "worker": other}
    now = _of(forked.result.run_dir, "step", "step", "worker", "tool_calls")
    assert now[:3] == steps[:3]  # replayed
    assert {s["worker"] for s in now[3:]} == {other}  # live, on the fork's worker
    assert live.get(other).model.calls == len(now) - 3 == report["model_calls"]["live"]
    assert report["model_calls"]["from_recording"] == 3
    [start] = _of(forked.result.run_dir, "task_start", "replay_of", "from_step", "fork_worker")
    assert start == {"replay_of": run.result.task_id, "from_step": 4, "fork_worker": other}
    # --worker alone forks at the first call
    again = replay_run(run.result.task_id, run.root, worker="alpha", catalog=run.catalog())
    assert again.comparison["fork"] == {"from_step": 1, "worker": "fake/alpha"}
    assert again.comparison["model_calls"]["from_recording"] == 0


def test_replay_finds_runs_and_refuses_what_it_cannot_replay(
    recorded: dict[str, replay_fixture.Recorded], tmp_path: Path
) -> None:
    run = recorded["calc-fix"]
    task_id = run.result.task_id
    run_dir = Path(run.result.run_dir)
    assert find_run(run.root, task_id) == run_dir
    assert find_run(run.root, task_id[:-2]) == run_dir
    assert find_run(run.root, run_dir) == run_dir
    assert find_run(run.root, "last").name >= task_id
    with pytest.raises(ReplayError, match="no recorded run"):
        find_run(run.root, "t1999")
    with pytest.raises(ReplayError, match="between 1 and"):
        replay_run(task_id, run.root, from_step=99, catalog=run.catalog())
    with pytest.raises(ReplayError, match="unknown worker"):
        replay_run(task_id, run.root, from_step=2, worker="nobody", catalog=run.catalog())
    # a run recorded without the full answers (before Phase 7)
    old = tmp_path / "runs" / "t-old"
    old.mkdir(parents=True)
    lines = []
    for event in read_events(run_dir):
        event.pop("response_blob", None)
        lines.append(json.dumps(event))
    (old / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ReplayError, match="full model answers"):
        load_recording(old, run.root)
    (old / "events.jsonl").write_text(lines[0] + "\n", encoding="utf-8")
    with pytest.raises(ReplayError, match="workers"):
        load_recording(old, run.root)
    with pytest.raises(ReplayError, match="no recorded run"):
        load_recording(tmp_path, run.root)


def _tamper(run_dir: Path, root: Path) -> None:
    """Make the recorded fix of add() subtract again, as if the recording had been edited."""
    path = run_dir / "events.jsonl"
    events = read_events(run_dir)
    edits = [e for e in events if e["type"] == "step" and e.get("tool_calls") == ["edit"]]
    target = edits[-1]
    blob = root / ".stepswitch" / "blobs" / target["response_blob"][:2] / target["response_blob"]
    text = blob.read_text(encoding="utf-8")
    assert replay_fixture.RIGHT.strip() in text
    target["response_blob"] = put_blob(root, text.replace("a + b", "a - b"))
    path.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8"
    )


def test_replay_reports_where_a_tampered_recording_departs(tmp_path: Path) -> None:
    run = replay_fixture.record("calc-fix", tmp_path)
    _tamper(Path(run.result.run_dir), run.root)
    replayed = replay_run(run.result.task_id, run.root, catalog=run.catalog())
    report = replayed.comparison
    assert not report["identical_diffs"]
    assert any("edit with other arguments than recorded" in n for n in report["departures"])
    result = CliRunner().invoke(
        app, ["--project", str(run.root), "replay", run.result.task_id, "--json"]
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["identical_diffs"] is False


def test_replay_cli_replays_without_configured_models(
    recorded: dict[str, replay_fixture.Recorded],
) -> None:
    run = recorded["calc-fix"]
    runner = CliRunner()
    # the project configures no endpoints: a replay needs none
    shown = runner.invoke(app, ["--project", str(run.root), "replay", run.result.task_id])
    assert shown.exit_code == 0, shown.output
    assert f"Replayed {run.result.task_id} as " in shown.output
    assert "Diffs: identical; gate results: identical" in shown.output
    assert f"Model calls: {run.result.steps} from the recording, 0 live" in shown.output
    as_json = runner.invoke(
        app, ["--project", str(run.root), "replay", run.result.task_id, "--json"]
    )
    data = json.loads(as_json.output)
    assert data["identical_diffs"] and data["result"]["status"] == "done"
    missing = runner.invoke(app, ["--project", str(run.root), "replay", "t1999"])
    assert missing.exit_code != 0 and "no recorded run" in missing.output


def test_replay_model_serves_answers_failures_then_the_live_model() -> None:
    answer = Response(message=Message(role="assistant", content=[TextPart(text="hi")]))
    recorded = answer.model_dump(mode="json")
    stream = Stream(
        calls=[
            ModelCall(1, 1, "step", "fake/w", 2.5, response=recorded),
            ModelCall(2, 2, "step", "fake/w", 0.5, error=("overloaded", "busy")),
            ModelCall(3, 2, "aux", "fake/w", 1.0, response=recorded),
        ]
    )
    clock = ReplayClock(base=lambda: 0.0)
    replay = CandidateReplay(None, stream, clock, from_step=3)
    live = ScriptedModel([Action(text="live")])
    worker = Worker(replay_fixture.spec_of("w"), live)
    request = Request(model="w")
    model = replay.model(worker, "step")
    assert model.complete(request).response.message.text_content() == "hi"
    assert clock() == 2.5
    with pytest.raises(ModelError) as failed:
        model.complete(request)
    assert failed.value.kind == "overloaded" and str(failed.value) == "busy"
    # the next recorded call is a brief: a step call here departs from the recording
    with pytest.raises(ModelError, match="no answer"):
        model.complete(request)
    assert replay.notes and "aux call" in replay.notes[0]
    assert replay.model(worker, "aux").complete(request).response.message.text_content() == "hi"
    assert replay.decision(3) is None and replay.forked  # the fork
    assert model.complete(request).response.message.text_content() == "live"
    assert live.calls == 1 and replay.served == 3 and replay.live == 1


def test_replay_compressions_are_recorded_and_given_back() -> None:
    class Loop:
        step = 4

        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, Any]]] = []
            self.trajectory = self

        def emit(self, kind: str, **data: Any) -> None:
            self.events.append((kind, data))

        def blob(self, text: str) -> str:
            return f"blob:{text}"

    class Inner:
        def compress(self, text: str, rate: float) -> str:
            if text == "boom":
                raise ValueError("no server")
            return text[: int(len(text) * rate)]

    loop = Loop()
    recorder = _RecordedCompressor(Inner(), loop)  # type: ignore[arg-type]
    assert recorder.compress("abcdefgh", 0.5) == "abcd"
    with pytest.raises(ValueError):
        recorder.compress("boom", 0.5)
    assert [kind for kind, _ in loop.events] == ["compress", "compress"]
    assert loop.events[0][1]["output_blob"] == "blob:abcd"
    assert "no server" in loop.events[1][1]["error"]

    stream = Stream(compressions=[Compression(1, "abcd", None), Compression(2, None, "no server")])
    replay = CandidateReplay(None, stream, ReplayClock())
    given = replay.compressor(Inner())
    assert given.compress("anything", 0.5) == "abcd"
    with pytest.raises(RuntimeError, match="no server"):
        given.compress("anything", 0.5)
    with pytest.raises(RuntimeError, match="no compression"):
        given.compress("more", 0.5)
    assert replay.notes == ["a compression the recording does not have"]
