from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stepswitch import __version__
from stepswitch.cli import app

ROOT = Path(__file__).resolve().parents[2]
SPEC_COMMANDS = [
    "init",
    "doctor",
    "models",
    "probe",
    "calibrate",
    "run",
    "agent",
    "proxy",
    "bench",
    "dash",
    "replay",
    "prove",
    "policy",
    "audit",
]

runner = CliRunner()


def _words(text: str) -> set[str]:
    return set(re.findall(r"[\w-]+", text))


def test_help_lists_every_spec_command() -> None:
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert set(SPEC_COMMANDS) <= _words(result.output)


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.parametrize(
    ("args", "phase"),
    [
        (["run", "fix the bug"], 2),
        (["agent"], 2),
        (["proxy"], 8),
        (["bench", "run", "--suite", "mini-general"], 3),
        (["bench", "report"], 3),
        (["dash"], 4),
        (["replay", "run-1"], 7),
    ],
)
def test_later_phase_commands_say_so_and_exit_2(args: list[str], phase: int) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert f"Phase {phase}" in result.output


def test_models_list_json_reports_the_cpu_tier(tmp_path: Path) -> None:
    env = {"STEPSWITCH_HOME": str(tmp_path / "home"), "STEPSWITCH_MODELS_DIR": str(tmp_path)}
    result = runner.invoke(
        app, ["-C", str(tmp_path), "models", "list", "--tier", "cpu", "--json"], env=env
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data["models"]) >= 3
    assert all(row["installed"] is False for row in data["models"])
    assert data["models_dir"] == str(tmp_path)


def test_init_creates_files_without_detection(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    result = runner.invoke(
        app,
        ["-C", str(tmp_path), "init", "--no-detect", "--json"],
        env={"STEPSWITCH_HOME": str(tmp_path / "home")},
    )
    assert result.exit_code == 0, result.output
    assert tomllib.loads((tmp_path / "stepswitch.toml").read_text(encoding="utf-8"))["schema"] == 1
    assert (tmp_path / ".stepswitch" / "local.toml").is_file()
    assert (tmp_path / ".stepswitch" / "state.db").is_file()
    assert ".stepswitch/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
    again = runner.invoke(app, ["-C", str(tmp_path), "init", "--no-detect", "--json"])
    assert again.exit_code == 0
    assert json.loads(again.output)["created"] == []


def test_policy_show_redacts_keys(tmp_path: Path) -> None:
    runner.invoke(app, ["-C", str(tmp_path), "init", "--no-detect"])
    local = tmp_path / ".stepswitch" / "local.toml"
    local.write_text(
        local.read_text(encoding="utf-8")
        + '\n[[endpoints]]\nname = "remote"\nurl = "https://example.invalid/v1"\n'
        'auth = "bearer"\napi_key = "very-secret-value"\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["-C", str(tmp_path), "policy", "show", "--json"])
    assert result.exit_code == 0, result.output
    assert "very-secret-value" not in result.output
    assert json.loads(result.output)["endpoints"][0]["api_key"] == "<redacted>"


def test_audit_without_project_is_empty(tmp_path: Path) -> None:
    result = runner.invoke(app, ["-C", str(tmp_path), "audit", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"rows": []}


def test_prove_readme_check_passes_on_the_repository() -> None:
    result = runner.invoke(app, ["-C", str(ROOT), "prove", "--render-readme", "--check"])
    assert result.exit_code == 0, result.output


def test_pull_needs_names_or_tier() -> None:
    result = runner.invoke(app, ["models", "pull"])
    assert result.exit_code == 1
    assert "--tier" in result.output
