"""Definitions and references from tree-sitter grammars (SPEC §13 TK2, ADR 0024).

The grammars come from pinned per-language wheels, which work offline (docs/RESEARCH.md,
"tree-sitter grammar wheels"). The queries are Stepswitch's own (`QUERIES`): the tags queries
that ship with the grammars leave out definitions the map needs (C macros, Java constructors,
TypeScript type aliases and enums, Go and Rust method receivers) and mark some definitions by
their declarator only, so a function's range would end at its signature. Each pattern captures
a definition's node as `@def.<kind>`, its name as `@name` and, where the parent is not the
enclosing definition (a Go receiver, a Rust impl, a C++ `Scope::name`), the parent as `@parent`.
Other parents are the innermost enclosing definition.

Python keeps the standard library's `ast` (`repomap._python_file_tags`), which is exact for
Python. A language whose grammar does not load keeps the regex extractor. References for
ranking are the identifier leaves of the syntax tree, so names in strings and comments do not
count.
"""

from __future__ import annotations

import functools
import importlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

# language -> (module, language function)
GRAMMARS: dict[str, tuple[str, str]] = {
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "java": ("tree_sitter_java", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "ruby": ("tree_sitter_ruby", "language"),
}

_JS_FUNCTIONS = """
(function_declaration name: (identifier) @name) @def.function
(generator_function_declaration name: (identifier) @name) @def.function
(lexical_declaration
  (variable_declarator name: (identifier) @name
    value: [(arrow_function) (function_expression)])) @def.function
(variable_declaration
  (variable_declarator name: (identifier) @name
    value: [(arrow_function) (function_expression)])) @def.function
(method_definition name: [(property_identifier) (private_property_identifier)] @name) @def.method
"""

_TS = (
    _JS_FUNCTIONS
    + """
(class_declaration name: (type_identifier) @name) @def.class
(abstract_class_declaration name: (type_identifier) @name) @def.class
(interface_declaration name: (type_identifier) @name) @def.interface
(type_alias_declaration name: (type_identifier) @name) @def.type
(enum_declaration name: (identifier) @name) @def.enum
(method_signature name: (property_identifier) @name) @def.method
(abstract_method_signature name: (property_identifier) @name) @def.method
(function_signature name: (identifier) @name) @def.function
(internal_module name: (identifier) @name) @def.module
"""
)

_C_FUNCTIONS = """
(function_definition
  declarator: (function_declarator declarator: (identifier) @name)) @def.function
(function_definition
  declarator: (pointer_declarator
    declarator: (function_declarator declarator: (identifier) @name))) @def.function
(declaration declarator: (function_declarator declarator: (identifier) @name)) @def.function
(declaration
  declarator: (pointer_declarator
    declarator: (function_declarator declarator: (identifier) @name))) @def.function
(struct_specifier name: (type_identifier) @name body: (field_declaration_list)) @def.type
(union_specifier name: (type_identifier) @name body: (field_declaration_list)) @def.type
(enum_specifier name: (type_identifier) @name body: (enumerator_list)) @def.type
(type_definition declarator: (type_identifier) @name) @def.type
(preproc_def name: (identifier) @name) @def.macro
(preproc_function_def name: (identifier) @name) @def.macro
"""

QUERIES: dict[str, str] = {
    "javascript": _JS_FUNCTIONS
    + """
(class_declaration name: (identifier) @name) @def.class
""",
    "typescript": _TS,
    "tsx": _TS,
    "go": """
(function_declaration name: (identifier) @name) @def.function
(method_declaration
  receiver: (parameter_list
    (parameter_declaration
      type: [(type_identifier) @parent
             (pointer_type (type_identifier) @parent)
             (generic_type type: (type_identifier) @parent)
             (pointer_type (generic_type type: (type_identifier) @parent))]))
  name: (field_identifier) @name) @def.method
(type_spec name: (type_identifier) @name) @def.type
(type_alias name: (type_identifier) @name) @def.type
(const_spec name: (identifier) @name) @def.constant
""",
    "rust": """
(struct_item name: (type_identifier) @name) @def.type
(enum_item name: (type_identifier) @name) @def.type
(union_item name: (type_identifier) @name) @def.type
(type_item name: (type_identifier) @name) @def.type
(trait_item name: (type_identifier) @name) @def.trait
(function_item name: (identifier) @name) @def.function
(impl_item
  type: [(type_identifier) @parent
         (generic_type type: (type_identifier) @parent)
         (scoped_type_identifier name: (type_identifier) @parent)]
  body: (declaration_list (function_item name: (identifier) @name) @def.method))
(trait_item
  name: (type_identifier) @parent
  body: (declaration_list
    [(function_signature_item name: (identifier) @name)
     (function_item name: (identifier) @name)] @def.method))
(const_item name: (identifier) @name) @def.constant
(static_item name: (identifier) @name) @def.constant
(macro_definition name: (identifier) @name) @def.macro
(mod_item name: (identifier) @name) @def.module
""",
    "java": """
(class_declaration name: (identifier) @name) @def.class
(record_declaration name: (identifier) @name) @def.class
(interface_declaration name: (identifier) @name) @def.interface
(annotation_type_declaration name: (identifier) @name) @def.interface
(enum_declaration name: (identifier) @name) @def.enum
(method_declaration name: (identifier) @name) @def.method
(constructor_declaration name: (identifier) @name) @def.constructor
""",
    "c": _C_FUNCTIONS,
    "cpp": _C_FUNCTIONS
    + """
(class_specifier name: (type_identifier) @name body: (field_declaration_list)) @def.type
(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier
      scope: [(namespace_identifier) (template_type)] @parent
      name: (identifier) @name))) @def.method
(function_definition
  declarator: (function_declarator declarator: (field_identifier) @name)) @def.method
(field_declaration
  declarator: (function_declarator declarator: (field_identifier) @name)) @def.method
(namespace_definition name: (namespace_identifier) @name) @def.module
(alias_declaration name: (type_identifier) @name) @def.type
""",
    "ruby": """
(module name: [(constant) (scope_resolution)] @name) @def.module
(class name: [(constant) (scope_resolution)] @name) @def.class
(method name: (_) @name) @def.method
(singleton_method name: (_) @name) @def.method
""",
}
SIGNATURE_CHARS = 150
# Leaf node types that hold a name.
IDENTIFIER_TYPES = frozenset(
    {
        "identifier",
        "type_identifier",
        "field_identifier",
        "property_identifier",
        "private_property_identifier",
        "shorthand_property_identifier",
        "package_identifier",
        "namespace_identifier",
        "constant",
    }
)


@dataclass(frozen=True)
class _Grammar:
    language: Any
    query: Any


@functools.cache
def _grammar(name: str) -> _Grammar | None:
    """The grammar and compiled query for `name`, or None when it cannot be loaded."""
    spec = GRAMMARS.get(name)
    if spec is None:
        return None
    try:
        from tree_sitter import Language, Query

        module = importlib.import_module(spec[0])
        language = Language(getattr(module, spec[1])())
        return _Grammar(language, Query(language, QUERIES[name]))
    except Exception:  # noqa: BLE001 - a missing or incompatible grammar means "use the regexes"
        return None


def available_languages() -> list[str]:
    """Languages whose grammar loads here (offline: grammars come from installed wheels)."""
    return sorted(name for name in GRAMMARS if _grammar(name) is not None)


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SIGNATURE_CHARS else text[: SIGNATURE_CHARS - 3] + "..."


def _parse(grammar: _Grammar, data: bytes) -> Any:
    from tree_sitter import Parser

    return Parser(grammar.language).parse(data)


def _identifiers(root: Any) -> Counter[str]:
    counts: Counter[str] = Counter()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in IDENTIFIER_TYPES and node.child_count == 0 and node.text:
            counts[node.text.decode("utf-8", errors="replace")] += 1
        stack.extend(node.children)
    return counts


def _end_line(node: Any) -> int:
    """1-based last line; a node that ends at a line start (a trailing newline) ends before."""
    row, column = int(node.end_point[0]), int(node.end_point[1])
    start = int(node.start_point[0])
    return max(start, row - 1 if column == 0 and row > start else row) + 1


def _text(node: Any) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text else ""


def file_tags(text: str, language: str) -> Any | None:
    """`repomap.FileTags` for `text`, or None when no grammar is available for `language`."""
    from tree_sitter import QueryCursor

    from stepswitch.context.repomap import FileTags, Tag

    grammar = _grammar(language)
    if grammar is None:
        return None
    data = text.encode("utf-8", errors="replace")
    tree = _parse(grammar, data)
    if language == "typescript" and tree.root_node.has_error and "<" in text:
        tsx = _grammar("tsx")  # JSX in a file mapped to TypeScript: the TSX grammar parses it
        if tsx is not None:
            grammar, tree = tsx, _parse(tsx, data)
    # (start byte, name) -> (kind, definition node, name node, explicit parent or None)
    found: dict[tuple[int, str], tuple[str, Any, Any, str | None]] = {}
    for _pattern, captures in QueryCursor(grammar.query).matches(tree.root_node):
        kinds = sorted(c for c in captures if c.startswith("def."))
        names = captures.get("name") or []
        if not kinds or not names:
            continue
        node = captures[kinds[0]][0]
        parents = captures.get("parent") or []
        parent = _text(parents[0]) if parents else None
        parent = parent or None
        name = _text(names[0])
        key = (node.start_byte, name)
        if not name:
            continue
        # A pattern that names the parent (an impl method) wins over a plain one (a function).
        if key not in found or (parent is not None and found[key][3] is None):
            found[key] = (kinds[0].split(".", 1)[1], node, names[0], parent)
    spans = [(node.start_byte, node.end_byte, name) for (_, name), (_, node, _, _) in found.items()]
    lines = text.splitlines()
    tags = []
    for (_, name), (kind, node, name_node, parent) in sorted(
        found.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        outer = sorted(
            (s for s in spans if s[0] <= node.start_byte and node.end_byte <= s[1]),
            key=lambda s: (s[0], -s[1]),
        )
        outer = [s for s in outer if (s[0], s[1]) != (node.start_byte, node.end_byte)]
        line = node.start_point[0] + 1
        tags.append(
            Tag(
                name=name,
                kind=kind,
                line=line,
                end_line=_end_line(node),
                column=name_node.start_point[1],
                signature=_short(lines[line - 1]) if 0 < line <= len(lines) else name,
                parent=parent if parent is not None else (outer[-1][2] if outer else None),
                depth=len(outer) + (1 if parent is not None and not outer else 0),
            )
        )
    return FileTags(language, tags, dict(_identifiers(tree.root_node)))
