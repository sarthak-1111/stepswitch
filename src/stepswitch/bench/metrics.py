"""Bench metrics (SPEC §16): pass rate with a confidence interval, median wall time, tokens,
GPU-seconds, escalation rate, cache hit rate and a reference-cost equivalent.

Runs of one task with different seeds are not independent, so the confidence interval of a pass
rate resamples tasks and keeps all runs of a task together (a cluster bootstrap, percentile
method). With fewer than two tasks there is nothing to resample, and the Wilson score interval
over runs is used instead. Resampling uses a fixed seed, so a report is reproducible from its
results file.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_CONFIDENCE = 0.95
DEFAULT_RESAMPLES = 10_000
DEFAULT_SEED = 0
BOOTSTRAP = "cluster bootstrap over tasks (percentile)"
WILSON = "Wilson score interval over runs"


@dataclass(frozen=True)
class Interval:
    low: float
    high: float
    confidence: float
    method: str
    resamples: int = 0


def quantile(ordered: Sequence[float], p: float) -> float:
    """Linear interpolation between order statistics (the common "type 7" definition)."""
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * p
    below = math.floor(position)
    above = min(below + 1, len(ordered) - 1)
    weight = position - below
    return ordered[below] + (ordered[above] - ordered[below]) * weight


def wilson_interval(
    successes: float, runs: int, confidence: float = DEFAULT_CONFIDENCE
) -> Interval:
    """The Wilson score interval for `successes` out of `runs`."""
    if runs <= 0:
        return Interval(math.nan, math.nan, confidence, WILSON)
    z = statistics.NormalDist().inv_cdf(0.5 + confidence / 2)
    rate = successes / runs
    scale = 1 + z * z / runs
    center = (rate + z * z / (2 * runs)) / scale
    half = z * math.sqrt(rate * (1 - rate) / runs + z * z / (4 * runs * runs)) / scale
    return Interval(max(0.0, center - half), min(1.0, center + half), confidence, WILSON)


def bootstrap_ci(
    groups: Sequence[Sequence[float]],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> Interval:
    """Confidence interval of the pooled mean of `groups` (one group of 0/1 outcomes per task).

    Each resample draws as many tasks as there are, with replacement, and pools all runs of the
    drawn tasks. With fewer than two non-empty groups the Wilson interval is returned.
    """
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be between 0 and 1, not {confidence}")
    if resamples < 1:
        raise ValueError(f"resamples must be at least 1, not {resamples}")
    clusters = [(float(sum(group)), len(group)) for group in groups if len(group)]
    if len(clusters) < 2:
        successes = sum(s for s, _ in clusters)
        runs = sum(n for _, n in clusters)
        return wilson_interval(successes, runs, confidence)
    rng = random.Random(seed)
    count = len(clusters)
    stats = []
    for _ in range(resamples):
        drawn = [clusters[rng.randrange(count)] for _ in range(count)]
        stats.append(sum(s for s, _ in drawn) / sum(n for _, n in drawn))
    stats.sort()
    alpha = 1 - confidence
    return Interval(
        quantile(stats, alpha / 2),
        quantile(stats, 1 - alpha / 2),
        confidence,
        BOOTSTRAP,
        resamples,
    )


def _number(value: Any) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _tokens(row: Mapping[str, Any]) -> dict[str, float]:
    tokens = row.get("tokens") or {}
    return {key: _number(tokens.get(key)) for key in ("input", "output", "cached_input")}


@dataclass
class Summary:
    """Metrics of one contestant (a worker, or the router with a policy) over its runs."""

    contestant: str
    runs: int
    tasks: int
    passed: int
    pass_rate: float
    ci: Interval
    median_wall_seconds: float | None
    median_tokens: float | None
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    gpu_seconds: float
    escalation_rate: float
    cache_hit_rate: float | None
    reference_cost: float | None
    statuses: dict[str, int] = field(default_factory=dict)
    # Runs that changed a protected path (restored before the acceptance commands ran).
    tests_modified: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize(
    rows: Iterable[Mapping[str, Any]],
    *,
    price_in: float | None = None,
    price_out: float | None = None,
    confidence: float = DEFAULT_CONFIDENCE,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> list[Summary]:
    """One summary per contestant, best first (pass rate, then the interval's lower end, then
    median wall time). Prices are per million tokens; without them there is no cost."""
    by_contestant: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_contestant[str(row["contestant"])].append(row)
    summaries = [
        _summary(name, runs, price_in, price_out, confidence, resamples, seed)
        for name, runs in by_contestant.items()
    ]
    summaries.sort(
        key=lambda s: (
            -s.pass_rate,
            -s.ci.low if not math.isnan(s.ci.low) else 0.0,
            s.median_wall_seconds if s.median_wall_seconds is not None else math.inf,
            s.contestant,
        )
    )
    return summaries


def _summary(
    name: str,
    runs: Sequence[Mapping[str, Any]],
    price_in: float | None,
    price_out: float | None,
    confidence: float,
    resamples: int,
    seed: int,
) -> Summary:
    by_task: dict[str, list[float]] = defaultdict(list)
    for row in runs:
        by_task[str(row["task"])].append(1.0 if row.get("passed") else 0.0)
    passed = sum(1 for row in runs if row.get("passed"))
    walls = sorted(_number(row.get("wall_seconds")) for row in runs)
    tokens = [_tokens(row) for row in runs]
    totals = sorted(t["input"] + t["output"] for t in tokens)
    input_tokens = sum(t["input"] for t in tokens)
    output_tokens = sum(t["output"] for t in tokens)
    cached = sum(t["cached_input"] for t in tokens)
    cost = None
    if price_in is not None or price_out is not None:
        cost = (input_tokens * (price_in or 0.0) + output_tokens * (price_out or 0.0)) / 1e6
    return Summary(
        contestant=name,
        runs=len(runs),
        tasks=len(by_task),
        passed=passed,
        pass_rate=passed / len(runs),
        ci=bootstrap_ci(
            list(by_task.values()), confidence=confidence, resamples=resamples, seed=seed
        ),
        median_wall_seconds=quantile(walls, 0.5) if walls else None,
        median_tokens=quantile(totals, 0.5) if totals else None,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cached_input_tokens=int(cached),
        gpu_seconds=round(sum(_number(row.get("gpu_seconds")) for row in runs), 6),
        escalation_rate=sum(1 for row in runs if _number(row.get("escalations")) > 0) / len(runs),
        cache_hit_rate=cached / input_tokens if input_tokens else None,
        reference_cost=round(cost, 6) if cost is not None else None,
        statuses=dict(Counter(str(row.get("status")) for row in runs)),
        tests_modified=sum(1 for row in runs if row.get("tests_modified")),
    )
