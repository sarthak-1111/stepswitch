"""Verification gates (SPEC §8.1): run cheapest first and stop at the first hard failure.

Each track lists its gates in order (`[tracks.<track>].gates`, defaults in PROJECT_TEMPLATE).
The runner executes them in that order. When a gate fails or errors, the remaining gates are
reported as skipped ("not run: <gate> failed"). The verdict carries a distilled report for the
worker, which names the failing gate, the command and the decisive lines.

A gate whose tool is not configured or not installed in the project's environment is
skipped, never failed. Tools run from the project's interpreter (see `commands`). Each
command can be overridden per track in `[tracks.<track>.commands]` with lists of argv strings:

* `{python}` is the project interpreter and `{repo}` is the repository.
* An argument that is exactly `{files}` (format, lint, typecheck) becomes the changed Python
  files; the gate is skipped when there are none.
* An argument that is exactly `{tests}` (tests) becomes the impacted test selection. Without
  it, the selection is appended to the command.

Gates:

* format, lint: `ruff format --check` and `ruff check` on the changed Python files, when the
  project configures ruff.
* typecheck: mypy, when the project configures it. It checks the configured files or
  packages, else the changed files.
* protected: protected values in science.toml against the base revision (`science`).
* impacted_tests: the tests selected for the changed files (`impact`). When the selection is
  the whole suite and full_tests follows, this gate defers to full_tests.
* golden: golden cases with per-quantity tolerances (`science`). It first pushes paired
  scripts into their notebooks.
* full_tests: the whole suite, then the task's acceptance commands. Acceptance commands are
  command lines the user wrote; they run through the platform shell.
* review: the cross-family review is an agent step (SPEC §8.5), so it is reported as skipped.
"""

from __future__ import annotations

import configparser
import math
import re
import time
import tomllib
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from stepswitch import config as config_module
from stepswitch.verify import commands, impact

if TYPE_CHECKING:
    from stepswitch.config import Config
    from stepswitch.verify.science import ScienceConfig

GateStatus = Literal["passed", "failed", "skipped", "error"]
GATE_NAMES = (
    "format",
    "lint",
    "typecheck",
    "protected",
    "impacted_tests",
    "golden",
    "full_tests",
    "review",
)
FAILING = frozenset({"failed", "error"})
REPORT_LIMIT = 2000
SUMMARY_LIMIT = 300

_PYTEST_KEEP = re.compile(
    r"^(FAILED|ERROR)\b|^E\s|^_{3,} .+ _{3,}$|^\S+\.py:\d+: |"
    r"\b\d+ (failed|passed|errors?)\b.* in [\d.]+s|^no tests ran|"
    r"^(ImportError|ModuleNotFoundError|SyntaxError)|^ERROR: |^Interrupted:"
)
# `ruff format --check` printed "Would reformat: <path>" before ruff 0.16; since then it prints
# diagnostics ("unformatted: ..." or "invalid-syntax: ..." followed by " --> <path>:<l>:<c>")
# in the project's output format. Both are kept as decisive lines, and so is the
# "would reformat <path>" of formatters run through a command override.
_RUFF_FORMAT_KEEP = re.compile(
    r"^(?i:would reformat)|^(?:unformatted|invalid-syntax|error|warning):|^\s*--> |"
    r":\d+:\d+: |\bfiles? would be reformatted"
)
_WOULD_REFORMAT = re.compile(r"^(?i:would reformat):? (?P<path>.+?)\s*$")
_UNFORMATTED_CONCISE = re.compile(r"^(?P<path>\S.*?):\d+:\d+: unformatted\b")
_LOCATION = re.compile(r"^\s*--> (?P<path>.+?):\d+:\d+\s*$")
_GROUPED_FILE = re.compile(r"^(?P<path>\S.*):\s*$")
_GROUPED_UNFORMATTED = re.compile(r"^\s+\d+:\d+ unformatted\b")
_RUFF_CHECK_KEEP = re.compile(r"^\S.*:\d+:\d+: |^Found \d+ error|^error")
_MYPY_KEEP = re.compile(r": error:|^Found \d+ error|^error:")
_PYTEST_SUMMARY = re.compile(
    r"(\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|deselected)[^=\n]* in [\d.]+s)"
)


