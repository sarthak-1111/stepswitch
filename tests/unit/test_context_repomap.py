"""Repository map: definitions, ranking, the token budget and caching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stepswitch.context import repomap as R

PYTHON = '''\
"""Module doc."""

from __future__ import annotations

import os

MAX_RETRIES = 3
_private = 1
lower_case = 2

if os.name == "nt":
    def platform_name() -> str:
        return "windows"
else:
    def platform_name() -> str:
        return "posix"


class Engine(Base, metaclass=Meta):
    """Doc."""

    speed: int = 0

    def start(self, gear: int = 1) -> bool:
        def inner():
            return 1
        return True

    async def stop(self) -> None:
        pass


@decorator
def helper(*args, **kwargs):
    return args
'''


def test_python_definitions_come_from_the_ast() -> None:
    tags = R.extract_tags(PYTHON, "python")
    rows = [(t.name, t.kind, t.line, t.end_line, t.parent, t.depth, t.signature) for t in tags.tags]
    assert rows == [
        ("MAX_RETRIES", "variable", 7, 7, None, 0, "MAX_RETRIES = 3"),
        ("platform_name", "function", 12, 13, None, 0, "def platform_name() -> str"),
        ("platform_name", "function", 15, 16, None, 0, "def platform_name() -> str"),
        ("Engine", "class", 19, 30, None, 0, "class Engine(Base, metaclass=Meta)"),
        ("start", "method", 24, 27, "Engine", 1, "def start(self, gear: int=1) -> bool"),
        ("stop", "method", 29, 30, "Engine", 1, "async def stop(self) -> None"),
        ("helper", "function", 34, 35, None, 0, "def helper(*args, **kwargs)"),
    ]
    assert tags.tags[3].column == 7  # the name, not the keyword
    assert "os" in tags.imports and "__future__.annotations" in tags.imports
    assert tags.identifiers["Base"] == 1 and "inner" not in tags.identifiers


def test_python_that_does_not_parse_still_gets_definitions() -> None:
    broken = "def ok():\n    pass\n\ndef broken(:\n    pass\n\nclass Thing:\n    pass\n"
    tags = R.extract_tags(broken, "python")
    names = [(t.name, t.kind, t.line) for t in tags.tags]
    assert names == [("ok", "function", 1), ("broken", "function", 4), ("Thing", "class", 7)]
    assert tags.attributes is None  # no syntax tree: a name may mean any definition


def test_python_references_split_names_and_members() -> None:
    text = (
        "import json\n"
        "from app.core import compute_total\n"
        "from app import proc\n"
        "\n"
        "def run(env):\n"
        "    local = compute_total(env)\n"
        "    proc.which('git')\n"
        "    client.stream()\n"
        "    return json.dumps(local), helper_value\n"
    )
    tags = R.extract_tags(text, "python")
    # Local variables, arguments and modules bound by `import` are not references.
    assert tags.identifiers == {
        "compute_total": 2,
        "proc": 2,
        "which": 1,
        "dumps": 1,
        "client": 1,
        "helper_value": 1,
    }
    assert tags.attributes == {"which": 1, "stream": 1, "dumps": 1}  # can only mean members
    assert tags.imports == ["app", "app.core", "app.core.compute_total", "app.proc", "json"]


SAMPLES: dict[str, tuple[str, list[tuple[str, str, int, int, str | None]]]] = {
    "javascript": (
        "import { thing } from './thing.js';\n"
        "\n"
        "export class Cart {\n"
        "  #count = 0;\n"
        "\n"
        "  total() {\n"
        "    if (this.#count > 0) {\n"
        "      return `${this.#count}`;\n"
        "    }\n"
        "  }\n"
        "}\n"
        "\n"
        "export function formatPrice(value) {\n"
        "  return value.toFixed(2);\n"
        "}\n"
        "\n"
        "const applyDiscount = (cart, rate) =>\n"
        "  cart.total() * (1 - rate);\n",
        [
            ("Cart", "class", 3, 11, None),
            ("total", "method", 6, 10, "Cart"),
            ("formatPrice", "function", 13, 15, None),
            ("applyDiscount", "function", 17, 18, None),
        ],
    ),
    "typescript": (
        "export interface Shape {\n"
        "  area(): number;\n"
        "}\n"
        "\n"
        "export type Point = { x: number; y: number };\n"
        "\n"
        "export enum Color {\n"
        "  Red,\n"
        "}\n"
        "\n"
        "export abstract class Polygon implements Shape {\n"
        "  public area(): number {\n"
        "    return 0;\n"
        "  }\n"
        "}\n"
        "\n"
        "export async function loadShapes(url: string): Promise<Shape[]> {\n"
        "  return [];\n"
        "}\n",
        [
            ("Shape", "interface", 1, 3, None),
            ("area", "method", 2, 2, "Shape"),
            ("Point", "type", 5, 5, None),
            ("Color", "enum", 7, 9, None),
            ("Polygon", "class", 11, 15, None),
            ("area", "method", 12, 14, "Polygon"),
            ("loadShapes", "function", 17, 19, None),
        ],
    ),
    "go": (
        "package geometry\n"
        "\n"
        "type Celsius float64\n"
        "\n"
        "type Rect struct {\n"
        "\tW, H float64\n"
        "}\n"
        "\n"
        "func (r *Rect) Area() float64 {\n"
        "\treturn r.W * r.H\n"
        "}\n"
        "\n"
        "func NewRect(\n"
        "\tw, h float64,\n"
        ") *Rect {\n"
        "\treturn &Rect{W: w, H: h}\n"
        "}\n",
        [
            ("Celsius", "type", 3, 3, None),
            ("Rect", "type", 5, 7, None),
            ("Area", "method", 9, 11, "Rect"),
            ("NewRect", "function", 13, 17, None),
        ],
    ),
    "rust": (
        "pub struct Stack<T> {\n"
        "    items: Vec<T>,\n"
        "}\n"
        "\n"
        "impl<T> Stack<T> {\n"
        "    pub fn push(&mut self, item: T) {\n"
        "        self.items.push(item);\n"
        "    }\n"
        "}\n"
        "\n"
        "pub trait Shape {\n"
        "    fn area(&self) -> f64;\n"
        "}\n"
        "\n"
        "pub const MAX_DEPTH: usize = 64;\n"
        "\n"
        "macro_rules! square {\n"
        "    ($x:expr) => { $x * $x };\n"
        "}\n",
        [
            ("Stack", "type", 1, 3, None),
            ("push", "method", 6, 8, "Stack"),
            ("Shape", "trait", 11, 13, None),
            ("area", "method", 12, 12, "Shape"),
            ("MAX_DEPTH", "constant", 15, 15, None),
            ("square", "macro", 17, 19, None),
        ],
    ),
    "java": (
        "package demo;\n"
        "\n"
        "public class Account {\n"
        "    private long balance;\n"
        "\n"
        "    public Account(long opening) {\n"
        "        this.balance = opening;\n"
        "    }\n"
        "\n"
        "    public long deposit(long amount)\n"
        "            throws IllegalStateException {\n"
        "        balance += amount;\n"
        "        return balance;\n"
        "    }\n"
        "}\n",
        [
            ("Account", "class", 3, 15, None),
            ("Account", "constructor", 6, 8, "Account"),
            ("deposit", "method", 10, 14, "Account"),
        ],
    ),
    "c": (
        "#include <stdio.h>\n"
        "#define BUFFER_SIZE 64\n"
        "#define MAX(a, b) \\\n"
        "    ((a) > (b) ? (a) : (b))\n"
        "\n"
        "struct point {\n"
        "    int x;\n"
        "};\n"
        "\n"
        "typedef struct {\n"
        "    int w;\n"
        "} size_t2;\n"
        "\n"
        "static int add_points(struct point *a, struct point *b) {\n"
        "    return a->x + b->x;\n"
        "}\n"
        "\n"
        "int scale(int value, int factor);\n",
        [
            ("BUFFER_SIZE", "macro", 2, 2, None),
            ("MAX", "macro", 3, 4, None),
            ("point", "type", 6, 8, None),
            ("size_t2", "type", 10, 12, None),
            ("add_points", "function", 14, 16, None),
            ("scale", "function", 18, 18, None),
        ],
    ),
    "cpp": (
        "class Widget\n"
        "{\n"
        "public:\n"
        "    int size;\n"
        "};\n"
        "\n"
        "double Circle::area() const {\n"
        "    return 3.14 * r * r;\n"
        "}\n",
        [("Widget", "type", 1, 5, None), ("area", "method", 7, 9, "Circle")],
    ),
    "ruby": (
        "module Billing\n"
        "  class Invoice\n"
        "    def total\n"
        "      42\n"
        "    end\n"
        "\n"
        "    def self.build(items)\n"
        "      new(items)\n"
        "    end\n"
        "  end\n"
        "end\n",
        [
            ("Billing", "module", 1, 11, None),
            ("Invoice", "class", 2, 10, "Billing"),
            ("total", "method", 3, 5, "Invoice"),
            ("build", "method", 7, 9, "Invoice"),
        ],
    ),
}


@pytest.mark.parametrize("language", sorted(SAMPLES))
def test_definitions_in_other_languages(language: str) -> None:
    text, expected = SAMPLES[language]
    tags = R.extract_tags(text, language).tags
    assert [(t.name, t.kind, t.line, t.end_line, t.parent) for t in tags] == expected


def test_languages_by_suffix() -> None:
    assert R.language_for("a/b.tsx") == "typescript"
    assert R.language_for("x.HPP") == "cpp"
    assert R.language_for("notes.md") is None


# -- a small project -----------------------------------------------------------------------


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


PROJECT = {
    "app/core.py": "def compute_total(items):\n    return sum(items)\n\n\n"
    "def rarely_used_helper():\n    return 0\n",
    "app/orders.py": "from app.core import compute_total\n\n\n"
    "def order_total(order):\n    return compute_total(order.items)\n",
    "app/invoices.py": "from app.core import compute_total\n\n\n"
    "def invoice_total(invoice):\n    return compute_total(invoice.lines)\n",
    "app/reports.py": "from app import core\n\n\n"
    "def report(rows):\n    return core.compute_total(rows)\n",
    "app/legacy.py": "def compute_total(values):\n    return 0\n",
    "web/page.ts": "export function renderPage(title: string): string {\n  return title;\n}\n",
}


def test_ranking_follows_references(tmp_path: Path) -> None:
    repo = R.RepoMap(write(tmp_path, PROJECT))
    ranked = repo.rank()
    top = ranked[0].definition
    assert (top.path, top.name) == ("app/core.py", "compute_total")
    # The same name in a module nobody imports ranks below the imported one.
    legacy = next(r for r in ranked if r.definition.path == "app/legacy.py")
    assert legacy.score < ranked[0].score / 5
    assert [r.score for r in ranked] == sorted((r.score for r in ranked), reverse=True)


def test_focus_moves_files_and_names_up(tmp_path: Path) -> None:
    repo = R.RepoMap(write(tmp_path, PROJECT))
    by_file = repo.rank(["web/page.ts"])
    assert by_file[0].definition.name == "renderPage"
    by_name = repo.rank(["rarely_used_helper"])
    assert by_name[0].definition.name == "rarely_used_helper"
    assert repo.rank() == repo.rank()  # cached and stable


def test_render_fits_the_budget(tmp_path: Path) -> None:
    repo = R.RepoMap(write(tmp_path, PROJECT))
    full = repo.render(max_tokens=10_000)
    assert full.shown == full.total == 7 and full.estimated
    assert full.text.startswith("app/core.py:\n  1 def compute_total(items)")
    small = repo.render(max_tokens=20)
    assert 0 < small.shown < small.total and small.tokens <= 20
    one_more = R.RepoMap.format([r.definition for r in repo.rank()[: small.shown + 1]])
    assert R.estimate_tokens(one_more) > 20  # the largest prefix that fits
    assert repo.render(max_tokens=0).text == ""


def test_an_injected_token_counter_is_used(tmp_path: Path) -> None:
    calls: list[str] = []

    def count(text: str) -> int:
        calls.append(text)
        return len(text.split())

    repo = R.RepoMap(write(tmp_path, PROJECT), token_counter=count)
    result = repo.render(max_tokens=12)
    assert calls and not result.estimated and result.tokens <= 12


def test_tags_are_cached_by_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = write(tmp_path / "repo", PROJECT)
    cache = tmp_path / "cache"
    first = R.RepoMap(root, cache_dir=cache)
    assert len(first.files()) == 6
    stored = sorted(cache.glob(f"repomap-v{R.TAGS_VERSION}/*/*.json"))
    assert len(stored) == 6
    assert json.loads(stored[0].read_text(encoding="utf-8"))["v"] == R.TAGS_VERSION

    def no_parsing(text: str, language: str) -> R.FileTags:
        raise AssertionError("tags should come from the cache")

    monkeypatch.setattr(R, "extract_tags", no_parsing)
    second = R.RepoMap(root, cache_dir=cache)  # a new process would start like this
    assert [d.name for d in second.definitions(path="app/core.py")] == [
        "compute_total",
        "rarely_used_helper",
    ]
    monkeypatch.undo()

    (root / "app" / "core.py").write_text("def renamed_total():\n    pass\n", encoding="utf-8")
    second.invalidate("app/core.py")
    assert [d.name for d in second.definitions(path="app/core.py")] == ["renamed_total"]


def test_files_that_are_skipped(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "keep.py": "def kept():\n    pass\n",
            "node_modules/lib/index.js": "function vendored() {}\n",
            ".venv/lib/site.py": "def installed():\n    pass\n",
            "build/out.py": "def built():\n    pass\n",
            "pkg/__pycache__/x.py": "def cached():\n    pass\n",
            "ignored/gen.py": "def generated():\n    pass\n",
            "private/secret.py": "def hidden():\n    pass\n",
            ".gitignore": "ignored/\n",
        },
    )
    (root / "binary.py").write_bytes(b"\x00\x01def nope(): pass\n")
    (root / "huge.py").write_text("x = 1\n" * 200_000, encoding="utf-8")  # over 1 MB
    repo = R.RepoMap(root, exclude=lambda rel: rel.startswith("private/"))
    assert repo.files() == ["keep.py"]


def test_pagerank() -> None:
    ranks = R.pagerank(["a", "b", "c"], {"a": {"b": 1.0}, "b": {"c": 1.0}, "c": {"b": 1.0}})
    assert sum(ranks.values()) == pytest.approx(1.0)
    assert ranks["b"] > ranks["a"] and ranks["c"] > ranks["a"]
    focused = R.pagerank(["a", "b"], {}, {"a": 1.0})
    assert focused["a"] == pytest.approx(1.0)
    assert R.pagerank([], {}) == {}
    weak = R.pagerank(["a", "b"], {"a": {"b": 0.1}}, min_out_weight=1.0)
    strong = R.pagerank(["a", "b"], {"a": {"b": 1.0}}, min_out_weight=1.0)
    assert weak["b"] < strong["b"] and sum(weak.values()) == pytest.approx(1.0)


def test_members_and_locals_do_not_create_false_links(tmp_path: Path) -> None:
    repo = R.RepoMap(
        write(
            tmp_path,
            {
                "lib/settings.py": "def environment_settings():\n    return {}\n",
                "lib/client.py": "class Client:\n    def stream_events(self):\n        pass\n",
                "app/local.py": "def build():\n    environment_settings = {}\n"
                "    return environment_settings\n",
                "app/calls.py": "def go(client):\n    return client.stream_events()\n",
            },
        )
    )
    scores = {(r.definition.path, r.definition.name): r.score for r in repo.rank()}
    unreferenced = scores[("lib/settings.py", "environment_settings")]
    referenced = scores[("lib/client.py", "stream_events")]
    assert referenced > 5 * unreferenced  # a method call links; a local variable does not
