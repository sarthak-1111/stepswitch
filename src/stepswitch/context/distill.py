"""Tool-output distillation (SPEC §13 TK4).

A command's output longer than `DISTILL_TOKENS` is not put into the transcript as it is. The
model gets a distilled view instead: the lines that carry errors, failures, warnings and
file:line references, verbatim and in order, the last lines (where test runners summarize), and
an optional summary by a tiny model (T0) that is told to keep error lines, file:line references
and numbers verbatim. The raw output is stored and stays retrievable: `read` with the path
`@output/<n>` and a line range shows any part of it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

DISTILL_TOKENS = 2000
CHARS_PER_TOKEN = 4
DISTILL_TOOLS = frozenset({"run", "test", "grep", "search", "lsp"})
OUTPUT_PREFIX = "@output/"
KEY_LINES = 60
TAIL_LINES = 12
LINE_CHARS = 300
KEY_CHARS = 4000
SUMMARY_INPUT_CHARS = 12000

KEY_RE = re.compile(
    r"error|exception|traceback|fail|fatal|warning|assert|denied|not found|panic|segfault"
    r"|undefined|cannot|unable|invalid|expected|^\s*E\s{2,}|^\s*>\s",
    re.I,
)
FILE_LINE_RE = re.compile(r"[\w./\\-]+\.[A-Za-z]\w*[:(]\d+")

SUMMARY_PROMPT = """\
Summarize this tool output for a coding agent in at most five sentences. Say what ran, what
passed and what failed. Keep error messages, file:line references and numbers exactly as they
appear. Do not add advice."""


@dataclass
class OutputStore:
    """Raw outputs of one task, by number (`@output/<n>`)."""

    outputs: dict[int, str] = field(default_factory=dict)

    def put(self, text: str) -> int:
        number = len(self.outputs) + 1
        self.outputs[number] = text
        return number

    def get(self, number: int) -> str | None:
        return self.outputs.get(number)

    def parse(self, path: str) -> int | None:
        """The output number of an `@output/<n>` path, or None for an ordinary path."""
        if not path.startswith(OUTPUT_PREFIX):
            return None
        tail = path[len(OUTPUT_PREFIX) :].strip()
        return int(tail) if tail.isdigit() else -1

    def show(self, number: int, start: int | None, end: int | None, max_chars: int) -> str:
        text = self.get(number)
        if text is None:
            known = ", ".join(f"{OUTPUT_PREFIX}{n}" for n in sorted(self.outputs)) or "none"
            raise KeyError(f"no stored output {OUTPUT_PREFIX}{number} (stored: {known})")
        lines = text.splitlines()
        first = max(1, int(start or 1))
        last = min(len(lines), int(end or len(lines)))
        rows: list[str] = []
        used = 0
        shown = first - 1
        for number_ in range(first, last + 1):
            row = f"{number_}|{lines[number_ - 1][:LINE_CHARS]}"
            if rows and used + len(row) + 1 > max_chars:
                break
            rows.append(row)
            used += len(row) + 1
            shown = number_
        header = f"{OUTPUT_PREFIX}{number} lines {first}-{shown} of {len(lines)}"
        more = f"\n[more: read with start={shown + 1}]" if shown < last else ""
        return header + "\n" + "\n".join(rows) + more


def too_long(text: str, threshold_tokens: int = DISTILL_TOKENS) -> bool:
    return len(text) > threshold_tokens * CHARS_PER_TOKEN


def key_lines(text: str, *, limit: int = KEY_LINES) -> list[str]:
    """Lines with errors, failures, warnings or file:line references, verbatim, in order;
    consecutive repeats are folded."""
    out: list[str] = []
    previous = None
    repeats = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not (KEY_RE.search(line) or FILE_LINE_RE.search(line)):
            continue
        clipped = line[:LINE_CHARS]
        if clipped == previous:
            repeats += 1
            continue
        if repeats:
            out.append(f"   [the line above repeats {repeats} more times]")
            repeats = 0
        out.append(f"{number}|{clipped}")
        previous = clipped
        if len(out) >= limit:
            out.append("   [more key lines in the full output]")
            break
    if repeats:
        out.append(f"   [the line above repeats {repeats} more times]")
    return out


def distill(text: str, *, tool: str, number: int, summary: str | None = None) -> str:
    lines = text.splitlines()
    parts = [
        f"[{tool} output: {len(lines)} lines, {len(text)} characters, distilled. The full"
        f" output is {OUTPUT_PREFIX}{number}: read that path with start and end to see any"
        " lines.]"
    ]
    if summary:
        parts.append("Summary: " + summary.strip())
    keys = key_lines(text)
    used = 0
    kept: list[str] = []
    for line in keys:
        if used + len(line) > KEY_CHARS:
            kept.append("   [more key lines in the full output]")
            break
        kept.append(line)
        used += len(line) + 1
    if kept:
        parts.append("Key lines (line number|text, verbatim):\n" + "\n".join(kept))
    tail = [
        f"{len(lines) - TAIL_LINES + i + 1}|{line[:LINE_CHARS]}"
        for i, line in enumerate(lines[-TAIL_LINES:])
    ]
    if len(lines) <= TAIL_LINES:
        tail = [f"{i + 1}|{line[:LINE_CHARS]}" for i, line in enumerate(lines)]
    parts.append("Last lines:\n" + "\n".join(tail))
    return "\n\n".join(parts)


def summary_input(text: str) -> str:
    """What the summarizer reads: head and tail of a long output."""
    if len(text) <= SUMMARY_INPUT_CHARS:
        return text
    half = SUMMARY_INPUT_CHARS // 2
    return text[:half] + "\n[... middle omitted ...]\n" + text[-half:]


def summarize(model: Any, text: str, *, max_output_tokens: int = 250) -> tuple[str | None, Any]:
    """A T0 summary of `text` (None when the model fails or answers nothing)."""
    from stepswitch.compat.errors import ModelError
    from stepswitch.compat.ir import Message, Request, Sampling, TextPart

    request = Request(
        model="",
        system=[TextPart(text=SUMMARY_PROMPT)],
        messages=[Message.text("user", summary_input(text))],
        max_output_tokens=max_output_tokens,
        sampling=Sampling(temperature=0.0, seed=0),
    )
    try:
        completion = model.complete(request)
    except ModelError:
        return None, None
    answer = completion.response.message.text_content().strip()
    return (answer[:1500] or None), completion


def numbered(lines: Sequence[str], first: int = 1) -> list[str]:
    return [f"{first + i}|{line}" for i, line in enumerate(lines)]
