"""Simulation harness for the learning router (SPEC §10; claim C-015, ADR 0026).

A synthetic world has four workers, one per ladder rung (tiers t0 to t3), and tasks whose
contexts vary in track, difficulty, task type, language and context size. `random_world(seed)`
draws one:

* each worker has a skill (stronger rungs higher), a bonus or malus per track (one model is
  better at science code than its rung suggests, another worse) and a small effect per
  (worker, context) pair; its success probability on a context is a logistic function of skill
  minus the task's hardness, plus those effects;
* time and tokens grow with the rung (30 s and 20K tokens on t0, three and two times more per
  rung), so the cheaper worker earns the higher reward where it is about as likely to succeed;
* the registry prior of a worker is its true mean success rate plus noise, and the router's
  cold start applies `policy.prior` to it, as in production.

A task's outcome is a Bernoulli draw and its reward SPEC §10's (`learning.reward`). Policies:

* `thompson`: the learning router (`learning.Bandit.choose`), from the cold-start priors and
  the workers' known costs (in production, calibrated throughput);
* `static`: the router without learning, the worker with the best expected reward under the
  cold-start priors;
* `fixed`: the single worker with the best expected reward over all contexts, knowing the world
  (the best policy that ignores the context);
* `random`: a uniformly random worker;
* `oracle`: the best worker for each context (zero regret, for reference).

The regret of a task is the expected reward of the context's best worker minus that of the
chosen one. Every policy sees the same tasks and the same outcome draws.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from stepswitch.router import learning
from stepswitch.router.features import TaskFeatures
from stepswitch.router.policy import prior
from stepswitch.workers.base import WorkerSpec

POLICIES = ("thompson", "static", "fixed", "random", "oracle")
TIERS = ("t0", "t1", "t2", "t3")
TRACKS = ("general", "science")
TASK_TYPES = ("bugfix", "feature", "refactor")
LANGUAGES = ("python", "typescript")
CONTEXT_TOKENS = (4_000, 16_000, 48_000)


@dataclass(frozen=True)
class World:
    workers: tuple[WorkerSpec, ...]
    contexts: tuple[TaskFeatures, ...]
    # success[w][c]: true success probability of worker w on context c.
    success: tuple[tuple[float, ...], ...]
    seconds: tuple[float, ...]
    tokens: tuple[int, ...]
    registry: tuple[float, ...]
    settings: learning.LearningSettings = field(default_factory=learning.LearningSettings)

    def expected(self, worker: int, context: int) -> float:
        """Expected reward of `worker` on `context`."""
        cost = learning.reward(True, self.seconds[worker], self.tokens[worker], self.settings)
        return self.success[worker][context] - (1.0 - cost)

    def best(self, context: int) -> int:
        return max(range(len(self.workers)), key=lambda w: (self.expected(w, context), -w))

    def cold_start(self, context: int) -> dict[str, float]:
        features = self.contexts[context]
        return {
            spec.id: prior(spec, "edit", features, base=self.registry[w])
            for w, spec in enumerate(self.workers)
        }

    def costs(self) -> dict[str, tuple[float, float]]:
        """Each worker's normalized time and tokens per task."""
        return {
            spec.id: learning.normalized_costs(self.seconds[w], self.tokens[w], self.settings)
            for w, spec in enumerate(self.workers)
        }


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def random_world(seed: int, settings: learning.LearningSettings | None = None) -> World:
    """A world drawn from `seed` (see the module docstring)."""
    rng = random.Random(f"stepswitch-world:{seed}")
    workers = tuple(
        WorkerSpec(id=f"sim/{tier}", endpoint="sim", model=f"model-{tier}", tier=tier)
        for tier in TIERS
    )
    skills = sorted(rng.uniform(-1.2, 1.8) for _ in workers)
    track_bonus = [{track: rng.gauss(0.0, 0.7) for track in TRACKS} for _ in workers]
    contexts = tuple(
        TaskFeatures(
            task_type=task_type,
            difficulty=difficulty,
            language=language,
            est_context_tokens=tokens,
            track=track,
        )
        for track in TRACKS
        for difficulty in range(1, 6)
        for task_type in TASK_TYPES
        for language in LANGUAGES
        for tokens in CONTEXT_TOKENS
    )
    type_hardness = {task_type: rng.gauss(0.0, 0.3) for task_type in TASK_TYPES}
    pair_effects: dict[tuple[int, str], float] = {}
    success = []
    for w in range(len(workers)):
        row = []
        for context in contexts:
            key = learning.context_keys(context)[2]
            effect = pair_effects.setdefault((w, key), rng.gauss(0.0, 0.3))
            hardness = 0.8 * (context.difficulty - 3) + type_hardness[context.task_type]
            hardness += 0.3 * CONTEXT_TOKENS.index(context.est_context_tokens)
            logit = 1.6 * (skills[w] - hardness) + track_bonus[w][context.track] + effect
            row.append(round(min(0.98, max(0.02, _sigmoid(logit))), 4))
        success.append(tuple(row))
    seconds = tuple(30.0 * 3**w for w in range(len(workers)))
    tokens = tuple(20_000 * 2**w for w in range(len(workers)))
    registry = tuple(
        round(min(0.95, max(0.05, sum(row) / len(row) + rng.gauss(0.0, 0.08))), 4)
        for row in success
    )
    return World(
        workers,
        contexts,
        tuple(success),
        seconds,
        tokens,
        registry,
        settings or learning.LearningSettings(),
    )


