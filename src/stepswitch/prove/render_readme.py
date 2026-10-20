"""The README claims section and results table, generated from claims/claims.yaml (SPEC §18.3).

Only claims whose latest status is `passing` are listed, each with its exact registered wording
inside claim markers and a link to its evidence file. A claim's `readme_numbers` add measured
values after the link, each read from the claim's evidence file and wrapped in a `num` marker,
so the integrity lint can check it against that file (SPEC §18.3 item 3, claim C-018).

Withdrawn claims (ADR 0041) are listed after them by id, with a link to the decision.

The results table (ADR 0039) is generated the same way from the registry's `results` spec: each
cell names `<claim>:<evidence key>` and shows that value, in a `num` marker, only while the claim
is passing and its evidence holds the key; any other cell shows a dash.
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
RESULTS_START = "<!-- results:start -->"
RESULTS_END = "<!-- results:end -->"
RESULTS_RE = re.compile(r"<!--\s*results:start\s*-->.*?<!--\s*results:end\s*-->", re.S)
DASH = "—"


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


def _number(claim_id: str, key: str, evidence: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    """`<num marker>value</num> unit`, or "" when the evidence holds no number at `key`."""
    value = _lookup(evidence, key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    text = f"{value:,.{int(spec.get('digits', 0))}f}"
    unit = f" {spec['unit']}" if spec.get("unit") else ""
    return f"<!-- num:{claim_id}:{key} -->{text}<!-- /num -->{unit}"


def _numbers(claim: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[str]:
    """`label <num marker>value</num> unit` for each of the claim's README numbers that its
    evidence holds; numbers missing from the evidence are left out."""
    shown = []
    for spec in claim.get("readme_numbers") or []:
        number = _number(claim["id"], spec["key"], evidence, spec)
        if number:
            shown.append(f"{spec['label']} {number}")
    return shown


def render_claims_section(registry: Mapping[str, Any], evidence_dir: Path | None = None) -> str:
    """The text from the start marker to the end marker, both included."""
    lines = []
    for claim in registry["claims"]:
        if claim.get("status") != "passing" or claim.get("withdrawn_by"):
            continue
        cid = claim["id"]
        numbers = _numbers(claim, _evidence(evidence_dir, cid))
        extra = "; " + ", ".join(numbers) if numbers else ""
        lines.append(
            f"- **{cid}** <!-- claim:{cid} -->{claim['text']}<!-- /claim --> "
            f"([evidence](claims/evidence/{cid}.json){extra})"
        )
    body = "\n".join(lines) if lines else EMPTY
    gone = claims_module.withdrawn(registry)
    if gone:
        # Withdrawn claims stay visible by id, without their wording (ADR 0041).
        body += "\n\nWithdrawn: " + ", ".join(
            f"**{cid}** ([why]({decision}))" for cid, decision in gone.items()
        )
    return f"{START}\n{body}\n{END}"


def render_results_section(registry: Mapping[str, Any], evidence_dir: Path | None = None) -> str:
    """The results table from the registry's `results` spec, both markers included."""
    spec = registry["results"]
    passing = {c["id"] for c in registry["claims"] if c.get("status") == "passing"}
    sources: set[str] = set()
    dashes = False
    lines = [
        "| | " + " | ".join(spec["columns"]) + " |",
        "|---" * (len(spec["columns"]) + 1) + "|",
    ]
    for row in spec["rows"]:
        cells = []
        for cell in row["cells"]:
            claim_id, _, key = cell.partition(":")
            number = ""
            if claim_id in passing:
                number = _number(claim_id, key, _evidence(evidence_dir, claim_id), row)
            if number:
                sources.add(claim_id)
            dashes = dashes or not number
            cells.append(number or DASH)
        lines.append(f"| {row['label']} | " + " | ".join(cells) + " |")
    notes = []
    if sources:
        links = ", ".join(f"[{cid}](claims/evidence/{cid}.json)" for cid in sorted(sources))
        notes.append(f"Evidence: {links}.")
    if dashes:
        notes.append(f"A {DASH} marks a value with no passing claim behind it.")
    footer = "\n\n" + " ".join(notes) if notes else ""
    return f"{RESULTS_START}\n" + "\n".join(lines) + f"{footer}\n{RESULTS_END}"


def _replace(text: str, pattern: re.Pattern[str], section: str, markers: str) -> str:
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"README.md has no {markers} markers")
    return text[: match.start()] + section + text[match.end() :]


def rendered(root: Path) -> str:
    """README.md as `update_readme` would write it now."""
    text = (root / "README.md").read_text(encoding="utf-8")
    registry = claims_module.load(root)
    evidence_dir = root / "claims" / "evidence"
    section = render_claims_section(registry, evidence_dir)
    text = _replace(text, SECTION_RE, section, f"{START} / {END}")
    if registry.get("results") is not None:
        table = render_results_section(registry, evidence_dir)
        text = _replace(text, RESULTS_RE, table, f"{RESULTS_START} / {RESULTS_END}")
    return text


def update_readme(root: Path, *, check: bool = False) -> bool:
    """Rewrite the generated sections of README.md from the registry and the evidence files.
    True when the file was (or would be) changed; with `check` it is left alone."""
    readme = root / "README.md"
    current = readme.read_text(encoding="utf-8")
    updated = rendered(root)
    if updated == current:
        return False
    if not check:
        readme.write_text(updated, encoding="utf-8", newline="\n")
    return True
