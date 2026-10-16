"""Locating installed models and engines for claim tests that run real models.

Model files are found in `STEPSWITCH_MODELS_DIR` when it is set, otherwise in the models
directory of the checkout's own configuration (`ssw models pull --tier cpu` puts them there).
Nothing is downloaded here: when a model or the engine is missing the test reports the claim
as pending and says how to provide it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from stepswitch import config as config_module
from stepswitch import registry
from stepswitch.registry import ModelEntry
from stepswitch.serve.llamacpp import find_binary

ROOT = Path(__file__).resolve().parents[2]
MODELS_ENV = "STEPSWITCH_MODELS_DIR"


def models_dir() -> Path:
    override = os.environ.get(MODELS_ENV)
    if override:
        return Path(override).expanduser()
    return config_module.load(ROOT).models_dir()


def installed(model_id: str) -> tuple[ModelEntry, Path] | None:
    """The registry entry and the models directory, when the model's files are installed."""
    entry = registry.load().get(model_id)
    if entry is None:
        return None
    directory = models_dir()
    return (entry, directory) if entry.installed(directory) else None


def llamacpp_server() -> Path | None:
    return find_binary()


def ssw_env(**extra: str) -> dict[str, str]:
    """The environment for `ssw` subprocesses: no inherited Stepswitch settings."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("STEPSWITCH_")}
    env.update({"PYTHONUTF8": "1", "NO_COLOR": "1"})
    env.update(extra)
    return env


@contextmanager
def stopping_servers(repo: Path, env: dict[str, str]) -> Iterator[None]:
    """Stop every model server the project at `repo` started, however the block ends.
    Managed servers outlive `ssw run` by design (later runs reuse them), so a test that runs
    real models in a scratch project must stop them itself."""
    try:
        yield
    finally:
        ssw(repo, "models", "stop", "--all", env=env, timeout=120)


def ssw(repo: Path, *args: str, env: dict[str, str], timeout: float = 1800) -> Any:
    return subprocess.run(
        [sys.executable, "-m", "stepswitch", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        check=False,
    )
