"""Text search for the agent: `grep` and `semantic` (SPEC §13 TK9).

`grep` uses ripgrep when it is installed (found on the absolute PATH entries only; nothing is
downloaded) and a pure-Python walk otherwise. Both see the same files (`stepswitch.context.walk`:
hidden files, skipped directories, `.gitignore`, files over 1 MB and binary files are left out),
take the same glob forms and return the same `Match` rows. Patterns are regular expressions; one
that does not compile is searched as plain text, and a pattern ripgrep's regex engine rejects
(look-around, back-references) is searched with the Python engine. ripgrep runs with fixed flags
and `--no-config`, so no user configuration file changes its behaviour.

`semantic` splits files into chunks (one per definition from the repo map, line windows for the
rest), scores them with BM25 over identifier-aware tokens, and, when an embedder is given, also
by cosine similarity of embeddings, fused by reciprocal rank. Without an embedder every hit is
marked `mode="keyword"`. Chunks and embeddings are cached by content hash. Bulk text that is not
code (translation catalogs, minified bundles, source maps, lock files, SVG) is not indexed;
`grep` still searches it. A search can be limited to a file or directory.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import math
import re
import subprocess
import sys
import threading
from array import array
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

from stepswitch.context import proc
from stepswitch.context.repomap import RepoMap, language_for, words
from stepswitch.context.walk import (
    MAX_FILE_BYTES,
    SKIP_DIR_SUFFIXES,
    SKIP_DIRS,
    GlobFilter,
    IgnoreMatcher,
    iter_files,
    looks_binary,
)

MAX_LINE_CHARS = 240
RIPGREP_TIMEOUT = 60.0


@dataclass(frozen=True)
class Match:
    """One matching line."""

    path: str  # POSIX, relative to the search root
    line: int  # 1-based
    column: int  # 1-based, in characters
    text: str  # the line without its line ending; very long lines are shortened


@dataclass
class GrepReport:
    matches: list[Match]
    engine: str  # "ripgrep" or "python"
    truncated: bool = False
    literal: bool = False  # the pattern was not a valid regex and was searched as plain text
    note: str | None = None


class SearchError(ValueError):
    """A bad search request (path outside the root, missing path)."""


def ripgrep_path() -> str | None:
    """ripgrep from the absolute PATH entries; on Windows only a real `.exe` (a batch-file
    wrapper would pass the pattern through the command interpreter)."""
    found = proc.which("rg")
    if found and sys.platform == "win32" and not found.lower().endswith(".exe"):
        return None
    return found


def _clip(text: str, column: int) -> str:
    if len(text) <= MAX_LINE_CHARS:
        return text
    start = max(0, min(column - 1 - MAX_LINE_CHARS // 3, len(text) - MAX_LINE_CHARS))
    clipped = text[start : start + MAX_LINE_CHARS]
    head = "..." if start > 0 else ""
    tail = "..." if start + MAX_LINE_CHARS < len(text) else ""
    return head + clipped + tail


def _start_path(root: Path, path: str | None) -> Path | None:
    if path is None or path.strip() in {"", ".", "./"}:
        return None
    candidate = (root / path.strip()).resolve()
    if not candidate.is_relative_to(root):
        raise SearchError(f"path {path!r} is outside the repository")
    if not candidate.exists():
        raise SearchError(f"no such file or directory: {path}")
    return candidate


def grep(
    root: Path,
    pattern: str,
    *,
    path: str | None = None,
    glob: str | None = None,
    ignore_case: bool = False,
    max_results: int = 200,
) -> list[Match]:
    """Lines matching `pattern` under `root` (see the module docstring for the rules)."""
    return run_grep(
        root, pattern, path=path, glob=glob, ignore_case=ignore_case, max_results=max_results
    ).matches


def run_grep(
    root: Path,
    pattern: str,
    *,
    path: str | None = None,
    glob: str | None = None,
    ignore_case: bool = False,
    max_results: int = 200,
    use_ripgrep: bool | None = None,
) -> GrepReport:
    """`grep` with details: which engine ran, whether results were cut, plain-text fallback."""
    root = root.resolve()
    start = _start_path(root, path)
    flags = re.IGNORECASE if ignore_case else 0
    literal = False
    try:
        regex = re.compile(pattern, flags)
    except re.error:
        literal = True
        regex = re.compile(re.escape(pattern), flags)
    rg = ripgrep_path() if use_ripgrep is not False else None
    note = None
    if rg is not None:
        report = _ripgrep(rg, root, pattern, start, glob, ignore_case, literal, max_results)
        if report is not None:
            report.literal = literal
            return report
        note = "ripgrep could not parse this pattern; searched with the Python engine"
    matches, truncated = _python_grep(root, regex, start, glob, max_results)
    return GrepReport(matches, "python", truncated, literal, note)


def _ripgrep_args(
    rg: str,
    pattern: str,
    start_rel: str | None,
    glob: str | None,
    ignore_case: bool,
    literal: bool,
) -> list[str]:
    args = [
        rg,
        "--json",
        "--no-config",
        "--no-require-git",
        "--no-ignore-parent",
        "--no-ignore-global",
        "--no-messages",
        "--sort",
        "path",
        "--max-filesize",
        str(MAX_FILE_BYTES),
    ]
    if ignore_case:
        args.append("--ignore-case")
    if literal:
        args.append("--fixed-strings")
    if glob:
        args += ["--glob", glob.strip()]
    # Later globs win in ripgrep, so these exclusions hold even against an include glob.
    args += ["--glob", "!.*"]
    for name in sorted(SKIP_DIRS):
        args += ["--glob", f"!{name}/"]
    args += ["--glob", f"!*{SKIP_DIR_SUFFIXES[0]}/"]
    args += ["--regexp", pattern, "--"]
    if start_rel:
        args.append(start_rel)
    return args


def _json_bytes(value: Any) -> bytes:
    """The bytes of a ripgrep JSON "arbitrary data" object: {"text": ...} for UTF-8 data,
    {"bytes": base64} for anything else."""
    if not isinstance(value, dict):
        return b""
    if "text" in value:
        return str(value["text"]).encode("utf-8", errors="surrogatepass")
    with contextlib.suppress(ValueError, TypeError):
        return base64.b64decode(value.get("bytes", ""))
    return b""


def _json_text(value: Any) -> str:
    return _json_bytes(value).decode("utf-8", errors="replace")


def parse_ripgrep_json(lines: Sequence[str]) -> list[Match]:
    """Matches from ripgrep `--json` output lines (only "match" messages are used)."""
    matches: list[Match] = []
    for line in lines:
        match = _parse_ripgrep_line(line)
        if match is not None:
            matches.append(match)
    return matches


def _parse_ripgrep_line(line: str) -> Match | None:
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(message, dict) or message.get("type") != "match":
        return None
    data = message.get("data") or {}
    path = PurePath(_json_text(data.get("path"))).as_posix()
    if path.startswith("./"):
        path = path[2:]
    raw = _json_bytes(data.get("lines"))
    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
    submatches = data.get("submatches") or []
    byte_start = submatches[0].get("start", 0) if submatches else 0
    # Offsets count bytes of the original line; the column counts characters.
    column = len(raw[: int(byte_start)].decode("utf-8", errors="replace")) + 1
    return Match(path, int(data.get("line_number") or 0), column, _clip(text, column))


def _ripgrep(
    rg: str,
    root: Path,
    pattern: str,
    start: Path | None,
    glob: str | None,
    ignore_case: bool,
    literal: bool,
    max_results: int,
) -> GrepReport | None:
    """Run ripgrep; None when it rejected the pattern (the caller falls back to Python)."""
    start_rel = start.relative_to(root).as_posix() if start is not None else None
    args = _ripgrep_args(rg, pattern, start_rel, glob, ignore_case, literal)
    matches: list[Match] = []
    truncated = False
    with subprocess.Popen(
        args,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=proc.minimal_env(),
        **proc.popen_kwargs(),
    ) as child:
        assert child.stdout is not None and child.stderr is not None
        watchdog = threading.Timer(RIPGREP_TIMEOUT, child.kill)  # ripgrep has no children
        watchdog.daemon = True
        watchdog.start()
        for raw in child.stdout:
            match = _parse_ripgrep_line(raw.decode("utf-8", errors="replace"))
            if match is None:
                continue
            if len(matches) >= max_results:
                truncated = True
                break
            matches.append(match)
        if truncated:
            child.kill()
        try:
            _, stderr = child.communicate(timeout=RIPGREP_TIMEOUT)
        except subprocess.TimeoutExpired:
            child.kill()
            stderr = b""
        finally:
            watchdog.cancel()
    if child.returncode == 2 and not truncated and not matches:
        message = stderr.decode("utf-8", errors="replace")
        if "regex" in message.lower() or "error parsing" in message.lower():
            return None
    return GrepReport(matches, "ripgrep", truncated)


def _python_grep(
    root: Path,
    regex: re.Pattern[str],
    start: Path | None,
    glob: str | None,
    max_results: int,
) -> tuple[list[Match], bool]:
    matches: list[Match] = []
    glob_filter = GlobFilter.parse(glob) if glob else None
    for rel, file in iter_files(root, start, matcher=IgnoreMatcher(root), glob=glob_filter):
        try:
            data = file.read_bytes()
        except OSError:
            continue
        if looks_binary(data):
            continue
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.split("\n"), start=1):
            line = line.rstrip("\r")
            found = regex.search(line)
            if found is None:
                continue
            if len(matches) >= max_results:
                return matches, True
            column = found.start() + 1
            matches.append(Match(rel, number, column, _clip(line, column)))
    return matches, False


# --------------------------------------------------------------------------------------------
# Semantic search
# --------------------------------------------------------------------------------------------

STOPWORDS = words(
    "the a an of and or to in on at by for with from is are was were be been it its this that "
    "these those as if else elif then not no do does did can could should would will shall may "
    "def class return import self cls none true false null let var const fn func function "
    "public private static void new int str"
)
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
WINDOW_LINES = 40
WINDOW_OVERLAP = 10
MAX_CHUNK_LINES = 120
EMBED_CHARS = 2000
EMBED_LIMIT = 2000  # embed every chunk up to this many; beyond it, only keyword candidates
RRF_K = 60
UNINDEXED_SUFFIXES = (".po", ".pot", ".min.js", ".min.css", ".map", ".lock", ".svg")
_TERMS: dict[str, tuple[str, ...]] = {}
_TERMS_LIMIT = 500_000  # distinct words whose terms are remembered


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _terms_of(word: str) -> tuple[str, ...]:
    """The terms of one word, remembered: the same identifiers recur all over a repository."""
    found = _TERMS.get(word)
    if found is None:
        parts = [p.lower() for chunk in word.split("_") for p in _CAMEL_RE.findall(chunk)]
        whole = word.strip("_").lower()
        terms = [whole] if len(parts) > 1 and whole not in STOPWORDS else []
        terms += [_stem(p) for p in parts if len(p) >= 2 and p not in STOPWORDS]
        found = tuple(terms)
        if len(_TERMS) < _TERMS_LIMIT:
            _TERMS[word] = found
    return found


def tokenize(text: str) -> list[str]:
    """Identifier-aware terms: `parseHttpHeader` -> parsehttpheader, parse, http, header."""
    terms: list[str] = []
    for word in _WORD_RE.findall(text):
        terms += _terms_of(word)
    return terms


@dataclass(frozen=True)
class Chunk:
    path: str
    start: int  # 1-based, inclusive
    end: int
    symbol: str | None
    text: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(f"{self.path}\0{self.text}".encode()).hexdigest()


@dataclass(frozen=True)
class Hit:
    path: str
    start: int
    end: int
    symbol: str | None
    score: float
    snippet: str
    mode: str  # "keyword" or "hybrid"


@dataclass
class _IndexedFile:
    sha: str
    chunks: list[Chunk]
    terms: list[Counter[str]]


@dataclass
class SearchResult:
    hits: list[Hit]
    mode: str
    note: str | None = None
    chunks: int = 0


def _windows(lines: list[str], first: int, last: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = first
    while start <= last:
        end = min(last, start + WINDOW_LINES - 1)
        spans.append((start, end))
        if end == last:
            break
        start = end + 1 - WINDOW_OVERLAP
    return spans


def chunk_file(rel: str, text: str, definitions: Sequence[tuple[str, int, int]]) -> list[Chunk]:
    """Chunks for one file: one per top-level definition (long ones split into windows) and
    line windows over everything the definitions do not cover."""
    lines = text.split("\n")
    total = len(lines)
    spans: list[tuple[int, int, str | None]] = []
    covered = 0
    for name, start, end in sorted(definitions, key=lambda d: (d[1], -d[2])):
        start, end = max(1, start), min(total, max(start, end))
        if start <= covered:
            continue
        if start > covered + 1:
            spans += [(a, b, None) for a, b in _windows(lines, covered + 1, start - 1)]
        if end - start + 1 > MAX_CHUNK_LINES:
            spans += [(a, b, name) for a, b in _windows(lines, start, end)]
        else:
            spans.append((start, end, name))
        covered = end
    if covered < total:
        spans += [(a, b, None) for a, b in _windows(lines, covered + 1, total)]
    chunks = []
    for start, end, symbol in spans:
        body = "\n".join(lines[start - 1 : end])
        if body.strip():
            chunks.append(Chunk(rel, start, end, symbol, body))
    return chunks


class SearchIndex:
    """BM25 index over chunks of the files under `root`, refreshed by content hash."""

    def __init__(
        self,
        root: Path,
        *,
        repo_map: RepoMap | None = None,
        cache_dir: Path | None = None,
        exclude: Callable[[str], bool] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.repo_map = repo_map or RepoMap(self.root, cache_dir=cache_dir, exclude=exclude)
        self.cache_dir = cache_dir
        self.exclude = exclude  # root-relative paths to leave out (e.g. privacy deny lists)
        self._files: dict[str, _IndexedFile] = {}
        self._df: Counter[str] = Counter()
        self._avg_len = 1.0
        self._embeddings: dict[tuple[str, str], list[float]] = {}

    # -- index -------------------------------------------------------------------------------

    def refresh(self) -> None:
        seen: set[str] = set()
        changed = False
        self.repo_map.refresh()  # once: asking it about each file must not walk the tree again
        for rel, path in iter_files(self.root, matcher=IgnoreMatcher(self.root)):
            if self.exclude is not None and self.exclude(rel):
                continue
            if rel.lower().endswith(UNINDEXED_SUFFIXES):
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if looks_binary(data):
                continue
            seen.add(rel)
            sha = hashlib.sha256(data).hexdigest()
            entry = self._files.get(rel)
            if entry is not None and entry.sha == sha:
                continue
            text = data.decode("utf-8", errors="replace")
            definitions: list[tuple[str, int, int]] = []
            if language_for(rel) is not None:
                tags = self.repo_map.tags(rel, refresh=False)
                if tags is not None:
                    definitions = [(t.name, t.line, t.end_line) for t in tags.tags if t.depth == 0]
            chunks = chunk_file(rel, text, definitions)
            # The path and the symbol name (twice) count as terms of every chunk.
            terms = [
                Counter(tokenize(f"{rel} {c.symbol or ''} {c.symbol or ''} {c.text}"))
                for c in chunks
            ]
            self._files[rel] = _IndexedFile(sha, chunks, terms)
            changed = True
        for rel in set(self._files) - seen:
            del self._files[rel]
            changed = True
        if changed or not self._df:
            self._df = Counter()
            lengths = 0
            count = 0
            for entry in self._files.values():
                for counts in entry.terms:
                    self._df.update(counts.keys())
                    lengths += sum(counts.values())
                    count += 1
            self._avg_len = lengths / count if count else 1.0

    def _all(self) -> list[tuple[Chunk, Counter[str]]]:
        return [
            (chunk, terms)
            for rel in sorted(self._files)
            for chunk, terms in zip(self._files[rel].chunks, self._files[rel].terms, strict=True)
        ]

    def keyword_scores(self, query: str) -> list[tuple[float, int]]:
        """(BM25 score, chunk position in `_all()`) for chunks with a positive score."""
        k1, b = 1.2, 0.75
        terms = set(tokenize(query))
        everything = self._all()
        total = len(everything)
        scored: list[tuple[float, int]] = []
        for position, (_, counts) in enumerate(everything):
            length = sum(counts.values()) or 1
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                df = self._df.get(term, 0)
                idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / self._avg_len))
            if score > 0:
                scored.append((score, position))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored

    # -- embeddings --------------------------------------------------------------------------

    def _embedder_key(self, embedder: Any) -> str:
        key = getattr(embedder, "cache_key", None)
        return str(key) if key else f"{type(embedder).__qualname__}:{id(embedder)}"

    def _disk_vector(self, key: str, digest: str) -> Path | None:
        if self.cache_dir is None:
            return None
        bucket = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self.cache_dir / "embeddings" / bucket / digest[:2] / f"{digest}.f32"

    def _vectors(self, embedder: Any, chunks: list[Chunk]) -> list[list[float]]:
        key = self._embedder_key(embedder)
        missing: list[Chunk] = []
        for chunk in chunks:
            if (key, chunk.digest) in self._embeddings:
                continue
            disk = self._disk_vector(key, chunk.digest)
            if disk is not None and disk.is_file():
                values = array("f")
                with contextlib.suppress(OSError, ValueError):
                    values.frombytes(disk.read_bytes())
                    self._embeddings[(key, chunk.digest)] = values.tolist()
                    continue
            missing.append(chunk)
        if missing:
            texts = [f"{c.path}\n{c.text[:EMBED_CHARS]}" for c in missing]
            vectors = _embed(embedder, texts)
            for chunk, vector in zip(missing, vectors, strict=True):
                self._embeddings[(key, chunk.digest)] = vector
                disk = self._disk_vector(key, chunk.digest)
                if disk is not None:
                    with contextlib.suppress(OSError):
                        disk.parent.mkdir(parents=True, exist_ok=True)
                        disk.write_bytes(array("f", vector).tobytes())
        return [self._embeddings[(key, c.digest)] for c in chunks]

    # -- query -------------------------------------------------------------------------------

    def search(
        self, query: str, *, embedder: Any | None = None, k: int = 8, path: str | None = None
    ) -> SearchResult:
        """The best `k` chunks for `query`, from the chunks of `path` (a root-relative file or
        directory) when it is given."""
        self.refresh()
        everything = self._all()
        scope = PurePath(path).as_posix().strip("/") if path else ""
        allowed: Sequence[int] = range(len(everything))
        if scope not in ("", "."):
            allowed = [
                position
                for position, (chunk, _) in enumerate(everything)
                if chunk.path == scope or chunk.path.startswith(scope + "/")
            ]
        inside = set(allowed)
        keyword = [item for item in self.keyword_scores(query) if item[1] in inside]
        mode = "keyword"
        note = None
        fused: dict[int, float] = {}
        for rank, (_, position) in enumerate(keyword[:200]):
            fused[position] = fused.get(position, 0.0) + 1.0 / (RRF_K + rank + 1)
        if embedder is not None and allowed:
            if len(allowed) <= EMBED_LIMIT:
                candidates = list(allowed)
            else:
                candidates = [position for _, position in keyword[:200]]
            try:
                query_vector = _embed_query(embedder, query)
                vectors = self._vectors(embedder, [everything[p][0] for p in candidates])
                similarities = _cosine_all(query_vector, vectors)
                order = sorted(range(len(candidates)), key=lambda i: -similarities[i])
                for rank, index in enumerate(order[:200]):
                    position = candidates[index]
                    fused[position] = fused.get(position, 0.0) + 1.0 / (RRF_K + rank + 1)
                mode = "hybrid"
            except Exception as exc:  # noqa: BLE001 - search degrades to keywords, never fails
                note = f"embeddings unavailable ({type(exc).__name__}: {exc}); keyword-only"
        best = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[: max(1, k)]
        hits = [
            Hit(
                everything[p][0].path,
                everything[p][0].start,
                everything[p][0].end,
                everything[p][0].symbol,
                round(score, 6),
                _snippet(everything[p][0].text),
                mode,
            )
            for p, score in best
        ]
        if mode == "keyword" and note is None:
            note = "keyword-only (no embedder)"
        return SearchResult(hits, mode, note, len(everything))


def _snippet(text: str, lines: int = 6, chars: int = 400) -> str:
    rows = [row.rstrip() for row in text.split("\n") if row.strip()][:lines]
    snippet = "\n".join(rows)
    return snippet if len(snippet) <= chars else snippet[: chars - 3] + "..."


def _embed(embedder: Any, texts: list[str]) -> list[list[float]]:
    method = getattr(embedder, "embed", None)
    result = method(texts) if callable(method) else embedder(texts)
    vectors = [list(map(float, vector)) for vector in result]
    if len(vectors) != len(texts):
        raise ValueError(f"embedder returned {len(vectors)} vectors for {len(texts)} texts")
    return vectors


def _embed_query(embedder: Any, query: str) -> list[float]:
    method = getattr(embedder, "embed_query", None)
    if callable(method):
        return list(map(float, method(query)))
    return _embed(embedder, [query])[0]


def _cosine_all(query: list[float], vectors: list[list[float]]) -> list[float]:
    import numpy as np

    if not vectors:
        return []
    matrix = np.asarray(vectors, dtype=np.float32)
    q = np.asarray(query, dtype=np.float32)
    if matrix.shape[1] != q.shape[0]:
        raise ValueError(f"embedding sizes differ: query {q.shape[0]}, chunks {matrix.shape[1]}")
    norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(q) or 1.0)
    norms[norms == 0] = 1.0
    return [float(v) for v in (matrix @ q) / norms]


_INDEXES: dict[Path, SearchIndex] = {}


def semantic(root: Path, query: str, *, embedder: Any | None = None, k: int = 8) -> list[Hit]:
    """The `k` chunks under `root` most related to `query` (see the module docstring)."""
    resolved = root.resolve()
    index = _INDEXES.get(resolved)
    if index is None:
        if len(_INDEXES) >= 8:
            _INDEXES.pop(next(iter(_INDEXES)))
        index = _INDEXES[resolved] = SearchIndex(resolved)
    return index.search(query, embedder=embedder, k=k).hits
