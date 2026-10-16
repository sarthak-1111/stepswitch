"""The README claims section, generated from claims/claims.yaml (SPEC §18.3 item 4).

Only claims whose latest status is `passing` are listed, each with its exact registered wording
inside claim markers and a link to its evidence file. A claim's `readme_numbers` add measured
values after the link, each read from the claim's evidence file and wrapped in a `num` marker,
so the integrity lint can check it against that file (SPEC §18.3 item 3, claim C-018).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stepswitch.prove import claims as claims_module

START = "<!-- claims:start -->"
END = "<!-- claims:end -->"
EMPTY = "_No claims verified yet._"
SECTION_RE = re.compile(r"<!--\s*claims:start\s*-->.*?<!--\s*claims:end\s*-->", re.S)


def _evidence(evidence_dir: Path | None, claim_id: str) -> dict[str, Any]:
    if evidence_dir is None:
        return {}
    path = evidence_dir / f"{claim_id}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _lookup(data: Mapping[str, Any], key: str) -> Any:
    value: Any = data
    for part in key.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _numbers(claim: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[str]:
    """`label <num marker>value</num> unit` for each of the claim's README numbers that its
    evidence holds; numbers missing from the evidence are left out."""
    shown = []
    for spec in claim.get("readme_numbers") or []:
        value = _lookup(evidence, spec["key"])
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        text = f"{value:,.{int(spec.get('digits', 0))}f}"
        unit = f" {spec['unit']}" if spec.get("unit") else ""
        marker = f"<!-- num:{claim['id']}:{spec['key']} -->{text}<!-- /num -->"
        shown.append(f"{spec['label']} {marker}{unit}")
    return shown


def render_claims_section(registry: Mapping[str, Any], evidence_dir: Path | None = None) -> str:
    """The text from the start marker to the end marker, both included."""
    lines = []
    for claim in registry["claims"]:
        if claim.get("status") != "passing":
            continue
        cid = claim["id"]
        numbers = _numbers(claim, _evidence(evidence_dir, cid))
        extra = "; " + ", ".join(numbers) if numbers else ""
        lines.append(
            f"- **{cid}** <!-- claim:{cid} -->{claim['text']}<!-- /claim --> "
            f"([evidence](claims/evidence/{cid}.json){extra})"
        )
    body = "\n".join(lines) if lines else EMPTY
    return f"{START}\n{body}\n{END}"


def rendered(root: Path) -> str:
    """README.md as `update_readme` would write it now."""
    text = (root / "README.md").read_text(encoding="utf-8")
    match = SECTION_RE.search(text)
    if match is None:
        raise ValueError(f"README.md has no {START} / {END} markers")
    section = render_claims_section(claims_module.load(root), root / "claims" / "evidence")
    return text[: match.start()] + section + text[match.end() :]


def update_readme(root: Path, *, check: bool = False) -> bool:
    """Rewrite the section in README.md from the registry and the evidence files. True when it
    was (or would be) changed; with `check` the file is left alone."""
    readme = root / "README.md"
    current = readme.read_text(encoding="utf-8")
    updated = rendered(root)
    if updated == current:
        return False
    if not check:
        readme.write_text(updated, encoding="utf-8", newline="\n")
    return True
