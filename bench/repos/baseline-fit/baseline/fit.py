"""Straight-line baselines fitted to the ends of a spectrum."""

import numpy as np
from numpy.typing import ArrayLike, NDArray

from baseline.regions import edge_mask


def linear_baseline(x: ArrayLike, y: ArrayLike, points: int = 20) -> NDArray[np.float64]:
    """The least-squares straight line through the ends of the spectrum, at every x.

    The line is fitted to the first `points` and the last `points` samples of y.
    """
    positions = np.asarray(x, dtype=float)
    values = np.asarray(y, dtype=float)
    if positions.ndim != 1 or positions.shape != values.shape:
        raise ValueError("x and y must be one-dimensional and of the same length")
    mask = edge_mask(positions.size, points)
    slope, intercept = np.polyfit(positions[mask], values[mask], 1)
    return slope * positions + intercept


def subtract_baseline(x: ArrayLike, y: ArrayLike, points: int = 20) -> NDArray[np.float64]:
    """The spectrum y with its linear baseline (see linear_baseline) removed."""
    return np.asarray(y, dtype=float) - linear_baseline(x, y, points)
