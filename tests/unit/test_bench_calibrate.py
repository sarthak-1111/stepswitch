"""`ssw bench calibrate`: base rates from single-model contestants and the handoff recovery from
the briefs of relay runs, judged by the bench's own verdicts (SPEC §7.2, ADR 0024)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stepswitch.bench.calibrate import calibrate, model_of
from stepswitch.bench.report import MANIFEST_FILE, RESULTS_FILE
from stepswitch.bench.runner import HANDOFFS_FILE
from stepswitch.router.calibration import HandoffRecovery


def write_run(
    root: Path,
    name: str,
    contestants: list[str],
    rows: list[dict[str, Any]],
    handoffs: list[dict[str, Any]] = (),  # type: ignore[assignment]
    **manifest: Any,
) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    data = {"suite": "mini", "contestants": contestants, **manifest}
    (folder / MANIFEST_FILE).write_text(json.dumps(data), encoding="utf-8")
    (folder / RESULTS_FILE).write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    if handoffs:
        (folder / HANDOFFS_FILE).write_text(
            "".join(json.dumps(h) + "\n" for h in handoffs), encoding="utf-8"
        )
    return folder


def row(contestant: str, task: str, seed: int, passed: bool, **extra: Any) -> dict[str, Any]:
    return {
        "contestant": contestant,
        "task": task,
        "seed": seed,
        "passed": passed,
        "status": "done",
        "track": "general",
        **extra,
    }


def test_single_model_rows_become_base_rates(tmp_path: Path) -> None:
    single = write_run(
        tmp_path,
        "single",
        ["local/big", "router", "fake:oracle"],
        [
            row("local/big", "a", 0, True),
            row("local/big", "b", 0, False),
            row("local/big", "c", 0, True, status="error"),
            row("router", "a", 0, True),
            row("fake:oracle", "a", 0, True),
        ],
    )
    merged = write_run(
        tmp_path, "merged", ["local/big"], [row("local/big", "a", 0, True)], sources=["single"]
    )
    found = calibrate([single, merged])
    assert found.priors.counts == {"big|general": [1, 2]}
    assert found.runs == ["single"] and found.skipped == ["merged"] and found.rows == 2
    assert found.priors.rate("big", "general", 0.65) == (1 + 4 * 0.65) / (2 + 4)
    assert model_of("managed/qwen3-4b") == "qwen3-4b" and model_of("solo") == "solo"


def test_relay_briefs_update_the_handoff_recovery(tmp_path: Path) -> None:
    handoffs = [
        {
            "task": "a",
            "seed": 0,
            "contestant": "relay",
            "reason": "relay",
            "p_current": 0.4,
            "p_new": 0.8,
        },
        {
            "task": "b",
            "seed": 0,
            "contestant": "relay",
            "reason": "relay",
            "p_current": 0.4,
            "p_new": 0.8,
        },
        {
            "task": "a",
            "seed": 0,
            "contestant": "relay",
            "reason": "compaction",
            "p_current": 0.4,
            "p_new": 0.8,
        },
        {
            "task": "c",
            "seed": 0,
            "contestant": "relay",
            "reason": "relay",
            "p_current": 0.8,
            "p_new": 0.4,
        },
    ]
    rows = [row("relay", "a", 0, True), row("relay", "b", 0, False), row("relay", "c", 0, True)]
    run = write_run(tmp_path, "relay", ["relay"], rows, handoffs, optimize=True)
    found = calibrate([run])
    assert found.handoffs == 2 and found.recovery.observations == 2
    expected = HandoffRecovery()
    expected.update(0.4, 0.8, True)
    expected.update(0.4, 0.8, False)
    assert found.recovery.mean == expected.mean
    summary = found.summary()
    assert summary["handoff_recovery"]["observations"] == 2 and summary["runs"] == ["relay"]
