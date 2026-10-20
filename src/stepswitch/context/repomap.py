"""Repository map (SPEC §13 TK2): ranked definitions within a token budget.

Definitions come from Python's `ast` for Python and from tree-sitter grammars for JavaScript,
TypeScript, Go, Rust, Java, C, C++ and Ruby (`context.treesitter`); line regexes remain the
fallback for a language whose grammar is not installed.

Ranking follows the design recorded in docs/RESEARCH.md ("Aider repo map"), reimplemented:
files are nodes; each identifier that one file uses and another defines adds an edge from the
user to the definer, weighted by sqrt(occurrences) and a few multipliers (long descriptive
names up, private and widely defined names down, focus names and focus files up).
Personalized PageRank ranks the files, and each file's rank flows along its edges to the
definitions it references; definitions nobody references get a small share of their own
file's rank (no self-edges, which would turn such files into rank sinks). The map is the
largest prefix of that ranking that fits the token budget, grouped by file.

Tags are cached per file content hash, in memory and on disk when a cache directory is given,
so a worktree with uncommitted edits never sees stale tags.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from stepswitch.context.walk import MAX_FILE_BYTES, IgnoreMatcher, iter_files, looks_binary

TokenCounter = Callable[[str], int]
DEFAULT_MAP_TOKENS = 2048
TAGS_VERSION = 4
SIGNATURE_CHARS = 150
UNREFERENCED_SHARE = 0.1  # of its file's rank, for a definition no other file uses
MIN_OUT_WEIGHT = 1.0  # a file whose references weigh less passes on only part of its rank


def words(text: str) -> frozenset[str]:
    return frozenset(text.split())


LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".rb": "ruby",
}

KEYWORDS = words(
    "if elif else for while do switch case default catch try finally return yield await new "
    "delete sizeof typeof instanceof throw throws with in is not and or super this self None "
    "True False null nil true false function def class struct enum union interface import "
    "from export package using namespace static const let var pass break continue goto "
    "lambda async public private protected"
)


def estimate_tokens(text: str) -> int:
    """The fallback token count: about four characters per token (always an estimate)."""
    return (len(text) + 3) // 4


def language_for(path: str) -> str | None:
    return LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower())


@dataclass(frozen=True)
class Tag:
    """A definition inside one file (independent of the file's path, so it can be cached)."""

    name: str
    kind: str
    line: int
    end_line: int
    column: int
    signature: str
    parent: str | None = None
    depth: int = 0


@dataclass(frozen=True)
class Definition:
    """A definition at a path: what `definitions()` and `rank()` return."""

    path: str
    name: str
    kind: str
    line: int
    end_line: int
    column: int
    signature: str
    parent: str | None = None
    depth: int = 0

    @classmethod
    def at(cls, path: str, tag: Tag) -> Definition:
        return cls(path, **asdict(tag))


@dataclass
class FileTags:
    language: str
    tags: list[Tag]
    # Names the file refers to. When `attributes` is None (no syntax tree), any definition
    # with the name may be meant; otherwise only top-level ones.
    identifiers: dict[str, int]
    # Python only: imported modules as written, relative ones keep their leading dots.
    imports: list[str] = field(default_factory=list)
    # Python only: called attributes (`client.stream()`), which can only mean members.
    attributes: dict[str, int] | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "v": TAGS_VERSION,
            "language": self.language,
            "tags": [asdict(tag) for tag in self.tags],
            "identifiers": self.identifiers,
            "imports": self.imports,
            "attributes": self.attributes,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> FileTags | None:
        if data.get("v") != TAGS_VERSION:
            return None
        raw_tags = data.get("tags")
        idents = data.get("identifiers")
        imports = data.get("imports", [])
        attributes = data.get("attributes")
        if not (isinstance(raw_tags, list) and isinstance(idents, dict)):
            return None
        tags = [Tag(**tag) for tag in raw_tags if isinstance(tag, dict)]
        return cls(
            str(data.get("language")),
            tags,
            {str(k): int(v) for k, v in idents.items()},
            [str(i) for i in imports] if isinstance(imports, list) else [],
            {str(k): int(v) for k, v in attributes.items()}
            if isinstance(attributes, dict)
            else None,
        )


@dataclass(frozen=True)
class Ranked:
    definition: Definition
    score: float


@dataclass
class RepoMapResult:
    text: str
    tokens: int
    estimated: bool  # True when the default len/4 estimate counted the tokens
    files: list[str]
    shown: int
    total: int


@dataclass
class _FileEntry:
    stat_key: tuple[int, int]
    sha: str
    tags: FileTags


# --------------------------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------------------------

_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")


def count_identifiers(text: str) -> dict[str, int]:
    counts = Counter(m.group() for m in _IDENT_RE.finditer(text))
    return {name: n for name, n in counts.items() if len(name) >= 3 and name not in KEYWORDS}


def _short(text: str, limit: int = SIGNATURE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _name_column(line: str, name: str, start: int = 0) -> int:
    match = re.search(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", line[start:])
    return (start + match.start() + 1) if match else start + 1


def _python_references(tree: ast.AST) -> tuple[dict[str, int], dict[str, int]]:
    """(names, attributes) that Python code refers to.

    `names` can mean top-level definitions elsewhere: names imported with `from ... import`,
    names read but never bound in the file (a local variable called `env` is not a use of
    another file's `env`; a module bound by `import json` is not a definition), and
    attributes of imported names (`proc.which`, `ir.ToolSpec`). `attributes` can mean
    members: every called attribute (`client.stream()`). Plain attribute reads such as
    `response.text` are left out: generic attribute names would swamp the ranking."""
    names: Counter[str] = Counter()
    attributes: Counter[str] = Counter()
    reads: Counter[str] = Counter()
    bound: set[str] = set()
    imported: set[str] = set()  # names that import statements bind
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".", 1)[0])
                imported.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names[alias.name] += 1
                    imported.add(alias.asname or alias.name)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                reads[node.id] += 1
            else:
                bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler
        ):
            bound.add(node.name or "")  # an `except` clause without `as` binds nothing
    bound.discard("")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attributes[node.func.attr] += 1
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in imported
        ):
            names[node.attr] += 1
    names.update({name: n for name, n in reads.items() if name not in bound})

    def keep(counts: Counter[str]) -> dict[str, int]:
        return {k: n for k, n in counts.items() if len(k) >= 3 and k not in KEYWORDS}

    return keep(names), keep(attributes)


