"""The learning router: reward, context keys, the Beta posterior with backoff (SPEC §10)."""

from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stepswitch.router import learning
from stepswitch.router.features import TaskFeatures
from stepswitch.router.learning import Bandit, LearningSettings, context_keys, reward

SETTINGS = LearningSettings()
EASY = TaskFeatures(difficulty=1, task_type="bugfix", language="Python", est_context_tokens=3000)
HARD = TaskFeatures(difficulty=5, task_type="feature", track="science", est_context_tokens=40000)


def test_learning_router_reward_is_the_spec_formula() -> None:
    assert reward(True, 0.0, 0, SETTINGS) == 1.0
    assert reward(False, 0.0, 0, SETTINGS) == 0.0
    # half the time scale and a quarter of the token scale
    assert reward(True, 300.0, 50_000, SETTINGS) == pytest.approx(1 - 0.1 * 0.5 - 0.1 * 0.25)
    # costs are capped at 1, so the reward stays in [-(lambda + mu), 1]
    assert reward(False, 10**6, 10**9, SETTINGS) == pytest.approx(-0.2)
    custom = LearningSettings(lambda_time=0.5, mu_tokens=0.0, time_scale_seconds=100.0)
    assert reward(True, 50.0, 10**9, custom) == pytest.approx(0.75)


def test_learning_router_context_keys_go_from_coarse_to_fine() -> None:
    keys = context_keys(EASY)
    assert keys == (
        "*",
        "general",
        "general|easy",
        "general|easy|d1",
        "general|easy|d1|bugfix|python|small",
    )
    assert [learning.level_of(k) for k in keys] == list(learning.LEVELS)
    hard = context_keys(HARD)
    assert hard[2:4] == ("science|hard", "science|hard|d5")
    assert hard[4].endswith("|feature|unknown|large")
    assert learning.difficulty_band(3) == "medium"
    assert learning.context_band(8_000) == "medium"


def test_learning_router_settings_come_from_the_config() -> None:
    assert LearningSettings.from_config(None) == SETTINGS
    table = {
        "enabled": False,
        "lambda_time": 0.3,
        "mu_tokens": "lots",
        "time_scale_seconds": 0,
        "token_scale": 50_000,
        "backoff_cap": 4,
    }
    config = SimpleNamespace(model_extra={"router": {"learning": table}})
    settings = LearningSettings.from_config(config)
    assert settings.mode == "off" and not settings.samples and not settings.learns
    assert settings.lambda_time == 0.3 and settings.mu_tokens == SETTINGS.mu_tokens
    assert settings.time_scale_seconds == SETTINGS.time_scale_seconds  # 0 is not a scale
    assert (settings.token_scale, settings.backoff_cap) == (50_000, 4.0)
    frozen = SimpleNamespace(model_extra={"router": {"learning": {"mode": "frozen"}}})
    assert LearningSettings.from_config(frozen).samples
    assert not LearningSettings.from_config(frozen).learns
    assert LearningSettings().as_dict()["mode"] == "on"


def test_learning_router_cold_start_is_the_static_prior() -> None:
    belief = Bandit().belief("w", context_keys(EASY), 0.7, SETTINGS, (0.2, 0.1))
    assert belief.success == pytest.approx(0.7)
    assert belief.alpha + belief.beta == pytest.approx(SETTINGS.prior_strength)
    assert (belief.time_cost, belief.token_cost) == (0.2, 0.1)
    assert belief.value(0.7, SETTINGS) == pytest.approx(0.7 - 0.02 - 0.01)


def test_learning_router_learns_per_context_with_backoff() -> None:
    bandit = Bandit()
    keys = context_keys(EASY)
    for outcome in [True] * 18 + [False] * 2:
        bandit.update({"w": 1.0}, keys, outcome, 60.0, 20_000, SETTINGS)
    assert bandit.tasks == 20
    learned = bandit.belief("w", keys, 0.3, SETTINGS)
    # the data (90%) outweighs the prior (30%), which is capped at `backoff_cap` per level
    assert learned.success > 0.8
    assert learned.time_cost == pytest.approx(0.1, abs=0.02)
    # a sibling context borrows from the shared coarser levels
    sibling = TaskFeatures(difficulty=1, task_type="refactor", est_context_tokens=3000)
    borrowed = bandit.belief("w", context_keys(sibling), 0.3, SETTINGS)
    assert 0.5 < borrowed.success < learned.success
    # another track shares only the top level, and stays nearer its prior
    other = bandit.belief("w", context_keys(HARD), 0.3, SETTINGS)
    assert other.success < borrowed.success
    # a worker without data keeps its prior
    assert bandit.belief("v", keys, 0.3, SETTINGS).success == pytest.approx(0.3)


