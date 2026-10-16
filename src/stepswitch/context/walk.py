"""Which files the context services look at: one skip list and one set of ignore rules.

The repo map, the Python grep fallback and the semantic index all walk a tree the same way,
and the ripgrep invocation in `search` is built to match it:

* hidden files and directories (name starts with ".") are skipped, as ripgrep does by default;
  this also keeps `.git`, `.stepswitch`, `.venv` and files such as `.env` out;
* directories in `SKIP_DIRS` (dependency folders, caches, build outputs) are skipped;
* `.gitignore` and `.ignore` files at the root and in subdirectories are honoured for the
  common pattern forms (see `IgnoreMatcher`);
* symbolic links are not followed, so a walk never leaves the tree;
* files over `MAX_FILE_BYTES` are skipped; callers skip binary files (a NUL byte in the first
  8 KiB) when they read them.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".stepswitch",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".eggs",
        "build",
        "dist",
        "target",
        ".next",
        ".nuxt",
        ".gradle",
        "htmlcov",
    }
)
SKIP_DIR_SUFFIXES = (".egg-info",)
MAX_FILE_BYTES = 1_000_000
BINARY_SNIFF_BYTES = 8192
# Lowest precedence first, as in ripgrep (.rgignore > .ignore > .gitignore).
IGNORE_FILES = (".gitignore", ".ignore", ".rgignore")


def skip_dir(name: str) -> bool:
    return name.startswith(".") or name in SKIP_DIRS or name.endswith(SKIP_DIR_SUFFIXES)


def looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def read_text(path: Path, max_bytes: int | None = MAX_FILE_BYTES) -> tuple[str, bytes] | None:
    """(text, raw bytes) of a UTF-8-ish text file, or None for binary, missing or large files."""
    try:
        if max_bytes is not None and path.stat().st_size > max_bytes:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if looks_binary(data):
        return None
    return data.decode("utf-8", errors="replace"), data


# --------------------------------------------------------------------------------------------
# gitignore-style patterns
# --------------------------------------------------------------------------------------------


def translate_glob(pattern: str) -> str:
    """A gitignore-style glob as a regular expression body (no anchors).

    `*` and `?` never match "/", `**/` matches zero or more directories, a trailing `/**`
    matches everything inside, `[...]` is a character class (`[!...]` negates), `{a,b}` is an
    alternation (ripgrep accepts it in globs) and a backslash escapes the next character.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**", i):
                i += 2
                if i < n and pattern[i] == "/":
                    out.append("(?:.*/)?")
                    i += 1
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 2 if pattern.startswith("[!", i) else i + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = end
        elif char == "{":
            end = pattern.find("}", i)
            if end == -1:
                out.append(re.escape(char))
            else:
                options = pattern[i + 1 : end].split(",")
                out.append("(?:" + "|".join(translate_glob(o) for o in options) + ")")
                i = end
        elif char == "\\" and i + 1 < n:
            i += 1
            out.append(re.escape(pattern[i]))
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class GlobRule:
    regex: re.Pattern[str]
    negate: bool = False
    dir_only: bool = False

    def matches(self, rel: str, is_dir: bool) -> bool:
        return (is_dir or not self.dir_only) and self.regex.match(rel) is not None


def compile_glob(pattern: str, *, allow_negation: bool = True) -> GlobRule | None:
    """One gitignore-style line as a rule matched against a "/"-separated relative path.

    A pattern without a "/" (other than a trailing one) matches the name at any depth; one
    with a "/" is anchored to the directory of the ignore file (or the search root).
    """
    line = pattern.rstrip(" \t\r\n")
    if not line or line.startswith("#"):
        return None
    negate = False
    if allow_negation and line.startswith("!"):
        negate, line = True, line[1:]
    elif line.startswith(("\\!", "\\#")):
        line = line[1:]
    dir_only = line.endswith("/")
    line = line.rstrip("/")
    if not line:
        return None
    anchored = "/" in line
    line = line.lstrip("/")
    prefix = "^" if anchored else "^(?:.*/)?"
    try:
        regex = re.compile(prefix + translate_glob(line) + "$")
    except re.error:
        return None
    return GlobRule(regex, negate, dir_only)


