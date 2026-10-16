"""Step-level routing: `route()` (SPEC §7.1, §7.2, ADR 0005, ADR 0015)."""

from __future__ import annotations

import pytest

from stepswitch.router.cost import estimate, should_switch
from stepswitch.router.features import TaskFeatures
from stepswitch.router.policy import prior
from stepswitch.router.router import RouterState, route
from stepswitch.router.rules import parse_rules
from stepswitch.workers.base import WorkerSpec

T0 = WorkerSpec(id="managed/tiny", endpoint="managed", model="tiny", tier="cpu", roles=("router",))
MID = WorkerSpec(id="gpu/mid", endpoint="gpu", model="mid", tier="t2", roles=("editor",))
BIG = WorkerSpec(id="gpu/big", endpoint="gpu", model="big", tier="t3", roles=("planner",))
REMOTE = WorkerSpec(id="far/huge", endpoint="far", model="huge", tier="remote", local=False)
WORKERS = [T0, MID, BIG, REMOTE]
FEATURES = TaskFeatures()


def chosen(step_type: str, workers: list[WorkerSpec] = WORKERS, **state: object) -> str | None:
    decision = route(FEATURES, step_type, RouterState(step=1, **state), workers)  # type: ignore[arg-type]
    return decision.chosen


def test_rung_is_a_ceiling_and_a_floor_for_heavy_steps() -> None:
    assert chosen("edit", level=0) == T0.id
    assert chosen("edit", level=1) == MID.id
    assert chosen("edit", level=2) == BIG.id
    # light steps may go below the rung; with equal priors the faster big model still wins here
    decision = route(FEATURES, "navigate", RouterState(step=1, level=1), WORKERS)
    excluded = {c.worker: c.excluded for c in decision.candidates}
    assert excluded[T0.id] is None
    assert excluded[BIG.id] is not None and "above the current rung" in excluded[BIG.id]


def test_remote_is_opt_in_and_offline_is_local_only() -> None:
    assert chosen("edit", level=3) == BIG.id  # remote excluded: nearest rung with a worker
    assert chosen("edit", level=3, allow_remote=True) == REMOTE.id
    assert chosen("edit", level=3, allow_remote=True, offline=True) == BIG.id


def test_relaxation_is_recorded_when_the_rung_is_empty() -> None:
    decision = route(FEATURES, "edit", RouterState(step=1, level=0), [MID, BIG])
    assert decision.chosen == MID.id
    assert any("using rung" in note for note in decision.notes)
    assert [c.relaxed for c in decision.candidates] == [True, False]


def test_science_track_never_gives_code_steps_to_t0() -> None:
    assert chosen("edit", [T0], track="science") is None
    decision = route(FEATURES, "edit", RouterState(step=1, track="science"), [T0])
    assert "T0 models never edit code" in decision.reason
    # text steps are fine
    assert chosen("summarize", [T0], track="science") == T0.id


def test_worker_pin_overrides_the_ladder_but_not_hard_rules() -> None:
    assert chosen("edit", level=0, worker_pin=BIG.id) == BIG.id
    assert chosen("edit", level=0, worker_pin=REMOTE.id) == REMOTE.id  # a pin opts in
    assert chosen("edit", level=0, worker_pin=REMOTE.id, offline=True) is None
    assert chosen("edit", [T0], worker_pin=T0.id, track="science") is None


def test_failed_workers_and_small_contexts_are_excluded() -> None:
    assert chosen("edit", level=1, failed=frozenset({MID.id})) == BIG.id
    small = WorkerSpec(id="gpu/small-ctx", endpoint="gpu", model="s", tier="t2", context=4096)
    decision = route(
        FEATURES, "edit", RouterState(step=1, level=1, transcript_tokens=5000), [small]
    )
    assert decision.chosen is None
    assert "too small" in decision.reason


def test_rules_pin_exclude_and_prefer() -> None:
    rules = parse_rules(
        [
            {"name": "plan-big", "when": {"step_type": "plan"}, "to": {"model": "big"}},
            {"name": "no-mid", "when": {"step_type": "review"}, "exclude": {"model": "mid"}},
            {
                "name": "likes-tiny",
                "when": {"step_type": "navigate"},
                "prefer": {"tier": "cpu"},
                "bonus": 0.5,
            },
        ]
    )
    plan = route(FEATURES, "plan", RouterState(step=1, level=0), WORKERS, rules)
    assert (plan.chosen, plan.rule) == (BIG.id, "plan-big")  # a pin beats the ladder ceiling
    review = route(FEATURES, "review", RouterState(step=1, level=1), WORKERS, rules)
    # mid is excluded, tiny is below the rung for reviews: the next rung up takes it
    assert review.chosen == BIG.id
    navigate = route(FEATURES, "navigate", RouterState(step=1, level=1), WORKERS, rules)
    assert navigate.chosen == T0.id  # the bonus outweighs the higher prior of the bigger model


def test_a_pinning_rule_that_matches_nothing_is_skipped_with_a_note() -> None:
    rules = parse_rules([{"name": "ghost", "when": {}, "to": {"model": "nope"}}])
    decision = route(FEATURES, "edit", RouterState(step=1, level=1), WORKERS, rules)
    assert decision.chosen == MID.id
    assert decision.rule is None
    assert any("ghost" in note for note in decision.notes)


