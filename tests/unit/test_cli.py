from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

import stepswitch.cli as cli_module
from stepswitch import __version__
from stepswitch.cli import app
from tests.unit.bench_project import make_project

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
    ("command", "options"),
    [
        ("run", ["--track", "--best-of", "--offline", "--budget-tokens", "--worker", "--json"]),
        ("agent", []),
    ],
)
def test_phase_2_commands_exist_with_their_options(command: str, options: list[str]) -> None:
    result = runner.invoke(app, [command, "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "Phase 2" not in result.output
    assert all(option in result.output for option in options), result.output


def test_no_command_is_a_later_phase_stub() -> None:
    # Phase 8 implemented the last stub (`ssw proxy`); `_not_yet` stays for future commands.
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    assert source.count("_not_yet(") == 1, "a command still says it arrives in a later phase"


def test_bench_run_and_report_with_a_fake_contestant(tmp_path: Path) -> None:
    root = make_project(tmp_path / "project")
    out = tmp_path / "results"
    env = {"STEPSWITCH_HOME": str(tmp_path / "home"), "COLUMNS": "200"}
    args = ["-C", str(root), "bench", "run", "--suite", "tiny", "--workers", "fake:oracle"]
    result = runner.invoke(app, [*args, "--out", str(out), "--no-review"], env=env)
    assert result.exit_code == 0, result.output
    assert "fake:oracle" in result.output and "100%" in result.output
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["command"] == (
        "ssw bench run --suite tiny --workers fake:oracle --seeds 1 --no-review"
    )
    (out / "leaderboard.md").unlink()
    report = runner.invoke(app, ["bench", "report", str(out), "--json"], env=env)
    assert report.exit_code == 0, report.output
    assert json.loads(report.output)["contestants"][0]["pass_rate"] == 1.0
    assert (out / "leaderboard.md").is_file()


def test_bench_run_rejects_unknown_workers_before_running(tmp_path: Path) -> None:
    root = make_project(tmp_path / "project")
    env = {"STEPSWITCH_HOME": str(tmp_path / "home")}
    args = ["-C", str(root), "bench", "run", "--suite", "tiny", "--workers", "nowhere/nothing"]
    result = runner.invoke(app, args, env=env)
    assert result.exit_code != 0
    assert "unknown worker(s) nowhere/nothing" in result.output
    assert not (root / "bench" / "results").exists()


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


def _fake_report(problems: list[str]) -> dict[str, object]:
    return {"profile": {"tier": "cpu", "reason": "test"}, "problems": problems}


def test_doctor_exits_1_when_the_report_lists_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stepswitch import doctor

    monkeypatch.setattr(doctor, "collect", lambda **_: _fake_report(["sqlite has the WAL bug"]))
    monkeypatch.setattr(doctor, "write_profile", lambda *_: None)
    result = runner.invoke(app, ["-C", str(tmp_path), "doctor", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["problems"] == ["sqlite has the WAL bug"]


def test_doctor_warns_when_the_profile_cannot_be_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stepswitch import doctor

    runner.invoke(app, ["-C", str(tmp_path), "init", "--no-detect"])

    def conflict(*_: object) -> None:
        # (msg, doc, pos): Python 3.14 deprecates free-form arguments; older versions take any.
        raise tomllib.TOMLDecodeError("Cannot declare ('profile',) twice", "", 0)

    monkeypatch.setattr(doctor, "collect", lambda **_: _fake_report([]))
    monkeypatch.setattr(doctor, "write_profile", conflict)
    result = runner.invoke(app, ["-C", str(tmp_path), "doctor", "--json"])
    assert result.exit_code == 0, result.output
    assert "profile not recorded" in result.stderr


@pytest.mark.parametrize(
    ("engines", "engine", "installs"),
    [
        ({"vllm": {}}, None, False),  # only vLLM can serve it: no llama.cpp offer
        ({"llamacpp": {}, "vllm": {}}, None, True),
        ({"vllm": {}}, "llamacpp", True),
    ],
)
def test_models_serve_offers_llamacpp_only_when_it_would_serve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engines: dict[str, Any],
    engine: str | None,
    installs: bool,
) -> None:
    from stepswitch import registry
    from stepswitch.serve import install, llamacpp
    from stepswitch.serve.manager import ServerManager

    offered: list[bool] = []
    monkeypatch.setattr(registry, "find", lambda name, dirs=None: SimpleNamespace(engines=engines))
    monkeypatch.setattr(llamacpp.LlamaCppEngine, "find_binary", lambda self: None)
    monkeypatch.setattr(install, "install_llamacpp", lambda **kwargs: offered.append(True))
    served = SimpleNamespace(model="m", engine="vllm", base_url="http://127.0.0.1:1", pid=1)
    monkeypatch.setattr(ServerManager, "start", lambda self, model, **kwargs: served)
    args = ["-C", str(tmp_path), "models", "serve", "m", "--yes"]
    result = runner.invoke(
        app,
        [*args, *(["--engine", engine] if engine else [])],
        env={"STEPSWITCH_HOME": str(tmp_path)},
    )
    assert result.exit_code == 0, result.output
    assert offered == ([True] if installs else [])


def test_dash_prints_a_loopback_link_with_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    served: list[Any] = []

    def fake_run(self: Any, sockets: Any = None) -> None:
        served.append(self.config)

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    (tmp_path / ".stepswitch").mkdir()
    result = runner.invoke(
        app, ["-C", str(tmp_path), "dash", "--no-open", "--port", "7791", "--max-tasks", "2"]
    )
    assert result.exit_code == 0, result.output
    (config,) = served
    assert (config.host, config.port, config.access_log) == ("127.0.0.1", 7791, False)
    link = re.search(r"http://127\.0\.0\.1:7791/\?token=([\w-]+)", result.output)
    assert link is not None and len(link.group(1)) >= 32


def test_dash_refuses_a_port_in_use(tmp_path: Path) -> None:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        result = runner.invoke(app, ["-C", str(tmp_path), "dash", "--no-open", "--port", str(port)])
    assert result.exit_code == 1
    assert f"port {port} is in use" in result.output
