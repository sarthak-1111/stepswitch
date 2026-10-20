"""Hash-anchored line edits: Stepswitch's edit format (spec/edit-format.md, SPEC §13 TK5).

`read` shows every line as `<line>:<hash>|<text>`. An edit names lines by those anchors, and
every anchor is checked against the file as it is now: if any anchor is stale, nothing is
written and the error shows the current lines with fresh anchors. All anchors in one call refer
to the file before the call; the edits are applied together and written atomically. Newline
style (per line), final newline, UTF-8 byte-order mark and file mode are preserved; binary and
non-UTF-8 files are refused. The result carries a unified diff.

Prior art: the hash-anchored line edits of the Oh My Pi harness (MIT). This format is designed
independently; see the spec for the differences and the reasoning behind the hash length.
"""

from __future__ import annotations

import codecs
import contextlib
import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from collections import defaultdict
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HASH_LEN = 2
ALPHABET = "abcdefghijklmnopqrstuvwxyz"
TRAILING_WHITESPACE = " \t\r\f\v"
SMALL_FILE_LINES = 100
PREVIEW_CONTEXT = 2
PREVIEW_MAX_LINES = 40

Seen = MutableMapping[tuple[int, str], str]


class EditError(ValueError):
    """An edit that was refused; the message is written for the model to act on."""


# --------------------------------------------------------------------------------------------
# Anchors
# --------------------------------------------------------------------------------------------


def _content(line: str) -> str:
    return line.rstrip(TRAILING_WHITESPACE)


def line_hash(text: str, ordinal: int = 1) -> str:
    """The anchor hash of a line: two lowercase letters (see the spec for the exact rule).

    `ordinal` is how many lines up to and including this one have the same content (trailing
    whitespace ignored); repeats hash differently, so a stale anchor on a blank line or a lone
    brace does not silently match another copy of it.
    """
    data = _content(text).encode("utf-8", errors="surrogatepass")
    if ordinal > 1:
        data += b"\x00" + str(ordinal).encode("ascii")
    value = int.from_bytes(hashlib.sha256(data).digest()[:4], "big") % (len(ALPHABET) ** HASH_LEN)
    letters = []
    for _ in range(HASH_LEN):
        value, digit = divmod(value, len(ALPHABET))
        letters.append(ALPHABET[digit])
    return "".join(reversed(letters))


def line_hashes(lines: Sequence[str]) -> list[str]:
    seen: dict[str, int] = defaultdict(int)
    hashes = []
    for line in lines:
        content = _content(line)
        seen[content] += 1
        hashes.append(line_hash(content, seen[content]))
    return hashes


@dataclass(frozen=True)
class Anchor:
    """A line by number and hash. Version 2 also holds a partial anchor until it is resolved
    against the file (`resolve_anchor`): a bare hash has line 0, a bare line number hash ""."""

    line: int
    hash: str

    @property
    def partial(self) -> bool:
        return not (self.line and self.hash)

    def __str__(self) -> str:
        return f"{self.line or '?'}:{self.hash or '?'}"


_ANCHOR_RE = re.compile(rf"^\s*[Ll]?(\d+)\s*[:#]\s*([A-Za-z]{{{HASH_LEN}}})(?![A-Za-z])")
# A bare hash, perhaps followed by the line as read shows it: "ab" or "ab|    return x".
_HASH_RE = re.compile(rf"^\s*([A-Za-z]{{{HASH_LEN}}})\s*(?:\|.*)?$", re.S)


def parse_anchor(value: Any, what: str = "anchor") -> Anchor:
    """`"12:ab"`, `"12:ab|text..."`, `{"line": 12, "hash": "ab"}` (case-insensitive hash).

    Version 2 (ADR 0038): a bare hash `"ab"` or a bare line number `12` parses to a partial
    anchor, which `resolve_anchor` completes or refuses against the file."""
    if isinstance(value, Anchor):
        return value
    if isinstance(value, Mapping):
        line, digest = value.get("line"), value.get("hash")
        if isinstance(line, str) and line.strip().isdigit():
            line = int(line)
        if isinstance(line, int) and not isinstance(line, bool) and isinstance(digest, str):
            return parse_anchor(f"{line}:{digest}", what)
        if line is not None and digest is None:
            return parse_anchor(line, what)
    if isinstance(value, bool):
        raise EditError(f"{what} {value!r} is not an anchor: use `<line>:<hash>` as shown by read")
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        number = int(value)
        if number < 1:
            raise EditError(f"{what} {value}: lines are numbered from 1")
        return Anchor(number, "")
    if isinstance(value, str):
        match = _ANCHOR_RE.match(value)
        if match:
            return Anchor(int(match.group(1)), match.group(2).lower())
        bare = _HASH_RE.match(value)
        if bare:
            return Anchor(0, bare.group(1).lower())
    raise EditError(
        f"{what} {value!r} is not an anchor: use `<line>:<hash>` as shown by read, e.g. 12:ab"
    )


# --------------------------------------------------------------------------------------------
# Text files
# --------------------------------------------------------------------------------------------


