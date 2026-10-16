"""A tiny bench project for tests: one bug-fix task with its repository and golden patch."""

from __future__ import annotations

from pathlib import Path

CALC = '''"""Arithmetic helpers."""


def add(a, b):
    """Return the sum of a and b."""
    return a - b
'''
TEST_CALC = """from calc import add


def test_add():
    assert add(2, 3) == 5
"""
PYPROJECT = """[project]
name = "adder"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
pythonpath = ["."]
"""
BLANK = " "  # an empty context line of a unified diff


def patch(*lines: str) -> str:
    return "\n".join(lines) + "\n"


GOLDEN = patch(
    "--- a/calc.py",
    "+++ b/calc.py",
    "@@ -3,4 +3,4 @@",
    BLANK,
    " def add(a, b):",
    '     """Return the sum of a and b."""',
    "-    return a - b",
    "+    return a + b",
)
CHEAT = patch(
    "--- a/tests/test_calc.py",
    "+++ b/tests/test_calc.py",
    "@@ -2,4 +2,4 @@",
    BLANK,
    BLANK,
    " def test_add():",
    "-    assert add(2, 3) == 5",
    "+    assert add(2, 3) == -1",
)
SUITE = """schema: 1
name: tiny
description: One bug fix.
tasks:
  - id: adder-fix
    repo: bench/repos/adder
    instruction: add() in calc.py subtracts. Make it add.
    acceptance: ["python -m pytest -q"]
    golden: bench/golden/adder-fix.patch
    track: general
    tags: [bugfix, python, d1]
"""


def make_project(root: Path) -> Path:
    """Create a project root holding a one-task suite (`tiny`), its repository and its golden
    patch, and return it."""
    repo = root / "bench" / "repos" / "adder"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(CALC, encoding="utf-8", newline="\n")
    (repo / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8", newline="\n")
    (repo / "tests" / "test_calc.py").write_text(TEST_CALC, encoding="utf-8", newline="\n")
    golden = root / "bench" / "golden"
    golden.mkdir(parents=True)
    (golden / "adder-fix.patch").write_text(GOLDEN, encoding="utf-8", newline="\n")
    suites = root / "bench" / "suites"
    suites.mkdir(parents=True)
    (suites / "tiny.yaml").write_text(SUITE, encoding="utf-8", newline="\n")
    return root
