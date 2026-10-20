"""Text patches for the edit tool (edit format version 2, ADR 0038).

Some models change files with a text patch rather than tool arguments. Two kinds are read:

- the `*** Begin Patch` format, which some open-weight models are trained to write;
- unified diffs (`--- a/path`, `+++ b/path`, `@@ -l,c +l,c @@`).

Grammar of the `*** Begin Patch` format (docs/RESEARCH.md, Phase 12):

    *** Begin Patch
    *** Add File: <path>      followed by the file's lines, each written `+text`
    *** Update File: <path>   followed by hunks: `@@`, or `@@ <a line to find first>`, then
                              lines ` text` (context), `-text` (removed), `+text` (added),
                              optionally `*** End of File` (the hunk ends the file)
    *** Delete File: <path>
    *** End Patch

A hunk is found by its content, never by line numbers: its old text is its context and removed
lines, its new text its context and added lines. The search moves forward through the file, hunk
after hunk, as in the reference tools, and an `@@ <line>` header starts it at that line. A match
is tried exactly, then ignoring indentation, then also reading typographic dashes, quotes and
spaces as ASCII; trailing whitespace never counts. Context lines keep the file's own text, as in
the reference tools; after a looser match the added lines get the file's indentation (the
reference tools write them as given).

Stepswitch is lenient where models slip, as the reference parsers are, and stricter where a wrong
guess would change the wrong lines:

- Lenient: blank lines around the patch, a heredoc wrapper, markers padded with spaces, CRLF, a
  missing `*** Begin Patch` before the first file marker, a missing `@@` before the first hunk,
  a unified `@@ -l,c +l,c @@` header (its line is then a hint), a missing `*** End Patch`.
  `*** End of File` prefers a match at the end of the file and falls back to the hunk's match
  elsewhere, as the Python reference does.
- Strict: a hunk whose old text matches more than once ahead of the search position, with no
  `@@` line to tell the matches apart, is refused (the references take the first match). So is
  a file that cannot be found or created. Deleting and moving files is refused: the edit tool
  changes content only.
- A hunk with only added lines goes after its `@@` line, as in the Python reference; without
  one, at the end of the file, as in the Rust reference.
- In order: a file named twice is changed by its second section as the first left it.
- Atomic: every file is checked before any is written, so a refused patch changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from stepswitch.context import edits as E

BEGIN = "*** Begin Patch"
END = "*** End Patch"
_FILE_MARKERS = ("*** Add File:", "*** Update File:", "*** Delete File:")
_HEREDOC_RE = re.compile(r"^\s*(?:\S*apply_?patch\s*)?<<-?\s*['\"]?(\w+)['\"]?\s*$")
_UNIFIED_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class PatchError(E.EditError):
    """A patch that was refused; nothing was written."""


@dataclass
class Hunk:
    old: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    anchor: str | None = None  # the `@@ <line>` header: search from that line
    at_end: bool = False  # `*** End of File`: the hunk ends the file
    line: int | None = None  # a unified diff's old start line (a hint only)
    # (index in old, index in new) of each context line: the file keeps its own copy of these
    # lines, as in the reference tools, so a looser match never rewrites them.
    context: list[tuple[int, int]] = field(default_factory=list)

    def add(self, prefix: str, text: str) -> None:
        if prefix == " ":
            self.context.append((len(self.old), len(self.new)))
        if prefix in " -":
            self.old.append(text)
        if prefix in " +":
            self.new.append(text)


@dataclass
class FileChange:
    path: str
    kind: str  # "add", "update", "delete"
    hunks: list[Hunk] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # an added file's content
    move_to: str | None = None


def looks_like_patch(text: str) -> bool:
    """Whether a raw tool argument is a patch rather than JSON."""
    body = _normalize(text).lstrip()
    return body.startswith((BEGIN, *_FILE_MARKERS)) or bool(
        re.search(r"^--- .+\n\+\+\+ .+\n@@ ", body, re.M)
    )


def _normalize(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        heredoc = _HEREDOC_RE.match(lines[0])
        if heredoc and lines[-1].strip() == heredoc.group(1):
            lines = lines[1:-1]
    return "\n".join(lines)


def parse(text: str) -> list[FileChange]:
    """The file changes of a patch in either format."""
    body = _normalize(text)
    if body.lstrip().startswith(_FILE_MARKERS):
        body = f"{BEGIN}\n{body.lstrip()}"  # the Begin line was left out
    if body.lstrip().startswith(BEGIN):
        return _parse_begin(body)
    if re.search(r"^(---|\+\+\+) ", body, re.M):
        return _parse_unified(body)
    raise PatchError(
        "a patch starts with `*** Begin Patch` (or is a unified diff with --- and +++ lines)"
    )


def _marker(line: str, name: str) -> str | None:
    stripped = line.strip()
    head = f"*** {name}:"
    return stripped[len(head) :].strip() if stripped.startswith(head) else None


def _parse_begin(body: str) -> list[FileChange]:
    lines = body.split("\n")
    changes: list[FileChange] = []
    current: FileChange | None = None
    hunk: Hunk | None = None
    for number, raw in enumerate(lines[1:], start=2):
        stripped = raw.strip()
        if stripped == END:
            break
        if stripped.startswith("*** Environment ID:"):
            continue
        add, update, delete = (_marker(raw, n) for n in ("Add File", "Update File", "Delete File"))
        move = _marker(raw, "Move to")
        if add is not None or update is not None or delete is not None:
            path = add if add is not None else update if update is not None else delete
            kind = "add" if add is not None else "update" if update is not None else "delete"
            current, hunk = FileChange(str(path), kind), None
            changes.append(current)
            continue
        if current is None:
            raise PatchError(
                f"patch line {number}: expected `*** Add File:`, `*** Update File:` or "
                f"`*** Delete File:`, got {raw[:80]!r}"
            )
        if move is not None and current.kind == "update":
            current.move_to = move
            continue
        if current.kind == "add":
            if raw.startswith("+"):
                current.lines.append(raw[1:])
            elif not raw.strip():
                current.lines.append("")
            else:
                raise PatchError(
                    f"patch line {number}: every line of an added file starts with +, got "
                    f"{raw[:80]!r}"
                )
            continue
        if current.kind == "delete":
            if raw.strip():
                raise PatchError(f"patch line {number}: a deleted file takes no lines")
            continue
        if stripped == "*** End of File":
            if hunk is not None:
                hunk.at_end = True
            continue
        if stripped.startswith("@@"):
            unified = _UNIFIED_HUNK_RE.match(stripped)
            if unified:  # a unified header slipped into this format: its line is a hint
                hunk = Hunk(line=int(unified.group(1)))
            else:
                hunk = Hunk(anchor=stripped[2:].strip() or None)
            current.hunks.append(hunk)
            continue
        if hunk is None:
            hunk = Hunk()
            current.hunks.append(hunk)
        if raw[:1] in (" ", "-", "+"):
            hunk.add(raw[:1], raw[1:])
        elif not raw:
            hunk.add(" ", "")  # an empty line is an empty context line
        else:
            raise PatchError(
                f"patch line {number}: hunk lines start with a space, - or +, got {raw[:80]!r}"
            )
    if not changes:
        raise PatchError("the patch names no file (`*** Update File: <path>` and its hunks)")
    return changes


def _diff_path(value: str) -> str | None:
    path = value.strip().split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    return path[2:] if path.startswith(("a/", "b/")) else path


def _parse_unified(body: str) -> list[FileChange]:
    changes: list[FileChange] = []
    current: FileChange | None = None
    hunk: Hunk | None = None
    old_path: str | None = None
    rows = body.split("\n")
    for number, raw in enumerate(rows, start=1):
        if raw.startswith(("diff --git", "index ", "new file mode", "deleted file mode")):
            continue
        following = rows[number] if number < len(rows) else ""
        if raw.startswith("--- ") and following.startswith("+++ "):
            # A file header; a removed line that reads "-- ..." is not followed by +++.
            old_path, hunk = _diff_path(raw[4:]), None
            continue
        if raw.startswith("+++ ") and hunk is None:
            new_path = _diff_path(raw[4:])
            if new_path is None:
                current = FileChange(str(old_path), "delete")
            elif old_path is None:
                current = FileChange(new_path, "add")
            else:
                current = FileChange(new_path, "update")
            changes.append(current)
            continue
        if current is None:
            continue  # text before the first file header
        hunk_header = _UNIFIED_HUNK_RE.match(raw)
        if hunk_header:
            hunk = Hunk(line=int(hunk_header.group(1)))
            current.hunks.append(hunk)
            continue
        if raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        if hunk is None:
            if raw.strip():
                raise PatchError(f"diff line {number}: a hunk starts with @@ -l,c +l,c @@")
            continue
        if raw[:1] in (" ", "-", "+"):
            hunk.add(raw[:1], raw[1:])
        elif not raw:
            hunk.add(" ", "")
        else:
            raise PatchError(
                f"diff line {number}: hunk lines start with a space, - or +, got {raw[:80]!r}"
            )
    for change in changes:
        if change.kind == "add":
            change.lines = [line for hunk in change.hunks for line in hunk.new]
    if not changes:
        raise PatchError("the diff names no file (--- a/<path> and +++ b/<path>)")
    return changes


# --------------------------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------------------------


@dataclass
class Planned:
    """One file of a checked patch, ready to be written."""

    path: Path
    display: str
    old: E.TextDoc
    new: E.TextDoc
    regions: list[tuple[int, int]]
    created: bool


def _find_anchor(lines: list[str], wanted: str, start: int) -> int | None:
    """The 0-based index of the first line at or after `start` that is the `@@` line, compared
    without surrounding whitespace, then also without typographic punctuation."""
    stripped = wanted.strip()
    found = next((i for i in range(start, len(lines)) if lines[i].strip() == stripped), None)
    if found is None:
        folded = stripped.translate(E._FOLD).strip()
        found = next(
            (i for i in range(start, len(lines)) if lines[i].translate(E._FOLD).strip() == folded),
            None,
        )
    return found


def _hunk_ops(doc: E.TextDoc, change: FileChange) -> list[E.Op]:
    """The hunks of one update as anchored edits, searched forward through the file."""
    hashes = E.line_hashes(doc.lines)
    lines = doc.lines
    if not lines and all(not hunk.old and hunk.anchor is None for hunk in change.hunks):
        return [_whole([line for hunk in change.hunks for line in hunk.new])]
    ops: list[E.Op] = []
    position = 0  # 0-based: where the search for the next hunk starts
    for number, hunk in enumerate(change.hunks, start=1):
        label = f"{change.path} hunk {number}"
        if hunk.anchor is not None:
            found_anchor = _find_anchor(lines, hunk.anchor, position)
            if found_anchor is None:
                raise PatchError(
                    f"{label}: the @@ line {hunk.anchor!r} is not in the file after line "
                    f"{position}. Nothing was changed."
                )
            position = found_anchor
            if not hunk.old:
                ops.append(_insert_after(hashes, found_anchor, hunk.new, len(ops)))
                position = found_anchor + 1
                continue
        if not hunk.old:
            # Only added lines and no @@ line: they go at the end of the file.
            ops.append(_insert_after(hashes, len(lines) - 1, hunk.new, len(ops)))
            position = len(lines)
            continue
        matches, level = E.find_lines(lines, hunk.old, position)
        found = [n - 1 for n in matches]  # 0-based
        loose = level > 0
        if hunk.at_end:
            found = [i for i in found if i + len(hunk.old) == len(lines)] or found
        if not found:
            first = hunk.old[0].strip() if hunk.old else ""
            near = next((i for i, line in enumerate(lines) if first and line.strip() == first), 0)
            low, high = max(1, near - 1), min(len(lines), near + len(hunk.old) + 2)
            raise PatchError(
                f"{label}: its context and - lines are not in the file after line {position}. "
                "Copy them from the file as it is now. Nothing was changed. "
                f"Lines {low}-{high} are now:\n{E.render(lines, hashes, low, high)}"
            )
        if len(found) > 1 and hunk.anchor is None:
            if hunk.line is not None:
                found = [min(found, key=lambda i: abs(i + 1 - int(hunk.line or 0)))]
            else:
                rows = "\n...\n".join(
                    E.render(lines, hashes, i + 1, i + len(hunk.old)) for i in found[:4]
                )
                raise PatchError(
                    f"{label}: its context and - lines match {len(found)} places; add context "
                    f"lines or an `@@ <line>` header. Nothing was changed.\n{rows}"
                )
        start = found[0]
        end = start + len(hunk.old) - 1
        new = list(hunk.new)
        if loose:
            new = E._reindent(lines[start : end + 1], hunk.old, new)
            if not new and hunk.new:
                raise PatchError(
                    f"{label}: lines {start + 1}-{end + 1} match only with other indentation, "
                    "and not by one consistent amount; copy them exactly. Nothing was changed."
                )
        for in_old, in_new in hunk.context:
            new[in_new] = lines[start + in_old]  # the file's own copy of a context line
        ops.append(
            E.Op(
                "replace",
                E.Anchor(start + 1, hashes[start]),
                E.Anchor(end + 1, hashes[end]),
                tuple(new),
                len(ops),
            )
        )
        position = end + 1
    return ops


def _insert_after(hashes: list[str], index: int, new: list[str], order: int) -> E.Op:
    anchor = E.Anchor(index + 1, hashes[index])
    return E.Op("insert_after", anchor, anchor, tuple(new), order)


def _whole(new: list[str]) -> E.Op:
    return E.Op("whole", E.Anchor(1, "?"), E.Anchor(1, "?"), tuple(new))


def plan(
    changes: list[FileChange], resolve: Callable[[str, bool], tuple[Path, str]]
) -> list[Planned]:
    """Check every file of a patch and compute its new content; write nothing.

    `resolve(path, existing)` gives the file's absolute path and display path; `existing` says
    the patch changes a file that should already be there. Hunks are found by their text, so no
    anchor shown earlier is needed or checked."""
    planned: dict[Path, Planned] = {}
    for change in changes:
        if change.kind == "delete":
            raise PatchError(
                f"{change.path}: deleting files is not supported here; change their content "
                "instead. Nothing was changed."
            )
        if change.move_to:
            raise PatchError(
                f"{change.path}: moving files is not supported here. Nothing was changed."
            )
        path, display = resolve(change.path, change.kind == "update")
        earlier = planned.get(path)
        if change.kind == "add":
            if earlier is not None or path.exists():
                raise PatchError(
                    f"{display} already exists: use `*** Update File: {display}` with hunks. "
                    "Nothing was changed."
                )
            new = E.TextDoc.from_lines(change.lines)
            regions = [(1, len(new.lines))] if new.lines else []
            planned[path] = Planned(path, display, E.TextDoc([], []), new, regions, True)
            continue
        if earlier is None and not path.is_file():
            raise PatchError(f"{display} does not exist. Nothing was changed.")
        if not change.hunks:
            raise PatchError(f"{display}: the update has no hunks. Nothing was changed.")
        base = earlier.new if earlier is not None else E.TextDoc.decode(path.read_bytes(), display)
        ops = _hunk_ops(base, change)
        if ops and ops[0].kind == "whole":
            new = E.TextDoc(list(ops[0].lines), [], base.newline, base.bom)
            new.endings = [base.newline] * len(new.lines)
            regions = [(1, len(new.lines))] if new.lines else []
        else:
            new, regions = E.apply_ops(base, ops)
        if earlier is not None:
            # The second section changed the first one's result: the diff still runs from the
            # file on disk, and the preview shows the newest changes.
            earlier.new, earlier.regions = new, regions
        else:
            planned[path] = Planned(path, display, base, new, regions, False)
    return list(planned.values())


def write(planned: list[Planned], seen_for: Callable[[str], E.Seen | None]) -> list[E.EditResult]:
    """Write a checked patch and describe each file's change."""
    results = []
    for item in planned:
        if item.created:
            item.path.parent.mkdir(parents=True, exist_ok=True)
        result = E._result(
            item.display,
            item.old,
            item.new,
            item.regions,
            created=item.created,
            seen=seen_for(item.display),
        )
        if result.changed:
            E.atomic_write(item.path, item.new.encode())
        results.append(result)
    return results
