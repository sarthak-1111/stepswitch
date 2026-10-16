"""A small science-track project for the science-gate claims (C-008, C-009).

The package `toy` computes a synthetic spectrum (Gaussian peaks on a grid). Its golden case
`tests/golden/peaks/` holds the input parameters and the expected output, produced by the
unmodified code. Module-level constants SCALE, SHIFT and BROADEN are the mutation hooks: the
claim tests rewrite those lines to inject numerical regressions of known size.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SPECTRA = '''\
"""Synthetic spectrum: Gaussian peaks on a uniform grid."""

import json
import sys
from pathlib import Path

import numpy as np

SCALE = 0.0
SHIFT = 0.0
BROADEN = 0.0


def spectrum(params):
    x = np.linspace(params["x_min"], params["x_max"], params["points"])
    y = np.zeros_like(x)
    for peak in params["peaks"]:
        center = peak["center"] + SHIFT
        width = peak["width"] * (1.0 + BROADEN)
        y += peak["height"] * np.exp(-0.5 * ((x - center) / width) ** 2)
    return x, y * (1.0 + SCALE)


def main(argv):
    params = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    x, y = spectrum(params)
    out = {"x": x.tolist(), "y": y.tolist()}
    Path(argv[2]).write_text(json.dumps(out), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
'''

CONSTANTS = '''\
"""Physical constants and instrument calibration used by the analysis."""

SPEED_OF_LIGHT = 299_792_458.0  # m/s, exact by definition
PLANCK = 6.626_070_15e-34  # J s, exact by definition
DETECTOR_GAIN = 1.5  # not protected: tuned per run
'''

CALIBRATION = {"offset": 0.12, "gain": 1.5, "operator": "lab"}

PARAMS = {
    "x_min": 0.0,
    "x_max": 20.0,
    "points": 2001,
    "peaks": [
        {"center": 4.0, "width": 0.5, "height": 1.0},
        {"center": 9.5, "width": 0.7, "height": 0.6},
        {"center": 15.0, "width": 0.4, "height": 0.35},
    ],
}

GOLDEN_COMMAND = ["{python}", "-m", "toy.spectra", "{case}/input.json", "{output}/spectrum.json"]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def run_reference(repo: Path, params_file: Path, out_file: Path) -> dict[str, Any]:
    """Run the project's code the way the golden command does; return the output.

    Cached bytecode is deleted first and none is written: `set_constant` rewrites a module with
    a value of the same size, often within the same second, and Python would then trust the
    cached bytecode of the previous value (its check is size plus whole-second mtime).
    """
    for cache in list(repo.rglob("__pycache__")):
        if ".git" not in cache.parts:
            shutil.rmtree(cache, ignore_errors=True)
    subprocess.run(
        [sys.executable, "-B", "-m", "toy.spectra", str(params_file), str(out_file)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    data: dict[str, Any] = json.loads(out_file.read_text(encoding="utf-8"))
    return data


def make_project(root: Path) -> Path:
    """Create the project in `root / "project"` as a git repository with one commit."""
    repo = root / "project"
    (repo / "toy").mkdir(parents=True)
    (repo / "toy" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "toy" / "spectra.py").write_text(SPECTRA, encoding="utf-8")
    (repo / "toy" / "constants.py").write_text(CONSTANTS, encoding="utf-8")
    (repo / "toy" / "calibration.json").write_text(
        json.dumps(CALIBRATION, indent=2) + "\n", encoding="utf-8"
    )
    case = repo / "tests" / "golden" / "peaks"
    (case / "expected").mkdir(parents=True)
    (case / "input.json").write_text(json.dumps(PARAMS, indent=2) + "\n", encoding="utf-8")
    run_reference(repo, case / "input.json", case / "expected" / "spectrum.json")
    write_science_toml(repo, [])
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "reference implementation")
    return repo


def write_science_toml(repo: Path, quantities: list[dict[str, Any]]) -> None:
    """science.toml with one golden case and the given quantities, plus the protected zones."""
    lines = [
        "# Science-track settings (SPEC §6.2).",
        "",
        "[[golden]]",
        'case = "peaks"',
        "command = [" + ", ".join(json.dumps(part) for part in GOLDEN_COMMAND) + "]",
    ]
    for quantity in quantities:
        lines += ["", "[[golden.quantities]]"]
        lines += [f"{key} = {json.dumps(value)}" for key, value in quantity.items()]
    lines += [
        "",
        "[[protected]]",
        'file = "toy/constants.py"',
        'names = ["SPEED_OF_LIGHT", "PLANCK"]',
        "",
        "[[protected]]",
        'file = "toy/calibration.json"',
        'names = ["gain"]',
        "",
    ]
    (repo / "science.toml").write_text("\n".join(lines), encoding="utf-8")


def set_constant(path: Path, name: str, value: str) -> None:
    """Rewrite the module-level assignment `name = ...` (the rest of the line is dropped)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{name} = "):
            lines[index] = f"{name} = {value}"
            break
    else:
        raise KeyError(name)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
