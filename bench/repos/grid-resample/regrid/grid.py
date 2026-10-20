"""Uniform grids."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def uniform_grid(start: float, stop: float, step: float) -> NDArray[np.float64]:
    """Points from `start` to `stop`, both included, `step` apart."""
    if step <= 0:
        raise ValueError("step must be positive")
    if stop < start:
        raise ValueError("stop must not be below start")
    count = int(round((stop - start) / step)) + 1
    return start + step * np.arange(count, dtype=float)
