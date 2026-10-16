"""Amide band areas of spectra: python -m bands <input.json> <output.json>.

The input file holds the wavenumber grid in cm^-1 ("x") and absorbance spectra on that grid,
one per row ("spectra"). The output file holds the band names ("bands") and, for each
spectrum, the area of each band ("areas", one row per spectrum).
"""

import json
import sys
from pathlib import Path

import numpy as np

from bands.integrate import band_area
from bands.windows import BANDS


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m bands <input.json> <output.json>")
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    spectra = np.asarray(data["spectra"], dtype=float)
    areas = [band_area(data["x"], spectra, window) for window in BANDS.values()]
    result = {"bands": list(BANDS), "areas": np.column_stack(areas).tolist()}
    Path(argv[2]).write_text(json.dumps(result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
