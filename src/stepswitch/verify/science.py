"""Science-track checks (SPEC §6.2): golden fixtures, protected zones, reproducibility records.

`science.toml` at the repository root configures them; docs/science-toml.md is the reference.

* Golden cases: each `[[golden]]` case runs its command with placeholders filled in
  ({python}, {case} = tests/golden/<case>, {output} = a fresh temporary directory, {repo}),
  then compares every declared quantity of the output with the expected copy in
  `tests/golden/<case>/expected/`. Comparison modes and their exact metrics:

  - elementwise: flagged iff the shapes differ, any |a - e| > atol + rtol * |e|, or NaN or
    infinity appears where the other side has none (or infinities differ in sign);
  - integral: trapezoid rule over x (sample index without x): flagged iff
    |I(a) - I(e)| > atol + rtol * |I(e)|;
  - peaks: local maxima with y[i] > y[i-1], y[i] >= y[i+1] and y[i] >= min_peak_height * max(y),
    each refined by the vertex of the parabola through the three samples (offset
    0.5 * (l - r) / (l - 2m + r) times the local grid step x[i+1] - x[i]); flagged iff the peak
    counts differ or any |shift| > atol, pairing peaks in sorted order;
  - overlap: cosine similarity a.e / (|a| |e|); flagged iff it is below min_overlap.

* Protected zones: values that may not change unless the task allows it. Python files are
  compared by their module-level bindings, JSON and TOML files by dotted keys, other files as
  a whole. Zones declared at the base revision still apply when the working tree drops them.
* Reproducibility record: interpreter, platform, git commit, lockfile hashes and seeds.
* Notebook pairing: with a `jupytext.toml`, a changed script paired with an `.ipynb` is pushed
  into the notebook with `jupytext --to ipynb --update` (never `--sync`).

Nothing here touches the network.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import math
import os
import platform
import re
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from stepswitch.verify import commands, plot

SCIENCE_TOML = "science.toml"
GOLDEN_DIR = "tests/golden"
JUPYTEXT_TOML = "jupytext.toml"
Mode = Literal["elementwise", "integral", "peaks", "overlap"]
MODES: tuple[str, ...] = ("elementwise", "integral", "peaks", "overlap")
Status = Literal["passed", "failed", "error"]
DEFAULT_RTOL = 1e-7  # the default of numpy.testing.assert_allclose
DEFAULT_ATOL = 0.0
DEFAULT_MIN_OVERLAP = 0.999
DEFAULT_MIN_PEAK_HEIGHT = 0.05
LOCKFILE_PATTERNS = (
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "Pipfile.lock",
    "pylock.toml",
    "pylock.*.toml",
    "requirements*.txt",
)
PLACEHOLDERS = ("python", "case", "output", "repo")
OUTPUT_TAIL = 3000
PLOT_POINTS = 4000  # longer curves are reduced to a min/max envelope before plotting


class ScienceConfigError(ValueError):
    """science.toml is missing required fields, has unknown keys or invalid values."""


class DataError(ValueError):
    """A data file could not be read as the numbers a quantity declares."""


class ProtectedError(ValueError):
    """A protected file could not be read or parsed."""


# --------------------------------------------------------------------------------------------
# science.toml
# --------------------------------------------------------------------------------------------


def _relative(value: str, what: str) -> str:
    text = value.strip().replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or re.match(r"^[A-Za-z]:", text) or ".." in path.parts:
        raise ValueError(f"{what} must be a relative path inside the project, got {value!r}")
    return path.as_posix()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quantity(_Strict):
    """One compared output: `[[golden.quantities]]`."""

    name: str = Field(min_length=1)
    file: str = Field(min_length=1)
    key: str | None = None
    x_key: str | None = None
    mode: Mode = "elementwise"
    rtol: float = Field(default=DEFAULT_RTOL, ge=0)
    atol: float = Field(default=DEFAULT_ATOL, ge=0)
    min_overlap: float = Field(default=DEFAULT_MIN_OVERLAP, ge=-1, le=1)
    min_peak_height: float | None = Field(default=None, ge=0, le=1)
    expected: str | None = None

    @field_validator("file")
    @classmethod
    def _file(cls, value: str) -> str:
        for name in ("output", "repo", "case"):
            prefix = "{" + name + "}/"
            if value.startswith(prefix):
                return prefix + _relative(value[len(prefix) :], "file")
        return _relative(value, "file")

    @field_validator("expected")
    @classmethod
    def _expected(cls, value: str | None) -> str | None:
        return None if value is None else _relative(value, "expected")

    @property
    def data_key(self) -> str:
        return self.key or self.name

    @property
    def expected_path(self) -> str:
        """The expected copy, relative to the case directory."""
        if self.expected:
            return self.expected
        if self.file.startswith("{"):
            return f"expected/{PurePosixPath(self.file).name}"
        return f"expected/{self.file}"


class GoldenCase(_Strict):
    """`[[golden]]`: a reference run whose outputs are compared with expected copies."""

    case: str
    command: list[str] = Field(min_length=1)
    quantities: list[Quantity] = Field(default_factory=list)
    timeout: float | None = Field(default=None, gt=0)

    @field_validator("case")
    @classmethod
    def _case(cls, value: str) -> str:
        return _relative(value, "case")


class ProtectedZone(_Strict):
    """`[[protected]]`: values in `file` that may not change; no names = the whole file."""

    file: str
    names: list[str] = Field(default_factory=list)

    @field_validator("file")
    @classmethod
    def _file(cls, value: str) -> str:
        return _relative(value, "file")


class ScienceSettings(_Strict):
    python: str | None = None
    min_peak_height: float = Field(default=DEFAULT_MIN_PEAK_HEIGHT, ge=0, le=1)


class Reproducibility(BaseModel):
    """`[reproducibility]`: recorded with every golden run (extra keys are recorded too)."""

    model_config = ConfigDict(extra="allow")

    seeds: list[int] = Field(default_factory=list)


class ScienceConfig(_Strict):
    science: ScienceSettings = Field(default_factory=ScienceSettings)
    golden: list[GoldenCase] = Field(default_factory=list)
    protected: list[ProtectedZone] = Field(default_factory=list)
    reproducibility: Reproducibility = Field(default_factory=Reproducibility)


def _location(loc: Sequence[int | str]) -> str:
    text = ""
    for part in loc:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else str(part))
    return text or "(top level)"


def parse_science(data: Mapping[str, Any] | str, *, source: str = SCIENCE_TOML) -> ScienceConfig:
    """Validate science.toml content (TOML text or parsed tables)."""
    if isinstance(data, str):
        try:
            data = tomllib.loads(data)
        except tomllib.TOMLDecodeError as exc:
            raise ScienceConfigError(f"{source}: invalid TOML: {exc}") from exc
    try:
        return ScienceConfig.model_validate(dict(data))
    except ValidationError as exc:
        problems = "; ".join(f"{_location(e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ScienceConfigError(f"{source}: {problems}") from exc


def load_science(repo: Path) -> ScienceConfig | None:
    """The repository's science.toml, or None when it has none."""
    path = repo / SCIENCE_TOML
    if not path.is_file():
        return None
    return parse_science(path.read_text(encoding="utf-8-sig"))