def _python_imports(tree: ast.Module) -> list[str]:
    """Modules a file imports. `from a.b import c` records `a.b` and `a.b.c` (c may be a
    module); relative imports keep their dots and are resolved against the file's path."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            if node.module:
                found.append(base)
            joiner = "" if base.endswith(".") else "."
            found.extend(f"{base}{joiner}{alias.name}" for alias in node.names if alias.name != "*")
    return sorted(set(found))


def _python_file_tags(text: str) -> FileTags:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return FileTags("python", _regex_tags(text, "python"), count_identifiers(text))
    attributes: dict[str, int] | None
    try:
        identifiers, attributes = _python_references(tree)
        imports = _python_imports(tree)
    except RecursionError:
        identifiers, imports, attributes = count_identifiers(text), [], None
    return FileTags("python", _python_tags(tree, text), identifiers, imports, attributes)


def _python_tags(tree: ast.Module, text: str) -> list[Tag]:
    lines = text.splitlines()
    tags: list[Tag] = []

    def line_of(node: ast.AST) -> str:
        number = getattr(node, "lineno", 1)
        return lines[number - 1] if 0 < number <= len(lines) else ""

    def visit(body: list[ast.stmt], parent: str | None, depth: int, in_class: bool) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                try:
                    signature = f"{prefix} {node.name}({ast.unparse(node.args)})"
                    if node.returns is not None:
                        signature += f" -> {ast.unparse(node.returns)}"
                except (ValueError, RecursionError):
                    signature = line_of(node).strip()
                tags.append(
                    Tag(
                        node.name,
                        "method" if in_class else "function",
                        node.lineno,
                        node.end_lineno or node.lineno,
                        _name_column(line_of(node), node.name),
                        _short(signature),
                        parent,
                        depth,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                try:
                    bases = [ast.unparse(b) for b in node.bases]
                    bases += [ast.unparse(k) for k in node.keywords]
                except (ValueError, RecursionError):
                    bases = []
                signature = f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
                tags.append(
                    Tag(
                        node.name,
                        "class",
                        node.lineno,
                        node.end_lineno or node.lineno,
                        _name_column(line_of(node), node.name),
                        _short(signature),
                        parent,
                        depth,
                    )
                )
                visit(node.body, node.name, depth + 1, True)
            elif isinstance(node, ast.If | ast.Try | ast.With):
                for block in _blocks(node):
                    visit(block, parent, depth, in_class)
            elif depth == 0 and isinstance(node, ast.stmt):
                for target in _assign_targets(node):
                    if target[:1].isupper():
                        tags.append(
                            Tag(
                                target,
                                "variable",
                                node.lineno,
                                node.end_lineno or node.lineno,
                                _name_column(line_of(node), target),
                                _short(line_of(node).strip(), 100),
                                None,
                                0,
                            )
                        )

    visit(tree.body, None, 0, False)
    return tags


def _blocks(node: ast.If | ast.Try | ast.With) -> list[list[ast.stmt]]:
    if isinstance(node, ast.If):
        return [node.body, node.orelse]
    if isinstance(node, ast.Try):
        return [node.body, *[h.body for h in node.handlers], node.orelse, node.finalbody]
    return [node.body]


def _assign_targets(node: ast.stmt) -> list[str]:
    """Names bound at module level by an assignment or a `type X = ...` alias (3.12+)."""
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    if type(node).__name__ == "TypeAlias":
        name = getattr(node, "name", None)
        return [name.id] if isinstance(name, ast.Name) else []
    return []


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    kind: str
    container: bool = False  # later, deeper definitions get this one as their parent
    define: bool = True  # False: only opens a container (a Rust impl block)


def _p(regex: str, kind: str, container: bool = False, define: bool = True) -> _Pattern:
    return _Pattern(re.compile(regex), kind, container, define)


_JS_ID = r"[A-Za-z_$][\w$]*"
_JS = [
    _p(
        rf"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+(?P<name>{_JS_ID})",
        "class",
        container=True,
    ),
    _p(rf"^\s*(?:export\s+)?(?:declare\s+)?interface\s+(?P<name>{_JS_ID})", "interface", True),
    _p(rf"^\s*(?:export\s+)?(?:declare\s+)?(?:const\s+)?enum\s+(?P<name>{_JS_ID})", "enum"),
    _p(rf"^\s*(?:export\s+)?(?:declare\s+)?type\s+(?P<name>{_JS_ID})\s*(?:<[^=]*>)?\s*=", "type"),
    _p(
        rf"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*"
        rf"(?P<name>{_JS_ID})\s*[<(]",
        "function",
    ),
    _p(
        rf"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>{_JS_ID})\s*(?::[^=]+)?=\s*"
        rf"(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::\s*[^=]+)?=>|{_JS_ID}\s*=>)",
        "function",
    ),
    _p(
        r"^\s+(?:(?:public|private|protected|static|readonly|async|override|abstract|get|set)\s+)*"
        rf"(?P<name>{_JS_ID})\s*(?:<[^>()]*>)?\s*\((?:[^()]|\([^()]*\))*\)\s*"
        r"(?::\s*[^{;=]+)?\s*\{\s*$",
        "method",
    ),
]
_GO = [
    _p(
        r"^func\s+\(\s*\w*\s*\*?\s*(?P<parent>\w+)(?:\[[^\]]*\])?\s*\)\s*"
        r"(?P<name>[A-Za-z_]\w*)\s*[\[(]",
        "method",
    ),
    _p(r"^func\s+(?P<name>[A-Za-z_]\w*)\s*[\[(]", "function"),
    _p(r"^type\s+(?P<name>[A-Za-z_]\w*)\b", "type"),
]
_RS_VIS = r"(?:pub(?:\s*\([^)]*\))?\s+)?"
_RUST = [
    _p(
        rf"^\s*{_RS_VIS}(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
        r'(?:extern\s+"[^"]*"\s+)?fn\s+(?P<name>[A-Za-z_]\w*)',
        "function",
    ),
    _p(rf"^\s*{_RS_VIS}(?:struct|enum|union)\s+(?P<name>[A-Za-z_]\w*)", "type"),
    _p(rf"^\s*{_RS_VIS}(?:unsafe\s+)?trait\s+(?P<name>[A-Za-z_]\w*)", "trait", True),
    _p(rf"^\s*{_RS_VIS}type\s+(?P<name>[A-Za-z_]\w*)", "type"),
    _p(rf"^\s*{_RS_VIS}mod\s+(?P<name>[A-Za-z_]\w*)", "module", True),
    _p(r"^\s*macro_rules!\s*(?P<name>[A-Za-z_]\w*)", "macro"),
    _p(rf"^\s*{_RS_VIS}(?:const|static)\s+(?:mut\s+)?(?P<name>[A-Z_][A-Z0-9_]*)\s*:", "constant"),
    _p(
        r"^\s*(?:unsafe\s+)?impl\b(?:\s*<[^>]*>)?\s+(?:[\w:<>, ]+?\s+for\s+)?"
        r"(?P<name>[A-Za-z_]\w*)",
        "impl",
        container=True,
        define=False,
    ),
]
_JV_MOD = (
    r"(?:(?:public|protected|private|static|final|abstract|sealed|non-sealed|strictfp|"
    r"synchronized|native|default|transient|volatile)\s+)*"
)
_JAVA = [
    _p(
        rf"^\s*{_JV_MOD}(?:class|interface|enum|record|@interface)\s+(?P<name>[A-Za-z_$][\w$]*)",
        "class",
        container=True,
    ),
    _p(
        rf"^\s+{_JV_MOD}(?:<[^>]+>\s+)?(?P<type>[\w$.]+(?:<[^()]*>)?(?:\[\])*)\s+"
        r"(?P<name>[A-Za-z_$][\w$]*)\s*\([^;]*$",
        "method",
    ),
    _p(rf"^\s+{_JV_MOD}(?P<name>[A-Z][\w$]*)\s*\([^;]*$", "constructor"),
]
_C = [
    _p(r"^#\s*define\s+(?P<name>[A-Za-z_]\w*)", "macro"),
    # `typedef struct { ... } name;`: the name is on the closing line (see _regex_tags).
    _p(r"^\s*typedef\s+(?:struct|union|enum)(?P<name>)\s*\{\s*$", "type", container=True),
    _p(
        r"^\s*(?:typedef\s+)?(?:template\s*<[^>]*>\s*)?(?:struct|union|enum(?:\s+class)?|class)\s+"
        r"(?:\w+\s+)*?(?P<name>[A-Za-z_]\w*)\s*(?:final\s*)?(?::[^;{]*)?\{?\s*$",
        "type",
        container=True,
    ),
    _p(r"^\s*namespace\s+(?P<name>[A-Za-z_]\w*)\s*\{?\s*$", "namespace", container=True),
    _p(
        r"^(?P<type>(?:[\w:<>,*&~]+[\s*&]+)+)(?P<name>~?[A-Za-z_][\w:~]*)\s*\([^;]*\)\s*"
        r"(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?\{?\s*$",
        "function",
    ),
    _p(
        r"^(?P<type>(?:[\w:<>,*&~]+[\s*&]+)+)(?P<name>[A-Za-z_]\w*)\s*\([^;{]*\)\s*;",
        "function",
    ),
    _p(r"^\s*typedef\s+.*?\b(?P<name>[A-Za-z_]\w*)\s*;\s*$", "type"),
]
_RUBY = [
    _p(r"^\s*class\s+(?:[A-Z]\w*::)*(?P<name>[A-Z]\w*)", "class", container=True),
    _p(r"^\s*module\s+(?:[A-Z]\w*::)*(?P<name>[A-Z]\w*)", "module", container=True),
    _p(r"^\s*def\s+(?:self\.)?(?P<name>[A-Za-z_]\w*[?!=]?)", "method"),
]
_PYTHON_RE = [
    _p(r"^\s*class\s+(?P<name>[A-Za-z_]\w*)", "class", container=True),
    _p(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)", "function"),
]
PATTERNS: dict[str, list[_Pattern]] = {
    "javascript": _JS,
    "typescript": _JS,
    "go": _GO,
    "rust": _RUST,
    "java": _JAVA,
    "c": _C,
    "cpp": _C,
    "ruby": _RUBY,
    "python": _PYTHON_RE,
}
_BRACES = {"javascript", "typescript", "go", "rust", "java", "c", "cpp"}
_STRINGS_RE = re.compile(
    r'"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`|\'(?:\\.|[^\'\\])*\'|//.*$|/\*.*?\*/'
)
_RUST_STRINGS_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])\'|//.*$|/\*.*?\*/')
_TYPEDEF_NAME_RE = re.compile(r"^\s*}\s*\*?\s*(?P<name>[A-Za-z_]\w*)")
# A definition line that opens no block yet continues on the next line when it ends with one of
# these, or when the next line starts with one of _CONTINUED_BY (a brace on its own line, a
# `where` or `throws` clause, a constructor's initializer list).
_CONTINUES = ("(", "[", ",", "=", "=>", "->", "&&", "||", "?", ":")
_CONTINUED_BY = ("{", "where", "throws", "extends", "implements", ":", "->", "=>", ".", ")")


def _indent(line: str) -> int:
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())


def _comment_line(stripped: str, language: str) -> bool:
    if language in {"python", "ruby"}:
        return stripped.startswith("#")
    if stripped.startswith("#"):
        return language not in {"c", "cpp"} or not stripped.startswith(("#define", "# define"))
    return stripped.startswith(("//", "/*", "*"))


def _regex_tags(text: str, language: str) -> list[Tag]:
    patterns = PATTERNS.get(language, [])
    lines = text.splitlines()
    tags: list[Tag] = []
    stack: list[tuple[int, str]] = []
    in_block = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*") and "*/" not in stripped and language != "python":
            in_block = True
            continue
        if not stripped or _comment_line(stripped, language):
            continue
        for pattern in patterns:
            match = pattern.regex.match(line)
            if match is None:
                continue
            name = match.group("name")
            groups = match.groupdict()
            if name in KEYWORDS or (groups.get("type") or "").strip() in KEYWORDS:
                continue
            end = _end_line(lines, index, language)
            if language in {"c", "cpp"} and stripped.startswith("typedef") and "{" in line:
                alias = _TYPEDEF_NAME_RE.match(lines[end - 1])  # `} name;` closes the block
                name = alias.group("name") if alias else name
            if not name:
                break
            indent = _indent(line)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = groups.get("parent") or (stack[-1][1] if stack else None)
            if "::" in name:  # C++ out-of-class definition: Foo::bar
                qualifier, name = name.rsplit("::", 1)
                parent = qualifier.split("::")[-1] or parent
            if pattern.container:
                stack.append((indent, name))
            if not pattern.define:
                break
            kind = "method" if pattern.kind == "function" and parent else pattern.kind
            tags.append(
                Tag(
                    name,
                    kind,
                    index + 1,
                    end,
                    _name_column(line, name),
                    _short(stripped.rstrip("{").rstrip()),
                    parent,
                    len(stack) - (1 if pattern.container else 0),
                )
            )
            break
    return tags


def _end_line(lines: list[str], index: int, language: str) -> int:
    if language in _BRACES:
        return _brace_end(lines, index, language)
    indent = _indent(lines[index])
    for j in range(index + 1, len(lines)):
        stripped = lines[j].strip()
        if not stripped:
            continue
        if _indent(lines[j]) <= indent:
            if language == "ruby" and stripped.startswith("end"):
                return j + 1
            return j if language != "ruby" else index + 1
    return len(lines)


def _brace_end(lines: list[str], index: int, language: str, limit: int = 4000) -> int:
    """The last line (1-based) of the block that the definition on `lines[index]` opens, or of
    the declaration itself when it opens none (a prototype, an alias, a macro)."""
    strings = _RUST_STRINGS_RE if language == "rust" else _STRINGS_RE
    depth = parens = 0
    opened = False
    stop = min(len(lines), index + limit)
    for j in range(index, stop):
        raw = lines[j]
        directive = language in {"c", "cpp"} and raw.lstrip().startswith("#")
        code = "" if directive else strings.sub("", raw)
        for char in code:
            if char in "([":
                parens += 1
            elif char in ")]":
                parens = max(0, parens - 1)
            elif char == "{":
                depth += 1
                opened = True
            elif char == "}":
                depth -= 1
                if opened and depth <= 0:
                    return j + 1
        if opened:
            continue
        stripped = code.strip()
        if directive:
            if raw.rstrip().endswith("\\") and j + 1 < stop:  # a macro continues
                continue
            return j + 1
        if j - index >= 10:
            return index + 1
        if parens == 0 and stripped.endswith(";"):
            return j + 1
        following = next((row.strip() for row in lines[j + 1 : j + 3] if row.strip()), "")
        if parens > 0 or stripped.endswith(_CONTINUES) or following.startswith(_CONTINUED_BY):
            continue
        return j + 1
    return stop if opened else index + 1


def extract_tags(text: str, language: str) -> FileTags:
    """Definitions and identifier counts for one file's text."""
    if language == "python":
        return _python_file_tags(text)
    from stepswitch.context import treesitter

    parsed: FileTags | None = treesitter.file_tags(text, language)
    if parsed is not None:
        return parsed
    tags = _regex_tags(text, language)
    if language in _BRACES:  # count references in code only, not in strings and comments
        strings = _RUST_STRINGS_RE if language == "rust" else _STRINGS_RE
        text = "\n".join(strings.sub(" ", line) for line in text.splitlines())
    return FileTags(language, tags, count_identifiers(text))


