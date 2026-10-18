"""Stable prefix layout (SPEC §13 TK1).

Every work step of a task sends the same prompt prefix, byte for byte, so each model server can
reuse its prefix cache: `[system][tools][repo map][glossary]`. The tool specs travel in the
request's own field, which every codec renders next to the system text; the system text holds
the role prompt, then the repository overview, then the track notes and domain glossary.
Volatile content (budgets left, verification reports, nudges, time) never goes into the prefix;
it is appended as messages. Messages only grow, except at the batched points where TK6 prunes
superseded results or replaces old turns with a brief.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from stepswitch.compat.ir import Request
from stepswitch.router.cache import chain_of

LAYOUT = ("system", "tools", "repo map", "glossary")
# Content that changes between steps and therefore must not appear in the prefix.
VOLATILE = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"),  # timestamps
    re.compile(r"\bbudget left\b", re.I),
    re.compile(r"\bstep \d+ of\b", re.I),
    re.compile(r"\bverification (passed|failed)\b", re.I),
)


@dataclass(frozen=True)
class StablePrefix:
    system: str
    repo_map: str = ""
    glossary: str = ""

    def text(self) -> str:
        return "\n\n".join(part for part in (self.system, self.repo_map, self.glossary) if part)


def digest(request: Request) -> str:
    """Hash of the system text and tool specs (block 0 of the prefix registry's chain)."""
    return chain_of(request).blocks[0][0]


def append_only(before: Request, after: Request) -> bool:
    """`after` starts with exactly the prompt of `before`: same prefix, messages only grew."""
    if digest(before) != digest(after) or len(after.messages) < len(before.messages):
        return False
    return after.messages[: len(before.messages)] == before.messages


def volatile_parts(text: str) -> list[str]:
    """Fragments of `text` that look volatile (for checks that the prefix stays stable)."""
    return [match.group(0) for pattern in VOLATILE for match in pattern.finditer(text)]
