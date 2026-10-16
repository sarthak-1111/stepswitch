"""C-008: the science gate catches numerical regressions beyond your tolerances.

Proof (SPEC §18.5): mutation test. Injected perturbations above tolerance are all caught;
perturbations below tolerance are not flagged.

The project in `science_fixture.py` has a golden case whose expected output came from the
unmodified code. For each comparison mode of science.toml (elementwise, integral, peak
positions, spectral overlap) the test declares one quantity with a tolerance, then rewrites a
constant in the code to inject a perturbation at a multiple of that tolerance: 0.1x to 0.8x
(below) and 1.25x to 100x (above). The science gate runs through the same entry point tasks
use (`run_gates`, track "science", gate "golden").

Whether a perturbation is above or below tolerance is decided by an oracle in this file that
measures the produced output directly with numpy, using the metric definitions documented for
science.toml:

* elementwise: any |a - e| > atol + rtol * |e|;
* integral (trapezoid rule over x): |I(a) - I(e)| > atol + rtol * |I(e)|;
* peaks (local maxima above 5% of the maximum, refined by a parabola through the three samples
  around each maximum): a different number of peaks, or any |position shift| > atol;
* overlap (cosine similarity of the two curves): similarity < min_overlap.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stepswitch.verify.gates import run_gates
from tests.claims import evidence
from tests.claims.science_fixture import (
    make_project,
    run_reference,
    set_constant,
    write_science_toml,
)

pytestmark = [pytest.mark.claim("C-008")]

BELOW = (0.1, 0.3, 0.5, 0.8)
ABOVE = (1.25, 2.0, 5.0, 10.0, 100.0)
MIN_HEIGHT = 0.05

# mode -> (quantity settings, mutated constant, size of the constant per unit of tolerance)
MODES: dict[str, dict[str, Any]] = {
    "elementwise": {
        "quantity": {"mode": "elementwise", "rtol": 1e-6, "atol": 0.0},
        "constant": "SCALE",
    },
    "integral": {
        "quantity": {"mode": "integral", "rtol": 1e-4, "atol": 0.0},
        "constant": "SCALE",
    },
    "peaks": {
        "quantity": {"mode": "peaks", "atol": 0.01},
        "constant": "SHIFT",
    },
    "overlap": {
        "quantity": {"mode": "overlap", "min_overlap": 0.999},
        "constant": "BROADEN",
    },
}


def constant_for(mode: str, multiple: float) -> float:
    """The constant that perturbs the output by about `multiple` tolerances in `mode`."""
    quantity = MODES[mode]["quantity"]
    if mode in ("elementwise", "integral"):
        return float(multiple * quantity["rtol"])
    if mode == "peaks":
        return float(multiple * quantity["atol"])
    # Broadening a Gaussian by a factor (1 + b) gives cosine similarity of about 1 - b^2 / 4.
    deficit = multiple * (1.0 - quantity["min_overlap"])
    return float(2.0 * np.sqrt(deficit))


# --------------------------------------------------------------------------------------------
# Oracle
# --------------------------------------------------------------------------------------------


def peak_positions(x: np.ndarray, y: np.ndarray) -> list[float]:
    threshold = MIN_HEIGHT * float(np.max(y))
    positions = []
    for i in range(1, len(y) - 1):
        if y[i] > y[i - 1] and y[i] >= y[i + 1] and y[i] >= threshold:
            left, mid, right = y[i - 1], y[i], y[i + 1]
            denominator = left - 2.0 * mid + right
            offset = 0.5 * (left - right) / denominator if denominator != 0 else 0.0
            positions.append(float(x[i] + offset * (x[i + 1] - x[i])))
    return positions


def oracle(mode: str, expected: dict[str, Any], actual: dict[str, Any]) -> tuple[float, bool]:
    """(measured deviation in units of tolerance, whether it is beyond tolerance)."""
    quantity = MODES[mode]["quantity"]
    x = np.asarray(expected["x"])
    e, a = np.asarray(expected["y"]), np.asarray(actual["y"])
    if mode == "elementwise":
        allowed = quantity["atol"] + quantity["rtol"] * np.abs(e)
        ratio = float(np.max(np.abs(a - e) / np.where(allowed > 0, allowed, np.inf)))
        return ratio, bool(np.any(np.abs(a - e) > allowed))
    if mode == "integral":
        ie, ia = float(np.trapezoid(e, x)), float(np.trapezoid(a, np.asarray(actual["x"])))
        allowed = quantity["atol"] + quantity["rtol"] * abs(ie)
        return abs(ia - ie) / allowed, abs(ia - ie) > allowed
    if mode == "peaks":
        pe, pa = peak_positions(x, e), peak_positions(np.asarray(actual["x"]), a)
        if len(pe) != len(pa):
            return float("inf"), True
        shift = max(abs(p - q) for p, q in zip(pe, pa, strict=True))
        return shift / quantity["atol"], shift > quantity["atol"]
    similarity = float(np.dot(a, e) / (np.linalg.norm(a) * np.linalg.norm(e)))
    deficit = 1.0 - similarity
    allowed = 1.0 - quantity["min_overlap"]
    return deficit / allowed, similarity < quantity["min_overlap"]


# --------------------------------------------------------------------------------------------
# The mutation test
# --------------------------------------------------------------------------------------------


def test_mutations_above_tolerance_caught_below_not_flagged(tmp_path: Path) -> None:
    limits = evidence.thresholds("C-008")
    repo = make_project(tmp_path)
    case = repo / "tests" / "golden" / "peaks"
    expected = run_reference(repo, case / "input.json", tmp_path / "expected.json")
    spectra = repo / "toy" / "spectra.py"
    rows: list[dict[str, Any]] = []
    baseline: dict[str, bool] = {}

    for mode, setup in MODES.items():
        quantity = {
            "name": f"spectrum-{mode}",
            "file": "spectrum.json",
            "key": "y",
            "x_key": "x",
            **setup["quantity"],
        }
        write_science_toml(repo, [quantity])
        baseline[mode] = run_gates(repo, track="science", base=None, only=["golden"]).passed
        for multiple in (*BELOW, *ABOVE):
            value = constant_for(mode, multiple)
            set_constant(spectra, setup["constant"], repr(value))
            actual = run_reference(repo, case / "input.json", tmp_path / f"{mode}.json")
            measured, beyond = oracle(mode, expected, actual)
            verdict = run_gates(repo, track="science", base=None, only=["golden"])
            set_constant(spectra, setup["constant"], "0.0")
            rows.append(
                {
                    "mode": mode,
                    "constant": setup["constant"],
                    "value": value,
                    "multiple": multiple,
                    "measured_in_tolerances": measured,
                    "beyond_tolerance": beyond,
                    "flagged": not verdict.passed,
                    "gate": verdict.failed_gate,
                }
            )

    above = [r for r in rows if r["beyond_tolerance"]]
    below = [r for r in rows if not r["beyond_tolerance"]]
    caught = sum(r["flagged"] for r in above) / len(above)
    flagged = sum(r["flagged"] for r in below) / len(below)
    misclassified = [r for r in rows if r["beyond_tolerance"] != (r["multiple"] > 1)]
    # Near the threshold every mutation must land close to its target size, so a reference run
    # that measured the wrong code (stale bytecode, a missed rewrite) cannot pass unnoticed.
    off_target = [
        r
        for r in rows
        if r["multiple"] <= 2
        and abs(r["measured_in_tolerances"] - r["multiple"]) > 0.15 * r["multiple"]
    ]
    evidence.write(
        "C-008",
        "mutations",
        {
            "project": "science_fixture.py: Gaussian peaks, golden case `peaks`",
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "baseline_passes": baseline,
            "mutations": rows,
            "above_tolerance": len(above),
            "below_tolerance": len(below),
            "caught_above_tolerance": caught,
            "flagged_below_tolerance": flagged,
        },
    )
    assert all(baseline.values()), f"unmutated code fails the gate: {baseline}"
    assert not misclassified, f"mutation sizes off target: {misclassified}"
    assert not off_target, f"measured deviation far from the injected one: {off_target}"
    assert len(above) >= 4 * len(ABOVE) and len(below) >= 4 * len(BELOW)
    assert caught >= limits["caught_above_tolerance"], [r for r in above if not r["flagged"]]
    assert flagged <= limits["flagged_below_tolerance"], [r for r in below if r["flagged"]]
