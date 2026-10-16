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
    line: int
    hash: str

    def __str__(self) -> str:
        return f"{self.line}:{self.hash}"


_ANCHOR_RE = re.compile(rf"^\s*[Ll]?(\d+)\s*[:#]\s*([A-Za-z]{{{HASH_LEN}}})(?![A-Za-z])")


def parse_anchor(value: Any, what: str = "anchor") -> Anchor:
    """`"12:ab"`, `"12:ab|text..."`, `{"line": 12, "hash": "ab"}` (case-insensitive hash)."""
    if isinstance(value, Anchor):
        return value
    if isinstance(value, Mapping):
        line, digest = value.get("line"), value.get("hash")
        if isinstance(line, str) and line.strip().isdigit():
            line = int(line)
        if isinstance(line, int) and isinstance(digest, str):
            return parse_anchor(f"{line}:{digest}", what)
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        raise EditError(
            f"{what} {value} has no hash: copy the full anchor `<line>:<hash>` from read output, "
            "for example 12:ab"
        )
    if isinstance(value, str):
        match = _ANCHOR_RE.match(value)
        if match:
            return Anchor(int(match.group(1)), match.group(2).lower())
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
START_KEYS = ("start", "from", "anchor", "line", "at", "begin", "start_anchor", "first")
END_KEYS = ("end", "to", "until", "stop", "end_anchor", "last")
TEXT_KEYS = ("text", "content", "new_text", "replacement", "code", "lines", "new", "with")
EXAMPLE = '{"op": "replace", "start": "12:ab", "end": "14:cd", "text": "new lines"}'


@dataclass(frozen=True)
class Op:
    kind: str  # "replace", "delete", "insert_before" or "insert_after"
    start: Anchor
    end: Anchor
    lines: tuple[str, ...] = ()
    index: int = 0  # position in the request, for messages


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
    text_raw = _first(data, TEXT_KEYS)
    after, before = data.get("after"), data.get("before")
    if kind in (None, "insert"):
        if after is not None:
            kind = "insert_after"
        elif before is not None:
            kind = "insert_before"
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
            raise EditError(f"{label}: {kind} needs `start` (and `end` for several lines)")
        start = parse_anchor(start_raw, f"{label} start")
        end = parse_anchor(end_raw, f"{label} end") if end_raw is not None else start
        if end.line < start.line:
            raise EditError(f"{label}: end {end} comes before start {start}")
    if kind == "delete":
        return Op(kind, start, end, (), index)
    if text_raw is None:
        hint = " (use op delete to remove lines)" if kind == "replace" else ""
        raise EditError(f"{label}: {kind} needs `text`{hint}")
    return Op(kind, start, end, tuple(text_lines(text_raw, f"{label} text")), index)


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
        _check(doc, hashes, op.start, seen)
        if op.end != op.start:
            _check(doc, hashes, op.end, seen)
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
    if edits is not None and text is not None:
        raise EditError("give either `edits` (anchored changes) or `text` (whole file), not both")
    if create and edits is not None:
        raise EditError("`create` takes `text` (the whole new file), not `edits`")
    exists = path.exists()
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
