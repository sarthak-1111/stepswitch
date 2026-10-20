"""Failures that already happen at the base commit (ADR 0042).

A test that fails before a task's change is not the change's fault. On a real repository some
tests fail in a given environment whatever the change: a missing service or locale, a version
of a dependency, timing. An agent that is told only "tests failed" spends its steps on them.

So when a test run fails, the same command runs once more in a checkout of the base commit, and
the failures both runs share are reported as pre-existing: the agent's `test` tool and the test
gates list them apart. Nothing passes because of them, since a task is often to fix a test that
fails at the base commit. A failure is compared by test id as `summarize_tests` reads it (pytest
node ids, unittest labels), and it is pre-existing only when the base commit's run named it too:
a failure that either run cannot name is not compared, and the report says how many there were.
A run that took longer than `MAX_SECONDS` is not compared.

The base checkout is a detached git worktree of the base commit beside the task's worktree
(`<state>/wt/<id>-base`, so the project's environment treats it as the task's worktree is
treated), made on first use and shared by the tool and the gates of the task. `release` removes
it when the task's worktree goes. Results are kept per command line.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from stepswitch.verify import commands

MAX_SECONDS = 600.0  # a slower failing run is not repeated at the base commit
SUFFIX = "-base"

EnvFor = Callable[[Path], Mapping[str, str]]
Summarize = Callable[[str], tuple[list[str], int]]


class Baseline:
    """Runs test commands at the base commit of one task worktree, once per command line."""

    def __init__(self, workdir: Path, base: str) -> None:
        self.workdir = Path(workdir).absolute()
        self.base = base
        self.path = self.workdir.parent / f"{self.workdir.name}{SUFFIX}"
        self._lock = threading.Lock()
        self._made: bool | None = None
        self._failing: dict[tuple[str, ...], frozenset[str] | None] = {}

    def checkout(self) -> Path | None:
        """The base checkout, made on first use; None when git cannot make it."""
        with self._lock:
            if self._made is None:
                self._made = self._make()
            return self.path if self._made else None

    def _make(self) -> bool:
        if (self.path / ".git").exists():
            return True
        done = commands.git(self.workdir, "worktree", "add", "--detach", str(self.path), self.base)
        return done is not None and done.returncode == 0

    def failing(
        self,
        argv: Sequence[str],
        *,
        env_for: EnvFor,
        summarize: Summarize,
        timeout: float,
    ) -> frozenset[str] | None:
        """Ids of the named tests that fail when `argv` runs at the base commit, or None when
        that cannot be told (no checkout, or the run did not start or end)."""
        key = tuple(argv)
        if key in self._failing:
            return self._failing[key]
        found: frozenset[str] | None = None
        checkout = self.checkout()
        if checkout is not None:
            moved = [arg.replace(str(self.workdir), str(checkout)) for arg in argv]
            commands.purge_stale_bytecode(checkout)
            result = commands.run(moved, cwd=checkout, timeout=timeout, env=dict(env_for(checkout)))
            if result.started and not result.timed_out:
                named, _ = summarize(result.output)
                found = frozenset(named)
        self._failing[key] = found
        return found

    def close(self) -> None:
        with self._lock:
            if self._made:
                done = commands.git(self.workdir, "worktree", "remove", "--force", str(self.path))
                if done is None or done.returncode != 0:
                    shutil.rmtree(self.path, ignore_errors=True)
                    commands.git(self.workdir, "worktree", "prune")
            self._made = None


_OPEN: dict[str, Baseline] = {}
_OPEN_LOCK = threading.Lock()


def for_worktree(workdir: Path, base: str | None) -> Baseline | None:
    """The task's baseline, shared by its test tool and its gates; None without a base."""
    if not base:
        return None
    key = str(Path(workdir).absolute())
    with _OPEN_LOCK:
        found = _OPEN.get(key)
        if found is None or found.base != base:
            found = _OPEN[key] = Baseline(Path(workdir), base)
        return found


def release(workdir: Path) -> None:
    """Remove the base checkout of a task worktree, if one was made."""
    with _OPEN_LOCK:
        found = _OPEN.pop(str(Path(workdir).absolute()), None)
    if found is not None:
        found.close()


def compare(failing: Sequence[str], before: frozenset[str] | None) -> tuple[list[str], list[str]]:
    """(new failures, pre-existing failures) of a run, given the base commit's failures."""
    if before is None:
        return list(failing), []
    return [t for t in failing if t not in before], [t for t in failing if t in before]
