"""tree-sitter definitions and references for the repository map (TK2)."""

from __future__ import annotations

import pytest

from stepswitch.context import repomap, treesitter

CORE = {"javascript", "typescript", "tsx", "go", "rust", "java", "c", "cpp", "ruby"}


def test_core_grammars_load_offline() -> None:
    assert set(treesitter.available_languages()) >= CORE


def test_names_in_strings_and_comments_are_not_references() -> None:
    text = (
        "function formatPrice(value) { return value; }\n"
        'const label = "formatPrice";  // formatPrice in a comment\n'
        "formatPrice(1);\n"
    )
    tags = treesitter.file_tags(text, "javascript")
    assert tags is not None
    assert [t.name for t in tags.tags] == ["formatPrice"]
    # The definition and the one call; the string and the comment do not count.
    assert tags.identifiers["formatPrice"] == 2


def test_jsx_in_a_typescript_file_parses_with_the_tsx_grammar() -> None:
    text = "export const Badge = (props: { n: number }) => <span>{props.n}</span>;\n"
    tags = treesitter.file_tags(text, "typescript")
    assert tags is not None
    assert [(t.name, t.kind) for t in tags.tags] == [("Badge", "function")]


def test_nested_definitions_take_the_innermost_parent() -> None:
    text = "class Outer {\n  static class Inner {\n    void run() {}\n  }\n}\n"
    tags = treesitter.file_tags(text, "java")
    assert tags is not None
    assert [(t.name, t.parent, t.depth) for t in tags.tags] == [
        ("Outer", None, 0),
        ("Inner", "Outer", 1),
        ("run", "Inner", 2),
    ]


def test_unknown_languages_have_no_grammar() -> None:
    assert treesitter.file_tags("x = 1", "cobol") is None


def test_the_regex_extractor_remains_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "func Area() float64 {\n\treturn 1\n}\n"
    with_grammar = repomap.extract_tags(text, "go")
    monkeypatch.setattr(treesitter, "file_tags", lambda _text, _language: None)
    without = repomap.extract_tags(text, "go")
    assert [(t.name, t.kind, t.line) for t in with_grammar.tags] == [("Area", "function", 1)]
    assert [(t.name, t.kind, t.line) for t in without.tags] == [("Area", "function", 1)]