# --------------------------------------------------------------------------------------------
# Data files
# --------------------------------------------------------------------------------------------


def lookup(document: Any, dotted: str) -> tuple[bool, Any]:
    """(found, value) for a key in nested tables; `a.b.0` indexes lists. Exact keys win."""
    if isinstance(document, Mapping) and dotted in document:
        return True, document[dotted]
    node = document
    for part in dotted.split("."):
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False, None
    return True, node


def _numeric(value: Any, what: str) -> NDArray[Any]:
    try:
        array = np.asarray(value)
    except (ValueError, TypeError) as exc:
        raise DataError(f"{what}: not a rectangular array of numbers ({exc})") from exc
    kind = array.dtype.kind
    if kind in "biuf":
        return array.astype(np.float64)
    if kind == "c":
        return array.astype(np.complex128)
    if kind == "O":  # JSON null stands for a missing value
        try:
            return np.array(value, dtype=np.float64)
        except (ValueError, TypeError) as exc:
            raise DataError(f"{what}: values are not all numbers ({exc})") from exc
    raise DataError(f"{what}: values are not numbers (dtype {array.dtype})")


def _csv_column(path: Path, key: str) -> list[float]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        lines.pop(0)
    rows = [row for row in csv.reader(io.StringIO("\n".join(lines))) if any(c.strip() for c in row)]
    if not rows:
        raise DataError(f"{path.name}: no header row")
    header = [cell.strip() for cell in rows[0]]
    if key not in header:
        raise DataError(f"{path.name}: no column {key!r} (columns: {', '.join(header)})")
    column = header.index(key)
    values: list[float] = []
    for number, row in enumerate(rows[1:], start=2):
        cell = row[column].strip() if column < len(row) else ""
        try:
            values.append(float(cell) if cell else math.nan)
        except ValueError:
            message = f"{path.name}: row {number}, column {key!r}: {cell!r} is not a number"
            raise DataError(message) from None
    return values


def read_array(path: Path, key: str, *, keyed: bool = False) -> NDArray[Any]:
    """Numbers stored under `key` in a .json, .npz, .npy or .csv file.

    A bare JSON list or a .npy file holds one array and ignores `key`, unless `keyed` is set
    (x axes need a named entry).
    """
    if not path.is_file():
        raise DataError(f"{path.name}: file not found")
    suffix = path.suffix.lower()
    what = f"{path.name}[{key}]"
    value: Any
    try:
        if suffix == ".json":
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(document, Mapping):
                found, value = lookup(document, key)
                if not found:
                    keys = ", ".join(str(k) for k in list(document)[:12])
                    raise DataError(f"{path.name}: no key {key!r} (keys: {keys})")
            elif keyed:
                raise DataError(f"{path.name}: holds a bare list, so it has no key {key!r}")
            else:
                value = document
        elif suffix == ".npz":
            with np.load(path, allow_pickle=False) as archive:
                if key not in archive.files:
                    names = ", ".join(archive.files)
                    raise DataError(f"{path.name}: no array {key!r} (arrays: {names})")
                value = archive[key]
        elif suffix == ".npy":
            if keyed:
                raise DataError(f"{path.name}: a .npy file holds one array, so it has no {key!r}")
            value = np.load(path, allow_pickle=False)
        elif suffix == ".csv":
            value = _csv_column(path, key)
        else:
            raise DataError(f"{path.name}: unsupported data file (use .json, .npz, .npy or .csv)")
    except DataError:
        raise
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise DataError(f"{path.name}: cannot read: {exc}") from exc
    return _numeric(value, what)


# --------------------------------------------------------------------------------------------
# Comparisons
# --------------------------------------------------------------------------------------------


@dataclass
class Comparison:
    """One quantity compared with its expected copy.

    `metric` is the mode's decisive measurement (see the module docstring) and `ratio` the same
    deviation in units of the tolerance: above 1 means beyond tolerance.
    """

    case: str = ""
    quantity: str = ""
    mode: str = ""
    file: str = ""
    key: str = ""
    status: Status = "passed"
    max_abs: float | None = None
    max_rel: float | None = None
    metric: float | None = None
    metric_name: str = ""
    ratio: float | None = None
    tolerance: str = ""
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def label(self) -> str:
        return f"{self.case}/{self.quantity}" if self.case else self.quantity

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "passed": self.passed}


