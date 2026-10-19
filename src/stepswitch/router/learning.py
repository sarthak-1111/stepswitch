"""The learning router (SPEC §10, ADR 0026): contextual Thompson sampling over workers.

Arms are workers; a task's context is what the classifier and the repository say about it, at
five levels from coarse to fine (`context_keys`):

    *                                    every task
    <track>                              general or science
    <track>|<band>                       difficulty band: easy (1-2), medium (3), hard (4-5)
    <track>|<band>|d<difficulty>         the classifier's difficulty, 1 to 5
    <track>|<band>|d<n>|<task type>|<language>|<context band>

The objective is SPEC §10's reward, success - lambda * normalized time - mu * normalized tokens
(`reward`; time and tokens divided by `time_scale_seconds` and `token_scale`, capped at 1). Of
its terms, success is the uncertain one, so the router keeps a Beta posterior of each worker's
success per context key, and running means of its normalized time and tokens per task:

* **Cold start.** A worker's success prior is the static prior of `policy.prior` for edit steps
  (registry and tier, probe results through the tool protocol, bench-measured base rates),
  worth `prior_strength` pseudo-tasks; its cost prior is the caller's estimate (in production,
  the cost model from calibrated throughput), worth one task.
* **Backoff.** Each level's statistics are added to those of the level above it, which is first
  scaled down to at most `backoff_cap` pseudo-tasks. A context with little data borrows from
  coarser ones; one with much data stands on its own.
* **Sampling.** Per task, one success sample per worker is drawn from the task's seed. The
  task-level policy of the simulation picks the worker whose sample, minus its expected costs,
  is highest. The production router uses the sample instead of the static task-level prior in
  its own cost-aware score (`RouterState.learned`), which already weighs time per step.
* **Update.** A finished task adds its outcome (success means the task ended `done`) and its
  normalized costs at every level, for each worker in proportion to the work steps it took.

Settings live in `[router.learning]`; the statistics live in the project's state database
(`router_params`, key "bandit") and never leave the machine. Only `ssw router stats --export`
writes aggregates to a file, and only when asked.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from stepswitch.router.features import TaskFeatures

BANDIT_KEY = "bandit"
LEVELS = ("all", "track", "band", "difficulty", "context")
MODES = ("on", "frozen", "off")
# Index of each statistic in a context entry.
SUCCESS, FAILURE, COST_WEIGHT, TIME_SUM, TOKEN_SUM = range(5)


@dataclass(frozen=True)
class LearningSettings:
    """`[router.learning]`. `mode`: on (sample and learn), frozen (sample from what was learned,
    learn nothing) or off (the static priors); benches run with it off (ADR 0026)."""

    mode: str = "on"
    lambda_time: float = 0.1
    mu_tokens: float = 0.1
    time_scale_seconds: float = 600.0
    token_scale: int = 200_000
    prior_strength: float = 8.0
    backoff_cap: float = 15.0

    @property
    def learns(self) -> bool:
        return self.mode == "on"

    @property
    def samples(self) -> bool:
        return self.mode in ("on", "frozen")

    @classmethod
    def from_config(cls, config: Any) -> LearningSettings:
        extra = getattr(config, "model_extra", None) or {}
        router = extra.get("router") if isinstance(extra, Mapping) else None
        table = router.get("learning") if isinstance(router, Mapping) else None
        if not isinstance(table, Mapping):
            return cls()
        values: dict[str, Any] = {}
        mode, enabled = table.get("mode"), table.get("enabled")
        if isinstance(mode, str) and mode in MODES:
            values["mode"] = mode
        elif isinstance(enabled, bool):
            values["mode"] = "on" if enabled else "off"
        for name in ("lambda_time", "mu_tokens", "prior_strength", "backoff_cap"):
            value = table.get(name)
            if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
                values[name] = float(value)
        scale = table.get("time_scale_seconds")
        if isinstance(scale, int | float) and not isinstance(scale, bool) and scale > 0:
            values["time_scale_seconds"] = float(scale)
        tokens = table.get("token_scale")
        if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
            values["token_scale"] = tokens
        return cls(**values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "lambda_time": self.lambda_time,
            "mu_tokens": self.mu_tokens,
            "time_scale_seconds": self.time_scale_seconds,
            "token_scale": self.token_scale,
            "prior_strength": self.prior_strength,
            "backoff_cap": self.backoff_cap,
        }


def normalized_costs(
    seconds: float, tokens: int, settings: LearningSettings
) -> tuple[float, float]:
    return (
        min(1.0, max(0.0, seconds) / settings.time_scale_seconds),
        min(1.0, max(0, tokens) / settings.token_scale),
    )


def reward(success: bool, seconds: float, tokens: int, settings: LearningSettings) -> float:
    """SPEC §10: success - lambda * normalized time - mu * normalized tokens."""
    time_cost, token_cost = normalized_costs(seconds, tokens, settings)
    value = (1.0 if success else 0.0) - settings.lambda_time * time_cost
    return round(value - settings.mu_tokens * token_cost, 6)


def difficulty_band(difficulty: int) -> str:
    return "easy" if difficulty <= 2 else ("medium" if difficulty == 3 else "hard")


def context_band(tokens: int) -> str:
    return "small" if tokens < 8_000 else ("medium" if tokens < 32_000 else "large")


def context_keys(features: TaskFeatures) -> tuple[str, str, str, str, str]:
    """The task's key at each level of `LEVELS`, coarse to fine."""
    band = f"{features.track}|{difficulty_band(features.difficulty)}"
    exact = f"{band}|d{features.difficulty}"
    language = (features.language or "unknown").lower()
    fine = f"{exact}|{features.task_type}|{language}|{context_band(features.est_context_tokens)}"
    return ("*", features.track, band, exact, fine)


