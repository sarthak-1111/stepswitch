"""Property tests for scientific invariants: a template (SPEC §6.2).

Copy this file into your project's test directory (for example as tests/test_invariants.py).
Then replace each placeholder function under "Code under test" with an import of your own
function. Keep the properties that apply to your code and delete the rest. Each test states an
invariant that must hold for every valid input. Hypothesis searches for an input that breaks it
and shrinks that input to a minimal failing example.

The four patterns:

1. Normalization is conserved: after normalizing, the area (or sum) is exactly what was asked
   for, whatever the input scale and sampling.
2. Physical bounds: outputs stay in their physical range. Intensities stay non-negative, and
   smoothing never overshoots the data.
3. Monotonicity: where theory says an output only falls (or only grows) with an input, it never
   moves the other way. Example: d-spacing falls as the Bragg angle grows.
4. Symmetry: results respect a symmetry of the problem. An area does not depend on the order of
   the samples, and mirroring the axis mirrors the centroid.

Guidelines:

- Generate only physically valid inputs, such as strictly increasing axes, non-negative
  intensities and angles inside their range. Use `assume` only for rare extra conditions. Test
  invalid inputs separately and expect a clear error.
- Compare floats with a tolerance that reflects rounding, and say why in a comment. Never loosen
  a tolerance to make a failing property pass: the failure is the finding.
- The shared settings make every run use the same examples (derandomize=True), so a failure in
  CI reproduces locally. They impose no per-example time limit (deadline=None), so slow
  numerical code does not fail on timing alone. They keep no example database
  (database=None); Hypothesis may still cache constants under .hypothesis/, which it
  gitignores.

Hypothesis is a test-only dependency: add it to your project's development dependencies. This
module is skipped when it is not installed. If your project always installs it for tests,
replace the importorskip lines with plain imports.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

np = pytest.importorskip("numpy")
hypothesis = pytest.importorskip("hypothesis")
st = pytest.importorskip("hypothesis.strategies")
hnp = pytest.importorskip("hypothesis.extra.numpy")

assume = hypothesis.assume
given = hypothesis.given

# Shared settings for every property below; see the module docstring.
INVARIANTS = hypothesis.settings(derandomize=True, deadline=None, database=None, max_examples=200)

# numpy.trapezoid needs numpy 2.0 or later; older releases only have numpy.trapz.
trapezoid = getattr(np, "trapezoid", None) or np.trapz


# --- Code under test ---------------------------------------------------------------------------
# Replace each placeholder with an import of your own function, for example:
#     from mypkg.spectra import centroid, normalize_area, peak_area, smooth
#     from mypkg.diffraction import d_spacing


def peak_area(x: Any, y: Any) -> float:
    """Area under y(x) by the trapezoidal rule, whatever the order of the samples."""
    order = np.argsort(x, kind="stable")
    return float(trapezoid(y[order], x[order]))


def normalize_area(x: Any, y: Any) -> Any:
    """Scale y so that the area under y(x) is 1."""
    area = peak_area(x, y)
    if not math.isfinite(area) or area <= 0:
        raise ValueError(f"cannot normalize a spectrum with area {area}")
    return y / area


def smooth(y: Any, width: int = 5) -> Any:
    """Moving average that averages fewer samples at the edges instead of padding with zeros."""
    kernel = np.ones(width)
    total = np.convolve(y, kernel, mode="full")
    count = np.convolve(np.ones_like(y), kernel, mode="full")
    start = (width - 1) // 2
    return (total / count)[start : start + len(y)]


def d_spacing(two_theta_deg: Any, wavelength: float) -> Any:
    """Bragg's law with n = 1: d = wavelength / (2 sin(theta)), where theta is half of 2-theta."""
    theta = np.radians(two_theta_deg) / 2
    return wavelength / (2 * np.sin(theta))


def centroid(x: Any, y: Any) -> float:
    """Intensity-weighted mean position of a spectrum."""
    return float(np.sum(x * y) / np.sum(y))


# --- Inputs ------------------------------------------------------------------------------------


@st.composite
def spectra(draw: Any, min_size: int = 2, max_size: int = 200) -> tuple[Any, Any]:
    """A strictly increasing axis with non-negative intensities, as an instrument records them."""
    n = draw(st.integers(min_size, max_size))
    steps = draw(hnp.arrays(np.float64, n - 1, elements=st.floats(1e-3, 10.0)))
    start = draw(st.floats(-1e3, 1e3))
    x = start + np.concatenate(([0.0], np.cumsum(steps)))
    y = draw(hnp.arrays(np.float64, n, elements=st.floats(0.0, 1e6)))
    return x, y


# --- 1. Normalization is conserved -------------------------------------------------------------


@INVARIANTS
@given(spectra())
def test_normalized_area_is_one(spectrum: tuple[Any, Any]) -> None:
    x, y = spectrum
    assume(peak_area(x, y) > 1e-6)  # a spectrum with no signal cannot be normalized; see below
    area = peak_area(x, normalize_area(x, y))
    # Dividing each sample and integrating again costs a few rounding errors per sample.
    assert math.isclose(area, 1.0, rel_tol=1e-9)


@INVARIANTS
@given(spectra())
def test_normalizing_an_empty_spectrum_is_an_error(spectrum: tuple[Any, Any]) -> None:
    x, _ = spectrum
    with pytest.raises(ValueError):
        normalize_area(x, np.zeros_like(x))


# --- 2. Physical bounds ------------------------------------------------------------------------


@INVARIANTS
@given(spectra(), st.integers(1, 15))
def test_smoothing_stays_within_the_data(spectrum: tuple[Any, Any], width: int) -> None:
    _, y = spectrum
    smoothed = smooth(y, width)
    assert smoothed.shape == y.shape
    # An average of samples leaves [min, max] only through rounding in a sum of `width` terms.
    # Padding with zeros instead would pull the edges below the minimum and fail here.
    slack = 4 * width * np.finfo(np.float64).eps * float(np.max(np.abs(y)))
    assert np.all(smoothed >= y.min() - slack)
    assert np.all(smoothed <= y.max() + slack)


# --- 3. Monotonicity ---------------------------------------------------------------------------


@INVARIANTS
@given(
    st.floats(1.0, 179.0),
    st.floats(1.0, 179.0),
    st.floats(0.5, 2.5),  # laboratory X-ray wavelengths, in angstrom
)
def test_d_spacing_falls_as_the_angle_grows(a: float, b: float, wavelength: float) -> None:
    low, high = sorted((a, b))
    assume(low < high)
    # sin is accurate to about one unit in the last place, so allow that much reversal.
    assert d_spacing(high, wavelength) <= d_spacing(low, wavelength) * (1 + 1e-12)


# --- 4. Symmetry -------------------------------------------------------------------------------


@INVARIANTS
@given(spectra())
def test_area_does_not_depend_on_sample_order(spectrum: tuple[Any, Any]) -> None:
    x, y = spectrum
    # Many instruments record decreasing axes; numpy's trapezoidal rule on a decreasing axis
    # returns a negative area, which this property catches.
    backward = peak_area(x[::-1], y[::-1])
    # A different summation order may change the last bits, nothing more.
    assert math.isclose(backward, peak_area(x, y), rel_tol=1e-12)


@INVARIANTS
@given(spectra())
def test_mirroring_the_axis_mirrors_the_centroid(spectrum: tuple[Any, Any]) -> None:
    x, y = spectrum
    assume(np.sum(y) > 0)
    scale = float(np.max(np.abs(x)))
    assert centroid(-x, y) == pytest.approx(-centroid(x, y), rel=1e-12, abs=1e-12 * scale)
