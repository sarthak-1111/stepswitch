"""The compatibility matrix: probe results in, verdicts and docs/COMPATIBILITY.md out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from stepswitch.cli import app
from stepswitch.compat import matrix, probe

PASSING = {
    "basic_chat": True,
    "system_prompt": True,
    "native_tool_call": False,
    "fallback_tool_protocol": True,
    "usage": True,
    "formats_served": True,
    "streaming": True,
    "parallel_tool_calls": None,
}


def result(model: str, checks: dict[str, bool | None], **extra: Any) -> dict[str, Any]:
    return {
        "schema": probe.SCHEMA,
        "kind": probe.EXPORT_KIND,
        "date": "2026-10-16",
        "model": model,
        "model_hash": "sha256:" + "0" * 64,
        "engine": "llamacpp",
        "engine_version": "b11669",
        "format": "chat-completions",
        "quick": False,
        "hardware": {
            "os": {"system": "Linux", "machine": "x86_64"},
            "cpu": {"cores_physical": 4, "cores_logical": 8},
            "ram_gb": 16.0,
            "gpus": [],
        },
        "checks": {name: {"ok": ok, "detail": ""} for name, ok in checks.items()},
        **extra,
    }


def make_root(tmp_path: Path, results: dict[str, dict[str, Any]]) -> Path:
    root = tmp_path / "checkout"
    folder = root / "probes"
    folder.mkdir(parents=True)
    (folder / "ci-set.yaml").write_text(
        "schema: 1\nengine: llamacpp\nmodels: [qwen3-4b-instruct-2507, phi-3.5-mini-instruct]\n",
        encoding="utf-8",
    )
    for name, data in results.items():
        (folder / name).write_text(json.dumps(data), encoding="utf-8")
    return root


def test_verdict_needs_the_agent_checks_and_one_tool_path() -> None:
    ok = matrix.ProbeResult(
        Path("a.json"),
        "m",
        "llamacpp",
        "b1",
        "chat-completions",
        "2026-10-16",
        False,
        None,
        checks={k: {"ok": v} for k, v in PASSING.items()},
    )
    assert matrix.verdict(ok) == matrix.Verdict(True, [], "fallback")
    no_tools = {**PASSING, "fallback_tool_protocol": False}
    bad = matrix.ProbeResult(
        Path("b.json"),
        "m",
        None,
        None,
        None,
        None,
        True,
        None,
        checks={k: {"ok": v} for k, v in {**no_tools, "system_prompt": False}.items()},
    )
    assert matrix.verdict(bad) == matrix.Verdict(
        False, ["system_prompt", "tool calls", "full probe"], None
    )


def test_render_lists_ci_models_first_with_checks_and_hardware(tmp_path: Path) -> None:
    root = make_root(
        tmp_path,
        {
            "another-model--llamacpp.json": result("another-model", PASSING),
            "phi-3.5-mini-instruct--llamacpp.json": result("phi-3.5-mini-instruct", PASSING),
            "qwen3-4b-instruct-2507--llamacpp.json": result(
                "qwen3-4b-instruct-2507", {**PASSING, "native_tool_call": True}
            ),
        },
    )
    text = matrix.render(root)
    assert matrix.GENERATED_MARKER in text
    rows = [line for line in text.splitlines() if line.startswith("| ") and "`" in line]
    models = [row.split("|")[1].strip() for row in rows[:3]]
    assert models == ["qwen3-4b-instruct-2507", "phi-3.5-mini-instruct", "another-model"]
    assert "| qwen3-4b-instruct-2507 | qwen3 | llamacpp b11669 |" in text
    assert "| passes | native |" in text and "| passes | fallback |" in text
    assert "| basic chat | yes | yes | yes |" in text
    assert "| parallel tool calls | skipped | skipped | skipped |" in text
    assert "| `another-model--llamacpp.json` | Linux | x86_64 | 8 | 16.0 | 0 |" in text


def test_write_and_check(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"m--llamacpp.json": result("m", PASSING)})
    assert not matrix.is_current(root)
    matrix.write(root)
    assert matrix.is_current(root)
    (root / "probes" / "n--llamacpp.json").write_text(
        json.dumps(result("n", PASSING)), encoding="utf-8"
    )
    assert not matrix.is_current(root)


def test_results_must_be_probe_exports(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"x.json": {"kind": "something-else", "schema": 1}})
    with pytest.raises(matrix.MatrixError, match="not a probe result"):
        matrix.load_results(root)


def test_cli_render_matrix_and_check(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"m--llamacpp.json": result("m", PASSING)})
    (root / "claims").mkdir()
    (root / "claims" / "claims.yaml").write_text("version: 1\n", encoding="utf-8")
    (root / "tests" / "claims").mkdir(parents=True)
    runner = CliRunner()
    env = {"STEPSWITCH_HOME": str(tmp_path / "home")}
    stale = runner.invoke(app, ["-C", str(root), "probe", "--render-matrix", "--check"], env=env)
    assert stale.exit_code == 1 and "out of date" in stale.output
    wrote = runner.invoke(app, ["-C", str(root), "probe", "--render-matrix"], env=env)
    assert wrote.exit_code == 0, wrote.output
    fresh = runner.invoke(app, ["-C", str(root), "probe", "--render-matrix", "--check"], env=env)
    assert fresh.exit_code == 0, fresh.output
