import numpy as np
import pytest
from bands.integrate import band_area, integrate, trapezoid_weights
from bands.windows import AMIDE_I
from numpy.testing import assert_allclose


def test_weights_on_an_even_grid():
    assert_allclose(trapezoid_weights([0.0, 1.0, 2.0, 3.0]), [0.5, 1.0, 1.0, 0.5])


def test_weights_on_an_uneven_grid():
    assert_allclose(trapezoid_weights([0.0, 1.0, 3.0]), [0.5, 1.5, 1.0])


def test_the_area_under_a_constant():
    assert integrate(np.full(11, 3.0), np.linspace(0.0, 2.0, 11)) == pytest.approx(6.0, rel=1e-12)


def test_straight_lines_are_integrated_exactly():
    # The integral of 2 x + 1 from 1 to 4 is (16 + 4) - (1 + 1) = 18.
    x = np.array([1.0, 1.5, 2.5, 4.0])
    assert integrate(2.0 * x + 1.0, x) == pytest.approx(18.0, rel=1e-12)


def test_the_area_of_a_gaussian():
    x = np.linspace(-10.0, 10.0, 2001)
    assert integrate(np.exp(-0.5 * x**2), x) == pytest.approx(np.sqrt(2.0 * np.pi), rel=1e-9)


def test_each_row_is_a_curve():
    x = np.linspace(0.0, 1.0, 5)
    curves = np.vstack([np.ones(5), x, 4.0 * x])
    assert_allclose(integrate(curves, x), [1.0, 0.5, 2.0], rtol=1e-12)


def test_band_area_uses_the_grid_points_inside_the_window():
    x = np.arange(0.0, 11.0)
    assert band_area(x, np.ones_like(x), (2.0, 6.0)) == pytest.approx(4.0, rel=1e-12)


def test_amide_band_on_a_sloped_background():
    # 0.1 + 0.001 (x - 1600) from 1600 to 1700 cm^-1: 0.1 * 100 + 0.001 * 100**2 / 2 = 15.
    x = np.arange(1500.0, 1800.0, 2.0)
    y = 0.1 + 0.001 * (x - 1600.0)
    assert band_area(x, y, AMIDE_I) == pytest.approx(15.0, rel=1e-12)


@pytest.mark.parametrize("x", [[0.0, 1.0, 1.0, 2.0], [2.0, 1.0, 0.0], [1.0]])
def test_the_grid_must_increase(x):
    with pytest.raises(ValueError):
        trapezoid_weights(x)
