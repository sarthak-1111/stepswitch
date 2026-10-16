"""Evidence files written by claim tests (SPEC §18.2).

Each `claims/evidence/<id>.json` holds the claim wording and thresholds, the environment, the
source digest it was produced from, the command that reproduces it and one section per test.
Sections from an older source digest are dropped, so a file never mixes runs of different code.
Evidence is committed, so the writer replaces the home directory, the checkout path, the user
name and the host name with placeholders (working rule 5).
"""

from __future__ import annotations

import getpass
import json
import os
import re
import socket
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from stepswitch import __version__
from stepswitch.prove.digest import source_digest
from stepswitch.prove.runner import MODE_ENV, platform_info

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "claims" / "evidence"


def claim(claim_id: str) -> dict[str, Any]:
    registry = YAML(typ="safe", pure=True).load(
        (ROOT / "claims" / "claims.yaml").read_text(encoding="utf-8")
    )
    for entry in registry["claims"]:
        if entry["id"] == claim_id:
            return dict(entry)
    raise KeyError(claim_id)


def thresholds(claim_id: str) -> dict[str, Any]:
    return dict(claim(claim_id)["thresholds"])


def prove_mode() -> str:
    return os.environ.get(MODE_ENV, "quick")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read(claim_id: str) -> dict[str, Any] | None:
    path = EVIDENCE_DIR / f"{claim_id}.json"
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _placeholders() -> list[tuple[re.Pattern[str], str]]:
    """(pattern, placeholder) pairs for machine-specific values, longest paths first."""
    paths = {
        str(ROOT): "<checkout>",
        ROOT.as_posix(): "<checkout>",
        str(Path(tempfile.gettempdir())): "<tmp>",
        Path(tempfile.gettempdir()).as_posix(): "<tmp>",
        str(Path.home()): "~",
        Path.home().as_posix(): "~",
    }
    pairs = [
        (re.compile(re.escape(value), re.IGNORECASE), placeholder)
        for value, placeholder in sorted(paths.items(), key=lambda item: -len(item[0]))
        if len(value) >= 4
    ]
    names = {socket.gethostname(): "<host>"}
    user = getpass.getuser()
    if not user.isalpha():  # plain alphabetic names (CI defaults) are ordinary words
        names[user] = "<user>"
    for value, placeholder in names.items():
        if len(value) >= 4 and value.lower() != "localhost":
            pattern = rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])"
            pairs.append((re.compile(pattern, re.IGNORECASE), placeholder))
    return pairs


def scrub(node: Any, pairs: list[tuple[re.Pattern[str], str]] | None = None) -> Any:
    """`node` with machine-specific values in every string replaced by placeholders."""
    pairs = _placeholders() if pairs is None else pairs
    if isinstance(node, dict):
        return {scrub(key, pairs): scrub(value, pairs) for key, value in node.items()}
    if isinstance(node, list | tuple):
        return [scrub(item, pairs) for item in node]
    if isinstance(node, str):
        for pattern, placeholder in pairs:
            node = pattern.sub(placeholder, node)
    return node


def write(claim_id: str, section: str, data: dict[str, Any], *, command: str | None = None) -> Path:
    """Merge one test's section into the claim's evidence file and return its path."""
    entry = claim(claim_id)
    digest = source_digest(ROOT)
    path = EVIDENCE_DIR / f"{claim_id}.json"
    current = read(claim_id) or {}
    sections = current.get("sections", {}) if current.get("source_digest") == digest else {}
    sections[section] = scrub({"recorded": now(), **json.loads(json.dumps(data, default=str))})
    evidence = {
        "claim": claim_id,
        "text": entry["text"],
        "scope": entry["scope"],
        "thresholds": entry["thresholds"],
        "command": command or f"uv run ssw prove --claim {claim_id}",
        "mode": prove_mode(),
        "source_digest": digest,
        "stepswitch_version": __version__,
        "environment": platform_info(),
        "updated": now(),
        "sections": sections,
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    return path
