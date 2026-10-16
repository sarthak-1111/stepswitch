"""C-018: every number in this README is reproducible.

Proof (SPEC §18.5): integrity lint (§18.3) plus evidence files with commands. Scope: numbers in
the README, each rendered from an evidence file that records its command.

The README shows numbers only inside `num` markers, which `ssw prove --render-readme` fills
from evidence files; any other digit fails the lint (claim IDs, section references and version
tokens excepted). The test checks the README as the renderer produces it from the evidence
files present when the test runs, because `ssw prove` rewrites evidence before it renders the
README: every marker must show its evidence value at the displayed precision, and its evidence
file must record the command that reproduces it, the source digest and the date. The committed
README must have no hand-typed number. Two self-checks keep the lint honest: a hand-typed
number added to the README fails it, and so does a marker whose value differs from its
evidence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from stepswitch.prove import render_readme
from tests.claims import evidence
from tests.claims.test_integrity import (
    EVIDENCE_DIR,
    NUM_SPAN_RE,
    lint_hand_typed_numbers,
    lint_number_markers,
)

pytestmark = [pytest.mark.claim("C-018")]

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
COMMAND_RE = re.compile(r"^uv run ssw prove( --[a-z-]+( C-\d{3})?)*$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def registry() -> dict[str, Any]:
    data: dict[str, Any] = YAML(typ="safe", pure=True).load(
        (ROOT / "claims" / "claims.yaml").read_text(encoding="utf-8")
    )
    return data


def provenance(claim_id: str) -> dict[str, Any]:
    path = EVIDENCE_DIR / f"{claim_id}.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return {
        "evidence": f"claims/evidence/{claim_id}.json",
        "command": data.get("command"),
        "source_digest": data.get("source_digest"),
        "updated": data.get("updated"),
    }


def test_every_readme_number_has_evidence_and_command() -> None:
    limits = evidence.thresholds("C-018")
    reg = registry()
    committed = README.read_text(encoding="utf-8")
    rendered = render_readme.rendered(ROOT)
    problems = lint_hand_typed_numbers(committed, reg) + lint_number_markers(rendered)
    problems += lint_hand_typed_numbers(rendered, reg)
    numbers = []
    for claim_id, key, shown in NUM_SPAN_RE.findall(rendered):
        source = provenance(claim_id)
        numbers.append({"marker": f"{claim_id}:{key}", "shown": shown.strip(), **source})
        if not (source["command"] and COMMAND_RE.match(source["command"])):
            problems.append(f"{claim_id}: evidence records no prove command")
        if not (source["source_digest"] and DIGEST_RE.match(source["source_digest"])):
            problems.append(f"{claim_id}: evidence records no source digest")
        if not source["updated"]:
            problems.append(f"{claim_id}: evidence records no date")
    planted = lint_hand_typed_numbers(committed + "\nA run takes 42 seconds.\n", reg)
    altered = rendered
    first = NUM_SPAN_RE.search(rendered)
    if first is not None:
        start, end = first.span(3)
        value = first.group(3).strip()
        altered = (
            rendered[:start] + ("9" + value if value[:1] != "9" else "1" + value) + rendered[end:]
        )
    evidence.write(
        "C-018",
        "readme",
        {
            "numbers": numbers,
            "unsourced_numbers": len(problems),
            "problems": problems,
            "self_checks": {
                "hand_typed_number_flagged": bool(planted),
                "altered_marker_flagged": bool(lint_number_markers(altered)),
            },
        },
    )
    assert numbers, "the README shows no number rendered from evidence"
    assert len(problems) <= limits["unsourced_numbers"], problems
    assert planted, "the lint missed a hand-typed number"
    assert lint_number_markers(altered), "the lint missed a marker that differs from evidence"