@dataclass
class TextDoc:
    """A UTF-8 text file as lines plus the line ending each line had."""

    lines: list[str]
    endings: list[str]
    newline: str = "\n"  # the file's dominant style, used for new lines
    bom: bool = False

    @property
    def final_newline(self) -> bool:
        return bool(self.endings) and self.endings[-1] != ""

    @classmethod
    def decode(cls, data: bytes, display: str = "file") -> TextDoc:
        if b"\x00" in data[:8192]:
            raise EditError(f"{display} looks binary; only UTF-8 text files can be edited")
        bom = data.startswith(codecs.BOM_UTF8)
        try:
            text = data[len(codecs.BOM_UTF8) :].decode("utf-8") if bom else data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EditError(
                f"{display} is not valid UTF-8 ({exc.reason} at byte {exc.start})"
            ) from exc
        parts = text.split("\n")
        final = parts[-1] == ""
        if final:
            parts.pop()
        lines: list[str] = []
        endings: list[str] = []
        for index, part in enumerate(parts):
            crlf = part.endswith("\r")
            lines.append(part[:-1] if crlf else part)
            last = index == len(parts) - 1
            endings.append("" if last and not final else ("\r\n" if crlf else "\n"))
        crlf_count = sum(1 for e in endings if e == "\r\n")
        lf_count = sum(1 for e in endings if e == "\n")
        return cls(lines, endings, "\r\n" if crlf_count > lf_count else "\n", bom)

    @classmethod
    def from_lines(
        cls, lines: list[str], *, newline: str = "\n", final_newline: bool = True
    ) -> TextDoc:
        doc = cls(list(lines), [newline] * len(lines), newline)
        if lines and not final_newline:
            doc.endings[-1] = ""
        return doc

    def encode(self) -> bytes:
        try:
            body = "".join(
                line + ending for line, ending in zip(self.lines, self.endings, strict=True)
            )
            data = body.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise EditError(
                f"the new text holds characters that are not valid UTF-8: {exc}"
            ) from exc
        return (codecs.BOM_UTF8 + data) if self.bom and data else data


