import numpy as np
import pytest
from regrid.grid import uniform_grid
from regrid.resample import resample


def test_increasing_points() -> None:
    assert np.allclose(resample([0.0, 1.0, 2.0], [0.0, 10.0, 20.0], [0.5, 1.5]), [5.0, 15.0])


def test_decreasing_points() -> None:
    # Wavenumbers recorded from high to low.
    assert np.allclose(resample([2.0, 1.0, 0.0], [20.0, 10.0, 0.0], [0.5, 1.5]), [5.0, 15.0])
    x = np.linspace(3000.0, 100.0, 59)
    y = np.sin(x / 300.0)
    assert np.allclose(resample(x, y, [150.0, 2950.0]), resample(x[::-1], y[::-1], [150.0, 2950.0]))


def test_points_outside_the_measured_range_are_nan() -> None:
    out = resample([0.0, 1.0, 2.0], [0.0, 10.0, 20.0], [-1.0, 1.0, 3.0])
    assert np.isnan(out[0]) and out[1] == 10.0 and np.isnan(out[2])
    assert np.isnan(resample([2.0, 1.0, 0.0], [20.0, 10.0, 0.0], [2.5])[0])


def test_mismatched_shapes_are_refused() -> None:
    with pytest.raises(ValueError):
        resample([0.0, 1.0], [0.0], [0.5])


def test_uniform_grid() -> None:
    assert np.allclose(uniform_grid(100.0, 120.0, 5.0), [100.0, 105.0, 110.0, 115.0, 120.0])
    with pytest.raises(ValueError):
        uniform_grid(0.0, 1.0, 0.0)
