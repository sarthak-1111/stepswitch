"""Filesystem locations: project root, per-project state, per-user data, registry directories."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "stepswitch"
STATE_DIR_NAME = ".stepswitch"
PROJECT_CONFIG_NAME = "stepswitch.toml"
LOCAL_CONFIG_NAME = "local.toml"
HOME_ENV = "STEPSWITCH_HOME"


def find_project_root(start: Path | None = None) -> Path:
    """Nearest ancestor with a project config or state dir, else the nearest git root."""
    here = (start or Path.cwd()).resolve()
    candidates = [here, *here.parents]
    for directory in candidates:
        if (directory / PROJECT_CONFIG_NAME).is_file() or (directory / STATE_DIR_NAME).is_dir():
            return directory
    for directory in candidates:
        if (directory / ".git").exists():
            return directory
    return here


def state_dir(root: Path) -> Path:
    return root / STATE_DIR_NAME


def local_config_path(root: Path) -> Path:
    return state_dir(root) / LOCAL_CONFIG_NAME


def project_config_path(root: Path) -> Path:
    return root / PROJECT_CONFIG_NAME


def state_db_path(root: Path) -> Path:
    return state_dir(root) / "state.db"


def user_data_dir() -> Path:
    """Per-user data (engines, models, user registry), overridable with STEPSWITCH_HOME."""
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_NAME


def default_models_dir() -> Path:
    return user_data_dir() / "models"


def engines_dir() -> Path:
    return user_data_dir() / "engines"


def user_registry_dir() -> Path:
    return user_data_dir() / "registry"


def packaged_registry_dir() -> Path | None:
    """The registry shipped with the package, or the repository's registry/ in a source checkout."""
    package_dir = Path(__file__).resolve().parent
    for candidate in (package_dir / "_registry", package_dir.parents[1] / "registry"):
        if candidate.is_dir():
            return candidate
    return None


def registry_dirs() -> list[Path]:
    """Registry directories in increasing precedence: packaged entries, then the user's own."""
    dirs = [d for d in (packaged_registry_dir(), user_registry_dir()) if d is not None]
    return [d for d in dirs if d.is_dir()]