@dataclass
class Result:
    policy: str
    regret: list[float]
    chosen: list[int]

    @property
    def total(self) -> float:
        return round(sum(self.regret), 4)

    def window(self, start: float, end: float) -> float:
        """Mean regret per task over a share of the run, e.g. the last quarter (0.75, 1)."""
        low, high = int(len(self.regret) * start), int(len(self.regret) * end)
        part = self.regret[low:high]
        return round(sum(part) / len(part), 5) if part else 0.0

    def as_dict(self) -> dict[str, Any]:
        quarters = [self.window(i / 4, (i + 1) / 4) for i in range(4)]
        return {"policy": self.policy, "total_regret": self.total, "regret_per_quarter": quarters}


def simulate(policy: str, world: World, rounds: int, seed: int) -> Result:
    """Route `rounds` tasks of `world` with `policy`; the task sequence depends on `seed` only."""
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r} (policies: {', '.join(POLICIES)})")
    tasks = random.Random(f"stepswitch-tasks:{seed}")
    choices = random.Random(f"stepswitch-choices:{seed}:{policy}")
    bandit = learning.Bandit()
    ids = [spec.id for spec in world.workers]
    count = len(ids)
    means = [
        sum(world.expected(w, c) for c in range(len(world.contexts))) / len(world.contexts)
        for w in range(count)
    ]
    fixed = max(range(count), key=lambda w: (means[w], -w))
    costs = world.costs()
    weights = world.settings.lambda_time, world.settings.mu_tokens
    expected_cost = {k: weights[0] * t + weights[1] * n for k, (t, n) in costs.items()}
    regret: list[float] = []
    chosen: list[int] = []
    for _ in range(rounds):
        context = tasks.randrange(len(world.contexts))
        draw = tasks.random()
        keys = learning.context_keys(world.contexts[context])
        if policy == "thompson":
            worker = bandit.choose(world.cold_start(context), costs, keys, world.settings, choices)
            pick = ids.index(worker)
        elif policy == "static":
            start = world.cold_start(context)
            pick = max(range(count), key=lambda w: (start[ids[w]] - expected_cost[ids[w]], -w))
        elif policy == "fixed":
            pick = fixed
        elif policy == "random":
            pick = choices.randrange(count)
        else:
            pick = world.best(context)
        success = draw < world.success[pick][context]
        if policy == "thompson":
            bandit.update(
                {ids[pick]: 1.0},
                keys,
                success,
                world.seconds[pick],
                world.tokens[pick],
                world.settings,
            )
        best = world.best(context)
        regret.append(round(world.expected(best, context) - world.expected(pick, context), 6))
        chosen.append(pick)
    return Result(policy, regret, chosen)


def compare(
    worlds: Sequence[int], rounds: int, policies: Sequence[str] = POLICIES
) -> dict[str, list[Result]]:
    """Every policy on every world, each world with its own task sequence."""
    return {
        name: [simulate(name, random_world(seed), rounds, seed) for seed in worlds]
        for name in policies
    }
