"""The built-in agent's toolbox (SPEC §12 item 1, ARCHITECTURE §7 and §8).

`Toolbox(root)` exposes `read`, `grep`, `repo_map`, `lsp`, `edit`, `run`, `test` and `search`
plus the control tool `finish`. `specs()` gives their IR tool specs (short descriptions: small
models read them every step) and `execute(name, arguments)` runs one call. `execute` never
raises for a tool problem: bad JSON, an unknown tool or bad arguments come back as
`ToolOutput(is_error=True)` with one helpful paragraph, so the agent loop can pass it to the
model as is. Arguments are read forgivingly (common synonyms, numbers as strings, a bare
string for a one-argument tool).

All paths are relative to `root`. Absolute paths, paths that resolve outside `root` (also
through symbolic links), the `.git` and `.stepswitch` directories, and paths matched by the
config's `privacy.deny_paths` are refused. Output is bounded by `max_output_chars` (head and
tail with an elision note). `run` starts programs without a shell, in `root`, with a minimal
environment that never carries secrets.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stepswitch.compat.ir import ToolSpec
from stepswitch.context import edits as edit_format
from stepswitch.context import proc
from stepswitch.context.deny import DenyList
from stepswitch.context.walk import (
    SKIP_DIRS,
    IgnoreMatcher,
    iter_files,
    looks_binary,
)

TOOL_NAMES = ("read", "grep", "repo_map", "lsp", "edit", "run", "test", "search")
CONTROL_TOOLS = ("finish",)

READ_FILE_LIMIT = 10_000_000
READ_LINE_CHARS = 500
READ_WHOLE_LINES = 60  # files this short are always shown whole, whatever range is asked
GREP_RESULTS = 100
LOCATION_RESULTS = 50

_ANCHOR = {"type": "string", "description": "line:hash from read"}
_STR: dict[str, Any] = {"type": "string"}
_INT: dict[str, Any] = {"type": "integer"}
_SPECS: dict[str, tuple[str, dict[str, Any], tuple[str, ...]]] = {
    "read": (
        "Read a file (or list a directory). Lines show as `line:hash|text`; "
        "edit takes the `line:hash` anchors.",
        {"path": _STR, "start": _INT, "end": _INT},
        ("path",),
    ),
    "grep": (
        "Search file contents with a regex; returns path:line: text.",
        {
            "pattern": _STR,
            "path": {"type": "string", "description": "file or directory"},
            "glob": {"type": "string", "description": "e.g. *.py"},
            "ignore_case": {"type": "boolean"},
        },
        ("pattern",),
    ),
    "repo_map": (
        "Ranked outline of the repo: files and their key definitions.",
        {
            "focus": {
                "type": "array",
                "items": _STR,
                "description": "paths or symbol names",
            },
            "max_tokens": _INT,
        },
        (),
    ),
    "lsp": (
        "Ask the language server: defs, refs, hover or diagnostics at a position or symbol.",
        {
            "action": {"type": "string", "enum": ["defs", "refs", "hover", "diagnostics"]},
            "path": _STR,
            "line": _INT,
            "column": _INT,
            "symbol": _STR,
        },
        ("action",),
    ),
    "edit": (
        "Change a file with anchors from read. Or give `text` to create a file "
        "(create: true) or rewrite a small one.",
        {
            "path": _STR,
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": ["replace", "insert_after", "insert_before", "delete"],
                        },
                        "start": _ANCHOR,
                        "end": _ANCHOR,
                        "text": _STR,
                    },
                    "required": ["op", "start"],
                },
            },
            "text": _STR,
            "create": {"type": "boolean"},
        },
        ("path",),
    ),
    "run": (
        "Run a command in the repo root. No shell: no pipes, redirects or &&.",
        {"command": _STR, "timeout": {"type": "number"}},
        ("command",),
    ),
    "test": (
        "Run the tests (or one file, test id or -k filter) and summarize failures.",
        {"selector": _STR},
        (),
    ),
    "search": (
        "Find code related to a description.",
        {"query": _STR, "k": _INT},
        ("query",),
    ),
    "finish": (
        "Stop and report: what changed and whether it is done.",
        {"summary": _STR},
        ("summary",),
    ),
}

TOOL_ALIASES = {
    "read_file": "read",
    "open": "read",
    "cat": "read",
    "view": "read",
    "ls": "read",
    "search_files": "grep",
    "rg": "grep",
    "repomap": "repo_map",
    "map": "repo_map",
    "edit_file": "edit",
    "write": "edit",
    "write_file": "edit",
    "apply_edit": "edit",
    "bash": "run",
    "shell": "run",
    "exec": "run",
    "run_command": "run",
    "run_tests": "test",
    "tests": "test",
    "semantic_search": "search",
    "search_code": "search",
    "done": "finish",
    "submit": "finish",
    "complete": "finish",
}

_PATH_ALIASES = ("file", "filename", "file_path", "filepath", "path_name")
_PARAMS: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
    "read": {
        "path": ("str", _PATH_ALIASES),
        "start": ("int", ("start_line", "line_start", "from", "offset", "line")),
        "end": ("int", ("end_line", "line_end", "to")),
    },
    "grep": {
        "pattern": ("str", ("query", "regex", "search", "text", "expression")),
        "path": ("str", ("dir", "directory", *_PATH_ALIASES)),
        "glob": ("str", ("include", "files", "file_glob", "file_pattern")),
        "ignore_case": ("bool", ("case_insensitive", "i", "nocase", "insensitive")),
    },
    "repo_map": {
        "focus": ("list", ("files", "symbols", "paths", "focus_files")),
        "max_tokens": ("int", ("tokens", "budget", "limit")),
    },
    "lsp": {
        "action": ("str", ("op", "kind", "method", "request", "command")),
        "path": ("str", _PATH_ALIASES),
        "line": ("int", ("row", "line_number")),
        "column": ("int", ("col", "character", "char")),
        "symbol": ("str", ("name", "identifier")),
    },
    "edit": {
        "path": ("str", _PATH_ALIASES),
        "edits": ("any", ("changes", "ops", "operations", "edit", "patch")),
        "text": ("str", ("content", "contents", "new_text", "code", "file_text")),
        "create": ("bool", ("new", "new_file", "create_file")),
    },
    "run": {
        "command": ("command", ("cmd", "commandline", "command_line", "args", "argv", "shell")),
        "timeout": ("float", ("timeout_s", "seconds", "time_limit")),
    },
    "test": {"selector": ("str", ("test", "tests", "target", "path", "file", "filter", "k"))},
    "search": {
        "query": ("str", ("q", "text", "description", "pattern", "search")),
        "k": ("int", ("limit", "top_k", "n", "count")),
    },
    "finish": {"summary": ("str", ("message", "result", "reason", "answer", "text"))},
}
_EXAMPLES = {
    "read": '{"path": "src/app.py", "start": 1, "end": 80}',
    "grep": '{"pattern": "def main", "glob": "*.py"}',
    "repo_map": '{"focus": ["src/app.py"]}',
    "lsp": '{"action": "defs", "path": "src/app.py", "line": 12, "column": 8}',
    "edit": '{"path": "src/app.py", "edits": [{"op": "replace", "start": "12:ab", '
    '"end": "13:cd", "text": "    return a + b"}]}',
    "run": '{"command": "python -m pytest -q"}',
    "test": '{"selector": "tests/test_app.py"}',
    "search": '{"query": "where the config file is parsed"}',
    "finish": '{"summary": "Fixed the off-by-one in parse_range; tests pass."}',
}
_LSP_ACTIONS = {
    "defs": "defs",
    "def": "defs",
    "definition": "defs",
    "definitions": "defs",
    "goto": "defs",
    "goto_definition": "defs",
    "refs": "refs",
    "ref": "refs",
    "references": "refs",
    "find_references": "refs",
    "usages": "refs",
    "hover": "hover",
    "info": "hover",
    "type": "hover",
    "doc": "hover",
    "diagnostics": "diagnostics",
    "diagnostic": "diagnostics",
    "diag": "diagnostics",
    "errors": "diagnostics",
    "check": "diagnostics",
}


@dataclass
class ToolOutput:
    text: str  # what the model sees; bounded by max_output_chars
    is_error: bool = False
    data: dict[str, Any] = field(default_factory=dict)  # facts for trajectories
    changed_files: list[str] = field(default_factory=list)
    # The whole text when `max_output_chars` cut it; the agent distills it (TK4).
    raw: str | None = None


class ToolError(Exception):
    """A refused or failed tool call; the message goes to the model."""


def bound(text: str, limit: int) -> str:
    """`text` cut to about `limit` characters: head and tail kept, the middle elided."""
    if len(text) <= limit:
        return text
    head_size = int(limit * 0.6)
    tail_size = max(0, limit - head_size - 60)
    head = text[:head_size]
    if "\n" in head:
        head = head[: head.rfind("\n")]
    tail = text[len(text) - tail_size :] if tail_size else ""
    if "\n" in tail:
        tail = tail[tail.find("\n") + 1 :]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n[... {omitted} characters omitted ...]\n{tail}"


# --------------------------------------------------------------------------------------------
# Test output summaries
# --------------------------------------------------------------------------------------------

_PYTEST_COUNTS = re.compile(
    r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?|rerun)"
)
_PYTEST_FINAL = re.compile(r"^(?:=+ )?(?:\d+ \w+(?:, )?)+(?: in [\d.]+s)")
_PYTEST_FAILED = re.compile(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$")
_GO_FAIL = re.compile(r"^\s*--- FAIL: (\S+)")
_CARGO_FAIL = re.compile(r"^test (\S+) \.\.\. FAILED")
_CARGO_RESULT = re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored")
_JEST_TOTALS = re.compile(r"^Tests:\s+(.*)$")
_JEST_FAIL = re.compile(r"^\s*●\s+(.+?)\s*$")


@dataclass
class TestSummary:
    passed: int | None = None
    failed: int | None = None
    errors: int | None = None
    skipped: int | None = None
    failing: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    runner: str = "unknown"

    def counts(self) -> dict[str, int | None]:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
        }


def summarize_tests(output: str) -> TestSummary:
    """Counts, failing test ids and the decisive assertion lines from a test run's output
    (pytest in detail; go test, cargo test and jest-style summaries in outline)."""
    summary = TestSummary()
    lines = output.splitlines()
    for line in reversed(lines):
        stripped = line.strip().strip("=").strip()
        if _PYTEST_FINAL.match(stripped) and " in " in stripped:
            counts = {kind: int(n) for n, kind in _PYTEST_COUNTS.findall(stripped)}
            summary.runner = "pytest"
            summary.passed = counts.get("passed", 0)
            summary.failed = counts.get("failed", 0)
            summary.errors = counts.get("error", 0) + counts.get("errors", 0)
            summary.skipped = counts.get("skipped", 0)
            break
    for line in lines:
        match = _PYTEST_FAILED.match(line.strip())
        if match:
            test_id = match.group(2)
            if test_id not in summary.failing:
                summary.failing.append(test_id)
            if match.group(3):
                summary.details.append(f"{test_id}: {match.group(3).strip()}")
        elif (go := _GO_FAIL.match(line)) is not None:
            summary.runner = "go"
            summary.failing.append(go.group(1))
        elif (cargo := _CARGO_FAIL.match(line)) is not None:
            summary.runner = "cargo"
            summary.failing.append(cargo.group(1))
        elif (jest := _JEST_FAIL.match(line)) is not None and "›" in jest.group(1):
            summary.failing.append(jest.group(1))
    if summary.runner == "pytest":
        evidence = [
            line.rstrip()
            for line in lines
            if line.startswith("E ") or re.match(r"^\S+\.py:\d+: \w+", line)
        ]
        summary.details += evidence[:30]
    for line in lines:
        if (cargo_total := _CARGO_RESULT.search(line)) is not None:
            summary.runner = "cargo"
            summary.passed = (summary.passed or 0) + int(cargo_total.group(1))
            summary.failed = (summary.failed or 0) + int(cargo_total.group(2))
            summary.skipped = (summary.skipped or 0) + int(cargo_total.group(3))
        elif (jest_total := _JEST_TOTALS.match(line.strip())) is not None:
            summary.runner = "jest"
            for n, kind in re.findall(r"(\d+) (passed|failed|skipped)", jest_total.group(1)):
                setattr(summary, kind, int(n))
    return summary


# --------------------------------------------------------------------------------------------
# The toolbox
# --------------------------------------------------------------------------------------------


class Toolbox:
    """The agent's tools over one working directory (normally a task worktree)."""

    def __init__(
        self,
        root: Path,
        *,
        test_command: Sequence[str] | None = None,
        run_timeout: float = 120.0,
        allow_run: bool = True,
        config: Any | None = None,
        embedder: Any | None = None,
        offline: bool = False,
        max_output_chars: int = 6000,
        cache_dir: Path | None = None,
        env: Mapping[str, str] | None = None,
        test_timeout: float | None = None,
        lsp_manager: Any | None = None,
    ) -> None:
        self.root = root.resolve()
        self.test_command = list(test_command) if test_command else None
        self.run_timeout = run_timeout
        self.test_timeout = test_timeout or max(run_timeout, 600.0)
        self.allow_run = allow_run
        self.config = config
        self.embedder = embedder
        self.offline = offline
        self.max_output_chars = max_output_chars
        self.cache_dir = cache_dir
        self.env = dict(env or {})
        self._deny = DenyList.from_config(config)
        self._seen: dict[str, edit_format.Seen] = {}
        self._repo_map: Any = None
        self._search: Any = None
        self._lsp: Any = lsp_manager

    # -- specs -------------------------------------------------------------------------------

    def specs(self, names: Iterable[str] | None = None) -> list[ToolSpec]:
        """IR tool specs, by default for every tool in TOOL_NAMES plus `finish`. Each carries
        one example argument object (no wire format sends it; the fallback protocol shows it)."""
        wanted = list(names) if names is not None else [*TOOL_NAMES, *CONTROL_TOOLS]
        specs = []
        for name in wanted:
            description, properties, required = _SPECS[name]
            parameters: dict[str, Any] = {"type": "object", "properties": properties}
            if required:
                parameters["required"] = list(required)
            example: dict[str, Any] = json.loads(_EXAMPLES[name])
            specs.append(
                ToolSpec(
                    name=name, description=description, parameters=parameters, examples=[example]
                )
            )
        return specs

    # -- dispatch ----------------------------------------------------------------------------

    def execute(self, name: str, arguments: Mapping[str, Any] | str | None) -> ToolOutput:
        """Run one tool call. Never raises for tool errors (see the module docstring)."""
        tool = TOOL_ALIASES.get((name or "").strip().lower(), (name or "").strip().lower())
        if tool not in _SPECS:
            available = ", ".join([*TOOL_NAMES, *CONTROL_TOOLS])
            return ToolOutput(f"unknown tool {name!r}. Available tools: {available}.", True)
        try:
            args = self._arguments(tool, arguments)
            handler: Callable[[dict[str, Any]], ToolOutput] = getattr(self, f"_tool_{tool}")
            output = handler(args)
        except ToolError as exc:
            output = ToolOutput(str(exc), is_error=True)
        except edit_format.EditError as exc:
            output = ToolOutput(f"edit refused: {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001 - a tool failure must never end the agent loop
            output = ToolOutput(f"{tool} failed: {type(exc).__name__}: {exc}", is_error=True)
        if len(output.text) > self.max_output_chars:
            output.raw = output.text
        output.text = bound(output.text, self.max_output_chars)
        output.data.setdefault("tool", tool)
        return output

    def close(self) -> None:
        if self._lsp is not None:
            self._lsp.close()

    def _arguments(self, tool: str, arguments: Mapping[str, Any] | str | None) -> dict[str, Any]:
        spec = _PARAMS[tool]
        example = _EXAMPLES[tool]
        raw: Any = arguments
        if raw is None:
            raw = {}
        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            if not text:
                raw = {}
            elif text[:1] in "{[":
                try:
                    raw = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ToolError(
                        f"{tool}: arguments are not valid JSON ({exc.msg} at character "
                        f"{exc.pos}). Send one JSON object, for example {example}"
                    ) from exc
            else:
                required = _SPECS[tool][2]
                if len(required) != 1:
                    raise ToolError(f"{tool}: send the arguments as a JSON object, e.g. {example}")
                raw = {required[0]: text}
        if not isinstance(raw, Mapping):
            raise ToolError(f"{tool}: arguments must be a JSON object, e.g. {example}")
        given = {str(k).strip().lower(): v for k, v in raw.items()}
        args: dict[str, Any] = {}
        for name, (kind, aliases) in spec.items():
            key = next((k for k in (name, *aliases) if k in given and given[k] is not None), None)
            if key is None:
                continue
            args[name] = self._coerce(tool, name, kind, given.pop(key), example)
        for name in _SPECS[tool][2]:
            if name not in args and not (tool == "finish" and name == "summary"):
                raise ToolError(f"{tool}: missing `{name}`. Example: {example}")
        return args

    @staticmethod
    def _coerce(tool: str, name: str, kind: str, value: Any, example: str) -> Any:
        def fail(expected: str) -> ToolError:
            return ToolError(
                f"{tool}: `{name}` must be {expected}, got {value!r}. Example: {example}"
            )

        if kind == "any":
            return value
        if kind == "command":
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                return value
            if isinstance(value, str):
                return value
            raise fail("a command string")
        if kind == "str":
            if isinstance(value, str):
                return value
            if isinstance(value, int | float) and not isinstance(value, bool):
                return str(value)
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                return " ".join(value)
            raise fail("a string")
        if kind in {"int", "float"}:
            if isinstance(value, bool):
                raise fail("a number")
            if isinstance(value, int | float):
                return int(value) if kind == "int" else float(value)
            if isinstance(value, str) and re.fullmatch(r"\s*-?\d+(\.\d+)?\s*", value):
                return int(float(value)) if kind == "int" else float(value)
            raise fail("a number")
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, int | float):
                return bool(value)
            if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "on"}:
                return True
            if isinstance(value, str) and value.strip().lower() in {"false", "0", "no", "off"}:
                return False
            raise fail("true or false")
        if kind == "list":
            if isinstance(value, str):
                return [part.strip() for part in value.split(",") if part.strip()]
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                return value
            raise fail("a list of strings")
        raise fail(kind)

    # -- paths -------------------------------------------------------------------------------

    def denied(self, rel: str, *, is_dir: bool = False) -> bool:
        """Whether a root-relative path (a directory when `is_dir`) is excluded by
        `privacy.deny_paths`."""
        return self._deny.denied(rel, is_dir=is_dir)

    def resolve(self, raw: str, *, must_exist: bool = True) -> tuple[Path, str]:
        """(absolute path, POSIX path relative to root) for a path the model gave."""
        text = str(raw).strip().strip("\"'`")
        if not text:
            raise ToolError("the path is empty; give a path relative to the repository root")
        normalized = text.replace("\\", "/")
        if normalized.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", normalized):
            hint = ""
            with contextlib.suppress(ValueError, OSError):
                inside = Path(text).expanduser().resolve().relative_to(self.root).as_posix()
                hint = f" Use `{inside}`."
            raise ToolError(
                "absolute paths are not accepted; paths are relative to the repository root." + hint
            )
        while normalized.startswith("./"):
            normalized = normalized[2:]
        resolved = (self.root / normalized).resolve()
        if not resolved.is_relative_to(self.root):
            raise ToolError(f"{text} is outside the repository; paths must stay inside it")
        rel = resolved.relative_to(self.root).as_posix()
        rel = "." if rel in {"", "."} else rel
        parts = rel.split("/")
        if ".git" in parts or ".stepswitch" in parts:
            raise ToolError(f"{rel} is internal state (.git or .stepswitch) and is not available")
        if rel != "." and self.denied(rel, is_dir=resolved.is_dir()):
            raise ToolError(f"{rel} is excluded by the privacy deny list")
        if must_exist and not resolved.exists():
            raise ToolError(f"no such file or directory: {rel}.{self._suggest(Path(rel).name)}")
        return resolved, rel

    def _named(self, name: str, limit: int) -> list[str]:
        """Up to `limit` files of the repository whose file name is `name`."""
        found: list[str] = []
        for rel, _ in iter_files(self.root, matcher=IgnoreMatcher(self.root), max_bytes=None):
            if rel.rsplit("/", 1)[-1] == name and not self.denied(rel):
                found.append(rel)
                if len(found) == limit:
                    break
        return found

    def _suggest(self, name: str) -> str:
        found = self._named(name, 3)
        return f" Did you mean: {', '.join(found)}?" if found else ""

    def _only_named(self, path: Path, rel: str) -> tuple[Path, str] | None:
        """For a missing `rel`: the one file of the repository with its file name, if there is
        exactly one. Small models misremember directories (`src/calc.py` for `calc.py`) and tend
        to repeat a failed call; the file they mean is then not in doubt."""
        if path.exists():
            return None
        found = self._named(rel.rsplit("/", 1)[-1], 2)
        return self.resolve(found[0]) if len(found) == 1 else None

    # -- lazily built services ---------------------------------------------------------------

    def repo_map(self) -> Any:
        if self._repo_map is None:
            from stepswitch.context.repomap import RepoMap

            cache = self.cache_dir / "repomap" if self.cache_dir else None
            self._repo_map = RepoMap(self.root, cache_dir=cache, exclude=self.denied)
        return self._repo_map

    def search_index(self) -> Any:
        if self._search is None:
            from stepswitch.context.search import SearchIndex

            cache = self.cache_dir / "search" if self.cache_dir else None
            self._search = SearchIndex(
                self.root, repo_map=self.repo_map(), cache_dir=cache, exclude=self.denied
            )
        return self._search

    def lsp_manager(self) -> Any:
        if self._lsp is None:
            from stepswitch.context.lsp import LspManager

            self._lsp = LspManager(self.root, config=self.config)
        return self._lsp

    # -- read --------------------------------------------------------------------------------

    def _tool_read(self, args: dict[str, Any]) -> ToolOutput:
        path, rel = self.resolve(args["path"], must_exist=False)
        only = self._only_named(path, rel)
        if only is None:
            path, rel = self.resolve(args["path"])
            if path.is_dir():
                return self._list_dir(path, rel)
            return self._read_file(path, rel, args)
        asked, (path, rel) = rel, only
        output = self._read_file(path, rel, args)
        output.text = f"[{asked} does not exist: this is {rel}, the only file of that name]\n" + (
            output.text
        )
        output.data["asked"] = asked
        return output

    def _read_file(self, path: Path, rel: str, args: dict[str, Any]) -> ToolOutput:
        size = path.stat().st_size
        if size > READ_FILE_LIMIT:
            raise ToolError(f"{rel} is {size} bytes; too large to read. Use grep on it instead.")
        data = path.read_bytes()
        if looks_binary(data):
            raise ToolError(f"{rel} is a binary file ({size} bytes)")
        try:
            text = data.decode("utf-8")
            note = ""
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            note = " (not valid UTF-8: shown with replacement characters; edit will refuse it)"
        if text.startswith("\ufeff"):
            text = text[1:]
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        lines = [line[:-1] if line.endswith("\r") else line for line in lines]
        total = len(lines)
        digest = hashlib.sha256(data).hexdigest()
        if total == 0:
            return ToolOutput(f"{rel} is empty (0 lines)", data={"path": rel, "total_lines": 0})
        start = max(1, int(args.get("start") or 1))
        end = int(args.get("end") or total)
        requested = [start, end]
        widened = total <= READ_WHOLE_LINES and (start, min(max(end, start), total)) != (1, total)
        if total <= READ_WHOLE_LINES:
            # A short file costs little to show whole, and a model that sees only part of it
            # tends to edit the part it saw.
            start, end = 1, total
        if start > total:
            raise ToolError(f"start {start} is past the end of {rel} ({total} lines)")
        end = min(max(end, start), total)
        hashes = edit_format.line_hashes(lines)
        seen = self._seen.setdefault(rel, {})
        budget = self.max_output_chars - 200
        rows: list[str] = []
        used = 0
        last = start - 1
        for number in range(start, end + 1):
            row = edit_format.render(
                lines, hashes, number, number, max_line_chars=READ_LINE_CHARS, seen=seen
            )
            if rows and used + len(row) + 1 > budget:
                break
            rows.append(row)
            used += len(row) + 1
            last = number
        header = f"{rel} lines {start}-{last} of {total}{note}"
        if widened:
            header += " (the whole file: short files are shown whole)"
        footer = ""
        if last < end:
            footer = f"\n[more: read with start={last + 1}]"
        output = ToolOutput(
            f"{header}\n" + "\n".join(rows) + footer,
            data={
                "path": rel,
                "start": start,
                "end": last,
                "requested": requested,
                "total_lines": total,
                "sha256": digest,
            },
        )
        return output

    def _list_dir(self, path: Path, rel: str) -> ToolOutput:
        entries = []
        for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            name = child.name
            child_rel = name if rel == "." else f"{rel}/{name}"
            is_dir = child.is_dir()
            if name in {".git", ".stepswitch"} or name in SKIP_DIRS:
                continue
            if self.denied(child_rel, is_dir=is_dir):
                continue
            entries.append(f"{name}/" if is_dir else name)
        shown = entries[:200]
        more = f"\n[... {len(entries) - 200} more entries]" if len(entries) > 200 else ""
        text = f"{rel}/ ({len(entries)} entries)\n" + "\n".join(shown) + more
        return ToolOutput(text, data={"path": rel, "directory": True, "entries": len(entries)})

    # -- grep, repo_map, search --------------------------------------------------------------

    def _tool_grep(self, args: dict[str, Any]) -> ToolOutput:
        from stepswitch.context.search import SearchError, run_grep

        pattern = args["pattern"]
        if not pattern:
            raise ToolError("grep: `pattern` is empty")
        path = args.get("path")
        if path:
            _, path = self.resolve(path)
        try:
            report = run_grep(
                self.root,
                pattern,
                path=None if path == "." else path,
                glob=args.get("glob"),
                ignore_case=bool(args.get("ignore_case")),
                max_results=GREP_RESULTS,
            )
        except SearchError as exc:
            raise ToolError(f"grep: {exc}") from exc
        matches = [m for m in report.matches if not self.denied(m.path)]
        notes = []
        if report.literal:
            notes.append("not a valid regex, searched as plain text")
        if report.note:
            notes.append(report.note)
        if report.truncated:
            notes.append(f"first {GREP_RESULTS} shown; narrow with path or glob")
        suffix = f" ({'; '.join(notes)})" if notes else ""
        if not matches:
            text = f"no matches for {pattern!r}{suffix}"
        else:
            rows = [f"{m.path}:{m.line}: {m.text}" for m in matches]
            text = f"{len(matches)} matches{suffix}\n" + "\n".join(rows)
        data = {
            "pattern": pattern,
            "matches": len(matches),
            "engine": report.engine,
            "truncated": report.truncated,
            "files": sorted({m.path for m in matches}),
        }
        return ToolOutput(text, data=data)

    def _tool_repo_map(self, args: dict[str, Any]) -> ToolOutput:
        limit = max(100, self.max_output_chars // 4 - 50)
        max_tokens = min(int(args.get("max_tokens") or 1000), limit)
        result = self.repo_map().render(focus=args.get("focus") or [], max_tokens=max_tokens)
        how = "estimated" if result.estimated else "counted"
        header = (
            f"repo map: {result.shown} of {result.total} definitions in {len(result.files)} "
            f"files (~{result.tokens} tokens, {how})"
        )
        body = result.text or "(no definitions found in supported source files)"
        data = {
            "tokens": result.tokens,
            "estimated": result.estimated,
            "files": result.files,
            "shown": result.shown,
            "total": result.total,
        }
        return ToolOutput(f"{header}\n{body}", data=data)

    def _embedder(self) -> tuple[Any | None, str | None]:
        if self.embedder is None:
            return None, None
        local = getattr(self.embedder, "local", None)
        if self.offline and local is False:
            return None, "the embedder is remote and offline mode is on; keyword-only"
        return self.embedder, None

    def _tool_search(self, args: dict[str, Any]) -> ToolOutput:
        query = args["query"].strip()
        if not query:
            raise ToolError("search: `query` is empty")
        k = min(max(int(args.get("k") or 8), 1), 20)
        embedder, skipped = self._embedder()
        result = self.search_index().search(query, embedder=embedder, k=k)
        note = skipped or result.note
        rows = []
        for hit in result.hits:
            label = f"{hit.path}:{hit.start}-{hit.end}" + (f" {hit.symbol}" if hit.symbol else "")
            snippet = "\n".join("    " + row for row in hit.snippet.split("\n")[:4])
            rows.append(f"{label}\n{snippet}")
        header = f"{len(result.hits)} results ({result.mode}{'; ' + note if note else ''})"
        text = header + ("\n" + "\n".join(rows) if rows else "")
        data = {
            "query": query,
            "mode": result.mode,
            "hits": [f"{h.path}:{h.start}-{h.end}" for h in result.hits],
        }
        return ToolOutput(text, data=data)

    # -- lsp ---------------------------------------------------------------------------------

    def _tool_lsp(self, args: dict[str, Any]) -> ToolOutput:
        from stepswitch.context.lsp import LspError

        raw_action = str(args["action"]).strip().lower().replace("-", "_").replace(" ", "_")
        action = _LSP_ACTIONS.get(raw_action)
        if action is None:
            raise ToolError("lsp: `action` must be one of defs, refs, hover, diagnostics")
        symbol = (args.get("symbol") or "").strip() or None
        line = args.get("line")
        column = args.get("column")
        rel: str | None = None
        path: Path | None = None
        if args.get("path"):
            path, rel = self.resolve(args["path"])
            if path.is_dir():
                raise ToolError(f"lsp: {rel} is a directory; give a file")
        if symbol and line is None and action != "diagnostics":
            rel, line, column = self._locate_symbol(symbol, rel)
            path, rel = self.resolve(rel)
        if path is None or rel is None:
            raise ToolError("lsp: give `path` (and `line`/`column`), or a `symbol` to look up")
        manager = self.lsp_manager()
        try:
            if action == "diagnostics":
                found = manager.diagnostics(path)
                rows = [
                    f"{rel}:{d.line}:{d.column}: {d.severity}: {d.message}"
                    + (f" [{d.source}{' ' + d.code if d.code else ''}]" if d.source else "")
                    for d in found
                ]
                text = f"{len(found)} diagnostics in {rel}" + (
                    "\n" + "\n".join(rows) if rows else ""
                )
                return ToolOutput(text, data={"action": action, "path": rel, "count": len(found)})
            if line is None:
                raise ToolError(f"lsp {action}: give `line` and `column` (1-based), or `symbol`")
            if column is None:
                column = self._default_column(path, int(line), symbol)
            if action == "hover":
                text = manager.hover(path, int(line), int(column)) or "(no hover information)"
                return ToolOutput(text, data={"action": action, "path": rel, "line": line})
            locations = (
                manager.definition(path, int(line), int(column))
                if action == "defs"
                else manager.references(path, int(line), int(column))
            )
        except LspError as exc:
            raise ToolError(f"lsp: {exc}") from exc
        rows = [self._location_row(loc) for loc in locations[:LOCATION_RESULTS]]
        more = len(locations) - len(rows)
        text = f"{len(locations)} locations" + ("\n" + "\n".join(rows) if rows else "")
        if more > 0:
            text += f"\n[... {more} more]"
        data = {
            "action": action,
            "path": rel,
            "line": line,
            "column": column,
            "results": [f"{loc.path}:{loc.line}:{loc.column}" for loc in locations],
        }
        return ToolOutput(text, data=data)

    def _locate_symbol(self, symbol: str, rel: str | None) -> tuple[str, int, int]:
        name = re.split(r"[.:#]+", symbol)[-1]
        definitions = self.repo_map().definitions(name, path=rel)
        if definitions:
            first = definitions[0]
            return first.path, first.line, first.column
        if rel is not None:
            path = self.root / rel
            text = path.read_text(encoding="utf-8", errors="replace").split("\n")
            pattern = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
            for number, line in enumerate(text, start=1):
                match = pattern.search(line)
                if match:
                    return rel, number, match.start() + 1
        where = f" in {rel}" if rel else ""
        raise ToolError(f"lsp: symbol {symbol!r} not found{where}; give `path` and `line` instead")

    def _default_column(self, path: Path, line: int, symbol: str | None) -> int:
        rows = path.read_text(encoding="utf-8", errors="replace").split("\n")
        text = rows[line - 1] if 0 < line <= len(rows) else ""
        if symbol:
            index = text.find(re.split(r"[.:#]+", symbol)[-1])
            if index >= 0:
                return index + 1
        return len(text) - len(text.lstrip()) + 1

    def _location_row(self, loc: Any) -> str:
        if not loc.inside:
            return f"{loc.path}:{loc.line}:{loc.column} (outside the repository)"
        text = ""
        if not self.denied(loc.path):
            try:
                rows = (self.root / loc.path).read_text(encoding="utf-8", errors="replace")
                lines = rows.split("\n")
                if 0 < loc.line <= len(lines):
                    text = lines[loc.line - 1].strip()[:200]
            except OSError:
                text = ""
        return f"{loc.path}:{loc.line}:{loc.column}: {text}"

    # -- edit --------------------------------------------------------------------------------

    def _tool_edit(self, args: dict[str, Any]) -> ToolOutput:
        path, rel = self.resolve(args["path"], must_exist=False)
        asked = None
        if args.get("edits") is not None and not args.get("create"):
            # Anchored edits change an existing file; a missing path stands for the one file
            # of its name, as for read. The anchors are checked against that file.
            only = self._only_named(path, rel)
            if only is not None:
                asked, (path, rel) = rel, only
        if path.is_dir():
            raise ToolError(f"{rel} is a directory")
        result = edit_format.edit_file(
            path,
            display=rel,
            edits=args.get("edits"),
            text=args.get("text"),
            create=bool(args.get("create")),
            seen=self._seen.setdefault(rel, {}),
        )
        if self._repo_map is not None:
            self._repo_map.invalidate(rel)
        data = {
            "path": rel,
            "created": result.created,
            "changed": result.changed,
            "added": result.added,
            "removed": result.removed,
            "total_lines": result.total_lines,
            "diff": result.diff,
        }
        changed = [rel] if result.changed else []
        if result.created:
            text = f"created {rel} ({result.total_lines} lines)\n{result.preview}"
        elif not result.changed:
            text = f"no change: {rel} already has that content"
        else:
            diff = bound(result.diff, max(1000, self.max_output_chars // 2))
            text = (
                f"edited {rel}: +{result.added} -{result.removed} "
                f"(now {result.total_lines} lines)\n{diff}\n"
                f"updated lines with fresh anchors:\n{result.preview}"
            )
        if asked is not None:
            data["asked"] = asked
            note = f"[{asked} does not exist: the edit went to {rel}, the only file of that name]"
            text = f"{note}\n{text}"
        return ToolOutput(text, data=data, changed_files=changed)

    # -- run and test ------------------------------------------------------------------------

    def _child_env(self) -> dict[str, str]:
        return proc.minimal_env(self.env, offline=self.offline)

    def _program(self, token: str, env: Mapping[str, str]) -> str:
        """An absolute program path: relative paths from root, bare names from PATH only."""
        token = token.strip().strip('"')
        if Path(token).is_absolute():
            if Path(token).is_file():
                return token
            raise ToolError(f"program not found: {token}")
        if "/" in token or "\\" in token:
            candidate = (self.root / token).resolve()
            if not candidate.is_relative_to(self.root):
                raise ToolError(f"{token} is outside the repository")
            found = proc.which(candidate.name, str(candidate.parent))
            if found is None:
                raise ToolError(f"program not found: {token}")
            return found
        found = proc.which(token, env.get("PATH", ""))
        if found is None:
            raise ToolError(f"`{token}` was not found on PATH (no shell builtins: use programs)")
        return found

    def _fresh_bytecode(self) -> None:
        """Drop cached bytecode older than its source, as the gates do: an edit that keeps a
        file's size within the second of the last compile would otherwise run the old code."""
        from stepswitch.verify.commands import purge_stale_bytecode

        purge_stale_bytecode(self.root)

    def _tool_run(self, args: dict[str, Any]) -> ToolOutput:
        if not self.allow_run:
            raise ToolError("run is disabled for this task; use test, read and grep instead")
        command = args["command"]
        if isinstance(command, list):
            command = (
                subprocess.list2cmdline(command) if sys.platform == "win32" else shlex.join(command)
            )
        command = command.strip()
        if not command:
            raise ToolError("run: `command` is empty")
        self._fresh_bytecode()
        requested = float(args.get("timeout") or self.run_timeout)
        timeout = min(max(requested, 1.0), self.run_timeout)
        env = self._child_env()
        if sys.platform == "win32":
            match = re.match(r'\s*("([^"]+)"|(\S+))(.*)$', command, re.DOTALL)
            if match is None:
                raise ToolError("run: could not read the command")
            program = self._program(match.group(2) or match.group(3), env)
            rest = match.group(4)
            if program.lower().endswith((".bat", ".cmd")) and re.search(r"[&|<>]", rest):
                # Windows runs batch files through the command interpreter, which would treat
                # these characters as shell syntax; keep the no-shell promise.
                raise ToolError("run: no shell, so &, |, < and > are not available")
            # The resolved absolute path goes first on the command line, so Windows never
            # searches the current directory for the program; the rest is passed verbatim.
            invocation: str | list[str] = f'"{program}"{rest}'
        else:
            try:
                argv = shlex.split(command)
            except ValueError as exc:
                raise ToolError(f"run: cannot split the command ({exc})") from exc
            argv[0] = self._program(argv[0], env)
            invocation = argv
        result = proc.run_capture(invocation, cwd=self.root, env=env, timeout=timeout)
        if result.error is not None:
            raise ToolError(f"run: could not start the command: {result.error}")
        status = f"exit {result.exit_code}"
        if result.timed_out:
            status = f"timed out after {timeout:g} s (process tree stopped)"
        output = result.output.rstrip() or "(no output)"
        text = f"$ {command}\n{status} in {result.duration_s:.1f} s\n{output}"
        data = {
            "command": command,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_s": round(result.duration_s, 3),
        }
        return ToolOutput(text, data=data)

    def _tool_test(self, args: dict[str, Any]) -> ToolOutput:
        if not self.test_command:
            raise ToolError("no test command is configured for this task; use run instead")
        selector = (args.get("selector") or "").strip()
        try:
            extra = shlex.split(selector.replace("\\", "/")) if selector else []
        except ValueError as exc:
            raise ToolError(f"test: cannot split the selector ({exc})") from exc
        extra, notes = self._test_paths(extra)
        env = self._child_env()
        argv = [self._program(self.test_command[0], env), *self.test_command[1:], *extra]
        self._fresh_bytecode()
        result = proc.run_capture(argv, cwd=self.root, env=env, timeout=self.test_timeout)
        if result.error is not None:
            raise ToolError(f"test: could not start {self.test_command[0]}: {result.error}")
        summary = summarize_tests(result.output)
        counts = summary.counts()
        shown = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
        status = "passed" if result.exit_code == 0 and not result.timed_out else "failed"
        if result.timed_out:
            status = f"timed out after {self.test_timeout:g} s"
        head = f"tests {status} (exit {result.exit_code}, {result.duration_s:.1f} s)"
        lines = [f"note: {note}" for note in notes] + [head + (f": {shown}" if shown else "")]
        if summary.failing:
            lines.append("failing:")
            lines += [f"  {test_id}" for test_id in summary.failing[:30]]
        if summary.details:
            lines.append("details:")
            lines += [f"  {row}" for row in summary.details[:40]]
        if status != "passed" and not summary.failing and not summary.details:
            tail = "\n".join(result.output.rstrip().splitlines()[-40:])
            lines.append("output (last lines):\n" + tail)
        data = {
            "argv": [Path(argv[0]).name, *argv[1:]],
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "ok": status == "passed",
            "runner": summary.runner,
            "counts": counts,
            "failing": summary.failing,
            "duration_s": round(result.duration_s, 3),
        }
        return ToolOutput("\n".join(lines), data=data)

    def _test_paths(self, extra: list[str]) -> tuple[list[str], list[str]]:
        """The selector with test files as paths from the root: a `.py` file that does not
        exist as given (`test_calc.py` for `tests/test_calc.py`) becomes the one file of the
        repository with that name, keeping any `::test` part. Small models often drop the
        directory. Returns the arguments and a note for each rewrite or ambiguous name."""
        resolved: list[str] = []
        notes: list[str] = []
        by_name: dict[str, list[str]] | None = None
        for i, token in enumerate(extra):
            path, sep, node = token.partition("::")
            option_value = i > 0 and extra[i - 1] in {"-k", "-m"}
            if option_value or token.startswith("-") or not path.endswith(".py"):
                resolved.append(token)
                continue
            if (self.root / path).exists():
                resolved.append(token)
                continue
            if by_name is None:
                by_name = {}
                for rel, _ in iter_files(
                    self.root, matcher=IgnoreMatcher(self.root), max_bytes=None
                ):
                    by_name.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
            found = by_name.get(Path(path).name, [])
            if len(found) == 1:
                resolved.append(found[0] + sep + node)
                notes.append(f"{path} is {found[0]} (paths are from the repository root)")
                continue
            resolved.append(token)
            if found:
                shown = ", ".join(found[:10])
                notes.append(f"{path} is not a path from the repository root; candidates: {shown}")
        return resolved, notes

    # -- finish ------------------------------------------------------------------------------

    def _tool_finish(self, args: dict[str, Any]) -> ToolOutput:
        summary = str(args.get("summary") or "").strip()
        return ToolOutput("finished", data={"finish": True, "summary": summary})
