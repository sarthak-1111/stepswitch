"""C-012: best-of-N across model families passes at least as many tasks as the best single worker.

Proof (SPEC §18.5): bench evidence on the mini suites with confidence intervals. Scope: the
mini bench suites, with bootstrap confidence intervals.

The `best-of-n` bench contestant draws its candidates from the run's worker pool (`run.json`
`router_pool`), one per model family, and keeps the best (SPEC §8.3). For each mini suite the
test takes the newest bench run in which `best-of-n` and every worker of that pool ran as
contestants, on the same tasks and seeds. Over the task runs they all share it counts the
passes (acceptance commands on a fresh copy with the run's diff):

* the best single worker is the pool worker with the most passes;
* the claim holds when best-of-N passes at least as many task runs as that worker, in every
  suite.

The evidence carries each contestant's pass rate with a 95% cluster-bootstrap interval (tasks
resampled, the seeds of a task kept together; `stepswitch.bench.metrics`), and the interval of
the paired difference per task run, best-of-N minus the best single worker.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from stepswitch.bench import metrics
from tests.claims import evidence
from tests.claims.bench_runs import Run, runs

pytestmark = [pytest.mark.claim("C-012")]

SUITES = ("mini-general", "mini-science")
BEST_OF_N = "best-of-n"
Key = tuple[str, int]


def best_of_n_run(suite: str) -> Run | None:
    """The newest run of `suite` with best-of-N and every worker of its pool as contestants."""
    found = [r for r in runs() if r.suite == suite and BEST_OF_N in r.meta.get("contestants", [])]
    for run in sorted(found, key=lambda r: r.meta.get("started", ""), reverse=True):
        pool = list(run.meta.get("router_pool") or [])
        if pool and set(pool) <= set(run.meta.get("contestants", [])):
            return run
    return None


def outcomes(run: Run, contestant: str) -> dict[Key, bool]:
    return {
        (str(row["task"]), int(row["seed"])): bool(row.get("passed"))
        for row in run.rows
        if row.get("contestant") == contestant
    }


def groups(results: Mapping[Key, float], shared: Sequence[Key]) -> list[list[float]]:
    """One group of outcomes per task, seeds together (the bootstrap's clusters)."""
    by_task: dict[str, list[float]] = {}
    for key in shared:
        by_task.setdefault(key[0], []).append(results[key])
    return [by_task[task] for task in sorted(by_task)]


def interval(values: list[list[float]], confidence: float) -> dict[str, Any]:
    ci = metrics.bootstrap_ci(values, confidence=confidence)
    return {"low": round(ci.low, 4), "high": round(ci.high, 4), "method": ci.method}


def test_best_of_n_matches_or_beats_best_single_worker() -> None:
    limits = evidence.thresholds("C-012")
    confidence = float(limits["confidence_level"])
    suites: dict[str, Any] = {}
    problems: list[str] = []
    for suite in SUITES:
        run = best_of_n_run(suite)
        assert run is not None, f"no {suite} bench run with best-of-n and its pool as contestants"
        pool = list(run.meta["router_pool"])
        by = {name: outcomes(run, name) for name in (BEST_OF_N, *pool)}
        shared = sorted(set.intersection(*(set(results) for results in by.values())))
        assert shared, f"{run.name}: the contestants share no task runs"
        passes = {name: sum(by[name][key] for key in shared) for name in by}
        best = max(pool, key=lambda name: (passes[name], name))
        contestants = {
            name: {
                "passed": passes[name],
                "runs": len(shared),
                "pass_rate": round(passes[name] / len(shared), 4),
                "interval": interval(
                    groups({k: float(v) for k, v in by[name].items()}, shared), confidence
                ),
            }
            for name in by
        }
        difference = {key: float(by[BEST_OF_N][key]) - float(by[best][key]) for key in shared}
        suites[suite] = {
            "run": run.name,
            "commit": run.meta.get("commit"),
            "best_of": run.meta.get("best_of"),
            "pool": pool,
            "best_single": best,
            "contestants": contestants,
            "difference": {
                "mean": round(sum(difference.values()) / len(shared), 4),
                "interval": interval(groups(difference, shared), confidence),
                "won": sorted(f"{t} s{s}" for (t, s), d in difference.items() if d > 0),
                "lost": sorted(f"{t} s{s}" for (t, s), d in difference.items() if d < 0),
            },
        }
        if passes[BEST_OF_N] < passes[best]:
            problems.append(f"{suite}: best-of-N passed {passes[BEST_OF_N]}, {best} {passes[best]}")
    evidence.write(
        "C-012",
        "comparison",
        {"confidence": confidence, "suites": suites, "problems": problems},
        command="uv run ssw bench run --suite <suite> --workers <pool>,best-of-n "
        "--router-pool <pool> --best-of 2 ... (each run's run.json); "
        "uv run ssw prove --claim C-012",
    )
    assert not problems, problems