def test_learning_router_updates_by_work_share() -> None:
    bandit = Bandit()
    keys = context_keys(EASY)
    bandit.update({"a": 3, "b": 1}, keys, True, 0.0, 0, SETTINGS)
    fine = keys[-1]
    assert bandit.stats["a"][fine][learning.SUCCESS] == pytest.approx(0.75)
    assert bandit.stats["b"][fine][learning.SUCCESS] == pytest.approx(0.25)
    bandit.update({"a": 0}, keys, True, 0.0, 0, SETTINGS)  # nobody worked: no update
    assert bandit.tasks == 1
    assert learning.work_shares({"a": 3, "b": 1, "c": 0}) == {"a": 0.75, "b": 0.25}
    assert learning.work_shares({}) == {}


def test_learning_router_state_round_trips_and_summarizes() -> None:
    bandit = Bandit()
    bandit.update({"a": 1.0}, context_keys(EASY), False, 120.0, 40_000, SETTINGS)
    copy = Bandit.from_dict(bandit.as_dict())
    assert copy == bandit
    assert Bandit.from_dict(None) == Bandit()
    assert Bandit.from_dict({"stats": {"a": {"*": [1, 2]}}, "tasks": 1}).stats == {"a": {}}
    rows = bandit.summary()
    assert sorted(row["level"] for row in rows) == sorted(learning.LEVELS)
    top = next(row for row in rows if row["context"] == "*")
    assert (top["tasks"], top["success_rate"], top["time_cost"]) == (1.0, 0.0, 0.2)


def test_learning_router_samples_repeat_with_the_seed_and_choose_weighs_costs() -> None:
    bandit = Bandit()
    keys = context_keys(EASY)
    priors = {"big": 0.8, "small": 0.8}
    first = bandit.sample(priors, keys, SETTINGS, random.Random(4))
    assert first == bandit.sample(priors, keys, SETTINGS, random.Random(4))
    assert set(first) == {"big", "small"} and all(0 < v < 1 for v in first.values())
    assert learning.task_rng(1, "t-1").random() == learning.task_rng(1, "t-1").random()
    assert learning.task_rng(1, "t-1").random() != learning.task_rng(2, "t-1").random()
    # equally likely to succeed after much data: the cheaper worker earns more
    for _ in range(200):
        bandit.update({"big": 1.0}, keys, True, 600.0, 200_000, SETTINGS)
        bandit.update({"small": 1.0}, keys, True, 30.0, 10_000, SETTINGS)
    costs = {"big": (1.0, 1.0), "small": (0.05, 0.05)}
    picks = [
        bandit.choose(priors, costs, keys, SETTINGS, random.Random(seed)) for seed in range(20)
    ]
    assert picks.count("small") >= 18


# --------------------------------------------------------------------------------------------
# Routing with the samples, and learning from finished tasks
# --------------------------------------------------------------------------------------------


def test_learning_router_samples_set_the_task_level_prior() -> None:
    from stepswitch.router.router import RouterState, route
    from stepswitch.workers.base import WorkerSpec

    one = WorkerSpec(id="gpu/one", endpoint="gpu", model="one", tier="t2")
    two = WorkerSpec(id="gpu/two", endpoint="gpu", model="two", tier="t2")
    state = RouterState(step=1, level=1, learned={one.id: 0.3, two.id: 0.9})
    edit = route(TaskFeatures(), "edit", state, [one, two])
    assert edit.chosen == two.id
    by_worker = {c.worker: c for c in edit.candidates}
    assert (by_worker[two.id].learned, by_worker[two.id].prior) == (0.9, 0.9)
    # a light step keeps its adjustment relative to the edit prior
    static = route(TaskFeatures(), "navigate", RouterState(step=1, level=1), [one, two])
    static_edit = route(TaskFeatures(), "edit", RouterState(step=1, level=1), [one, two])
    shift = static.candidates[0].prior - static_edit.candidates[0].prior  # type: ignore[operator]
    light = route(TaskFeatures(), "navigate", state, [one, two])
    assert light.candidates[0].prior == pytest.approx(0.3 + shift)


def test_estimates_of_success_use_the_posterior_mean_not_the_sample() -> None:
    from stepswitch.router.router import RouterState, route
    from stepswitch.workers.base import WorkerSpec

    one = WorkerSpec(id="gpu/one", endpoint="gpu", model="one", tier="t2")
    two = WorkerSpec(id="gpu/two", endpoint="gpu", model="two", tier="t2")
    # the samples rank the workers against their means, as exploring sometimes does
    learned, means = {one.id: 0.9, two.id: 0.3}, {one.id: 0.4, two.id: 0.7}
    state = RouterState(step=1, level=1, learned=learned, means=means)
    edit = route(TaskFeatures(), "edit", state, [one, two])
    assert edit.chosen == one.id  # the score explores with the sample
    by_worker = {c.worker: c for c in edit.candidates}
    assert [by_worker[w].prior for w in (one.id, two.id)] == [0.9, 0.3]
    assert [by_worker[w].expected_success for w in (one.id, two.id)] == [0.4, 0.7]
    # without the learning router, the estimate is the prior
    static = route(TaskFeatures(), "edit", RouterState(step=1, level=1), [one, two])
    assert all(c.expected is None and c.expected_success == c.prior for c in static.candidates)
    # a light step shifts the mean as it shifts the sample
    static_light = route(TaskFeatures(), "navigate", RouterState(step=1, level=1), [one, two])
    light = route(TaskFeatures(), "navigate", state, [one, two])
    for cand, light_prior, edit_prior in zip(
        light.candidates, static_light.candidates, static.candidates, strict=True
    ):
        shift = light_prior.prior - edit_prior.prior  # type: ignore[operator]
        assert cand.expected == pytest.approx(means[cand.worker] + shift)


