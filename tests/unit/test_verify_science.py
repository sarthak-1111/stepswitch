"""Science-track checks (SPEC §6.2): readers, the four comparison modes, golden runs, protected
zones, reproducibility records and notebook pairing.

Numerical fixtures: `fixtures/science/modes.json` (and the same numbers in `modes.csv`) hold
x = [0, 0.5, 1, 1.5, 2], e = [1, 2, 4, 2, 1] and a = [1, 2.5, 4, 2, 1]. Worked by hand:

* deviation: max |a - e| = 0.5 at index 1, max relative = 0.5 / 2 = 0.25;
* trapezoid over x: I(e) = 0.5 * (1.5 + 3 + 3 + 1.5) = 4.5, I(a) = 4.75, so |dI| = 0.25;
  over the sample index: I(e) = 9, I(a) = 9.5;
* peaks: e peaks at index 2 (l = r = 2, offset 0) so x = 1.0; a has l = 2.5, m = 4, r = 2,
  offset = 0.5 * (2.5 - 2) / (2.5 - 8 + 2) = -1/14 of the step 0.5, so x = 1 - 1/28;
* overlap: a.e = 27, |e|^2 = 26, |a|^2 = 28.25, cosine similarity = 27 / sqrt(26 * 28.25).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stepswitch.verify import commands, science
from stepswitch.verify.science import DataError, ScienceConfigError, compare

FIXTURES = Path(__file__).parent / "fixtures" / "science"
MODES = json.loads((FIXTURES / "modes.json").read_text(encoding="utf-8"))
X, E, A = (np.asarray(MODES[k], dtype=float) for k in ("x", "e", "a"))
COSINE = 27 / math.sqrt(26 * 28.25)
SHIFT = -1 / 28


# --------------------------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------------------------


def test_json_and_csv_fixtures_read_the_same_numbers() -> None:
    for name in ("x", "e", "a"):
        from_json = science.read_array(FIXTURES / "modes.json", name)
        from_csv = science.read_array(FIXTURES / "modes.csv", name)
        assert from_json.dtype == np.float64
        assert np.array_equal(from_json, from_csv)
    nested = science.read_array(FIXTURES / "modes.json", "meta.calibration.offsets")
    assert nested.tolist() == [0.1, 0.2]
    assert science.read_array(FIXTURES / "modes.json", "meta.calibration.gain").shape == ()


def test_npz_npy_bare_lists_and_nulls(tmp_path: Path) -> None:
    np.savez(tmp_path / "data.npz", x=X, y=E)
    np.save(tmp_path / "data.npy", A)
    (tmp_path / "list.json").write_text("[1, 2, null, 4]", encoding="utf-8")
    assert np.array_equal(science.read_array(tmp_path / "data.npz", "y"), E)
    assert np.array_equal(science.read_array(tmp_path / "data.npy", "anything"), A)
    values = science.read_array(tmp_path / "list.json", "ignored")
    assert values[:2].tolist() == [1.0, 2.0] and math.isnan(values[2])
    with pytest.raises(DataError, match="bare list"):
        science.read_array(tmp_path / "list.json", "x", keyed=True)
    with pytest.raises(DataError, match="one array"):
        science.read_array(tmp_path / "data.npy", "x", keyed=True)
    with pytest.raises(DataError, match="arrays: x, y"):
        science.read_array(tmp_path / "data.npz", "z")


@pytest.mark.parametrize(
    ("name", "content", "key", "message"),
    [
        ("bad.csv", "x,y\n1,abc\n", "y", "row 2, column 'y'"),
        ("bad.csv", "x,y\n1,2\n", "z", "columns: x, y"),
        ("bad.json", '{"y": [1, "two"]}', "y", "not numbers"),
        ("bad.json", '{"y": [[1, 2], [3]]}', "y", "rectangular"),
        ("bad.json", '{"y": [1, 2]}', "z", "no key 'z'"),
        ("bad.json", "{not json", "y", "cannot read"),
        ("bad.txt", "1 2 3", "y", "unsupported data file"),
    ],
)
def test_reader_errors_are_clear(
    tmp_path: Path, name: str, content: str, key: str, message: str
) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8")
    with pytest.raises(DataError, match=message):
        science.read_array(tmp_path / name, key)
    with pytest.raises(DataError, match="file not found"):
        science.read_array(tmp_path / "missing.json", key)


# --------------------------------------------------------------------------------------------
# Comparison modes
# --------------------------------------------------------------------------------------------


def test_deviation_of_the_fixture() -> None:
    assert science.deviation(E, A) == (0.5, 0.25)
    assert science.deviation(E, A[:3]) == (None, None)


@pytest.mark.parametrize(
    ("rtol", "atol", "passed", "ratio"),
    [
        (0.1, 0.0, False, 2.5),
        (0.25, 0.0, True, 1.0),
        (0.0, 0.5, True, 1.0),
        (0.0, 0.49, False, None),
    ],
)
def test_elementwise_tolerance_boundary(
    rtol: float, atol: float, passed: bool, ratio: float | None
) -> None:
    result = compare("elementwise", E, A, rtol=rtol, atol=atol)
    assert result.passed is passed
    if ratio is not None:
        assert result.ratio == pytest.approx(ratio)
    assert (result.max_abs, result.max_rel) == (0.5, 0.25)
    if not passed:
        assert "1 of 5 values beyond tolerance; worst at index 1" in result.detail


def test_elementwise_relative_perturbations_around_tolerance() -> None:
    expected = np.linspace(0.1, 10.0, 101)
    rtol = 1e-6
    below = compare("elementwise", expected, expected * (1 + 0.8 * rtol), rtol=rtol)
    above = compare("elementwise", expected, expected * (1 + 1.25 * rtol), rtol=rtol)
    assert below.passed and below.ratio == pytest.approx(0.8, rel=1e-6)
    assert not above.passed and above.ratio == pytest.approx(1.25, rel=1e-6)


@pytest.mark.parametrize(
    ("expected", "actual", "passed"),
    [
        ([1.0, math.nan, 3.0], [1.0, math.nan, 3.0], True),
        ([1.0, math.nan, 3.0], [1.0, 2.0, 3.0], False),
        ([1.0, 2.0, 3.0], [1.0, math.nan, 3.0], False),
        ([math.inf], [math.inf], True),
        ([1.0], [math.inf], False),
        ([math.inf], [1.0], False),
        ([math.inf], [-math.inf], False),
        ([1.0, 2.0], [1.0, 2.0, 3.0], False),
    ],
)
def test_elementwise_nan_inf_and_shape(
    expected: list[float], actual: list[float], passed: bool
) -> None:
    result = compare("elementwise", np.array(expected), np.array(actual), rtol=0.5, atol=1.0)
    assert result.passed is passed


def test_integral_over_x_and_over_the_sample_index() -> None:
    result = compare("integral", E, A, rtol=0.05, x_expected=X, x_actual=X)
    assert result.extra == {"integral_expected": 4.5, "integral_actual": 4.75}
    assert result.metric == pytest.approx(0.25)
    assert not result.passed and result.ratio == pytest.approx(0.25 / 0.225)
    assert compare("integral", E, A, rtol=0.06, x_expected=X, x_actual=X).passed
    by_index = compare("integral", E, A, rtol=0.05)
    assert by_index.extra == {"integral_expected": 9.0, "integral_actual": 9.5}
    assert by_index.ratio == pytest.approx(0.5 / 0.45)


def test_integral_compares_curves_on_different_grids() -> None:
    coarse_x, coarse = np.array([0.0, 1.0, 2.0]), np.array([0.0, 1.0, 0.0])
    fine_x, fine = np.array([0.0, 0.5, 1.0, 1.5, 2.0]), np.array([0.0, 0.5, 1.0, 0.5, 0.0])
    result = compare("integral", coarse, fine, rtol=1e-12, x_expected=coarse_x, x_actual=fine_x)
    assert result.passed and result.max_abs is None  # no elementwise deviation across grids


def test_peak_positions_are_refined_by_a_parabola() -> None:
    assert science.peak_positions(E, X) == [1.0]
    assert science.peak_positions(A, X) == pytest.approx([1.0 + SHIFT])
    assert science.peak_positions(np.array([0.0, 1.0, 1.0, 0.0])) == [1.5]  # plateau: midpoint
    small = np.array([0.0, 1.0, 0.0, 0.04, 0.0, 0.06, 0.0])
    assert science.peak_positions(small, min_height=0.05) == [1.0, 5.0]
    grid = np.linspace(0.0, 10.0, 1001)
    gaussian = np.exp(-0.5 * ((grid - 5.003) / 0.5) ** 2)
    (found,) = science.peak_positions(gaussian, grid)
    assert abs(found - 5.003) < 1e-4  # off-grid centre recovered well within the step 0.01


def test_peaks_mode_tolerance_and_count() -> None:
    within = compare("peaks", E, A, atol=0.04, x_expected=X, x_actual=X)
    beyond = compare("peaks", E, A, atol=0.03, x_expected=X, x_actual=X)
    assert within.passed and within.metric == pytest.approx(-SHIFT)
    assert not beyond.passed and beyond.ratio == pytest.approx(-SHIFT / 0.03)
    assert beyond.extra["expected_peaks"] == [1.0]
    extra_peak = np.array([1.0, 2.0, 4.0, 2.0, 3.0, 1.0])  # an end sample is never a peak
    counted = compare("peaks", E, extra_peak, atol=1.0)
    assert not counted.passed and counted.detail == "expected 1 peaks, found 2"
    assert math.isinf(counted.ratio or 0.0)


def test_overlap_is_cosine_similarity() -> None:
    result = compare("overlap", E, A, min_overlap=0.99)
    assert result.passed and result.metric == pytest.approx(COSINE)
    assert not compare("overlap", E, A, min_overlap=0.999).passed
    # Cosine similarity ignores scale: combine with integral or elementwise to catch it.
    assert compare("overlap", E, 3 * E, min_overlap=0.999999).passed
    assert compare("overlap", np.zeros(3), np.zeros(3)).metric == 1.0
    assert not compare("overlap", np.zeros(3), np.ones(3)).passed
    assert not compare("overlap", E, A[:4]).passed


def test_setup_problems_raise_and_output_problems_fail() -> None:
    with pytest.raises(DataError, match="unknown comparison mode"):
        compare("bogus", E, A)
    with pytest.raises(DataError, match="one-dimensional"):
        compare("peaks", np.ones((2, 3)), np.ones((2, 3)))
    result = compare("integral", E, np.ones((2, 3)))
    assert result.status == "failed" and "one-dimensional" in result.detail


def test_markdown_table_escapes_cells() -> None:
    comparison = compare("elementwise", E, A, rtol=0.1)
    comparison.case, comparison.quantity = "peaks", "spectrum"
    table = science.markdown_table([comparison])
    header, rule, row = table.splitlines()
    assert header.startswith("| case | quantity | mode | max abs dev | max rel dev")
    assert row.startswith("| peaks | spectrum | elementwise | 0.5 | 0.25 | 2.5 x tol |")
    assert "\\|a - e\\|" in row and row.endswith("| FAIL |")


def test_envelope_keeps_narrow_features() -> None:
    y = np.zeros(100_000)
    y[54_321] = 7.0
    keep = science.envelope_index(y, 4000)
    assert 54_321 in keep and len(keep) <= 4000


# --------------------------------------------------------------------------------------------
# science.toml
# --------------------------------------------------------------------------------------------

MINIMAL = """
[[golden]]
case = "peaks"
command = ["{python}", "run.py", "{case}/input.json", "{output}"]