# --------------------------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[.:#/\\-]+|::")

# Names that standard libraries use everywhere (`d.get()`, `path.read_text()`): a use of one is
# far more likely a library call than a reference to the project's own definition.
COMMON_NAMES = words(
    "get set put add pop push append extend insert remove delete clear copy update keys values "
    "items join split strip lstrip rstrip replace format read write open close flush seek send "
    "recv run start stop wait call apply map filter reduce sort sorted reverse find index count "
    "len size length next iter init main setup teardown load dump dumps loads parse render print "
    "log debug info warning warn error exception emit handle process execute exec text json "
    "value name key type str int float bool list dict tuple new create build make reset reload "
    "refresh toString valueOf equals hashCode compareTo then resolve reject fetch query all any "
    "sum min max abs round encode decode lower upper exists is_file is_dir mkdir unlink rename "
    "stat match search compile group groups sub read_text write_text read_bytes write_bytes "
    "as_posix resolve relative_to with_suffix with_name startswith endswith isinstance getattr "
    "setattr hasattr result results response request content status data path paths file "
    "files line lines label summary item test tests check validate convert to_dict from_dict "
    "as_dict model_dump model_validate model_copy"
)


def _identifier_weight(name: str, definers: int, focus_idents: set[str]) -> float:
    weight = 1.0
    if name in focus_idents:
        weight *= 10.0
    elif name in COMMON_NAMES:
        weight *= 0.05
    descriptive = "_" in name.strip("_") or "-" in name or any(c.isupper() for c in name[1:])
    if len(name) >= 8 and descriptive:
        weight *= 10.0
    if name.startswith("_"):
        weight *= 0.1
    if definers > 5:
        weight *= 0.1
    return weight


