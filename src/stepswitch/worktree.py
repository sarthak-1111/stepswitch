"""Isolated working copies for tasks (SPEC §14 RN8, ARCHITECTURE §7).

`WorktreePool(repo, state_dir)` gives each task its own directory under `<state_dir>/wt/<id>`
(short names, for Windows path limits):

* In a git repository (`repo` is the top level and has a commit), `git worktree add --detach`
  checks out `base` (default `HEAD`). Uncommitted changes in `repo` are NOT included by
  default: the task starts from the committed `base`. With `include_uncommitted=True` the
  current tracked changes and untracked, not-ignored files are copied in and recorded as a
  snapshot commit, which becomes `base_commit`; the task's diff then holds only its own work
  and applies on top of the user's dirty tree.
* Any other directory (no git, no commits yet, or a subdirectory of a repository) is copied,
  skipping `.git`, `.venv`, `node_modules` and generated files (below), and initialised as a
  git repository with one base commit, so diffs work the same way.

`Worktree.diff()` is the change against `base_commit` including new files; `apply_to(target)`
applies it with `git apply` after `git apply --check`, so a change that would not apply
cleanly is refused rather than half-applied. `changed_files()`, `diff()` and `commit()` all
read one temporary index (base commit plus the working copy's changes), so they agree.
Commits use a fixed identity through `git -c` (the user's git configuration is never
changed), skip hooks, and set the branch `stepswitch/<task_id>`. Hooks are also off for
`worktree add` (it runs `post-checkout`).

Generated files (`GENERATED_PATTERNS`: bytecode, test and type-checker caches, coverage data,
the Stepswitch state directory) are never part of a task's change: running the tests creates
them, and they would otherwise show up as new files. The patterns are passed to
`git ls-files --exclude=...`; the user's `.gitignore` and `.git/info/exclude` are not touched.
They only filter new, untracked files: a file the base commit tracks is always compared,
whatever its name.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stepswitch.context import proc

IDENTITY = ("-c", "user.name=stepswitch", "-c", "user.email=stepswitch@localhost")
NO_SIGNING = ("-c", "commit.gpgsign=false")
# Files and directories that running code and its tests creates, plus the state directory.
# Gitignore syntax: a pattern without "/" matches the name at any depth, a trailing "/" matches
# directories only.
GENERATED_PATTERNS = (
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".hypothesis/",
    ".ipynb_checkpoints/",
    ".coverage",
    ".stepswitch/",
)
EXCLUDE_GENERATED = tuple(f"--exclude={pattern}" for pattern in GENERATED_PATTERNS)
COPY_SKIP = frozenset({".git", ".venv", "node_modules"})  # besides generated files
# Variables that would point git commands at another repository or index.
GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_CEILING_DIRECTORIES",
)
GIT_TIMEOUT = 600.0


class WorktreeError(RuntimeError):
    pass


def generated(name: str, *, is_dir: bool) -> bool:
    """Whether a file or directory called `name` matches `GENERATED_PATTERNS`."""
    for pattern in GENERATED_PATTERNS:
        if (is_dir or not pattern.endswith("/")) and fnmatch.fnmatchcase(name, pattern.rstrip("/")):
            return True
    return False


def branch_name(task_id: str) -> str:
    """`stepswitch/<task_id>` with the id reduced to characters git accepts in a ref."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id)
    safe = re.sub(r"\.{2,}", ".", safe).strip(".-")
    if safe.endswith(".lock"):
        safe = safe[: -len(".lock")] + "-lock"
    return f"stepswitch/{safe or 'task'}"


def _remove_tree(path: Path, attempts: int = 5) -> None:
    """rmtree that clears read-only bits (git objects on Windows) and retries briefly."""

    def clear_readonly(function: Callable[..., Any], target: str, *_: Any) -> None:
        with contextlib.suppress(OSError):
            Path(target).chmod(stat.S_IWRITE | stat.S_IREAD)
            function(target)

    for attempt in range(attempts):
        if not path.exists():
            return
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=clear_readonly)
            else:
                shutil.rmtree(path, onerror=clear_readonly)
        except OSError:
            pass
        if not path.exists():
            return
        time.sleep(0.1 * (attempt + 1))
    if path.exists():
        raise WorktreeError(f"could not remove {path}")