[[golden.quantities]]
name = "spectrum"
file = "spectrum.json"
"""


def test_parse_science_defaults() -> None:
    config = science.parse_science(MINIMAL)
    (quantity,) = config.golden[0].quantities
    assert (quantity.mode, quantity.rtol, quantity.atol) == ("elementwise", 1e-7, 0.0)
    assert (quantity.min_overlap, config.science.min_peak_height) == (0.999, 0.05)
    assert quantity.data_key == "spectrum" and quantity.expected_path == "expected/spectrum.json"
    assert config.protected == [] and config.reproducibility.seeds == []


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("rtoll = 1e-6", "golden[0].quantities[0].rtoll: Extra inputs are not permitted"),
        ('mode = "bogus"', "golden[0].quantities[0].mode"),
        ("rtol = -1.0", "golden[0].quantities[0].rtol"),
        ("min_overlap = 1.5", "golden[0].quantities[0].min_overlap"),
        ('expected = "../escape.json"', "relative path inside the project"),
    ],
)
def test_parse_science_rejects_bad_quantities(snippet: str, message: str) -> None:
    with pytest.raises(ScienceConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
        science.parse_science(MINIMAL + snippet + "\n")


@pytest.mark.parametrize(
    "text",
    [
        '[[golden]]\ncase = "x"\ncommand = "python run.py"\n',
        '[[golden]]\ncase = "../x"\ncommand = ["python"]\n',
        '[[protected]]\nfile = "/etc/constants.py"\n',
        '[[protected]]\nfile = "C:/constants.py"\n',
        "[[goldens]]\n",
        "[[golden]\n",
    ],
)
def test_parse_science_rejects_bad_files(text: str) -> None:
    with pytest.raises(ScienceConfigError, match="science.toml"):
        science.parse_science(text)


def test_parse_science_paths_and_placeholders() -> None:
    config = science.parse_science(
        MINIMAL.replace('file = "spectrum.json"', 'file = "{repo}/out/spectrum.json"')
        + '[[protected]]\nfile = "toy\\\\constants.py"\nnames = ["C"]\n'
        + '[reproducibility]\nseeds = [1, 2]\nnote = "free text"\n'
    )
    (quantity,) = config.golden[0].quantities
    assert quantity.file == "{repo}/out/spectrum.json"
    assert quantity.expected_path == "expected/spectrum.json"
    assert config.protected[0].file == "toy/constants.py"
    assert config.reproducibility.model_extra == {"note": "free text"}


def test_load_science_absent(tmp_path: Path) -> None:
    assert science.load_science(tmp_path) is None


# --------------------------------------------------------------------------------------------
# Golden runs
# --------------------------------------------------------------------------------------------

PACKAGE = "VALUE = 1.0\n"
GENERATOR = '''\
"""Writes a synthetic spectrum in three formats; imports `mypkg` from the repository root."""
import csv
import json
import sys
from pathlib import Path

import numpy as np

import mypkg

params = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = Path(sys.argv[2])
x = np.linspace(0.0, 10.0, 501)
y = mypkg.VALUE * params["height"] * np.exp(-0.5 * ((x - params["center"]) / 0.4) ** 2)
(out / "spectrum.json").write_text(json.dumps({"x": x.tolist(), "y": y.tolist()}))
with (out / "table.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["x", "y"])
    writer.writerows(zip(x.tolist(), y.tolist()))
np.savez(out / "arrays.npz", x=x, y=y)
'''
QUANTITIES = [
    {"name": "elementwise", "file": "spectrum.json", "key": "y", "rtol": 1e-6},
    {
        "name": "integral",
        "file": "table.csv",
        "key": "y",
        "x_key": "x",
        "mode": "integral",
        "rtol": 1e-6,
    },
    {
        "name": "peaks",
        "file": "arrays.npz",
        "key": "y",
        "x_key": "x",
        "mode": "peaks",
        "atol": 1e-3,
    },
    {"name": "overlap", "file": "spectrum.json", "key": "y", "mode": "overlap"},
]


def write_toml(repo: Path, command: list[str], quantities: list[dict[str, Any]]) -> None:
    lines = ["[reproducibility]", "seeds = [3, 7]", "", "[[golden]]", 'case = "peak"']
    lines.append("command = " + json.dumps(command))
    for quantity in quantities:
        lines.append("[[golden.quantities]]")
        lines += [f"{key} = {json.dumps(value)}" for key, value in quantity.items()]
    (repo / "science.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def golden_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    (repo / "mypkg").mkdir(parents=True)
    (repo / "mypkg" / "__init__.py").write_text(PACKAGE, encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "gen.py").write_text(GENERATOR, encoding="utf-8")
    case = repo / "tests" / "golden" / "peak"
    (case / "expected").mkdir(parents=True)
    (case / "input.json").write_text('{"height": 2.0, "center": 4.2}', encoding="utf-8")
    subprocess.run(
        [sys.executable, "scripts/gen.py", str(case / "input.json"), str(case / "expected")],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(repo)},
        check=True,
        capture_output=True,
    )
    write_toml(repo, ["{python}", "scripts/gen.py", "{case}/input.json", "{output}"], QUANTITIES)
    return repo


def run(repo: Path, artifacts: Path | None = None, timeout: float = 120.0) -> science.GoldenReport:
    config = science.load_science(repo)
    assert config is not None
    return science.run_golden(
        repo, config, python=sys.executable, artifacts=artifacts, timeout=timeout
    )


def test_golden_run_passes_and_writes_the_report(golden_repo: Path, tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    report = run(golden_repo, artifacts)
    assert report.status == "passed", report.failure_report()
    assert [c.quantity for c in report.comparisons] == [q["name"] for q in QUANTITIES]
    assert all(c.max_abs == 0.0 for c in report.comparisons)
    assert report.summary() == "4 quantities within tolerance in 1 case(s)"
    assert report.failure_report() == ""
    record = report.reproducibility
    assert record["seeds"] == [3, 7] and record["python"] == sys.version.split()[0]
    assert "packages" in record and record["git_commit"] is None
    text = (artifacts / "golden-report.md").read_text(encoding="utf-8")
    assert "| peak | peaks | peaks |" in text and "## Reproducibility" in text
    plots = report.to_dict()["plots"]
    assert len(plots) == 4
    assert all(Path(p).read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for p in plots)
    json.dumps(report.to_dict(), allow_nan=False)


def test_scaling_fails_elementwise_and_integral_but_not_peaks_or_overlap(
    golden_repo: Path,
) -> None:
    (golden_repo / "mypkg" / "__init__.py").write_text("VALUE = 1.001\n", encoding="utf-8")
    report = run(golden_repo)
    statuses = {c.quantity: c.status for c in report.comparisons}
    assert statuses == {
        "elementwise": "failed",
        "integral": "failed",
        "peaks": "passed",
        "overlap": "passed",
    }
    assert report.status == "failed"
    text = report.failure_report()
    assert "| peak | elementwise | elementwise |" in text and "FAIL" in text
    assert "peak/elementwise:" in text and len(text) <= 1800


def test_golden_case_failures(golden_repo: Path) -> None:
    failing = ["{python}", "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"]
    write_toml(golden_repo, failing, QUANTITIES[:1])
    (case,) = run(golden_repo).cases
    assert case.status == "failed" and case.error == "the command exited with code 3"
    assert "boom" in case.output

    silent = ["{python}", "-c", "pass"]
    write_toml(golden_repo, silent, QUANTITIES[:1])
    (comparison,) = run(golden_repo).comparisons
    assert comparison.status == "failed" and "file not found" in comparison.detail

    missing_expected = [{**QUANTITIES[0], "expected": "expected/other.json"}]
    write_toml(golden_repo, ["{python}", "-c", "pass"], missing_expected)
    (comparison,) = run(golden_repo).comparisons
    assert comparison.status == "error" and "expected copy" in comparison.detail

    write_toml(golden_repo, ["no-such-program-for-stepswitch-tests"], QUANTITIES[:1])
    report = run(golden_repo)
    assert report.status == "error" and "cannot run" in report.cases[0].error

    shutil.rmtree(golden_repo / "tests" / "golden" / "peak")
    report = run(golden_repo)
    assert report.status == "error" and "tests/golden/peak/ not found" in report.summary()


def test_golden_run_ignores_stale_bytecode(
    golden_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-size edit within the same second as the cached version must not run old code."""
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    init = golden_repo / "mypkg" / "__init__.py"
    second = int(time.time()) - 100
    os.utime(init, (second + 0.1, second + 0.1))
    assert run(golden_repo).status == "passed"  # caches the bytecode of VALUE = 1.0
    (cached,) = (golden_repo / "mypkg" / "__pycache__").glob("__init__.*.pyc")
    os.utime(cached, (second + 0.5, second + 0.5))  # as if cached within that second
    init.write_text("VALUE = 1.5\n", encoding="utf-8")  # same size, edited in the same second
    os.utime(init, (second + 0.9, second + 0.9))
    probe = [sys.executable, "-c", "import mypkg; print(mypkg.VALUE)"]
    stale = subprocess.run(probe, cwd=golden_repo, capture_output=True, text=True, check=True)
    assert stale.stdout.strip() == "1.0"  # CPython itself reuses the stale cache
    assert run(golden_repo).status == "failed"  # the gate runs the code that is on disk


