"""The built-in bench suites (SPEC §16): schema, bundled repositories, golden patches.

mini-general holds general-track tasks; mini-science holds science-track tasks on synthetic
numerical code (SPEC §6.2); dev holds tuning tasks of both tracks on repositories that no mini
suite uses (ADR 0037). Every check runs for every suite, and every task is checked end to end.
In a git-initialized copy of its repository at least one acceptance command fails; after
`git apply` of the golden patch every acceptance command passes. Acceptance commands run with
the interpreter running these tests ("python" means the project's interpreter), without
bytecode or pytest cache files, so nothing is written into the source tree.

Science-track repositories also carry a science.toml (docs/science-toml.md). Its golden cases
exist, fail before the golden patch and pass after it, and the golden patch leaves the
protected values alone. Editing a protected value instead of the code, the tempting wrong fix
listed in WRONG_FIXES, fails the protected check and the acceptance commands.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from ruamel.yaml import YAML, YAMLError

from stepswitch.verify import science

ROOT = Path(__file__).resolve().parents[2]
SUITES_DIR = ROOT / "bench" / "suites"
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


@dataclass(frozen=True)
class SuiteRules:
    track: str | None  # None: general and science tasks both belong
    min_tasks: int
    max_tasks: int | None = None  # None: no upper limit


TRACKS = {"general", "science"}
# SPEC §16: mini-general has 10-20 small tasks; mini-science has synthetic numerical tasks, at
# least 5 of them (scripts/phase_check.py, Phase 3). dev holds the tuning tasks (ADR 0037).
SUITES = {
    "mini-general": SuiteRules(track="general", min_tasks=10, max_tasks=20),
    "mini-science": SuiteRules(track="science", min_tasks=5),
    "dev": SuiteRules(track=None, min_tasks=4, max_tasks=12),
}

# Tempting wrong fixes: editing a protected value instead of the code that misuses it.
# (task id, file, text before, text after)
WRONG_FIXES = [
    pytest.param(
        "wavenumber-to-wavelength",
        "spectro/constants.py",
        "NM_PER_M = 1e9",
        "NM_PER_M = 1e7",
        id="wavenumber-to-wavelength",
    ),
    pytest.param(
        "voigt-lorentzian-width",
        "widths/constants.py",
        "VOIGT_A = 0.5346",
        "VOIGT_A = 1.0692",
        id="voigt-lorentzian-width",
    ),
]


def suite_file(suite: str) -> Path:
    return SUITES_DIR / f"{suite}.yaml"


def load_suite(suite: str) -> dict[str, Any]:
    data = YAML(typ="safe", pure=True).load(suite_file(suite).read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def suite_tasks(suite: str) -> list[dict[str, Any]]:
    """The tasks to parametrize over; an unreadable suite gives none and fails the schema test."""
    try:
        tasks = load_suite(suite).get("tasks")
    except (OSError, YAMLError):
        return []
    return [task for task in tasks if isinstance(task, dict)] if isinstance(tasks, list) else []


TASKS = {suite: suite_tasks(suite) for suite in SUITES}
CASES = [(suite, task) for suite, tasks in TASKS.items() for task in tasks]
CASE_IDS = [f"{suite}/{task.get('id')}" for suite, task in CASES]
SCIENCE = [(suite, task) for suite, task in CASES if task.get("track") == "science"]
SCIENCE_IDS = [f"{suite}/{task.get('id')}" for suite, task in SCIENCE]


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
# Suite files
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("suite", list(SUITES))
def test_suite_file_schema(suite: str) -> None:
    rules = SUITES[suite]
    data = load_suite(suite)
    assert data.get("schema") == 1
    assert data.get("name") == suite
    assert isinstance(data.get("description"), str) and data["description"].strip()
    tasks = data.get("tasks")
    assert isinstance(tasks, list)
    count = len(tasks)
    if rules.max_tasks is None:
        wanted, enough = f"at least {rules.min_tasks}", count >= rules.min_tasks
    else:
        wanted = f"{rules.min_tasks}-{rules.max_tasks}"
        enough = rules.min_tasks <= count <= rules.max_tasks
    assert enough, f"{suite} needs {wanted} tasks, found {count}"
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
        if task["track"] not in TRACKS or rules.track not in (None, task["track"]):
            problems.append(f"{name}: track must be {rules.track or ' or '.join(sorted(TRACKS))}")
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


def test_the_dev_suite_shares_no_repository_with_the_mini_suites() -> None:
    # Tuning on dev must not tune on the tasks the claims are measured on (ADR 0037).
    measured = {task.get("repo") for suite, task in CASES if suite.startswith("mini-")}
    shared = sorted({str(task.get("repo")) for task in TASKS["dev"]} & measured)
    assert TASKS["dev"] and not shared, f"dev shares repositories with the mini suites: {shared}"


def test_task_ids_are_unique_across_suites() -> None:
    # Golden patches of every suite share bench/golden/<id>.patch.
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for suite, task in CASES:
        task_id = str(task.get("id"))
        if task_id in seen and seen[task_id] != suite:
            clashes.append(f"{task_id} ({seen[task_id]} and {suite})")
        seen.setdefault(task_id, suite)
    assert not clashes, f"task ids used by more than one suite: {clashes}"


# --------------------------------------------------------------------------------------------
# Bundled repositories and golden patches
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("suite", list(SUITES))
def test_repositories_and_golden_patches_exist(suite: str) -> None:
    assert TASKS[suite], f"no tasks loaded from {suite_file(suite)}"
    problems: list[str] = []
    for task in TASKS[suite]:
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


@pytest.mark.parametrize("suite", list(SUITES))
def test_repositories_hold_no_generated_files(suite: str) -> None:
    found: list[str] = []
    for repo in sorted({repo_dir(task) for task in TASKS[suite]}):
        for path in repo.rglob("*"):
            rel = path.relative_to(repo)
            if is_generated(path.name) and not any(is_generated(p) for p in rel.parts[:-1]):
                found.append(f"{repo.relative_to(ROOT).as_posix()}/{rel.as_posix()}")
    assert not found, f"generated files in bundled repositories (delete them): {found}"


@pytest.mark.parametrize("suite", list(SUITES))
def test_repository_files_are_lf_and_carry_no_hint_markers(suite: str) -> None:
    problems: list[str] = []
    for repo in sorted({repo_dir(task) for task in TASKS[suite]}):
        for path in sorted(p for p in repo.rglob("*") if p.is_file()):
            rel = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            if b"\r" in data:
                problems.append(f"{rel}: CR line endings")
            if HINT_MARKER_RE.search(data.decode("utf-8", "replace")):
                problems.append(f"{rel}: TODO-style marker")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("suite", list(SUITES))
def test_golden_patches_are_git_diffs_that_leave_tests_alone(suite: str) -> None:
    problems: list[str] = []
    for task in TASKS[suite]:
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


def project_env() -> dict[str, str]:
    """This environment without PYTHONPATH and pytest settings, writing no bytecode files."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH" and not key.startswith("PYTEST_")
    }
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return env


