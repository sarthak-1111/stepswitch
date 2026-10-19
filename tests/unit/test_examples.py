"""The example configurations (examples/): each loads with the real configuration loader and
builds a worker catalog, keeps keys out of files, and does what its README line says."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from stepswitch import config as config_module
from stepswitch.config import Config
from stepswitch.core import jobs
from stepswitch.serve.manager import default_engines
from stepswitch.verify.science import parse_science
from stepswitch.workers.catalog import Catalog

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
NAMES = sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir())


def project(tmp_path: Path, name: str) -> Path:
    """A project directory set up the way examples/README.md says."""
    root = tmp_path / name
    (root / ".stepswitch").mkdir(parents=True)
    shutil.copy(EXAMPLES / name / "stepswitch.toml", root / "stepswitch.toml")
    shutil.copy(EXAMPLES / name / "local.toml.example", root / ".stepswitch" / "local.toml")
    if (EXAMPLES / name / "science.toml").is_file():
        shutil.copy(EXAMPLES / name / "science.toml", root / "science.toml")
    return root


def load(tmp_path: Path, name: str) -> tuple[Path, Config, Catalog]:
    root = project(tmp_path, name)
    config = config_module.load(root, environ={})
    return root, config, Catalog.from_config(config, root, include_managed=False)


def test_there_are_examples_and_the_readme_lists_each() -> None:
    assert len(NAMES) >= 3
    readme = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    for name in NAMES:
        assert f"[{name}]({name}/)" in readme, name


@pytest.mark.parametrize("name", NAMES)
def test_every_example_loads_and_builds_its_workers(tmp_path: Path, name: str) -> None:
    for required in ("stepswitch.toml", "local.toml.example"):
        assert (EXAMPLES / name / required).is_file(), required
    _root, config, catalog = load(tmp_path, name)
    assert config.schema_version == 1
    ids = {spec.id for spec in catalog.specs()}
    for endpoint in config.endpoints:
        assert endpoint.api_key is None, "keys belong in the environment (api_key_env)"
        for model in endpoint.models:
            assert f"{endpoint.name}/{model}" in ids
    gates = config.model_extra["tracks"] if config.model_extra else {}
    assert gates, "every example sets its track gates"


def test_cpu_laptop_uses_managed_models_only(tmp_path: Path) -> None:
    _root, config, _catalog = load(tmp_path, "cpu-laptop")
    assert config.endpoints == [] and config.storage.max_gb == 8
    assert ".env" in config.privacy.deny_paths


def test_ollama_workstation_is_local(tmp_path: Path) -> None:
    _root, config, catalog = load(tmp_path, "ollama-workstation")
    (endpoint,) = config.endpoints
    assert endpoint.engine == "ollama" and endpoint.is_local()
    assert all(spec.local for spec in catalog.specs())


def test_remote_endpoint_keeps_its_key_in_the_environment(tmp_path: Path) -> None:
    _root, config, catalog = load(tmp_path, "remote-endpoint")
    (endpoint,) = config.endpoints
    assert not endpoint.is_local() and endpoint.auth == "bearer"
    assert endpoint.api_key_env == "HOSTED_MODELS_KEY" and endpoint.api_key is None
    assert config.privacy.redact == "remote" and "*.pem" in config.privacy.deny_paths
    assert not any(spec.local for spec in catalog.specs())
    assert config.model_extra is not None
    assert config.model_extra["tracks"]["science"]["offline"] is True


def test_science_project_has_valid_science_checks(tmp_path: Path) -> None:
    root, config, _catalog = load(tmp_path, "science-project")
    science = parse_science((root / "science.toml").read_text(encoding="utf-8"))
    assert [case.case for case in science.golden] == ["reference"]
    assert science.golden[0].quantities[0].rtol == 1e-6
    assert science.protected[0].names == ["SPEED_OF_LIGHT", "BOLTZMANN"]
    assert config.model_extra is not None
    assert config.model_extra["tracks"]["science"]["gates"][-2:] == ["golden", "full_tests"]


def test_slow_lane_workers_are_pin_only(tmp_path: Path) -> None:
    _root, config, catalog = load(tmp_path, "slow-lane")
    specs = {spec.id: spec for spec in catalog.specs()}
    for worker in ("big-box/big-moe", "offload/big-moe-offload"):
        assert specs[worker].tier == "sl" and specs[worker].pin_only, worker
    assert jobs.timeout_seconds(config) == 21600
    airllm = default_engines(config)["airllm"]
    assert airllm.settings_problem is None
    assert airllm.python is not None and airllm.python.endswith("python")
