"""The ledger: per-step accounting of tokens, wall time and GPU-seconds (SPEC §2, TK12).

One row per model call, taken from server-reported usage. When a server reports no usage the
row holds an estimate (characters / 4) and says so in `usage_source`, so totals that include
estimates are never presented as measured (claim C-019 compares ledger totals with the usage
servers reported).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from stepswitch import paths
from stepswitch.compat.ir import Response, Usage
from stepswitch.state import StateDB, open_project_state

CHARS_PER_TOKEN = 4.0
COLUMNS = (
    "task_id",
    "step",
    "ts",
    "step_type",
    "endpoint",
    "model",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "wall_seconds",
    "gpu_seconds",
    "usage_source",
    "data",
)


@dataclass
class LedgerRow:
    task_id: str
    step: int
    step_type: str
    endpoint: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    wall_seconds: float = 0.0
    gpu_seconds: float = 0.0
    # "server": reported by the model server; "estimate": characters / 4; other values name
    # the source a worker declared (scripted workers say "scripted").
    usage_source: str = "server"
    ts: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def estimate_tokens(chars: int) -> int:
    """A flagged estimate from a character count (about four characters per token)."""
    return int(-(-max(0, chars) // CHARS_PER_TOKEN))


def usage_row_fields(
    usage: Usage | None, *, request_chars: int = 0, response_chars: int = 0
) -> dict[str, Any]:
    """Token columns from a response's usage, or an estimate when the server reported none."""
    if usage is not None and (usage.input_tokens is not None or usage.output_tokens is not None):
        raw = usage.raw or {}
        source = raw.get("source") if isinstance(raw.get("source"), str) else "server"
        return {
            "input_tokens": int(usage.input_tokens or 0),
            "output_tokens": int(usage.output_tokens or 0),
            "cached_input_tokens": int(usage.cached_input_tokens or 0),
            "reasoning_tokens": int(usage.reasoning_tokens or 0),
            "usage_source": source,
        }
    return {
        "input_tokens": estimate_tokens(request_chars),
        "output_tokens": estimate_tokens(response_chars),
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "usage_source": "estimate",
    }


def gpu_seconds_of(response: Response, wall_seconds: float, *, gpu: bool) -> float:
    """GPU-seconds of one call: what the worker reported, else the wall time on a GPU worker."""
    raw = response.usage.raw if response.usage is not None and response.usage.raw else {}
    value = raw.get("gpu_seconds")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return wall_seconds if gpu else 0.0


class Ledger:
    """Writes rows to the project state database and keeps running totals for one task."""

    def __init__(
        self, db: StateDB | None, task_id: str, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.db = db
        self.task_id = task_id
        self.clock = clock
        self.rows: list[LedgerRow] = []

    def record(self, row: LedgerRow) -> LedgerRow:
        self.rows.append(row)
        if self.db is not None:
            values = asdict(row)
            values["data"] = json.dumps(row.data, sort_keys=True, default=str)
            placeholders = ", ".join("?" for _ in COLUMNS)
            self.db.execute(
                f"INSERT INTO ledger ({', '.join(COLUMNS)}) VALUES ({placeholders})",
                tuple(values[c] for c in COLUMNS),
            )
        return row

    def totals(self) -> dict[str, Any]:
        return summarize(asdict(row) for row in self.rows)


def summarize(rows: Any) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "wall_seconds": 0.0,
        "gpu_seconds": 0.0,
        "estimated_calls": 0,
    }
    for row in rows:
        totals["calls"] += 1
        for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"):
            totals[key] += int(row[key] or 0)
        totals["wall_seconds"] += float(row["wall_seconds"] or 0.0)
        totals["gpu_seconds"] += float(row["gpu_seconds"] or 0.0)
        if row["usage_source"] == "estimate":
            totals["estimated_calls"] += 1
    totals["tokens"] = totals["input_tokens"] + totals["output_tokens"]
    totals["wall_seconds"] = round(totals["wall_seconds"], 3)
    totals["gpu_seconds"] = round(totals["gpu_seconds"], 3)
    return totals


def by_worker(rows: Iterable[LedgerRow]) -> dict[tuple[str, str], dict[str, int]]:
    """Calls and tokens per (endpoint, model): input (cached included), cached_input and
    output."""
    totals: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        counts = totals.setdefault(
            (row.endpoint, row.model), {"calls": 0, "input": 0, "cached_input": 0, "output": 0}
        )
        counts["calls"] += 1
        counts["input"] += int(row.input_tokens or 0)
        counts["cached_input"] += int(row.cached_input_tokens or 0)
        counts["output"] += int(row.output_tokens or 0)
    return totals


def task_rows(root: Path, task_id: str) -> list[dict[str, Any]]:
    """Ledger rows of one task, in step order, with `data` decoded."""
    if not paths.state_db_path(root).is_file():
        return []
    db = open_project_state(root)
    try:
        rows = db.query("SELECT * FROM ledger WHERE task_id = ? ORDER BY step, id", (task_id,))
    finally:
        db.close()
    for row in rows:
        row["data"] = json.loads(row["data"]) if row.get("data") else {}
    return rows


def task_totals(root: Path, task_id: str) -> dict[str, Any]:
    return summarize(task_rows(root, task_id))
