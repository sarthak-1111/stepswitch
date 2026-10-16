"""The samples of a spectrum that describe its baseline."""

import numpy as np
from numpy.typing import NDArray


def edge_mask(size: int, points: int) -> NDArray[np.bool_]:
    """A mask over `size` samples that selects the first `points` and the last `points`.

    The two ends of a spectrum hold no peaks, so they describe its baseline.
    """
    if points < 1 or 2 * points > size:
        raise ValueError(f"cannot take {points} samples from each end of {size} samples")
    mask = np.zeros(size, dtype=bool)
    mask[:points] = True
    mask[:-points] = True
    return mask