def test_switching_follows_the_cost_rule() -> None:
    # Staying is preferred while the gain does not cover the re-reading cost plus the margin.
    a = WorkerSpec(id="x/a", endpoint="x", model="a", tier="t2", prefill_tps=100.0)
    b = WorkerSpec(
        id="y/b", endpoint="y", model="b", tier="t2", roles=("editor",), prefill_tps=100.0
    )
    state = RouterState(
        step=5, level=1, current=a.id, transcript_tokens=20_000, seen_tokens={a.id: 20_000}
    )
    decision = route(FEATURES, "edit", state, [a, b])
    # b's prior is 0.05 higher (role match): 30 s of value, but 200 s to re-read 20k tokens
    assert decision.chosen == a.id and not decision.switched
    assert "stay on x/a" in decision.reason
    cheap = RouterState(
        step=5, level=1, current=a.id, transcript_tokens=100, seen_tokens={a.id: 100}
    )
    big_gain = WorkerSpec(id="z/c", endpoint="z", model="c", tier="t3", prefill_tps=100.0)
    switched = route(FEATURES, "edit", RouterState(**{**cheap.__dict__, "level": 2}), [a, big_gain])
    # after an escalation the previous worker is below the rung for edits: it must hand over
    assert switched.chosen == big_gain.id and switched.switched
    assert switched.switch_cost is not None and "below the current rung" in switched.reason


def test_switch_records_reason_gain_and_cost() -> None:
    a = WorkerSpec(id="x/a", endpoint="x", model="a", tier="t2")
    b = WorkerSpec(id="y/b", endpoint="y", model="b", tier="t2", roles=("planner",))
    rules = parse_rules([{"name": "plan-b", "when": {"step_type": "plan"}, "to": {"id": "y/b"}}])
    state = RouterState(
        step=3, level=1, current=a.id, transcript_tokens=3000, seen_tokens={a.id: 3000}
    )
    decision = route(FEATURES, "plan", state, [a, b], rules)
    assert decision.switched and decision.chosen == b.id
    assert decision.reason.startswith("switch x/a -> y/b")
    assert decision.switch_cost is not None
    assert decision.switch_cost["tokens"] == 3000.0
    assert decision.switch_cost["seconds"] > 0
    assert isinstance(decision.expected_gain, float)


def test_pinned_transcript_stays_on_the_current_worker() -> None:
    a = WorkerSpec(id="x/a", endpoint="x", model="a", tier="t2")
    b = WorkerSpec(id="y/b", endpoint="y", model="b", tier="t3")
    state = RouterState(step=2, level=2, current=a.id, pinned=True)
    # a is below the rung for edit steps, so it is not eligible: the pin cannot hold it
    assert route(FEATURES, "edit", state, [a, b]).chosen == b.id
    light = route(FEATURES, "navigate", state, [a, b])
    assert light.chosen == a.id and "only its endpoint holds" in light.reason


def test_decisions_are_deterministic_and_record_every_candidate() -> None:
    state = RouterState(step=1, level=1)
    first = route(FEATURES, "edit", state, WORKERS, seed=7)
    second = route(FEATURES, "edit", state, list(reversed(WORKERS)), seed=7)
    assert first.chosen == second.chosen
    assert first.seed == 7
    assert {c.worker for c in first.candidates} == {w.id for w in WORKERS}
    for cand in first.candidates:
        assert cand.prior is not None and cand.estimate is not None
        assert cand.excluded is not None or cand.score is not None


def test_ties_break_by_worker_id() -> None:
    twins = [
        WorkerSpec(id="b/m", endpoint="b", model="m", tier="t2"),
        WorkerSpec(id="a/m", endpoint="a", model="m", tier="t2"),
    ]
    assert chosen("edit", twins, level=1) == "a/m"


@pytest.mark.parametrize(
    ("gain", "cost", "margin", "expected"),
    [(100.0, 20.0, 30.0, True), (40.0, 20.0, 30.0, False), (50.0, 20.0, 30.0, False)],
)
def test_should_switch(gain: float, cost: float, margin: float, expected: bool) -> None:
    assert should_switch(gain, cost, margin) is expected


def test_estimates_use_calibrated_throughput_and_count_uncached_tokens() -> None:
    calibrated = WorkerSpec(id="c/m", tier="t2", prefill_tps=1000.0, decode_tps=50.0)
    est = estimate(calibrated, input_tokens=4000, output_tokens=100, uncached_tokens=1000)
    assert est.switch_tokens == 1000
    assert est.switch_seconds == pytest.approx(1.0)
    assert est.seconds == pytest.approx(1.0 + 2.0)
    assert est.tokens == 4100


def test_priors_order_rungs_and_reward_matching_roles() -> None:
    assert prior(T0, "edit", FEATURES) < prior(MID, "edit", FEATURES) < prior(BIG, "edit", FEATURES)
    assert prior(MID, "edit", FEATURES) > prior(WorkerSpec(id="n/r", tier="t2"), "edit", FEATURES)
    hard = TaskFeatures(difficulty=5)
    assert prior(T0, "edit", hard) < prior(T0, "edit", FEATURES)
    fallback = WorkerSpec(id="f/b", tier="t2", tool_protocol="fallback")
    assert prior(fallback, "edit", FEATURES) < prior(
        WorkerSpec(id="n/a", tier="t2"), "edit", FEATURES
    )
