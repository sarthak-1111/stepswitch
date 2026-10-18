"""Router parameters learned from outcomes (SPEC §7.2, §10; ADR 0005, ADR 0024).

* `HandoffRecovery`: the share r of the success gap that a worker continuing from a HANDOFF
  brief recovers (`cost.handoff_success`). It is a posterior over a grid of r values: the prior
  is centred on `DEFAULT_HANDOFF_RECOVERY`, since published results recover less than half of
  the gap, and each task that handed over updates it with the task's outcome. With no data the
  router uses the prior mean.
* `PriorTable`: success rates per model and track from bench runs (`ssw bench calibrate`), which
  replace the tier-based base rate in `policy.prior` for models the bench has measured. Rates
  are shrunk towards the tier rate by a few pseudo-runs, so a handful of runs never dominates.

Both live in the project state database (`router_params`) and never leave the machine.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from stepswitch.router.cost import DEFAULT_HANDOFF_RECOVERY

GRID = tuple(i / 20 for i in range(21))
# Prior weight: a Beta(a, b) density with mean DEFAULT_HANDOFF_RECOVERY and a + b = PRIOR_STRENGTH.
PRIOR_STRENGTH = 4.0
# Pseudo-runs at the tier rate that a measured rate is shrunk towards.
PRIOR_PSEUDO_RUNS = 4.0
EPSILON = 1e-6
HANDOFF_KEY = "handoff_recovery"
PRIORS_KEY = "priors"


def _beta_log_prior(mean: float, strength: float) -> list[float]:
    a, b = mean * strength, (1 - mean) * strength
    weights = []
    for r in GRID:
        x = min(1 - EPSILON, max(EPSILON, r))
        weights.append((a - 1) * math.log(x) + (b - 1) * math.log(1 - x))
    return weights


@dataclass
class HandoffRecovery:
    """Posterior over the recovery share r, as log weights on GRID."""

    log_weights: list[float] = field(
        default_factory=lambda: _beta_log_prior(DEFAULT_HANDOFF_RECOVERY, PRIOR_STRENGTH)
    )
    observations: int = 0

    def update(self, p_current: float, p_new: float, success: bool) -> None:
        """One handoff from a worker with prior `p_current` to one with `p_new`, and whether the
        task then succeeded. Handoffs to a worker that is not better say nothing about r."""
        if p_new <= p_current:
            return
        for i, r in enumerate(GRID):
            p = min(1 - EPSILON, max(EPSILON, p_current + r * (p_new - p_current)))
            self.log_weights[i] += math.log(p if success else 1 - p)
        self.observations += 1

    def _weights(self) -> list[float]:
        top = max(self.log_weights)
        raw = [math.exp(w - top) for w in self.log_weights]
        total = sum(raw)
        return [w / total for w in raw]

    @property
    def mean(self) -> float:
        return round(sum(w * r for w, r in zip(self._weights(), GRID, strict=True)), 4)

    def as_dict(self) -> dict[str, Any]:
        return {
            "log_weights": [round(w, 6) for w in self.log_weights],
            "observations": self.observations,
            "mean": self.mean,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> HandoffRecovery:
        if not data or len(data.get("log_weights", [])) != len(GRID):
            return cls()
        return cls([float(w) for w in data["log_weights"]], int(data.get("observations", 0)))


@dataclass
class PriorTable:
    """Measured success counts per (model, track): {"<model>|<track>": [passes, runs]}."""

    counts: dict[str, list[int]] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    @staticmethod
    def key(model: str, track: str) -> str:
        return f"{model.strip().lower()}|{track}"

    def add(self, model: str, track: str, passed: bool) -> None:
        entry = self.counts.setdefault(self.key(model, track), [0, 0])
        entry[0] += int(bool(passed))
        entry[1] += 1

    def rate(self, model: str, track: str, tier_rate: float) -> float | None:
        """The measured rate shrunk towards `tier_rate`, or None without measurements."""
        entry = self.counts.get(self.key(model, track))
        if not entry or entry[1] <= 0:
            return None
        passes, runs = entry
        return (passes + PRIOR_PSEUDO_RUNS * tier_rate) / (runs + PRIOR_PSEUDO_RUNS)

    def as_dict(self) -> dict[str, Any]:
        return {"counts": self.counts, "sources": self.sources}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> PriorTable:
        if not data:
            return cls()
        counts = {str(k): [int(v[0]), int(v[1])] for k, v in dict(data.get("counts", {})).items()}
        return cls(counts, [str(s) for s in data.get("sources", [])])


def priors_from_results(
    rows: Iterable[Mapping[str, Any]], *, models: Mapping[str, str]
) -> PriorTable:
    """A table from bench result rows of single-model contestants. `models` maps a contestant
    name to the model it pinned (rows of other contestants, such as the router, are skipped)."""
    table = PriorTable()
    for row in rows:
        model = models.get(str(row.get("contestant")))
        if model is None or row.get("status") == "error":
            continue
        table.add(model, str(row.get("track") or "general"), bool(row.get("passed")))
    return table


# --------------------------------------------------------------------------------------------
# Storage in the state database (migration 4: router_params)
# --------------------------------------------------------------------------------------------


def load_param(db: Any, key: str) -> dict[str, Any] | None:
    if db is None:
        return None
    rows = db.query("SELECT value FROM router_params WHERE key = ?", (key,))
    if not rows:
        return None
    value: dict[str, Any] = json.loads(rows[0]["value"])
    return value


def save_param(db: Any, key: str, value: Mapping[str, Any]) -> None:
    db.execute(
        "INSERT INTO router_params (key, value, updated) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
        (key, json.dumps(dict(value), sort_keys=True), time.time()),
    )


def load_recovery(db: Any) -> HandoffRecovery:
    return HandoffRecovery.from_dict(load_param(db, HANDOFF_KEY))


def load_priors(db: Any) -> PriorTable:
    return PriorTable.from_dict(load_param(db, PRIORS_KEY))


def record_handoff_outcomes(
    db: Any, handoffs: Iterable[tuple[float, float]], success: bool
) -> HandoffRecovery:
    """Update the stored posterior with one finished task's handoffs ((p_current, p_new) each)."""
    recovery = load_recovery(db)
    changed = False
    for p_current, p_new in handoffs:
        before = recovery.observations
        recovery.update(p_current, p_new, success)
        changed = changed or recovery.observations != before
    if changed and db is not None:
        save_param(db, HANDOFF_KEY, recovery.as_dict())
    return recovery
