import numpy as np
import pytest
from numpy.testing import assert_allclose
from peaks.centroid import centroid, centroids, window


def gaussian(x, center, width, height=1.0):
    return height * np.exp(-0.5 * ((x - center) / width) ** 2)


def test_each_position_is_weighted_by_its_intensity():
    # (1 * 1 + 2 * 1 + 3 * 2) / (1 + 1 + 2)
    assert centroid([1.0, 2.0, 3.0], [1.0, 1.0, 2.0]) == pytest.approx(2.25, rel=1e-12)


def test_a_symmetric_peak_has_its_centroid_at_its_center():
    x = np.linspace(500.0, 540.0, 401)
    assert centroid(x, gaussian(x, 520.0, 3.0)) == pytest.approx(520.0, abs=1e-9)


def test_a_peak_with_a_shoulder():
    # Two Gaussians of equal area (height times width): the centroid lies halfway between them.
    x = np.linspace(480.0, 570.0, 1801)
    y = gaussian(x, 520.0, 2.0, 1.0) + gaussian(x, 530.0, 4.0, 0.5)
    assert centroid(x, y) == pytest.approx(525.0, abs=1e-9)


def test_the_intensity_scale_does_not_matter():
    x = np.array([10.0, 11.0, 12.0, 13.0])
    y = np.array([0.2, 1.0, 0.7, 0.1])
    assert centroid(x, 1000.0 * y) == pytest.approx(centroid(x, y), rel=1e-12)


def test_window_keeps_the_samples_near_the_center():
    x = np.arange(10.0)
    positions, intensities = window(x, 10.0 * x, center=4.0, half_width=1.5)
    assert_allclose(positions, [3.0, 4.0, 5.0])
    assert_allclose(intensities, [30.0, 40.0, 50.0])


def test_centroids_of_two_bands():
    # The first band has a shoulder of half its area: (3 * 450 + 1.5 * 456) / 4.5 = 452.
    x = np.linspace(400.0, 700.0, 3001)
    y = gaussian(x, 450.0, 3.0) + gaussian(x, 456.0, 3.0, 0.5) + gaussian(x, 600.0, 5.0, 0.8)
    assert_allclose(centroids(x, y, [450.0, 600.0], half_width=40.0), [452.0, 600.0], atol=1e-9)


def test_a_peak_without_intensity_is_rejected():
    with pytest.raises(ValueError):
        centroid([1.0, 2.0, 3.0], [0.0, 0.0, 0.0])


def test_positions_and_intensities_must_match():
    with pytest.raises(ValueError):
        centroid([1.0, 2.0, 3.0], [1.0, 2.0])


def test_an_empty_window_is_rejected():
    with pytest.raises(ValueError):
        window([1.0, 2.0, 3.0], [1.0, 1.0, 1.0], center=10.0, half_width=1.0)
