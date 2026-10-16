"""A spectrum model: a sum of Gaussian peaks."""

import math
from collections.abc import Iterable, Sequence


def gaussian_sum(
    x: Iterable[float],
    centers: Sequence[float],
    widths: Sequence[float],
    heights: Sequence[float],
) -> list[float]:
    """The sum of the Gaussian peaks at every position in x.

    Peak k contributes heights[k] * exp(-((x - centers[k]) / widths[k]) ** 2 / 2): each width
    is a standard deviation, in the units of x.
    """
    if len(widths) != len(centers) or len(heights) != len(centers):
        raise ValueError("centers, widths and heights must have the same length")
    if any(width <= 0 for width in widths):
        raise ValueError("peak widths must be positive")
    values = []
    for position in x:
        total = 0.0
        for center, width, height in zip(centers, widths, heights, strict=True):
            total += height * math.exp(-0.5 * ((position - center) / width) ** 2)
        values.append(total)
    return values
