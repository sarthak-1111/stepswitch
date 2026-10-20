"""Failures that already happen at the base commit (ADR 0042): the agent's `test` tool and the
test gates name them apart, and never pass because of them, since a task is often to fix such a
test. A large suite without a selector runs only the impacted tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from stepswitch.config import Config
from stepswitch.context.proc import minimal_env
from stepswitch.verify import baseline, commands
from stepswitch.verify.gates import run_gates
from stepswitch.workers import tools as T

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
CALC = "def add(a, b):\n    return a + b\n"
TESTS = """\
import os

from calc import add


def test_add():
    assert add(2, 3) == 5


def test_needs_a_service():
    assert os.environ.get("SURELY_NOT_SET_SERVICE_URL"), "no service in this environment"
"""


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run([*GIT, *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture
def task(tmp_path: Path) -> tuple[Path, str]:
    """A checkout whose suite has one test failing at the base commit, and a task worktree."""
    checkout = tmp_path / "project"
    (checkout / "tests").mkdir(parents=True)
    (checkout / "calc.py").write_text(CALC, encoding="utf-8")
    (checkout / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8")
    (checkout / "tests" / "test_other.py").write_text("def test_other():\n    pass\n", "utf-8")
    git(checkout, "init", "-q")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "base")
    base = git(checkout, "rev-parse", "HEAD")
    worktree = checkout / ".stepswitch" / "wt" / "4fe9ecc6"
    git(checkout, "worktree", "add", "-q", "--detach", str(worktree), base)
    return worktree, base


def toolbox(worktree: Path, base: str | None) -> T.Toolbox:
    python = sys.executable
    return T.Toolbox(
        worktree,
        test_command=[python, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        env=commands.project_overrides(python, worktree, base=minimal_env()),
        base=base,
        base_env=lambda path: commands.project_overrides(python, path, base=minimal_env()),
    )


def test_the_test_tool_tells_new_failures_from_old_ones(task: tuple[Path, str]) -> None:
    worktree, base = task
    service = "tests/test_calc.py::test_needs_a_service"
    box = toolbox(worktree, base)
    (worktree / "calc.py").write_text(CALC.replace("a + b", "a - b"), encoding="utf-8")
    broken = box.execute("test", {"selector": "tests/test_calc.py"})
    assert broken.data["ok"] is False
    assert broken.data["failing"] == ["tests/test_calc.py::test_add"]
    assert broken.data["preexisting"] == [service]
    assert "failing since your change:\n  tests/test_calc.py::test_add" in broken.text
    assert f"failing at the base commit too, before your change:\n  {service}" in broken.text
    checkout = worktree.parent / f"{worktree.name}{baseline.SUFFIX}"
    assert (checkout / "calc.py").read_text(encoding="utf-8") == CALC  # the base commit

    (worktree / "calc.py").write_text(CALC, encoding="utf-8")
    fixed = box.execute("test", {"selector": "tests/test_calc.py"})
    assert fixed.data["ok"] is False and fixed.data["failing"] == []  # still a failing test
    assert fixed.data["preexisting"] == [service]
    assert "every failing test also fails at the base commit, before your change" in fixed.text

    baseline.release(worktree)
    assert not checkout.exists()


def test_without_a_base_failures_are_reported_as_before(task: tuple[Path, str]) -> None:
    worktree, _ = task
    found = toolbox(worktree, None).execute("test", {"selector": "tests/test_calc.py"})
    assert found.data["ok"] is False and "preexisting" not in found.data
    assert found.data["failing"] == ["tests/test_calc.py::test_needs_a_service"]


def test_a_test_gate_names_old_failures_and_still_fails(task: tuple[Path, str]) -> None:
    worktree, base = task
    settings = {"gates": ["full_tests"], "python": sys.executable}
    config = Config.model_validate({"tracks": {"general": settings}})
    (worktree / "calc.py").write_text(CALC + "\n# a comment\n", encoding="utf-8")
    verdict = run_gates(worktree, track="general", base=base, config=config)
    assert not verdict.passed
    result = verdict.results[0]
    assert "1 of the failing tests also fail at the base commit" in result.summary
    assert result.details["preexisting"] == ["tests/test_calc.py::test_needs_a_service"]
    assert result.details["new_failures"] == []

    (worktree / "calc.py").write_text(CALC.replace("a + b", "a * b"), encoding="utf-8")
    failed = run_gates(worktree, track="general", base=base, config=config)
    assert not failed.passed
    assert failed.results[0].details["new_failures"] == ["tests/test_calc.py::test_add"]
    baseline.release(worktree)


def test_an_unfixed_bug_is_never_passed_over(tmp_path: Path) -> None:
    # The task is to fix a test that fails at the base commit; a change that leaves it failing
    # must fail, though the failure is old (the case ADR 0042 corrected).
    checkout = tmp_path / "project"
    (checkout / "tests").mkdir(parents=True)
    (checkout / "calc.py").write_text(CALC.replace("a + b", "a - b"), encoding="utf-8")
    (checkout / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8")
    git(checkout, "init", "-q")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "base")
    base = git(checkout, "rev-parse", "HEAD")
    worktree = checkout / ".stepswitch" / "wt" / "5ab0c7d1"
    git(checkout, "worktree", "add", "-q", "--detach", str(worktree), base)
    (worktree / "calc.py").write_text(CALC.replace("a + b", "b - a"), encoding="utf-8")
    found = toolbox(worktree, base).execute("test", {"selector": "tests/test_calc.py"})
    assert found.data["ok"] is False
    assert "tests/test_calc.py::test_add" in found.data["preexisting"]
    settings = {"gates": ["full_tests"], "python": sys.executable}
    config = Config.model_validate({"tracks": {"general": settings}})
    assert not run_gates(worktree, track="general", base=base, config=config).passed
    baseline.release(worktree)


def test_a_large_suite_without_a_selector_runs_the_impacted_tests(
    task: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree, base = task
    monkeypatch.setattr(T, "LARGE_SUITE", 1)
    box = toolbox(worktree, base)
    nothing = box.execute("test", {})
    assert nothing.is_error and "give a selector" in nothing.text
    assert "no changed files" in nothing.text
    (worktree / "calc.py").write_text(CALC + "\n", encoding="utf-8")
    impacted = box.execute("test", {})
    assert impacted.text.startswith("no selector: running the 1 test file(s) that import")
    assert impacted.data["argv"][-1] == "tests/test_calc.py"
    # A runner that stops before any test (django's without a GIS backend, ADR 0042) is named,
    # and the agent is told to choose the tests itself.
    aborting = T.Toolbox(
        worktree,
        test_command=[sys.executable, "-c", "print('Aborting: no backend'); raise SystemExit(1)"],
        base=base,
    )
    stopped = aborting.execute("test", {})
    assert "no test ran: the test runner stopped before running any test" in stopped.text
    assert "give a selector (one test file or test id)" in stopped.text
    assert "Aborting: no backend" in stopped.text
    baseline.release(worktree)
