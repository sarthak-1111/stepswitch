"""Test impact analysis (SPEC §8.2): which tests to run first for a set of changed files.

Selection modes, in order of preference:

* `testmon`: the project environment has pytest-testmon. pytest runs with `--testmon` and
  `TESTMON_DATAFILE` pointing at one data file per worktree (`.stepswitch/testmondata`), which
  the worktree pool may seed from a baseline. Cloud storage (`--tmnet`) is never used, and
  testmon is not used at all when the project's pytest configuration mentions it.
* `mapped`: a static map. Every Python file in the project is parsed for imports; a test file
  is selected when it imports a changed module directly or through other project modules, when
  it is itself changed, when a changed `conftest.py` applies to it, or when it is named after a
  changed module (`test_<module>.py`, `<module>_test.py`).
* `full`: the whole suite. Forced by any change to a non-Python file (data, configs,
  science.toml, `tests/golden/**`, lockfiles), since neither testmon nor the import map sees
  those, and used when the changed files are unknown.
* `none`: nothing to run (no tests, no changes, or no test reaches a changed module).
"""

from __future__ import annotations

import ast
import os
import re
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from stepswitch import paths
from stepswitch.verify import commands

SelectionMode = Literal["testmon", "mapped", "full", "none"]
TESTMON_DATAFILE_ENV = "TESTMON_DATAFILE"
TESTMON_DATAFILE_NAME = "testmondata"
SKIP_DIRS = commands.SKIP_DIRS
PYTEST_CONFIG_FILES = ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")
_TMNET_RE = re.compile(r"--tmnet\b")


@dataclass
class Selection:
    """Tests to run for a change: `args` go after the test command."""

    mode: SelectionMode
    args: list[str]
    reason: str
    env: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------------------------
# Project files
# --------------------------------------------------------------------------------------------


