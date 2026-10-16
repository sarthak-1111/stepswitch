"""Bench reports: leaderboard.json, leaderboard.md and the chart, from results and manifest."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from stepswitch.bench.report import (
    CHART_SVG,
    LEADERBOARD_JSON,
    LEADERBOARD_MD,
    leaderboard,
    read_results,
    render_markdown,
    write_report,
)

MANIFEST = {
    "suite": "mini-general",
    "command": "ssw bench run --suite mini-general --workers router,tiny --seeds 2",
    "started": "2026-10-16T10:00:00Z",
    "finished": "2026-10-16T10:30:00Z",
    "hardware": {"os": "Linux", "cpu_cores": 8, "ram_gb": 16, "gpu": "none"},
    "version": "0.1.0",
    "commit": "abc1234",
    "seeds": [0, 1],
    "prices": {"input_per_million": 2.0, "output_per_million": 8.0},
}


def result(contestant: str, task: str, seed: int, passed: bool) -> dict[str, Any]:
    return {
        "contestant": contestant,
        "task": task,
        "seed": seed,
        "passed": passed,
        "status": "done" if passed else "failed",
        "wall_seconds": 20.0 + seed,
        "tokens": {"input": 2000, "output": 200, "cached_input": 500},
        "gpu_seconds": 1.5,
        "escalations": 0,
    }


def write_run(run_dir: Path) -> None:
    rows = [
        result("router", task, seed, passed=not (task == "c" and seed == 1))
        for task in ("a", "b", "c")
        for seed in (0, 1)
    ] + [
        result("tiny", task, seed, passed=task == "a")
        for task in ("a", "b", "c")
        for seed in (0, 1)
    ]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "results.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    (run_dir / "run.json").write_text(json.dumps(MANIFEST), encoding="utf-8")


def test_report_files_carry_provenance_and_every_metric(tmp_path: Path) -> None:
    write_run(tmp_path)
    paths = write_report(tmp_path, resamples=2_000)
    board = json.loads((tmp_path / LEADERBOARD_JSON).read_text(encoding="utf-8"))
    assert set(paths) == {"json", "markdown", "chart"}
    assert board["results_file"] == "results.jsonl"
    assert board["command"] == MANIFEST["command"]
    assert board["hardware"] == MANIFEST["hardware"]
    assert board["date"] == MANIFEST["finished"]
    assert (board["runs"], board["tasks"]) == (12, 3)
    first, second = board["contestants"]
    assert (first["rank"], first["contestant"], second["contestant"]) == (1, "router", "tiny")
    assert first["pass_rate"] == pytest.approx(5 / 6)
    assert first["ci"]["low"] <= first["pass_rate"] <= first["ci"]["high"]
    assert first["reference_cost"] == pytest.approx((12_000 * 2 + 1_200 * 8) / 1e6)
    text = (tmp_path / LEADERBOARD_MD).read_text(encoding="utf-8")
    assert "[`results.jsonl`](results.jsonl)" in text
    assert MANIFEST["command"] in text and "2026-10-16T10:30:00Z" in text
    assert "| 1 | router | 83% |" in text
    assert f"![Pass rate by contestant]({CHART_SVG})" in text
    svg = ET.fromstring((tmp_path / CHART_SVG).read_text(encoding="utf-8"))
    assert svg.tag.endswith("svg")
    assert sum(1 for e in svg.iter() if e.tag.endswith("rect")) == 3  # background + two bars


def test_report_is_reproducible(tmp_path: Path) -> None:
    write_run(tmp_path)
    write_report(tmp_path, resamples=1_000)
    first = (tmp_path / LEADERBOARD_JSON).read_bytes()
    write_report(tmp_path, resamples=1_000)
    assert (tmp_path / LEADERBOARD_JSON).read_bytes() == first


def test_results_must_name_contestant_and_task(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text('{"contestant": "x", "task": "a"}\n\n{"task": "b"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 3"):
        read_results(path)
    path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        read_results(path)


def test_markdown_without_manifest_says_what_is_missing() -> None:
    board = leaderboard([result("solo", "a", 0, True)], resamples=10)
    text = render_markdown(board)
    assert "Command: `not recorded`" in text and "Hardware: not recorded" in text
    assert "Reference cost" not in text.split("|")[0]
    assert board["contestants"][0]["reference_cost"] is None