# --------------------------------------------------------------------------------------------
# Protected zones: Python bindings
# --------------------------------------------------------------------------------------------


def value(source: str, name: str = "K") -> science.ProtectedValue | None:
    return science.python_values(source, [name]).get(name)


def same(a: str, b: str, name: str = "K") -> bool:
    return science._same(value(a, name), value(b, name))


def test_python_literals_compare_by_value() -> None:
    assert value("K = 6.626_070_15e-34") == science.ProtectedValue(
        6.62607015e-34, ("literal", 6.62607015e-34)
    )
    assert same("K = 1", "K = 1.0")
    assert same("K = -2.5  # comment", "K: float = -2.50")
    assert same("K = (1, 2)", "K = (1,\n     2)")
    assert not same("K = (1, 2)", "K = [1, 2]")
    assert not same("K = True", "K = 1")
    assert same("A, K = 1.0, 3", "K = 3")
    assert value("A = 1", "K") is None


@pytest.mark.parametrize(
    ("changed", "differs"),
    [
        ("K = 2 * PI", False),  # same expression, written differently
        ("K = 2 * PI + 0", True),
        ("K = 2*PI\nK *= 1.001", True),  # augmented assignment
        ("K = 2*PI\nif True:\n    K = 1.0", True),  # rebinding inside a block
        ("K = 2*PI\ndef f():\n    global K\n    K = 1.0", True),
        ("K = 2*PI\ndef f():\n    K = 1.0", False),  # a local, not the module constant
        ("K = 2*PI\nclass C:\n    K = 1.0", False),  # a class attribute
        ("K = 2*PI\nfrom other import *", True),
        ("from other import PI as K", True),
        ("K = 2*PI\ndel K", True),
        ("K = 2*PI\nfor K in range(3):\n    pass", True),
        ("K = 2*PI\nwith open('f') as K:\n    pass", True),
        ("K = 2*PI\nprint(K := 1)", True),
    ],
)
def test_python_rebindings_are_changes(changed: str, differs: bool) -> None:
    assert same("K = 2*PI", changed) is not differs