def _module_names(rel: str) -> list[str]:
    """Dotted names a Python file can be imported as: every suffix of its path's parts
    (`src/pkg/mod.py` -> `pkg.mod`, `mod`, ...); a package's `__init__.py` is the package."""
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return [".".join(parts[i:]) for i in range(len(parts)) if parts[i:]]


def _resolve_import(module: str, rel: str) -> str:
    """An import as written in `rel`, made absolute when it is relative (leading dots)."""
    level = len(module) - len(module.lstrip("."))
    if level == 0:
        return module
    package = list(Path(rel).parent.parts)
    if level > 1:
        package = package[: len(package) - (level - 1)]
    rest = module[level:]
    return ".".join([*package, rest] if rest else package)


def pagerank(
    nodes: list[str],
    edges: dict[str, dict[str, float]],
    personalization: dict[str, float] | None = None,
    *,
    damping: float = 0.85,
    iterations: int = 100,
    tolerance: float = 1e-10,
    min_out_weight: float = 0.0,
) -> dict[str, float]:
    """Weighted personalized PageRank by power iteration. Rank a node does not pass along its
    edges follows the teleport: all of it for a node without edges, and the part missing
    when its edges weigh less than `min_out_weight` in total (weak links pass on little)."""
    if not nodes:
        return {}
    weights = {n: (personalization or {}).get(n, 0.0) for n in nodes}
    total = sum(weights.values())
    teleport = {n: w / total for n, w in weights.items()} if total > 0 else None
    if teleport is None:
        teleport = {n: 1.0 / len(nodes) for n in nodes}
    out_weight = {n: sum(edges.get(n, {}).values()) for n in nodes}
    rank = dict(teleport)
    for _ in range(iterations):
        new = dict.fromkeys(nodes, 0.0)
        kept = 0.0  # rank that does not flow along an edge
        for source in nodes:
            norm = max(out_weight[source], min_out_weight)
            if norm <= 0:
                kept += rank[source]
                continue
            share = damping * rank[source] / norm
            for target, weight in edges.get(source, {}).items():
                new[target] += share * weight
            kept += rank[source] * (1.0 - out_weight[source] / norm)
        base = 1.0 - damping + damping * kept
        for node in nodes:
            new[node] += base * teleport[node]
        delta = sum(abs(new[n] - rank[n]) for n in nodes)
        rank = new
        if delta < tolerance:
            break
    return rank