class GateConfigError(ValueError):
    """A gate is configured in a way the runner cannot use."""


@dataclass
class GateResult:
    """One gate's outcome. `report` is the distilled failure text (empty when passed)."""

    name: str
    status: GateStatus
    seconds: float = 0.0
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    report: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = jsonable(
            {
                "name": self.name,
                "status": self.status,
                "seconds": round(self.seconds, 3),
                "summary": self.summary,
                "details": self.details,
                "report": self.report,
            }
        )
        return data


@dataclass
class Verdict:
    """The outcome of a gate run. Skipped gates do not fail a verdict."""

    passed: bool
    results: list[GateResult]
    failed_gate: str | None
    report: str

    def result(self, name: str) -> GateResult | None:
        return next((r for r in self.results if r.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failed_gate": self.failed_gate,
            "report": self.report,
            "results": [r.to_dict() for r in self.results],
        }


def jsonable(value: Any) -> Any:
    """`value` with JSON-safe leaves: non-finite floats as strings, numpy values as Python."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(v) for v in value]
    if type(value).__module__ == "numpy" and hasattr(value, "tolist"):
        return jsonable(value.tolist())
    if isinstance(value, bool) or value is None or isinstance(value, int | str):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if isinstance(value, Path):
        return str(value)
    return str(value)


# --------------------------------------------------------------------------------------------
# Track configuration
# --------------------------------------------------------------------------------------------


@cache
def _template_gates() -> dict[str, tuple[str, ...]]:
    data = tomllib.loads(config_module.PROJECT_TEMPLATE)
    return {
        name: tuple(table.get("gates", ()))
        for name, table in data.get("tracks", {}).items()
        if isinstance(table, dict)
    }


def gates_for(track: str, config: Config | None) -> list[str]:
    """The track's gates in order: `[tracks.<track>].gates`, else PROJECT_TEMPLATE's defaults."""
    configured = commands.track_settings(config, track).get("gates")
    if configured is not None:
        if not isinstance(configured, list) or not all(isinstance(g, str) for g in configured):
            raise GateConfigError(f"[tracks.{track}].gates must be a list of gate names")
        return list(configured)
    defaults = _template_gates()
    if track not in defaults:
        raise GateConfigError(f"unknown track {track!r}: configure [tracks.{track}].gates")
    return list(defaults[track])


def plan(track_gates: Sequence[str], only: Sequence[str] | None) -> list[str]:
    """Gates to run: the track's order, restricted to `only`; extra names follow in
    GATE_NAMES order, unknown names last."""
    ordered = list(dict.fromkeys(track_gates))
    if only is None:
        return ordered
    wanted = list(dict.fromkeys(only))
    planned = [g for g in ordered if g in wanted]
    planned += [g for g in GATE_NAMES if g in wanted and g not in planned]
    planned += [g for g in wanted if g not in planned]
    return planned


def ruff_configured(repo: Path) -> bool:
    """True when the project configures ruff: ruff.toml, .ruff.toml or [tool.ruff]."""
    if (repo / "ruff.toml").is_file() or (repo / ".ruff.toml").is_file():
        return True
    return isinstance(_pyproject_tool(repo, "ruff"), dict)


def mypy_settings(repo: Path) -> dict[str, Any] | None:
    """The project's mypy settings (mypy.ini, .mypy.ini, [tool.mypy], setup.cfg [mypy])."""
    for name in ("mypy.ini", ".mypy.ini"):
        if (repo / name).is_file():
            return _ini_section(repo / name, "mypy") or {}
    tool = _pyproject_tool(repo, "mypy")
    if isinstance(tool, dict):
        return dict(tool)
    if (repo / "setup.cfg").is_file():
        return _ini_section(repo / "setup.cfg", "mypy")
    return None


def unformatted_files(output: str) -> list[str]:
    """Files that `ruff format --check` would reformat, read from any ruff version's output.

    Understands the "Would reformat: <path>" lines of ruff before 0.16, the "full", "concise"
    and "grouped" diagnostic formats since, and "would reformat <path>" lines. Syntax errors
    are not counted.
    """
    found: list[str] = []
    header = False  # "full": the "unformatted: ..." header comes before its " --> " line
    group: str | None = None  # "grouped": a "<path>:" line comes before its diagnostics
    for line in commands.strip_ansi(output).splitlines():
        match = _WOULD_REFORMAT.match(line) or _UNFORMATTED_CONCISE.match(line)
        if match:
            found.append(match["path"])
        elif line.startswith("unformatted:"):
            header = True
            continue
        elif header and (match := _LOCATION.match(line)):
            found.append(match["path"])
        elif match := _GROUPED_FILE.match(line):
            group = match["path"]
        elif group is not None and _GROUPED_UNFORMATTED.match(line):
            found.append(group)
        header = False
    return list(dict.fromkeys(found))


def _pyproject_tool(repo: Path, name: str) -> Any:
    path = repo / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return None
    tool = data.get("tool")
    return tool.get(name) if isinstance(tool, dict) else None


def _ini_section(path: Path, section: str) -> dict[str, Any] | None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return None
    return dict(parser[section]) if parser.has_section(section) else None


# --------------------------------------------------------------------------------------------
# The run context
# --------------------------------------------------------------------------------------------

_UNSET: Any = object()


@dataclass
class _Run:
    repo: Path
    track: str
    base: str | None
    config: Any
    planned: list[str]
    allow_protected: list[str]
    acceptance: list[str]
    artifacts: Path | None
    timeout: float
    changed_override: list[str] | None
    current: str = ""
    _changed: Any = _UNSET
    _python: str | None = None
    _env: dict[str, str] | None = None
    _tools: commands.Tools | None = None
    _science: Any = _UNSET
    _purged: bool = False

    @property
    def settings(self) -> dict[str, Any]:
        return commands.track_settings(self.config, self.track)

    def science(self) -> ScienceConfig | None:
        """The working tree's science.toml (ScienceConfigError when it is invalid)."""
        if self._science is _UNSET:
            from stepswitch.verify import science

            try:
                self._science = science.load_science(self.repo)
            except science.ScienceConfigError as exc:
                self._science = exc
        if isinstance(self._science, Exception):
            raise self._science
        loaded: ScienceConfig | None = self._science
        return loaded

    @property
    def python(self) -> str:
        if self._python is None:
            science_python = None
            if self.track == "science":
                try:
                    loaded = self.science()
                except ValueError:
                    loaded = None
                science_python = loaded.science.python if loaded else None
            self._python = commands.project_python(
                self.repo, self.config, self.track, science_python=science_python
            )
        return self._python

    @property
    def env(self) -> dict[str, str]:
        if self._env is None:
            self._env = commands.project_env(self.python, repo=self.repo)
        return self._env

    @property
    def tools(self) -> commands.Tools:
        if self._tools is None:
            self._tools = commands.Tools(self.python, self.repo, self.env)
        return self._tools

    def changed(self) -> list[str] | None:
        if self._changed is _UNSET:
            if self.changed_override is not None:
                self._changed = sorted({_clean(p) for p in self.changed_override if _clean(p)})
            else:
                self._changed = commands.changed_files(self.repo, self.base)
        changed: list[str] | None = self._changed
        return changed

    def changed_python(self) -> list[str] | None:
        changed = self.changed()
        if changed is None:
            return None
        return [p for p in changed if p.endswith((".py", ".pyi")) and (self.repo / p).is_file()]

    def runs_later(self, gate: str) -> bool:
        """True when `gate` is planned after the current gate."""
        if gate not in self.planned or self.current not in self.planned:
            return False
        return self.planned.index(gate) > self.planned.index(self.current)

    def override(self, key: str) -> list[str] | None:
        table = self.settings.get("commands")
        if not isinstance(table, dict) or table.get(key) is None:
            return None
        value = table[key]
        if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
            raise GateConfigError(
                f"[tracks.{self.track}.commands].{key} must be a non-empty list of strings"
            )
        return list(value)

    def expand(
        self, argv: Sequence[str], *, files: Sequence[str] = (), tests: Sequence[str] = ()
    ) -> list[str]:
        values = {"python": self.python, "repo": str(self.repo)}
        out: list[str] = []
        for arg in argv:
            if arg == "{files}":
                out.extend(files)
            elif arg == "{tests}":
                out.extend(tests)
            else:
                out.extend(commands.expand([arg], values))
        return out

    def execute(
        self,
        argv: Sequence[str] | str,
        *,
        shell: bool = False,
        extra_env: dict[str, str] | None = None,
        unset: Sequence[str] = (),
    ) -> commands.CommandResult:
        if not self._purged:  # never run project code from stale bytecode
            commands.purge_stale_bytecode(self.repo)
            self._purged = True
        env = {**self.env, **(extra_env or {})}
        for name in unset:
            env.pop(name, None)
        result = commands.run(argv, cwd=self.repo, timeout=self.timeout, env=env, shell=shell)
        if self.artifacts is not None:
            self.artifacts.mkdir(parents=True, exist_ok=True)
            log = self.artifacts / f"{self.current or 'gate'}.log"
            with log.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"$ {result.display()}\n(exit {result.returncode}, {result.seconds:.2f} s)\n"
                    f"{result.output}\n\n"
                )
        return result


