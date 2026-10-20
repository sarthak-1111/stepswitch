"""The frontier comparison behind claims C-021 to C-023 (ADR 0034): reading the bench runs.

Stepswitch's contestants (`router`, `harness:claude-code`) and the frontier baselines
(`native:claude-code:<model>`: Claude Code alone, on its own login) run on the mini suites, in
one bench run or in several. For each suite and contestant the comparison reads the newest bench
run that has the contestant (`run.json` `contestants`). A contestant's rows of a suite therefore
come from one run, and no run is picked by its results.

A task run is a (suite, task, seed) triple. The comparison uses the task runs every contestant
has, and they must cover every task of both suites with at least `min_seeds` seeds, so a partial
run (a pilot) proves nothing. A task run where any contestant's run hit an infrastructure error
(the row's `infra_error`: an API or model-server error, a usage limit, a harness that ended
without reporting) is left out for every contestant and listed in the evidence; at most
`max_excluded_share` of the task runs may be left out.

Pass rates carry 95% cluster-bootstrap intervals over tasks (the seeds of a task kept
together), and the paired difference per task run carries one too (`stepswitch.bench.metrics`).

With the `preflight` threshold, every compared run of a contestant on local models (any but
`native:`) must have passed the bench's canary check (`run.json` `preflight`, ADR 0037): a
model that answers nothing would otherwise go unnoticed. With `same_pool`, those runs must
share one router pool.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from stepswitch.bench import metrics
from stepswitch.bench.suites import load_suite
from tests.claims.bench_runs import ROOT, Run, runs, tokens

SUITES = ("mini-general", "mini-science")
# The role of the Stepswitch contestant in the evidence (its baselines have their own roles).
OURS = "stepswitch"
Key = tuple[str, str, int]
Row = dict[str, Any]


def uncached(row: Row) -> int:
    """Input tokens not served from a prompt cache, plus output tokens (ADR 0028)."""
    counts = row.get("tokens") or {}
    used = int(counts.get("input") or 0) - int(counts.get("cached_input") or 0)
    return max(0, used) + int(counts.get("output") or 0)


MEASURES: dict[str, Callable[[Row], int]] = {"tokens": tokens, "uncached": uncached}


def newest_run(suite: str, contestant: str, found: Sequence[Run]) -> Run | None:
    """The newest run of `suite` with `contestant` among its contestants."""
    having = [r for r in found if r.suite == suite and contestant in r.meta.get("contestants", [])]
    return max(having, key=lambda r: str(r.meta.get("started", "")), default=None)


@dataclass
class Comparison:
    """The task runs of some contestants, as the claims compare them."""

    roles: dict[str, str]  # role -> contestant
    rows: dict[str, dict[Key, Row]] = field(default_factory=dict)  # contestant -> rows
    runs: dict[str, dict[str, str]] = field(default_factory=dict)  # contestant -> suite -> run
    # contestant -> suite -> that run's run.json
    metas: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    shared: list[Key] = field(default_factory=list)
    excluded: list[Key] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def outcomes(self, role: str, keys: Sequence[Key] | None = None) -> dict[Key, float]:
        rows = self.rows[self.roles[role]]
        return {key: float(bool(rows[key].get("passed"))) for key in keys or self.shared}

    def measure(self, role: str, measure: Callable[[Row], int]) -> dict[Key, float]:
        rows = self.rows[self.roles[role]]
        return {key: float(measure(rows[key])) for key in self.shared}


def compare(roles: Mapping[str, str], *, min_seeds: int, max_excluded_share: float) -> Comparison:
    """Collect the task runs of `roles` (role -> contestant) and check their coverage."""
    found = runs()
    comp = Comparison(dict(roles))
    for name in roles.values():
        comp.rows[name], comp.runs[name], comp.metas[name] = {}, {}, {}
        for suite in SUITES:
            run = newest_run(suite, name, found)
            if run is None:
                comp.problems.append(f"{suite}: no bench run has the contestant {name}")
                continue
            comp.runs[name][suite] = run.name
            comp.metas[name][suite] = run.meta
            for row in run.rows:
                if row.get("contestant") == name:
                    comp.rows[name][(suite, str(row["task"]), int(row["seed"]))] = row
    common = set.intersection(*(set(rows) for rows in comp.rows.values()))
    for key in sorted(common):
        if any(comp.rows[name][key].get("infra_error") for name in roles.values()):
            comp.excluded.append(key)
        else:
            comp.shared.append(key)
    for suite in SUITES:
        seeds: dict[str, int] = {}
        for _, task, _ in (key for key in common if key[0] == suite):
            seeds[task] = seeds.get(task, 0) + 1
        for task in (t.id for t in load_suite(suite, ROOT).tasks):
            if seeds.get(task, 0) < min_seeds:
                comp.problems.append(
                    f"{suite}/{task}: {seeds.get(task, 0)} task run(s) shared by every"
                    f" contestant, {min_seeds} needed"
                )
    planned = len(comp.shared) + len(comp.excluded)
    if planned and len(comp.excluded) / planned > max_excluded_share:
        comp.problems.append(
            f"{len(comp.excluded)} of {planned} task runs hit infrastructure errors"
            f" (at most {max_excluded_share:.0%} may be left out)"
        )
    return comp


def by_task(values: Mapping[Key, float], keys: Sequence[Key]) -> list[list[float]]:
    """One group of values per task, its seeds together (the bootstrap's clusters)."""
    groups: dict[tuple[str, str], list[float]] = {}
    for key in keys:
        groups.setdefault(key[:2], []).append(values[key])
    return [groups[task] for task in sorted(groups)]


def percent_interval(groups: list[list[float]], confidence: float) -> dict[str, Any]:
    ci = metrics.bootstrap_ci(groups, confidence=confidence)
    return {"low": round(100 * ci.low, 1), "high": round(100 * ci.high, 1), "method": ci.method}


def pass_summary(comp: Comparison, role: str, keys: Sequence[Key], confidence: float) -> Row:
    outcomes = comp.outcomes(role, keys)
    passed = int(sum(outcomes.values()))
    return {
        "contestant": comp.roles[role],
        "passed": passed,
        "runs": len(keys),
        "pass_rate_percent": round(100 * passed / len(keys), 1) if keys else None,
        "interval_percent": percent_interval(by_task(outcomes, keys), confidence) if keys else None,
    }


def models_seen(comp: Comparison, role: str) -> list[str]:
    rows = comp.rows[comp.roles[role]]
    return sorted({str(name) for key in comp.shared for name in rows[key].get("models") or {}})


def check_models(comp: Comparison, baselines: Mapping[str, Mapping[str, str]]) -> None:
    """Every compared run of a baseline used the model its claim names, if it names one."""
    for role, spec in baselines.items():
        if "model" not in spec:
            continue
        rows = comp.rows[spec["contestant"]]
        wrong = [
            f"{suite}/{task} s{seed}"
            for suite, task, seed in comp.shared
            if not any(
                spec["model"] in str(name) for name in rows[(suite, task, seed)].get("models") or {}
            )
        ]
        if wrong:
            comp.problems.append(
                f"{role}: {len(wrong)} run(s) did not use {spec['model']}: {wrong[:5]}"
            )


def local_contestants(comp: Comparison) -> list[str]:
    """The compared contestants that run on local workers: every one but `native:` ones."""
    return [name for name in comp.roles.values() if not name.startswith("native:")]


def check_preflight(comp: Comparison) -> dict[str, dict[str, Any]]:
    """Each local contestant's runs passed the bench's canary check; returns what they record."""
    seen: dict[str, dict[str, Any]] = {}
    for name in local_contestants(comp):
        for suite, meta in comp.metas.get(name, {}).items():
            preflight = meta.get("preflight") or {}
            seen.setdefault(name, {})[suite] = preflight
            if preflight.get("ok") is not True:
                run = comp.runs[name][suite]
                comp.problems.append(f"{name}: run {run} has no passing canary check (preflight)")
    return seen


def check_same_pool(comp: Comparison) -> dict[str, list[str]]:
    """The local contestants' runs of each suite used one router pool; returns the pools."""
    pools: dict[str, list[str]] = {}
    for suite in SUITES:
        found = {
            name: tuple(sorted(comp.metas.get(name, {}).get(suite, {}).get("router_pool") or []))
            for name in local_contestants(comp)
        }
        if len(set(found.values())) > 1:
            comp.problems.append(f"{suite}: the contestants' runs used different router pools")
        pools[suite] = sorted({worker for pool in found.values() for worker in pool})
    return pools


def check_hosted(comp: Comparison, role: str, most: int) -> int:
    """The hosted tokens of a role's compared runs; a problem when over `most` or unknown."""
    rows = comp.rows[comp.roles[role]]
    unknown = [key for key in comp.shared if rows[key].get("hosted_tokens") is None]
    if unknown:
        comp.problems.append(f"{role}: {len(unknown)} run(s) without hosted-token accounting")
    hosted = sum(int(rows[key].get("hosted_tokens") or 0) for key in comp.shared)
    if hosted > most:
        comp.problems.append(f"{role}: {hosted} tokens went to hosted models (at most {most})")
    return hosted


def excluded_list(comp: Comparison) -> list[str]:
    return [f"{suite}/{task} s{seed}" for suite, task, seed in comp.excluded]


def roles_of(limits: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, Mapping[str, str]]]:
    """(role -> contestant for every contestant, the baselines' specs) from a claim's thresholds."""
    baselines = dict(limits["baselines"])
    roles = {OURS: str(limits["contestant"])}
    roles.update({role: str(spec["contestant"]) for role, spec in baselines.items()})
    return roles, baselines


