"""C-011: uses fewer uncached tokens than the unoptimized baseline at the same pass rate.

Proof (SPEC §18.5): bench evidence comparing the Phase 2 baseline with the optimized
configuration; the public text carries the measured percentage. Scope: the mini bench suites,
optimized configuration vs the Phase 2 baseline.

The baseline is the same code with every token optimization of SPEC §13 switched off
(`ssw bench run --baseline`, which records `"optimize": false` in run.json): the Phase 2
agent's behaviour. For each mini suite the test takes the newest optimized run and the newest
baseline run with the same suite, contestants, router pool, tasks and seeds, and compares:

* the claimed measure (`thresholds.measure`): uncached tokens, the input tokens the server did
  not serve from its prompt cache plus the output tokens, from the result rows (the ledger of
  each run is what the rows add up). ADR 0028 narrowed the claim from all tokens to these;
* pass rate: the share of task runs whose acceptance commands passed.

The claim holds when every suite uses less of the measure and passes at least as often, and,
with the `significant` threshold, when the reduction over both suites together is significant:
its confidence interval (a cluster bootstrap over tasks, each task's runs of both
configurations kept together; `stepswitch.bench.metrics.bootstrap_ratio_ci`) lies above the
minimum reduction. Run-to-run variation in the number of steps is large, so a point estimate
alone would be noise (ADR 0024). The README number is the claimed reduction over both suites
together. The evidence also reports all tokens (input plus output) with their interval.
"""

from __future__ import annotations

from typing import Any

import pytest

from stepswitch.bench import metrics
from tests.claims import evidence
from tests.claims.bench_runs import Run, runs, tokens

pytestmark = [pytest.mark.claim("C-011")]

SUITES = ("mini-general", "mini-science")


def pairs() -> dict[str, tuple[Run, Run]]:
    """suite -> (baseline, optimized), newest matching pair per suite."""
    found: dict[str, tuple[Run, Run]] = {}
    all_runs = runs()
    for suite in SUITES:
        optimized = [r for r in all_runs if r.suite == suite and r.optimize is True]
        for candidate in sorted(optimized, key=lambda r: r.meta.get("started", ""), reverse=True):
            baselines = [
                r for r in all_runs if r.optimize is False and r.setup() == candidate.setup()
            ]
            if baselines:
                baseline = max(baselines, key=lambda r: r.meta.get("started", ""))
                found[suite] = (baseline, candidate)
                break
    return found


def uncached(row: dict[str, Any]) -> int:
    counts = row.get("tokens") or {}
    used = int(counts.get("input") or 0) - int(counts.get("cached_input") or 0)
    return max(0, used) + int(counts.get("output") or 0)


MEASURES = {"tokens": tokens, "uncached": uncached}


def summary(run: Run) -> dict[str, Any]:
    rows = run.rows
    passed = sum(1 for row in rows if row.get("passed"))
    return {
        "run": run.name,
        "commit": run.meta.get("commit"),
        "runs": len(rows),
        "passed": passed,
        "pass_rate": round(passed / len(rows), 4) if rows else None,
        "tokens": sum(tokens(row) for row in rows),
        "input_tokens": sum(int((row.get("tokens") or {}).get("input") or 0) for row in rows),
        "output_tokens": sum(int((row.get("tokens") or {}).get("output") or 0) for row in rows),
        "uncached_tokens": sum(uncached(row) for row in rows),
    }


def clusters(baseline: Run, optimized: Run, measure: Any) -> list[tuple[float, float]]:
    """(optimized, baseline) totals per task over the seeds both runs have."""
    base = {(row["task"], row["seed"]): row for row in baseline.rows}
    opt = {(row["task"], row["seed"]): row for row in optimized.rows}
    shared = sorted(set(base) & set(opt))
    by_task: dict[str, list[float]] = {}
    for key in shared:
        totals = by_task.setdefault(str(key[0]), [0.0, 0.0])
        totals[0] += measure(opt[key])
        totals[1] += measure(base[key])
    return [(o, b) for o, b in by_task.values()]


