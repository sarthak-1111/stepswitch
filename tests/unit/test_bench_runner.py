"""The bench runner: contestants, the independent acceptance check, results and the report.

Runs use the deterministic fake contestants, which drive the real orchestrator, agent loop,
toolbox and gates through scripted turns: `fake:oracle` writes the golden solution, `fake:noop`
changes nothing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stepswitch.bench import fakes, runner, suites
from stepswitch.bench.report import MANIFEST_FILE, RESULTS_FILE
from stepswitch.bench.suites import load_suite
from tests.unit.bench_project import CALC, CHEAT, GOLDEN, make_project


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path / "project")


def read_rows(out: Path) -> list[dict[str, Any]]:
    lines = (out / RESULTS_FILE).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_contestants_parse_router_workers_and_fakes() -> None:
    parsed = runner.parse_contestants("router, managed/small ,fake:oracle,router")
    assert [c.name for c in parsed] == ["router", "managed/small", "fake:oracle"]
    assert parsed[0].worker is None and parsed[0].fake is None
    assert parsed[1].worker == "managed/small"
    assert parsed[2].fake == "oracle" and parsed[2].worker == fakes.worker_id("oracle")
    assert [c.name for c in runner.parse_contestants(None)] == ["router"]
    with pytest.raises(ValueError, match="unknown fake contestant"):
        runner.parse_contestants("fake:psychic")


def test_a_leading_python_means_the_project_interpreter(project: Path) -> None:
    task = load_suite("tiny", project).tasks[0]
    assert runner.acceptance_commands(task) == ["{python} -m pytest -q"]


def test_acceptance_check_applies_the_diff_to_a_fresh_copy(project: Path, tmp_path: Path) -> None:
    task = load_suite("tiny", project).tasks[0]
    check = runner.check_acceptance(
        task, GOLDEN, tmp_path / "golden", protected=("tests/",), timeout=120
    )
    assert check.passed and not check.tests_modified and check.apply_error is None
    assert [row["exit_code"] for row in check.commands] == [0]
    unchanged = runner.check_acceptance(
        task, "", tmp_path / "empty", protected=("tests/",), timeout=120
    )
    assert not unchanged.passed
    assert (project / "bench" / "repos" / "adder" / "calc.py").read_text(encoding="utf-8") == CALC


def test_changed_tests_are_restored_and_flagged(project: Path, tmp_path: Path) -> None:
    task = load_suite("tiny", project).tasks[0]
    # The diff fixes the code but also changes the test; the original test is what counts.
    check = runner.check_acceptance(
        task, GOLDEN + CHEAT, tmp_path / "both", protected=("tests/",), timeout=120
    )
    assert check.tests_modified
    assert check.passed  # the restored original test passes against the fixed code
    cheat = runner.check_acceptance(
        task, CHEAT, tmp_path / "cheat", protected=("tests/",), timeout=120
    )
    assert cheat.tests_modified and not cheat.passed


def test_a_patch_that_does_not_apply_fails_the_check(project: Path, tmp_path: Path) -> None:
    task = load_suite("tiny", project).tasks[0]
    broken = GOLDEN.replace("return a - b", "return a * b")
    check = runner.check_acceptance(
        task, broken, tmp_path / "broken", protected=("tests/",), timeout=120
    )
    assert not check.passed and check.apply_error


def test_bench_runs_fakes_and_writes_results_manifest_and_report(project: Path) -> None:
    suite = load_suite("tiny", project)
    contestants = runner.parse_contestants("fake:oracle,fake:noop")
    out = project / "bench" / "results" / "run-1"
    messages: list[str] = []
    runner.run_bench(
        suite,
        contestants,
        root=project,
        out_dir=out,
        options=runner.BenchOptions(seeds=2),
        command="ssw bench run --suite tiny --workers fake:oracle,fake:noop --seeds 2",
        echo=messages.append,
    )
    rows = read_rows(out)
    assert [(r["contestant"], r["seed"]) for r in rows] == [
        ("fake:oracle", 0),
        ("fake:oracle", 1),
        ("fake:noop", 0),
        ("fake:noop", 1),
    ]
    oracle, noop = rows[:2], rows[2:]
    assert all(r["passed"] and r["status"] == "done" for r in oracle), oracle
    assert all(r["changed_files"] == ["calc.py"] for r in oracle)
    assert not any(r["passed"] for r in noop)
    assert all(r["changed_files"] == [] for r in noop)
    for row in rows:
        turns = row["tokens"]["input"] // fakes.TURN_USAGE.input_tokens
        assert turns >= 1
        assert row["tokens"]["input"] == turns * fakes.TURN_USAGE.input_tokens
        assert row["tokens"]["output"] == turns * fakes.TURN_USAGE.output_tokens
        assert row["suite"] == "tiny" and row["task"] == "adder-fix"
        assert not row["tests_modified"] and row["task_id"]
    manifest = json.loads((out / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["suite"] == "tiny" and manifest["seeds"] == [0, 1]
    assert manifest["contestants"] == ["fake:oracle", "fake:noop"]
    assert manifest["tasks"] == ["adder-fix"] and manifest["finished"]
    assert manifest["command"].startswith("ssw bench run")
    board = json.loads((out / "leaderboard.json").read_text(encoding="utf-8"))
    rates = {entry["contestant"]: entry["pass_rate"] for entry in board["contestants"]}
    assert rates == {"fake:oracle": 1.0, "fake:noop": 0.0}
    assert (out / "leaderboard.md").is_file() and (out / "pass-rate.svg").is_file()
    assert messages[0].startswith("[1/4] fake:oracle adder-fix seed 0")
    assert not (project / ".stepswitch" / "bench" / "run-1").exists()  # workspaces removed


def test_a_crashing_run_is_a_failed_row(project: Path) -> None:
    suite = load_suite("tiny", project)

    def crash(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("engine went away")

    out = project / "bench" / "results" / "crash"
    runner.run_bench(
        suite, runner.parse_contestants("router"), root=project, out_dir=out, run_task=crash
    )
    (row,) = read_rows(out)
    assert row["status"] == "error" and not row["passed"]
    assert row["error"] == "RuntimeError: engine went away"


def test_hardware_summary_has_counts_and_sizes_only() -> None:
    detected = {
        "cpu": {"model": "Acme Turbo 9000", "arch": "x86_64", "cores_logical": 16},
        "ram_gb": 31.7,
        "gpus": [{"index": 0, "name": "Brandname Accelerator", "vram_gb": 24.0}],
    }
    summary = runner.hardware_summary(SimpleNamespace(host={"detected": detected}))
    assert summary["cpu_cores"] == 16 and summary["ram_gb"] == 31.7
    assert summary["gpus"] == 1 and summary["gpu_memory_gb"] == [24.0]
    text = json.dumps(summary)
    assert "Acme" not in text and "Brandname" not in text
    assert set(summary) <= {"os", "arch", "cpu_cores", "ram_gb", "gpus", "gpu_memory_gb"}


def test_packaged_suites_resolve_their_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installed, the built-in suites live in `stepswitch/_bench/` (pyproject force-include)."""
    source = make_project(tmp_path / "source") / "bench"
    package = tmp_path / "site" / "stepswitch"
    for part in ("suites", "repos", "golden"):
        shutil.copytree(source / part, package / "_bench" / part)
    monkeypatch.setattr(suites, "PACKAGE_DIR", package)
    monkeypatch.setattr(suites, "BUNDLED_ROOTS", (package / "_bench",))
    task = suites.load_suite("tiny", tmp_path / "elsewhere").tasks[0]
    assert task.repo == (package / "_bench" / "repos" / "adder").resolve()
    assert task.golden == (package / "_bench" / "golden" / "adder-fix.patch").resolve()
    assert (task.repo / "calc.py").is_file() and task.golden.is_file()


def test_the_router_contestant_routes_within_its_pool(project: Path) -> None:
    suite = load_suite("tiny", project)
    seen: dict[str, list[str]] = {}

    def capture(spec: Any, **_kwargs: Any) -> Any:
        seen[spec.worker or "router"] = list(spec.workers)
        raise RuntimeError("stop here")

    out = project / "bench" / "results" / "pool"
    runner.run_bench(
        suite,
        runner.parse_contestants("router,managed/small"),
        root=project,
        out_dir=out,
        options=runner.BenchOptions(router_pool=["managed/small", "local/large"]),
        run_task=capture,
    )
    assert seen == {"router": ["managed/small", "local/large"], "managed/small": []}
    manifest = json.loads((out / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["router_pool"] == ["managed/small", "local/large"]
