"""The cache-aware cost model of SPEC §7.2 (`router.cost`) and how `route()` uses it."""

from __future__ import annotations

import pytest

from stepswitch.router.cost import (
    HANDOFF_THRESHOLD_TOKENS,
    handoff_success,
    switch_terms,
)
from stepswitch.router.features import TaskFeatures
from stepswitch.router.router import RouterState, route
from stepswitch.workers.base import WorkerSpec

SLOW = WorkerSpec(id="slow", tier="t1", prefill_tps=1000.0, decode_tps=10.0)
FAST = WorkerSpec(id="fast", tier="t1", prefill_tps=2000.0, decode_tps=50.0)


def test_handoff_success_recovers_part_of_the_gap_only() -> None:
    assert handoff_success(0.9, 0.5, 0.5) == pytest.approx(0.7)
    assert handoff_success(0.9, 0.5, 0.0) == pytest.approx(0.5)
    assert handoff_success(0.9, 0.5, 2.0) == pytest.approx(0.9)  # recovery is clamped to 1
    # A worker that is not better keeps its own rate.
    assert handoff_success(0.4, 0.5, 0.5) == pytest.approx(0.4)


def test_transcript_switch_pays_for_the_uncached_part() -> None:
    terms = switch_terms(
        SLOW,
        0.6,
        FAST,
        0.6,
        transcript_tokens=10_000,
        output_tokens=100,
        cached_current=9_000,
        cached_new=4_000,
        value_of_success=600.0,
    )
    assert terms.mode == "transcript"
    assert terms.reread_tokens == 6_000
    assert terms.cost_seconds == pytest.approx(6_000 / 2000.0)
    # Staying decodes slowly and reads the 1,000 new tokens; the new worker decodes faster.
    assert terms.gain_seconds == pytest.approx(100 / 10.0 + 1_000 / 1000.0 - 100 / 50.0)


def test_brief_switch_reads_prefix_and_brief_and_pays_the_handoff_penalty() -> None:
    terms = switch_terms(
        SLOW,
        0.5,
        FAST,
        0.9,
        transcript_tokens=60_000,
        output_tokens=100,
        cached_current=60_000,
        cached_new=1_000,
        value_of_success=600.0,
        prefix_tokens=3_000,
        brief_tokens=1_200,
        handoff_seconds=20.0,
        recovery=0.5,
    )
    assert terms.mode == "brief"
    assert terms.reread_tokens == 3_000 - 1_000 + 1_200
    assert terms.cost_seconds == pytest.approx(3_200 / 2000.0 + 20.0)
    assert terms.p_success == pytest.approx(0.7)
    assert terms.gain_seconds == pytest.approx(0.2 * 600.0 + 100 / 10.0 - 100 / 50.0)


def test_baseline_never_hands_off() -> None:
    terms = switch_terms(
        SLOW,
        0.5,
        FAST,
        0.9,
        transcript_tokens=60_000,
        output_tokens=100,
        cached_current=60_000,
        cached_new=0,
        value_of_success=600.0,
        handoff_allowed=False,
    )
    assert terms.mode == "transcript"
    assert terms.reread_tokens == 60_000


def _state(**fields: object) -> RouterState:
    base: dict[str, object] = {
        "step": 5,
        "current": "slow",
        "level": 1,
        "transcript_tokens": 50_000,
        "seen_tokens": {"slow": 50_000, "fast": 0},
    }
    base.update(fields)
    return RouterState(**base)  # type: ignore[arg-type]


def test_route_hands_off_when_the_uncached_part_is_large() -> None:
    small = WorkerSpec(id="slow", tier="t1", prefill_tps=1000.0, decode_tps=2.0)
    decision = route(TaskFeatures(), "edit", _state(), [small, FAST], margin=0.0)
    assert decision.switched and decision.chosen == "fast"
    assert decision.handoff
    assert decision.switch_cost is not None
    assert "HANDOFF brief" in decision.reason
    assert next(c for c in decision.candidates if c.worker == "fast").switch is not None


def test_route_stays_while_a_tool_loop_is_open() -> None:
    small = WorkerSpec(id="slow", tier="t1", prefill_tps=1000.0, decode_tps=2.0)
    decision = route(
        TaskFeatures(), "edit", _state(open_tool_calls=True), [small, FAST], margin=0.0
    )
    assert decision.chosen == "slow" and not decision.switched
    assert "tool-call loop" in decision.reason


def test_route_starts_from_a_brief_after_a_failover_on_a_long_transcript() -> None:
    state = _state(current=None, transcript_tokens=HANDOFF_THRESHOLD_TOKENS + 5_000)
    decision = route(TaskFeatures(), "edit", state, [FAST])
    assert decision.chosen == "fast" and decision.handoff
    first = route(TaskFeatures(), "plan", _state(step=1, current=None, transcript_tokens=0), [FAST])
    assert not first.handoff
