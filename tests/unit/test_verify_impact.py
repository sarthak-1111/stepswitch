"""Test impact analysis (SPEC §8.2): testmon, the static import map, forced full runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from stepswitch.verify import impact

FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/core.py": "VALUE = 1\n",
    "src/pkg/api.py": "from pkg.core import VALUE\n",
    "src/pkg/other.py": "OTHER = 2\n",
    "src/pkg/sub/__init__.py": "from . import leaf\n",
    "src/pkg/sub/leaf.py": "from ..core import VALUE\n",
    "tests/test_api.py": "import pkg.api\n",
    "tests/test_other.py": "from pkg import other\n",
    "tests/unit/test_core.py": "def test_nothing_imported():\n    pass\n",
    "tests/unit/test_sub.py": "from pkg.sub import leaf\n",
    "tests/helpers.py": "import pkg.other\n",
    "tests/test_uses_helper.py": "from helpers import *\n",
    "tests/suite/__init__.py": "",
    "tests/suite/conftest.py": "",
    "tests/suite/support.py": "import pkg.core\n",
    "tests/suite/test_relative.py": "from .support import *\n",
    "scripts/tool.py": "import pkg.api\n",
}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))  # not a git checkout
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def select(repo: Path, changed: list[str] | None, **kwargs: object) -> impact.Selection:
    options: dict[str, object] = {"has_testmon": False, **kwargs}
    return impact.select_tests(repo, changed, python=sys.executable, **options)  # type: ignore[arg-type]


def test_python_files_skip_environments_and_caches(repo: Path) -> None:
    for rel in (".venv/lib/x.py", "build/lib/x.py", "node_modules/x.py", ".hidden/x.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("", encoding="utf-8")
    assert impact.python_files(repo) == sorted(FILES)


def test_has_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    (tmp_path / "tests" / "golden" / "case").mkdir(parents=True)
    (tmp_path / "tests" / "golden" / "case" / "input.json").write_text("{}", encoding="utf-8")
    assert not impact.has_tests(tmp_path)  # golden data alone is not a test suite
    (tmp_path / "check_test.py").write_text("", encoding="utf-8")
    assert impact.has_tests(tmp_path)
    assert impact.is_test_file("a/test_x.py") and impact.is_test_file("x_test.py")
    assert not impact.is_test_file("a/testing.py")


def test_nothing_to_select(repo: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    (empty / "pkg").mkdir(parents=True)
    (empty / "pkg" / "mod.py").write_text("", encoding="utf-8")
    assert select(empty, ["pkg/mod.py"]).mode == "none"
    assert select(repo, []) == impact.Selection("none", [], "no changed files")
    assert select(repo, None) == impact.Selection("full", [], "changed files are unknown")
    assert select(repo, ["src/pkg/core.pyi"]).reason == "only type stubs changed"


@pytest.mark.parametrize(
    "changed",
    [
        ["data/spectrum.csv"],
        ["science.toml"],
        ["tests/golden/peaks/expected/spectrum.json"],
        ["uv.lock"],
        ["src/pkg/core.py", "pyproject.toml"],
    ],
)
def test_non_python_changes_force_the_full_suite(repo: Path, changed: list[str]) -> None:
    selection = select(repo, changed, has_testmon=True)
    assert selection.mode == "full" and selection.args == []
    assert "non-Python files changed" in selection.reason


def test_mapped_selection_follows_imports(repo: Path) -> None:
    selection = select(repo, ["src/pkg/core.py"])
    assert selection.mode == "mapped"
    assert selection.args == [
        "tests/suite/test_relative.py",  # relative import of a helper that imports pkg.core
        "tests/test_api.py",  # through pkg.api
        "tests/unit/test_core.py",  # named after the module
        "tests/unit/test_sub.py",  # pkg.sub imports leaf, which imports pkg.core
    ]
    assert select(repo, ["src/pkg/other.py"]).args == [
        "tests/test_other.py",
        "tests/test_uses_helper.py",  # bare import of a helper next to the tests
    ]


def test_changed_tests_conftest_and_package_init(repo: Path) -> None:
    assert select(repo, ["tests/test_other.py"]).args == ["tests/test_other.py"]
    assert select(repo, ["tests/suite/conftest.py"]).args == ["tests/suite/test_relative.py"]
    # pkg/__init__.py runs whenever anything in pkg is imported; test_core.py imports nothing.
    assert select(repo, ["src/pkg/__init__.py"]).args == [
        "tests/suite/test_relative.py",
        "tests/test_api.py",
        "tests/test_other.py",
        "tests/test_uses_helper.py",
        "tests/unit/test_sub.py",
    ]
    assert select(repo, ["scripts/tool.py"]) == impact.Selection(
        "none", [], "no test imports the changed modules"
    )


def test_deleted_module_selects_its_importers(repo: Path) -> None:
    (repo / "tests" / "test_gone.py").write_text("from pkg.gone import thing\n", encoding="utf-8")
    assert select(repo, ["src/pkg/gone.py"]).args == ["tests/test_gone.py"]


def test_import_graph_runs_parent_packages(repo: Path) -> None:
    graph = impact.import_graph(repo, impact.python_files(repo))
    assert graph["tests/unit/test_sub.py"] >= {
        "src/pkg/__init__.py",
        "src/pkg/sub/__init__.py",
        "src/pkg/sub/leaf.py",
    }
    assert graph["src/pkg/sub/leaf.py"] == {"src/pkg/__init__.py", "src/pkg/core.py"}


def test_testmon_mode_uses_one_data_file_per_worktree(repo: Path, tmp_path: Path) -> None:
    selection = select(repo, ["src/pkg/core.py"], has_testmon=True)
    assert selection.mode == "testmon" and selection.args == ["--testmon"]
    datafile = Path(selection.env[impact.TESTMON_DATAFILE_ENV])
    assert datafile == repo / ".stepswitch" / "testmondata" and datafile.parent.is_dir()
    state = tmp_path / "state"
    other = select(repo, ["src/pkg/core.py"], has_testmon=True, state_dir=state)
    assert other.env[impact.TESTMON_DATAFILE_ENV] == str(state / "testmondata")
    assert all("tmnet" not in arg for arg in selection.args + other.args)


def test_testmon_is_not_used_when_tmnet_is_configured(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "pytest.ini").write_text("[pytest]\naddopts = --testmon --tmnet\n", encoding="utf-8")
    selection = select(repo, ["src/pkg/core.py"], has_testmon=True)
    assert selection.mode == "mapped" and "--tmnet is configured" in selection.reason
    (repo / "pytest.ini").unlink()
    monkeypatch.setenv("PYTEST_ADDOPTS", "--tmnet")
    assert select(repo, ["src/pkg/core.py"], has_testmon=True).mode == "mapped"


def test_testmon_probe_uses_the_project_interpreter(repo: Path) -> None:
    # Stepswitch's own environment does not install pytest-testmon.
    selection = impact.select_tests(repo, ["src/pkg/core.py"], python=sys.executable)
    assert selection.mode == "mapped"
