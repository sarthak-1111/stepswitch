"""The built-in mini-general bench suite (SPEC §16): schema, bundled repositories, golden patches.

Every task is checked end to end. In a git-initialized copy of its repository at least one
acceptance command fails; after `git apply` of the golden patch every acceptance command passes.
Acceptance commands run with the interpreter running these tests ("python" means the project's
interpreter), without bytecode or pytest cache files, so nothing is written into the source tree.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from ruamel.yaml import YAML, YAMLError

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "bench" / "suites" / "mini-general.yaml"
REPOS = "bench/repos"
GOLDEN = "bench/golden"
REQUIRED_KEYS = {"id", "repo", "instruction", "acceptance", "golden", "track", "tags"}
KINDS = {"bugfix", "feature", "refactor", "config", "docs"}
DIFFICULTIES = {"d1", "d2", "d3"}
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
GENERATED_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".tox",
    ".venv",
    "build",
    "dist",
    "htmlcov",
}
GENERATED_SUFFIXES = {".pyc", ".pyo", ".orig", ".rej", ".egg-info"}
HINT_MARKER_RE = re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b")
PATCHED_FILE_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.MULTILINE)
GIT = shutil.which("git")
COMMAND_TIMEOUT = 120.0


def load_suite() -> dict[str, Any]:
    data = YAML(typ="safe", pure=True).load(SUITE.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def suite_tasks() -> list[dict[str, Any]]:
    """The tasks to parametrize over; an unreadable suite gives none and fails the schema test."""
    try:
        tasks = load_suite().get("tasks")
    except (OSError, YAMLError):
        return []
    return [task for task in tasks if isinstance(task, dict)] if isinstance(tasks, list) else []


TASKS = suite_tasks()


def checkout_path(value: Any) -> Path | None:
    """A path from the suite file: relative to the checkout root, POSIX style, inside it."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return None
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    return ROOT.joinpath(*rel.parts)


def repo_dir(task: dict[str, Any]) -> Path:
    path = checkout_path(task.get("repo"))
    assert path is not None, f"{task.get('id')}: bad repo path {task.get('repo')!r}"
    return path


def golden_patch(task: dict[str, Any]) -> Path:
    path = checkout_path(task.get("golden"))
    assert path is not None, f"{task.get('id')}: bad golden path {task.get('golden')!r}"
    return path


def is_generated(name: str) -> bool:
    return name in GENERATED_DIRS or any(name.endswith(suffix) for suffix in GENERATED_SUFFIXES)


# --------------------------------------------------------------------------------------------
# Suite file
# --------------------------------------------------------------------------------------------


def test_suite_file_schema() -> None:
    data = load_suite()
    assert data.get("schema") == 1
    assert data.get("name") == "mini-general"
    assert isinstance(data.get("description"), str) and data["description"].strip()
    tasks = data.get("tasks")
    assert isinstance(tasks, list)
    assert 10 <= len(tasks) <= 20, f"SPEC §16 asks for 10-20 tasks, found {len(tasks)}"
    assert all(isinstance(task, dict) for task in tasks)
    ids = [str(task.get("id")) for task in tasks]
    duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
    assert not duplicates, f"duplicate task ids: {duplicates}"

    problems: list[str] = []
    for task in tasks:
        name = str(task.get("id", "?"))
        missing = REQUIRED_KEYS - set(task)
        if missing:
            problems.append(f"{name}: missing keys {sorted(missing)}")
            continue
        if not isinstance(task["id"], str) or not ID_RE.match(task["id"]):
            problems.append(f"{name}: id must be kebab-case")
        if task["track"] != "general":
            problems.append(f"{name}: track must be 'general'")
        if not isinstance(task["instruction"], str) or not task["instruction"].strip():
            problems.append(f"{name}: empty instruction")
        acceptance = task["acceptance"]
        if not (
            isinstance(acceptance, list)
            and acceptance
            and all(isinstance(command, str) and command.strip() for command in acceptance)
        ):
            problems.append(f"{name}: acceptance must be a non-empty list of command strings")
        tags = task["tags"]
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            problems.append(f"{name}: tags must be a list of strings")
            continue
        if len(KINDS & set(tags)) != 1:
            problems.append(f"{name}: tags need exactly one kind out of {sorted(KINDS)}")
        if len(DIFFICULTIES & set(tags)) != 1:
            problems.append(f"{name}: tags need exactly one difficulty: d1, d2 or d3")
        if not str(task["repo"]).startswith(f"{REPOS}/"):
            problems.append(f"{name}: repo must live under {REPOS}/")
        if task["golden"] != f"{GOLDEN}/{task['id']}.patch":
            problems.append(f"{name}: golden must be {GOLDEN}/{task['id']}.patch")
    assert not problems, "\n".join(problems)


# --------------------------------------------------------------------------------------------
# Bundled repositories and golden patches
# --------------------------------------------------------------------------------------------


