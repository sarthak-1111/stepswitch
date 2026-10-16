import numpy as np
import pytest
from baseline.fit import linear_baseline, subtract_baseline
from baseline.regions import edge_mask
from numpy.testing import assert_allclose

X = np.linspace(200.0, 1800.0, 801)
PEAKS = np.exp(-0.5 * ((X - 700.0) / 12.0) ** 2) + 0.6 * np.exp(-0.5 * ((X - 1300.0) / 20.0) ** 2)
SLOPED = 0.15 + 2.5e-4 * X


def test_edge_mask_selects_both_ends():
    assert edge_mask(8, 2).tolist() == [True, True, False, False, False, False, True, True]


def test_edge_mask_selects_the_same_number_of_samples_at_each_end():
    mask = edge_mask(100, 10)
    assert mask.sum() == 20
    assert mask[:10].all()
    assert mask[-10:].all()


@pytest.mark.parametrize("points", [0, 5])
def test_edge_mask_needs_room_for_both_ends(points):
    with pytest.raises(ValueError):
        edge_mask(9, points)


def test_a_sloped_baseline_is_found():
    assert_allclose(linear_baseline(X, PEAKS + SLOPED, points=40), SLOPED, rtol=0, atol=1e-9)


def test_the_corrected_spectrum_keeps_only_the_peaks():
    assert_allclose(subtract_baseline(X, PEAKS + SLOPED, points=40), PEAKS, rtol=0, atol=1e-9)


def test_the_default_number_of_points():
    assert_allclose(subtract_baseline(X, PEAKS + SLOPED), PEAKS, rtol=0, atol=1e-9)


def test_a_spectrum_without_baseline_is_unchanged():
    assert_allclose(subtract_baseline(X, PEAKS, points=40), PEAKS, rtol=0, atol=1e-9)


def test_x_and_y_must_match():
    with pytest.raises(ValueError):
        linear_baseline(X, PEAKS[:-1])
