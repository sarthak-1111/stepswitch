"""Checks before a release (.github/workflows/release.yml): the tag, the package version and
the version both installers pin agree, and the dashboard is built.

Usage: python scripts/release_check.py v0.1.0
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
INSTALLER_PINS = {
    "install.sh": re.compile(r'^STEPSWITCH_VERSION="([^"]+)"', re.M),
    "install.ps1": re.compile(r"^\$StepswitchVersion = '([^']+)'", re.M),
}


def problems(tag: str, root: Path = ROOT) -> list[str]:
    match = TAG_RE.match(tag)
    if match is None:
        return [f"the tag {tag!r} is not vX.Y.Z"]
    version = match.group(1)
    found: list[str] = []
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["version"] != version:
        found.append(f"pyproject.toml has version {project['version']}, the tag says {version}")
    for name, pattern in INSTALLER_PINS.items():
        pin = pattern.search((root / name).read_text(encoding="utf-8"))
        if pin is None or pin.group(1) != version:
            found.append(f"{name} pins {pin.group(1) if pin else 'no version'}, not {version}")
    if not (root / "dashboard" / "dist" / "index.html").is_file():
        found.append("the dashboard is not built (cd dashboard && npm ci && npm run build)")
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    found = problems(argv[0])
    for problem in found:
        print(f"release check: {problem}", file=sys.stderr)
    if not found:
        print(f"release check: {argv[0]} is consistent")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
