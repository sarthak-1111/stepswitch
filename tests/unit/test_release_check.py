"""scripts/release_check.py: a release tag must match the package version and the version
both installers pin, and the dashboard must be built."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def release_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_check", ROOT / "scripts" / "release_check.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(tmp_path: Path, version: str, *, pinned: str, dashboard: bool = True) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "stepswitch"\nversion = "{version}"\n', encoding="utf-8"
    )
    for name in ("install.sh", "install.ps1"):
        text = (ROOT / name).read_text(encoding="utf-8")
        text = text.replace('STEPSWITCH_VERSION="0.1.0"', f'STEPSWITCH_VERSION="{pinned}"')
        text = text.replace("$StepswitchVersion = '0.1.0'", f"$StepswitchVersion = '{pinned}'")
        (root / name).write_text(text, encoding="utf-8")
    if dashboard:
        (root / "dashboard" / "dist").mkdir(parents=True)
        (root / "dashboard" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    return root


def test_a_consistent_release_passes(tmp_path: Path) -> None:
    check = release_check()
    assert check.problems("v1.2.3", project(tmp_path, "1.2.3", pinned="1.2.3")) == []


def test_each_mismatch_is_reported(tmp_path: Path) -> None:
    check = release_check()
    root = project(tmp_path, "1.2.3.dev0", pinned="1.2.2", dashboard=False)
    found = check.problems("v1.2.3", root)
    assert any("pyproject.toml has version 1.2.3.dev0" in p for p in found)
    assert any(p.startswith("install.sh pins 1.2.2") for p in found)
    assert any(p.startswith("install.ps1 pins 1.2.2") for p in found)
    assert any("the dashboard is not built" in p for p in found)
    assert check.problems("1.2.3", root) == ["the tag '1.2.3' is not vX.Y.Z"]
    shutil.rmtree(root)


def test_the_installers_carry_the_pins_the_check_reads() -> None:
    check = release_check()
    for name, pattern in check.INSTALLER_PINS.items():
        assert pattern.search((ROOT / name).read_text(encoding="utf-8")), name
