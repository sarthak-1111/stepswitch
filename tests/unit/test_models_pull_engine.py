"""`ssw models pull` installs the pinned llama.cpp build that serves the GGUF files it pulled,
when no llama-server is found, and reports how long that took (the quickstart and C-017 time
the engine apart from the model download)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from stepswitch import modelstore
from stepswitch.cli import app
from stepswitch.serve import install, llamacpp
from stepswitch.serve.base import EngineError


@pytest.fixture
def pulled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """A project whose pull plan and download are stubbed: one model ends up installed."""
    monkeypatch.chdir(tmp_path)
    seen: dict[str, Any] = {"installs": 0, "binary": None, "error": None}
    monkeypatch.setattr(modelstore, "select", lambda tier, names, **kw: ["entry"])
    monkeypatch.setattr(modelstore, "plan", lambda entries, **kw: "plan")
    monkeypatch.setattr(modelstore, "describe", lambda plan: "the plan")
    monkeypatch.setattr(
        modelstore,
        "execute",
        lambda plan, **kw: modelstore.PullResult(installed=["tiny"], downloaded=["tiny"]),
    )
    monkeypatch.setattr(llamacpp.LlamaCppEngine, "find_binary", lambda self: seen["binary"])

    def fake_install(**kwargs: Any) -> Path:
        seen["installs"] += 1
        confirm = kwargs["confirm"]
        if not confirm("Download llama.cpp?"):
            raise EngineError("llama.cpp installation cancelled")
        if seen["error"]:
            raise EngineError(seen["error"])
        return tmp_path / "engines" / "llama-server"

    monkeypatch.setattr(install, "install_llamacpp", fake_install)
    return seen


def pull(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(app, ["models", "pull", "--tier", "cpu", *args], input=input)


def test_a_missing_engine_is_installed_and_timed(pulled: dict[str, Any]) -> None:
    result = pull("--yes", "--json")
    assert result.exit_code == 0, result.output
    engine = json.loads(result.output)["engine"]
    assert engine["status"] == "installed" and engine["tag"] == install.PINNED_LLAMACPP
    assert engine["path"].endswith("llama-server") and engine["seconds"] >= 0
    assert pulled["installs"] == 1


def test_an_installed_engine_is_left_alone(pulled: dict[str, Any], tmp_path: Path) -> None:
    pulled["binary"] = tmp_path / "llama-server"
    engine = json.loads(pull("--yes", "--json").output)["engine"]
    assert engine["status"] == "present" and pulled["installs"] == 0


def test_no_engine_install_and_other_engines_install_nothing(pulled: dict[str, Any]) -> None:
    assert json.loads(pull("--yes", "--json", "--no-engine-install").output)["engine"] is None
    ollama = pull("--yes", "--json", "--engine", "ollama")
    assert json.loads(ollama.output)["engine"] is None and pulled["installs"] == 0


def test_a_failed_install_fails_the_pull(pulled: dict[str, Any]) -> None:
    pulled["error"] = "no build for this platform"
    result = pull("--yes")
    assert result.exit_code == 1
    assert "llama.cpp was not installed: no build for this platform" in result.output


def test_declining_the_engine_is_not_a_failure(pulled: dict[str, Any]) -> None:
    result = pull(input="n\n")
    assert result.exit_code == 0, result.output
    assert "llama.cpp was not installed" not in result.output
    output = pull("--json", input="n\n").output  # the question comes first, then the JSON
    assert json.loads(output[output.index("{") :])["engine"]["status"] == "declined"
