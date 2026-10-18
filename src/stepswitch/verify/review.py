"""Cross-family review (SPEC §8.5, TK8; ADR 0025).

A reviewer from a model family other than the author's checks a diff against the instruction
and the acceptance criteria. Its answer is constrained by `REVIEW_SCHEMA`:

    {"approve": bool,
     "issues": [{"severity": "low" | "medium" | "high", "file": str, "line": int, "note": str}]}

`line` is 0 when an issue has no single line. `parse` reads the answer: the JSON object on its
own, inside a code fence or after other text. A model that does not follow the schema may
still answer in plain text starting with APPROVE or ISSUES (the Phase 2 review format), which
`parse` accepts as well and marks as not constrained.

`Review.score` is the number best-of-N selection compares, in [0, 1]: approval is worth 0.5,
and the other 0.5 shrinks with each issue by its severity weight (high 0.5, medium 0.2,
low 0.05). `Review.blocking` is true when the reviewer did not approve or found a
high-severity issue.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

SEVERITIES = ("low", "medium", "high")
SEVERITY_WEIGHT = {"low": 0.05, "medium": 0.2, "high": 0.5}
# Other words models use for a severity.
SEVERITY_ALIASES = {
    "critical": "high",
    "major": "high",
    "error": "high",
    "blocker": "high",
    "warning": "medium",
    "moderate": "medium",
    "minor": "low",
    "nit": "low",
    "info": "low",
    "style": "low",
}
MAX_ISSUES = 8
FILE_CHARS = 200
NOTE_CHARS = 300
TEXT_CHARS = 2000
DIFF_CHARS = 12_000

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approve": {"type": "boolean"},
        "issues": {
            "type": "array",
            "maxItems": MAX_ISSUES,
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "file": {"type": "string", "maxLength": FILE_CHARS},
                    "line": {"type": "integer", "minimum": 0},
                    "note": {"type": "string", "maxLength": NOTE_CHARS},
                },
                "required": ["severity", "file", "line", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["approve", "issues"],
    "additionalProperties": False,
}

REVIEW_PROMPT = """\
You review a code change that another model made.
Step: review
Check the diff against the task and its acceptance criteria: is the task done, correctly and
completely, without unrelated changes? Answer with JSON only, in this shape:
{"approve": true, "issues": [{"severity": "high", "file": "path", "line": 0, "note": "..."}]}
Severity high: wrong or missing behaviour. Medium: a risk or a likely bug. Low: style.
Approve only when no high issue remains. Use line 0 when an issue has no single line."""

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S | re.I)


@dataclass(frozen=True)
class Issue:
    severity: str
    file: str
    line: int
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "file": self.file, "line": self.line, "note": self.note}

    def text(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"[{self.severity}] {where + ': ' if where else ''}{self.note}"


@dataclass
class Review:
    approve: bool
    issues: list[Issue] = field(default_factory=list)
    # True when the answer was the schema's JSON; False for the plain-text fallback.
    constrained: bool = True
    text: str = ""
    reviewer: str | None = None
    reviewer_family: str | None = None
    # The families of the workers that wrote the change.
    author_families: tuple[str, ...] = ()

    @property
    def cross_family(self) -> bool:
        return (
            self.reviewer_family is not None
            and bool(self.author_families)
            and self.reviewer_family not in self.author_families
        )

    @property
    def blocking(self) -> bool:
        return not self.approve or any(issue.severity == "high" for issue in self.issues)

    @property
    def score(self) -> float:
        penalty = sum(SEVERITY_WEIGHT.get(issue.severity, 0.2) for issue in self.issues)
        return round((0.5 if self.approve else 0.0) + 0.5 * max(0.0, 1.0 - penalty), 4)

    def feedback(self) -> str:
        """The issues as lines for the author to address."""
        lines = [issue.text() for issue in self.issues]
        return "\n".join(f"- {line}" for line in lines) or self.text or "The reviewer disapproved."

    def as_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approve,
            "issues": [issue.as_dict() for issue in self.issues],
            "score": self.score,
            "blocking": self.blocking,
            "constrained": self.constrained,
            "parsed": True,
            "reviewer": self.reviewer,
            "reviewer_family": self.reviewer_family,
            "author_families": list(self.author_families),
            "cross_family": self.cross_family,
            "text": self.feedback(),
        }


def request_text(instruction: str, acceptance: Sequence[str], diff: str) -> str:
    """The user message of a review: task, acceptance commands and the diff (cut if long)."""
    shown = diff if len(diff) <= DIFF_CHARS else diff[:DIFF_CHARS] + "\n[diff cut]"
    accept = "\n".join(f"- {command}" for command in acceptance) or "- the project's tests pass"
    return f"Task: {instruction.strip()}\n\nAcceptance:\n{accept}\n\nDiff:\n{shown or '(empty)'}"


def _json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    candidates = [stripped]
    candidates += [match.group(1) for match in _FENCE.finditer(text)]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _severity(value: Any) -> str:
    word = str(value or "").strip().lower()
    if word in SEVERITIES:
        return word
    return SEVERITY_ALIASES.get(word, "medium")


def _line(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    match = re.match(r"\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else 0


def _issue(value: Any) -> Issue | None:
    if isinstance(value, str):
        note = value.strip()
        return Issue("medium", "", 0, note[:NOTE_CHARS]) if note else None
    if not isinstance(value, dict):
        return None
    note = str(value.get("note") or value.get("message") or value.get("issue") or "").strip()
    if not note:
        return None
    return Issue(
        _severity(value.get("severity")),
        str(value.get("file") or value.get("path") or "")[:FILE_CHARS],
        _line(value.get("line")),
        note[:NOTE_CHARS],
    )


def parse(text: str) -> Review | None:
    """The review in a model's answer, or None when the answer is neither JSON nor text."""
    data = _json_object(text)
    if data is not None and isinstance(data.get("approve", data.get("approved")), bool):
        raw = data.get("issues")
        issues = [i for i in (_issue(item) for item in raw or []) if i is not None]
        approve = bool(data.get("approve", data.get("approved")))
        return Review(approve, issues[:MAX_ISSUES], True, text[:TEXT_CHARS])
    head = text.lstrip("*# \n").upper()
    if head.startswith("APPROVE"):
        return Review(True, [], False, text[:TEXT_CHARS])
    if head.startswith("ISSUES"):
        note = text.strip()[:NOTE_CHARS]
        return Review(False, [Issue("medium", "", 0, note)], False, text[:TEXT_CHARS])
    return None
