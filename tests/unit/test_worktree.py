"""Task worktrees: git worktrees for repositories, git-initialised copies for other folders."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from stepswitch import worktree as W
from stepswitch.context import proc

GIT = proc.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")

CALC = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"


def git(cwd: Path, *args: str) -> str:
    assert GIT is not None
    env = {k: v for k, v in os.environ.items() if k.upper() not in W.GIT_LOCATION_VARS}
    command = [GIT, "-c", "user.name=t", "-c", "user.email=t@localhost"]
    command += ["-c", "commit.gpgsign=false", *args]
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, check=True)
    return done.stdout.decode("utf-8").strip()


def write(path: Path, text: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write(root / "calc.py", CALC)
    write(root / "tests" / "test_calc.py", "from calc import add\n")
    write(root / "old.txt", "remove me\n")
    write(root / "tracked.pyc", b"\x00tracked bytecode")  # tracked although it looks generated
    write(root / ".gitignore", "*.log\n")
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "base")
    return root


@pytest.fixture
def pool(repo: Path, tmp_path: Path) -> Iterator[W.WorktreePool]:
    made = W.WorktreePool(repo, tmp_path / "state")
    yield made
    for tree in made.active():
        tree.release()
    made.close()


def test_branch_names_are_valid_refs() -> None:
    assert W.branch_name("t-1") == "stepswitch/t-1"
    assert W.branch_name("a b..c/d.lock") == "stepswitch/a-b.c-d-lock"
    assert W.branch_name("..") == "stepswitch/task"


def test_generated_names() -> None:
    assert W.generated("__pycache__", is_dir=True)
    assert not W.generated("__pycache__", is_dir=False)  # "__pycache__/" names directories
    assert W.generated("m.cpython-312.pyc", is_dir=False)
    assert W.generated(".coverage", is_dir=False)
    assert W.generated(".stepswitch", is_dir=True)
    assert not W.generated("calc.py", is_dir=False)


def test_git_worktree_diff_commit_apply_release(repo: Path, pool: W.WorktreePool) -> None:
    head = git(repo, "rev-parse", "HEAD")
    tree = pool.acquire("task-1")
    assert pool.mode() == "git" and not tree.copied
    assert tree.base_commit == head
    assert tree.path.parent == pool.base_dir
    write(tree.path / "calc.py", FIXED)
    write(tree.path / "new.py", "x = 1\n")
    (tree.path / "old.txt").unlink()
    write(tree.path / "debug.log", "ignored by .gitignore\n")

    assert tree.changed_files() == ["calc.py", "new.py", "old.txt"]
    patch = tree.diff()
    assert "+    return a + b" in patch and "+++ b/new.py" in patch
    assert "deleted file mode" in patch and "debug.log" not in patch

    sha = tree.commit("fix add")
    assert git(repo, "rev-parse", "stepswitch/task-1") == sha
    author = git(repo, "log", "-1", "--format=%an <%ae>|%s", sha)
    assert author == "stepswitch <stepswitch@localhost>|fix add"
    assert git(tree.path, "rev-parse", "HEAD") == sha
    assert git(tree.path, "status", "--porcelain") == ""  # the index follows the new HEAD
    assert tree.commit("again") == sha  # nothing new: HEAD comes back
    assert tree.diff() == patch  # still measured against the base commit

    tree.apply_to(repo)
    assert (repo / "calc.py").read_text(encoding="utf-8") == FIXED
    assert (repo / "new.py").exists() and not (repo / "old.txt").exists()
    assert git(repo, "rev-parse", "HEAD") == head  # only files change, never history

    path = tree.path
    tree.release()
    assert not path.exists()
    assert str(path.name) not in git(repo, "worktree", "list")


def test_generated_files_are_left_out(repo: Path, pool: W.WorktreePool) -> None:
    tree = pool.acquire("task-gen")
    write(tree.path / "calc.py", FIXED)
    write(tree.path / "__pycache__" / "x.pyc", b"\x00bytecode")
    write(tree.path / ".pytest_cache" / "v" / "cache" / "nodeids", "[]\n")
    write(tree.path / "tests" / "__pycache__" / "test_calc.cpython-312-pytest.pyc", b"\x00")
    write(tree.path / ".coverage", b"\x00coverage")
    write(tree.path / ".stepswitch" / "cache" / "map.json", "{}\n")

    assert tree.changed_files() == ["calc.py"]
    patch = tree.diff()
    assert patch.count("diff --git") == 1 and "a/calc.py" in patch
    for name in (
        "__pycache__",
        ".pytest_cache",
        "nodeids",
        ".coverage",
        ".stepswitch",
        "GIT binary",
    ):
        assert name not in patch
    sha = tree.commit("fix")
    assert git(repo, "ls-tree", "-r", "--name-only", sha).split() == [
        ".gitignore",
        "calc.py",
        "old.txt",
        "tests/test_calc.py",
        "tracked.pyc",
    ]


def test_tracked_files_show_up_even_when_they_look_generated(
    repo: Path, pool: W.WorktreePool
) -> None:
    tree = pool.acquire("task-tracked")
    write(tree.path / "tracked.pyc", b"\x00new bytecode")
    assert tree.changed_files() == ["tracked.pyc"]
    assert "GIT binary patch" in tree.diff()
    tree.apply_to(repo)
    assert (repo / "tracked.pyc").read_bytes() == b"\x00new bytecode"


def test_binary_files_round_trip(repo: Path, pool: W.WorktreePool) -> None:
    tree = pool.acquire("task-bin")
    blob = bytes(range(256)) * 4
    write(tree.path / "data.bin", blob)
    tree.apply_to(repo)
    assert (repo / "data.bin").read_bytes() == blob


def test_a_change_that_does_not_apply_is_refused(repo: Path, pool: W.WorktreePool) -> None:
    tree = pool.acquire("task-conflict")
    write(tree.path / "calc.py", FIXED)
    write(tree.path / "new.py", "x = 1\n")
    write(repo / "calc.py", "def add(a, b):\n    return b - a\n")  # the user edited it meanwhile
    with pytest.raises(W.WorktreeError, match="do not apply cleanly"):
        tree.apply_to(repo)
    assert (repo / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return b - a\n"
    assert not (repo / "new.py").exists()  # nothing half-applied


def test_prepared_worktrees_are_reused(repo: Path, pool: W.WorktreePool) -> None:
    assert pool.prepare(2) == 2
    idle = {path for path, _ in pool._idle}
    tree = pool.acquire("task-a")
    assert tree.path in idle
    assert (tree.path / "calc.py").read_text(encoding="utf-8") == CALC
    pool.close()
    remaining = idle - {tree.path}
    assert remaining and not any(path.exists() for path in remaining)


def test_include_uncommitted_starts_from_the_dirty_tree(repo: Path, pool: W.WorktreePool) -> None:
    head = git(repo, "rev-parse", "HEAD")
    write(repo / "calc.py", CALC + "# wip\n")
    write(repo / "notes.txt", "draft\n")
    write(repo / "__pycache__" / "calc.cpython-312.pyc", b"\x00stale")
    (repo / "old.txt").unlink()

    plain = pool.acquire("task-clean")
    assert (plain.path / "calc.py").read_text(encoding="utf-8") == CALC
    assert not (plain.path / "notes.txt").exists()

    tree = pool.acquire("task-dirty", include_uncommitted=True)
    assert tree.base_commit != head
    assert (tree.path / "calc.py").read_text(encoding="utf-8") == CALC + "# wip\n"
    assert (tree.path / "notes.txt").exists() and not (tree.path / "old.txt").exists()
    assert not (tree.path / "__pycache__").exists()  # generated files are not copied
    assert tree.changed_files() == []
    write(tree.path / "calc.py", FIXED + "# wip\n")
    assert tree.changed_files() == ["calc.py"]
    tree.apply_to(repo)  # the diff holds only the task's own work, so it fits the dirty tree
    assert (repo / "calc.py").read_text(encoding="utf-8") == FIXED + "# wip\n"
    assert git(repo, "rev-parse", "HEAD") == head


def test_release_can_keep_the_directory(pool: W.WorktreePool) -> None:
    tree = pool.acquire("task-keep")
    tree.release(keep=True)
    assert tree.path.exists() and pool.active() == []
    tree.release()  # a second release does nothing


def test_plain_directories_are_copied(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    write(folder / "a.py", "a = 1\n")
    write(folder / "sub" / "b.txt", "b\n")
    write(folder / "__pycache__" / "a.cpython-312.pyc", b"\x00")
    write(folder / "c.pyc", b"\x00")
    write(folder / ".pytest_cache" / "README.md", "cache\n")
    write(folder / "node_modules" / "m.js", "m\n")
    pool = W.WorktreePool(folder, folder / ".stepswitch")  # state inside the copied folder
    try:
        tree = pool.acquire("task-copy")
        assert pool.mode() == "copy" and tree.copied
        copied = sorted(
            p.relative_to(tree.path).as_posix()
            for p in tree.path.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(tree.path).parts
        )
        assert copied == ["a.py", "sub/b.txt"]
        write(tree.path / "a.py", "a = 2\n")
        write(tree.path / "c.py", "c = 3\n")
        write(tree.path / "__pycache__" / "a.cpython-312.pyc", b"\x00new")
        assert tree.changed_files() == ["a.py", "c.py"]
        tree.apply_to(folder)
        assert (folder / "a.py").read_text(encoding="utf-8") == "a = 2\n"
        assert (folder / "c.py").read_text(encoding="utf-8") == "c = 3\n"
        assert not (folder / ".git").exists()
        with pytest.raises(W.WorktreeError, match="not a git repository"):
            pool.acquire("task-2", base="main")
        path = tree.path
        tree.release()
        assert not path.exists()
    finally:
        pool.close()