def run_acceptance(command: str, repo: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        acceptance_argv(command),
        cwd=repo,
        env=project_env(),
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


def task_checkout(task: dict[str, Any], tmp_path: Path) -> Path:
    """A git repository holding a copy of the task's repository as its only commit."""
    repo = tmp_path / "repo"
    shutil.copytree(repo_dir(task), repo, ignore=shutil.ignore_patterns(*GENERATED_DIRS, "*.pyc"))
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Task starting point")
    return repo


def apply_golden_patch(task: dict[str, Any], repo: Path) -> None:
    patch = golden_patch(task).read_bytes()
    git(repo, "apply", "--check", "-", stdin=patch)
    git(repo, "apply", "-", stdin=patch)


def report(command: str, result: subprocess.CompletedProcess[bytes]) -> str:
    lines = (result.stdout + result.stderr).decode("utf-8", "replace").strip().splitlines()
    return f"$ {command} -> exit {result.returncode}\n" + "\n".join(lines[-40:])


@pytest.mark.skipif(GIT is None, reason="git is not installed")
@pytest.mark.parametrize(("suite", "task"), CASES, ids=CASE_IDS)
def test_golden_patch_makes_failing_acceptance_pass(
    suite: str, task: dict[str, Any], tmp_path: Path
) -> None:
    repo = task_checkout(task, tmp_path)

    commands: list[str] = task["acceptance"]
    before = [run_acceptance(command, repo) for command in commands]
    assert any(result.returncode != 0 for result in before), (
        f"{suite}/{task['id']}: every acceptance command already passes before the golden patch:\n"
        + "\n".join(report(c, r) for c, r in zip(commands, before, strict=True))
    )

    apply_golden_patch(task, repo)

    after = [run_acceptance(command, repo) for command in commands]
    failures = [report(c, r) for c, r in zip(commands, after, strict=True) if r.returncode != 0]
    assert not failures, (
        f"{suite}/{task['id']}: acceptance fails after the golden patch:\n" + "\n".join(failures)
    )


# --------------------------------------------------------------------------------------------
# Science track: golden cases and protected values (science.toml)
# --------------------------------------------------------------------------------------------


def load_science(repo: Path) -> science.ScienceConfig:
    config = science.load_science(repo)
    assert config is not None, f"{repo.name}: a science-track repository needs a science.toml"
    return config


def run_golden(repo: Path) -> science.GoldenReport:
    """The golden cases, run by the interpreter running these tests."""
    return science.run_golden(
        repo, load_science(repo), python=sys.executable, env=project_env(), timeout=COMMAND_TIMEOUT
    )


def protected_values(path: Path, names: list[str]) -> set[str]:
    """The names among `names` that `path` binds (Python) or holds (JSON, TOML)."""
    data = path.read_bytes()
    if path.suffix == ".py":
        return set(science.python_values(data, names))
    return set(science.document_values(data, path.suffix, names))


@pytest.mark.parametrize(("suite", "task"), SCIENCE, ids=SCIENCE_IDS)
def test_science_toml_names_golden_cases_and_protected_values_that_exist(
    suite: str, task: dict[str, Any]
) -> None:
    repo = repo_dir(task)
    config = load_science(repo)
    problems: list[str] = []
    if not config.golden:
        problems.append("science.toml declares no golden cases")
    for case in config.golden:
        case_dir = repo / science.GOLDEN_DIR / case.case
        if not (case_dir / "input.json").is_file():
            problems.append(f"{science.GOLDEN_DIR}/{case.case}/input.json is missing")
        if not case.quantities:
            problems.append(f"golden case {case.case} compares no quantities")
        for quantity in case.quantities:
            if not (case_dir / quantity.expected_path).is_file():
                problems.append(f"{case.case}/{quantity.name}: {quantity.expected_path} is missing")
    for zone in config.protected:
        path = repo / zone.file
        if not path.is_file():
            problems.append(f"protected file {zone.file} is missing")
            continue
        missing = sorted(set(zone.names) - protected_values(path, zone.names))
        if missing:
            problems.append(f"{zone.file} holds no protected value named {missing}")
    assert not problems, f"{suite}/{task['id']}:\n" + "\n".join(problems)


@pytest.mark.skipif(GIT is None, reason="git is not installed")
@pytest.mark.parametrize(("suite", "task"), SCIENCE, ids=SCIENCE_IDS)
def test_golden_cases_fail_before_the_golden_patch_and_pass_after_it(
    suite: str, task: dict[str, Any], tmp_path: Path
) -> None:
    repo = task_checkout(task, tmp_path)

    before = run_golden(repo)
    assert before.status == "failed", (
        f"{suite}/{task['id']}: the golden cases do not fail before the golden patch "
        f"({before.status}): {before.summary()}"
    )

    apply_golden_patch(task, repo)

    after = run_golden(repo)
    assert after.status == "passed", (
        f"{suite}/{task['id']}: the golden cases fail after the golden patch:\n"
        + after.failure_report()
    )
    protected = science.check_protected(repo, "HEAD", load_science(repo))
    assert protected.status in {"passed", "skipped"}, (
        f"{suite}/{task['id']}: the golden patch changes protected values:\n" + protected.report()
    )


@pytest.mark.skipif(GIT is None, reason="git is not installed")
@pytest.mark.parametrize(("task_id", "file", "before", "after"), WRONG_FIXES)
def test_tempting_wrong_fixes_fail_the_protected_check(
    task_id: str, file: str, before: str, after: str, tmp_path: Path
) -> None:
    tasks = [task for _, task in SCIENCE if task.get("id") == task_id]
    assert tasks, f"no science-track task {task_id}"
    task = tasks[0]
    repo = task_checkout(task, tmp_path)
    path = repo / file
    text = path.read_bytes().decode("utf-8")
    assert text.count(before) == 1, f"{file} does not hold {before!r} exactly once"
    path.write_bytes(text.replace(before, after).encode("utf-8"))

    protected = science.check_protected(repo, "HEAD", load_science(repo))
    assert protected.status == "failed", protected.summary()
    assert [change["file"] for change in protected.disallowed] == [file]

    results = [run_acceptance(command, repo) for command in task["acceptance"]]
    assert any(result.returncode != 0 for result in results), (
        f"{task_id}: the acceptance commands pass with a changed protected value"
    )
