"""Areas under sampled curves by the trapezoid rule."""

import numpy as np
from numpy.typing import ArrayLike, NDArray


def trapezoid_weights(x: ArrayLike) -> NDArray[np.float64]:
    """Weights w such that sum(w * y) is the trapezoid-rule integral of y over the grid x.

    The grid must be increasing, but its spacing may vary.
    """
    grid = np.asarray(x, dtype=float)
    if grid.ndim != 1 or grid.size < 2:
        raise ValueError("the grid needs at least two points")
    if np.any(np.diff(grid) <= 0):
        raise ValueError("the grid must be increasing")
    weights = np.empty_like(grid)
    weights[1:-1] = (grid[2:] - grid[:-2]) / 2
    weights[0] = grid[1] - grid[0]
    weights[-1] = grid[-1] - grid[-2]
    return weights


def integrate(y: ArrayLike, x: ArrayLike) -> float | NDArray[np.float64]:
    """The trapezoid-rule integral of y over the grid x.

    y may hold several curves on the same grid, one per row: then each row is integrated.
    """
    values = np.asarray(y, dtype=float)
    weights = trapezoid_weights(x)
    if values.shape[-1:] != weights.shape:
        raise ValueError("y needs one value per grid point along its last axis")
    return values @ weights


def band_area(
    x: ArrayLike, y: ArrayLike, window: tuple[float, float]
) -> float | NDArray[np.float64]:
    """The area of the band between window[0] and window[1], both included, on the grid x."""
    grid = np.asarray(x, dtype=float)
    low, high = window
    inside = (grid >= low) & (grid <= high)
    if inside.sum() < 2:
        raise ValueError(f"fewer than two grid points between {low} and {high}")
    return integrate(np.asarray(y, dtype=float)[..., inside], grid[inside])
