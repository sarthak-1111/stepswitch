"""Line widths for a list of lines: python -m widths <input.json> <output.json>.

The input file holds each line's Gaussian standard deviation ("sigma") and Lorentzian half
width ("gamma"), in cm^-1. The output file holds the full widths at half maximum of the
Gaussian part ("gaussian"), the Lorentzian part ("lorentzian") and the Voigt profile ("voigt").
"""

import json
import sys
from pathlib import Path

from widths.shapes import gaussian_fwhm, lorentzian_fwhm, voigt_fwhm


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m widths <input.json> <output.json>")
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    sigma, gamma = data["sigma"], data["gamma"]
    result = {
        "gaussian": gaussian_fwhm(sigma).tolist(),
        "lorentzian": lorentzian_fwhm(gamma).tolist(),
        "voigt": voigt_fwhm(sigma, gamma).tolist(),
    }
    Path(argv[2]).write_text(json.dumps(result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
