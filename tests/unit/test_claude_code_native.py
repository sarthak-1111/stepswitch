"""Claude Code native runs and isolated runs (ADR 0034).

A native run is Claude Code alone on its own login: the bench's frontier baseline. It must start
isolated from the user's customizations (safe mode, project settings only, no session files, no
auto memory) and with none of the user's keys, tokens or endpoint variables, so the harness
reads its own login. An isolated proxy run (every bench harness run) gets safe mode too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from stepswitch.workers.claude_code import LIMIT_RE, model_usage
from stepswitch.workers.harness import (
    HarnessEndpoint,
    HarnessEvent,
    HarnessSettings,
    HarnessTask,
    RunResult,
    get_adapter,
)

FAKE_NATIVE = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "fake_claude_native.py"
ENDPOINT = HarnessEndpoint(
    base_url="http://127.0.0.1:47001", token="ssw-run-token", session="t-1", context=131072
)
# What the user's shell could hold: keys, tokens, endpoints and a relocated config directory.
USER_ENV = {
    "PATH": "/usr/bin",
    "HOME": "/home/someone",
    "USERPROFILE": r"C:\Users\someone",
    "APPDATA": r"C:\Users\someone\AppData\Roaming",
    "ANTHROPIC_API_KEY": "user-key",
    "ANTHROPIC_AUTH_TOKEN": "user-token",
    "ANTHROPIC_BASE_URL": "http://gateway.example",
    "ANTHROPIC_MODEL": "some-model",
    "ANTHROPIC_PROFILE": "work",
    "CLAUDE_CODE_OAUTH_TOKEN": "long-lived-token",
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "CLAUDE_CONFIG_DIR": "/elsewhere",
}


def settings(tmp_path: Path, **fields: object) -> HarnessSettings:
    return HarnessSettings(
        state_dir=tmp_path / "state", test_command=["python", "-m", "pytest", "-q"], **fields
    )


def test_a_native_run_is_isolated_and_uses_the_model_it_names(tmp_path: Path) -> None:
    adapter = get_adapter("claude-code")
    assert adapter.native
    task = HarnessTask("Fix add().", resume="an-old-session")
    args = adapter.native_command(task, tmp_path, "claude-sonnet-5-5", settings(tmp_path))
    assert args[:2] == ["-p", "Fix add()."]
    assert args[args.index("--model") + 1] == "claude-sonnet-5-5"
    assert "--safe-mode" in args and "--no-session-persistence" in args
    assert args[args.index("--setting-sources") + 1] == "project,local"
    assert "--strict-mcp-config" in args and args[args.index("--permission-prompts") + 1] == "none"
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert args[args.index("--allowedTools") + 1] == "Bash(python -m pytest -q *)"
    assert "WebFetch" in args and "WebSearch" in args
    # One session: never resumed, and the session ID is a fresh one.
    assert "--resume" not in args and "--session-id" in args


def test_a_native_run_gets_no_key_token_endpoint_or_config_dir(tmp_path: Path) -> None:
    adapter = get_adapter("claude-code")
    env = adapter.native_full_environment(
        HarnessTask("x"), tmp_path, "claude-opus-5-5", settings(tmp_path), base=USER_ENV
    )
    leaked = sorted(set(USER_ENV) & set(env) - {"PATH", "HOME", "USERPROFILE", "APPDATA"})
    assert not leaked
    assert not {"user-key", "user-token", "long-lived-token", "/elsewhere"} & set(env.values())
    # The login lives under the home directory, which the harness still finds.
    home = "USERPROFILE" if sys.platform == "win32" else "HOME"
    assert env[home] == USER_ENV[home]
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_native_runs_need_a_harness_that_has_them_and_are_never_offline(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no native runs"):
        get_adapter("pi").start_native(HarnessTask("x"), tmp_path, "m", settings(tmp_path))
    with pytest.raises(ValueError, match="not offline"):
        get_adapter("claude-code").start_native(
            HarnessTask("x"), tmp_path, "m", settings(tmp_path, offline=True)
        )


def test_an_isolated_proxy_run_gets_safe_mode_and_no_memory(tmp_path: Path) -> None:
    adapter = get_adapter("claude-code")
    plain = settings(tmp_path)
    isolated = settings(tmp_path, isolated=True)
    assert "--safe-mode" not in adapter.command(HarnessTask("x"), tmp_path, ENDPOINT, plain)
    args = adapter.command(HarnessTask("x"), tmp_path, ENDPOINT, isolated)
    assert "--safe-mode" in args
    # A proxy run keeps its session, so a gate retry can resume it.
    assert "--no-session-persistence" not in args and "--setting-sources" not in args
    env = adapter.full_environment(HarnessTask("x"), tmp_path, ENDPOINT, isolated, base={})
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in adapter.full_environment(
        HarnessTask("x"), tmp_path, ENDPOINT, plain, base={}
    )


def finish(
    lines: list[dict[str, object]], exit_code: int = 0
) -> tuple[list[HarnessEvent], RunResult]:
    adapter = get_adapter("claude-code")
    state = adapter.new_state()
    events = [e for line in lines for e in adapter.parse(json.dumps(line), state)]
    return events, adapter.finish(state, exit_code, "", cancelled=False)


def test_the_final_report_gives_tokens_per_model_counted_like_the_ledger() -> None:
    table = {
        "claude-sonnet-5-5": {
            "inputTokens": 100,
            "outputTokens": 20,
            "cacheReadInputTokens": 1000,
            "cacheCreationInputTokens": 50,
        },
        "small": {"inputTokens": 7, "outputTokens": 1},
    }
    assert model_usage(table) == {
        "claude-sonnet-5-5": {"input": 1150, "cached_input": 1000, "cache_write": 50, "output": 20},
        "small": {"input": 7, "cached_input": 0, "cache_write": 0, "output": 1},
    }
    assert model_usage(None) == {} and model_usage({"bad": 3}) == {}
    events, result = finish(
        [
            {"type": "system", "subtype": "init", "model": "m", "apiKeySource": "none"},
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 3,
                "result": "Done.",
                "modelUsage": table,
                "total_cost_usd": 0.25,
            },
        ]
    )
    assert events[0].kind == "start" and events[0].data["auth"] == "none"
    assert result.status == "done" and not result.infra_error
    assert result.models["claude-sonnet-5-5"]["input"] == 1150
    assert result.reported_cost == 0.25
    assert result.as_dict()["models"] == result.models


@pytest.mark.parametrize(
    ("final", "infra"),
    [
        ({"subtype": "success", "is_error": True, "api_error_status": 529, "result": "x"}, True),
        (
            {
                "subtype": "error_during_execution",
                "is_error": True,
                "errors": ["5-hour limit reached"],
            },
            True,
        ),
        ({"subtype": "success", "is_error": True, "result": "Claude AI usage limit reached"}, True),
        ({"subtype": "error_max_turns", "is_error": True, "errors": ["max turns"]}, False),
        ({"subtype": "error_during_execution", "is_error": True, "errors": ["tool failed"]}, False),
    ],
)
def test_infrastructure_errors_are_told_apart_from_the_model_failing(
    final: dict[str, object], infra: bool
) -> None:
    _, result = finish([{"type": "result", "num_turns": 1, **final}], exit_code=1)
    assert result.status != "done" and result.infra_error is infra


def test_a_harness_that_ends_without_its_report_is_an_infrastructure_error() -> None:
    _, result = finish([{"type": "system", "subtype": "init", "model": "m"}], exit_code=3)
    assert result.status == "failed" and result.infra_error
    assert LIMIT_RE.search("You've hit your limit")


def test_the_fake_native_harness_refuses_an_unisolated_start(tmp_path: Path) -> None:
    """The bench tests' stand-in checks what the real start must do; a plain proxy-style start
    with a key in the environment fails its checks."""
    import subprocess

    out = subprocess.run(
        [sys.executable, str(FAKE_NATIVE), "-p", "Fix add().", "--model", "m"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"ANTHROPIC_API_KEY": "user-key", "PATH": ""},
        check=False,
    )
    final = json.loads(out.stdout.splitlines()[-1])
    assert final["is_error"] and "ANTHROPIC_API_KEY is set" in final["errors"]
    assert "missing --safe-mode" in final["errors"]