def _num(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "-"
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return f"{value:.{digits}g}"


def deviation(expected: NDArray[Any], actual: NDArray[Any]) -> tuple[float | None, float | None]:
    """(max |a - e|, max |a - e| / |e| over e != 0) when the shapes match, else (None, None).

    Equal values (including NaN against NaN) count as no deviation; NaN against a number counts
    as an infinite one.
    """
    if expected.shape != actual.shape:
        return None, None
    if expected.size == 0:
        return 0.0, None
    with np.errstate(all="ignore"):
        same = (actual == expected) | (np.isnan(actual) & np.isnan(expected))
        diff = np.where(same, 0.0, np.abs(actual - expected))
        diff = np.where(np.isnan(diff), np.inf, diff)
        scale = np.abs(expected)
        usable = (scale > 0) & np.isfinite(scale)
        max_rel = float(np.max(diff[usable] / scale[usable])) if usable.any() else None
    return float(np.max(diff)), max_rel


def _units(measured: float, allowed: float) -> float:
    """`measured` in units of `allowed` (0 when both are 0, infinite beyond a zero tolerance)."""
    if math.isnan(measured):
        return math.inf
    if allowed > 0:
        return measured / allowed
    return 0.0 if measured == 0 else math.inf


def _elementwise(
    result: Comparison, e: NDArray[Any], a: NDArray[Any], rtol: float, atol: float
) -> None:
    result.metric_name = "max |a - e| / (atol + rtol * |e|)"
    result.tolerance = f"|a - e| <= {atol:g} + {rtol:g} * |e|"
    if e.shape != a.shape:
        result.status = "failed"
        result.metric = result.ratio = math.inf
        result.detail = f"shape {list(a.shape)} differs from the expected {list(e.shape)}"
        return
    if e.size == 0:
        result.metric = result.ratio = 0.0
        return
    with np.errstate(all="ignore"):
        same = (a == e) | (np.isnan(a) & np.isnan(e))
        diff = np.where(same, 0.0, np.abs(a - e))
        allowed = atol + rtol * np.abs(e)
        special = (np.isnan(a) ^ np.isnan(e)) | ((np.isinf(a) | np.isinf(e)) & ~same)
        beyond = special | (diff > allowed)
        units = np.where(allowed > 0, diff / allowed, np.where(diff > 0, np.inf, 0.0))
        units = np.where(special | np.isnan(units), np.inf, units)
    worst = int(np.argmax(units))
    result.metric = result.ratio = float(units.flat[worst])
    count = int(np.count_nonzero(beyond))
    if count:
        index = np.unravel_index(worst, e.shape)
        where = index[0] if len(index) == 1 else tuple(int(i) for i in index)
        result.status = "failed"
        result.detail = (
            f"{count} of {e.size} values beyond tolerance; worst at index {where}: "
            f"expected {e.flat[worst]:.9g}, actual {a.flat[worst]:.9g}"
        )


def _check_axis(y: NDArray[Any], x: NDArray[Any] | None, side: str) -> None:
    if y.ndim != 1:
        raise DataError(f"{side} data must be one-dimensional for this mode, got shape {y.shape}")
    if x is not None and x.shape != y.shape:
        raise DataError(f"{side} x axis has shape {x.shape} but the data has {y.shape}")


def _integral(
    result: Comparison,
    e: NDArray[Any],
    a: NDArray[Any],
    rtol: float,
    atol: float,
    xe: NDArray[Any] | None,
    xa: NDArray[Any] | None,
) -> None:
    _check_axis(e, xe, "expected")
    result.metric_name = "|I(a) - I(e)|"
    try:
        _check_axis(a, xa, "actual")
    except DataError as exc:
        result.status, result.detail = "failed", str(exc)
        result.metric = result.ratio = math.inf
        return
    ie = np.trapezoid(e, xe) if xe is not None else np.trapezoid(e)
    ia = np.trapezoid(a, xa) if xa is not None else np.trapezoid(a)
    measured = float(abs(ia - ie))
    allowed = atol + rtol * float(abs(ie))
    result.metric = measured
    result.ratio = _units(measured, allowed)
    result.tolerance = f"|I(a) - I(e)| <= {atol:g} + {rtol:g} * |I(e)| = {allowed:.3g}"
    result.detail = f"I(expected) = {ie:.9g}, I(actual) = {ia:.9g}"
    result.extra = {
        "integral_expected": np.asarray(ie).item(),
        "integral_actual": np.asarray(ia).item(),
    }
    if not measured <= allowed:
        result.status = "failed"


def peak_positions(
    y: NDArray[Any], x: NDArray[Any] | None = None, min_height: float = DEFAULT_MIN_PEAK_HEIGHT
) -> list[float]:
    """Local maxima above `min_height * max(y)`, refined by a parabola through three samples."""
    values = np.asarray(y, dtype=np.float64)
    if values.size < 3 or np.isnan(values).all():
        return []
    axis = None if x is None else np.asarray(x, dtype=np.float64)
    threshold = min_height * float(np.nanmax(values))  # equals max(y) when there is no NaN
    left, mid, right = values[:-2], values[1:-1], values[2:]
    with np.errstate(invalid="ignore"):
        candidates = np.nonzero((mid > left) & (mid >= right) & (mid >= threshold))[0] + 1
    positions: list[float] = []
    for i in candidates.tolist():
        lo, top, hi = values[i - 1], values[i], values[i + 1]
        denominator = lo - 2.0 * top + hi
        offset = 0.5 * (lo - hi) / denominator if denominator != 0 else 0.0
        if axis is None:
            positions.append(float(i + offset))
        else:
            positions.append(float(axis[i] + offset * (axis[i + 1] - axis[i])))
    return positions


def _peaks(
    result: Comparison,
    e: NDArray[Any],
    a: NDArray[Any],
    atol: float,
    min_height: float,
    xe: NDArray[Any] | None,
    xa: NDArray[Any] | None,
) -> None:
    _check_axis(e, xe, "expected")
    if np.iscomplexobj(e):
        raise DataError("peaks mode needs real numbers")
    result.metric_name = "max |peak shift|"
    result.tolerance = f"same peak count (>= {min_height:g} x max), |shift| <= {atol:g}"
    try:
        _check_axis(a, xa, "actual")
        if np.iscomplexobj(a):
            raise DataError("actual data is complex; peaks mode needs real numbers")
    except DataError as exc:
        result.status, result.detail = "failed", str(exc)
        result.metric = result.ratio = math.inf
        return
    found_e = sorted(peak_positions(e, xe, min_height))
    found_a = sorted(peak_positions(a, xa, min_height))
    result.extra = {"expected_peaks": found_e[:50], "actual_peaks": found_a[:50]}
    if np.isnan(a).any() and not np.isnan(e).any():
        result.status, result.detail = "failed", "the actual data contains NaN"
        result.metric = result.ratio = math.inf
        return
    if len(found_e) != len(found_a):
        result.status = "failed"
        result.metric = result.ratio = math.inf
        result.detail = f"expected {len(found_e)} peaks, found {len(found_a)}"
        return
    shifts = [q - p for p, q in zip(found_e, found_a, strict=True)]
    worst = max(range(len(shifts)), key=lambda i: abs(shifts[i]), default=None)
    measured = abs(shifts[worst]) if worst is not None else 0.0
    result.metric = measured
    result.ratio = _units(measured, atol)
    if worst is not None:
        result.detail = (
            f"{len(shifts)} peaks; largest shift {shifts[worst]:+.6g} at {found_e[worst]:.9g}"
        )
    else:
        result.detail = "no peaks above the height threshold on either side"
    if not measured <= atol:
        result.status = "failed"


def _overlap(result: Comparison, e: NDArray[Any], a: NDArray[Any], min_overlap: float) -> None:
    if np.iscomplexobj(e):
        raise DataError("overlap mode needs real numbers")
    result.metric_name = "cosine similarity"
    result.tolerance = f"cosine similarity >= {min_overlap:g}"
    if e.shape != a.shape or np.iscomplexobj(a):
        result.status = "failed"
        result.metric, result.ratio = None, math.inf
        result.detail = (
            "actual data is complex; overlap mode needs real numbers"
            if np.iscomplexobj(a)
            else f"shape {list(a.shape)} differs from the expected {list(e.shape)}"
        )
        return
    ev, av = e.ravel(), a.ravel()
    with np.errstate(all="ignore"):
        norm_e, norm_a = float(np.linalg.norm(ev)), float(np.linalg.norm(av))
        if norm_e == 0 and norm_a == 0:
            similarity = 1.0
        elif norm_e == 0 or norm_a == 0:
            similarity = 0.0
        else:
            similarity = float(np.dot(av, ev) / (np.linalg.norm(av) * np.linalg.norm(ev)))
    result.metric = similarity
    deficit = 1.0 - similarity
    result.ratio = max(_units(deficit, 1.0 - min_overlap), 0.0)
    result.detail = f"similarity {similarity:.9f}"
    if not similarity >= min_overlap:
        result.status = "failed"


def compare(
    mode: str,
    expected: NDArray[Any],
    actual: NDArray[Any],
    *,
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
    min_peak_height: float = DEFAULT_MIN_PEAK_HEIGHT,
    x_expected: NDArray[Any] | None = None,
    x_actual: NDArray[Any] | None = None,
) -> Comparison:
    """Compare `actual` with `expected` in one mode (definitions in the module docstring).

    Raises DataError when the expected data cannot be compared in this mode (a setup problem);
    problems on the actual side give a failed comparison instead.
    """
    e, a = np.asarray(expected), np.asarray(actual)
    result = Comparison(mode=mode)
    result.max_abs, result.max_rel = deviation(e, a)
    if mode == "elementwise":
        _elementwise(result, e, a, rtol, atol)
    elif mode == "integral":
        _integral(result, e, a, rtol, atol, x_expected, x_actual)
    elif mode == "peaks":
        _peaks(result, e, a, atol, min_peak_height, x_expected, x_actual)
    elif mode == "overlap":
        _overlap(result, e, a, min_overlap)
    else:
        raise DataError(f"unknown comparison mode {mode!r} (use {', '.join(MODES)})")
    return result


def measured_text(comparison: Comparison) -> str:
    if comparison.status == "error":
        return "-"
    ratio = f" ({_num(comparison.ratio)} x tol)" if comparison.ratio is not None else ""
    names = {"integral": "dI", "peaks": "shift", "overlap": "similarity"}
    if comparison.mode in names and comparison.metric is not None:
        digits = 9 if comparison.mode == "overlap" else 3
        return f"{names[comparison.mode]} {_num(comparison.metric, digits)}{ratio}"
    return f"{_num(comparison.ratio)} x tol" if comparison.ratio is not None else "-"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def markdown_table(comparisons: Sequence[Comparison]) -> str:
    """Max abs/rel deviation per quantity (SPEC §6.2 numerical diff report)."""
    lines = [
        "| case | quantity | mode | max abs dev | max rel dev | measured | tolerance | result |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for c in comparisons:
        result = {"passed": "pass", "failed": "FAIL", "error": "ERROR"}[c.status]
        cells = [
            c.case,
            c.quantity,
            c.mode,
            _num(c.max_abs),
            _num(c.max_rel),
            measured_text(c),
            c.tolerance or "-",
            result,
        ]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# Golden runs
# --------------------------------------------------------------------------------------------


@dataclass
class CaseResult:
    case: str
    status: Status
    command: list[str]
    returncode: int | None = None
    seconds: float = 0.0
    error: str = ""
    output: str = ""
    comparisons: list[Comparison] = field(default_factory=list)
    plots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["command"] = commands.display(self.command)
        data["comparisons"] = [c.to_dict() for c in self.comparisons]
        return data


@dataclass
class GoldenReport:
    """Golden cases run, compared and (optionally) plotted; the numerical diff report."""

    cases: list[CaseResult] = field(default_factory=list)
    reproducibility: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    artifacts: str | None = None

    @property
    def comparisons(self) -> list[Comparison]:
        return [c for case in self.cases for c in case.comparisons]

    @property
    def status(self) -> Status:
        statuses = {case.status for case in self.cases}
        if "failed" in statuses:
            return "failed"
        return "error" if "error" in statuses else "passed"

    @property
    def table(self) -> str:
        return markdown_table(self.comparisons)

    def summary(self) -> str:
        comparisons = self.comparisons
        bad = [c for c in comparisons if not c.passed]
        broken = [case for case in self.cases if case.error]
        if not bad and not broken:
            return f"{len(comparisons)} quantities within tolerance in {len(self.cases)} case(s)"
        parts = [f"{case.case}: {case.error}" for case in broken]
        parts += [f"{c.label} {c.status} ({measured_text(c)})" for c in bad[:4]]
        more = f" and {len(bad) - 4} more" if len(bad) > 4 else ""
        return "; ".join(parts) + more

    def failure_report(self, limit: int = 1800) -> str:
        if self.status == "passed":
            return ""
        lines = ["Golden comparisons (science.toml, tests/golden/) did not pass."]
        bad = [c for c in self.comparisons if not c.passed]
        if bad:
            lines += ["", markdown_table(bad), ""]
            lines += [f"- {c.label}: {c.detail}" for c in bad if c.detail]
        for case in self.cases:
            if case.status != "passed":
                lines.append(f"Case {case.case}: {commands.display(case.command)}")
                if case.error:
                    lines.append(f"  {case.error}")
                if case.output and case.error:
                    lines.append(case.output[-600:])
        text = "\n".join(lines)
        return text if len(text) <= limit else text[: limit - 20] + "\n... (truncated)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "cases": [case.to_dict() for case in self.cases],
            "table": self.table,
            "plots": [p for case in self.cases for p in case.plots],
            "reproducibility": self.reproducibility,
            "notes": list(self.notes),
            "artifacts": self.artifacts,
        }

    def markdown(self) -> str:
        record = json.dumps(self.reproducibility, indent=2, default=str)
        return (
            f"# Numerical diff report\n\nStatus: {self.status}\n\n{self.table}\n\n"
            f"## Reproducibility\n\n```json\n{record}\n```\n"
        )


def golden_env(repo: Path, env: Mapping[str, str]) -> dict[str, str]:
    """`env` with the repository root first on PYTHONPATH."""
    merged = dict(env)
    existing = merged.get("PYTHONPATH")
    merged["PYTHONPATH"] = str(repo) + (os.pathsep + existing if existing else "")
    return merged


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "quantity"


def envelope_index(y: NDArray[Any], points: int) -> NDArray[Any]:
    """Indices of a min/max envelope of about `points` samples (narrow peaks survive)."""
    if y.size <= points:
        return np.arange(y.size)
    edges = np.linspace(0, y.size, max(points // 2, 1) + 1).astype(int)
    keep: set[int] = set()
    with np.errstate(invalid="ignore"):
        for start, stop in zip(edges[:-1], edges[1:], strict=True):
            if stop <= start:
                continue
            chunk = np.where(np.isfinite(y[start:stop]), y[start:stop], np.nan)
            if np.isnan(chunk).all():
                keep.add(int(start))
                continue
            keep.add(int(start + np.nanargmin(chunk)))
            keep.add(int(start + np.nanargmax(chunk)))
    return np.asarray(sorted(keep), dtype=int)


def _plot(
    path: Path,
    comparison: Comparison,
    e: NDArray[Any],
    a: NDArray[Any],
    xe: NDArray[Any] | None,
    xa: NDArray[Any] | None,
) -> Path | None:
    """Overlay of expected and actual (plus the difference on a shared grid); 1-D real only."""
    if e.ndim != 1 or a.ndim != 1 or np.iscomplexobj(e) or np.iscomplexobj(a):
        return None
    axis_e = xe if xe is not None else np.arange(e.size, dtype=np.float64)
    axis_a = xa if xa is not None else np.arange(a.size, dtype=np.float64)
    if e.shape == a.shape and np.array_equal(axis_e, axis_a):
        keep = np.union1d(envelope_index(e, PLOT_POINTS), envelope_index(a, PLOT_POINTS))
        x_exp = x_act = axis_e[keep]
        e_plot, a_plot = e[keep], a[keep]
    else:
        keep_e, keep_a = envelope_index(e, PLOT_POINTS), envelope_index(a, PLOT_POINTS)
        x_exp, e_plot = axis_e[keep_e], e[keep_e]
        x_act, a_plot = axis_a[keep_a], a[keep_a]
    note = f"{comparison.mode}: {comparison.tolerance}; measured {measured_text(comparison)}"
    return plot.overlay_png(
        path,
        e_plot.tolist(),
        a_plot.tolist(),
        x_expected=x_exp.tolist(),
        x_actual=x_act.tolist(),
        title=f"{comparison.label} ({comparison.status})",
        x_label="x" if xe is not None else "sample index",
        notes=[note],
    )


def _compare_quantity(
    quantity: Quantity,
    case: GoldenCase,
    case_dir: Path,
    output: Path,
    values: Mapping[str, str],
    settings: ScienceSettings,
    plots: Path | None,
) -> tuple[Comparison, str | None]:
    base = Comparison(
        case=case.case,
        quantity=quantity.name,
        mode=quantity.mode,
        file=quantity.file,
        key=quantity.data_key,
    )
    expected_path = case_dir / quantity.expected_path
    if quantity.file.startswith("{"):
        actual_path = Path(commands.expand([quantity.file], values)[0])
    else:
        actual_path = output / quantity.file
    try:
        e = read_array(expected_path, quantity.data_key)
        xe = read_array(expected_path, quantity.x_key, keyed=True) if quantity.x_key else None
    except DataError as exc:
        base.status, base.detail = "error", f"expected copy {quantity.expected_path}: {exc}"
        return base, None
    try:
        a = read_array(actual_path, quantity.data_key)
        xa = read_array(actual_path, quantity.x_key, keyed=True) if quantity.x_key else None
    except DataError as exc:
        base.status, base.detail = "failed", f"output {quantity.file}: {exc}"
        return base, None
    height = (
        quantity.min_peak_height
        if quantity.min_peak_height is not None
        else settings.min_peak_height
    )
    try:
        measured = compare(
            quantity.mode,
            e,
            a,
            rtol=quantity.rtol,
            atol=quantity.atol,
            min_overlap=quantity.min_overlap,
            min_peak_height=height,
            x_expected=xe,
            x_actual=xa,
        )
    except DataError as exc:
        base.status, base.detail = "error", f"{quantity.mode} mode: {exc}"
        return base, None
    for item in fields(measured):
        if item.name not in ("case", "quantity", "mode", "file", "key"):
            setattr(base, item.name, getattr(measured, item.name))
    drawn = None
    if plots is not None:
        target = plots / f"{_safe_name(case.case)}--{_safe_name(quantity.name)}.png"
        written = _plot(target, base, e, a, xe, xa)
        drawn = str(written) if written else None
    return base, drawn


def _run_case(
    repo: Path,
    case: GoldenCase,
    settings: ScienceSettings,
    *,
    python: str,
    env: Mapping[str, str],
    timeout: float,
    plots: Path | None,
) -> CaseResult:
    case_dir = repo / GOLDEN_DIR / case.case
    if not case_dir.is_dir():
        return CaseResult(
            case.case, "error", list(case.command), error=f"{GOLDEN_DIR}/{case.case}/ not found"
        )
    with tempfile.TemporaryDirectory(prefix="ssw-golden-", ignore_cleanup_errors=True) as tmp:
        output = Path(tmp)
        values = {"python": python, "case": str(case_dir), "output": str(output), "repo": str(repo)}
        argv = commands.expand(case.command, values)
        run = commands.run(argv, cwd=repo, timeout=case.timeout or timeout, env=env)
        result = CaseResult(case.case, "passed", argv, run.returncode, run.seconds)
        if not run.ok:
            result.status = "failed" if run.started else "error"
            if run.start_error:
                result.error = run.start_error
            elif run.timed_out:
                result.error = f"the command timed out after {case.timeout or timeout:g} s"
            else:
                result.error = f"the command exited with code {run.returncode}"
            result.output = run.output[-OUTPUT_TAIL:]
            return result
        for quantity in case.quantities:
            comparison, drawn = _compare_quantity(
                quantity, case, case_dir, output, values, settings, plots
            )
            result.comparisons.append(comparison)
            if drawn:
                result.plots.append(drawn)
        statuses = {c.status for c in result.comparisons}
        if "failed" in statuses:
            result.status = "failed"
        elif "error" in statuses:
            result.status = "error"
        if result.status != "passed":
            result.output = run.output[-OUTPUT_TAIL:]
    return result


def run_golden(
    repo: Path,
    config: ScienceConfig,
    *,
    python: str,
    artifacts: Path | None = None,
    timeout: float = 900.0,
    env: Mapping[str, str] | None = None,
) -> GoldenReport:
    """Run every golden case and compare its quantities (plots go to `artifacts` if given)."""
    base_env = env if env is not None else commands.project_env(python)
    run_env = golden_env(repo, base_env)
    commands.purge_stale_bytecode(repo)  # the cases must run the code as it is on disk
    report = GoldenReport(artifacts=str(artifacts) if artifacts else None)
    for case in config.golden:
        report.cases.append(
            _run_case(
                repo,
                case,
                config.science,
                python=python,
                env=run_env,
                timeout=timeout,
                plots=artifacts,
            )
        )
        if not case.quantities:
            report.notes.append(
                f"case {case.case} declares no quantities: only its exit code counts"
            )
    report.reproducibility = reproducibility_record(repo, config, python=python, env=base_env)
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "golden-report.md").write_text(report.markdown(), encoding="utf-8")
    return report


# --------------------------------------------------------------------------------------------
# Protected zones
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtectedValue:
    """A protected value: `shown` for reports, `key` for comparison."""

    shown: Any
    key: tuple[Any, ...]


def values_equal(a: Any, b: Any) -> bool:
    """Equality of parsed values: numbers by value (1 == 1.0), containers by type and items."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, int | float | complex) and isinstance(b, int | float | complex):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return True
        return a == b
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return (
            type(a) is type(b)
            and len(a) == len(b)
            and all(values_equal(x, y) for x, y in zip(a, b, strict=True))
        )
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and bool(a == b)


def _same(before: ProtectedValue | None, after: ProtectedValue | None) -> bool:
    if before is None or after is None:
        return before is after
    if before.key[0] == after.key[0] == "literal":
        return values_equal(before.key[1], after.key[1])
    return before.key == after.key


def _declared_globals(body: Sequence[ast.AST]) -> set[str]:
    """Names a function body declares `global`, not counting nested scopes."""
    found: set[str] = set()
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Global):
            found.update(node.names)
        elif not isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
        ):
            stack.extend(ast.iter_child_nodes(node))
    return found


class _Bindings(ast.NodeVisitor):
    """Every statement that binds one of `names` at module scope, in source order.

    Covers assignments in any compound statement, augmented assignments, imports (a star
    import may bind anything), for/with/except/match targets, walrus, del, def and class, and
    assignments inside functions that declare the name `global`.
    """

    def __init__(self, names: set[str]) -> None:
        self.names = names
        self.sites: dict[str, list[tuple[ast.AST, ast.expr | None]]] = {n: [] for n in names}
        self.scope_globals: set[str] | None = None  # None at module scope

    def bind(self, name: str, node: ast.AST, value: ast.expr | None = None) -> None:
        if name in self.names and (self.scope_globals is None or name in self.scope_globals):
            self.sites[name].append((node, value))

    def bind_target(self, target: ast.expr, node: ast.AST, value: ast.expr | None) -> None:
        if isinstance(target, ast.Name):
            self.bind(target.id, node, value)
        elif isinstance(target, ast.Tuple | ast.List):
            values: list[ast.expr] | None = None
            if isinstance(value, ast.Tuple | ast.List) and len(value.elts) == len(target.elts):
                values = list(value.elts)
                if any(isinstance(e, ast.Starred) for e in [*target.elts, *values]):
                    values = None
            for index, element in enumerate(target.elts):
                self.bind_target(element, node, values[index] if values else None)
        elif isinstance(target, ast.Starred):
            self.bind_target(target.value, node, None)

    def _scope(self, body: Sequence[ast.AST], declared: set[str]) -> None:
        saved = self.scope_globals
        self.scope_globals = declared
        for statement in body:
            self.visit(statement)
        self.scope_globals = saved

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self.bind_target(target, node, node.value)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.bind_target(node.target, node, node.value)
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.bind_target(node.target, node, None)
        self.visit(node.value)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self.bind_target(node.target, node, None)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_withitem(self, node: ast.withitem) -> None:
        if node.optional_vars is not None:
            self.bind_target(node.optional_vars, node, None)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bind(alias.asname or alias.name.split(".")[0], node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                for name in self.names:
                    self.bind(name, node)
            else:
                self.bind(alias.asname or alias.name, node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.bind_target(node.target, node, None)
        self.visit(node.value)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.bind_target(target, node, None)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bind(node.name, node)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.bind(node.name, node)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.bind(node.name, node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.bind(node.rest, node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.bind(node.name, node)
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        self._scope(node.body, _declared_globals(node.body))

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self._scope([node.body], set())

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bind(node.name, node)
        for child in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(child)
        self._scope(node.body, _declared_globals(node.body))


def _shorten(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def python_values(source: bytes | str, names: Sequence[str]) -> dict[str, ProtectedValue]:
    """Values of module-level names in Python source (SyntaxError when it does not parse).

    One plain assignment of a literal gives the literal (so 6.626_070_15e-34 equals
    6.62607015e-34); one assignment of another expression gives its normalized AST; several
    bindings, or any other kind, give all of them, so a later rebinding is a change.
    """
    tree = ast.parse(source)
    finder = _Bindings(set(names))
    finder.visit(tree)
    values: dict[str, ProtectedValue] = {}
    for name in names:
        sites = finder.sites[name]
        if not sites:
            continue
        node, value = sites[0]
        if len(sites) == 1 and value is not None and isinstance(node, ast.Assign | ast.AnnAssign):
            try:
                literal = ast.literal_eval(value)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                values[name] = ProtectedValue(
                    _shorten(ast.unparse(value)), ("expr", ast.dump(value))
                )
            else:
                values[name] = ProtectedValue(literal, ("literal", literal))
            continue
        shown = "; ".join(_shorten(ast.unparse(site)) for site, _ in sites)
        values[name] = ProtectedValue(
            _shorten(shown, 400), ("bindings", *(ast.dump(s) for s, _ in sites))
        )
    return values


def document_values(data: bytes, suffix: str, names: Sequence[str]) -> dict[str, ProtectedValue]:
    """Values at dotted keys of a JSON or TOML document (ValueError when it does not parse)."""
    text = data.decode("utf-8-sig")
    document = json.loads(text) if suffix == ".json" else tomllib.loads(text)
    values: dict[str, ProtectedValue] = {}
    for name in names:
        found, value = lookup(document, name)
        if found:
            values[name] = ProtectedValue(value, ("literal", value))
    return values


def _extract(data: bytes, rel: str, names: Sequence[str], where: str) -> dict[str, ProtectedValue]:
    suffix = PurePosixPath(rel).suffix.lower()
    try:
        if suffix == ".py":
            return python_values(data, names)
        return document_values(data, suffix, names)
    except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
        raise ProtectedError(f"{rel} ({where}) cannot be parsed: {exc}") from exc


def _shown(value: ProtectedValue | None) -> Any:
    if value is None:
        return None
    shown = value.shown
    if isinstance(shown, tuple | set | frozenset):
        return [_plain(v) for v in shown]
    return _plain(shown)


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return repr(value)


@dataclass
class ProtectedReport:
    """Protected values compared between the base revision and the working tree."""

    base: str | None
    commit: str | None = None
    zones: list[dict[str, Any]] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def disallowed(self) -> list[dict[str, Any]]:
        return [c for c in self.changes if not c["allowed"]]

    @property
    def status(self) -> Literal["passed", "failed", "error", "skipped"]:
        if self.disallowed:
            return "failed"
        if self.errors:
            return "error"
        if self.commit is None or not self.zones:
            return "skipped"
        return "passed"

    def summary(self) -> str:
        if self.base is None:
            return "no base revision to compare against"
        if self.errors and not self.disallowed:
            return self.errors[0]
        if not self.zones:
            return "no protected zones declared in science.toml"
        count = sum(max(len(z["names"]), 1) for z in self.zones)
        if self.disallowed:
            names = ", ".join(f"{c['file']}:{c['name']}" for c in self.disallowed[:5])
            return f"{len(self.disallowed)} protected value(s) changed without permission: {names}"
        allowed = [c for c in self.changes if c["allowed"]]
        if allowed:
            return (
                f"{count} protected value(s) checked; {len(allowed)} change(s) allowed by the task"
            )
        return f"{count} protected value(s) unchanged"

    def report(self) -> str:
        if not self.disallowed and not self.errors:
            return ""
        lines: list[str] = []
        if self.disallowed:
            lines.append(
                "Protected values changed (science.toml [[protected]]: physical constants, "
                "calibration values, fitting defaults):"
            )
            for change in self.disallowed:
                what = "the whole file" if change["name"] == "*" else change["name"]
                lines.append(f"- {change['file']}: {what} {_describe(change)}")
            first = self.disallowed[0]
            if first["name"] == "*":
                permission = f"`ssw run --allow-protected {first['file']}`"
            else:
                permission = (
                    f"`ssw run --allow-protected {first['name']}` "
                    f"(or `--allow-protected {first['file']}:{first['name']}`)"
                )
            lines.append(
                "Restore these values. If the task really must change one, rerun it with "
                f"explicit permission, for example {permission}."
            )
        lines += [f"Error: {error}" for error in self.errors]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"status": self.status}


def _describe(change: Mapping[str, Any]) -> str:
    if change["kind"] == "added":
        return f"was added with value {change['after']!r}"
    if change["kind"] == "removed":
        return f"was removed (it was {change['before']!r})"
    return f"changed from {change['before']!r} to {change['after']!r}"


def _normalize_allow(entries: Sequence[str]) -> set[str]:
    allowed: set[str] = set()
    for entry in entries:
        text = entry.strip().replace("\\", "/")
        if ":" in text:
            file, name = text.rsplit(":", 1)
            while file.startswith("./"):
                file = file[2:]
            allowed.add(f"{file}:{name.strip()}")
        else:
            while text.startswith("./"):
                text = text[2:]
            allowed.add(text)
    return allowed


def _is_allowed(file: str, name: str, allowed: set[str]) -> bool:
    if name == "*":
        return file in allowed or f"{file}:*" in allowed
    return name in allowed or f"{file}:{name}" in allowed


def _zone_changes(repo: Path, commit: str, rel: str, names: Sequence[str]) -> list[dict[str, Any]]:
    if not names:  # the whole file
        old_blob = commands.blob_at(repo, commit, rel)
        new_blob = commands.blob_in_worktree(repo, rel)
        if old_blob == new_blob:
            return []
        kind = "added" if old_blob is None else "removed" if new_blob is None else "changed"
        shown = {
            "before": old_blob and f"blob {old_blob[:12]}",
            "after": new_blob and f"blob {new_blob[:12]}",
        }
        return [{"file": rel, "name": "*", **shown, "kind": kind}]
    suffix = PurePosixPath(rel).suffix.lower()
    if suffix not in (".py", ".json", ".toml"):
        raise ProtectedError(
            f"{rel}: named values need a .py, .json or .toml file; "
            "leave out `names` to protect the whole file"
        )
    base_data = commands.file_at(repo, commit, rel)
    path = repo / rel
    before = _extract(base_data, rel, names, f"at {commit[:12]}") if base_data is not None else {}
    after = _extract(path.read_bytes(), rel, names, "working tree") if path.is_file() else {}
    changes: list[dict[str, Any]] = []
    for name in names:
        old, new = before.get(name), after.get(name)
        if _same(old, new):
            continue
        kind = "added" if old is None else "removed" if new is None else "changed"
        changes.append(
            {"file": rel, "name": name, "before": _shown(old), "after": _shown(new), "kind": kind}
        )
    return changes


def _zones(*configs: ScienceConfig | None) -> dict[str, list[str]]:
    """file -> protected names (empty = the whole file), merged over the given configs."""
    merged: dict[str, set[str] | None] = {}
    for config in configs:
        for zone in config.protected if config else []:
            current = merged.get(zone.file, set())
            if current is None or not zone.names:
                merged[zone.file] = None
            else:
                merged[zone.file] = current | set(zone.names)
    return {file: sorted(names) if names else [] for file, names in sorted(merged.items())}


def check_protected(
    repo: Path, base: str | None, config: ScienceConfig | None, *, allow: Sequence[str] = ()
) -> ProtectedReport:
    """Compare protected values between `base` and the working tree.

    Zones are the union of the working tree's science.toml (`config`) and the base revision's,
    so dropping a zone in the same change does not unprotect it. `allow` entries match a bare
    name or `file:name` (`file` or `file:*` for a whole-file zone).
    """
    report = ProtectedReport(base=base)
    if base is None:
        report.zones = [{"file": f, "names": n} for f, n in _zones(config).items()]
        return report
    commit = commands.resolve_commit(repo, base)
    if commit is None:
        report.errors.append(f"cannot resolve the base revision {base!r} in {repo.name}")
        return report
    report.commit = commit
    base_config: ScienceConfig | None = None
    base_toml = commands.file_at(repo, commit, SCIENCE_TOML)
    if base_toml is not None:
        try:
            base_config = parse_science(base_toml.decode("utf-8-sig"), source="science.toml (base)")
        except (ScienceConfigError, UnicodeDecodeError) as exc:
            report.notes.append(f"science.toml at the base revision is unusable: {exc}")
    zones = _zones(config, base_config)
    report.zones = [{"file": f, "names": n} for f, n in zones.items()]
    allowed = _normalize_allow(allow)
    for rel, names in zones.items():
        try:
            changes = _zone_changes(repo, commit, rel, names)
        except ProtectedError as exc:
            report.errors.append(str(exc))
            continue
        for change in changes:
            change["allowed"] = _is_allowed(rel, change["name"], allowed)
            report.changes.append(change)
    return report


# --------------------------------------------------------------------------------------------
# Reproducibility and notebooks
# --------------------------------------------------------------------------------------------


def _sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def reproducibility_record(
    repo: Path,
    config: ScienceConfig | None = None,
    *,
    python: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Interpreter, platform, git commit, lockfile hashes (CRLF read as LF) and seeds."""
    if python is not None:
        info = commands.probe_interpreter(
            python, cwd=repo, env=env if env is not None else commands.project_env(python)
        )
    else:
        info = {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
        }
    lockfiles: dict[str, str] = {}
    for pattern in LOCKFILE_PATTERNS:
        for path in sorted(repo.glob(pattern)):
            if path.is_file():
                lockfiles[path.name] = _sha256_text(path)
    commit = commands.resolve_commit(repo, "HEAD")
    dirty: bool | None = None
    if commit is not None:
        status = commands.git(repo, "status", "--porcelain", "--", ".")
        if status is not None and status.returncode == 0:
            dirty = bool(status.stdout.strip())
    record: dict[str, Any] = {
        "python": info.get("python"),
        "implementation": info.get("implementation"),
        "platform": platform.platform(),
        "machine": platform.machine().lower(),
        "git_commit": commit,
        "git_dirty": dirty,
        "lockfiles": lockfiles,
        "seeds": list(config.reproducibility.seeds) if config else [],
    }
    if "packages" in info:
        record["packages"] = {
            "count": info.get("packages"),
            "sha256": info.get("packages_sha256"),
            "versions": info.get("versions", {}),
        }
    if info.get("error"):
        record["interpreter_error"] = info["error"]
    if config is not None and config.reproducibility.model_extra:
        record["settings"] = dict(config.reproducibility.model_extra)
    return record


def paired_notebooks(repo: Path, changed: Sequence[str] | None) -> list[str]:
    """Changed scripts with an `.ipynb` beside them, when the project pairs with jupytext."""
    if changed is None or not (repo / JUPYTEXT_TOML).is_file():
        return []
    return sorted(
        rel
        for rel in changed
        if rel.endswith(".py")
        and (repo / rel).is_file()
        and (repo / rel).with_suffix(".ipynb").is_file()
    )


def sync_notebooks(
    repo: Path, scripts: Sequence[str], *, tools: commands.Tools, timeout: float
) -> dict[str, Any]:
    """Push edited scripts into their notebooks: `jupytext --to ipynb --update <script>`.

    `--update` keeps the notebook's outputs; `--sync` is never used because it picks the
    source by timestamp.
    """
    if not scripts:
        return {}
    command = tools.command("jupytext")
    if command is None:
        return {
            "scripts": list(scripts),
            "updated": [],
            "failed": [],
            "note": "jupytext not installed; paired notebooks were not updated",
        }
    updated: list[str] = []
    failed: list[dict[str, str]] = []
    for script in scripts:
        argv = [*command, "--to", "ipynb", "--update", script]
        result = commands.run(argv, cwd=repo, timeout=timeout, env=tools.env)
        if result.ok:
            updated.append(script)
        else:
            failed.append(
                {"script": script, "command": result.display(), "output": result.output[-800:]}
            )
    return {"scripts": list(scripts), "updated": updated, "failed": failed}
