"""Learned router parameters: the handoff recovery posterior and bench priors."""

from __future__ import annotations

from pathlib import Path

import pytest

from stepswitch.router import calibration
from stepswitch.router.calibration import HandoffRecovery, PriorTable, priors_from_results
from stepswitch.router.cost import DEFAULT_HANDOFF_RECOVERY
from stepswitch.state import StateDB


def test_prior_mean_is_the_published_recovery() -> None:
    assert HandoffRecovery().mean == pytest.approx(DEFAULT_HANDOFF_RECOVERY, abs=0.02)


def test_successes_raise_and_failures_lower_the_recovery() -> None:
    up, down = HandoffRecovery(), HandoffRecovery()
    for _ in range(20):
        up.update(0.3, 0.9, True)
        down.update(0.3, 0.9, False)
    assert up.mean > DEFAULT_HANDOFF_RECOVERY + 0.2
    assert down.mean < DEFAULT_HANDOFF_RECOVERY - 0.2
    assert up.observations == 20


def test_handoffs_to_a_worker_that_is_not_better_carry_no_information() -> None:
    recovery = HandoffRecovery()
    recovery.update(0.8, 0.5, False)
    assert recovery.observations == 0
    assert recovery.mean == HandoffRecovery().mean


def test_round_trip_and_bad_data() -> None:
    recovery = HandoffRecovery()
    recovery.update(0.2, 0.8, True)
    again = HandoffRecovery.from_dict(recovery.as_dict())
    assert again.mean == recovery.mean and again.observations == 1
    assert HandoffRecovery.from_dict({"log_weights": [0.0]}).mean == HandoffRecovery().mean


def test_prior_table_shrinks_towards_the_tier_rate() -> None:
    rows = [
        {"contestant": "big", "track": "general", "passed": True, "status": "done"},
        {"contestant": "big", "track": "general", "passed": True, "status": "done"},
        {"contestant": "router", "track": "general", "passed": False, "status": "done"},
        {"contestant": "big", "track": "science", "passed": False, "status": "error"},
    ]
    table = priors_from_results(rows, models={"big": "Big-Model"})
    assert table.counts == {"big-model|general": [2, 2]}
    rate = table.rate("big-model", "general", 0.5)
    assert rate == pytest.approx((2 + 4 * 0.5) / (2 + 4))
    assert table.rate("big-model", "science", 0.5) is None
    assert PriorTable.from_dict(table.as_dict()).counts == table.counts


def test_parameters_persist_in_the_state_database(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    try:
        assert calibration.load_recovery(db).observations == 0
        stored = calibration.record_handoff_outcomes(db, [(0.3, 0.9), (0.9, 0.5)], True)
        assert stored.observations == 1
        assert calibration.load_recovery(db).observations == 1
        calibration.save_param(
            db, calibration.PRIORS_KEY, PriorTable({"m|general": [1, 2]}).as_dict()
        )
        assert calibration.load_priors(db).counts == {"m|general": [1, 2]}
    finally:
        db.close()
