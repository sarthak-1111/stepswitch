"""C-011: uses fewer tokens than the unoptimized baseline at the same pass rate.

Proof (SPEC §18.5): bench evidence comparing the Phase 2 baseline with the optimized
configuration; the public text carries the measured percentage. Scope: the mini bench suites,
optimized configuration vs the Phase 2 baseline.

The baseline is the same code with every token optimization of SPEC §13 switched off
(`ssw bench run --baseline`, which records `"optimize": false` in run.json): the Phase 2
agent's behaviour. For each mini suite the test takes the newest optimized run and the newest
baseline run with the same suite, contestants, router pool, tasks and seeds, and compares:

* tokens: input plus output tokens over all task runs, from the result rows (cached input
  tokens count as input; the ledger of each run is what the rows add up);
* pass rate: the share of task runs whose acceptance commands passed.

The claim holds when every suite uses fewer tokens and passes at least as often. The README
number is the reduction over both suites together.
"""

from __future__ import annotations

from typing import Any

import pytest

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
    }


def test_optimized_uses_fewer_tokens_at_same_pass_rate() -> None:
    limits = evidence.thresholds("C-011")
    found = pairs()
    assert set(found) == set(SUITES), f"no baseline/optimized pair for {set(SUITES) - set(found)}"
    suites: dict[str, Any] = {}
    problems = []
    for suite, (baseline, optimized) in found.items():
        base, opt = summary(baseline), summary(optimized)
        assert base["runs"] == opt["runs"] > 0, (suite, base["runs"], opt["runs"])
        reduction = 100.0 * (1.0 - opt["tokens"] / base["tokens"])
        suites[suite] = {
            "baseline": base,
            "optimized": opt,
            "token_reduction_percent": round(reduction, 2),
        }
        if reduction <= limits["token_reduction_pct_min"]:
            problems.append(f"{suite}: {reduction:.1f}% fewer tokens")
        if limits["pass_rate_not_lower"] and opt["pass_rate"] < base["pass_rate"]:
            problems.append(f"{suite}: pass rate {opt['pass_rate']} < {base['pass_rate']}")
    base_total = sum(s["baseline"]["tokens"] for s in suites.values())
    opt_total = sum(s["optimized"]["tokens"] for s in suites.values())
    overall = round(100.0 * (1.0 - opt_total / base_total), 1)
    evidence.write(
        "C-011",
        "comparison",
        {
            "suites": suites,
            "baseline_tokens": base_total,
            "optimized_tokens": opt_total,
            "token_reduction_percent": overall,
            "problems": problems,
        },
        command="uv run ssw bench run --suite <suite> --baseline ... ; uv run ssw bench run "
        "--suite <suite> ... (commands in each run's run.json); uv run ssw prove --claim C-011",
    )
    assert not problems, problems
