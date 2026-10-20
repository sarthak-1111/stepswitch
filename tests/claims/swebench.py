"""The SWE-bench Verified Mini comparison behind claims C-026 to C-028 (ADR 0042).

`ssw bench run --suite swebench-verified-mini` writes one results directory per run. The claims
read two of them:

- the gold check, whose directory name ends in `-swebench-gold`: the dataset's reference patches
  graded by the official harness on the same machine;
- the confirmatory run, whose name ends in `-swebench-mini-confirmatory`: Stepswitch (`router`,
  with a pool of one model) and mini-SWE-agent on that same model. There must be exactly one,
  so no run is picked by its results (the test set runs once).

An instance whose reference patch the harness did not resolve is left out for both contestants,
and so is one where either contestant's run hit an infrastructure error (the row's
`infra_error`); at most `max_excluded_share` of the suite may be left out, and every left-out
instance is listed. Every other instance needs one graded run of each contestant.

Resolve rates carry Wilson intervals over instances; the paired difference carries a cluster
bootstrap interval over instances and an exact McNemar test (`stepswitch.bench.metrics`). Token
ratios carry cluster bootstrap intervals over instances, as in the frontier comparison.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from stepswitch.bench import metrics
from stepswitch.bench.suites import load_suite
from tests.claims.bench_runs import ROOT, Run, runs, tokens
from tests.claims.frontier import ratio, uncached

CONFIRMATORY = "-swebench-mini-confirmatory"
GOLD = "-swebench-gold"
OURS = "stepswitch"
THEIRS = "mini_swe_agent"
MINI_PREFIX = "mini-swe-agent:"
Row = dict[str, Any]


@dataclass
class Swebench:
    """The graded runs of both contestants on the instances they are compared on."""

    limits: Mapping[str, Any]
    run: Run | None = None
    gold: Run | None = None
    roles: dict[str, str] = field(default_factory=dict)  # role -> contestant
    rows: dict[str, dict[str, Row]] = field(default_factory=dict)  # role -> instance -> row
    instances: list[str] = field(default_factory=list)  # compared
    excluded: dict[str, list[str]] = field(default_factory=dict)  # reason -> instances
    problems: list[str] = field(default_factory=list)

    def resolved(self, role: str) -> dict[str, bool]:
        return {i: bool(self.rows[role][i].get("passed")) for i in self.instances}


def _one(found: list[Run], suffix: str, suite: str, what: str, problems: list[str]) -> Run | None:
    named = [run for run in found if run.name.endswith(suffix) and run.suite == suite]
    if len(named) != 1:
        problems.append(f"{len(named)} {what} runs of {suite} under bench/results ({suffix})")
    return named[-1] if named else None


def load(limits: Mapping[str, Any]) -> Swebench:
    """Read the gold check and the confirmatory run, and check how they were run."""
    suite = str(limits["suite"])
    comp = Swebench(limits)
    found = runs()
    comp.run = _one(found, CONFIRMATORY, suite, "confirmatory", comp.problems)
    comp.gold = _one(found, GOLD, suite, "gold-check", comp.problems)
    if comp.run is None or comp.gold is None:
        return comp
    meta = comp.run.meta
    pool = list(meta.get("router_pool") or [])
    worker = pool[0] if len(pool) == 1 else None
    comp.roles = {OURS: "router", THEIRS: f"{MINI_PREFIX}{worker}"}
    checks = (
        (meta.get("kind") == "swebench", "the confirmatory run is not a SWE-bench run"),
        (worker is not None, f"the router pool is {pool}, not one model"),
        (
            worker is not None and worker.partition("/")[2] == limits["model"],
            f"the pool's model is not {limits['model']}",
        ),
        (
            list(meta.get("contestants") or []) == list(comp.roles.values()),
            f"contestants {meta.get('contestants')}, not {list(comp.roles.values())}",
        ),
        (list(meta.get("seeds") or []) == [0], "the confirmatory run must have one seed"),
        (
            (meta.get("dataset") or {}).get("name") == limits["dataset"],
            f"the dataset is not {limits['dataset']}",
        ),
        (
            (meta.get("harness") or {}).get("swebench") == limits["harness"],
            f"the harness is not swebench {limits['harness']}",
        ),
        (
            not limits.get("preflight") or (meta.get("preflight") or {}).get("ok") is True,
            "the confirmatory run has no passing canary check",
        ),
    )
    comp.problems += [problem for ok, problem in checks if not ok]
    ids = [task.id for task in load_suite(suite, ROOT).tasks]
    if list(meta.get("tasks") or []) != ids:
        comp.problems.append("the confirmatory run did not cover the suite's instances")
    gold = {str(row["task"]): bool(row.get("passed")) for row in comp.gold.rows}
    comp.excluded["gold"] = [i for i in ids if not gold.get(i)]
    for role, contestant in comp.roles.items():
        mine = [row for row in comp.run.rows if row.get("contestant") == contestant]
        comp.rows[role] = {str(row["task"]): row for row in mine}
        if len(comp.rows[role]) != len(mine):
            comp.problems.append(f"{contestant}: more than one run of an instance")
    comp.excluded["infra"] = [
        i
        for i in ids
        if i not in comp.excluded["gold"]
        and any(comp.rows[role].get(i, {}).get("infra_error") for role in comp.roles)
    ]
    left_out = set(comp.excluded["gold"]) | set(comp.excluded["infra"])
    comp.instances = [i for i in ids if i not in left_out]
    for role, contestant in comp.roles.items():
        missing = [i for i in comp.instances if i not in comp.rows[role]]
        if missing:
            comp.problems.append(f"{contestant}: no run of {', '.join(missing[:5])}")
    if ids and len(left_out) / len(ids) > float(limits["max_excluded_share"]):
        comp.problems.append(
            f"{len(left_out)} of {len(ids)} instances left out (at most"
            f" {float(limits['max_excluded_share']):.0%} may be)"
        )
    comp.instances = [i for i in comp.instances if all(i in comp.rows[r] for r in comp.roles)]
    return comp


def _per_instance(rows: list[Row], measure: Callable[[Row], float]) -> float:
    return round(sum(measure(row) for row in rows) / len(rows), 1) if rows else 0.0


def measured(comp: Swebench) -> dict[str, Any]:
    """Each contestant's resolve rate and means per instance, and the paired comparison: what
    the README's SWE-bench table shows, whatever a claim asserts."""
    confidence = float(comp.limits.get("confidence_level", metrics.DEFAULT_CONFIDENCE))
    n = len(comp.instances)
    results: dict[str, Any] = {}
    for role, contestant in comp.roles.items():
        rows = [comp.rows[role][i] for i in comp.instances]
        resolved = sum(bool(row.get("passed")) for row in rows)
        interval = metrics.wilson_interval(resolved, n, confidence)
        results[role] = {
            "contestant": contestant,
            "resolved": resolved,
            "instances": n,
            "resolved_percent": round(100 * resolved / n, 1) if n else None,
            "interval_percent": {
                "low": round(100 * interval.low, 1) if n else None,
                "high": round(100 * interval.high, 1) if n else None,
                "method": interval.method,
            },
            # Resolved by a run that ended as done, not stopped by a limit (ADR 0042, item 4).
            "resolved_done": sum(
                bool(row.get("passed")) and row.get("status") == "done" for row in rows
            ),
            "empty_patches": sum(
                bool((row.get("swebench") or {}).get("empty_patch")) for row in rows
            ),
            "per_instance": {
                "tokens": round(_per_instance(rows, tokens)),
                "uncached_tokens": round(_per_instance(rows, uncached)),
                "hosted_tokens": round(_per_instance(rows, lambda r: r.get("hosted_tokens") or 0)),
                "steps": _per_instance(rows, lambda r: r.get("steps") or 0),
                "minutes": round(_per_instance(rows, lambda r: r.get("wall_seconds") or 0) / 60, 1),
            },
            "median_minutes": round(median(r.get("wall_seconds") or 0 for r in rows) / 60, 1)
            if rows
            else None,
        }
    ours, theirs = comp.resolved(OURS), comp.resolved(THEIRS)
    only_ours = sum(ours[i] and not theirs[i] for i in comp.instances)
    only_theirs = sum(theirs[i] and not ours[i] for i in comp.instances)
    groups = [[float(ours[i]) - float(theirs[i])] for i in comp.instances]
    difference = metrics.bootstrap_ci(groups, confidence=confidence) if n > 1 else None
    paired = {
        "both": sum(ours[i] and theirs[i] for i in comp.instances),
        f"{OURS}_only": only_ours,
        f"{THEIRS}_only": only_theirs,
        "neither": sum(not ours[i] and not theirs[i] for i in comp.instances),
        "mcnemar_p": round(metrics.mcnemar_exact(only_ours, only_theirs), 4),
        "difference_percent": round(100 * (only_ours - only_theirs) / n, 1) if n else None,
        "difference_interval_percent": {
            "low": round(100 * difference.low, 1),
            "high": round(100 * difference.high, 1),
            "method": difference.method,
        }
        if difference is not None
        else None,
    }
    return {"results": results, "paired": paired}


