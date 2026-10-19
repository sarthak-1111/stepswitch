"""`ssw prefetch` (SPEC §15, `stepswitch.prefetch`): what context services need offline is
fetched before any task, and the check reports what each service has."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from stepswitch import modelstore, net, prefetch
from stepswitch.cli import app
from stepswitch.context import tokens

CONFIG = SimpleNamespace(model_extra={})


def test_the_check_reports_missing_tokenizer_files(tmp_path: Path) -> None:
    items = prefetch.check(CONFIG, tmp_path / "models")
    names = {item.name for item in items if item.service == "tokenizers"}
    assert set(tokens.load_specs()) <= names
    missing = [i for i in items if i.service == "tokenizers" and i.status == "missing"]
    assert missing and not prefetch.ready(items)
    assert all(i.status == "ready" for i in items if i.service == "tree-sitter")


def test_present_tokenizer_files_are_ready(tmp_path: Path) -> None:
    models = tmp_path / "models"
    for spec in tokens.load_specs().values():
        path = spec.local_path(models)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * (spec.size_bytes or 1))
    items = {i.name: i for i in prefetch.check(CONFIG, models) if i.service == "tokenizers"}
    for name in tokens.load_specs():
        assert items[name].status == "ready" and items[name].detail.endswith(".json"), name


def test_prefetch_marks_downloads_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs = sorted(tokens.load_specs())
    first, rest = specs[0], specs[1:]

    def fake_pull(names: list[str], **kwargs: object) -> modelstore.PullResult:
        spec = tokens.load_specs()[first]
        path = spec.local_path(kwargs["models_dir"])  # type: ignore[arg-type]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * (spec.size_bytes or 1))
        result = modelstore.PullResult()
        result.downloaded.append(first)
        result.failed.extend((name, "network down") for name in rest)
        return result

    monkeypatch.setattr(modelstore, "pull_tokenizers", fake_pull)
    items = {i.name: i for i in prefetch.prefetch(CONFIG, tmp_path / "models", echo=lambda _: None)}
    assert items[first].status == "downloaded"
    for name in rest:
        assert items[name].status == "failed" and items[name].detail == "network down"


def test_offline_prefetch_downloads_nothing(tmp_path: Path) -> None:
    egress = net.Egress(net.EgressPolicy(offline=True))
    items = prefetch.prefetch(CONFIG, tmp_path / "models", egress=egress, echo=lambda _: None)
    failed = [i for i in items if i.status == "failed"]
    assert failed and all("offline mode" in i.detail for i in failed)
    assert list(egress.memory) == []


def test_cli_check_prints_json_and_fails_when_something_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STEPSWITCH_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["prefetch", "--check", "--json"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["ready"] is False
    assert {"service", "name", "status", "detail"} <= set(data["items"][0])
