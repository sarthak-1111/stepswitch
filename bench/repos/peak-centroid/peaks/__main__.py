"""Peak centroids of a spectrum: python -m peaks <input.json> <output.json>.

The input file holds the spectrum ("x" and "y"), the nominal centers of its peaks ("centers")
and the half-width of the window around each center ("half_width"). The output file holds the
centers and the centroid found for each of them ("centroids").
"""

import json
import sys
from pathlib import Path

from peaks.centroid import centroids


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m peaks <input.json> <output.json>")
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    found = centroids(data["x"], data["y"], data["centers"], data["half_width"])
    result = {"centers": data["centers"], "centroids": found.tolist()}
    Path(argv[2]).write_text(json.dumps(result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
