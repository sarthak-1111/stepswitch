"""Model spectra on a grid: python -m peakmodel <input.json> <output.json>.

The input file holds the positions ("x": a list, or nested lists for several spectra) and the
peaks ("peaks", each with a "center", "width" and "height"). The output file holds the model
at every position ("y"), nested like x.
"""

import json
import sys
from pathlib import Path

import numpy as np

from peakmodel.model import gaussian_sum


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m peakmodel <input.json> <output.json>")
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    peaks = data["peaks"]
    y = gaussian_sum(
        np.asarray(data["x"], dtype=float),
        [peak["center"] for peak in peaks],
        [peak["width"] for peak in peaks],
        [peak["height"] for peak in peaks],
    )
    result = {"y": np.asarray(y, dtype=float).tolist()}
    Path(argv[2]).write_text(json.dumps(result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