def text_lines(value: Any, what: str = "text") -> list[str]:
    """Lines of new text. A string may end with or without a newline ("a\\nb\\n" and "a\\nb"
    are both two lines); "" is no lines and "\\n" is one empty line. A list of strings is also
    accepted. CRLF in the text is treated as LF."""
    if isinstance(value, list | tuple):
        lines: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise EditError(f"{what} must be a string or a list of strings")
            lines.extend(item.replace("\r\n", "\n").split("\n"))
        return [line.rstrip("\r") for line in lines]
    if not isinstance(value, str):
        raise EditError(f"{what} must be a string (the new lines)")
    if value == "":
        return []
    text = value.replace("\r\n", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n")


def render(
    lines: Sequence[str],
    hashes: Sequence[str],
    start: int = 1,
    end: int | None = None,
    *,
    max_line_chars: int | None = None,
    seen: Seen | None = None,
) -> str:
    """Lines `start..end` (1-based, inclusive) as `<line>:<hash>|<text>` rows."""
    end = len(lines) if end is None else min(end, len(lines))
    rows = []
    for number in range(max(1, start), end + 1):
        text = lines[number - 1]
        digest = hashes[number - 1]
        if seen is not None:
            seen[(number, digest)] = _content(text)
        if max_line_chars is not None and len(text) > max_line_chars:
            text = text[:max_line_chars] + f"... [{len(text) - max_line_chars} more chars]"
        rows.append(f"{number}:{digest}|{text}")
    return "\n".join(rows)


# --------------------------------------------------------------------------------------------
# Parsing edits
# --------------------------------------------------------------------------------------------

OP_ALIASES = {
    "replace": "replace",
    "replace_lines": "replace",
    "replace_range": "replace",
    "set": "replace",
    "change": "replace",
    "update": "replace",
    "modify": "replace",
    "edit": "replace",
    "delete": "delete",
    "delete_lines": "delete",
    "remove": "delete",
    "del": "delete",
    "cut": "delete",
    "insert_after": "insert_after",
    "after": "insert_after",
    "append": "insert_after",
    "add_after": "insert_after",
    "insert_below": "insert_after",
    "insert_before": "insert_before",
    "before": "insert_before",
    "prepend": "insert_before",
    "add_before": "insert_before",
    "insert_above": "insert_before",
    "insert": "insert",
}
START_KEYS = (
    "start",
    "from",
    "anchor",
    "line",
    "at",
    "begin",
    "start_anchor",
    "first",
    "line_start",
    "start_line",
)
END_KEYS = ("end", "to", "until", "stop", "end_anchor", "last", "line_end", "end_line")
TEXT_KEYS = ("text", "content", "new_text", "replacement", "code", "lines", "new", "with")
EXAMPLE = '{"op": "replace", "start": "12:ab", "end": "14:cd", "text": "new lines"}'


@dataclass(frozen=True)
class Op:
    kind: str  # "replace", "delete", "insert_before", "insert_after", or "excerpt" (v2)
    start: Anchor
    end: Anchor
    lines: tuple[str, ...] = ()
    index: int = 0  # position in the request, for messages
    # Version 2 (ADR 0038): an excerpt edit names its lines by their old text, and `start`
    # is then only a hint; a delete may give how many lines it removes instead of `end`.
    old: tuple[str, ...] | None = None
    count: int | None = None


NO_ANCHOR = Anchor(0, "")
OLD_KEYS = ("old", "old_text", "old_string", "old_str", "search", "find", "original", "old_lines")
NEW_KEYS: tuple[str, ...] = (
    "new",
    "new_text",
    "new_string",
    "new_str",
    "replace",
    "replacement",
    "with",
    "text",
    "content",
    "code",
    "lines",
    "insert",
)


def _first(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _split_range(value: Any) -> tuple[Any, Any]:
    if isinstance(value, list | tuple) and len(value) == 2:
        return value[0], value[1]
    if isinstance(value, str):
        parts = re.split(r"\s*(?:\.\.|-|–|,)\s*(?=[Ll]?\d)", value.strip(), maxsplit=1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return value, None
    return value, None


def _count(value: Any) -> int | None:
    """A line count such as the 2 of `{"delete": 2, "line": 19}`."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str) and value.strip().isdigit() and int(value) >= 1:
        return int(value)
    return None


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(_HASH_RE.match(value))


def _with_hash(start_raw: Any, digest: str) -> Any:
    """A line number and a hash given apart (`{"hash": "cp", "line": 13}`, or
    `{"delete": "pb", "line": 11}`) as one anchor; a hash alone as a bare hash."""
    if isinstance(start_raw, int) and not isinstance(start_raw, bool):
        return f"{start_raw}:{digest.strip()}"
    if isinstance(start_raw, str) and start_raw.strip().isdigit():
        return f"{start_raw.strip()}:{digest.strip()}"
    return start_raw if start_raw is not None else digest


def _parse_excerpt(
    data: Mapping[str, Any], kind: str | None, start_raw: Any, label: str, index: int
) -> Op | None:
    """Version 2: an edit that names its lines by their old text (`old`/`new`, `search`/
    `replace`, or `delete`/`insert` holding text). `start` or `line`, if given, is a hint."""
    old_raw = _first(data, OLD_KEYS)
    deleted = data.get("delete")
    if old_raw is None and isinstance(deleted, str) and _count(deleted) is None:
        old_raw = None if _is_hash(deleted) else deleted
    if old_raw is None:
        return None
    new_raw = _first(data, NEW_KEYS)
    if new_raw is None:
        if kind != "delete":
            raise EditError(
                f"{label}: give `new`, the text that replaces `old`, or op delete to remove it"
            )
        new_raw = ""
    old = tuple(text_lines(old_raw, f"{label} old text"))
    if not old:
        raise EditError(f"{label}: `old` is empty; to add lines, insert them at an anchor")
    hint = parse_anchor(start_raw, f"{label} line") if start_raw is not None else NO_ANCHOR
    new = tuple(text_lines(new_raw, f"{label} new text"))
    return Op("excerpt", hint, hint, new, index, old=old)


def parse_edit(raw: Any, index: int = 0) -> Op:
    label = f"edit {index + 1}"
    if not isinstance(raw, Mapping):
        raise EditError(f"{label}: expected an object such as {EXAMPLE}")
    data = {str(k).strip().lower(): v for k, v in raw.items()}
    op_name = str(_first(data, ("op", "action", "type", "kind")) or "")
    op_name = op_name.strip().lower().replace("-", "_").replace(" ", "_")
    kind = OP_ALIASES.get(op_name) if op_name else None
    if op_name and kind is None:
        raise EditError(
            f"{label}: unknown op {op_name!r}; use replace, insert_after, insert_before or delete"
        )
    start_raw = _first(data, START_KEYS)
    end_raw = _first(data, END_KEYS)
    if "range" in data and start_raw is None:
        start_raw, range_end = _split_range(data["range"])
        end_raw = end_raw if end_raw is not None else range_end
    if _is_hash(data.get("hash")):
        start_raw = _with_hash(start_raw, data["hash"])
    if _is_hash(start_raw) and _count(data.get("line")) is not None:
        start_raw = _with_hash(data["line"], start_raw)  # `{"anchor": "ml", "line": 15}`
    excerpt = _parse_excerpt(data, kind, start_raw, label, index)
    if excerpt is not None:
        return excerpt
    text_raw = _first(data, TEXT_KEYS)
    deleted = data.get("delete")
    by_hash = isinstance(deleted, str) and _is_hash(deleted)
    if isinstance(deleted, str) and by_hash:
        start_raw = _with_hash(start_raw, deleted)  # `{"delete": "pb", "line": 11}`
    count = _count(deleted) or (_count(data.get("count")) if kind == "delete" else None)
    insert_raw = data.get("insert")
    flagged = deleted is True  # `{"delete": true, "line": 18}`
    if kind in (None, "delete", "replace") and (
        flagged or ((count is not None or by_hash) and end_raw is None)
    ):
        # Version 2: `{"delete": 2, "line": 19}` removes two lines from line 19; with `insert`
        # holding text, the new lines take their place.
        if isinstance(insert_raw, str) and text_raw is None:
            text_raw = insert_raw
        kind = "replace" if text_raw is not None else "delete"
    elif isinstance(insert_raw, str) and text_raw is None:
        text_raw, count = insert_raw, None
        kind = kind or "insert"
    else:
        count = None
    after, before = data.get("after"), data.get("before")
    position = str(data.get("position") or data.get("where") or "").strip().lower()
    if (
        kind in (None, "insert")
        and position in ("after", "below", "before", "above")
        and after is None
        and before is None
        and start_raw is not None
    ):
        # `{"anchor": "gf", "insert": "...", "position": "after"}`
        after, before = (start_raw, None) if position in ("after", "below") else (None, start_raw)
    if kind in (None, "insert"):
        if after is not None:
            kind = "insert_after"
        elif before is not None:
            kind = "insert_before"
        elif kind == "insert" and start_raw is not None:
            kind = "insert_before"  # version 2: inserting "at" a line puts the text before it
        elif kind == "insert" and text_raw is not None:
            # Version 2: no anchor. It can only go where the call's deletes remove one block of
            # lines (`resolve_ops`); anywhere else it is refused there.
            lines = tuple(text_lines(text_raw, f"{label} text"))
            return Op("insert_in_place", NO_ANCHOR, NO_ANCHOR, lines, index)
        elif kind == "insert":
            raise EditError(f"{label}: insert needs `after` or `before` with an anchor")
        elif start_raw is not None and text_raw is not None:
            kind = "replace"
        else:
            raise EditError(
                f"{label}: say what to do with `op` (replace, insert_after, insert_before, "
                f"delete), e.g. {EXAMPLE}"
            )
    if kind == "insert_after":
        anchor = parse_anchor(after if after is not None else start_raw, f"{label} anchor")
        start = end = anchor
    elif kind == "insert_before":
        anchor = parse_anchor(before if before is not None else start_raw, f"{label} anchor")
        start = end = anchor
    else:
        if start_raw is None:
            raise EditError(
                f"{label}: {kind} needs `start` (and `end` for several lines) with anchors from "
                "read, or `old`: the exact lines it changes"
            )
        start = parse_anchor(start_raw, f"{label} start")
        end = parse_anchor(end_raw, f"{label} end") if end_raw is not None else start
        if not (start.partial or end.partial) and end.line < start.line:
            raise EditError(f"{label}: end {end} comes before start {start}")
    if kind == "delete":
        return Op(kind, start, end, (), index, count=count)
    if text_raw is None:
        hint = " (use op delete to remove lines)" if kind == "replace" else ""
        raise EditError(f"{label}: {kind} needs `text`{hint}")
    return Op(kind, start, end, tuple(text_lines(text_raw, f"{label} text")), index, count=count)


def parse_edits(raw: Any) -> list[Op]:
    """A list of edits from what a model sent (a list, one object, or a JSON string of them)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EditError(f"edits must be a list of objects such as [{EXAMPLE}]: {exc}") from exc
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise EditError(f"edits must be a non-empty list of objects such as [{EXAMPLE}]")
    return [parse_edit(item, index) for index, item in enumerate(raw)]


# --------------------------------------------------------------------------------------------
# Applying edits
# --------------------------------------------------------------------------------------------


def _stale(doc: TextDoc, hashes: list[str], anchor: Anchor, reason: str) -> EditError:
    total = len(doc.lines)
    if total == 0:
        return EditError(
            f"stale anchor {anchor}: the file is now empty. Nothing was changed. "
            "Replace the whole file with `text` instead."
        )
    line = min(max(anchor.line, 1), total)
    short = total <= SMALL_FILE_LINES
    # A short file is shown whole: the model sees every current anchor at once.
    low, high = (1, total) if short else (max(1, line - 3), min(total, line + 3))
    moved = [number for number, digest in enumerate(hashes, 1) if digest == anchor.hash]
    hint = ""
    if len(moved) == 1 and moved[0] != anchor.line:
        hint = f" The line it named may now be {moved[0]}:{anchor.hash}."
    advice = "Use these anchors, or read the file again."
    if short:
        advice += " This file is short: you can also send its whole new content as `text`."
    return EditError(
        f"stale anchor {anchor}: {reason}.{hint} Nothing was changed. "
        f"Current lines {low}-{high}:\n{render(doc.lines, hashes, low, high)}\n{advice}"
    )


def resolve_anchor(
    doc: TextDoc, hashes: list[str], anchor: Anchor, seen: Seen | None, what: str = "anchor"
) -> Anchor:
    """Complete a version 2 partial anchor against the file, or refuse it.

    A bare hash names the one line that has it now. A bare line number is taken only when the
    toolbox showed that line at that number with the content it holds now: the model cannot
    have counted from a stale copy."""
    if not anchor.partial:
        return anchor
    total = len(doc.lines)
    if not anchor.line:
        found = [number for number, digest in enumerate(hashes, 1) if digest == anchor.hash]
        if len(found) == 1:
            return Anchor(found[0], anchor.hash)
        if not found:
            raise EditError(
                f"{what} {anchor.hash!r} matches no line now: use `<line>:<hash>` as shown by "
                "read, e.g. 12:ab"
            )
        rows = "\n".join(render(doc.lines, hashes, n, n) for n in found[:5])
        raise EditError(f"{what} {anchor.hash!r} matches several lines; give the line too:\n{rows}")
    if anchor.line > total:
        raise _stale(doc, hashes, Anchor(anchor.line, "?"), f"the file has {total} lines")
    digest = hashes[anchor.line - 1]
    shown = seen.get((anchor.line, digest)) if seen is not None else None
    if shown is not None and shown == _content(doc.lines[anchor.line - 1]):
        return Anchor(anchor.line, digest)
    row = render(doc.lines, hashes, anchor.line, anchor.line)
    raise EditError(
        f"{what} {anchor.line} has no hash, and line {anchor.line} was not shown to you as it "
        f"is now: copy the full anchor `<line>:<hash>` from read output. Line {anchor.line} is "
        f"now:\n{row}"
    )


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


# Typographic characters a model may write where the file has the ASCII ones. The last matching
# level reads them as ASCII, as the reference patch tools do (docs/RESEARCH.md, Phase 12).
_FOLD = str.maketrans(
    {
        **dict.fromkeys("‐‑‒–—―−", "-"),
        **dict.fromkeys("‘’‚‛", "'"),
        **dict.fromkeys("“”„‟", '"'),
        **dict.fromkeys("            　", " "),
    }
)
MATCH_LEVELS = 3  # exact, any indentation, any indentation and typographic punctuation


def _matches(lines: Sequence[str], old: Sequence[str], *, level: int = 0) -> list[int]:
    """1-based start lines where `old` matches as whole lines. Trailing whitespace never counts;
    level 1 also ignores indentation, and level 2 also reads typographic dashes, quotes and
    spaces as ASCII."""
    size = len(old)

    def key(text: str) -> str:
        if level == 0:
            return _content(text)
        if level == 1:
            return _content(text).lstrip(" \t")
        return _content(text).translate(_FOLD).strip()

    wanted = [key(line) for line in old]
    return [
        start + 1
        for start in range(len(lines) - size + 1)
        if all(key(lines[start + offset]) == wanted[offset] for offset in range(size))
    ]


def find_lines(lines: Sequence[str], old: Sequence[str], start: int = 0) -> tuple[list[int], int]:
    """The 1-based start lines, at or after line `start + 1`, where `old` matches at the
    strictest level that matches at all, and that level."""
    for level in range(MATCH_LEVELS):
        found = [start + n for n in _matches(lines[start:], old, level=level)]
        if found:
            return found, level
    return [], 0


def _reindent(file_lines: Sequence[str], old: Sequence[str], new: Sequence[str]) -> list[str]:
    """New lines shifted by the indentation the file has and the excerpt lacked (or had too
    much of), when every non-blank matched line differs by the same amount."""
    deltas = set()
    for have, gave in zip(file_lines, old, strict=True):
        if not have.strip():
            continue
        have_ws, gave_ws = _indent(have), _indent(gave)
        if have_ws.endswith(gave_ws):
            deltas.add(("add", have_ws[: len(have_ws) - len(gave_ws)]))
        elif gave_ws.endswith(have_ws):
            deltas.add(("cut", gave_ws[: len(gave_ws) - len(have_ws)]))
        else:
            return []
    if len(deltas) != 1:
        return []
    ((direction, prefix),) = deltas
    if direction == "add":
        return [prefix + line if line.strip() else line for line in new]
    if all(line.startswith(prefix) or not line.strip() for line in new):
        return [line[len(prefix) :] if line.strip() else line for line in new]
    return []


def _hint_line(hashes: list[str], anchor: Anchor) -> int | None:
    """The line an excerpt's hint points at. A hint only chooses between matches of the old
    text, so a bare line number or a stale hash is enough for it."""
    if anchor == NO_ANCHOR:
        return None
    if anchor.line:
        return anchor.line
    found = [number for number, digest in enumerate(hashes, 1) if digest == anchor.hash]
    return found[0] if len(found) == 1 else None


def _closest(found: list[int], size: int, hint: int | None) -> list[int]:
    """Of several matches `size` lines long, the ones nearest the hint line (all without one)."""
    if hint is None or len(found) < 2:
        return found
    distance = {
        start: 0
        if start <= hint < start + size
        else min(abs(hint - start), abs(hint - (start + size - 1)))
        for start in found
    }
    best = min(distance.values())
    return [start for start in found if distance[start] == best]


def _word(char: str) -> bool:
    return char.isalnum() or char == "_"


def _inline_op(doc: TextDoc, hashes: list[str], op: Op, hint: int | None) -> Op | None:
    """The excerpt as text inside lines, as a string replace finds it: it must occur once (or
    once nearest the hint), and not start or end inside a word (`x = 1` is not found in
    `max = 1`). The lines it touches are replaced by themselves with the text swapped."""
    needle = "\n".join(op.old or ())
    if not needle.strip():
        return None
    text = "\n".join(doc.lines)
    starts: list[int] = []
    at = text.find(needle)
    while at != -1:
        end = at + len(needle)
        before = text[at - 1] if at else " "
        after = text[end] if end < len(text) else " "
        if not (_word(needle[0]) and _word(before)) and not (_word(needle[-1]) and _word(after)):
            starts.append(at)
        at = text.find(needle, at + 1)
    if not starts:
        return None
    first_line = {at: text.count("\n", 0, at) + 1 for at in starts}
    size = needle.count("\n") + 1
    closest = _closest(sorted(set(first_line.values())), size, hint)
    chosen = [at for at in starts if first_line[at] in closest]
    if len(chosen) > 1:
        label = f"edit {op.index + 1}"
        rows = "\n...\n".join(
            render(doc.lines, hashes, first_line[at], first_line[at] + size - 1)
            for at in chosen[:4]
        )
        raise EditError(
            f"{label}: the old text occurs {len(chosen)} times; give more of it, or `line` "
            f"with one of these anchors. Nothing was changed.\n{rows}"
        )
    at = chosen[0]
    end = at + len(needle)
    first, last = first_line[at], text.count("\n", 0, end) + 1
    line_start = text.rfind("\n", 0, at) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end == -1 else line_end
    block = text[line_start:at] + "\n".join(op.lines) + text[end:line_end]
    lines = tuple(block.split("\n")) if block else ()
    anchors = Anchor(first, hashes[first - 1]), Anchor(last, hashes[last - 1])
    return Op("replace", *anchors, lines, op.index, old=op.old)


def _excerpt_op(doc: TextDoc, hashes: list[str], op: Op) -> Op:
    """An excerpt edit as the replace of the lines its old text matches (ADR 0038): whole
    lines exactly, else whole lines whatever their indentation (the new lines then get the
    file's) and then also whatever their typographic punctuation, else text inside lines that
    occurs once."""
    label = f"edit {op.index + 1}"
    old = list(op.old or ())
    new = list(op.lines)
    hint = _hint_line(hashes, op.start)
    found, level = find_lines(doc.lines, old)
    loose = level > 0
    if not found:
        inline = _inline_op(doc, hashes, op, hint)
        if inline is not None:
            return inline
        first = old[0].strip()
        near = [n for n, line in enumerate(doc.lines, 1) if first and line.strip() == first]
        if near:
            low, high = max(1, near[0] - 2), min(len(doc.lines), near[0] + len(old) + 1)
        else:
            low, high = 1, min(len(doc.lines), SMALL_FILE_LINES)
        raise EditError(
            f"{label}: the old text is not in the file. Copy it exactly from read output, "
            f"indentation included. Nothing was changed. Lines {low}-{high} are now:\n"
            f"{render(doc.lines, hashes, low, high)}"
        )
    if len(found) > 1:
        closest = _closest(found, len(old), hint)
        if len(closest) == 1:
            found = closest
        if len(found) > 1:
            rows = "\n...\n".join(
                render(doc.lines, hashes, start, start + len(old) - 1) for start in found[:4]
            )
            raise EditError(
                f"{label}: the old text matches {len(found)} places; add a line of context, or "
                f"give `line` with one of these anchors. Nothing was changed.\n{rows}"
            )
    start = found[0]
    end = start + len(old) - 1
    if loose:
        shifted = _reindent(doc.lines[start - 1 : end], old, new)
        if not shifted and new:
            raise EditError(
                f"{label}: the old text matches lines {start}-{end} only with other "
                "indentation, and not by one consistent amount; copy those lines exactly. "
                f"Nothing was changed.\n{render(doc.lines, hashes, start, end)}"
            )
        new = shifted
    # `old` stays set: the lines were found by their current text, so `_validate` skips the
    # shown-before check, which a two-letter hash collision could fail.
    return Op(
        "replace",
        Anchor(start, hashes[start - 1]),
        Anchor(end, hashes[end - 1]),
        tuple(new),
        op.index,
        old=op.old,
    )


def resolve_ops(doc: TextDoc, ops: Sequence[Op], seen: Seen | None) -> list[Op]:
    """Version 2 edits as anchored ones: partial anchors completed, excerpts located, line
    counts turned into ends. Anchored edits pass through unchanged."""
    hashes = line_hashes(doc.lines)
    resolved: list[Op] = []
    for op in ops:
        label = f"edit {op.index + 1}"
        if op.kind == "excerpt":
            resolved.append(_excerpt_op(doc, hashes, op))
            continue
        if op.kind == "insert_in_place":
            resolved.append(op)  # placed below, once the deletes are known
            continue
        start = resolve_anchor(doc, hashes, op.start, seen, f"{label} start")
        end = (
            start
            if op.end == op.start
            else resolve_anchor(doc, hashes, op.end, seen, f"{label} end")
        )
        if op.count is not None:
            last = start.line + op.count - 1
            if last > len(doc.lines):
                raise EditError(
                    f"{label}: {op.count} lines from line {start.line} run past the end of the "
                    f"file ({len(doc.lines)} lines)"
                )
            end = Anchor(last, hashes[last - 1])
        if end.line < start.line:
            raise EditError(f"{label}: end {end} comes before start {start}")
        resolved.append(Op(op.kind, start, end, op.lines, op.index))
    return _place_inserts(resolved)


def _place_inserts(ops: list[Op]) -> list[Op]:
    """Version 2: an insert without an anchor goes where the call's deletes remove one block of
    lines (`[{"delete": true, "line": 18}, ..., {"delete": true, "line": 24}, {"insert": ...}]`
    replaces lines 18-24). Without such a block there is no place it could mean."""
    floating = [op for op in ops if op.kind == "insert_in_place"]
    if not floating:
        return ops
    deleted = sorted(
        {n for op in ops if op.kind == "delete" for n in range(op.start.line, op.end.line + 1)}
    )
    if not deleted or deleted != list(range(deleted[0], deleted[-1] + 1)):
        raise EditError(
            f"edit {floating[0].index + 1}: insert needs `after` or `before` with an anchor"
        )
    first = next(op.start for op in ops if op.kind == "delete" and op.start.line == deleted[0])
    return [
        Op("insert_before", first, first, op.lines, op.index)
        if op.kind == "insert_in_place"
        else op
        for op in ops
    ]


def _drop_covered_deletes(ops: list[Op]) -> list[Op]:
    """Version 2: a delete of lines that a replace in the same call also changes adds nothing.
    `[{"delete": true, "line": 15}, {"line": 15, "text": "..."}]` asks for one change."""
    replaces = [op for op in ops if op.kind == "replace"]
    return [
        op
        for op in ops
        if not (
            op.kind == "delete"
            and any(r.start.line <= op.start.line and op.end.line <= r.end.line for r in replaces)
        )
    ]


def _check(doc: TextDoc, hashes: list[str], anchor: Anchor, seen: Seen | None) -> None:
    total = len(doc.lines)
    if anchor.line < 1 or anchor.line > total:
        raise _stale(doc, hashes, anchor, f"the file has {total} lines")
    current = hashes[anchor.line - 1]
    if current != anchor.hash:
        raise _stale(doc, hashes, anchor, f"line {anchor.line} is now {anchor.line}:{current}")
    if seen is not None:
        shown = seen.get((anchor.line, anchor.hash))
        if shown is not None and shown != _content(doc.lines[anchor.line - 1]):
            raise _stale(doc, hashes, anchor, f"line {anchor.line} changed since it was shown")


def _validate(doc: TextDoc, ops: list[Op], seen: Seen | None) -> None:
    hashes = line_hashes(doc.lines)
    for op in ops:
        shown = seen if op.old is None else None
        _check(doc, hashes, op.start, shown)
        if op.end != op.start:
            _check(doc, hashes, op.end, shown)
    ranges = sorted(
        (op for op in ops if op.kind in ("replace", "delete")), key=lambda op: op.start.line
    )
    for first, second in zip(ranges, ranges[1:], strict=False):
        if second.start.line <= first.end.line:
            raise EditError(
                f"edits {first.index + 1} and {second.index + 1} overlap "
                f"({first.start}-{first.end} and {second.start}-{second.end}); "
                "combine them into one replace"
            )
    for op in ops:
        if op.kind not in ("insert_before", "insert_after"):
            continue
        gap = op.start.line - 1 if op.kind == "insert_before" else op.start.line
        for other in ranges:
            if other.start.line <= gap < other.end.line:
                raise EditError(
                    f"edit {op.index + 1} inserts inside the lines that edit {other.index + 1} "
                    f"replaces ({other.start}-{other.end}); put the new lines in that replace"
                )


def apply_ops(
    doc: TextDoc, ops: list[Op], *, seen: Seen | None = None
) -> tuple[TextDoc, list[tuple[int, int]]]:
    """The edited document and the new-file line ranges each edit produced (validated first)."""
    ops = _drop_covered_deletes(resolve_ops(doc, ops, seen))
    _validate(doc, ops, seen)
    newline = doc.newline
    inserts: dict[int, list[Op]] = defaultdict(list)  # gap index -> inserts, request order
    ranges: dict[int, Op] = {}
    for op in ops:
        if op.kind == "insert_before":
            inserts[op.start.line - 1].append(op)
        elif op.kind == "insert_after":
            inserts[op.start.line].append(op)
        else:
            ranges[op.start.line] = op
    lines: list[str] = []
    endings: list[str] = []
    regions: list[tuple[int, int]] = []

    def emit(new_lines: Sequence[str]) -> None:
        start = len(lines) + 1
        lines.extend(new_lines)
        endings.extend([newline] * len(new_lines))
        regions.append((start, len(lines)))

    for op in inserts.get(0, []):
        emit(op.lines)
    number = 1
    total = len(doc.lines)
    while number <= total:
        replaced = ranges.get(number)
        if replaced is not None:
            emit(replaced.lines)
            number = replaced.end.line + 1
            for insert in inserts.get(replaced.end.line, []):
                emit(insert.lines)
            continue
        lines.append(doc.lines[number - 1])
        endings.append(doc.endings[number - 1])
        for insert in inserts.get(number, []):
            emit(insert.lines)
        number += 1
    result = TextDoc(lines, endings, newline, doc.bom)
    _fix_endings(result, final_newline=doc.final_newline or total == 0)
    return result, regions


def _fix_endings(doc: TextDoc, *, final_newline: bool) -> None:
    for index in range(len(doc.endings) - 1):
        if not doc.endings[index]:
            doc.endings[index] = doc.newline
    if doc.endings:
        if final_newline and not doc.endings[-1]:
            doc.endings[-1] = doc.newline
        elif not final_newline:
            doc.endings[-1] = ""


# --------------------------------------------------------------------------------------------
# Writing and the edit entry point
# --------------------------------------------------------------------------------------------


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temporary file in the same directory and an atomic rename; keeps the mode."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            with contextlib.suppress(OSError):
                shutil.copymode(path, tmp)
        for attempt in range(10):
            try:
                tmp.replace(path)
                return
            except PermissionError:  # Windows: another process holds the file for a moment
                if attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def unified_diff(
    display: str, old: Sequence[str], new: Sequence[str], *, created: bool = False
) -> str:
    rows = difflib.unified_diff(
        list(old),
        list(new),
        fromfile="/dev/null" if created else f"a/{display}",
        tofile=f"b/{display}",
        n=PREVIEW_CONTEXT,
        lineterm="",
    )
    return "\n".join(rows)


@dataclass
class EditResult:
    path: str
    diff: str
    created: bool = False
    changed: bool = True
    added: int = 0
    removed: int = 0
    total_lines: int = 0
    preview: str = ""  # changed regions with fresh anchors
    regions: list[tuple[int, int]] = field(default_factory=list)


def _preview(doc: TextDoc, regions: list[tuple[int, int]], seen: Seen | None) -> str:
    if not doc.lines:
        return "(the file is now empty)"
    hashes = line_hashes(doc.lines)
    windows: list[tuple[int, int]] = []
    for start, end in sorted(regions):
        low = max(1, start - PREVIEW_CONTEXT)
        high = min(len(doc.lines), max(end, start - 1) + PREVIEW_CONTEXT)
        if windows and low <= windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], max(windows[-1][1], high))
        else:
            windows.append((low, high))
    blocks: list[str] = []
    budget = PREVIEW_MAX_LINES
    for low, high in windows:
        if budget <= 0:
            blocks.append("... (more changes; read the file for their anchors)")
            break
        high = min(high, low + budget - 1)
        blocks.append(render(doc.lines, hashes, low, high, max_line_chars=200, seen=seen))
        budget -= high - low + 1
    return "\n...\n".join(blocks)


def _result(
    display: str,
    old: TextDoc,
    new: TextDoc,
    regions: list[tuple[int, int]],
    *,
    created: bool,
    seen: Seen | None,
) -> EditResult:
    diff = unified_diff(display, old.lines, new.lines, created=created)
    body = [row for row in diff.splitlines()[2:] if not row.startswith("@@")]
    return EditResult(
        display,
        diff,
        created=created,
        changed=created or old.encode() != new.encode(),
        added=sum(1 for row in body if row.startswith("+")),
        removed=sum(1 for row in body if row.startswith("-")),
        total_lines=len(new.lines),
        preview=_preview(new, regions, seen),
        regions=regions,
    )


def edit_file(
    path: Path,
    *,
    display: str | None = None,
    edits: Any = None,
    text: Any = None,
    create: bool = False,
    seen: Seen | None = None,
    small_file_lines: int = SMALL_FILE_LINES,
) -> EditResult:
    """Apply anchored `edits`, create a file from `text`, or replace a small file with `text`.

    `seen` maps (line, hash) to the content shown to the model; when an anchor was shown for
    different content, the edit is refused even if the short hash happens to match.
    """
    display = display or path.name
    # Version 2: the empty defaults models send beside the real argument are left out.
    if edits in ([], {}, "") and text is not None:
        edits = None
    if edits is not None and isinstance(text, str) and not text.strip():
        text = None
    if edits is not None and text is not None:
        raise EditError("give either `edits` (anchored changes) or `text` (whole file), not both")
    exists = path.exists()
    if create and exists and (edits is not None or (seen and text is not None)):
        # Version 2: a stray `create` beside edits to an existing file, or `create` with the
        # whole new content of a file the model has read (rewritten as `text` would be).
        create = False
    if create and edits is not None:
        raise EditError("`create` takes `text` (the whole new file), not `edits`")
    if create or (not exists and edits is None):
        if exists:
            raise EditError(
                f"{display} already exists: change it with anchored edits from read "
                f"(or, for a file of up to {small_file_lines} lines, `text` without `create`)"
            )
        if text is None:
            raise EditError(
                f"{display} does not exist; to create it pass `create: true` and `text`"
            )
        new = TextDoc.from_lines(text_lines(text))
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, new.encode())
        regions = [(1, len(new.lines))] if new.lines else []
        return _result(display, TextDoc([], []), new, regions, created=True, seen=seen)
    if not exists:
        raise EditError(f"{display} does not exist; to create it pass `create: true` and `text`")
    if path.is_dir():
        raise EditError(f"{display} is a directory")
    if not os.access(path, os.W_OK):
        raise EditError(f"{display} is read-only")
    old = TextDoc.decode(path.read_bytes(), display)
    if edits is None:
        if text is None:
            raise EditError(f"nothing to do: give `edits` with anchors from read, e.g. [{EXAMPLE}]")
        if len(old.lines) > small_file_lines:
            raise EditError(
                f"{display} has {len(old.lines)} lines; replacing a whole file is only allowed "
                f"up to {small_file_lines} lines. Use anchored edits instead, e.g. [{EXAMPLE}]"
            )
        new = TextDoc(text_lines(text), [], old.newline, old.bom)
        new.endings = [old.newline] * len(new.lines)
        _fix_endings(new, final_newline=old.final_newline or not old.lines)
        regions = [(1, len(new.lines))] if new.lines else []
    else:
        new, regions = apply_ops(old, parse_edits(edits), seen=seen)
    result = _result(display, old, new, regions, created=False, seen=seen)
    if result.changed:
        atomic_write(path, new.encode())
    return result
