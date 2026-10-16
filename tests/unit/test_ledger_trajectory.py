"""Ledger accounting and trajectory recording (SPEC §2, §11, TK12)."""

from __future__ import annotations

import json
from pathlib import Path

from stepswitch.compat.ir import Message, Response, Usage
from stepswitch.ledger import (
    Ledger,
    LedgerRow,
    estimate_tokens,
    gpu_seconds_of,
    task_rows,
    task_totals,
    usage_row_fields,
)
from stepswitch.state import open_project_state
from stepswitch.trajectory.record import Trajectory, get_blob, read_events


def test_usage_fields_prefer_server_usage_and_flag_estimates() -> None:
    server = usage_row_fields(
        Usage(input_tokens=120, output_tokens=30, cached_input_tokens=100, reasoning_tokens=10)
    )
    assert server == {
        "input_tokens": 120,
        "output_tokens": 30,
        "cached_input_tokens": 100,
        "reasoning_tokens": 10,
        "usage_source": "server",
    }
    scripted = usage_row_fields(Usage(input_tokens=1, output_tokens=1, raw={"source": "scripted"}))
    assert scripted["usage_source"] == "scripted"
    guessed = usage_row_fields(None, request_chars=401, response_chars=8)
    assert guessed == {
        "input_tokens": 101,
        "output_tokens": 2,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "usage_source": "estimate",
    }
    assert estimate_tokens(0) == 0 and estimate_tokens(4) == 1 and estimate_tokens(5) == 2


def test_gpu_seconds() -> None:
    reported = Response(message=Message(role="assistant"), usage=Usage(raw={"gpu_seconds": 2.5}))
    assert gpu_seconds_of(reported, 9.0, gpu=False) == 2.5
    plain = Response(message=Message(role="assistant"))
    assert gpu_seconds_of(plain, 9.0, gpu=True) == 9.0
    assert gpu_seconds_of(plain, 9.0, gpu=False) == 0.0


def test_ledger_rows_and_totals(tmp_path: Path) -> None:
    db = open_project_state(tmp_path)
    ledger = Ledger(db, "t1")
    for step, source in ((1, "server"), (2, "estimate")):
        ledger.record(
            LedgerRow(
                task_id="t1",
                step=step,
                step_type="edit",
                endpoint="e",
                model="m",
                input_tokens=100,
                output_tokens=10,
                wall_seconds=1.5,
                gpu_seconds=0.5,
                usage_source=source,
                data={"worker": "e/m"},
            )
        )
    db.close()
    rows = task_rows(tmp_path, "t1")
    assert [r["step"] for r in rows] == [1, 2]
    assert rows[0]["data"] == {"worker": "e/m"}
    totals = task_totals(tmp_path, "t1")
    assert totals["tokens"] == 220 and totals["calls"] == 2 and totals["estimated_calls"] == 1
    assert totals["wall_seconds"] == 3.0 and totals["gpu_seconds"] == 1.0
    assert ledger.totals() == totals
    assert task_rows(tmp_path / "nowhere", "t1") == []


def test_trajectory_writes_jsonl_db_rows_and_blobs(tmp_path: Path) -> None:
    db = open_project_state(tmp_path)
    trajectory = Trajectory(tmp_path, "t2", db=db, clock=lambda: 12.0)
    seen: list[str] = []
    trajectory.listeners.append(lambda event: seen.append(event["type"]))
    trajectory.emit("task_start", task_id="t2")
    trajectory.emit(
        "switch",
        step=3,
        **{"from": "a", "to": "b"},
        reason="cheaper",
        cost={"seconds": 1.0, "tokens": 10},
    )
    digest = trajectory.blob("big output " * 100)
    assert get_blob(tmp_path, digest).decode() == "big output " * 100
    events = read_events(trajectory.run_dir)
    assert [e["type"] for e in events] == ["task_start", "switch"]
    assert events[1] == {
        "seq": 2,
        "ts": 12.0,
        "type": "switch",
        "step": 3,
        "from": "a",
        "to": "b",
        "reason": "cheaper",
        "cost": {"seconds": 1.0, "tokens": 10},
    }
    rows = db.query("SELECT type, step, data FROM events WHERE task_id = ? ORDER BY seq", ("t2",))
    assert [(r["type"], r["step"]) for r in rows] == [("task_start", None), ("switch", 3)]
    assert json.loads(rows[1]["data"])["reason"] == "cheaper"
    assert seen == ["task_start", "switch"]
    path = trajectory.write_file("result.json", "{}\n")
    assert path.read_bytes() == b"{}\n"
    assert read_events(tmp_path / "missing") == []
    db.close()
