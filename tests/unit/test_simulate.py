"""The learning router's simulation harness (SPEC §10; claim C-015)."""

from __future__ import annotations

import pytest

from stepswitch.router import simulate
from stepswitch.router.simulate import random_world


def test_simulate_worlds_are_reproducible_and_well_formed() -> None:
    world = random_world(3)
    assert world == random_world(3) and world != random_world(4)
    assert [spec.tier for spec in world.workers] == list(simulate.TIERS)
    assert len(world.contexts) == 2 * 5 * 3 * 2 * 3
    assert all(0.02 <= p <= 0.98 for row in world.success for p in row)
    assert list(world.seconds) == sorted(world.seconds) and list(world.tokens) == sorted(
        world.tokens
    )
    assert all(0.05 <= p <= 0.95 for p in world.registry)
    # the best worker differs between contexts in most worlds
    varied = sum(
        len({w.best(c) for c in range(len(w.contexts))}) > 1
        for w in (random_world(seed) for seed in range(10))
    )
    assert varied >= 8


def test_simulate_regret_is_zero_for_the_oracle_and_shared_tasks() -> None:
    world = random_world(0)
    oracle = simulate.simulate("oracle", world, 300, seed=1)
    assert oracle.total == 0 and oracle.as_dict()["regret_per_quarter"] == [0.0] * 4
    fixed = simulate.simulate("fixed", world, 300, seed=1)
    assert len(set(fixed.chosen)) == 1 and fixed.total > 0
    again = simulate.simulate("random", world, 300, seed=1)
    assert again.chosen == simulate.simulate("random", world, 300, seed=1).chosen
    assert all(r >= -1e-9 for r in again.regret)
    with pytest.raises(ValueError, match="unknown policy"):
        simulate.simulate("greedy", world, 10, seed=0)


def test_simulate_learning_lowers_regret_over_time() -> None:
    results = simulate.compare(range(6), 1500, ("thompson", "random"))
    for learner, random_policy in zip(results["thompson"], results["random"], strict=True):
        assert learner.total < random_policy.total
    first = sum(r.window(0, 0.25) for r in results["thompson"])
    last = sum(r.window(0.75, 1) for r in results["thompson"])
    assert last < first
