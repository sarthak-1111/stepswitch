"""Smoothed spectrum: python -m smoothing <input.json> <output.json>.

The input file holds a spectrum on an even grid ("x" and "y") and the smoothing width in the
units of x ("width"). The output file holds x and the smoothed spectrum ("smoothed").
"""

import json
import sys
from pathlib import Path

from smoothing.spectrum import smooth


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m smoothing <input.json> <output.json>")
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    smoothed = smooth(data["x"], data["y"], data["width"])
    result = {"x": data["x"], "smoothed": smoothed.tolist()}
    Path(argv[2]).write_text(json.dumps(result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