class RepoMap:
    """Ranked, token-budgeted outline of the source files under `root`."""

    def __init__(
        self,
        root: Path,
        *,
        cache_dir: Path | None = None,
        token_counter: TokenCounter | None = None,
        max_file_bytes: int = MAX_FILE_BYTES,
        exclude: Callable[[str], bool] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.cache_dir = cache_dir
        self.token_counter = token_counter
        self.max_file_bytes = max_file_bytes
        self.exclude = exclude  # root-relative paths to leave out (e.g. privacy deny lists)
        self._files: dict[str, _FileEntry] = {}
        self._memory: dict[tuple[str, str], FileTags] = {}
        self._version = 0
        self._rank_cache: tuple[int, tuple[str, ...], list[Ranked]] | None = None

    # -- files and tags ----------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read files whose size or mtime changed (recently touched files always)."""
        seen: set[str] = set()
        now_ns = time.time_ns()
        changed = False
        for rel, path in iter_files(
            self.root, matcher=IgnoreMatcher(self.root), max_bytes=self.max_file_bytes
        ):
            language = language_for(rel)
            if language is None or (self.exclude is not None and self.exclude(rel)):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            key = (stat.st_mtime_ns, stat.st_size)
            entry = self._files.get(rel)
            recent = now_ns - stat.st_mtime_ns < 2_000_000_000
            if entry is not None and entry.stat_key == key and not recent:
                seen.add(rel)
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if looks_binary(data):
                continue
            seen.add(rel)
            sha = hashlib.sha256(data).hexdigest()
            if entry is not None and entry.sha == sha:
                entry.stat_key = key
                continue
            tags = self._tags_for(sha, language, data)
            self._files[rel] = _FileEntry(key, sha, tags)
            changed = True
        for rel in set(self._files) - seen:
            del self._files[rel]
            changed = True
        if changed:
            self._version += 1

    def invalidate(self, path: str | None = None) -> None:
        """Forget cached stat data (for one root-relative path, or all), e.g. after an edit."""
        if path is None:
            for entry in self._files.values():
                entry.stat_key = (-1, -1)
        elif path in self._files:
            self._files[path].stat_key = (-1, -1)

    def _cache_path(self, sha: str, language: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"repomap-v{TAGS_VERSION}" / sha[:2] / f"{sha}-{language}.json"

    def _tags_for(self, sha: str, language: str, data: bytes) -> FileTags:
        cached = self._memory.get((sha, language))
        if cached is not None:
            return cached
        disk = self._cache_path(sha, language)
        if disk is not None and disk.is_file():
            with contextlib.suppress(OSError, ValueError, TypeError):
                loaded = FileTags.from_json(json.loads(disk.read_text(encoding="utf-8")))
                if loaded is not None:
                    self._memory[(sha, language)] = loaded
                    return loaded
        tags = extract_tags(data.decode("utf-8", errors="replace"), language)
        self._memory[(sha, language)] = tags
        if disk is not None:
            with contextlib.suppress(OSError):
                disk.parent.mkdir(parents=True, exist_ok=True)
                tmp = disk.with_name(f"{disk.name}.{os.getpid()}.tmp")
                tmp.write_text(json.dumps(tags.to_json()), encoding="utf-8")
                tmp.replace(disk)
        return tags

    def files(self) -> list[str]:
        self.refresh()
        return sorted(self._files)

    def tags(self, path: str, *, refresh: bool = True) -> FileTags | None:
        """The definitions and references of one file; `refresh=False` skips the walk over
        the tree, for a caller that refreshed once before asking about many files."""
        if refresh:
            self.refresh()
        entry = self._files.get(path)
        return entry.tags if entry is not None else None

    def definitions(self, name: str | None = None, *, path: str | None = None) -> list[Definition]:
        """Definitions, optionally only those called `name` and/or only in `path`."""
        self.refresh()
        found: list[Definition] = []
        for rel in sorted(self._files):
            if path is not None and rel != path:
                continue
            for tag in self._files[rel].tags.tags:
                if name is None or tag.name == name:
                    found.append(Definition.at(rel, tag))
        return found

    # -- ranking -----------------------------------------------------------------------------

    def _split_focus(self, focus: Iterable[str]) -> tuple[set[str], set[str]]:
        files = set(self._files)
        focus_files: set[str] = set()
        focus_idents: set[str] = set()
        for item in focus:
            norm = item.replace("\\", "/").strip().strip("/")
            if norm.startswith("./"):
                norm = norm[2:]
            if not norm:
                continue
            if norm in files:
                focus_files.add(norm)
                continue
            inside = {f for f in files if f.startswith(norm + "/")}
            if inside:
                focus_files |= inside
                continue
            focus_idents |= {part for part in _SPLIT_RE.split(norm) if part}
        return focus_files, focus_idents

    def rank(self, focus: Iterable[str] = ()) -> list[Ranked]:
        """Every definition with a score, best first (ties broken by path and line)."""
        self.refresh()
        key = tuple(sorted(focus))
        if self._rank_cache is not None and self._rank_cache[:2] == (self._version, key):
            return self._rank_cache[2]
        ranked = self._rank(key)
        self._rank_cache = (self._version, key, ranked)
        return ranked

    def _rank(self, focus: tuple[str, ...]) -> list[Ranked]:
        focus_files, focus_idents = self._split_focus(focus)
        nodes = sorted(self._files)
        definers: dict[str, set[str]] = defaultdict(set)  # name -> files defining it anywhere
        top_level: dict[str, set[str]] = defaultdict(set)
        members: dict[str, set[str]] = defaultdict(set)
        for rel in nodes:
            for tag in self._files[rel].tags.tags:
                definers[tag.name].add(rel)
                (members if tag.parent else top_level)[tag.name].add(rel)

        # Python files can say which modules they use: a name used in a file that does not
        # import the defining module is most likely something else with the same name.
        modules: dict[str, set[str]] = defaultdict(set)
        for rel in nodes:
            if self._files[rel].tags.language == "python":
                for module in _module_names(rel):
                    modules[module].add(rel)

        # edges[user][definer] = weight; per_ident[(user, definer)][name] = weight
        edges: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        per_ident: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
        referenced: set[tuple[str, str]] = set()
        for user in nodes:
            user_tags = self._files[user].tags
            imported: set[str] | None = None
            if user_tags.language == "python":
                imported = set()
                for module in user_tags.imports:
                    imported |= modules.get(_resolve_import(module, user), set())
            user_mul = 50.0 if user in focus_files else 1.0
            # With a syntax tree, a plain name means a top-level definition and a called
            # attribute a member; without one, a name may mean either.
            uses: list[tuple[dict[str, int], dict[str, set[str]]]] = [
                (user_tags.identifiers, definers)
            ]
            if user_tags.attributes is not None:
                uses = [(user_tags.identifiers, top_level), (user_tags.attributes, members)]
            for counts, table in uses:
                for name, count in counts.items():
                    where = table.get(name)
                    if not where or (table is members and user in where):
                        continue  # `self.name()` most likely means the file's own member
                    # An ambiguous name spreads its weight over every file that defines it.
                    base = _identifier_weight(name, len(where), focus_idents) * user_mul
                    weight = base * math.sqrt(count) / len(where)
                    for definer in where:
                        if definer == user:
                            continue
                        unimported = (
                            imported is not None
                            and definer not in imported
                            and self._files[definer].tags.language == "python"
                        )
                        edge = weight * (0.1 if unimported else 1.0)
                        edges[user][definer] += edge
                        named = per_ident[(user, definer)]
                        named[name] = named.get(name, 0.0) + edge
                        referenced.add((definer, name))
        personalization: dict[str, float] = {}
        for rel in nodes:
            parts = set(Path(rel).with_suffix("").parts)
            defines_focus = any(rel in definers.get(name, ()) for name in focus_idents)
            if rel in focus_files or parts & focus_idents or defines_focus:
                personalization[rel] = 1.0
        graph = {k: dict(v) for k, v in edges.items()}
        file_rank = pagerank(nodes, graph, personalization, min_out_weight=MIN_OUT_WEIGHT)

        out_weight = {user: sum(targets.values()) for user, targets in edges.items()}
        scores: dict[tuple[str, str], float] = defaultdict(float)
        for (user, definer), names in per_ident.items():
            total = max(out_weight.get(user, 0.0), MIN_OUT_WEIGHT)
            for name, weight in names.items():
                scores[(definer, name)] += file_rank.get(user, 0.0) * weight / total
        # Definitions no other file references get a small share of their own file's rank, so
        # they follow the referenced ones, in the order of their files' importance. (A self-edge
        # in the graph instead would make such a file a rank sink.)
        for definer in nodes:
            unreferenced = {
                tag.name
                for tag in self._files[definer].tags.tags
                if (definer, tag.name) not in referenced
            }
            for name in unreferenced:
                share = UNREFERENCED_SHARE * file_rank.get(definer, 0.0) / len(unreferenced)
                scores[(definer, name)] += share

        ranked: list[Ranked] = []
        for rel in nodes:
            for tag in self._files[rel].tags.tags:
                score = scores.get((rel, tag.name), 0.0)
                if tag.name in focus_idents:
                    score = score * 10.0 + 1.0
                ranked.append(Ranked(Definition.at(rel, tag), score))
        ranked.sort(key=lambda r: (-r.score, r.definition.path, r.definition.line))
        return ranked

    # -- rendering ---------------------------------------------------------------------------

    def count_tokens(self, text: str) -> tuple[int, bool]:
        if self.token_counter is None:
            return estimate_tokens(text), True
        return self.token_counter(text), bool(getattr(self.token_counter, "estimated", False))

    @staticmethod
    def format(definitions: list[Definition]) -> str:
        """Group definitions by file (files in the given order) with "line signature" rows."""
        by_file: dict[str, list[Definition]] = {}
        for definition in definitions:
            by_file.setdefault(definition.path, []).append(definition)
        blocks: list[str] = []
        for path, items in by_file.items():
            rows = [f"{path}:"]
            for d in sorted(items, key=lambda d: (d.line, d.column)):
                rows.append(f"  {d.line} {'  ' * d.depth}{d.signature}")
            blocks.append("\n".join(rows))
        return "\n".join(blocks)

    def render(
        self, *, focus: Iterable[str] = (), max_tokens: int = DEFAULT_MAP_TOKENS
    ) -> RepoMapResult:
        """The largest ranked prefix of definitions whose rendering fits `max_tokens`."""
        ranked = self.rank(focus)
        definitions = [r.definition for r in ranked]

        def render_first(count: int) -> tuple[str, int, bool]:
            text = self.format(definitions[:count])
            tokens, estimated = self.count_tokens(text)
            return text, tokens, estimated

        low, high = 0, len(definitions)
        best: tuple[str, int, bool] = ("", 0, self.token_counter is None)
        best_count = 0
        while low <= high:
            middle = (low + high) // 2
            text, tokens, estimated = render_first(middle)
            if tokens <= max_tokens:
                best, best_count = (text, tokens, estimated), middle
                low = middle + 1
            else:
                high = middle - 1
        files = list(dict.fromkeys(d.path for d in definitions[:best_count]))
        return RepoMapResult(best[0], best[1], best[2], files, best_count, len(definitions))
