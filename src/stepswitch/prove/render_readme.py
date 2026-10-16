"""The README claims section, generated from claims/claims.yaml (SPEC §18.3 item 4).

Only claims whose latest status is `passing` are listed, each with its exact registered wording
inside claim markers and a link to its evidence file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stepswitch.prove import claims as claims_module

START = "<!-- claims:start -->"
END = "<!-- claims:end -->"
EMPTY = "_No claims verified yet._"
SECTION_RE = re.compile(r"<!--\s*claims:start\s*-->.*?<!--\s*claims:end\s*-->", re.S)


def render_claims_section(registry: Mapping[str, Any]) -> str:
    """The text from the start marker to the end marker, both included."""
    lines = []
    for claim in registry["claims"]:
        if claim.get("status") != "passing":
            continue
        cid = claim["id"]
        lines.append(
            f"- **{cid}** <!-- claim:{cid} -->{claim['text']}<!-- /claim --> "
            f"([evidence](claims/evidence/{cid}.json))"
        )
    body = "\n".join(lines) if lines else EMPTY
    return f"{START}\n{body}\n{END}"


def update_readme(root: Path, *, check: bool = False) -> bool:
    """Rewrite the section in README.md from the registry. True when it was (or would be)
    changed; with `check` the file is left alone."""
    readme = root / "README.md"
    text = readme.read_text(encoding="utf-8")
    match = SECTION_RE.search(text)
    if match is None:
        raise ValueError(f"README.md has no {START} / {END} markers")
    rendered = render_claims_section(claims_module.load(root))
    if match.group(0) == rendered:
        return False
    if not check:
        updated = text[: match.start()] + rendered + text[match.end() :]
        readme.write_text(updated, encoding="utf-8", newline="\n")
    return True
