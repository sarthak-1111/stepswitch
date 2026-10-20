"""grep (ripgrep or the Python fallback) and keyword/hybrid semantic search."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import pytest

from stepswitch.context import search as S

FIXTURES = Path(__file__).parent / "fixtures" / "context"


def write(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return root


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return write(
        tmp_path / "repo",
        {
            "src/app.py": "import os\n\n\ndef main():\n    return parse_config('x')\n",
            "src/config.py": "def parse_config(path):\n    # TODO: Main settings\n    return {}\n",
            "src/web/page.ts": "export function main(): void {}\n",
            "docs/guide.md": "Run main to start.\n",
            "notes.txt": "crlf main line\r\nsecond\r\n",
            "generated/out.py": "def main():\n    pass\n",
            "node_modules/lib/index.js": "function main() {}\n",
            ".hidden/secret.py": "def main():\n    pass\n",
            ".gitignore": "generated/\n*.log\n",
            "debug.log": "main\n",
            "image.png": b"\x89PNG\x00main\x00",
            "big.txt": "main\n" + "x" * 1_100_000,
        },
    )


# -- ripgrep output and arguments ----------------------------------------------------------


def test_ripgrep_json_is_parsed() -> None:
    lines = (FIXTURES / "ripgrep.jsonl").read_text(encoding="utf-8").splitlines()
    matches = S.parse_ripgrep_json(lines)
    assert matches == [
        S.Match("src/app.py", 3, 5, "def main():"),  # "./" dropped, CRLF dropped
        S.Match("src/app.py", 9, 10, "x = '\u03c0'; main()"),  # byte offset -> character column
        S.Match("legacy/latin1.txt", 1, 6, "caf\ufffd main()"),  # {"bytes": base64} lines
    ]
    assert S.parse_ripgrep_json(["not json", '{"type": "summary"}']) == []


def test_ripgrep_runs_with_fixed_flags() -> None:
    args = S._ripgrep_args("rg", "-pattern", "src", "*.py", True, True)
    assert args[:3] == ["rg", "--json", "--no-config"]
    for flag in ("--no-require-git", "--no-ignore-parent", "--no-ignore-global", "--no-messages"):
        assert flag in args
    assert args[args.index("--max-filesize") + 1] == "1000000"
    assert "--ignore-case" in args and "--fixed-strings" in args
    globs = [args[i + 1] for i, arg in enumerate(args) if arg == "--glob"]
    assert globs[0] == "*.py" and "!.*" in globs and "!node_modules/" in globs
    # The pattern is passed as a value (it may start with "-"), then the path after "--".
    assert args[-4:] == ["--regexp", "-pattern", "--", "src"]


# -- the Python engine ---------------------------------------------------------------------


def rows(report: S.GrepReport) -> list[tuple[str, int]]:
    return [(m.path, m.line) for m in report.matches]


def test_python_engine_sees_the_same_files_as_ripgrep(tree: Path) -> None:
    report = S.run_grep(tree, r"\bmain\b", use_ripgrep=False)
    assert report.engine == "python" and not report.truncated
    assert rows(report) == [
        ("docs/guide.md", 1),
        ("notes.txt", 1),
        ("src/app.py", 4),
        ("src/web/page.ts", 1),
    ]  # not ignored, hidden, vendored, binary or over 1 MB
    crlf = next(m for m in report.matches if m.path == "notes.txt")
    assert crlf.text == "crlf main line" and crlf.column == 6


def test_globs_paths_and_case(tree: Path) -> None:
    def found(pattern: str, **options: object) -> list[tuple[str, int]]:
        return rows(S.run_grep(tree, pattern, use_ripgrep=False, **options))  # type: ignore[arg-type]

    assert found("main", glob="*.py") == [("src/app.py", 4)]  # no "/": any depth
    assert found("main", glob="src/*.ts") == []  # anchored: src/web/page.ts is deeper
    assert found("main", glob="src/**/*.ts") == [("src/web/page.ts", 1)]
    assert found("main", glob="!*.py") == [
        ("docs/guide.md", 1),
        ("notes.txt", 1),
        ("src/web/page.ts", 1),
    ]
    assert found("main", glob="*.log") == [("debug.log", 1)]  # an include glob beats .gitignore
    assert found("main", path="src/web") == [("src/web/page.ts", 1)]
    assert found("MAIN", path="src", ignore_case=True) == [
        ("src/app.py", 4),
        ("src/config.py", 2),
        ("src/web/page.ts", 1),
    ]
    with pytest.raises(S.SearchError, match="outside the repository"):
        S.run_grep(tree, "x", path="../elsewhere", use_ripgrep=False)
    with pytest.raises(S.SearchError, match="no such file"):
        S.run_grep(tree, "x", path="missing", use_ripgrep=False)


def test_invalid_regex_is_searched_as_text_and_results_are_capped(tree: Path) -> None:
    literal = S.run_grep(tree, "parse_config(", use_ripgrep=False)
    assert literal.literal and rows(literal) == [("src/app.py", 5), ("src/config.py", 1)]
    capped = S.run_grep(tree, "main", max_results=2, use_ripgrep=False)
    assert capped.truncated and len(capped.matches) == 2
    assert (
        S.grep(tree, "parse_config", glob="config.py")
        == S.run_grep(tree, "parse_config", glob="config.py").matches
    )


def test_long_lines_are_clipped_around_the_match(tmp_path: Path) -> None:
    root = write(tmp_path, {"long.txt": "a" * 1000 + "needle" + "b" * 1000 + "\n"})
    match = S.run_grep(root, "needle", use_ripgrep=False).matches[0]
    assert match.column == 1001 and "needle" in match.text
    assert match.text.startswith("...") and match.text.endswith("...")
    assert len(match.text) <= S.MAX_LINE_CHARS + 6


@pytest.mark.skipif(S.ripgrep_path() is None, reason="ripgrep is not installed")
def test_ripgrep_and_python_agree(tree: Path) -> None:
    cases: list[dict[str, object]] = [
        {"pattern": r"\bmain\b"},
        {"pattern": "main", "glob": "*.py"},
        {"pattern": "main", "glob": "src/**/*.ts"},
        {"pattern": "main", "glob": "!*.py"},
        {"pattern": "main", "glob": "*.log"},
        {"pattern": "MAIN", "ignore_case": True, "path": "src"},
        {"pattern": "parse_config("},
        {"pattern": r"(?<=def )main"},  # look-behind: ripgrep refuses, Python runs it
    ]
    for case in cases:
        fast = S.run_grep(tree, use_ripgrep=None, **case)  # type: ignore[arg-type]
        slow = S.run_grep(tree, use_ripgrep=False, **case)  # type: ignore[arg-type]
        assert fast.matches == slow.matches, case
        assert fast.literal == slow.literal


# -- semantic search -----------------------------------------------------------------------


def test_tokenize_splits_identifiers() -> None:
    assert S.tokenize("parseHttpHeader") == ["parsehttpheader", "parse", "http", "header"]
    assert S.tokenize("load_user_profiles") == ["load_user_profiles", "load", "user", "profile"]
    assert S.tokenize("the class of a value") == ["value"]


def test_chunks_follow_definitions() -> None:
    text = "\n".join(
        ["import os", "", "def small():", "    return 1", ""]
        + ["def large():"]
        + [f"    x{i} = {i}" for i in range(150)]
        + ["", "TAIL = 1"]
    )
    definitions = [("small", 3, 4), ("large", 6, 156)]
    chunks = S.chunk_file("m.py", text, definitions)
    assert [(c.start, c.end, c.symbol) for c in chunks[:2]] == [(1, 2, None), (3, 4, "small")]
    large = [c for c in chunks if c.symbol == "large"]
    assert len(large) > 1 and large[0].start == 6 and large[-1].end == 156  # split in windows
    assert chunks[-1].symbol is None and chunks[-1].text.endswith("TAIL = 1")


SOURCE = {
    "src/config.py": "def parse_config(path):\n    '''Read the settings file.'''\n"
    "    return load_settings(path)\n",
    "src/network.py": "def open_socket(host, port):\n    '''Connect to a server.'''\n"
    "    return connect(host, port)\n",
    "src/report.py": "def render_report(rows):\n    '''Format rows as a table.'''\n"
    "    return '\\n'.join(rows)\n",
}


def test_keyword_search(tmp_path: Path) -> None:
    index = S.SearchIndex(write(tmp_path, SOURCE))
    result = index.search("where are the settings parsed", k=2)
    assert result.mode == "keyword" and result.note == "keyword-only (no embedder)"
    assert result.hits[0].path == "src/config.py" and result.hits[0].symbol == "parse_config"
    assert result.hits[0].mode == "keyword" and len(result.hits) <= 2
    assert result.chunks == 3


class BagOfWords:
    """A deterministic embedder: hashed term counts in 64 dimensions."""

    cache_key = "test|bag-of-words"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            vector = [0.0] * 64
            for term in S.tokenize(text):
                vector[int(hashlib.sha256(term.encode()).hexdigest(), 16) % 64] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors


def test_hybrid_search_caches_embeddings(tmp_path: Path) -> None:
    root = write(tmp_path / "repo", SOURCE)
    cache = tmp_path / "cache"
    embedder = BagOfWords()
    index = S.SearchIndex(root, cache_dir=cache)
    first = index.search("connect to a server socket", embedder=embedder, k=1)
    assert first.mode == "hybrid" and first.note is None
    assert first.hits[0].path == "src/network.py" and first.hits[0].mode == "hybrid"
    assert sum(len(batch) for batch in embedder.calls) == 4  # the query and three chunks
    index.search("format a table", embedder=embedder)
    assert sum(len(batch) for batch in embedder.calls) == 5  # chunks come from memory
    assert len(list(cache.rglob("*.f32"))) == 3

    fresh = BagOfWords()
    again = S.SearchIndex(root, cache_dir=cache).search("server socket", embedder=fresh, k=1)
    assert again.hits[0].path == "src/network.py"
    assert fresh.calls == [["server socket"]]  # chunk vectors came from the disk cache


def test_a_failing_embedder_falls_back_to_keywords(tmp_path: Path) -> None:
    class Broken:
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("endpoint down")

    index = S.SearchIndex(write(tmp_path, SOURCE))
    result = index.search("settings", embedder=Broken())
    assert result.mode == "keyword"
    assert result.note == "embeddings unavailable (RuntimeError: endpoint down); keyword-only"
    assert result.hits[0].path == "src/config.py"


def test_search_within_a_path_and_without_translation_catalogs(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            **SOURCE,
            "tests/test_config.py": (
                "def test_parse_config():\n    assert parse_config('settings')\n"
            ),
            # Translation catalogs are bulk text, not code: grep still sees them.
            "locale/fr/LC_MESSAGES/app.po": 'msgid "parse the settings"\nmsgstr "lire"\n',
        },
    )
    index = S.SearchIndex(root)
    everywhere = index.search("parse config settings", k=5)
    assert {hit.path for hit in everywhere.hits} == {"src/config.py", "tests/test_config.py"}
    within = index.search("parse config settings", k=5, path="tests")
    assert [hit.path for hit in within.hits] == ["tests/test_config.py"]
    assert index.search("parse config settings", path="src/config.py").hits[0].path == (
        "src/config.py"
    )
    assert index.search("parse config settings", path="docs").hits == []
    assert [m.path for m in S.grep(root, "parse the settings")] == ["locale/fr/LC_MESSAGES/app.po"]


def test_indexing_walks_the_tree_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Indexing asked the repo map for each file's definitions, and each question walked the
    # whole tree again: 11 minutes on a repository of Django's size.
    files = {f"pkg/mod{i}.py": f"def handler_{i}(event):\n    return event\n" for i in range(30)}
    root = write(tmp_path, files)
    walks = []
    original = S.RepoMap.refresh

    def counted(self: S.RepoMap) -> None:
        walks.append(1)
        original(self)

    monkeypatch.setattr(S.RepoMap, "refresh", counted)
    index = S.SearchIndex(root)
    result = index.search("handler_7 event", k=1)
    assert result.hits[0].path == "pkg/mod7.py" and result.hits[0].symbol == "handler_7"
    assert len(walks) == 1


def test_the_index_follows_file_changes(tmp_path: Path) -> None:
    root = write(tmp_path, SOURCE)
    hits = S.semantic(root, "render report table")
    assert hits[0].path == "src/report.py"
    (root / "src" / "report.py").unlink()
    write(root, {"src/charts.py": "def draw_chart(points):\n    return points\n"})
    paths = [hit.path for hit in S.semantic(root, "render report table draw chart")]
    assert "src/report.py" not in paths and paths[0] == "src/charts.py"
    assert re.fullmatch(r"[0-9a-f]{64}", S.Chunk("a", 1, 1, None, "x").digest)
