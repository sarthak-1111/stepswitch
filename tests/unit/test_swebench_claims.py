"""The SWE-bench claim helper (`tests.claims.swebench`, ADR 0042) on synthetic bench runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stepswitch.bench.suites import load_suite
from tests.claims import bench_runs, swebench

ROOT = Path(__file__).resolve().parents[2]
SUITE = "swebench-verified-mini"
WORKER = "local/gpt-oss:120b"
MINI = f"{swebench.MINI_PREFIX}{WORKER}"
LIMITS: dict[str, Any] = {
    "suite": SUITE,
    "dataset": "SWE-bench/SWE-bench_Verified",
    "harness": "5.0.2",
    "model": "gpt-oss:120b",
    "preflight": True,
    "hosted_tokens_max": 0,
    "max_excluded_share": 0.1,
    "confidence_level": 0.95,
}
IDS = [task.id for task in load_suite(SUITE, ROOT).tasks]


def meta(**fields: Any) -> dict[str, Any]:
    data = {
        "suite": SUITE,
        "kind": "swebench",
        "contestants": ["router", MINI],
        "router_pool": [WORKER],
        "seeds": [0],
        "tasks": IDS,
        "dataset": {"name": "SWE-bench/SWE-bench_Verified", "revision": "abc"},
        "harness": {"swebench": "5.0.2"},
        "preflight": {"ok": True},
        "command": "ssw bench run --suite swebench-verified-mini",
    }
    return data | fields


def row(contestant: str, task: str, passed: bool, **fields: Any) -> dict[str, Any]:
    tokens = {"input": 100_000, "cached_input": 90_000, "output": 2_000}
    data = {
        "contestant": contestant,
        "task": task,
        "seed": 0,
        "passed": passed,
        "status": "done",
        "infra_error": False,
        "hosted_tokens": 0,
        "tokens": tokens,
        "steps": 20,
        "wall_seconds": 300.0,
        "swebench": {"resolved": passed},
    }
    return data | fields


def write(results: Path, name: str, data: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    folder = results / name
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps(data), encoding="utf-8")
    lines = "".join(json.dumps(r) + "\n" for r in rows)
    (folder / "results.jsonl").write_text(lines, encoding="utf-8")


@pytest.fixture
def results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "results"
    monkeypatch.setattr(bench_runs, "RESULTS", folder)
    gold = [row("gold", task, task != IDS[0]) for task in IDS]  # one reference patch fails
    write(folder, "2026-01-01-swebench-gold", meta(contestants=["gold"]), gold)
    rows = []
    for index, task in enumerate(IDS):
        ours = index % 2 == 0 or index % 5 == 1  # 30 of 50
        theirs = index % 2 == 0 and index % 3 != 0  # 16 of 50
        rows.append(row("router", task, ours, tokens={"input": 50_000, "output": 1_000}))
        rows.append(row(MINI, task, theirs, infra_error=index == 1))
    write(folder, "2026-01-02-swebench-mini-confirmatory", meta(), rows)
    return folder


def test_the_comparison_leaves_out_gold_failures_and_infrastructure_errors(results: Path) -> None:
    comp = swebench.load(LIMITS)
    data = swebench.report(comp)
    assert comp.problems == []
    assert comp.excluded == {"gold": [IDS[0]], "infra": [IDS[1]]}
    assert data["instances"] == 48 and data["runs"]["gold"] == "2026-01-01-swebench-gold"
    ours, theirs = data["results"]["stepswitch"], data["results"]["mini_swe_agent"]
    kept = range(2, 50)
    assert ours["resolved"] == sum(i % 2 == 0 or i % 5 == 1 for i in kept)
    assert theirs["resolved"] == sum(i % 2 == 0 and i % 3 != 0 for i in kept)
    assert ours["per_instance"]["tokens"] == 51_000 and theirs["per_instance"]["tokens"] == 102_000
    assert ours["per_instance"]["uncached_tokens"] == 51_000
    assert theirs["per_instance"]["uncached_tokens"] == 12_000
    assert ours["per_instance"]["minutes"] == 5.0 and ours["resolved_done"] == ours["resolved"]
    paired = data["paired"]
    assert paired["mini_swe_agent_only"] == 0
    assert paired["stepswitch_only"] == ours["resolved"] - theirs["resolved"]
    assert paired["mcnemar_p"] < 0.05
    assert data["tokens"]["tokens"]["percent"] == 50.0
    assert data["tokens"]["uncached"]["percent"] < 0  # more uncached tokens than mini-SWE-agent
    assert data["hosted_tokens"] == 0


def test_only_one_confirmatory_run_counts(results: Path) -> None:
    write(results, "2026-01-03-swebench-mini-confirmatory", meta(), [])
    comp = swebench.load(LIMITS)
    assert "2 confirmatory runs" in comp.problems[0]


def test_the_contestants_must_share_one_model(results: Path) -> None:
    wide = meta(router_pool=[WORKER, "local/other:7b"], dataset={"name": "elsewhere"})
    (results / "2026-01-02-swebench-mini-confirmatory" / "run.json").write_text(
        json.dumps(wide), encoding="utf-8"
    )
    problems = swebench.load(LIMITS).problems
    assert any("not one model" in problem for problem in problems)
    assert any("dataset is not" in problem for problem in problems)


def test_too_many_left_out_instances_fail_the_comparison(results: Path) -> None:
    rows = [row("gold", task, index > 5) for index, task in enumerate(IDS)]
    gold = results / "2026-01-01-swebench-gold" / "results.jsonl"
    gold.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    problems = swebench.load(LIMITS).problems
    assert any("instances left out" in problem for problem in problems)