def test_repositories_and_golden_patches_exist() -> None:
    assert TASKS, f"no tasks loaded from {SUITE}"
    problems: list[str] = []
    for task in TASKS:
        repo, golden = repo_dir(task), golden_patch(task)
        if not (repo / "tests").is_dir():
            problems.append(f"{task['id']}: {task['repo']}/tests is missing")
        if not (repo / "pyproject.toml").is_file():
            problems.append(f"{task['id']}: {task['repo']}/pyproject.toml is missing")
        if not golden.is_file():
            problems.append(f"{task['id']}: {task['golden']} is missing")
        if golden.is_relative_to(repo):
            problems.append(f"{task['id']}: the golden patch must live outside the repository")
    assert not problems, "\n".join(problems)


def test_repositories_hold_no_generated_files() -> None:
    found: list[str] = []
    for repo in sorted({repo_dir(task) for task in TASKS}):
        for path in repo.rglob("*"):
            rel = path.relative_to(repo)
            if is_generated(path.name) and not any(is_generated(p) for p in rel.parts[:-1]):
                found.append(f"{repo.relative_to(ROOT).as_posix()}/{rel.as_posix()}")
    assert not found, f"generated files in bundled repositories (delete them): {found}"


def test_repository_files_are_lf_and_carry_no_hint_markers() -> None:
    problems: list[str] = []
    for repo in sorted({repo_dir(task) for task in TASKS}):
        for path in sorted(p for p in repo.rglob("*") if p.is_file()):
            rel = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            if b"\r" in data:
                problems.append(f"{rel}: CR line endings")
            if HINT_MARKER_RE.search(data.decode("utf-8", "replace")):
                problems.append(f"{rel}: TODO-style marker")
    assert not problems, "\n".join(problems)


def test_golden_patches_are_git_diffs_that_leave_tests_alone() -> None:
    problems: list[str] = []
    for task in TASKS:
        data = golden_patch(task).read_bytes()
        files = PATCHED_FILE_RE.findall(data.decode("utf-8"))
        if b"\r" in data or not data.endswith(b"\n"):
            problems.append(f"{task['id']}: patch must use LF line endings and end with one")
        if not files:
            problems.append(f"{task['id']}: patch holds no `diff --git a/... b/...` sections")
        touched = {path for pair in files for path in pair}
        if any(path == "tests" or path.startswith("tests/") for path in touched):
            problems.append(f"{task['id']}: the golden patch must not change tests/")
    assert not problems, "\n".join(problems)


# --------------------------------------------------------------------------------------------
# End to end: failing before the golden patch, passing after it
# --------------------------------------------------------------------------------------------


def acceptance_argv(command: str) -> list[str]:
    """Shell-free argv for an acceptance command, run by the current interpreter."""
    argv = shlex.split(command)
    if argv and argv[0] in {"pytest", "py.test"}:
        argv[:1] = ["python", "-m", "pytest"]
    if argv and argv[0] == "python":
        argv[0] = sys.executable
    if argv[1:3] == ["-m", "pytest"]:
        argv[3:3] = ["-p", "no:cacheprovider"]
    return argv


def run_acceptance(command: str, repo: Path) -> subprocess.CompletedProcess[bytes]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH" and not key.startswith("PYTEST_")
    }
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return subprocess.run(
        acceptance_argv(command),
        cwd=repo,
        env=env,
        capture_output=True,
        timeout=COMMAND_TIMEOUT,
        check=False,
    )


def git(repo: Path, *args: str, stdin: bytes | None = None) -> None:
    """Run git with no system or user configuration, so hooks, signing and EOL rules stay out."""
    assert GIT is not None
    empty_config = repo.parent / "empty.gitconfig"
    empty_config.touch()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(empty_config))
    identity = ["-c", "user.name=Bench", "-c", "user.email=bench@example.invalid"]
    result = subprocess.run(
        [GIT, *identity, *args],
        cwd=repo,
        env=env,
        input=stdin,
        capture_output=True,
        timeout=COMMAND_TIMEOUT,
        check=False,
    )
    output = (result.stdout + result.stderr).decode("utf-8", "replace")
    assert result.returncode == 0, f"git {' '.join(args)} failed:\n{output}"


def report(command: str, result: subprocess.CompletedProcess[bytes]) -> str:
    lines = (result.stdout + result.stderr).decode("utf-8", "replace").strip().splitlines()
    return f"$ {command} -> exit {result.returncode}\n" + "\n".join(lines[-40:])


@pytest.mark.skipif(GIT is None, reason="git is not installed")
@pytest.mark.parametrize("task", TASKS, ids=[str(task.get("id")) for task in TASKS])
def test_golden_patch_makes_failing_acceptance_pass(task: dict[str, Any], tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(repo_dir(task), repo, ignore=shutil.ignore_patterns(*GENERATED_DIRS, "*.pyc"))
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Task starting point")

    commands: list[str] = task["acceptance"]
    before = [run_acceptance(command, repo) for command in commands]
    assert any(result.returncode != 0 for result in before), (
        f"{task['id']}: every acceptance command already passes before the golden patch:\n"
        + "\n".join(report(c, r) for c, r in zip(commands, before, strict=True))
    )

    patch = golden_patch(task).read_bytes()
    git(repo, "apply", "--check", "-", stdin=patch)
    git(repo, "apply", "-", stdin=patch)

    after = [run_acceptance(command, repo) for command in commands]
    failures = [report(c, r) for c, r in zip(commands, after, strict=True) if r.returncode != 0]
    assert not failures, f"{task['id']}: acceptance fails after the golden patch:\n" + "\n".join(
        failures
    )
