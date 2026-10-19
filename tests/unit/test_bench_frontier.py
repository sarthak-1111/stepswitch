"""The bench's frontier comparison (ADR 0034): harness and native contestants, tokens per model,
hosted tokens and infrastructure errors in every row, and the leaderboard's token columns.

Native runs use a stand-in for Claude Code on its own login
(`tests/fixtures/harness/fake_claude_native.py`), which fails its run unless it was started
isolated and without the user's keys.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from stepswitch import config as config_module
from stepswitch.bench import native, runner
from stepswitch.bench.report import MANIFEST_FILE, RESULTS_FILE
from stepswitch.bench.suites import load_suite
from stepswitch.core.task import TaskResult, TaskSpec
from tests.unit.bench_project import make_project

FAKE_NATIVE = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "fake_claude_native.py"
SONNET = "native:claude-code:claude-sonnet-5-5"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path / "project")


def harness_config(root: Path, mode: str = "fix") -> Any:
    table = {"command": [sys.executable, str(FAKE_NATIVE)], "env": {"FAKE_NATIVE_MODE": mode}}
    return config_module.load(root, overrides={"harness": {"claude-code": table}})


def read_rows(out: Path) -> list[dict[str, Any]]:
    lines = (out / RESULTS_FILE).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_harness_and_native_contestants_parse() -> None:
    harness, sonnet, router = runner.parse_contestants(f"harness:claude-code,{SONNET},router")
    assert (harness.harness, harness.native, harness.worker) == ("claude-code", None, None)
    assert (sonnet.harness, sonnet.native) == ("claude-code", "claude-sonnet-5-5")
    assert harness.pooled and router.pooled and not sonnet.pooled
    assert native.parse("native:claude-code:opus") == ("claude-code", "opus")
    with pytest.raises(ValueError, match="unknown harness contestant"):
        runner.parse_contestants("harness:no-such-harness")
    with pytest.raises(ValueError, match="unknown harness"):
        runner.parse_contestants("native:no-such-harness:model")
    with pytest.raises(ValueError, match="no native runs"):
        runner.parse_contestants("native:pi:model")
    with pytest.raises(ValueError, match="names no model"):
        runner.parse_contestants("native:claude-code:")


def test_a_native_run_is_scored_with_its_tokens_per_model_all_hosted(project: Path) -> None:
    suite = load_suite("tiny", project)
    out = project / "bench" / "results" / "native"
    messages: list[str] = []
    runner.run_bench(
        suite,
        runner.parse_contestants(SONNET),
        root=project,
        out_dir=out,
        config=harness_config(project),
        echo=messages.append,
    )
    (row,) = read_rows(out)
    assert row["passed"] and row["status"] == "done", row
    assert row["changed_files"] == ["calc.py"] and not row["infra_error"]
    assert row["models"] == {
        "claude-sonnet-5-5": {
            "input": 1150,
            "cached_input": 1000,
            "cache_write": 50,
            "output": 20,
            "hosted": True,
        },
        "fake-auxiliary-model": {
            "input": 10,
            "cached_input": 0,
            "cache_write": 0,
            "output": 2,
            "hosted": True,
        },
    }
    assert row["tokens"] == {
        "input": 1160,
        "output": 22,
        "cached_input": 1000,
        "cache_write": 50,
        "reasoning": 0,
    }
    assert row["hosted_tokens"] == 1182
    harness = row["harness"]
    assert harness["reported_model"] == "claude-sonnet-5-5" and harness["auth"] == "none"
    assert harness["version"] == "0.0.0-fake" and harness["reported_cost"] == 0.012
    manifest = json.loads((out / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert set(manifest["harnesses"]) == {"claude-code"} and manifest["isolated_harnesses"]
    assert any("runs on its own login" in m and SONNET in m for m in messages), messages
    board = (out / "leaderboard.md").read_text(encoding="utf-8")
    assert "| Median uncached | Median hosted |" in board
    assert "| 182 | 1,182 |" in board  # uncached: 1160 - 1000 + 22; hosted: 1160 + 22


@pytest.mark.parametrize("mode", ["limit", "crash"])
def test_a_native_run_cut_short_by_the_service_is_an_infrastructure_error(
    project: Path, mode: str
) -> None:
    out = project / "bench" / "results" / mode
    runner.run_bench(
        load_suite("tiny", project),
        runner.parse_contestants(SONNET),
        root=project,
        out_dir=out,
        config=harness_config(project, mode),
    )
    (row,) = read_rows(out)
    assert not row["passed"] and row["infra_error"] and row["status"] != "done"


def test_routed_runs_report_their_workers_and_no_hosted_tokens(project: Path) -> None:
    out = project / "bench" / "results" / "oracle"
    runner.run_bench(
        load_suite("tiny", project),
        runner.parse_contestants("fake:oracle"),
        root=project,
        out_dir=out,
    )
    (row,) = read_rows(out)
    assert row["passed"] and not row["infra_error"]
    (worker,) = row["models"]
    counts = row["models"][worker]
    assert worker == "fake/oracle" and counts["hosted"] is False and counts["calls"] >= 1
    assert counts["input"] == row["tokens"]["input"]
    assert row["hosted_tokens"] == 0


def test_a_harness_contestant_runs_the_task_isolated_in_its_harness(project: Path) -> None:
    seen: list[TaskSpec] = []

    def fake_run_task(spec: TaskSpec, **_kwargs: Any) -> TaskResult:
        seen.append(spec)
        return TaskResult(
            task_id="t-1",
            status="done",
            usage_by_worker={
                "local/model": {
                    "calls": 2,
                    "input": 300,
                    "cached_input": 100,
                    "output": 30,
                    "local": True,
                },
                "remote/model": {
                    "calls": 1,
                    "input": 50,
                    "cached_input": 0,
                    "output": 5,
                    "local": False,
                },
            },
        )

    out = project / "bench" / "results" / "harness"
    runner.run_bench(
        load_suite("tiny", project),
        runner.parse_contestants("harness:claude-code"),
        root=project,
        out_dir=out,
        options=runner.BenchOptions(router_pool=["local/model"]),
        run_task=fake_run_task,
    )
    (spec,) = seen
    assert spec.harness == "claude-code" and spec.isolated and spec.workers == ["local/model"]
    (row,) = read_rows(out)
    assert row["models"]["local/model"]["hosted"] is False
    assert row["models"]["remote/model"]["hosted"] is True
    assert row["hosted_tokens"] == 55