def test_python_values_need_parsable_source() -> None:
    with pytest.raises(SyntaxError):
        science.python_values("K = (", ["K"])


# --------------------------------------------------------------------------------------------
# Protected zones against a git base
# --------------------------------------------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


ZONES = """
[[protected]]
file = "pkg/constants.py"
names = ["C", "H"]

[[protected]]
file = "pkg/params.json"
names = ["fit.tol", "fit.steps.1"]

[[protected]]
file = "pkg/settings.toml"
names = ["fit.max_iter"]

[[protected]]
file = "data/calibration.csv"
"""


@pytest.fixture
def protected_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "data").mkdir()
    (repo / "pkg" / "constants.py").write_text("C = 299_792_458.0\nH = 6.626e-34\n", "utf-8")
    params = {"fit": {"tol": 1e-6, "steps": [1, 2]}, "label": "x"}
    (repo / "pkg" / "params.json").write_text(json.dumps(params), encoding="utf-8")
    (repo / "pkg" / "settings.toml").write_text("[fit]\nmax_iter = 100\n", encoding="utf-8")
    (repo / "data" / "calibration.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (repo / "science.toml").write_text(ZONES, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def check(repo: Path, allow: list[str] | None = None) -> science.ProtectedReport:
    return science.check_protected(repo, "HEAD", science.load_science(repo), allow=allow or [])


@needs_git
def test_protected_unchanged_values_pass(protected_repo: Path) -> None:
    constants = protected_repo / "pkg" / "constants.py"
    constants.write_text("# reformatted\nC = 299792458.0\nH = 6.626_0e-34\nX = 1\n", "utf-8")
    report = check(protected_repo)
    assert report.status == "passed" and report.changes == []
    assert report.summary() == "6 protected value(s) unchanged"
    assert report.report() == ""


@needs_git
def test_protected_changes_in_every_file_type(protected_repo: Path) -> None:
    (protected_repo / "pkg" / "constants.py").write_text("C = 3e8\n", encoding="utf-8")
    params = {"fit": {"tol": 1e-6, "steps": [1, 5]}, "label": "changed"}
    (protected_repo / "pkg" / "params.json").write_text(json.dumps(params), encoding="utf-8")
    (protected_repo / "pkg" / "settings.toml").write_text("[fit]\nmax_iter = 50\n", "utf-8")
    (protected_repo / "data" / "calibration.csv").write_text("x,y\n1,3\n", encoding="utf-8")
    report = check(protected_repo)
    found = {(c["file"], c["name"]): (c["before"], c["after"], c["kind"]) for c in report.changes}
    assert found[("pkg/constants.py", "C")] == (299792458.0, 3e8, "changed")
    assert found[("pkg/constants.py", "H")] == (6.626e-34, None, "removed")
    assert found[("pkg/params.json", "fit.steps.1")] == (2, 5, "changed")
    assert found[("pkg/settings.toml", "fit.max_iter")] == (100, 50, "changed")
    before, after, kind = found[("data/calibration.csv", "*")]
    assert kind == "changed" and before.startswith("blob ") and before != after
    assert len(found) == 5 and report.status == "failed"  # fit.tol did not change
    text = report.report()
    assert "- pkg/constants.py: C changed from 299792458.0 to 300000000.0" in text
    assert "- pkg/constants.py: H was removed" in text
    assert "- data/calibration.csv: the whole file changed from 'blob " in text
    assert "`ssw run --allow-protected data/calibration.csv`" in text  # first change listed
    git(protected_repo, "checkout", "--", "data", "pkg/params.json", "pkg/settings.toml")
    (protected_repo / "pkg" / "constants.py").write_text("C = 3e8\nH = 6.626e-34\n", "utf-8")
    text = check(protected_repo).report()
    assert "`ssw run --allow-protected C` (or `--allow-protected pkg/constants.py:C`)" in text


@needs_git
def test_protected_allow_list_forms(protected_repo: Path) -> None:
    (protected_repo / "pkg" / "constants.py").write_text("C = 3e8\nH = 6.626e-34\n", "utf-8")
    (protected_repo / "data" / "calibration.csv").write_text("x,y\n1,3\n", encoding="utf-8")
    assert check(protected_repo, ["H", "data/calibration.csv"]).status == "failed"
    for allow in (
        ["C", "data/calibration.csv"],
        [".\\pkg\\constants.py:C", "./data/calibration.csv:*"],
        [" pkg/constants.py:C ", "data/calibration.csv"],
    ):
        report = check(protected_repo, allow)
        assert report.status == "passed", (allow, report.report())
        assert all(c["allowed"] for c in report.changes) and len(report.changes) == 2
        assert "2 change(s) allowed by the task" in report.summary()


@needs_git
def test_dropping_a_zone_does_not_unprotect_it(protected_repo: Path) -> None:
    (protected_repo / "science.toml").write_text("", encoding="utf-8")
    (protected_repo / "pkg" / "constants.py").write_text("C = 3e8\nH = 6.626e-34\n", "utf-8")
    report = check(protected_repo)
    assert report.status == "failed"
    assert [(c["file"], c["name"]) for c in report.changes] == [("pkg/constants.py", "C")]


@needs_git
def test_protected_errors_and_skips(protected_repo: Path) -> None:
    (protected_repo / "pkg" / "constants.py").write_text("C = (\n", encoding="utf-8")
    report = check(protected_repo)
    assert report.status == "error" and "cannot be parsed" in report.errors[0]
    assert "Error:" in report.report()
    unknown = science.check_protected(protected_repo, "no-such-revision", None)
    assert unknown.status == "error" and "cannot resolve" in unknown.summary()
    skipped = science.check_protected(protected_repo, None, science.load_science(protected_repo))
    assert (
        skipped.status == "skipped" and skipped.summary() == "no base revision to compare against"
    )
    git(protected_repo, "checkout", "--", "pkg/constants.py")
    (protected_repo / "science.toml").write_text(
        ZONES + '[[protected]]\nfile = "data/other.csv"\nnames = ["x"]\n', encoding="utf-8"
    )
    report = check(protected_repo)
    assert report.status == "error" and "named values need" in " ".join(report.errors)


@needs_git
def test_protected_added_values_need_permission(protected_repo: Path) -> None:
    (protected_repo / "science.toml").write_text(
        ZONES.replace('names = ["C", "H"]', 'names = ["C", "H", "G"]'), encoding="utf-8"
    )
    (protected_repo / "pkg" / "constants.py").write_text(
        "C = 299_792_458.0\nH = 6.626e-34\nG = 6.674e-11\n", "utf-8"
    )
    (change,) = check(protected_repo).changes
    assert (change["name"], change["kind"], change["before"], change["after"]) == (
        "G",
        "added",
        None,
        6.674e-11,
    )


# --------------------------------------------------------------------------------------------
# Reproducibility and notebooks
# --------------------------------------------------------------------------------------------


def test_reproducibility_record_outside_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "uv.lock").write_bytes(b"version = 1\r\n[[package]]\r\n")
    (repo / "requirements-dev.txt").write_bytes(b"pytest\n")
    config = science.parse_science("[reproducibility]\nseeds = [0, 42]\n")
    record = science.reproducibility_record(repo, config)
    assert record["git_commit"] is None and record["git_dirty"] is None
    assert record["seeds"] == [0, 42]
    lf = science.hashlib.sha256(b"version = 1\n[[package]]\n").hexdigest()
    assert record["lockfiles"]["uv.lock"] == lf  # CRLF and LF checkouts hash the same
    assert set(record["lockfiles"]) == {"uv.lock", "requirements-dev.txt"}
    assert {"python", "implementation", "platform", "machine"} <= set(record)


