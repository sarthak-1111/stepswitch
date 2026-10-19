"""The README's demo: docs/demo/demo.tape, the demo project's policy and scripts/record_demo.py
(the recording itself needs Docker and the models, so it is not run here)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from stepswitch import config as config_module
from stepswitch import registry
from stepswitch.core.task import STEP_TYPES
from stepswitch.router.rules import rules_from_config
from tests.claims.toy import TOY_INSTRUCTION

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "docs" / "demo"


def recorder() -> ModuleType:
    path = ROOT / "scripts" / "record_demo.py"
    spec = importlib.util.spec_from_file_location("record_demo", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_tape_records_the_toy_task_into_the_readme_gif() -> None:
    tape = (DEMO / "demo.tape").read_text(encoding="utf-8")
    assert "Output docs/assets/demo.gif" in tape.splitlines()
    assert "Require ssw" in tape.splitlines()
    assert f"Type \"ssw run '{TOY_INSTRUCTION}'\"" in tape
    assert "Wait+Screen /Trajectory:/" in tape  # the last line `ssw run` prints
    assert "docs/assets/demo.gif" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_demo_policy_pins_every_step_type_to_one_model(tmp_path: Path) -> None:
    (tmp_path / "stepswitch.toml").write_bytes((DEMO / "stepswitch.toml").read_bytes())
    rules = rules_from_config(config_module.load(tmp_path, environ={}))
    covered: dict[str, str] = {}
    for rule in rules:
        assert rule.to is not None and set(rule.to) == {"model"}, rule
        for step in rule.when["step_type"]:
            assert step not in covered, step
            covered[step] = rule.to["model"]
    assert set(covered) == set(STEP_TYPES)
    tiny = {entry.id for entry in registry.for_tier("cpu")}
    assert covered["plan"] in tiny  # the tiny tier plans, as the tape's caption says
    assert covered["review"] != covered["edit"]


def test_the_recording_container_gets_only_the_hosts_ollama(tmp_path: Path) -> None:
    demo = recorder()
    (tmp_path / ".stepswitch").mkdir()
    (tmp_path / ".stepswitch" / "local.toml").write_text(demo.LOCAL_TOML, encoding="utf-8")
    loaded = config_module.load(tmp_path, environ={})
    (endpoint,) = loaded.endpoints
    assert endpoint.url == "http://host.docker.internal:11434" and endpoint.is_local()
    assert endpoint.api_key is None and endpoint.api_key_env is None
    rules = (DEMO / "stepswitch.toml").read_text(encoding="utf-8")
    for model in demo.OLLAMA_MODELS:
        assert model in endpoint.models and f'"{model}"' in rules
    assert re.fullmatch(r"ghcr\.io/charmbracelet/vhs:v[\d.]+@sha256:[0-9a-f]{64}", demo.IMAGE)
    for step in ("libgomp1", "python3-venv", "sh /src/install.sh", "pip install -q pytest"):
        assert step in demo.SETUP_SH, step
    assert "ssw models pull --tier cpu --yes" in demo.SETUP_SH
    assert demo.SETUP_SH.rstrip().endswith("vhs /demo/demo.tape")
