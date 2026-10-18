"""Bench metrics (SPEC §16) against numerical fixtures."""

from __future__ import annotations

import math
from typing import Any

import pytest

from stepswitch.bench.metrics import (
    BOOTSTRAP,
    WILSON,
    bootstrap_ci,
    bootstrap_ratio_ci,
    quantile,
    summarize,
    wilson_interval,
)


def test_quantile_interpolates_between_order_statistics() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert quantile(values, 0.0) == 1.0
    assert quantile(values, 1.0) == 4.0
    assert quantile(values, 0.5) == 2.5
    assert quantile(values, 0.25) == pytest.approx(1.75)
    assert math.isnan(quantile([], 0.5))


def test_wilson_interval_matches_the_published_value() -> None:
    # 8 successes in 10 runs, 95%: 0.4902 to 0.9433 (Wilson 1927; common reference tables).
    interval = wilson_interval(8, 10)
    assert (round(interval.low, 4), round(interval.high, 4)) == (0.4902, 0.9433)
    assert interval.method == WILSON
    # 0 of 10 keeps the lower end at 0: 0.0000 to 0.2775.
    zero = wilson_interval(0, 10)
    assert (round(zero.low, 4), round(zero.high, 4)) == (0.0, 0.2775)
    assert math.isnan(wilson_interval(0, 0).low)


def test_cluster_bootstrap_matches_the_exact_resampling_distribution() -> None:
    # Four tasks, one run each, three passed. Resampling four tasks with replacement gives
    # pooled means k/4 with k ~ Binomial(4, 0.75): P = 1, 12, 54, 108, 81 (/256), so the
    # cumulative distribution is 0.0039, 0.0508, 0.2617, 0.6836, 1. The 2.5% quantile lies
    # inside the mass at 0.25 and the 97.5% quantile inside the mass at 1.0.
    interval = bootstrap_ci([[1], [0], [1], [1]], resamples=20_000, seed=7)
    assert (interval.low, interval.high) == (0.25, 1.0)
    assert interval.method == BOOTSTRAP and interval.resamples == 20_000


def test_cluster_bootstrap_keeps_runs_of_a_task_together() -> None:
    # Two tasks with three seeds each: one always passes, one never does. Resampling tasks
    # gives only 0, 0.5 or 1 (P = 1/4, 1/2, 1/4), never the 1/3 or 2/3 a run-level
    # bootstrap would produce.
    interval = bootstrap_ci([[1, 1, 1], [0, 0, 0]], confidence=0.5, resamples=4_000, seed=1)
    assert interval.low in {0.0, 0.5} and interval.high in {0.5, 1.0}
    assert bootstrap_ci([[1, 1, 1], [0, 0, 0]], seed=3) == bootstrap_ci(
        [[1, 1, 1], [0, 0, 0]], seed=3
    )


def test_bootstrap_falls_back_to_wilson_with_one_task() -> None:
    interval = bootstrap_ci([[1, 1, 1, 1, 1, 1, 1, 1, 0, 0]])
    assert interval.method == WILSON
    assert (round(interval.low, 4), round(interval.high, 4)) == (0.4902, 0.9433)
    assert math.isnan(bootstrap_ci([]).low)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"confidence": 1.0}, "confidence"), ({"resamples": 0}, "resamples")],
)
def test_bootstrap_rejects_bad_settings(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        bootstrap_ci([[1], [0]], **kwargs)


def row(contestant: str, task: str, passed: bool, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "contestant": contestant,
        "task": task,
        "seed": 0,
        "passed": passed,
        "status": "done" if passed else "failed",
        "wall_seconds": 10.0,
        "tokens": {"input": 1000, "output": 100, "cached_input": 400},
        "gpu_seconds": 2.0,
        "escalations": 0,
    }
    data.update(extra)
    return data


def test_summarize_computes_every_metric_and_ranks_contestants() -> None:
    rows = [
        row("router", "a", True, wall_seconds=12.0),
        row("router", "b", True, wall_seconds=30.0, escalations=1),
        row("router", "c", False, wall_seconds=18.0),
        row("tiny", "a", True),
        row("tiny", "b", False, tokens={"input": 3000, "output": 300, "cached_input": 0}),
        row("tiny", "c", False, status="stopped"),
    ]
    router, tiny = summarize(rows, price_in=2.0, price_out=8.0, resamples=2_000)
    assert (router.contestant, tiny.contestant) == ("router", "tiny")
    assert (router.runs, router.tasks, router.passed) == (3, 3, 2)
    assert router.pass_rate == pytest.approx(2 / 3)
    assert router.ci.low <= router.pass_rate <= router.ci.high
    assert router.median_wall_seconds == 18.0
    assert router.median_tokens == 1100.0
    assert (router.input_tokens, router.output_tokens, router.cached_input_tokens) == (
        3000,
        300,
        1200,
    )
    assert router.gpu_seconds == 6.0
    assert router.escalation_rate == pytest.approx(1 / 3)
    assert router.cache_hit_rate == pytest.approx(0.4)
    # (3000 * 2 + 300 * 8) / 1e6
    assert router.reference_cost == pytest.approx(0.0084)
    assert tiny.statuses == {"done": 1, "failed": 1, "stopped": 1}
    assert tiny.cache_hit_rate == pytest.approx(800 / 5000)
    assert summarize(rows)[0].reference_cost is None  # no price, no cost
    assert router.to_dict()["ci"]["method"] == BOOTSTRAP


def test_ratio_bootstrap_resamples_tasks_with_their_totals() -> None:
    # two tasks: 50 of 100 and 150 of 100; resamples give 0.5, 1.0 or 1.5 (a quarter, a half,
    # a quarter), so a 40% interval is the middle value and the 95% interval spans the ends
    clusters = [(50.0, 100.0), (150.0, 100.0)]
    middle = bootstrap_ratio_ci(clusters, confidence=0.4, resamples=8_000, seed=2)
    assert middle.low == middle.high == 1.0 and middle.method.startswith("cluster bootstrap")
    wide = bootstrap_ratio_ci(clusters, resamples=8_000, seed=2)
    assert (wide.low, wide.high) == (0.5, 1.5)
    assert bootstrap_ratio_ci(clusters, seed=5) == bootstrap_ratio_ci(clusters, seed=5)
    single = bootstrap_ratio_ci([(30.0, 40.0)])
    assert (single.low, single.high, single.method) == (0.75, 0.75, "none")
    assert math.isnan(bootstrap_ratio_ci([]).low)
