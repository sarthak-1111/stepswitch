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
from stepswitch.context import patch as patch_format
from stepswitch.context import proc
from stepswitch.context.deny import DenyList
from stepswitch.context.repomap import language_for
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
        "Change files: `edits` by read anchors or by `old`/`new` text, a `patch`, or `text` "
        "for a new or small file.",
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
                        "old": {"type": "string", "description": "exact lines to change"},
                        "new": {"type": "string", "description": "the lines that replace old"},
                    },
                },
            },
            "text": _STR,
            "create": {"type": "boolean"},
            "patch": {"type": "string", "description": "*** Begin Patch ... *** End Patch"},
        },
        (),
    ),
    "run": (
        "Run a command in the repo root. No shell: no pipes, redirects or &&.",
        {"command": _STR, "timeout": {"type": "number"}},
        ("command",),
    ),
    "test": (
        "Run tests: a file, test id or -k filter (none: a large suite runs the impacted"
        " tests). Summarizes failures.",
        {"selector": _STR},
        (),
    ),
    "search": (
        "Find code related to a description (for an exact name or text, use grep).",
        {
            "query": _STR,
            "path": {"type": "string", "description": "file or directory to search in"},
            "k": _INT,
        },
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
    "apply_patch": "edit",
    "applypatch": "edit",
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
        "edits": ("any", ("changes", "ops", "operations", "edit")),
        "patch": ("str", ("diff", "patch_text", "unified_diff")),
        # Version 2 (ADR 0038): one edit given at the top level, by old text or by anchors.
        "old": ("str", ("old_text", "old_string", "old_str", "search", "find", "original")),
        "new": ("any", ("new_string", "new_str", "replace", "replacement")),
        "op": ("str", ("action", "operation")),
        "start": ("any", ("anchor", "from", "line", "at", "start_line", "line_start")),
        "end": ("any", ("to", "until", "end_line", "line_end")),
        "after": ("any", ()),
        "before": ("any", ()),
        "text": ("str", ("content", "contents", "new_text", "code", "file_text")),
        "create": ("bool", ("new_file", "create_file")),
    },
    "run": {
        "command": ("command", ("cmd", "commandline", "command_line", "args", "argv", "shell")),
        "timeout": ("float", ("timeout_s", "seconds", "time_limit")),
    },
    "test": {"selector": ("str", ("test", "tests", "target", "path", "file", "filter", "k"))},
    "search": {
        "query": ("str", ("q", "text", "description", "pattern", "search")),
        "path": ("str", ("dir", "directory", *_PATH_ALIASES)),
        "k": ("int", ("limit", "top_k", "n", "count", "max_results")),
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
_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in [\d.]+s$")
_UNITTEST_RESULT = re.compile(r"^(OK|FAILED)(?: \((.*)\))?$")
_UNITTEST_HEADER = re.compile(r"^(?:FAIL|ERROR|UNEXPECTED SUCCESS): (\S+) \(([^)]*)\)")
_UNITTEST_RULE = re.compile(r"^(?:=+|-+)$")
LARGE_SUITE = 200  # test files; above it, `test` without a selector runs the impacted tests
EXACT_MATCHES = 20  # lines that name a searched name, shown above the search results
# Lines read to choose them: source files come first, then tests, then the rest (docs, change
# logs, data). In file order, sphinx's change logs filled all 20 lines for a name (ADR 0042).
EXACT_SCAN = 1000
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A search for a definition: `def name`, `class Name(Base)`, `fn name<T>`, ...
_DEFINITION_RE = re.compile(
    r"^(?:async\s+)?(def|class|function|fn|func|struct|interface|trait)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:$|[(:<\[{])"
)
_CODE_RE = re.compile(r"[()\[\]{}=:'\"]|\w\.\w|\w_\w")  # a query written as code, not prose
_GREP_META = re.compile(r"[\\.^$|?*+()\[\]{}]")  # escaped, a fragment is matched as written
TEST_DIRS = ("tests/", "test/")


def _match_rank(path: str) -> int:
    """0 for a source file, 1 for a test file, 2 for anything else."""
    if language_for(path) is None:
        return 2
    parts = path.split("/")
    name = parts[-1]
    stem = name.rsplit(".", 1)[0]
    in_tests = any(part in {"test", "tests"} for part in parts[:-1])
    test = in_tests or name.startswith("test_") or stem.endswith(("_test", ".test", ".spec"))
    return 1 if test else 0


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

    def unnamed(self) -> int:
        """Failures the summary counted but could not name (all of them for an unknown runner)."""
        if self.runner == "unknown":
            return max(len(self.failing), 1)
        return max((self.failed or 0) + (self.errors or 0) - len(self.failing), 0)


def failing_tests(output: str) -> tuple[list[str], int]:
    """(failing test ids, failures that could not be named) of a test run's output."""
    summary = summarize_tests(output)
    return summary.failing, summary.unnamed()


def _unittest_summary(lines: Sequence[str], summary: TestSummary) -> None:
    """unittest's report (Django's test runner prints it too): the counts after `Ran N tests`,
    each failing test as a label the runner accepts back (`module.Class.test`), and the last
    line of its traceback."""
    ran = None
    for i, line in enumerate(lines):
        found = _UNITTEST_RAN.match(line.strip())
        if found is None:
            continue
        result = next(
            (m for rest in lines[i + 1 :] if (m := _UNITTEST_RESULT.match(rest.strip()))), None
        )
        if result is not None:
            ran = (int(found.group(1)), result.group(2) or "")
    if ran is None:
        return
    total, detail = ran
    counts = {key.strip(): int(value) for key, value in re.findall(r"([a-z ]+)=(\d+)", detail)}
    summary.runner = "unittest"
    summary.failed = counts.get("failures", 0)
    summary.errors = counts.get("errors", 0)
    summary.skipped = counts.get("skipped", 0)
    others = counts.get("expected failures", 0) + counts.get("unexpected successes", 0)
    summary.passed = max(total - summary.failed - summary.errors - summary.skipped - others, 0)
    for i, line in enumerate(lines):
        header = _UNITTEST_HEADER.match(line)
        if header is None:
            continue
        name, where = header.groups()
        test_id = where if where.endswith("." + name) else f"{where}.{name}"
        if test_id not in summary.failing:
            summary.failing.append(test_id)
        body = []
        for rest in lines[i + 2 :]:  # past the rule under the header
            if _UNITTEST_RULE.match(rest.strip()):
                break
            if rest.strip():
                body.append(rest.strip())
        if body:
            summary.details.append(f"{test_id}: {body[-1]}")


def summarize_tests(output: str) -> TestSummary:
    """Counts, failing test ids and the decisive assertion lines from a test run's output
    (pytest and unittest in detail; go test, cargo test and jest-style summaries in outline)."""
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
    if summary.runner == "unknown":
        _unittest_summary(lines, summary)
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
        base: str | None = None,
        base_env: Callable[[Path], Mapping[str, str]] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.test_command = list(test_command) if test_command else None
        # The task's base commit: test failures are compared with it (`stepswitch.verify.
        # baseline`), and a large suite runs only what the change reaches without a selector.
        self.base = base
        self.base_env = base_env
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
            elif tool == "edit" and patch_format.looks_like_patch(text):
                raw = {"patch": text}  # a patch sent as the whole argument
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
        scope = self.resolve(args["path"])[1] if args.get("path") else ""
        embedder, skipped = self._embedder()
        result = self.search_index().search(query, embedder=embedder, k=k, path=scope or None)
        note = skipped or result.note
        rows = []
        for hit in result.hits:
            label = f"{hit.path}:{hit.start}-{hit.end}" + (f" {hit.symbol}" if hit.symbol else "")
            snippet = "\n".join("    " + row for row in hit.snippet.split("\n")[:4])
            rows.append(f"{label}\n{snippet}")
        header = f"{len(result.hits)} results ({result.mode}{'; ' + note if note else ''})"
        text = header + ("\n" + "\n".join(rows) if rows else "")
        data: dict[str, Any] = {
            "query": query,
            "mode": result.mode,
            "hits": [f"{h.path}:{h.start}-{h.end}" for h in result.hits],
        }
        if scope:
            data["path"] = scope
        exact = self._exact_matches(query, scope)
        if exact is not None:
            label, matches, found = exact
            data["exact"] = [f"{m.path}:{m.line}" for m in matches]
            shown = "\n".join(f"{m.path}:{m.line}: {m.text.strip()}" for m in matches)
            count = str(len(matches))
            if found > len(matches):
                more = "+" if found >= EXACT_SCAN else ""
                count = f"{len(matches)} of {found}{more}, source files first"
            text = f"{label} ({count}):\n{shown}\n\n{text}"
        return ToolOutput(text, data=data)

    def _exact_matches(self, query: str, scope: str) -> tuple[str, list[Any], int] | None:
        """For a query that is code rather than a description, the lines that hold it: models
        often search where grep would serve them. `def name` or `class name` gives the
        definitions; a fragment (`call_command('flush'`) its text as written; a name, or the name
        a fragment calls (`execute_sql_flush(self, using`, `ops.flush`), every line naming it."""
        text = query.strip().strip("`")
        attempts: list[tuple[str, str]] = []
        defined = _DEFINITION_RE.match(text)
        if defined:
            keyword, name = defined.groups()
            attempts.append((f"definitions of `{name}`", rf"\b{keyword}\s+{name}\b"))
        else:
            name = text.split("(", 1)[0].strip().strip("'\"").rstrip(":;, ").rsplit(".", 1)[-1]
            bare = text.strip("'\"").rstrip("():;, ")  # `add(` says no more than `add`
            if not _NAME_RE.fullmatch(bare) and _CODE_RE.search(text) and len(text) <= 80:
                attempts.append((f"exact matches of `{text}`", _GREP_META.sub(r"\\\g<0>", text)))
        if len(name) >= 3 and _NAME_RE.fullmatch(name):
            attempts.append((f"exact matches of `{name}`", rf"\b{name}\b"))
        for label, pattern in attempts:
            matches, found = self._grep_lines(pattern, scope)
            if matches:
                return label, matches, found
        return None

    def _grep_lines(self, pattern: str, scope: str) -> tuple[list[Any], int]:
        """The first `EXACT_MATCHES` lines that match, source files first, and how many lines
        matched (at most `EXACT_SCAN`)."""
        from stepswitch.context.search import SearchError, run_grep

        try:
            report = run_grep(
                self.root,
                pattern,
                path=scope if scope not in ("", ".") else None,
                max_results=EXACT_SCAN,
            )
        except SearchError:
            return [], 0
        matches = [m for m in report.matches if not self.denied(m.path)]
        matches.sort(key=lambda match: _match_rank(match.path))
        return matches[:EXACT_MATCHES], len(matches)

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
        if args.get("patch") is not None:
            return self._tool_patch(str(args["patch"]))
        new = args.pop("new", None)
        if isinstance(new, bool):
            args["create"] = args.get("create") or new  # `new: true` asks for a new file
            new = None
        old = args.pop("old", None)
        single = {key: args.pop(key, None) for key in ("op", "start", "end", "after", "before")}
        single = {key: value for key, value in single.items() if value is not None}
        if old is not None and args.get("edits") is None:
            # Version 2 (ADR 0038): one excerpt edit given at the top level.
            replacement = new if new is not None else args.pop("text", None)
            args["edits"] = [{"old": old, "new": replacement, **single}]
            new = None
        elif single and args.get("edits") is None and not args.get("create"):
            # Version 2: one anchored edit given at the top level.
            given = args.pop("text", None)
            args["edits"] = [{**single, "text": given if given is not None else new}]
            new = None
        found = None
        if args.get("path") is None and args.get("edits") is not None:
            found = self._path_from_edits(args["edits"])
            args["path"] = found
        if args.get("path") is None:
            raise ToolError(f"edit: missing `path`. Example: {_EXAMPLES['edit']}")
        path, rel = self.resolve(args["path"], must_exist=False)
        if new is not None and args.get("edits") is None and args.get("text") is None:
            if path.exists():
                # Taking `new` alone as the whole file could wipe out the rest of it.
                raise ToolError(
                    f"edit: `new` needs `old`, the exact lines of {rel} it replaces. To rewrite "
                    "the whole file, send it as `text`."
                )
            args["text"] = new if isinstance(new, str) else edit_format.text_lines(new)
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
        if found is not None:
            data["inferred_path"] = True
            text = f"[no `path` was given: the edit went to {rel}, the file it matches]\n{text}"
        return ToolOutput(text, data=data, changed_files=changed)

    def _path_from_edits(self, edits: Any) -> str | None:
        """For edits sent without `path`: the one file they fit among those read so far. Full
        anchors fit a file whose read output showed all of them; old text, a file that holds
        it now. A partial anchor fits no file: it names no file by itself."""
        try:
            ops = edit_format.parse_edits(edits)
        except edit_format.EditError:
            return None
        anchors = {(a.line, a.hash) for op in ops if op.old is None for a in (op.start, op.end)}
        if any(not (line and digest) for line, digest in anchors):
            return None
        excerpts = ["\n".join(op.old) for op in ops if op.old is not None]
        fits: list[str] = []
        for rel, seen in self._seen.items():
            if not seen or not anchors <= seen.keys():
                continue
            if excerpts:
                try:
                    current = (self.root / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
                except (OSError, UnicodeDecodeError):
                    continue
                if not all(excerpt in current for excerpt in excerpts):
                    continue
            fits.append(rel)
        return fits[0] if len(fits) == 1 else None

    def _tool_patch(self, text: str) -> ToolOutput:
        """A patch in the `*** Begin Patch` format or a unified diff (ADR 0038): every file is
        checked first, and a refused patch changes nothing."""
        changes = patch_format.parse(text)
        notes: list[str] = []

        def resolve(raw: str, existing: bool) -> tuple[Path, str]:
            path, rel = self.resolve(raw, must_exist=False)
            only = self._only_named(path, rel) if existing else None
            if only is not None:
                notes.append(
                    f"[{rel} does not exist: the patch went to {only[1]}, the only "
                    "file of that name]"
                )
                path, rel = only
            if path.is_dir():
                raise ToolError(f"{rel} is a directory")
            return path, rel

        def seen_for(rel: str) -> edit_format.Seen:
            return self._seen.setdefault(rel, {})

        results = patch_format.write(patch_format.plan(changes, resolve, notes), seen_for)
        rows: list[str] = list(notes)
        changed: list[str] = []
        budget = max(1000, self.max_output_chars // (2 * max(1, len(results))))
        for result in results:
            if self._repo_map is not None:
                self._repo_map.invalidate(result.path)
            if result.created:
                rows.append(f"created {result.path} ({result.total_lines} lines)\n{result.preview}")
            elif not result.changed:
                rows.append(f"no change: {result.path} already has that content")
            else:
                rows.append(
                    f"edited {result.path}: +{result.added} -{result.removed} "
                    f"(now {result.total_lines} lines)\n{bound(result.diff, budget)}\n"
                    f"updated lines with fresh anchors:\n{result.preview}"
                )
            if result.changed:
                changed.append(result.path)
        files = [
            {
                "path": r.path,
                "created": r.created,
                "changed": r.changed,
                "added": r.added,
                "removed": r.removed,
                "total_lines": r.total_lines,
            }
            for r in results
        ]
        data: dict[str, Any] = {
            "patch": True,
            "files": files,
            "changed": bool(changed),
            "diff": "\n".join(r.diff for r in results if r.changed),
        }
        if len(files) == 1:
            data.update(files[0])  # the shape of a one-file edit
        return ToolOutput("\n\n".join(rows), data=data, changed_files=changed)

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
        implicit = not extra
        if implicit:
            extra, notes = self._impacted_selection()
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
        # Failures that also happen at the base commit are told apart, never passed over: a task
        # is often to fix one of them. While no file differs from the base commit, every failure
        # is one of them, named or not: a dev run that saw 126 failures before its first edit,
        # two of them unnamed, set out to fix the test setup instead of the issue (ADR 0042).
        unchanged = status == "failed" and bool(summary.failing) and self._unchanged()
        new: list[str]
        old: list[str]
        if unchanged:
            new, old = [], list(summary.failing)
        else:
            new, old = self._preexisting(status, summary, argv, result.duration_s)
        head = f"tests {status} (exit {result.exit_code}, {result.duration_s:.1f} s)"
        lines = [*notes, head + (f": {shown}" if shown else "")]
        if unchanged:
            lines.append(
                "no file differs from the base commit: every failure here happens before any"
                " change of yours"
            )
        elif old and not new:
            lines.append("every failing test also fails at the base commit, before your change")
        if new:
            lines.append("failing:" if not old else "failing since your change:")
            lines += [f"  {test_id}" for test_id in new[:30]]
        if old:
            lines.append("failing at the base commit too, before your change:")
            lines += [f"  {test_id}" for test_id in old[:30]]
        if summary.details:
            lines.append("details:")
            lines += [f"  {row}" for row in summary.details[:40]]
        if status == "failed" and not any(counts.values()) and not summary.failing:
            # The runner stopped before running a test (a collection or setup error).
            hint = "no test ran: the test runner stopped before running any test"
            if implicit and extra:
                hint += (
                    "; the chosen files may hold tests that cannot run here, so give a selector"
                    " (one test file or test id) to run the tests you need"
                )
            lines.append(hint)
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
            "failing": new,
            "duration_s": round(result.duration_s, 3),
        }
        if old:
            data["preexisting"] = old
        return ToolOutput("\n".join(lines), data=data)

    def _unchanged(self) -> bool:
        """Whether the worktree still matches the base commit: no file changed or added."""
        if self.base is None:
            return False
        from stepswitch.verify import commands

        return commands.changed_files(self.root, self.base) == []

    def _preexisting(
        self, status: str, summary: TestSummary, argv: Sequence[str], seconds: float
    ) -> tuple[list[str], list[str]]:
        """(new failures, failures the base commit has too) of a failed run whose failures are
        all named (`stepswitch.verify.baseline`)."""
        from stepswitch.verify import baseline

        if (
            status != "failed"
            or not summary.failing
            or summary.unnamed()
            or seconds > baseline.MAX_SECONDS
            or self.base_env is None
        ):
            return list(summary.failing), []
        found = baseline.for_worktree(self.root, self.base)
        if found is None:
            return list(summary.failing), []
        offline = self.offline
        base_env = self.base_env
        before = found.failing(
            argv,
            env_for=lambda path: proc.minimal_env(base_env(path), offline=offline),
            summarize=failing_tests,
            timeout=self.test_timeout,
        )
        return baseline.compare(summary.failing, before)

    def _impacted_selection(self) -> tuple[list[str], list[str]]:
        """Without a selector, a suite of more than LARGE_SUITE test files runs only the tests
        that import the changed modules; a smaller suite runs whole."""
        from stepswitch.verify import commands, impact

        if self.base is None:
            return [], []
        files = impact.python_files(self.root)
        tests = [rel for rel in files if impact.is_test_file(rel) or rel.startswith(TEST_DIRS)]
        if len(tests) <= LARGE_SUITE:
            return [], []
        changed = commands.changed_files(self.root, self.base)
        selection = impact.select_tests(self.root, changed, python="", has_testmon=False)
        if selection.mode == "mapped":
            shown = ", ".join(selection.args[:8]) + (" ..." if len(selection.args) > 8 else "")
            note = f"no selector: running the {len(selection.args)} test file(s) that import"
            return selection.args, [f"{note} your changes ({shown})"]
        raise ToolError(
            f"test: this suite has {len(tests)} test files and runs for a long time; give a"
            f" selector (a test file, a test id or a -k filter). Without one, only the tests"
            f" that import your changes run, and here: {selection.reason}."
        )

    def _test_paths(self, extra: list[str]) -> tuple[list[str], list[str]]:
        """The selector's test files as paths from the root. A `.py` file that does not exist as
        given (`test_calc.py` for `tests/test_calc.py`) becomes the one file of the repository
        with that name, keeping any `::test` part, as `read` and `edit` do (`_only_named`).
        Returns the arguments and a note for each rewrite or ambiguous name."""
        resolved: list[str] = []
        notes: list[str] = []
        for i, token in enumerate(extra):
            path, sep, node = token.partition("::")
            option_value = i > 0 and extra[i - 1] in {"-k", "-m"}
            if option_value or token.startswith("-") or not path.endswith(".py"):
                resolved.append(token)
                continue
            if (self.root / path).exists():
                resolved.append(token)
                continue
            name = path.rsplit("/", 1)[-1]
            found = self._named(name, 2)
            if len(found) == 1:
                resolved.append(found[0] + sep + node)
                notes.append(
                    f"[{path} does not exist: the tests ran {found[0]}, the only file of that name]"
                )
                continue
            resolved.append(token)
            if found:
                notes.append(f"[{path} does not exist.{self._suggest(name)}]")
        return resolved, notes

    # -- finish ------------------------------------------------------------------------------

    def _tool_finish(self, args: dict[str, Any]) -> ToolOutput:
        summary = str(args.get("summary") or "").strip()
        return ToolOutput("finished", data={"finish": True, "summary": summary})