def bench_commands(limits: Mapping[str, Any]) -> list[str]:
    """The command that produces new runs for a claim's contestants; each run's run.json records
    the command it ran. The evidence's own command, `ssw prove --claim <id>`, recomputes every
    number from the committed runs, as for C-011."""
    roles, _ = roles_of(limits)
    return [
        f"uv run ssw bench run --suite <suite> --workers {','.join(roles.values())}"
        f" --seeds {limits['min_seeds']} --router-pool <local workers> ..."
    ]


def check_runs(comp: Comparison, limits: Mapping[str, Any], report: dict[str, Any]) -> None:
    """The `preflight` and `same_pool` thresholds, recorded in the report."""
    if limits.get("preflight"):
        report["preflight"] = check_preflight(comp)
    if limits.get("same_pool"):
        report["router_pools"] = check_same_pool(comp)


def parity(limits: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """C-021 and C-023: the Stepswitch contestant passes at least as many shared task runs as
    each baseline, both suites together, and sends at most `hosted_tokens_max` tokens to hosted
    models. Returns the evidence section and the problems."""
    confidence = float(limits.get("confidence_level", metrics.DEFAULT_CONFIDENCE))
    roles, baselines = roles_of(limits)
    comp = compare(
        roles,
        min_seeds=int(limits["min_seeds"]),
        max_excluded_share=float(limits["max_excluded_share"]),
    )
    report: dict[str, Any] = {
        "confidence": confidence,
        "roles": roles,
        "runs": comp.runs,
        "bench_commands": bench_commands(limits),
        "task_runs": len(comp.shared),
        "excluded": excluded_list(comp),
    }
    if comp.problems and not comp.shared:
        report["problems"] = comp.problems
        return report, comp.problems
    check_models(comp, baselines)
    check_runs(comp, limits, report)
    report["hosted_tokens"] = check_hosted(comp, OURS, int(limits["hosted_tokens_max"]))
    report[OURS] = pass_summary(comp, OURS, comp.shared, confidence)
    ours = comp.outcomes(OURS)
    for role in baselines:
        theirs = comp.outcomes(role)
        difference = {key: ours[key] - theirs[key] for key in comp.shared}
        report[role] = {
            **pass_summary(comp, role, comp.shared, confidence),
            "models": models_seen(comp, role),
            "difference_percent": {
                "mean": round(100 * sum(difference.values()) / len(comp.shared), 1),
                "interval": percent_interval(by_task(difference, comp.shared), confidence),
                "won": sorted(f"{s}/{t} s{n}" for (s, t, n), d in difference.items() if d > 0),
                "lost": sorted(f"{s}/{t} s{n}" for (s, t, n), d in difference.items() if d < 0),
            },
        }
        if report[OURS]["passed"] < report[role]["passed"]:
            comp.problems.append(
                f"{roles[OURS]} passed {report[OURS]['passed']} of {len(comp.shared)} task runs,"
                f" {roles[role]} {report[role]['passed']}"
            )
    report["suites"] = {
        suite: {
            role: pass_summary(comp, role, [k for k in comp.shared if k[0] == suite], confidence)
            for role in roles
        }
        for suite in SUITES
    }
    report["problems"] = comp.problems
    return report, comp.problems


def ratio(clusters: list[tuple[float, float]], confidence: float) -> dict[str, Any]:
    """How many percent fewer the first of each (ours, theirs) pair is, with its interval."""
    ours, theirs = sum(c[0] for c in clusters), sum(c[1] for c in clusters)
    ci = metrics.bootstrap_ratio_ci(clusters, confidence=confidence)
    return {
        "percent": round(100.0 * (1.0 - ours / theirs), 1) if theirs else None,
        "ours": int(ours),
        "theirs": int(theirs),
        "interval": {
            "low": round(100.0 * (1.0 - ci.high), 1),
            "high": round(100.0 * (1.0 - ci.low), 1),
            "confidence": confidence,
            "method": ci.method,
            "tasks": len(clusters),
        },
    }


def token_clusters(
    comp: Comparison, role: str, measure: Callable[[Row], int]
) -> list[tuple[float, float]]:
    """(ours, theirs) totals of `measure` per task over the shared task runs."""
    ours, theirs = comp.measure(OURS, measure), comp.measure(role, measure)
    groups: dict[tuple[str, str], list[float]] = {}
    for key in comp.shared:
        totals = groups.setdefault(key[:2], [0.0, 0.0])
        totals[0] += ours[key]
        totals[1] += theirs[key]
    return [(o, t) for o, t in (groups[task] for task in sorted(groups))]


def tokens_comparison(limits: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """C-022: the Stepswitch contestant uses less of the claimed measure per task than each
    baseline, over the shared task runs, significantly with the `significant` threshold, and
    sends at most `hosted_tokens_max` tokens to hosted models when that threshold is set. Both
    measures are reported whichever is claimed; `uncached_more_percent` is how many percent
    more uncached tokens the Stepswitch contestant used (ADR 0036)."""
    confidence = float(limits.get("confidence_level", metrics.DEFAULT_CONFIDENCE))
    measure_name = str(limits["measure"])
    measure = MEASURES[measure_name]
    roles, baselines = roles_of(limits)
    comp = compare(
        roles,
        min_seeds=int(limits["min_seeds"]),
        max_excluded_share=float(limits["max_excluded_share"]),
    )
    report: dict[str, Any] = {
        "confidence": confidence,
        "measure": measure_name,
        "roles": roles,
        "runs": comp.runs,
        "bench_commands": bench_commands(limits),
        "task_runs": len(comp.shared),
        "excluded": excluded_list(comp),
    }
    if comp.problems and not comp.shared:
        report["problems"] = comp.problems
        return report, comp.problems
    check_models(comp, baselines)
    check_runs(comp, limits, report)
    if "hosted_tokens_max" in limits:
        report["hosted_tokens"] = check_hosted(comp, OURS, int(limits["hosted_tokens_max"]))
    for role in roles:
        rows = comp.rows[roles[role]]
        report.setdefault("totals", {})[role] = {
            "contestant": roles[role],
            "tokens": sum(tokens(rows[key]) for key in comp.shared),
            "uncached_tokens": sum(uncached(rows[key]) for key in comp.shared),
            "hosted_tokens": sum(int(rows[key].get("hosted_tokens") or 0) for key in comp.shared),
        }
    for role in baselines:
        claimed = ratio(token_clusters(comp, role, measure), confidence)
        fewer_uncached = ratio(token_clusters(comp, role, uncached), confidence)
        report[role] = {
            "claimed": {"measure": measure_name, **claimed},
            "tokens": ratio(token_clusters(comp, role, tokens), confidence),
            "uncached": fewer_uncached,
            "uncached_more_percent": (
                -fewer_uncached["percent"] if fewer_uncached["percent"] is not None else None
            ),
        }
        if claimed["percent"] is None or claimed["percent"] <= 0:
            comp.problems.append(f"{role}: {claimed['percent']}% fewer {measure_name} tokens")
        elif limits.get("significant") and claimed["interval"]["low"] <= 0:
            comp.problems.append(
                f"{role}: {claimed['percent']}% fewer {measure_name} tokens, but the"
                f" {confidence:.0%} interval ({claimed['interval']['low']}% to"
                f" {claimed['interval']['high']}%) does not lie above zero"
            )
    report["problems"] = comp.problems
    return report, comp.problems