def token_ratios(comp: Swebench) -> dict[str, Any]:
    """How many percent fewer tokens, cached included and uncached, Stepswitch used than
    mini-SWE-agent over the compared instances, with intervals."""
    confidence = float(comp.limits.get("confidence_level", metrics.DEFAULT_CONFIDENCE))
    found: dict[str, Any] = {}
    for name, measure in (("tokens", tokens), ("uncached", uncached)):
        clusters = [
            (float(measure(comp.rows[OURS][i])), float(measure(comp.rows[THEIRS][i])))
            for i in comp.instances
        ]
        found[name] = ratio(clusters, confidence)
    return found


def report(comp: Swebench) -> dict[str, Any]:
    """The evidence every SWE-bench claim writes: provenance, what was measured, problems."""
    data: dict[str, Any] = {
        "runs": {
            "confirmatory": comp.run.name if comp.run else None,
            "gold": comp.gold.name if comp.gold else None,
        },
        "roles": comp.roles,
        "instances": len(comp.instances),
        "excluded": comp.excluded,
    }
    if comp.run is not None:
        meta = comp.run.meta
        data["bench_command"] = meta.get("command")
        data["dataset"] = meta.get("dataset")
        data["harness"] = meta.get("harness")
        data["commit"] = meta.get("commit")
    if comp.instances and set(comp.roles) <= set(comp.rows):
        data.update(measured(comp))
        data["tokens"] = token_ratios(comp)
        hosted = sum(
            int(comp.rows[role][i].get("hosted_tokens") or 0)
            for role in comp.roles
            for i in comp.instances
        )
        data["hosted_tokens"] = hosted
        most = comp.limits.get("hosted_tokens_max")
        if most is not None and hosted > int(most):
            comp.problems.append(f"{hosted} tokens went to hosted models (at most {most})")
    elif not comp.problems:
        comp.problems.append("no instance to compare")
    data["problems"] = comp.problems
    return data
