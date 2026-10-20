"""Verification gates (SPEC §8.1) and the project commands behind them.

Most gate tests replace a tool with a fake script through `[tracks.<track>.commands]`. That
checks the runner itself (order, stop at the first failure, skips, overrides, reports) without
depending on real tools. A few tests run the real ruff and pytest that the dev group installs.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stepswitch.config import Config
from stepswitch.verify import commands, gates
from stepswitch.verify.gates import GATE_NAMES, GateResult, Verdict, gates_for, plan, run_gates

# fake.py <name> <exit code> [args...]: logs [name, *args] to $FAKE_LOG, prints $FAKE_NOISE
# progress lines, and on failure $FAKE_DECISIVE "Would reformat" lines plus one lint line.
FAKE_TOOL = """\
import json, os, sys
name, code, *rest = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps([name, *rest]) + "\\n")
for number in range(int(os.environ.get("FAKE_NOISE", "2"))):
    print(f"{name}: progress line {number}")
if code != "0":
    for number in range(int(os.environ.get("FAKE_DECISIVE", "1"))):
        print(f"Would reformat: {name}_{number}.py")
    print(f"{name}_0.py:3:1: F401 `os` imported but unused")
sys.exit(int(code))
"""

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
needs_ruff = pytest.mark.skipif(importlib.util.find_spec("ruff") is None, reason="needs ruff")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def config(track: str = "general", **settings: Any) -> Config:
    return Config.model_validate({"tracks": {track: settings}})


def tool(fake: Path, name: str, code: int = 0, *args: str) -> list[str]:
    return ["{python}", str(fake), name, str(code), *args]


def calls(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "calls.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def statuses(verdict: Verdict) -> list[tuple[str, str]]:
    return [(r.name, r.status) for r in verdict.results]


def gate(verdict: Verdict, name: str) -> GateResult:
    result = verdict.result(name)
    assert result is not None, f"{name} not in {statuses(verdict)}"
    return result


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))  # not inside another checkout
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "fake.py"
    script.write_text(FAKE_TOOL, encoding="utf-8")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    return script


# --------------------------------------------------------------------------------------------
# Gate lists and plans
# --------------------------------------------------------------------------------------------


def test_gates_for_reads_the_track_list() -> None:
    general = ["format", "lint", "typecheck", "impacted_tests", "full_tests"]
    science = ["format", "lint", "protected", "impacted_tests", "golden", "full_tests"]
    assert gates_for("general", None) == general
    assert gates_for("science", None) == science
    assert set(general) | set(science) <= set(GATE_NAMES)
    assert gates_for("science", config("science", gates=["golden"])) == ["golden"]
    assert gates_for("docs", config("docs", gates=["format"])) == ["format"]
    with pytest.raises(gates.GateConfigError, match="unknown track"):
        gates_for("docs", None)
    with pytest.raises(gates.GateConfigError, match="must be a list"):
        gates_for("general", config(gates="format"))


@pytest.mark.parametrize(
    ("track", "only", "expected"),
    [
        (["format", "lint"], None, ["format", "lint"]),
        (["format", "format", "lint"], None, ["format", "lint"]),
        (["format", "lint", "typecheck"], ["typecheck", "format"], ["format", "typecheck"]),
        # names outside the track list follow in GATE_NAMES order, unknown names last
        (
            ["lint", "format"],
            ["review", "bogus", "format", "golden"],
            ["format", "golden", "review", "bogus"],
        ),
        (["format"], [], []),
    ],
)
def test_plan_follows_the_track_order(
    track: list[str], only: list[str] | None, expected: list[str]
) -> None:
    assert plan(track, only) == expected


def test_project_config_is_loaded_by_default(repo: Path) -> None:
    write(repo, {"stepswitch.toml": '[tracks.general]\ngates = ["review"]\n'})
    verdict = run_gates(repo, track="general", base=None, changed=[])
    assert statuses(verdict) == [("review", "skipped")]
    assert "agent step" in verdict.results[0].summary
    assert verdict.passed and verdict.failed_gate is None
    assert verdict.report == "All gates passed for the general track (review skipped)."


# --------------------------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------------------------


def test_gates_stop_at_the_first_failure(repo: Path, fake: Path, tmp_path: Path) -> None:
    settings = {
        "gates": ["format", "lint", "typecheck", "full_tests", "review"],
        "commands": {
            "format": tool(fake, "format"),
            "lint": tool(fake, "lint", 1),
            "typecheck": tool(fake, "typecheck"),
            "tests": tool(fake, "tests"),
        },
    }
    verdict = run_gates(repo, track="general", base=None, config=config(**settings), changed=[])
    assert statuses(verdict) == [
        ("format", "passed"),
        ("lint", "failed"),
        ("typecheck", "skipped"),
        ("full_tests", "skipped"),
        ("review", "skipped"),
    ]
    assert [call[0] for call in calls(tmp_path)] == ["format", "lint"]
    assert {r.summary for r in verdict.results[2:]} == {"not run: lint failed"}
    assert not verdict.passed and verdict.failed_gate == "lint"
    report = verdict.report
    assert report.startswith("Verification failed at gate `lint` (failed): ")
    assert "Command: " in report and "fake.py" in report and "Exit code: 1" in report
    assert "lint_0.py:3:1: F401" in report  # the decisive line, not the progress noise
    assert "progress line" not in report
    assert report.endswith(
        "Gates: format passed, lint failed, typecheck skipped, full_tests skipped, review skipped."
    )


def test_only_runs_the_named_gates(repo: Path, fake: Path, tmp_path: Path) -> None:
    settings = {
        "gates": ["lint", "format", "full_tests"],
        "commands": {"format": tool(fake, "format"), "lint": tool(fake, "lint")},
    }
    verdict = run_gates(
        repo,
        track="general",
        base=None,
        config=config(**settings),
        changed=[],
        only=["review", "format", "lint"],
    )
    assert statuses(verdict) == [("lint", "passed"), ("format", "passed"), ("review", "skipped")]
    assert [call[0] for call in calls(tmp_path)] == ["lint", "format"]
    assert verdict.passed


def test_unconfigured_tools_are_skipped_not_failed(repo: Path) -> None:
    write(repo, {"mod.py": "x = 1\n"})
    verdict = run_gates(repo, track="general", base=None, config=Config(), changed=["mod.py"])
    assert verdict.passed and {r.status for r in verdict.results} == {"skipped"}
    assert "ruff is not configured" in gate(verdict, "format").summary
    assert "ruff is not configured" in gate(verdict, "lint").summary
    assert "mypy is not configured" in gate(verdict, "typecheck").summary
    assert gate(verdict, "impacted_tests").summary == "no tests found"
    assert gate(verdict, "full_tests").summary == "no tests found; no acceptance commands"


def test_missing_tools_are_skipped_not_failed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(
        repo,
        {
            "pyproject.toml": "[tool.ruff]\nline-length = 100\n\n[tool.mypy]\nstrict = true\n",
            "mod.py": "x = 1\n",
            "tests/test_mod.py": "def test_x():\n    pass\n",
        },
    )
    nothing = {"modules": dict.fromkeys(commands.PROBE_MODULES, False)}
    monkeypatch.setattr(commands, "probe_interpreter", lambda *args, **kwargs: nothing)
    monkeypatch.setattr(commands.Tools, "which", lambda self, program: None)
    verdict = run_gates(repo, track="general", base=None, config=Config(), changed=["mod.py"])
    assert verdict.passed and {r.status for r in verdict.results} == {"skipped"}
    assert "ruff is not installed" in gate(verdict, "format").summary
    assert "mypy is not installed" in gate(verdict, "typecheck").summary
    assert "pytest is not installed" in gate(verdict, "impacted_tests").summary
    assert "pytest is not installed" in gate(verdict, "full_tests").summary


def test_an_interpreter_that_cannot_run_is_an_error(repo: Path, tmp_path: Path) -> None:
    write(repo, {"pyproject.toml": "[tool.ruff]\n", "mod.py": "x = 1\n"})
    broken = config(gates=["format", "lint"], python=str(tmp_path / "missing" / "python"))
    verdict = run_gates(repo, track="general", base=None, config=broken, changed=["mod.py"])
    assert statuses(verdict) == [("format", "error"), ("lint", "skipped")]
    assert "cannot run the project interpreter" in gate(verdict, "format").summary


def test_configuration_errors_and_crashes_become_gate_errors(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = run_gates(
        repo, track="general", base=None, config=config(gates=["bogus", "review"]), changed=[]
    )
    assert statuses(unknown) == [("bogus", "error"), ("review", "skipped")]
    assert "unknown gate 'bogus'" in unknown.results[0].summary

    bad = config(gates=["format"], commands={"format": "ruff format"})
    result = run_gates(repo, track="general", base=None, config=bad, changed=[]).results[0]
    assert result.status == "error" and "must be a non-empty list of strings" in result.summary

    def crash(run: Any) -> GateResult:
        raise RuntimeError("kaboom")

    monkeypatch.setitem(gates.GATES, "lint", crash)
    crashed = run_gates(
        repo, track="general", base=None, config=config(gates=["lint", "review"]), changed=[]
    )
    assert statuses(crashed) == [("lint", "error"), ("review", "skipped")]
    assert crashed.results[0].summary == "gate crashed: RuntimeError: kaboom"
    assert "RuntimeError: kaboom" in crashed.report

    with pytest.raises(gates.GateConfigError):
        run_gates(repo, track="nonexistent", base=None, config=Config(), changed=[])


def test_changed_overrides_detection(repo: Path, fake: Path, tmp_path: Path) -> None:
    write(repo, {"a.py": "", "b.pyi": "", "sub/c.py": "", "notes.md": ""})
    settings = {"gates": ["format"], "commands": {"format": tool(fake, "format", 0, "{files}")}}
    changed = ["sub\\c.py", "./a.py", "notes.md", "deleted.py", "b.pyi", "a.py"]
    verdict = run_gates(
        repo, track="general", base=None, config=config(**settings), changed=changed
    )
    assert verdict.passed
    assert calls(tmp_path) == [["format", "a.py", "b.pyi", "sub/c.py"]]
    only_docs = run_gates(
        repo, track="general", base=None, config=config(**settings), changed=["notes.md"]
    )
    assert gate(only_docs, "format").status == "skipped"
    assert gate(only_docs, "format").summary == "no changed Python files"


def test_overrides_expand_python_and_repo(repo: Path, fake: Path, tmp_path: Path) -> None:
    # The configured interpreter wins; the base interpreter differs from the test venv's.
    python = getattr(sys, "_base_executable", "") or sys.executable
    settings = {
        "gates": ["lint"],
        "python": python,
        "commands": {"lint": [*tool(fake, "lint"), "--root={repo}/src", "{unknown}"]},
    }
    verdict = run_gates(repo, track="general", base=None, config=config(**settings), changed=[])
    assert verdict.passed, verdict.report
    assert calls(tmp_path) == [["lint", f"--root={repo.resolve()}/src", "{unknown}"]]
    assert shlex.split(gate(verdict, "lint").details["command"])[0] == python


def test_artifacts_receive_each_gate_log(repo: Path, fake: Path, tmp_path: Path) -> None:
    settings = {
        "gates": ["format", "lint"],
        "commands": {"format": tool(fake, "format"), "lint": tool(fake, "lint", 2)},
    }
    artifacts = tmp_path / "artifacts"
    run_gates(
        repo, track="general", base=None, config=config(**settings), changed=[], artifacts=artifacts
    )
    log = (artifacts / "format.log").read_text(encoding="utf-8")
    assert log.startswith("$ ") and "(exit 0, " in log and "format: progress line 1" in log
    assert "(exit 2, " in (artifacts / "lint.log").read_text(encoding="utf-8")


def test_the_report_is_bounded(repo: Path, fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_NOISE", "3000")
    monkeypatch.setenv("FAKE_DECISIVE", "3000")
    settings = {"gates": ["format", "review"], "commands": {"format": tool(fake, "format", 1)}}
    verdict = run_gates(repo, track="general", base=None, config=config(**settings), changed=[])
    assert len(verdict.report) <= gates.REPORT_LIMIT
    assert verdict.report.startswith("Verification failed at gate `format` (failed): 3000 file(s)")
    assert "Would reformat: format_0.py" in verdict.report
    assert verdict.report.endswith("Gates: format failed, review skipped.")
    assert all(len(r.summary) <= gates.SUMMARY_LIMIT for r in verdict.results)


def test_verdict_to_dict_is_strict_json() -> None:
    details = {
        "ratio": math.inf,
        "low": -math.inf,
        "missing": math.nan,
        "value": np.float64(0.5),
        "array": np.arange(3),
        "path": Path("a") / "b",
        "pair": (1, 2),
    }
    result = GateResult("golden", "failed", seconds=1.23456, details=details)
    verdict = Verdict(False, [result], "golden", "report")
    data = json.loads(json.dumps(verdict.to_dict(), allow_nan=False))
    assert data["passed"] is False and data["failed_gate"] == "golden"
    assert data["results"][0]["seconds"] == 1.235
    assert data["results"][0]["details"] == {
        "ratio": "inf",
        "low": "-inf",
        "missing": "nan",
        "value": 0.5,
        "array": [0, 1, 2],
        "path": str(Path("a") / "b"),
        "pair": [1, 2],
    }
    assert verdict.result("golden") is result and verdict.result("lint") is None


# --------------------------------------------------------------------------------------------
# Gates with real tools
# --------------------------------------------------------------------------------------------


@needs_ruff
def test_ruff_checks_only_the_changed_files(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUFF_OUTPUT_FORMAT", "json")  # a user preference the gates must ignore
    write(
        repo,
        {
            "pyproject.toml": '[tool.ruff]\nline-length = 100\n[tool.ruff.lint]\nselect = ["F"]\n',
            "good.py": "x = 1\n",
            "ugly.py": "x=1\n",
            "unused.py": "import os\n",
            "broken.py": "x = (\n",
        },
    )
    settings = config(gates=["format", "lint"])
    clean = run_gates(repo, track="general", base=None, config=settings, changed=["good.py"])
    assert statuses(clean) == [("format", "passed"), ("lint", "passed")], clean.report

    changed = ["good.py", "ugly.py", "unused.py"]
    unformatted = run_gates(repo, track="general", base=None, config=settings, changed=changed)
    assert unformatted.failed_gate == "format"
    assert gate(unformatted, "format").summary == "1 file(s) would be reformatted: ugly.py"
    assert "Fix: run `ruff format ugly.py`." in unformatted.report

    syntax = run_gates(repo, track="general", base=None, config=settings, changed=["broken.py"])
    assert gate(syntax, "format").summary == "formatter check failed (exit code 2)"
    assert "broken.py:1:" in syntax.report  # where the parser stopped

    lint = run_gates(
        repo, track="general", base=None, config=settings, changed=changed, only=["lint"]
    )
    assert lint.failed_gate == "lint" and gate(lint, "lint").summary == "1 lint violation(s)"
    assert re.search(r"unused\.py:1:8: F401", lint.report)


FULL_FORMAT_OUTPUT = """\
invalid-syntax: unexpected EOF while parsing
 --> broken.py:1:7
  |