def is_test_file(rel: str) -> bool:
    """pytest's default `python_files`: test_*.py and *_test.py."""
    name = rel.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _walk_python_files(repo: Path) -> list[str]:
    found: list[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        base = Path(root)
        for name in files:
            if name.endswith(".py"):
                found.append((base / name).relative_to(repo).as_posix())
    return sorted(found)


def python_files(repo: Path) -> list[str]:
    """Project Python files as relative POSIX paths: git's view when available, else a walk."""
    done = commands.git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if done is None or done.returncode != 0:
        return _walk_python_files(repo)
    return sorted(
        rel
        for rel in done.stdout.decode("utf-8", "replace").split("\0")
        if rel.endswith(".py")
        and not set(rel.split("/")[:-1]) & SKIP_DIRS
        and (repo / rel).is_file()
    )


def has_tests(repo: Path, files: Sequence[str] | None = None) -> bool:
    """Python files under `tests/` or `test/`, or any file named like a pytest test module.

    A `tests/` directory holding only golden data does not count.
    """
    listed = files if files is not None else python_files(repo)
    return any(is_test_file(rel) or rel.startswith(("tests/", "test/")) for rel in listed)


def testmon_datafile(repo: Path, state_dir: Path | None = None) -> Path:
    """The worktree's own testmon data file (the pool may seed it from a baseline)."""
    return (state_dir or paths.state_dir(repo)) / TESTMON_DATAFILE_NAME


def tmnet_configured(repo: Path) -> bool:
    """True when the project's pytest configuration mentions testmon's cloud mode."""
    for name in PYTEST_CONFIG_FILES:
        path = repo / name
        if path.is_file() and _TMNET_RE.search(path.read_text(encoding="utf-8", errors="replace")):
            return True
    return bool(_TMNET_RE.search(os.environ.get("PYTEST_ADDOPTS", "")))


# --------------------------------------------------------------------------------------------
# The import map
# --------------------------------------------------------------------------------------------


def _module_name(rel: str, root: str = "") -> str | None:
    """Dotted module name of `rel` under the import root `root` (None if not importable)."""
    if root:
        if not rel.startswith(root + "/"):
            return None
        rel = rel[len(root) + 1 :]
    parts = rel[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def _import_roots(files: Sequence[str]) -> list[str]:
    """Directories that act as sys.path entries: the root, `src`, and pytest's test basedirs.

    For a test module, pytest's default import mode puts the first directory upwards without an
    `__init__.py` on sys.path, so sibling helper modules import by their bare name.
    """
    packages = {rel.rsplit("/", 1)[0] for rel in files if rel.endswith("/__init__.py")}
    roots = {"", "src"}
    for rel in files:
        if is_test_file(rel) or rel.endswith("conftest.py"):
            directory = rel.rsplit("/", 1)[0] if "/" in rel else ""
            while directory and directory in packages:
                directory = directory.rsplit("/", 1)[0] if "/" in directory else ""
            roots.add(directory)
    return sorted(roots)


def _imports(source: str, rel: str) -> set[str]:
    """Absolute dotted names a module imports (`from a import b` gives `a` and `a.b`)."""
    try:
        tree = ast.parse(source, filename=rel)
    except (SyntaxError, ValueError):
        return set()
    package = rel[: -len(".py")].split("/")[:-1]  # the containing package, also for __init__
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level - 1 > len(package):
                    continue
                anchor = package[: len(package) - (node.level - 1)]
                stem = ".".join([*anchor, *([node.module] if node.module else [])])
            else:
                stem = node.module or ""
            if not stem:
                continue
            names.add(stem)
            names.update(f"{stem}.{alias.name}" for alias in node.names if alias.name != "*")
    return names


def _prefixes(name: str) -> Iterable[str]:
    parts = name.split(".")
    for end in range(1, len(parts) + 1):
        yield ".".join(parts[:end])


def import_graph(
    repo: Path, files: Sequence[str], removed: Sequence[str] = ()
) -> dict[str, set[str]]:
    """file -> project files it imports (importing `a.b.c` also runs `a` and `a.b`).

    `removed` are deleted modules: they stay importable names, so their importers depend on them.
    """
    roots = _import_roots(files)
    index: dict[str, set[str]] = {}
    for rel in [*files, *removed]:
        for root in roots:
            name = _module_name(rel, root)
            if name:
                index.setdefault(name, set()).add(rel)
    graph: dict[str, set[str]] = {}
    for rel in files:
        try:
            source = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        targets: set[str] = set()
        for imported in _imports(source, rel):
            for prefix in _prefixes(imported):
                targets |= index.get(prefix, set())
        targets.discard(rel)
        graph[rel] = targets
    return graph


def _dependents(graph: dict[str, set[str]], changed: Iterable[str]) -> set[str]:
    reverse: dict[str, set[str]] = {}
    for source, targets in graph.items():
        for target in targets:
            reverse.setdefault(target, set()).add(source)
    seen = set(changed)
    queue = deque(seen)
    while queue:
        for dependent in reverse.get(queue.popleft(), ()):
            if dependent not in seen:
                seen.add(dependent)
                queue.append(dependent)
    return seen


def mapped_tests(repo: Path, changed_py: Sequence[str], files: Sequence[str]) -> list[str]:
    """Test files reached by the changed Python files (see the module docstring)."""
    tests = [rel for rel in files if is_test_file(rel)]
    present = set(files)
    removed = [rel for rel in changed_py if rel not in present]
    affected = _dependents(import_graph(repo, files, removed), changed_py)
    selected = {rel for rel in tests if rel in affected}
    for rel in changed_py:
        name = rel.rsplit("/", 1)[-1]
        if name == "conftest.py":
            directory = rel[: -len("conftest.py")]
            selected.update(test for test in tests if test.startswith(directory))
        elif not is_test_file(rel):
            stem = name[: -len(".py")]
            if stem == "__init__":
                stem = rel.rsplit("/", 2)[-2] if "/" in rel else ""
            if stem:
                wanted = {f"test_{stem}.py", f"{stem}_test.py"}
                selected.update(test for test in tests if test.rsplit("/", 1)[-1] in wanted)
    return sorted(selected)


# --------------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------------


def select_tests(
    repo: Path,
    changed: Sequence[str] | None,
    *,
    python: str,
    state_dir: Path | None = None,
    has_testmon: bool | None = None,
) -> Selection:
    """Choose the impacted tests for `changed` (relative POSIX paths; None = unknown).

    `has_testmon` says whether `python` can import testmon; None probes it.
    """
    files = python_files(repo)
    if not has_tests(repo, files):
        return Selection("none", [], "no tests found")
    if changed is None:
        return Selection("full", [], "changed files are unknown")
    changes = sorted({rel.replace("\\", "/") for rel in changed if rel})
    if not changes:
        return Selection("none", [], "no changed files")
    other = [rel for rel in changes if not rel.endswith((".py", ".pyi"))]
    if other:
        listed = ", ".join(other[:5]) + (f" and {len(other) - 5} more" if len(other) > 5 else "")
        return Selection("full", [], f"non-Python files changed ({listed})")
    changes = [rel for rel in changes if rel.endswith(".py")]  # stubs do not run
    if not changes:
        return Selection("none", [], "only type stubs changed")
    if has_testmon is None:
        has_testmon = commands.Tools(python, repo, commands.project_env(python)).has("testmon")
    if has_testmon and not tmnet_configured(repo):
        datafile = testmon_datafile(repo, state_dir)
        datafile.parent.mkdir(parents=True, exist_ok=True)
        return Selection(
            "testmon",
            ["--testmon"],
            "pytest-testmon selects tests that executed the changed code",
            {TESTMON_DATAFILE_ENV: str(datafile)},
        )
    selected = mapped_tests(repo, changes, files)
    if not selected:
        return Selection("none", [], "no test imports the changed modules")
    note = " (pytest-testmon not used: --tmnet is configured)" if has_testmon else ""
    return Selection("mapped", selected, f"{len(selected)} test files import the changes{note}")