@dataclass
class Worktree:
    """One task's working copy."""

    pool: WorktreePool
    path: Path
    base_commit: str
    task_id: str
    copied: bool = False  # made by copying a non-git directory
    branch: str | None = None
    released: bool = False
    own_index: Path | None = field(default=None, repr=False)  # found on first use

    def _git(
        self, *args: str, env: Mapping[str, str] | None = None, input_data: bytes | None = None
    ) -> bytes:
        return self.pool._git(list(args), cwd=self.path, env=env, input_data=input_data)

    def _index_file(self) -> Path | None:
        """The worktree's own index (its stat data seeds the temporary indexes)."""
        if self.own_index is None:
            try:
                out = self._git("rev-parse", "--git-path", "index")
            except WorktreeError:
                return None
            self.own_index = self.path / out.decode("utf-8", errors="surrogateescape").strip()
        return self.own_index

    @contextlib.contextmanager
    def _working_index(self) -> Iterator[dict[str, str]]:
        """A temporary index holding `base_commit` plus the working copy's changes: edits and
        deletions of files the base commit tracks, and new files that are neither ignored nor
        generated. Yields the environment that selects it; the worktree's index is untouched."""
        index = self.pool.base_dir / f".index-{uuid.uuid4().hex[:12]}"
        env = {"GIT_INDEX_FILE": str(index)}
        try:
            self.pool._fill_index(self.path, self.base_commit, env, seed=self._index_file())
            yield env
        finally:
            for leftover in (index, index.with_name(index.name + ".lock")):
                with contextlib.suppress(OSError):
                    leftover.unlink(missing_ok=True)

    def changed_files(self) -> list[str]:
        """Paths that differ from `base_commit` (edited, deleted, or new and neither ignored
        nor generated): exactly the files `diff()` covers."""
        with self._working_index() as env:
            out = self._git(
                "diff",
                "--cached",
                "--name-only",
                "-z",
                "--no-renames",
                self.base_commit,
                "--",
                env=env,
            )
        names = out.decode("utf-8", errors="surrogateescape").split("\0")
        return sorted({name for name in names if name})

    def diff(self) -> str:
        """A patch from `base_commit` to the working copy, new files included (binary-safe);
        ignored and generated new files are left out."""
        with self._working_index() as env:
            patch = self._git(
                "diff",
                "--cached",
                "--binary",
                "--unified=3",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                self.base_commit,
                "--",
                env=env,
            )
        return patch.decode("utf-8", errors="surrogateescape")

    def commit(self, message: str) -> str:
        """Commit the working copy on top of HEAD, with exactly the content `diff()` describes
        (so ignored and generated files stay out), and point `stepswitch/<task_id>` at it.
        Returns the commit; HEAD is returned unchanged when there is nothing new."""
        sha = self._commit_changes(message)
        branch = branch_name(self.task_id)
        self._git("branch", "-f", branch, sha)
        self.branch = branch
        return sha

    def _commit_changes(self, message: str) -> str:
        with self._working_index() as env:
            tree = self._git("write-tree", env=env).decode().strip()
        head, head_tree = self._git("rev-parse", "HEAD^{commit}", "HEAD^{tree}").decode().split()
        if tree == head_tree:
            return head
        text = (message.strip() or f"stepswitch: {self.task_id}") + "\n"
        made = self._git(
            *IDENTITY, *NO_SIGNING, "commit-tree", "-p", head, tree, input_data=text.encode()
        )
        sha = made.decode().strip()
        self._git("update-ref", "-m", "stepswitch: commit", "HEAD", sha, head)
        self._git("reset", "-q")  # the worktree's index follows HEAD; files are not touched
        return sha

    def apply_to(self, target: Path) -> None:
        """Apply `diff()` to `target` (the user's working tree); refuse if it does not apply
        cleanly. Only files are changed, never the target's index or history."""
        patch = self.diff()
        if not patch.strip():
            return
        target = target.resolve()
        data = patch.encode("utf-8", errors="surrogateescape")
        env: dict[str, str] = {}
        if not (target / ".git").exists():
            # Not a repository top level: keep git from finding an enclosing repository, so
            # the patch applies relative to `target` like a plain patch.
            env["GIT_CEILING_DIRECTORIES"] = str(target.parent)
        apply = ["apply", "--whitespace=nowarn", "--binary"]
        try:
            self.pool._git([*apply, "--check", "-"], cwd=target, env=env, input_data=data)
        except WorktreeError as exc:
            raise WorktreeError(f"the changes do not apply cleanly to {target}: {exc}") from exc
        self.pool._git([*apply, "-"], cwd=target, env=env, input_data=data)

    def release(self, keep: bool = False) -> None:
        """Give the worktree back: removed by default, left on disk with `keep=True`. The
        checkout of its base commit that test runs compared with goes in any case."""
        if not self.released:
            self.released = True
            from stepswitch.verify import baseline

            baseline.release(Path(self.path))
            self.pool._release(self, keep)