def level_of(key: str) -> str:
    """The level of `LEVELS` a context key belongs to."""
    if key == "*":
        return "all"
    return {0: "track", 1: "band", 2: "difficulty"}.get(key.count("|"), "context")


@dataclass(frozen=True)
class Belief:
    """What the router believes about one worker in one context."""

    alpha: float
    beta: float
    time_cost: float
    token_cost: float

    @property
    def success(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def value(self, success: float, settings: LearningSettings) -> float:
        """The reward expected with this success probability."""
        costs = settings.lambda_time * self.time_cost + settings.mu_tokens * self.token_cost
        return success - costs


@dataclass
class Bandit:
    """Per worker and context key: [successes, failures, cost weight, time sum, token sum]."""

    stats: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    tasks: int = 0

    def belief(
        self,
        worker: str,
        keys: Sequence[str],
        prior_success: float,
        settings: LearningSettings,
        prior_costs: tuple[float, float] = (0.0, 0.0),
    ) -> Belief:
        mean = min(0.99, max(0.01, prior_success))
        alpha = mean * settings.prior_strength
        beta = (1.0 - mean) * settings.prior_strength
        weight, time_sum, token_sum = 1.0, prior_costs[0], prior_costs[1]
        table = self.stats.get(worker, {})
        for depth, key in enumerate(keys):
            total = alpha + beta
            if depth and total > settings.backoff_cap > 0:
                scale = settings.backoff_cap / total
                alpha, beta = alpha * scale, beta * scale
            if depth and weight > settings.backoff_cap > 0:
                scale = settings.backoff_cap / weight
                weight, time_sum, token_sum = weight * scale, time_sum * scale, token_sum * scale
            entry = table.get(key)
            if entry:
                alpha += entry[SUCCESS]
                beta += entry[FAILURE]
                weight += entry[COST_WEIGHT]
                time_sum += entry[TIME_SUM]
                token_sum += entry[TOKEN_SUM]
        return Belief(max(alpha, 1e-3), max(beta, 1e-3), time_sum / weight, token_sum / weight)

    def sample(
        self,
        priors: Mapping[str, float],
        keys: Sequence[str],
        settings: LearningSettings,
        rng: random.Random,
    ) -> dict[str, float]:
        """One Thompson sample of each worker's success (in id order, so a seed repeats)."""
        drawn = {}
        for worker, p in sorted(priors.items()):
            belief = self.belief(worker, keys, p, settings)
            drawn[worker] = round(rng.betavariate(belief.alpha, belief.beta), 6)
        return drawn

    def choose(
        self,
        priors: Mapping[str, float],
        costs: Mapping[str, tuple[float, float]],
        keys: Sequence[str],
        settings: LearningSettings,
        rng: random.Random,
    ) -> str:
        """The task-level choice: the highest sampled success minus the expected costs."""
        best: tuple[float, str] | None = None
        for worker, p in sorted(priors.items()):
            belief = self.belief(worker, keys, p, settings, costs.get(worker, (0.0, 0.0)))
            value = belief.value(rng.betavariate(belief.alpha, belief.beta), settings)
            if best is None or value > best[0]:
                best = (value, worker)
        assert best is not None, "no workers"
        return best[1]

    def update(
        self,
        shares: Mapping[str, float],
        keys: Iterable[str],
        success: bool,
        seconds: float,
        tokens: int,
        settings: LearningSettings,
    ) -> None:
        """Add one finished task for the workers that did its work, by their shares."""
        levels = list(keys)
        total = sum(max(0.0, s) for s in shares.values())
        if total <= 0 or not levels:
            return
        time_cost, token_cost = normalized_costs(seconds, tokens, settings)
        for worker, share in shares.items():
            weight = max(0.0, share) / total
            if weight <= 0:
                continue
            table = self.stats.setdefault(worker, {})
            for key in levels:
                entry = table.setdefault(key, [0.0] * 5)
                entry[SUCCESS if success else FAILURE] += weight
                entry[COST_WEIGHT] += weight
                entry[TIME_SUM] += weight * time_cost
                entry[TOKEN_SUM] += weight * token_cost
                table[key] = [round(v, 6) for v in entry]
        self.tasks += 1

    def as_dict(self) -> dict[str, Any]:
        return {"stats": self.stats, "tasks": self.tasks}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> Bandit:
        if not data:
            return cls()
        stats: dict[str, dict[str, list[float]]] = {}
        for worker, table in dict(data.get("stats", {})).items():
            if not isinstance(table, Mapping):
                continue
            stats[str(worker)] = {
                str(key): [float(v) for v in value]
                for key, value in table.items()
                if isinstance(value, Sequence) and len(value) == 5
            }
        return cls(stats, int(data.get("tasks", 0)))

    def summary(self) -> list[dict[str, Any]]:
        """Per worker and context key: tasks counted, success rate and mean costs."""
        rows = []
        for worker in sorted(self.stats):
            for key, entry in sorted(self.stats[worker].items()):
                outcomes = entry[SUCCESS] + entry[FAILURE]
                weight = entry[COST_WEIGHT]
                rows.append(
                    {
                        "worker": worker,
                        "level": level_of(key),
                        "context": key,
                        "tasks": round(outcomes, 3),
                        "success_rate": round(entry[SUCCESS] / outcomes, 4) if outcomes else None,
                        "time_cost": round(entry[TIME_SUM] / weight, 4) if weight else None,
                        "token_cost": round(entry[TOKEN_SUM] / weight, 4) if weight else None,
                    }
                )
        return rows


def work_shares(steps: Mapping[str, int]) -> dict[str, float]:
    """Each worker's share of a task's work steps."""
    total = sum(max(0, n) for n in steps.values())
    if total <= 0:
        return {}
    return {worker: n / total for worker, n in steps.items() if n > 0}


def task_rng(seed: int, task_id: str) -> random.Random:
    """The sampling generator of one task, from its seed and id (string seeds are hashed
    deterministically by `random`)."""
    return random.Random(f"stepswitch-bandit:{seed}:{task_id}")


# Levels an export may contain: the finest one names languages and task types of single tasks.
EXPORT_LEVELS = ("all", "track", "band", "difficulty")


def export_aggregates(bandit: Bandit, settings: LearningSettings) -> dict[str, Any]:
    """Anonymized aggregates for `ssw router stats --export`: per model (the worker id without
    its endpoint name, which may name a machine) and coarse context level, the tasks counted,
    success rate and costs."""
    merged: dict[tuple[str, str], list[float]] = {}
    for worker, table in bandit.stats.items():
        name = worker.partition("/")[2] or worker
        for key, entry in table.items():
            if level_of(key) not in EXPORT_LEVELS:
                continue
            total = merged.setdefault((name, key), [0.0] * 5)
            for i, value in enumerate(entry):
                total[i] += value
    rows = []
    for (model, key), entry in sorted(merged.items()):
        outcomes = entry[SUCCESS] + entry[FAILURE]
        weight = entry[COST_WEIGHT]
        rows.append(
            {
                "model": model,
                "level": level_of(key),
                "context": key,
                "tasks": round(outcomes, 3),
                "success_rate": round(entry[SUCCESS] / outcomes, 4) if outcomes else None,
                "time_cost": round(entry[TIME_SUM] / weight, 4) if weight else None,
                "token_cost": round(entry[TOKEN_SUM] / weight, 4) if weight else None,
            }
        )
    return {
        "schema": 1,
        "tasks": bandit.tasks,
        "settings": {k: v for k, v in settings.as_dict().items() if k != "mode"},
        "rows": rows,
    }
