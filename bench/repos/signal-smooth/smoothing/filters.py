"""Smoothing filters for evenly sampled signals."""

import numpy as np
from numpy.typing import ArrayLike, NDArray


def moving_average(y: ArrayLike, window: int) -> NDArray[np.float64]:
    """Each sample replaced by the mean of the `window` samples centred on it.

    `window` must be odd. The result has the length of y: near the ends, the missing
    neighbours count as copies of the first and the last sample.
    """
    values = np.asarray(y, dtype=float)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("y must be a non-empty one-dimensional signal")
    if window < 1 or window % 2 == 0:
        raise ValueError("window must be a positive odd number of samples")
    padded = np.concatenate([np.full(window - 1, values[0]), values])
    return np.convolve(padded, np.full(window, 1.0 / window), mode="valid")