class WorktreePool:
    """Creates, recycles and removes task worktrees for one repository or directory."""

    def __init__(self, repo: Path, state_dir: Path) -> None:
        self.repo = repo.resolve()
        self.state_dir = state_dir.resolve()
        self.base_dir = self.state_dir / "wt"
        self._git_path = proc.which("git")
        self._lock = threading.RLock()
        self._idle: list[tuple[Path, str]] = []
        self._active: dict[Path, Worktree] = {}
        self._mode: str | None = None

    # -- git plumbing ------------------------------------------------------------------------

    @property
    def git(self) -> str:
        if self._git_path is None:
            raise WorktreeError("git is required for worktrees and was not found on PATH")
        return self._git_path

    def _hooks_dir(self) -> Path:
        hooks = self.base_dir / ".nohooks"
        hooks.mkdir(parents=True, exist_ok=True)
        return hooks

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        input_data: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        full_env = {k: v for k, v in os.environ.items() if k.upper() not in GIT_LOCATION_VARS}
        full_env["GIT_TERMINAL_PROMPT"] = "0"
        full_env.update(env or {})
        argv = [self.git, "-c", f"core.hooksPath={self._hooks_dir()}"]
        if sys.platform == "win32":
            argv += ["-c", "core.longpaths=true"]
        argv += list(args)
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            done = subprocess.run(
                argv,
                cwd=cwd,
                env=full_env,
                input=input_data,
                capture_output=True,
                timeout=GIT_TIMEOUT,
                check=False,
                **kwargs,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorktreeError(f"git {' '.join(args[:2])} failed: {exc}") from exc
        return done

    def _git(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        input_data: bytes | None = None,
    ) -> bytes:
        """Run git and return its stdout; a non-zero exit raises `WorktreeError`."""
        done = self._run(args, cwd=cwd, env=env, input_data=input_data)
        if done.returncode != 0:
            message = done.stderr.decode("utf-8", errors="replace").strip()
            raise WorktreeError(f"git {' '.join(args[:3])}: {message or done.returncode}")
        return done.stdout

    def _untracked(self, cwd: Path, env: Mapping[str, str] | None = None) -> list[bytes]:
        """Untracked files that are neither ignored (standard exclude files) nor generated;
        nested repositories (listed as `dir/`) are left out."""
        args = ["ls-files", "--others", "--exclude-standard", *EXCLUDE_GENERATED, "-z"]
        out = self._git(args, cwd=cwd, env=env)
        return [name for name in out.split(b"\0") if name and not name.endswith(b"/")]

    def _stage(self, cwd: Path, env: Mapping[str, str] | None = None) -> None:
        """Stage edits and deletions of tracked files plus the `_untracked` files into the
        index `env` selects (the working copy's own index when `env` is None)."""
        lenient = ("-c", "core.safecrlf=false")
        self._git([*lenient, "add", "-u"], cwd=cwd, env=env)
        new = self._untracked(cwd, env)
        if new:
            names = b"\0".join(new) + b"\0"  # literal paths: no pathspec magic, no length limit
            add = [*lenient, "update-index", "--add", "-z", "--stdin"]
            self._git(add, cwd=cwd, env=env, input_data=names)

    def _fill_index(
        self, cwd: Path, commit: str, env: Mapping[str, str], *, seed: Path | None
    ) -> None:
        """Write `commit` plus the working copy's changes into the temporary index `env`
        selects. A copy of the worktree's own index (`seed`) goes in first: `read-tree -m -i`
        keeps its stat data for entries that match `commit`, so unchanged files are not hashed
        again. Without a usable seed the index is read from `commit` alone."""
        index = Path(env["GIT_INDEX_FILE"])
        seeded = False
        if seed is not None:
            with contextlib.suppress(OSError):
                shutil.copy2(seed, index)  # keeps the mtime git's racy-entry checks rely on
                seeded = True
        try:
            self._git(["read-tree", *(["-m", "-i"] if seeded else []), commit], cwd=cwd, env=env)
        except WorktreeError:
            if not seeded:
                raise
            with contextlib.suppress(OSError):
                index.unlink(missing_ok=True)
            self._git(["read-tree", commit], cwd=cwd, env=env)
        self._stage(cwd, env)

    def mode(self) -> str:
        """`git` when `repo` is a git top level with a commit, else `copy`."""
        if self._mode is None:
            self._mode = "copy"
            if self._git_path is not None:
                try:
                    top = self._git(["rev-parse", "--show-toplevel"], cwd=self.repo)
                    toplevel = Path(top.decode().strip()).resolve()
                    self._git(["rev-parse", "--verify", "-q", "HEAD^{commit}"], cwd=self.repo)
                    if toplevel == self.repo:
                        self._mode = "git"
                except WorktreeError:
                    pass
        return self._mode

    def _resolve(self, base: str) -> str:
        out = self._git(["rev-parse", "--verify", "-q", f"{base}^{{commit}}"], cwd=self.repo)
        return str(out.decode().strip())

    def _new_path(self) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        while True:
            path = self.base_dir / uuid.uuid4().hex[:8]
            if not path.exists():
                return path

    def _add(self, commit: str) -> Path:
        path = self._new_path()
        self._git(["worktree", "add", "--detach", "--quiet", str(path), commit], cwd=self.repo)
        return path

    # -- public API --------------------------------------------------------------------------

    def prepare(self, count: int) -> int:
        """Pre-create up to `count` idle worktrees at HEAD (git mode only); returns how many
        are idle. Copy mode copies at `acquire` time, so nothing is prepared."""
        with self._lock:
            if self.mode() != "git":
                return 0
            commit = self._resolve("HEAD")
            while len(self._idle) < count:
                self._idle.append((self._add(commit), commit))
            return len(self._idle)

    def acquire(
        self, task_id: str, *, base: str = "HEAD", include_uncommitted: bool = False
    ) -> Worktree:
        """A worktree for `task_id` starting at `base` (see the module docstring)."""
        with self._lock:
            if self.mode() == "git":
                commit = self._resolve(base)
                path = self._take_idle(commit) or self._add(commit)
                tree = Worktree(self, path, commit, task_id)
                if include_uncommitted:
                    self._copy_uncommitted(path)
                    tree.base_commit = tree._commit_changes("stepswitch: uncommitted changes")
            else:
                if base != "HEAD":
                    raise WorktreeError(f"{self.repo} is not a git repository; `base` must be HEAD")
                path = self._new_path()
                self._copy_tree(path)
                tree = Worktree(self, path, self._init_copy(path), task_id, copied=True)
            self._active[path] = tree
            return tree

    def active(self) -> list[Worktree]:
        with self._lock:
            return list(self._active.values())

    def close(self) -> None:
        """Remove idle prepared worktrees (active ones stay until released)."""
        with self._lock:
            idle, self._idle = self._idle, []
            for path, _ in idle:
                self._remove(path, copied=False)
            if idle:
                with contextlib.suppress(WorktreeError):
                    self._git(["worktree", "prune"], cwd=self.repo)

    # -- internals ---------------------------------------------------------------------------

    def _take_idle(self, commit: str) -> Path | None:
        if not self._idle:
            return None
        path, at = self._idle.pop(0)
        if at != commit:
            self._git(["checkout", "--detach", "--force", "--quiet", commit], cwd=path)
            self._git(["clean", "-ffdq"], cwd=path)
        return path

    def _copy_uncommitted(self, path: Path) -> None:
        """Copy the repository's tracked changes and its untracked, not ignored, not generated
        files into the fresh worktree at `path` (deleted files are deleted there too)."""
        changed = self._git(
            ["diff", "--name-only", "-z", "--no-renames", "HEAD", "--"], cwd=self.repo
        )
        names = [name for name in changed.split(b"\0") if name] + self._untracked(self.repo)
        for raw in names:
            name = raw.decode("utf-8", errors="surrogateescape")
            source, target = self.repo / name, path / name
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            elif not source.exists():
                with contextlib.suppress(OSError):
                    target.unlink(missing_ok=True)

    def _copy_tree(self, destination: Path) -> None:
        state = self.state_dir
        for directory, dirnames, filenames in os.walk(self.repo):
            here = Path(directory)
            relative = here.relative_to(self.repo)
            keep = []
            for name in dirnames:
                child = here / name
                if name in COPY_SKIP or generated(name, is_dir=True) or child.resolve() == state:
                    continue
                if child.is_symlink():
                    _copy_link(child, destination / relative / name)
                    continue
                keep.append(name)
            dirnames[:] = keep
            (destination / relative).mkdir(parents=True, exist_ok=True)
            for name in filenames:
                source = here / name
                if generated(name, is_dir=False):
                    continue
                if source.is_symlink():
                    _copy_link(source, destination / relative / name)
                    continue
                with contextlib.suppress(OSError):
                    shutil.copy2(source, destination / relative / name)

    def _init_copy(self, path: Path) -> str:
        self._git(["-c", "init.defaultBranch=main", "init", "-q"], cwd=path)
        # Byte-exact content, so the diff applies back onto the original files unchanged.
        self._git(["config", "core.autocrlf", "false"], cwd=path)
        self._git(["config", "core.safecrlf", "false"], cwd=path)
        self._stage(path)
        commit = [*IDENTITY, *NO_SIGNING, "commit", "-q", "--no-verify", "--allow-empty"]
        self._git([*commit, "-m", "stepswitch: base snapshot"], cwd=path)
        return str(self._git(["rev-parse", "HEAD"], cwd=path).decode().strip())

    def _remove(self, path: Path, *, copied: bool) -> None:
        if not copied and self.mode() == "git":
            with contextlib.suppress(WorktreeError):
                self._git(["worktree", "remove", "--force", "--force", str(path)], cwd=self.repo)
        if path.exists():
            _remove_tree(path)

    def _release(self, tree: Worktree, keep: bool) -> None:
        with self._lock:
            self._active.pop(tree.path, None)
            if keep:
                return
            self._remove(tree.path, copied=tree.copied)
            if not tree.copied:
                with contextlib.suppress(WorktreeError):
                    self._git(["worktree", "prune"], cwd=self.repo)


def _copy_link(source: Path, destination: Path) -> None:
    """Recreate a symbolic link (never followed); skipped where links cannot be made."""
    with contextlib.suppress(OSError, NotImplementedError):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source.readlink(), target_is_directory=source.is_dir())
