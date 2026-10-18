"""Reading committed bench runs for the bench-kind claims (C-010, C-011).

A run directory under bench/results holds `run.json` (what was run), `results.jsonl` (one row
per task and seed) and, from Phase 5 on, `handoffs.jsonl` (one record per HANDOFF brief the run
produced). `run.json` says whether the run used the token optimizations (`optimize`); runs from
before Phase 5 have no such key and are not used here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "bench" / "results"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@dataclass
class Run:
    path: Path
    meta: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)
    handoffs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def suite(self) -> str:
        return str(self.meta.get("suite"))

    @property
    def optimize(self) -> bool | None:
        value = self.meta.get("optimize")
        return None if value is None else bool(value)

    def setup(self) -> tuple[Any, ...]:
        """What must match for two runs to be compared: suite, contestants, pool, tasks, seeds."""
        return (
            self.suite,
            tuple(self.meta.get("contestants", [])),
            tuple(sorted(self.meta.get("router_pool") or [])),
            tuple(self.meta.get("tasks", [])),
            tuple(self.meta.get("seeds", [])),
        )


def runs() -> list[Run]:
    found = []
    for meta_path in sorted(RESULTS.glob("*/run.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        folder = meta_path.parent
        found.append(
            Run(
                folder,
                meta,
                read_jsonl(folder / "results.jsonl"),
                read_jsonl(folder / "handoffs.jsonl"),
            )
        )
    return found


def tokens(row: dict[str, Any]) -> int:
    """Input plus output tokens of one task run (cached input tokens are part of input)."""
    counts = row.get("tokens") or {}
    return int(counts.get("input") or 0) + int(counts.get("output") or 0)
