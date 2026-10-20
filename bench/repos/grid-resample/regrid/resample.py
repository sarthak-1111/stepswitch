"""Linear resampling of a measured curve onto new points."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray


def resample(x: ArrayLike, y: ArrayLike, new_x: ArrayLike) -> NDArray[np.float64]:
    """y(x) interpolated linearly at the points `new_x`.

    `x` may be in increasing or decreasing order: spectrometers often record wavenumbers from
    high to low. A point of `new_x` outside the measured range gets NaN, since nothing was
    measured there.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    new_x = np.asarray(new_x, dtype=float)
    if x.shape != y.shape:
        raise ValueError("x and y must have the same shape")
    return np.interp(new_x, x, y)
