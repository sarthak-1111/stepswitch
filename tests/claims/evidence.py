"""Evidence files written by claim tests (SPEC §18.2).

Each `claims/evidence/<id>.json` holds the claim wording and thresholds, the environment, the
source digest it was produced from, the command that reproduces it and one section per test.
Sections from an older source digest are dropped, so a file never mixes runs of different code.
"""

from __future__ import annotations

import json
import os
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


def write(claim_id: str, section: str, data: dict[str, Any], *, command: str | None = None) -> Path:
    """Merge one test's section into the claim's evidence file and return its path."""
    entry = claim(claim_id)
    digest = source_digest(ROOT)
    path = EVIDENCE_DIR / f"{claim_id}.json"
    current = read(claim_id) or {}
    sections = current.get("sections", {}) if current.get("source_digest") == digest else {}
    sections[section] = {"recorded": now(), **data}
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
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    return path
