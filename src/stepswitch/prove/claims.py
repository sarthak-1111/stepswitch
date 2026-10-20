"""Reading claims/claims.yaml and writing statuses back without losing its comments."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

REGISTRY_REL = "claims/claims.yaml"
STATUSES = ("pending", "passing", "failing", "withdrawn")
WITHDRAWN = "withdrawn"


def registry_path(root: Path) -> Path:
    return root / REGISTRY_REL


def load(root: Path) -> dict[str, Any]:
    data = YAML(typ="safe", pure=True).load(registry_path(root).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "claims" not in data:
        raise ValueError(f"{REGISTRY_REL} is not a claims registry")
    return data


def withdrawn(registry: Mapping[str, Any]) -> dict[str, str]:
    """Claim id -> the decision that withdrew it (ADR 0041). A withdrawn claim keeps its wording,
    evidence and history in the registry, and is no longer proven or shown as a claim."""
    return {
        str(claim["id"]): str(claim["withdrawn_by"])
        for claim in registry["claims"]
        if claim.get("withdrawn_by")
    }


def parts(claim: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A claim's parts; a claim without `parts` is one part holding all its tests."""
    if "parts" in claim:
        return [dict(part) for part in claim["parts"]]
    return [{"name": "", "phase": claim["phase"], "tests": list(claim.get("tests", []))}]


def tests_of(claim: Mapping[str, Any]) -> list[str]:
    return [test for part in parts(claim) for test in part["tests"]]


_CLAIM_START_RE = re.compile(r"^(\s*)-\s+id:\s*['\"]?(C-\d{3})['\"]?\s*(?:#.*)?$")
_STATUS_RE = re.compile(r"^(\s*status:\s*)([A-Za-z]+)(.*)$")


def update_statuses(root: Path, statuses: Mapping[str, str]) -> list[str]:
    """Set `status` for the given claims and return the ids whose status changed.

    Only the value on each claim's `status:` line changes; every other byte of the file
    (comments, quoting, layout) stays as it was, so the source digest, which ignores status
    lines, is unchanged by this write. The result is parsed again and compared before it
    replaces the file.
    """
    for status in statuses.values():
        if status not in STATUSES:
            raise ValueError(f"bad status {status!r}")
    path = registry_path(root)
    original = path.read_text(encoding="utf-8")
    lines = original.split("\n")
    current: str | None = None
    changed: list[str] = []
    for index, line in enumerate(lines):
        start = _CLAIM_START_RE.match(line)
        if start:
            current = start.group(2)
            continue
        match = _STATUS_RE.match(line)
        if (
            match
            and current is not None
            and current in statuses
            and match.group(2) != statuses[current]
        ):
            lines[index] = f"{match.group(1)}{statuses[current]}{match.group(3)}"
            changed.append(current)
    if not changed:
        return []
    updated = "\n".join(lines)
    before = YAML(typ="safe", pure=True).load(original)
    after = YAML(typ="safe", pure=True).load(updated)
    for claim in before["claims"]:
        if claim["id"] in changed:
            claim["status"] = statuses[claim["id"]]
    if before != after:
        raise ValueError(f"{REGISTRY_REL}: status update would change more than statuses")
    path.write_text(updated, encoding="utf-8", newline="\n")
    return changed


# --------------------------------------------------------------------------------------------
# Which registered test nodes exist (static scan, no imports)
# --------------------------------------------------------------------------------------------


def _defined_tests(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(stmt.name)
        elif isinstance(stmt, ast.ClassDef):
            for item in stmt.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    names.add(f"{stmt.name}::{item.name}")
    return names


def existing_nodes(root: Path, nodes: Iterable[str]) -> set[str]:
    """The subset of pytest node IDs whose file and test function exist."""
    cache: dict[str, set[str]] = {}
    found: set[str] = set()
    for node in nodes:
        file_part, _, name = node.partition("::")
        if file_part not in cache:
            path = root / file_part
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError):
                cache[file_part] = set()
            else:
                cache[file_part] = _defined_tests(tree)
        if name in cache[file_part]:
            found.add(node)
    return found
