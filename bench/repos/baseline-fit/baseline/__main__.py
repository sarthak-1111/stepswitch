"""Baseline correction of a spectrum: python -m baseline <input.json> <output.json>.

The input file holds the spectrum ("x" and "y") and the number of samples at each end that
describe its baseline ("points"). The output file holds x, the fitted baseline ("baseline")
and the corrected spectrum ("corrected").
"""

import json
import sys
from pathlib import Path

from baseline.fit import linear_baseline, subtract_baseline


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m baseline <input.json> <output.json>")
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    x, y, points = data["x"], data["y"], data["points"]
    result = {
        "x": x,
        "baseline": linear_baseline(x, y, points).tolist(),
        "corrected": subtract_baseline(x, y, points).tolist(),
    }
    Path(argv[2]).write_text(json.dumps(result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
