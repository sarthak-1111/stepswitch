"""A small TOML writer for the files Stepswitch generates.

The standard library reads TOML (`tomllib`) but cannot write it. Stepswitch writes registry
entries, `ssw init` templates and managed blocks inside `.stepswitch/local.toml`, where the
rest of the file belongs to the user and must keep its comments. Supported values: str, bool,
int, float, date, datetime, lists of those, inline tables, tables and arrays of tables. `None`
values are left out, since TOML has no null.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def key(name: str) -> str:
    return name if _BARE_KEY_RE.match(name) else string(name)


def string(text: str) -> str:
    out = ['"']
    for char in text:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif char == "\r":
            out.append("\\r")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def value(item: Any) -> str:
    """One TOML value; mappings become inline tables."""
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, int):
        return str(item)
    if isinstance(item, float):
        if math.isnan(item):
            return "nan"
        if math.isinf(item):
            return "inf" if item > 0 else "-inf"
        return repr(item)
    if isinstance(item, str):
        return string(item)
    if isinstance(item, Path):
        return string(item.as_posix())
    if isinstance(item, datetime | date | time):
        return item.isoformat()
    if isinstance(item, Mapping):
        pairs = (f"{key(str(k))} = {value(v)}" for k, v in item.items() if v is not None)
        return "{ " + ", ".join(pairs) + " }" if item else "{}"
    if isinstance(item, Sequence):
        return "[" + ", ".join(value(v) for v in item if v is not None) + "]"
    raise TypeError(f"cannot write {type(item).__name__} as TOML")


def _is_table(item: Any) -> bool:
    return isinstance(item, Mapping)


def _is_table_array(item: Any) -> bool:
    return (
        isinstance(item, Sequence)
        and not isinstance(item, str)
        and bool(item)
        and all(isinstance(v, Mapping) for v in item)
    )


def _is_plain(item: Any) -> bool:
    return item is not None and not _is_table(item) and not _is_table_array(item)


def _emit(lines: list[str], path: list[str], table: Mapping[str, Any]) -> None:
    for name, item in table.items():
        if _is_plain(item):
            lines.append(f"{key(str(name))} = {value(item)}")
    for name, item in table.items():
        dotted = ".".join(key(p) for p in [*path, str(name)])
        if _is_table(item):
            # A table holding only sub-tables gets no header of its own (TOML defines it
            # implicitly), so a managed block can add [host.detected] to a file that already
            # has a hand-written [host] table.
            if not item or any(_is_plain(v) for v in item.values()):
                if lines:
                    lines.append("")
                lines.append(f"[{dotted}]")
            _emit(lines, [*path, str(name)], item)
        elif _is_table_array(item):
            for entry in item:
                if lines:
                    lines.append("")
                lines.append(f"[[{dotted}]]")
                _emit(lines, [*path, str(name)], entry)


def dumps(data: Mapping[str, Any], *, header: str = "") -> str:
    """Serialize a mapping as a TOML document, optionally after a comment header."""
    lines: list[str] = []
    _emit(lines, [], data)
    body = "\n".join(lines) + "\n"
    text = (header.rstrip("\n") + "\n\n" if header else "") + body
    tomllib.loads(text)  # never write something that does not read back
    return text


# --------------------------------------------------------------------------------------------
# Managed blocks inside user-owned files
# --------------------------------------------------------------------------------------------


def _markers(name: str) -> tuple[str, str]:
    return (
        f"# >>> {name}: managed by ssw; edits inside this block are overwritten >>>",
        f"# <<< {name} <<<",
    )


def replace_block(text: str, name: str, body: str) -> str:
    """Insert or replace the managed block `name`, leaving the rest of the file untouched.

    The block is appended at the end of the file, so its body must start with a table header;
    otherwise its keys would join whatever table precedes it.
    """
    first = next(
        (line.strip() for line in body.splitlines() if line.strip() and not line.startswith("#")),
        "",
    )
    if first and not first.startswith("["):
        raise ValueError("a managed block must start with a table header")
    begin, end = _markers(name)
    block = f"{begin}\n{body.strip()}\n{end}\n"
    pattern = re.compile(rf"^{re.escape(begin)}\n.*?^{re.escape(end)}[ \t]*\n?", re.M | re.S)
    if pattern.search(text):
        return pattern.sub(lambda _match: block, text, count=1)
    if not text:
        return block
    if text.endswith("\n\n"):
        separator = ""
    elif text.endswith("\n"):
        separator = "\n"
    else:
        separator = "\n\n"
    return text + separator + block


def atomic_write(path: Path, text: str) -> None:
    """Write via a temporary file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_block(path: Path, name: str, data: Mapping[str, Any]) -> None:
    """Write `data` as the managed block `name` of the TOML file at `path`.

    The result is parsed before it replaces the file, so a conflict with hand-written tables
    raises `tomllib.TOMLDecodeError` and leaves the file as it was.
    """
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = replace_block(current, name, dumps(data))
    tomllib.loads(updated)
    atomic_write(path, updated)
