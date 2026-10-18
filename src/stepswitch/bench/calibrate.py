"""`ssw bench calibrate`: router parameters from recorded bench results (SPEC §7.2, ADR 0024).

* Base success rates per model and track, from the rows of single-model contestants (every
  step pinned to one worker): `stepswitch.router.calibration.PriorTable`. Optimized runs use them
  in place of the tier's base rate, shrunk towards it.
* The handoff recovery r (SPEC §7.2: the share of the success gap a worker continuing from a
  HANDOFF brief recovers), from the briefs of optimized runs (`handoffs.jsonl`) and the bench's
  own verdict on each task: `stepswitch.router.calibration.HandoffRecovery`. A relay from a
  weaker to a stronger worker (`ssw bench run --relay`) is what informs it; compaction briefs
  and handoffs to a worker that is not better say nothing about r.

Rows of the router and relay contestants, fakes and crashed runs are skipped, and so are merged
result directories (`ssw bench report --out`), whose source runs are counted instead. The
result replaces what the state database held, so calibrating twice over the same runs gives the
same values.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stepswitch.bench.fakes import PREFIX as FAKE_PREFIX
from stepswitch.bench.report import MANIFEST_FILE, RESULTS_FILE
from stepswitch.bench.runner import HANDOFFS_FILE, RELAY, ROUTER
from stepswitch.router.calibration import HandoffRecovery, PriorTable


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def model_of(contestant: str) -> str:
    """The model a single-worker contestant pins: the part after `<endpoint>/`."""
    return contestant.split("/", 1)[1] if "/" in contestant else contestant


@dataclass
class Calibration:
    priors: PriorTable = field(default_factory=PriorTable)
    recovery: HandoffRecovery = field(default_factory=HandoffRecovery)
    runs: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    rows: int = 0
    handoffs: int = 0

    def summary(self) -> dict[str, Any]:
        rates = {}
        for key, (passes, runs) in sorted(self.priors.counts.items()):
            rates[key] = {"passes": passes, "runs": runs, "rate": round(passes / runs, 4)}
        return {
            "runs": self.runs,
            "skipped": self.skipped,
            "rows": self.rows,
            "priors": rates,
            "handoffs": self.handoffs,
            "handoff_recovery": {
                "observations": self.recovery.observations,
                "mean": self.recovery.mean,
            },
        }


def calibrate(
    run_dirs: Sequence[Path], *, model_for: Callable[[str], str] = model_of
) -> Calibration:
    """Priors and handoff recovery over the runs in `run_dirs` (bench result directories)."""
    result = Calibration()
    for directory in run_dirs:
        manifest_path = directory / MANIFEST_FILE
        if not manifest_path.is_file():
            raise ValueError(f"{directory} has no {MANIFEST_FILE}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("sources"):
            result.skipped.append(directory.name)
            continue
        rows = _jsonl(directory / RESULTS_FILE)
        single = {
            name: model_for(name)
            for name in manifest.get("contestants", [])
            if name not in {ROUTER, RELAY} and not str(name).startswith(FAKE_PREFIX)
        }
        verdicts: dict[tuple[str, int, str], bool] = {}
        for row in rows:
            key = (str(row.get("task")), int(row.get("seed") or 0), str(row.get("contestant")))
            verdicts[key] = bool(row.get("passed"))
            model = single.get(str(row.get("contestant")))
            if model is None or row.get("status") == "error":
                continue
            result.priors.add(model, str(row.get("track") or "general"), bool(row.get("passed")))
            result.rows += 1
        for record in _jsonl(directory / HANDOFFS_FILE):
            if record.get("reason") == "compaction":
                continue
            p_current, p_new = record.get("p_current"), record.get("p_new")
            key = (
                str(record.get("task")),
                int(record.get("seed") or 0),
                str(record.get("contestant")),
            )
            if p_current is None or p_new is None or key not in verdicts:
                continue
            before = result.recovery.observations
            result.recovery.update(float(p_current), float(p_new), verdicts[key])
            result.handoffs += result.recovery.observations - before
        result.runs.append(directory.name)
    result.priors.sources = list(result.runs)
    return result
