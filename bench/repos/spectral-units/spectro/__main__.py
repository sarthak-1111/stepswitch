"""Raman wavelengths for a laser line: python -m spectro <input.json> <output.json>.

The input file holds the wavelength of the laser in nm ("laser") and Raman shifts in cm^-1
("shift"). The output file holds the shifts, and for each of them the wavelength in nm
("wavelength") and the photon energy in eV ("energy") of the scattered light.
"""

import json
import sys
from pathlib import Path

from spectro.convert import raman_wavelength, wavelength_to_energy


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m spectro <input.json> <output.json>")
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    wavelength = raman_wavelength(data["shift"], data["laser"])
    result = {
        "shift": data["shift"],
        "wavelength": wavelength.tolist(),
        "energy": wavelength_to_energy(wavelength).tolist(),
    }
    Path(argv[2]).write_text(json.dumps(result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