def reduction(cluster_list: list[tuple[float, float]], confidence: float) -> dict[str, Any]:
    """The reduction in percent with its interval (flipped from the ratio's)."""
    opt, base = sum(c[0] for c in cluster_list), sum(c[1] for c in cluster_list)
    ratio = metrics.bootstrap_ratio_ci(cluster_list, confidence=confidence)
    return {
        "percent": round(100.0 * (1.0 - opt / base), 2) if base else None,
        "interval": {
            "low": round(100.0 * (1.0 - ratio.high), 2),
            "high": round(100.0 * (1.0 - ratio.low), 2),
            "confidence": confidence,
            "method": ratio.method,
            "tasks": len(cluster_list),
        },
    }


def test_optimized_uses_fewer_tokens_at_same_pass_rate() -> None:
    limits = evidence.thresholds("C-011")
    confidence = float(limits.get("confidence_level", metrics.DEFAULT_CONFIDENCE))
    # What the claim counts (ADR 0028): "uncached" tokens or all "tokens".
    measure_name = str(limits.get("measure", "tokens"))
    assert measure_name in MEASURES, measure_name
    measure = MEASURES[measure_name]
    found = pairs()
    assert set(found) == set(SUITES), f"no baseline/optimized pair for {set(SUITES) - set(found)}"
    suites: dict[str, Any] = {}
    problems = []
    all_tokens: list[tuple[float, float]] = []
    all_uncached: list[tuple[float, float]] = []
    all_claimed: list[tuple[float, float]] = []
    for suite, (baseline, optimized) in found.items():
        base, opt = summary(baseline), summary(optimized)
        assert base["runs"] == opt["runs"] > 0, (suite, base["runs"], opt["runs"])
        by_tokens = clusters(baseline, optimized, tokens)
        by_uncached = clusters(baseline, optimized, uncached)
        all_tokens += by_tokens
        all_uncached += by_uncached
        by_claimed = clusters(baseline, optimized, measure)
        all_claimed += by_claimed
        total = reduction(by_tokens, confidence)
        claimed = reduction(by_claimed, confidence)
        suites[suite] = {
            "baseline": base,
            "optimized": opt,
            "token_reduction_percent": total["percent"],
            "token_reduction_interval": total["interval"],
            "uncached_reduction": reduction(by_uncached, confidence),
            "claimed": {"measure": measure_name, **claimed},
        }
        if claimed["percent"] is None or claimed["percent"] <= limits["token_reduction_pct_min"]:
            problems.append(f"{suite}: {claimed['percent']}% fewer {measure_name} tokens")
        if limits["pass_rate_not_lower"] and opt["pass_rate"] < base["pass_rate"]:
            problems.append(f"{suite}: pass rate {opt['pass_rate']} < {base['pass_rate']}")
    base_total = sum(s["baseline"]["tokens"] for s in suites.values())
    opt_total = sum(s["optimized"]["tokens"] for s in suites.values())
    overall = round(100.0 * (1.0 - opt_total / base_total), 1)
    interval = reduction(all_tokens, confidence)["interval"]
    claimed_overall = reduction(all_claimed, confidence)
    claimed_overall["percent"] = round(claimed_overall["percent"], 1)
    low = claimed_overall["interval"]["low"]
    if limits.get("significant") and low <= limits["token_reduction_pct_min"]:
        problems.append(
            f"overall: {claimed_overall['percent']}% fewer {measure_name} tokens, but the"
            f" {confidence:.0%} interval ({low}% to {claimed_overall['interval']['high']}%)"
            f" does not lie above {limits['token_reduction_pct_min']}%"
        )
    evidence.write(
        "C-011",
        "comparison",
        {
            "suites": suites,
            "baseline_tokens": base_total,
            "optimized_tokens": opt_total,
            "token_reduction_percent": overall,
            "token_reduction_interval": interval,
            "uncached_reduction": reduction(all_uncached, confidence),
            "claimed": {"measure": measure_name, **claimed_overall},
            "problems": problems,
            # `ssw prove --claim C-011` recomputes every number from the committed bench runs;
            # these commands produce new runs (each run's run.json records its own).
            "bench_commands": [
                "uv run ssw bench run --suite <suite> --baseline ...",
                "uv run ssw bench run --suite <suite> ...",
            ],
        },
    )
    assert not problems, problems
