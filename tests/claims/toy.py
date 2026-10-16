"""Toy repositories for claim tests: copies of bench/repos/<name> as fresh git repositories."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPOS = ROOT / "bench" / "repos"
TOY_INSTRUCTION = "Fix the bug in add() in calc.py so that the tests pass."


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def toy_repo(name: str, dest: Path) -> Path:
    """Copy bench/repos/<name> to `dest` (which must not exist) and commit it."""
    shutil.copytree(
        REPOS / name, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
    )
    git(dest, "init", "-q")
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "toy repository")
    return dest
