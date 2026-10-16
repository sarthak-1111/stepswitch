"""Merging several bench runs into one leaderboard, and rescoring rows under the current rule."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from stepswitch.bench import runner
from stepswitch.bench.report import MANIFEST_FILE, RESULTS_FILE, merge_runs, read_results
from stepswitch.cli import app

HARDWARE = {"os": "Linux", "arch": "x86_64", "cpu_cores": 8, "ram_gb": 16.0, "gpus": 0}


def row(contestant: str, task: str, passed: bool, *, exit_code: int = 0, **extra: Any) -> dict:
    return {
        "suite": "tiny",
        "task": task,
        "contestant": contestant,
        "seed": 0,
        "status": "done",
        "passed": passed,
        "acceptance": [{"command": "python -m pytest -q", "exit_code": exit_code}],
        "tests_modified": False,
        "apply_error": None,
        "tokens": {"input": 100, "output": 10, "cached_input": 0, "reasoning": 0},
        "wall_seconds": 10.0,
        **extra,
    }


def make_run(directory: Path, rows: list[dict], **manifest: Any) -> Path:
    directory.mkdir(parents=True)
    data = {
        "schema": 1,
        "suite": "tiny",
        "command": f"ssw bench run --suite tiny --workers {rows[0]['contestant']} --seeds 1",
        "started": "2026-10-16T08:00:00+00:00",
        "finished": "2026-10-16T09:00:00+00:00",
        "hardware": HARDWARE,
        "version": "0.1.0.dev0",
        "commit": "abc123",
        "seeds": [0],
        "contestants": sorted({r["contestant"] for r in rows}),
        "scoring": runner.SCORING,
        **manifest,
    }
    (directory / MANIFEST_FILE).write_text(json.dumps(data), encoding="utf-8")
    text = "".join(json.dumps(r) + "\n" for r in rows)
    (directory / RESULTS_FILE).write_text(text, encoding="utf-8")
    return directory


def test_merge_keeps_each_runs_provenance(tmp_path: Path) -> None:
    first = make_run(tmp_path / "a", [row("small", "t1", True), row("small", "t2", False)])
    second = make_run(tmp_path / "b", [row("large", "t1", True), row("large", "t2", True)])
    out = merge_runs([first, second], tmp_path / "merged", command="ssw bench report a b")
    rows = read_results(out / RESULTS_FILE)
    assert [(r["contestant"], r["run"]) for r in rows] == [
        ("small", "a"),
        ("small", "a"),
        ("large", "b"),
        ("large", "b"),
    ]
    manifest = json.loads((out / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert [s["run"] for s in manifest["sources"]] == ["a", "b"]
    assert manifest["hardware"] == HARDWARE and manifest["scoring"] == runner.SCORING
    board = json.loads((out / "leaderboard.json").read_text(encoding="utf-8"))
    assert [e["contestant"] for e in board["contestants"]] == ["large", "small"]
    text = (out / "leaderboard.md").read_text(encoding="utf-8")
    assert "- Run `a`: `ssw bench run --suite tiny --workers small --seeds 1`" in text
    assert "commit abc123" in text


def test_merge_refuses_runs_of_different_suites(tmp_path: Path) -> None:
    first = make_run(tmp_path / "a", [row("small", "t1", True)])
    other = make_run(tmp_path / "b", [row("large", "t1", True)], suite="other")
    try:
        merge_runs([first, other], tmp_path / "merged")
    except ValueError as exc:
        assert "different suites" in str(exc)
    else:
        raise AssertionError("merged runs of different suites")


def test_rescore_recomputes_verdicts_from_acceptance_results(tmp_path: Path) -> None:
    rows = [
        row("small", "t1", False, tests_modified=True),  # strict rule failed it
        row("small", "t2", True, exit_code=1),
        row("small", "t3", False, apply_error="patch does not apply"),
    ]
    run = make_run(tmp_path / "old", rows, scoring=None)
    assert runner.rescore(run, commit="def456") == 2
    verdicts = [r["passed"] for r in read_results(run / RESULTS_FILE)]
    assert verdicts == [True, False, False]
    manifest = json.loads((run / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["scoring"] == runner.SCORING
    assert manifest["rescored"]["rows_changed"] == 2 and manifest["rescored"]["commit"] == "def456"
    assert runner.rescore(run) == 0  # idempotent


def test_cli_merges_several_runs_only_with_out(tmp_path: Path) -> None:
    first = make_run(tmp_path / "a", [row("small", "t1", True)])
    second = make_run(tmp_path / "b", [row("large", "t1", False)])
    cli = CliRunner()
    env = {"STEPSWITCH_HOME": str(tmp_path / "home")}
    refused = cli.invoke(app, ["bench", "report", str(first), str(second)], env=env)
    assert refused.exit_code != 0 and "--out" in refused.output
    merged = cli.invoke(
        app, ["bench", "report", str(first), str(second), "--out", str(tmp_path / "m")], env=env
    )
    assert merged.exit_code == 0, merged.output
    assert "| small |" in merged.output and "| large |" in merged.output
