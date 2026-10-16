"""Centroids of the peaks in a sampled spectrum."""

from collections.abc import Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray


def centroid(x: ArrayLike, y: ArrayLike) -> float:
    """The centroid of one peak: the mean of its positions x, weighted by its intensities y."""
    positions = np.asarray(x, dtype=float)
    intensities = np.asarray(y, dtype=float)
    if positions.ndim != 1 or positions.shape != intensities.shape:
        raise ValueError("x and y must be one-dimensional and of the same length")
    weights = intensities**2
    total = weights.sum()
    if total <= 0:
        raise ValueError("the peak has no positive intensity")
    return float(np.sum(weights * positions) / total)


def window(
    x: ArrayLike, y: ArrayLike, center: float, half_width: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """The samples whose positions lie within `half_width` of `center`."""
    positions = np.asarray(x, dtype=float)
    intensities = np.asarray(y, dtype=float)
    inside = np.abs(positions - center) <= half_width
    if not inside.any():
        raise ValueError(f"no samples within {half_width} of {center}")
    return positions[inside], intensities[inside]


def centroids(
    x: ArrayLike, y: ArrayLike, centers: Sequence[float], half_width: float
) -> NDArray[np.float64]:
    """The centroid of each peak, from the samples within `half_width` of its nominal center."""
    return np.array([centroid(*window(x, y, center, half_width)) for center in centers])
