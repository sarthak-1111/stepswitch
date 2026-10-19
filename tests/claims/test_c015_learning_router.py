"""C-015: the router learns which worker suits which task.

Proof (SPEC §18.5): simulation, cumulative regret below threshold and better than random and
fixed policies. Scope: the learning router's simulation harness with synthetic workers
(SPEC §10).

`stepswitch.router.simulate` draws `worlds` random worlds: four synthetic workers, one per
ladder rung, whose chances of success depend on the task's track, difficulty, type and context
size, and whose time and tokens grow with the rung. In each world every policy routes the same
`rounds` tasks with the same outcome draws. The regret of a task is the expected SPEC §10
reward of the best worker for its context minus that of the chosen worker. The claim holds
when, over the worlds:

* the learning router's cumulative regret is below random routing's in every world, and on
  average at most `regret_vs_random_max` of it;
* its mean cumulative regret is below that of both fixed policies: the router without learning
  (`static`, the cold-start priors) and the single best worker of each world (`fixed`, which
  knows the true rates);
* it learns: its mean regret per task in the last quarter of the run is at most
  `learning_ratio_max` of that in the first quarter.

The evidence lists every world's totals, including the worlds where a fixed policy did better.
"""

from __future__ import annotations

from typing import Any

import pytest

from stepswitch.router import simulate
from tests.claims import evidence

pytestmark = [pytest.mark.claim("C-015")]

POLICIES = ("thompson", "static", "fixed", "random")


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def test_bandit_regret_below_random_and_fixed_policies() -> None:
    limits = evidence.thresholds("C-015")
    worlds, rounds = int(limits["worlds"]), int(limits["rounds"])
    results = simulate.compare(range(worlds), rounds, POLICIES)
    totals = {name: [r.total for r in results[name]] for name in POLICIES}
    curves = {
        name: [mean([r.window(i / 4, (i + 1) / 4) for r in results[name]]) for i in range(4)]
        for name in POLICIES
    }
    learner = totals["thompson"]
    problems: list[str] = []
    beaten_by_random = [
        i for i, (t, r) in enumerate(zip(learner, totals["random"], strict=True)) if t >= r
    ]
    if limits["beats_random"] and beaten_by_random:
        problems.append(f"random routing did as well in worlds {beaten_by_random}")
    ratio = mean(learner) / mean(totals["random"])
    if ratio > limits["regret_vs_random_max"]:
        problems.append(f"regret is {ratio:.2f} of random's (max {limits['regret_vs_random_max']})")
    if limits["beats_fixed"]:
        for name in ("static", "fixed"):
            if mean(learner) >= mean(totals[name]):
                problems.append(f"mean regret {mean(learner)} >= {name} {mean(totals[name])}")
    first, last = curves["thompson"][0], curves["thompson"][3]
    learning_ratio = round(last / first, 4) if first else 1.0
    if learning_ratio > limits["learning_ratio_max"]:
        problems.append(f"last-quarter regret is {learning_ratio} of the first quarter's")
    per_world: list[dict[str, Any]] = [
        {"world": seed, **{name: totals[name][seed] for name in POLICIES}} for seed in range(worlds)
    ]
    evidence.write(
        "C-015",
        "simulation",
        {
            "worlds": worlds,
            "rounds": rounds,
            "workers": [spec.id for spec in simulate.random_world(0).workers],
            "contexts": len(simulate.random_world(0).contexts),
            "mean_total_regret": {name: mean(totals[name]) for name in POLICIES},
            "regret_per_task_by_quarter": curves,
            "regret_vs_random": round(ratio, 4),
            "learning_ratio": learning_ratio,
            "worlds_won": {
                name: sum(1 for t, o in zip(learner, totals[name], strict=True) if t < o)
                for name in POLICIES
                if name != "thompson"
            },
            "per_world": per_world,
            "problems": problems,
        },
        command="uv run ssw prove --claim C-015",
    )
    assert not problems, problems
