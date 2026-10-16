import numpy as np
import pytest
from numpy.testing import assert_allclose
from smoothing.filters import moving_average

PEAK = np.exp(-0.5 * ((np.arange(201.0) - 100.0) / 6.0) ** 2)


def test_a_straight_line_is_kept_away_from_the_ends():
    y = np.arange(20.0)
    assert_allclose(moving_average(y, 5)[2:-2], y[2:-2], rtol=1e-12)


def test_the_ends_repeat_the_first_and_the_last_sample():
    smoothed = moving_average([0.0, 1.0, 2.0, 3.0, 4.0], 3)
    assert_allclose(smoothed, [1 / 3, 1.0, 2.0, 3.0, 11 / 3], rtol=1e-12)


def test_a_symmetric_peak_keeps_its_position_and_its_symmetry():
    smoothed = moving_average(PEAK, 9)
    assert np.argmax(smoothed) == 100
    assert_allclose(smoothed[70:100], smoothed[130:100:-1], rtol=1e-12)


def test_the_area_is_kept():
    assert moving_average(PEAK, 9).sum() == pytest.approx(PEAK.sum(), rel=1e-12)


def test_a_window_of_one_changes_nothing():
    y = np.array([3.0, 1.0, 4.0, 1.0, 5.0])
    assert_allclose(moving_average(y, 1), y)


def test_the_length_is_kept():
    assert moving_average(np.ones(10), 7).shape == (10,)


@pytest.mark.parametrize("window", [0, 4, -3])
def test_the_window_must_be_a_positive_odd_number(window):
    with pytest.raises(ValueError):
        moving_average(np.ones(10), window)