class IgnoreMatcher:
    """`.gitignore`, `.ignore` and `.rgignore` files at the root and in subdirectories.

    Supported: comments, blank lines, negation (`!`), directory-only patterns (`dir/`),
    anchored patterns (containing "/"), `*`, `?`, `[...]` and `**`. The last matching pattern
    wins, and deeper files override shallower ones. Not supported: global excludes, the
    repository's `info/exclude`, and escaped trailing spaces.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._rules: dict[str, list[GlobRule]] = {}

    def _rules_for(self, directory: str) -> list[GlobRule]:
        cached = self._rules.get(directory)
        if cached is not None:
            return cached
        rules: list[GlobRule] = []
        base = self.root / directory if directory else self.root
        for name in IGNORE_FILES:
            try:
                text = (base / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                rule = compile_glob(line)
                if rule is not None:
                    rules.append(rule)
        self._rules[directory] = rules
        return rules

    def ignored(self, rel: str, is_dir: bool) -> bool:
        """Whether the root-relative path `rel` ("/"-separated) is ignored."""
        parts = rel.split("/")
        result = False
        for depth in range(len(parts)):
            directory = "/".join(parts[:depth])
            sub = "/".join(parts[depth:])
            for rule in self._rules_for(directory):
                if rule.matches(sub, is_dir):
                    result = not rule.negate
        return result


@dataclass(frozen=True)
class GlobFilter:
    """A user glob for search (`*.py`, `src/**/*.ts`, `!*_test.go`), ripgrep semantics.

    A glob without "/" matches file names at any depth; one with "/" is matched against the
    root-relative path. An include glob also selects files that `.gitignore` lists, as
    ripgrep's `--glob` overrides other ignore rules; hidden files and skipped directories stay
    out either way.
    """

    rule: GlobRule

    @classmethod
    def parse(cls, glob: str) -> GlobFilter | None:
        rule = compile_glob(glob.strip())
        return cls(rule) if rule is not None else None

    @property
    def include(self) -> bool:
        return not self.rule.negate

    def matches(self, rel: str) -> bool:
        return self.rule.matches(rel, False)


def iter_files(
    root: Path,
    start: Path | None = None,
    *,
    matcher: IgnoreMatcher | None = None,
    glob: GlobFilter | None = None,
    max_bytes: int | None = MAX_FILE_BYTES,
) -> Iterator[tuple[str, Path]]:
    """Yield (root-relative POSIX path, path) for the files a context service should see.

    `start` (a file or directory inside `root`) is searched even when it is hidden or ignored
    itself, as ripgrep does for explicit paths; the rules apply below it. Order is by name,
    depth first, so results are deterministic.
    """
    matcher = matcher or IgnoreMatcher(root)
    begin = start or root
    rel_begin = "" if begin == root else begin.relative_to(root).as_posix()
    if begin.is_file():
        if not begin.is_symlink() and _size_ok(begin, max_bytes):
            yield rel_begin, begin
        return
    yield from _walk(begin, rel_begin, matcher, glob, max_bytes)


def _size_ok(path: Path, max_bytes: int | None) -> bool:
    if max_bytes is None:
        return True
    try:
        return path.stat().st_size <= max_bytes
    except OSError:
        return False


def _walk(
    directory: Path,
    rel_dir: str,
    matcher: IgnoreMatcher,
    glob: GlobFilter | None,
    max_bytes: int | None,
) -> Iterator[tuple[str, Path]]:
    try:
        # os.scandir: one directory read with cached file types, much faster than
        # Path.iterdir() plus a stat per entry on Windows.
        with os.scandir(directory) as handle:
            entries = sorted(handle, key=lambda entry: entry.name)
    except OSError:
        return
    for entry in entries:
        name = entry.name
        rel = f"{rel_dir}/{name}" if rel_dir else name
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if skip_dir(name) or matcher.ignored(rel, True):
                    continue
                if glob is not None and not glob.include and glob.rule.matches(rel, True):
                    continue
                yield from _walk(Path(entry.path), rel, matcher, glob, max_bytes)
                continue
            if not entry.is_file(follow_symlinks=False) or name.startswith("."):
                continue
            if glob is not None:
                hit = glob.matches(rel)
                if glob.include and not hit:
                    continue
                if not glob.include and hit:
                    continue
                if not (glob.include and hit) and matcher.ignored(rel, False):
                    continue
            elif matcher.ignored(rel, False):
                continue
            if max_bytes is not None and entry.stat(follow_symlinks=False).st_size > max_bytes:
                continue
        except OSError:
            continue
        yield rel, Path(entry.path)
