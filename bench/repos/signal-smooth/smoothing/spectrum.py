"""Smoothing of spectra sampled on an even grid."""

import numpy as np
from numpy.typing import ArrayLike, NDArray

from smoothing.filters import moving_average


def window_samples(x: ArrayLike, width: float) -> int:
    """The moving-average window, in samples, for a smoothing width in the units of x.

    The width divided by the grid step is rounded to a whole number of samples, and one more
    sample is added when that number is even.
    """
    grid = np.asarray(x, dtype=float)
    if grid.ndim != 1 or grid.size < 2:
        raise ValueError("the grid needs at least two points")
    step = abs(grid[-1] - grid[0]) / (grid.size - 1)
    samples = max(round(width / step), 1)
    return samples if samples % 2 == 1 else samples + 1


def smooth(x: ArrayLike, y: ArrayLike, width: float) -> NDArray[np.float64]:
    """The spectrum y on the even grid x, smoothed by a moving average about `width` wide."""
    return moving_average(y, window_samples(x, width))