@needs_git
def test_reproducibility_record_in_git(protected_repo: Path) -> None:
    record = science.reproducibility_record(protected_repo, None, python=sys.executable)
    assert record["git_commit"] == git(protected_repo, "rev-parse", "HEAD").strip()
    assert record["git_dirty"] is False
    assert record["python"] == sys.version.split()[0]
    assert record["packages"]["count"] > 0 and len(record["packages"]["sha256"]) == 64
    (protected_repo / "new.txt").write_text("x", encoding="utf-8")
    assert science.reproducibility_record(protected_repo)["git_dirty"] is True


def test_paired_notebooks(tmp_path: Path) -> None:
    (tmp_path / "nb").mkdir()
    for name in ("nb/a.py", "nb/a.ipynb", "nb/b.py", "c.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    changed = ["nb/a.py", "nb/b.py", "c.py", "nb/a.ipynb"]
    assert science.paired_notebooks(tmp_path, changed) == []  # no jupytext.toml
    (tmp_path / "jupytext.toml").write_text('formats = "ipynb,py:percent"\n', encoding="utf-8")
    assert science.paired_notebooks(tmp_path, changed) == ["nb/a.py"]
    assert science.paired_notebooks(tmp_path, None) == []


def test_sync_notebooks_without_jupytext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = commands.Tools(sys.executable, tmp_path, dict(os.environ), {"modules": {}})
    monkeypatch.setattr(commands.Tools, "has", lambda self, module: False)
    monkeypatch.setattr(commands.Tools, "which", lambda self, program: None)
    result = science.sync_notebooks(tmp_path, ["a.py"], tools=tools, timeout=30)
    assert result["note"] == "jupytext not installed; paired notebooks were not updated"
    assert science.sync_notebooks(tmp_path, [], tools=tools, timeout=30) == {}


def test_sync_notebooks_runs_update_never_sync(tmp_path: Path) -> None:
    fake = tmp_path / "fake"
    (fake / "jupytext").mkdir(parents=True)
    (fake / "jupytext" / "__init__.py").write_text("", encoding="utf-8")
    (fake / "jupytext" / "__main__.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "Path('argv.json').write_text(json.dumps(sys.argv[1:]))\n"
        "sys.exit(1 if 'bad.py' in sys.argv else 0)\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "PYTHONPATH": str(fake)}
    tools = commands.Tools(sys.executable, repo, env)
    result = science.sync_notebooks(repo, ["nb.py"], tools=tools, timeout=60)
    assert result["updated"] == ["nb.py"] and result["failed"] == []
    assert json.loads((repo / "argv.json").read_text()) == ["--to", "ipynb", "--update", "nb.py"]
    result = science.sync_notebooks(repo, ["bad.py"], tools=tools, timeout=60)
    assert [f["script"] for f in result["failed"]] == ["bad.py"]