1 | x = (
  |      ^

unformatted: File would be reformatted
 --> sub dir\\b.py:1:2
  |
  - y=2
1 + y = 2
  |

unformatted: File would be reformatted
 --> ugly.py:1:2
  |
  - x=1
1 + x = 1
  |

2 files would be reformatted
"""


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        # ruff 0.17 default ("full") output, captured from a real run; the syntax error is
        # not a file that would be reformatted
        (FULL_FORMAT_OUTPUT, ["sub dir\\b.py", "ugly.py"]),
        # ruff before 0.16
        (
            "Would reformat: a.py\nWould reformat: b c.py\n2 files would be reformatted\n",
            ["a.py", "b c.py"],
        ),
        # --output-format concise and grouped
        (
            "a.py:1:2: unformatted: File would be reformatted\n1 file would be reformatted\n",
            ["a.py"],
        ),
        (
            "a.py:\n  1:2 unformatted: File would be reformatted\n\n1 file would be reformatted\n",
            ["a.py"],
        ),
        # another formatter through a command override
        ("would reformat src/a.py\n\nOh no!\n1 file would be reformatted.\n", ["src/a.py"]),
        ("error: Failed to parse a.py:1:7: Unexpected token\n", []),
    ],
)
def test_unformatted_files_reads_every_output_style(output: str, expected: list[str]) -> None:
    assert gates.unformatted_files(output) == expected


def test_typecheck_scope(
    repo: Path, fake: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = commands.Tools.command

    def command(self: commands.Tools, name: str) -> list[str] | None:
        return [sys.executable, str(fake), "mypy", "0"] if name == "mypy" else real(self, name)

    monkeypatch.setattr(commands.Tools, "command", command)
    write(repo, {"a.py": "x = 1\n", "mypy.ini": "[mypy]\nstrict = True\n"})
    settings = config(gates=["typecheck"])
    assert run_gates(repo, track="general", base=None, config=settings, changed=["a.py"]).passed
    unknown = run_gates(repo, track="general", base=None, config=settings)  # not a checkout
    assert "changed files are unknown" in gate(unknown, "typecheck").summary
    write(repo, {"mypy.ini": "[mypy]\nfiles = a.py\n"})
    assert run_gates(repo, track="general", base=None, config=settings).passed
    assert calls(tmp_path) == [["mypy", "a.py"], ["mypy"]]  # changed files, then the config


def test_tool_configuration_detection(tmp_path: Path) -> None:
    assert not gates.ruff_configured(tmp_path) and gates.mypy_settings(tmp_path) is None
    write(tmp_path, {"setup.cfg": "[metadata]\nname = x\n"})
    assert gates.mypy_settings(tmp_path) is None
    write(tmp_path, {"setup.cfg": "[mypy]\nfiles = src\n"})
    assert gates.mypy_settings(tmp_path) == {"files": "src"}
    write(tmp_path, {"pyproject.toml": '[tool.mypy]\npackages = ["pkg"]\n\n[tool.ruff]\n'})
    assert gates.mypy_settings(tmp_path) == {"packages": ["pkg"]}
    assert gates.ruff_configured(tmp_path)
    write(tmp_path, {".mypy.ini": "[mypy]\nstrict = True\n"})
    assert gates.mypy_settings(tmp_path) == {"strict": "True"}
    write(tmp_path, {"mypy.ini": "[other]\n"})
    assert gates.mypy_settings(tmp_path) == {}  # mypy reads mypy.ini even without [mypy]
    write(tmp_path, {"pyproject.toml": "not = [valid\n"})
    assert not gates.ruff_configured(tmp_path)
    write(tmp_path, {".ruff.toml": ""})
    assert gates.ruff_configured(tmp_path)


# --------------------------------------------------------------------------------------------
# Test gates
# --------------------------------------------------------------------------------------------

PROJECT = {
    "pytest.ini": "[pytest]\n",  # keeps the inner pytest from reading any parent configuration
    "pkg/__init__.py": "",
    "pkg/core.py": "VALUE = 2\n",
    "tests/test_core.py": (
        "from pkg.core import VALUE\n\n\ndef test_value():\n    assert VALUE == 2\n"
    ),
    "tests/test_broken.py": "def test_broken():\n    assert False, 'unrelated failure'\n",
}


def test_impacted_tests_run_the_selection(repo: Path) -> None:
    write(repo, PROJECT)
    settings = config(gates=["impacted_tests", "full_tests"])
    mapped = run_gates(
        repo,
        track="general",
        base=None,
        config=settings,
        changed=["pkg/core.py"],
        only=["impacted_tests"],
    )
    result = gate(mapped, "impacted_tests")
    assert result.status == "passed", mapped.report
    assert result.details["mode"] == "mapped"
    assert result.details["selected"] == ["tests/test_core.py"]  # test_broken is not run
    assert result.summary.startswith("impacted tests (mapped): 1 passed in ")

    none = run_gates(repo, track="general", base=None, config=settings, changed=["pkg/new.py"])
    assert gate(none, "impacted_tests").status == "skipped"

    full = run_gates(repo, track="general", base=None, config=settings, changed=["data/t.csv"])
    deferred = gate(full, "impacted_tests")
    assert deferred.status == "skipped" and "it runs at full_tests" in deferred.summary
    assert full.failed_gate == "full_tests"
    assert "test_broken" in full.report and "unrelated failure" in full.report
    assert gate(full, "full_tests").summary.startswith("test suite: 1 failed, 1 passed in ")


def test_test_command_overrides_take_the_selection(repo: Path, fake: Path, tmp_path: Path) -> None:
    write(repo, PROJECT)
    placed = config(
        gates=["impacted_tests"], commands={"tests": tool(fake, "t", 0, "{tests}", "-x")}
    )
    appended = config(gates=["impacted_tests"], commands={"tests": tool(fake, "t", 0, "-x")})
    for settings in (placed, appended):
        assert run_gates(
            repo, track="general", base=None, config=settings, changed=["pkg/core.py"]
        ).passed
    assert calls(tmp_path) == [["t", "tests/test_core.py", "-x"], ["t", "-x", "tests/test_core.py"]]


def test_no_tests_collected_passes_only_for_pytest(repo: Path, fake: Path) -> None:
    write(repo, {"pytest.ini": "[pytest]\n", "tests/test_empty.py": "# no tests yet\n"})
    verdict = run_gates(
        repo, track="general", base=None, config=config(gates=["full_tests"]), changed=[]
    )
    assert verdict.passed and verdict.results[0].summary == "test suite: no tests collected"
    other = config(gates=["full_tests"], commands={"tests": tool(fake, "tests", 5)})
    assert not run_gates(repo, track="general", base=None, config=other, changed=[]).passed


def test_acceptance_commands_run_after_the_suite(repo: Path) -> None:
    write(
        repo,
        {
            "pytest.ini": "[pytest]\n",
            "tests/test_mark.py": (
                "from pathlib import Path\n\n\n"
                "def test_mark():\n    Path('suite-ran.txt').write_text('yes')\n"
            ),
        },
    )
    after_suite = (
        '{python} -c "import pathlib, sys; '
        "sys.exit(0 if pathlib.Path('suite-ran.txt').is_file() else 4)\""
    )
    failing = '{python} -c "import sys; sys.exit(3)"'
    settings = config(gates=["full_tests"])
    verdict = run_gates(
        repo,
        track="general",
        base=None,
        config=settings,
        changed=[],
        acceptance=[after_suite, failing],
    )
    result = gate(verdict, "full_tests")
    assert result.status == "failed"
    assert result.summary == f"acceptance command failed: {failing}"
    ran = [(item["command"], item["exit_code"]) for item in result.details["acceptance"]]
    assert ran == [(after_suite, 0), (failing, 3)]

    passed = run_gates(
        repo, track="general", base=None, config=settings, changed=[], acceptance=[after_suite]
    )
    assert passed.passed
    assert gate(passed, "full_tests").summary.endswith("; 1 acceptance command(s) passed")


def test_acceptance_commands_run_without_a_test_suite(repo: Path) -> None:
    verdict = run_gates(
        repo,
        track="general",
        base=None,
        config=config(gates=["full_tests"]),
        changed=[],
        acceptance=['{python} -c "print(42)"'],
    )
    assert verdict.passed
    assert verdict.results[0].summary == "1 acceptance command(s) passed"


# --------------------------------------------------------------------------------------------
# Science gates
# --------------------------------------------------------------------------------------------

GOLDEN_TOML = """\
[[golden]]
case = "demo"
command = [
  "{python}", "-c",
  "import json, sys; json.dump({'y': [1.0, 2.0, 4.0, 2.0, 1.0]}, open(sys.argv[1], 'w'))",
  "{output}/out.json",
]

[[golden.quantities]]
name = "y"
file = "out.json"
rtol = 1e-9
"""


def science_project(repo: Path, expected: list[float]) -> None:
    write(
        repo,
        {
            "science.toml": GOLDEN_TOML,
            "tests/golden/demo/expected/out.json": json.dumps({"y": expected}),
        },
    )


def test_golden_gate_skips_errors_and_compares(repo: Path, tmp_path: Path) -> None:
    settings = config("science", gates=["golden"])

    def run(**kwargs: Any) -> Verdict:
        return run_gates(repo, track="science", base=None, config=settings, changed=[], **kwargs)

    assert gate(run(), "golden").summary == "no science.toml in the project"
    write(repo, {"science.toml": "[science]\nmin_peak_height = 0.1\n"})
    assert gate(run(), "golden").summary == "science.toml declares no golden cases"
    write(repo, {"science.toml": "[[golden]]\ncase = 'demo'\ncommand = []\n"})
    invalid = run()
    assert invalid.failed_gate == "golden" and gate(invalid, "golden").status == "error"
    assert "golden[0].command" in gate(invalid, "golden").summary

    science_project(repo, [1, 2, 4, 2, 1])
    artifacts = tmp_path / "artifacts"
    passed = run(artifacts=artifacts)
    assert passed.passed, passed.report
    assert gate(passed, "golden").summary == "1 quantities within tolerance in 1 case(s)"
    assert (artifacts / "golden" / "golden-report.md").is_file()
    assert (artifacts / "golden" / "demo--y.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    science_project(repo, [1, 2, 4.5, 2, 1])
    failed = run()
    assert failed.failed_gate == "golden"
    assert "| demo | y | elementwise |" in failed.report
    assert "worst at index 2: expected 4.5, actual 4" in failed.report


def test_golden_gate_pushes_paired_scripts_into_notebooks(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    science_project(repo, [1, 2, 4, 2, 1])
    write(repo, {"jupytext.toml": "formats = 'ipynb,py:percent'\n", "a.py": "", "a.ipynb": "{}"})
    settings = config("science", gates=["golden"])
    real = commands.Tools.command

    def without_jupytext(self: commands.Tools, name: str) -> list[str] | None:
        return None if name == "jupytext" else real(self, name)

    with monkeypatch.context() as patch:
        patch.setattr(commands.Tools, "command", without_jupytext)
        missing = run_gates(repo, track="science", base=None, config=settings, changed=["a.py"])
    assert missing.passed, missing.report
    notebooks = gate(missing, "golden").details["notebooks"]
    assert notebooks["note"] == "jupytext not installed; paired notebooks were not updated"

    # A jupytext that refuses to update the notebook fails the gate before the golden runs.
    module = tmp_path / "fakemods" / "jupytext"
    write(
        module, {"__init__.py": "", "__main__.py": "import sys\nprint('bad header')\nsys.exit(1)\n"}
    )
    monkeypatch.setenv("PYTHONPATH", str(module.parent))
    failed = run_gates(repo, track="science", base=None, config=settings, changed=["a.py"])
    result = gate(failed, "golden")
    assert result.status == "failed"
    assert result.summary == "jupytext could not update 1 paired notebook(s)"
    assert "bad header" in failed.report
    assert "--update" in result.details["notebooks"]["failed"][0]["command"]


@needs_git
def test_protected_gate_compares_against_the_base(repo: Path) -> None:
    git(repo, "init", "-q")
    write(repo, {"science.toml": '[[protected]]\nfile = "consts.py"\nnames = ["C"]\n'})
    write(repo, {"consts.py": "C = 299792458.0\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    settings = config("science", gates=["protected"])

    def run(base: str | None, allow: tuple[str, ...] = ()) -> Verdict:
        return run_gates(
            repo, track="science", base=base, config=settings, changed=[], allow_protected=allow
        )

    assert gate(run("HEAD"), "protected").summary == "1 protected value(s) unchanged"
    no_base = gate(run(None), "protected")
    assert no_base.status == "skipped" and no_base.summary == "no base revision to compare against"

    write(repo, {"consts.py": "C = 3e8\n"})
    changed = run("HEAD")
    assert changed.failed_gate == "protected"
    assert "- consts.py: C changed from 299792458.0 to 300000000.0" in changed.report
    assert "`ssw run --allow-protected C`" in changed.report
    allowed = run("HEAD", ("C",))
    assert allowed.passed and gate(allowed, "protected").details["changes"][0]["allowed"] is True

    write(repo, {"science.toml": "[[protected]]\nfile = 1\n"})  # broken; the base zone still holds
    broken = gate(run("HEAD"), "protected")
    assert broken.status == "failed"
    assert broken.details["notes"][0].startswith("working-tree science.toml ignored")


# --------------------------------------------------------------------------------------------
# Project commands
# --------------------------------------------------------------------------------------------


@needs_git
def test_changed_files_come_from_git(repo: Path, fake: Path, tmp_path: Path) -> None:
    assert commands.changed_files(repo, None) is None  # not a checkout yet
    git(repo, "init", "-q")
    write(repo, {".gitignore": "ignored.py\n", "a.py": "x = 1\n", "b.py": "", "keep.py": ""})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "b.py").unlink()
    write(repo, {"a.py": "x = 2\n", "new.py": "", "ignored.py": "", ".stepswitch/state.py": ""})
    # generated files (bytecode, tool caches) are not changes, as in a worktree's diff
    write(repo, {"__pycache__/a.cpython-312.pyc": "", "pkg/__pycache__/m.pyc": "", "x.pyc": ""})
    write(repo, {".pytest_cache/v/cache/lastfailed": "{}"})
    assert commands.changed_files(repo, "HEAD") == ["a.py", "b.py", "new.py"]
    assert commands.changed_files(repo, None) == ["a.py", "b.py", "new.py"]
    assert commands.changed_files(repo, "no-such-revision") is None
    assert commands.resolve_commit(repo, "--output=x") is None
    settings = config(gates=["format"], commands={"format": tool(fake, "format", 0, "{files}")})
    assert run_gates(repo, track="general", base="HEAD", config=settings).passed
    assert calls(tmp_path) == [["format", "a.py", "new.py"]]  # b.py is gone


def test_project_python_resolution(tmp_path: Path) -> None:
    repo = tmp_path
    assert commands.project_python(repo) == sys.executable
    venv = repo / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    write(venv.parent, {venv.name: ""})
    assert commands.project_python(repo) == str(venv)

    def resolved(rel: str) -> str:
        return str((repo / rel).resolve())

    both = Config.model_validate(
        {"tracks": {"general": {"python": "tools/py"}}, "science": {"python": "sci/py"}}
    )
    assert commands.project_python(repo, both, "general") == resolved("tools/py")
    assert commands.project_python(repo, both, "science", science_python="toml/py") == resolved(
        "sci/py"
    )
    assert commands.project_python(repo, None, "science", science_python="toml/py") == resolved(
        "toml/py"
    )
    first = Config.model_validate(
        {"tracks": {"science": {"python": "track/py"}}, "science": {"python": "sci/py"}}
    )
    assert commands.project_python(repo, first, "science") == resolved("track/py")
    science_only = Config.model_validate({"science": {"python": "sci/py"}})
    assert commands.project_python(repo, science_only, "general") == str(venv)
    absolute = str(tmp_path / "elsewhere" / "python")
    assert commands.project_python(repo, config(python=absolute)) == absolute
    assert commands.resolve_interpreter(repo, "surely-not-a-program") == "surely-not-a-program"


def test_project_env_puts_the_venv_first(tmp_path: Path) -> None:
    bindir = "Scripts" if sys.platform == "win32" else "bin"
    python = tmp_path / "venv" / bindir / ("python.exe" if sys.platform == "win32" else "python")
    write(tmp_path / "venv", {"pyvenv.cfg": "home = x\n", f"{bindir}/{python.name}": ""})
    env = commands.project_env(str(python), base={"PATH": "elsewhere"})
    assert env["VIRTUAL_ENV"] == str(tmp_path / "venv")
    assert env["PATH"] == str(python.parent) + os.pathsep + "elsewhere"
    assert env["PYTHONIOENCODING"] == "utf-8"
    plain = commands.project_env(
        "python", base={"PATH": "p", "PYTHONIOENCODING": "latin-1"}, extra={"X": "1"}
    )
    assert "VIRTUAL_ENV" not in plain and plain["PATH"] == "p"
    assert plain["PYTHONIOENCODING"] == "latin-1" and plain["X"] == "1"


def test_a_worktree_uses_the_environment_of_its_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "project"
    worktree = checkout / ".stepswitch" / "wt" / "4fe9ecc6"
    worktree.mkdir(parents=True)
    assert commands.source_checkout(worktree) == checkout
    assert commands.source_checkout(checkout) is None
    assert commands.source_checkout(tmp_path / "wt" / "x") is None
    assert commands.project_python(worktree) == sys.executable  # neither has a .venv
    venv = checkout / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    write(venv.parent, {venv.name: ""})
    assert commands.project_python(worktree) == str(venv)
    relative = config(python="tools/py")
    write(checkout / "tools", {"py": ""})
    assert commands.project_python(worktree, relative) == str((checkout / "tools/py").resolve())
    write(worktree / "tools", {"py": ""})  # the worktree's own copy wins
    assert commands.project_python(worktree, relative) == str((worktree / "tools/py").resolve())
    missing = config(python="bin/nowhere")
    assert commands.project_python(worktree, missing) == str((worktree / "bin/nowhere").resolve())


def test_project_env_puts_the_working_trees_src_first(tmp_path: Path) -> None:
    bindir = "Scripts" if sys.platform == "win32" else "bin"
    name = "python.exe" if sys.platform == "win32" else "python"
    checkout = tmp_path / "project"
    worktree = checkout / ".stepswitch" / "wt" / "4fe9ecc6"
    write(checkout / ".venv", {"pyvenv.cfg": "home = x\n", f"{bindir}/{name}": ""})
    python = str(checkout / ".venv" / bindir / name)
    (worktree / "src").mkdir(parents=True)
    src = str(worktree / "src")
    env = commands.project_env(python, repo=worktree, base={"PATH": "p", "PYTHONPATH": "lib"})
    assert env["PYTHONPATH"] == src + os.pathsep + "lib"
    assert env["PATH"] == str(checkout / ".venv" / bindir) + os.pathsep + "p"
    overrides = commands.project_overrides(python, worktree, base={"PATH": "p"})
    assert overrides == {
        "VIRTUAL_ENV": str(checkout / ".venv"),
        "PATH": str(checkout / ".venv" / bindir) + os.pathsep + "p",
        "PYTHONPATH": src,
    }
    # An interpreter inside the tree imports the tree's code already; no src/, nothing to do.
    (checkout / "src").mkdir()
    assert "PYTHONPATH" not in commands.project_env(python, repo=checkout, base={})
    assert "PYTHONPATH" not in commands.project_env(python, repo=tmp_path, base={})
    assert commands.project_overrides("python", worktree, base={}) == {"PYTHONPATH": src}


def test_project_env_activates_a_conda_environment(tmp_path: Path) -> None:
    # A conda environment has no pyvenv.cfg; its programs (pip, pytest, the interpreter
    # itself as `python`) must still come first on PATH.
    env = tmp_path / "miniconda3" / "envs" / "testbed"
    write(env, {"conda-meta/history": "", "bin/python": ""})
    python = str(env / "bin" / "python")
    overrides = commands.project_overrides(python, base={"PATH": "p"})
    assert overrides == {"CONDA_PREFIX": str(env), "PATH": str(env / "bin") + os.pathsep + "p"}
    # Windows keeps the interpreter at the environment's top level.
    windows = tmp_path / "conda-win"
    write(windows, {"conda-meta/history": "", "python.exe": ""})
    found = commands.project_overrides(str(windows / "python.exe"), base={"PATH": "p"})
    assert found["CONDA_PREFIX"] == str(windows)
    assert found["PATH"].split(os.pathsep) == [
        str(windows),
        str(windows / "Library" / "bin"),
        str(windows / "Scripts"),
        "p",
    ]
    assert commands.project_overrides(str(tmp_path / "bin" / "python"), base={"PATH": "p"}) == {}


def test_project_env_puts_a_flat_layout_worktree_first(tmp_path: Path) -> None:
    # A flat layout (the package at the top level, as in Django) installed in editable mode
    # from the checkout: a test runner started as a script imports the checkout's code unless
    # the task's worktree comes first.
    bindir = "Scripts" if sys.platform == "win32" else "bin"
    name = "python.exe" if sys.platform == "win32" else "python"
    checkout = tmp_path / "project"
    worktree = checkout / ".stepswitch" / "wt" / "4fe9ecc6"
    write(checkout / ".venv", {"pyvenv.cfg": "home = x\n", f"{bindir}/{name}": ""})
    write(worktree / "pkg", {"__init__.py": ""})
    python = str(checkout / ".venv" / bindir / name)
    env = commands.project_env(python, repo=worktree, base={"PATH": "p", "PYTHONPATH": "lib"})
    assert env["PYTHONPATH"] == str(worktree) + os.pathsep + "lib"
    assert commands.project_overrides("python", worktree, base={}) == {"PYTHONPATH": str(worktree)}
    # The checkout itself is what its editable install points at: nothing to do.
    write(checkout / "pkg", {"__init__.py": ""})
    assert "PYTHONPATH" not in commands.project_env(python, repo=checkout, base={})
    assert "PYTHONPATH" not in commands.project_env("python", repo=checkout, base={})


def test_tool_commands_prefer_the_project_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = commands.Tools("py", tmp_path, {})
    monkeypatch.setattr(commands.Tools, "has", lambda self, module: module == "ruff")
    monkeypatch.setattr(commands.Tools, "which", lambda self, program: f"/bin/{program}")
    assert tools.command("ruff") == ["py", "-m", "ruff"]
    assert tools.command("mypy") == ["/bin/mypy", "--python-executable", "py"]
    assert tools.command("pytest") is None  # another environment's pytest cannot see the project
    assert tools.command("jupytext") == ["/bin/jupytext"]


def test_probe_reports_tool_modules_and_is_cached(tmp_path: Path) -> None:
    env = commands.project_env(sys.executable)
    info = commands.probe_interpreter(sys.executable, cwd=tmp_path, env=env)
    assert info["python"] == platform.python_version()
    assert info["modules"]["pytest"] is True
    assert info["modules"]["testmon"] is (importlib.util.find_spec("testmon") is not None)
    assert info["packages"] > 0 and len(info["packages_sha256"]) == 64
    assert info["versions"]["numpy"] == np.__version__
    assert commands.probe_interpreter(sys.executable, cwd=tmp_path, env=env) is info
    broken = commands.probe_interpreter(str(tmp_path / "no-python"), cwd=tmp_path, env=env)
    assert broken["error"].startswith("cannot run the project interpreter")
    assert commands.Tools(str(tmp_path / "no-python"), tmp_path, env).error is not None


def test_run_reports_exit_codes_and_never_raises(tmp_path: Path) -> None:
    missing = commands.run(["surely-not-a-program"], cwd=tmp_path, timeout=30)
    assert missing.returncode == commands.NOT_STARTED and not missing.started and not missing.ok
    assert missing.start_error.startswith("cannot run surely-not-a-program")
    assert commands.run([], cwd=tmp_path, timeout=30).returncode == commands.NOT_STARTED
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    done = commands.run([sys.executable, "-c", code], cwd=tmp_path, timeout=60)
    assert (done.returncode, done.stdout, done.stderr) == (3, "out\n", "err\n")
    assert done.started and not done.ok and done.output.split() == ["out", "err"]
    shell = commands.run(
        f'{commands.shell_quote(sys.executable)} -c "print(7)"',
        cwd=tmp_path,
        timeout=60,
        shell=True,
    )
    assert shell.ok and shell.stdout.strip() == "7"


CHILD = """\
import sys, time
while True:
    with open(sys.argv[1], "a") as beat:
        beat.write(".")
    time.sleep(0.05)
"""
PARENT = """\
import os, subprocess, sys, time
subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]])
while not os.path.exists(sys.argv[2]):
    time.sleep(0.05)
