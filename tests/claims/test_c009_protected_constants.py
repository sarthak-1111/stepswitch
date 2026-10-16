"""C-009: changes to protected constants fail verification unless explicitly allowed.

Proof (SPEC §18.5): gate test with a modified protected value, with and without the allow flag.

The project in `science_fixture.py` declares protected zones in science.toml: two module-level
constants in a Python file and one key of a JSON calibration file. The tests change values in
the working tree and run the science-track gates against the committed base, through the same
entry point tasks use (`run_gates`), with and without the task's allow list.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stepswitch.verify.gates import Verdict, run_gates
from tests.claims import evidence
from tests.claims.science_fixture import make_project, set_constant

pytestmark = [pytest.mark.claim("C-009")]


def protected_changes(verdict: Verdict) -> list[dict[str, Any]]:
    (result,) = [r for r in verdict.results if r.name == "protected"]
    changes: list[dict[str, Any]] = result.details["changes"]
    return changes


def change_speed_of_light(repo: Path) -> None:
    set_constant(repo / "toy" / "constants.py", "SPEED_OF_LIGHT", "3.0e8")


def test_protected_change_fails_without_allow_flag(tmp_path: Path) -> None:
    repo = make_project(tmp_path)
    constants = repo / "toy" / "constants.py"

    # The same value written differently, and a change to an unprotected constant, pass.
    set_constant(constants, "PLANCK", "6.62607015e-34")
    set_constant(constants, "DETECTOR_GAIN", "2.0")
    unchanged = run_gates(repo, track="science", base="HEAD", only=["protected"])

    change_speed_of_light(repo)
    python_change = run_gates(repo, track="science", base="HEAD", only=["protected"])
    other_allowed = run_gates(
        repo, track="science", base="HEAD", only=["protected"], allow_protected=["PLANCK"]
    )

    calibration = repo / "toy" / "calibration.json"
    data = json.loads(calibration.read_text(encoding="utf-8"))
    calibration.write_text(json.dumps({**data, "gain": 1.6}, indent=2) + "\n", encoding="utf-8")
    both = run_gates(repo, track="science", base="HEAD", only=["protected"])

    evidence.write(
        "C-009",
        "without_allow",
        {
            "unchanged_values_pass": unchanged.passed,
            "python_constant_change": {
                "passed": python_change.passed,
                "failed_gate": python_change.failed_gate,
                "changes": protected_changes(python_change),
            },
            "allow_list_naming_another_constant": {"passed": other_allowed.passed},
            "python_and_json_changes": {
                "passed": both.passed,
                "changes": protected_changes(both),
            },
        },
    )
    assert unchanged.passed, unchanged.report
    assert not python_change.passed and python_change.failed_gate == "protected"
    (change,) = protected_changes(python_change)
    assert (change["file"], change["name"], change["allowed"]) == (
        "toy/constants.py",
        "SPEED_OF_LIGHT",
        False,
    )
    assert (change["before"], change["after"]) == (299_792_458.0, 3.0e8)
    assert "SPEED_OF_LIGHT" in python_change.report
    assert not other_allowed.passed
    assert not both.passed
    assert {(c["file"], c["name"]) for c in protected_changes(both)} == {
        ("toy/constants.py", "SPEED_OF_LIGHT"),
        ("toy/calibration.json", "gain"),
    }


def test_protected_change_passes_with_allow_flag(tmp_path: Path) -> None:
    repo = make_project(tmp_path)
    change_speed_of_light(repo)
    allowed = run_gates(
        repo, track="science", base="HEAD", only=["protected"], allow_protected=["SPEED_OF_LIGHT"]
    )
    qualified = run_gates(
        repo,
        track="science",
        base="HEAD",
        only=["protected"],
        allow_protected=["toy/constants.py:SPEED_OF_LIGHT"],
    )
    evidence.write(
        "C-009",
        "with_allow",
        {
            "allow_protected": ["SPEED_OF_LIGHT"],
            "passed": allowed.passed,
            "changes": protected_changes(allowed),
            "qualified_name_passed": qualified.passed,
        },
    )
    assert allowed.passed, allowed.report
    assert qualified.passed, qualified.report
    (change,) = protected_changes(allowed)
    assert (change["name"], change["allowed"]) == ("SPEED_OF_LIGHT", True)
