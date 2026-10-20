"""Resample a spectrum onto a uniform grid: python -m regrid <input.json> <output.json>.

The input file holds the measured points ("x", "y") and the grid ("start", "stop", "step").
The output file holds the grid ("x") and the resampled values ("y").
"""

import json
import sys
from pathlib import Path

from regrid.grid import uniform_grid
from regrid.resample import resample


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m regrid <input.json> <output.json>")
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    grid = uniform_grid(data["start"], data["stop"], data["step"])
    values = resample(data["x"], data["y"], grid)
    result = {"x": grid.tolist(), "y": values.tolist()}
    Path(argv[2]).write_text(json.dumps(result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