print("child running", flush=True)
time.sleep(120)
"""


def test_a_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    beat = tmp_path / "beat.txt"
    start = time.monotonic()
    result = commands.run([sys.executable, "-c", PARENT, CHILD, str(beat)], cwd=tmp_path, timeout=4)
    assert result.timed_out and result.returncode == commands.TIMED_OUT and not result.ok
    assert time.monotonic() - start < 30
    assert "child running" in result.stdout
    assert result.stderr.endswith("timed out after 4 s; the process tree was killed")
    time.sleep(0.5)
    size = beat.stat().st_size
    time.sleep(0.6)
    assert beat.stat().st_size == size  # the grandchild stopped too


def test_expand_and_distill() -> None:
    values = {"python": "/py", "repo": "/r"}
    assert commands.expand(["{python}", "--root={repo}/src", "{other}", "{"], values) == [
        "/py",
        "--root=/r/src",
        "{other}",
        "{",
    ]
    keep = re.compile(r"^E ")
    text = "\n".join([f"line {i}" for i in range(50)] + ["E boom", "E boom", "E bang"])
    assert commands.distill(text, keep) == "E boom\nE bang"  # repeated lines collapse
    assert commands.distill("a\n\nb\nc\n", keep, tail=2) == "b\nc"  # no match: the tail
    many = "\n".join(f"E failure {i}" for i in range(400))
    short = commands.distill(many, keep, limit=300)
    assert len(short) <= 300 and "\n...\n" in short
    assert short.startswith("E failure 0\n") and short.endswith("E failure 399")
    assert commands.strip_ansi("\x1b[31mred\x1b[0m") == "red"