def _clean(path: str) -> str:
    text = path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


# --------------------------------------------------------------------------------------------
# Results from commands
# --------------------------------------------------------------------------------------------


def _skipped(name: str, summary: str, **details: Any) -> GateResult:
    return GateResult(name, "skipped", summary=summary, details=details)


def _one_line(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= SUMMARY_LIMIT else line[: SUMMARY_LIMIT - 3] + "..."


def _from_command(
    name: str,
    result: commands.CommandResult,
    *,
    keep: re.Pattern[str],
    passed: str,
    failed: str,
    hint: str = "",
    details: dict[str, Any] | None = None,
) -> GateResult:
    info = {
        "command": result.display(),
        "exit_code": result.returncode,
        "command_seconds": round(result.seconds, 3),
        **(details or {}),
    }
    if result.ok:
        return GateResult(name, "passed", summary=_one_line(passed), details=info)
    if not result.started:
        return GateResult(
            name,
            "error",
            summary=_one_line(result.start_error),
            details=info,
            report=f"Gate {name} could not run its command.\n{result.start_error}",
        )
    if result.timed_out:
        failed = f"timed out after {result.seconds:.0f} s"
    decisive = commands.distill(result.output, keep)
    lines = [
        f"Gate {name} failed: {failed}",
        f"Command: {result.display()}",
        f"Exit code: {result.returncode}" + (" (timed out)" if result.timed_out else ""),
        decisive,
    ]
    if hint:
        lines.append(hint)
    return GateResult(
        name, "failed", summary=_one_line(failed), details=info, report="\n".join(lines)
    )


def _pytest_summary(output: str) -> str:
    matches = _PYTEST_SUMMARY.findall(commands.strip_ansi(output))
    return matches[-1] if matches else ""


# --------------------------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------------------------


def _ruff_gate(run: _Run, name: str) -> GateResult:
    override = run.override(name)
    files = run.changed_python()
    if override is not None:
        if "{files}" in override and files is not None and not files:
            return _skipped(name, "no changed Python files")
        argv = run.expand(override, files=files if files is not None else ["."])
    else:
        if not ruff_configured(run.repo):
            return _skipped(
                name,
                "ruff is not configured in this project "
                "(no [tool.ruff] in pyproject.toml, no ruff.toml or .ruff.toml)",
            )
        if run.tools.error:
            return GateResult(name, "error", summary=_one_line(run.tools.error))
        ruff = run.tools.command("ruff")
        if ruff is None:
            return _skipped(name, "ruff is not installed in the project environment or on PATH")
        if files is not None and not files:
            return _skipped(name, "no changed Python files")
        targets = files if files is not None else ["."]
        if name == "format":
            argv = [*ruff, "format", "--check", "--force-exclude", *targets]
        else:
            argv = [
                *ruff,
                "check",
                "--no-fix",
                "--force-exclude",
                "--output-format",
                "concise",
                *targets,
            ]
    # The user's RUFF_OUTPUT_FORMAT must not change the output the default commands parse.
    result = run.execute(argv, unset=("RUFF_OUTPUT_FORMAT",) if override is None else ())
    scope = f"{len(files)} changed Python file(s)" if files is not None else "the project"
    if name == "format":
        unformatted = unformatted_files(result.output)
        listed = f"{len(unformatted)} file(s) would be reformatted"
        if unformatted and result.returncode == 1:
            failed = f"{listed}: {', '.join(unformatted[:5])}"
        else:  # exit code 2: ruff could not check every file (a syntax error, a bad option)
            failed = f"formatter check failed (exit code {result.returncode})"
            failed += f"; {listed}" if unformatted else ""
        hint = (
            f"Fix: run `{commands.display(['ruff', 'format', *unformatted[:20]])}`."
            if unformatted
            else ""
        )
        return _from_command(
            name,
            result,
            keep=_RUFF_FORMAT_KEEP,
            passed=f"formatting matches ruff format in {scope}",
            failed=failed,
            hint=hint,
            details={"files": files, "unformatted": unformatted},
        )
    found = re.search(r"Found (\d+) error", result.output)
    failed = f"{found.group(1)} lint violation(s)" if found else "lint check failed"
    return _from_command(
        name,
        result,
        keep=_RUFF_CHECK_KEEP,
        passed=f"no lint violations in {scope}",
        failed=failed,
        details={"files": files},
    )


def _gate_format(run: _Run) -> GateResult:
    return _ruff_gate(run, "format")


def _gate_lint(run: _Run) -> GateResult:
    return _ruff_gate(run, "lint")


def _gate_typecheck(run: _Run) -> GateResult:
    override = run.override("typecheck")
    files = run.changed_python()
    if override is not None:
        if "{files}" in override and files is not None and not files:
            return _skipped("typecheck", "no changed Python files")
        argv = run.expand(override, files=files if files is not None else ["."])
    else:
        settings = mypy_settings(run.repo)
        if settings is None:
            return _skipped(
                "typecheck",
                "mypy is not configured in this project (no mypy.ini, .mypy.ini, "
                "[tool.mypy] in pyproject.toml or [mypy] in setup.cfg)",
            )
        if files is not None and not files:
            return _skipped("typecheck", "no changed Python files")
        if run.tools.error:
            return GateResult("typecheck", "error", summary=_one_line(run.tools.error))
        mypy = run.tools.command("mypy")
        if mypy is None:
            return _skipped(
                "typecheck", "mypy is not installed in the project environment or on PATH"
            )
        if any(key in settings for key in ("files", "packages", "modules")):
            argv = list(mypy)  # the project's configured scope
        elif files is None:
            return _skipped(
                "typecheck",
                "changed files are unknown and mypy has no files or packages configured",
            )
        else:
            argv = [*mypy, *files]
    result = run.execute(argv)
    found = re.search(r"Found (\d+) errors? in (\d+) files?", result.output)
    failed = (
        f"{found.group(1)} type error(s) in {found.group(2)} file(s)"
        if found
        else "type check failed"
    )
    return _from_command(
        "typecheck", result, keep=_MYPY_KEEP, passed="no type errors", failed=failed
    )


def _gate_protected(run: _Run) -> GateResult:
    from stepswitch.verify import science

    if run.base is None:
        return _skipped("protected", "no base revision to compare against", changes=[])
    notes: list[str] = []
    try:
        loaded = run.science()
    except science.ScienceConfigError as exc:
        loaded = None
        notes.append(f"working-tree science.toml ignored: {exc}")
    report = science.check_protected(run.repo, run.base, loaded, allow=run.allow_protected)
    report.notes[:0] = notes
    return GateResult(
        "protected",
        report.status,
        summary=_one_line(report.summary()),
        details=report.to_dict(),
        report=report.report(),
    )


def _test_command(run: _Run) -> tuple[list[str] | None, str, bool]:
    """(argv prefix, reason when there is none, whether it is the default pytest command)."""
    override = run.override("tests")
    if override is not None:
        return override, "", False
    if not impact.has_tests(run.repo):
        return None, "no tests found", False
    if run.tools.error:
        raise GateConfigError(run.tools.error)
    pytest = run.tools.command("pytest")
    if pytest is None:
        return None, "pytest is not installed in the project environment", False
    return [*pytest, "-q", "--tb=short"], "", True


def _tests_argv(run: _Run, base: list[str], selection: Sequence[str], default: bool) -> list[str]:
    if default:
        return [*base, *selection]
    if "{tests}" in base:
        return run.expand(base, tests=selection)
    return [*run.expand(base), *selection]


def _is_pytest(argv: Sequence[str]) -> bool:
    return any(Path(arg).stem == "pytest" for arg in argv)


def _pytest_result(
    name: str, result: commands.CommandResult, what: str, *, pytest: bool, **details: Any
) -> GateResult:
    summary = _pytest_summary(result.output)
    if pytest and result.returncode == 5 and not result.timed_out:  # no tests collected
        return GateResult(
            name,
            "passed",
            summary=f"{what}: no tests collected",
            details={"command": result.display(), "exit_code": 5, **details},
        )
    return _from_command(
        name,
        result,
        keep=_PYTEST_KEEP,
        passed=f"{what}: {summary or 'passed'}",
        failed=f"{what}: {summary or f'exit code {result.returncode}'}",
        details=details,
    )


def _gate_impacted_tests(run: _Run) -> GateResult:
    base, reason, default = _test_command(run)
    if base is None:
        return _skipped("impacted_tests", reason)
    has_testmon = default and run.tools.has("testmon")
    selection = impact.select_tests(
        run.repo, run.changed(), python=run.python, has_testmon=has_testmon
    )
    details = {"mode": selection.mode, "reason": selection.reason, "selected": selection.args}
    if selection.mode == "none":
        return _skipped("impacted_tests", selection.reason, **details)
    if selection.mode == "full" and run.runs_later("full_tests"):
        return _skipped(
            "impacted_tests",
            f"the selection is the whole suite ({selection.reason}); it runs at full_tests",
            **details,
        )
    args = selection.args if selection.mode != "full" else []
    argv = _tests_argv(run, base, args, default)
    result = run.execute(argv, extra_env=selection.env)
    what = f"impacted tests ({selection.mode})"
    return _pytest_result("impacted_tests", result, what, pytest=_is_pytest(argv), **details)


def _gate_full_tests(run: _Run) -> GateResult:
    base, reason, default = _test_command(run)
    parts: list[str] = []
    details: dict[str, Any] = {"acceptance": []}
    if base is not None:
        argv = _tests_argv(run, base, [], default)
        result = run.execute(argv)
        outcome = _pytest_result("full_tests", result, "test suite", pytest=_is_pytest(argv))
        details.update(outcome.details)
        if outcome.status != "passed":
            outcome.details = details
            return outcome
        parts.append(outcome.summary)
    elif not run.acceptance:
        return _skipped("full_tests", f"{reason}; no acceptance commands")
    values = {
        "python": commands.shell_quote(run.python),
        "repo": commands.shell_quote(str(run.repo)),
    }
    for line in run.acceptance:
        command = commands.expand([line], values)[0]
        result = run.execute(command, shell=True)
        details["acceptance"].append(
            {"command": line, "exit_code": result.returncode, "seconds": round(result.seconds, 3)}
        )
        if not result.ok:
            outcome = _from_command(
                "full_tests",
                result,
                keep=_PYTEST_KEEP,
                passed="",
                failed=f"acceptance command failed: {line}",
            )
            outcome.details = {**details, **outcome.details}
            return outcome
    if run.acceptance:
        parts.append(f"{len(run.acceptance)} acceptance command(s) passed")
    return GateResult("full_tests", "passed", summary=_one_line("; ".join(parts)), details=details)


def _gate_golden(run: _Run) -> GateResult:
    from stepswitch.verify import science

    try:
        loaded = run.science()
    except science.ScienceConfigError as exc:
        return GateResult("golden", "error", summary=_one_line(str(exc)), report=str(exc))
    if loaded is None:
        return _skipped("golden", "no science.toml in the project")
    if not loaded.golden:
        return _skipped("golden", "science.toml declares no golden cases")
    notebooks: dict[str, Any] = {}
    if (run.repo / science.JUPYTEXT_TOML).is_file():
        scripts = science.paired_notebooks(run.repo, run.changed())
        notebooks = science.sync_notebooks(run.repo, scripts, tools=run.tools, timeout=run.timeout)
        if notebooks.get("failed"):
            failures = notebooks["failed"]
            lines = [f"jupytext could not update {f['script']}:\n{f['output']}" for f in failures]
            return GateResult(
                "golden",
                "failed",
                summary=f"jupytext could not update {len(failures)} paired notebook(s)",
                details={"notebooks": notebooks},
                report="\n".join(lines),
            )
    plots = run.artifacts / "golden" if run.artifacts is not None else None
    report = science.run_golden(
        run.repo, loaded, python=run.python, artifacts=plots, timeout=run.timeout, env=run.env
    )
    details = report.to_dict()
    if notebooks:
        details["notebooks"] = notebooks
    return GateResult(
        "golden",
        report.status,
        summary=_one_line(report.summary()),
        details=details,
        report=report.failure_report(),
    )


def _gate_review(run: _Run) -> GateResult:
    return _skipped(
        "review",
        "the cross-family review is an agent step (SPEC §8.5); the orchestrator runs it",
    )


GATES: dict[str, Callable[[_Run], GateResult]] = {
    "format": _gate_format,
    "lint": _gate_lint,
    "typecheck": _gate_typecheck,
    "protected": _gate_protected,
    "impacted_tests": _gate_impacted_tests,
    "golden": _gate_golden,
    "full_tests": _gate_full_tests,
    "review": _gate_review,
}


# --------------------------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------------------------


def _verdict_report(results: Sequence[GateResult], failed_gate: str | None, track: str) -> str:
    overview = ", ".join(f"{r.name} {r.status}" for r in results)
    if failed_gate is None:
        return f"All gates passed for the {track} track ({overview})."
    failing = next(r for r in results if r.name == failed_gate)
    head = f"Verification failed at gate `{failing.name}` ({failing.status}): {failing.summary}"
    tail = f"Gates: {overview}."
    body = failing.report.strip()
    budget = REPORT_LIMIT - len(head) - len(tail) - 2
    if len(body) > budget:
        body = body[: max(budget - 16, 0)].rstrip() + "\n... (truncated)"
    return "\n".join(part for part in (head, body, tail) if part)


def run_gates(
    repo: Path,
    *,
    track: str,
    base: str | None,
    config: Config | None = None,
    changed: Sequence[str] | None = None,
    allow_protected: Sequence[str] = (),
    only: Sequence[str] | None = None,
    acceptance: Sequence[str] = (),
    artifacts: Path | None = None,
    timeout: float = 900.0,
) -> Verdict:
    """Run the track's gates on the working tree of `repo` (see the module docstring).

    `base` is the git revision the working tree is compared against: it decides the changed
    files (unless `changed` is given) and the protected values. With no base, changed files are
    the uncommitted ones and the protected gate is skipped. `config` defaults to the project's
    configuration. `timeout` bounds each command. `artifacts` receives command logs, the
    numerical diff report and its plots.

    Problems inside a gate become that gate's error result; a track without a gate list
    raises GateConfigError before any gate runs.
    """
    repo = Path(repo).resolve()
    loaded = config if config is not None else config_module.load(repo)
    run = _Run(
        repo=repo,
        track=track,
        base=base,
        config=loaded,
        planned=plan(gates_for(track, loaded), only),
        allow_protected=list(allow_protected),
        acceptance=list(acceptance),
        artifacts=artifacts,
        timeout=timeout,
        changed_override=list(changed) if changed is not None else None,
    )
    results: list[GateResult] = []
    failed_gate: str | None = None
    for name in run.planned:
        if failed_gate is not None:
            results.append(_skipped(name, f"not run: {failed_gate} failed"))
            continue
        run.current = name
        start = time.monotonic()
        gate = GATES.get(name)
        try:
            if gate is None:
                known = ", ".join(GATE_NAMES)
                result = GateResult(
                    name, "error", summary=f"unknown gate {name!r} (known: {known})"
                )
            else:
                result = gate(run)
        except GateConfigError as exc:
            result = GateResult(name, "error", summary=_one_line(str(exc)), report=str(exc))
        except Exception as exc:  # a broken gate must not take the runner down
            trace = "".join(traceback.format_exception(exc))[-1500:]
            result = GateResult(
                name,
                "error",
                summary=_one_line(f"gate crashed: {type(exc).__name__}: {exc}"),
                report=f"Gate {name} crashed inside Stepswitch.\n{trace}",
            )
        result.seconds = time.monotonic() - start
        result.summary = _one_line(result.summary)
        results.append(result)
        if result.status in FAILING:
            failed_gate = name
    return Verdict(
        passed=failed_gate is None,
        results=results,
        failed_gate=failed_gate,
        report=_verdict_report(results, failed_gate, track),
    )