def _run(tmp_path: Path, name: str, **spec: object) -> tuple[Any, Path]:
    from stepswitch.core.orchestrator import run_task
    from stepswitch.core.task import TaskSpec
    from tests.claims.disagreement_fixture import SEEDED

    task = next(t for t in SEEDED if t.name == "add-numbers")
    repo = task.make(tmp_path / name)
    result = run_task(
        TaskSpec(instruction=task.instruction, repo=repo, max_steps=10, **spec),  # type: ignore[arg-type]
        root=repo,
        catalog=task.catalog(repo),
    )
    return result, repo


def _stored(repo: Path) -> Bandit:
    from stepswitch.router import calibration
    from stepswitch.state import open_project_state

    db = open_project_state(repo)
    try:
        return Bandit.from_dict(calibration.load_param(db, learning.BANDIT_KEY))
    finally:
        db.close()


def test_learning_router_learns_from_a_finished_task(tmp_path: Path) -> None:
    from stepswitch.trajectory.record import read_events

    result, repo = _run(tmp_path, "on")
    assert result.status == "done"
    events = read_events(Path(result.run_dir))
    [sampled] = [e for e in events if e["type"] == "learning"]
    assert sampled["mode"] == "on" and set(sampled["samples"]) == {"fake/alpha", "fake/beta"}
    decisions = [e for e in events if e["type"] == "decision"]
    assert any(c.get("learned") is not None for d in decisions for c in d["candidates"])
    [update] = [e for e in events if e["type"] == "learning_update"]
    [credit] = update["updates"]
    assert credit["success"] is True and abs(sum(credit["workers"].values()) - 1) < 1e-9
    bandit = _stored(repo)
    assert bandit.tasks == 1
    worker = next(iter(credit["workers"]))
    assert bandit.stats[worker]["*"][learning.SUCCESS] > 0


def test_learning_router_frozen_and_off_modes(tmp_path: Path) -> None:
    from stepswitch.trajectory.record import read_events

    frozen, repo = _run(tmp_path, "frozen", learning="frozen")
    types = [e["type"] for e in read_events(Path(frozen.run_dir))]
    assert "learning" in types and "learning_update" not in types
    assert _stored(repo).tasks == 0
    off, repo = _run(tmp_path, "off", learning="off")
    types = [e["type"] for e in read_events(Path(off.run_dir))]
    assert "learning" not in types and "learning_update" not in types
    assert _stored(repo).tasks == 0


def test_learning_router_stats_and_anonymized_export(tmp_path: Path) -> None:
    import json

    from typer.testing import CliRunner

    from stepswitch.cli import app
    from stepswitch.router import calibration
    from stepswitch.state import open_project_state

    bandit = Bandit()
    bandit.update({"my-box/qwen2.5:32b": 1.0}, context_keys(EASY), True, 90.0, 30_000, SETTINGS)
    db = open_project_state(tmp_path)
    calibration.save_param(db, learning.BANDIT_KEY, bandit.as_dict())
    db.close()
    runner = CliRunner()
    shown = runner.invoke(app, ["--project", str(tmp_path), "router", "stats"])
    assert shown.exit_code == 0, shown.output
    assert "1 tasks learned from" in shown.output and "my-box/qwen2.5:32b" in shown.output
    as_json = runner.invoke(
        app, ["--project", str(tmp_path), "router", "stats", "--json", "--level", "band"]
    )
    rows = json.loads(as_json.output)["rows"]
    assert [row["context"] for row in rows] == ["general|easy"]
    target = tmp_path / "aggregates.json"
    exported = runner.invoke(
        app, ["--project", str(tmp_path), "router", "stats", "--export", str(target)]
    )
    assert exported.exit_code == 0, exported.output
    data = json.loads(target.read_text(encoding="utf-8"))
    text = json.dumps(data)
    # no endpoint names and no finest-level contexts (languages, task types of single tasks)
    assert "my-box" not in text and "python" not in text and "bugfix" not in text
    assert {row["model"] for row in data["rows"]} == {"qwen2.5:32b"}
    assert {row["level"] for row in data["rows"]} == {"all", "track", "band", "difficulty"}
    assert data["tasks"] == 1 and "mode" not in data["settings"]
