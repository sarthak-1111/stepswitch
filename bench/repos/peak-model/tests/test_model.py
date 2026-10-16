import math

import numpy as np
import pytest
from numpy.testing import assert_allclose
from peakmodel.model import gaussian_sum

CENTERS = [480.0, 520.0, 610.0]
WIDTHS = [12.0, 8.0, 20.0]
HEIGHTS = [0.8, 1.0, 0.35]


def reference(position):
    """The model at one position, term by term."""
    return sum(
        height * math.exp(-0.5 * ((position - center) / width) ** 2)
        for center, width, height in zip(CENTERS, WIDTHS, HEIGHTS, strict=True)
    )


def expected(positions):
    grid = np.asarray(positions, dtype=float)
    return np.array([reference(position) for position in grid.ravel()]).reshape(grid.shape)


def test_a_line_of_positions():
    x = np.linspace(400.0, 700.0, 301)
    assert_allclose(gaussian_sum(x, CENTERS, WIDTHS, HEIGHTS), expected(x), rtol=1e-10, atol=1e-12)


def test_the_result_is_an_array_shaped_like_x():
    y = gaussian_sum(np.linspace(400.0, 700.0, 301), CENTERS, WIDTHS, HEIGHTS)
    assert isinstance(y, np.ndarray)
    assert y.shape == (301,)


def test_a_two_dimensional_grid():
    x = np.linspace(400.0, 700.0, 60).reshape(4, 15)
    y = gaussian_sum(x, CENTERS, WIDTHS, HEIGHTS)
    assert np.shape(y) == (4, 15)
    assert_allclose(y, expected(x), rtol=1e-10, atol=1e-12)


def test_a_three_dimensional_grid():
    x = np.linspace(450.0, 650.0, 30).reshape(2, 3, 5)
    y = gaussian_sum(x, CENTERS, WIDTHS, HEIGHTS)
    assert np.shape(y) == (2, 3, 5)
    assert_allclose(y, expected(x), rtol=1e-10, atol=1e-12)


def test_a_single_position():
    y = gaussian_sum(520.0, CENTERS, WIDTHS, HEIGHTS)
    assert np.shape(y) == ()
    assert float(y) == pytest.approx(reference(520.0), rel=1e-12)


def test_peak_parameters_may_be_arrays():
    x = np.linspace(400.0, 700.0, 31)
    y = gaussian_sum(x, np.array(CENTERS), np.array(WIDTHS), np.array(HEIGHTS))
    assert_allclose(y, expected(x), rtol=1e-10, atol=1e-12)


def test_without_peaks_the_model_is_zero():
    y = gaussian_sum(np.zeros((3, 4)), [], [], [])
    assert np.shape(y) == (3, 4)
    assert not np.any(y)


@pytest.mark.parametrize(
    ("centers", "widths", "heights"),
    [([500.0], [0.0], [1.0]), ([500.0], [-2.0], [1.0]), ([500.0, 510.0], [5.0], [1.0, 1.0])],
)
def test_invalid_peaks_are_rejected(centers, widths, heights):
    with pytest.raises(ValueError):
        gaussian_sum(np.linspace(400.0, 700.0, 7), centers, widths, heights)
