"""Contract tests for the harness adapters (SPEC §12, §23 P8): recorded fixtures per adapter.

`tests/fixtures/harness/<adapter>/fixture.json` names the transcripts of each adapter (`cases`),
where they come from (`source`: recorded from a live run, or documented: written from the
harness's verified documentation in docs/RESEARCH.md) and what the adapter must make of them:

* Line-protocol adapters (claude-code, pi, oh-my-pi): every transcript is parsed line by line,
  and replayed through the adapter's subprocess path by a stand-in harness
  (`tests/fixtures/harness/replay_stream.py`); both must give the expected result and events.
* ACP adapters (opencode, kimi-code, acp): the ACP client runs against the mock agent
  (`tests/conformance/acp/mock_agent.py`), which replays the transcript strictly, so every
  message the client sends is checked too.
* Every adapter's command and environment point the harness at the proxy with the run's own
  token, carry none of the user's credentials, keep the harness's state in the run's directory
  and set the harness's egress switches.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from stepswitch.workers.acp import decide
from stepswitch.workers.harness import (
    ADAPTERS,
    HarnessEndpoint,
    HarnessEvent,
    HarnessSettings,
    HarnessTask,
    RunResult,
    get_adapter,
    shell_command,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "harness"
REPLAY = FIXTURES / "replay_stream.py"
MOCK_AGENT = ROOT / "tests" / "conformance" / "acp" / "mock_agent.py"
STREAM_ADAPTERS = ("claude-code", "pi", "oh-my-pi")
ACP_ADAPTERS = ("opencode", "kimi-code", "acp")
TOKEN = "ssw-run-token-for-tests"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name / "fixture.json").read_text(encoding="utf-8"))


def cases(names: tuple[str, ...]) -> list[tuple[str, str]]:
    return [(name, case) for name in names for case in fixture(name)["cases"]]


def endpoint(context: int | None = 131072) -> HarnessEndpoint:
    return HarnessEndpoint(
        base_url="http://127.0.0.1:47001",
        token=TOKEN,
        session="t-test",
        slots={"main": "ssw:auto", "plan": "ssw:plan", "fast": "ssw:auto", "review": "ssw:review"},
        context=context,
    )


def check(result: RunResult, events: list[HarnessEvent], expect: dict[str, Any]) -> None:
    detail = f"{result.as_dict()}"
    assert result.status == expect["status"], detail
    assert result.stop_reason == expect.get("stop_reason"), detail
    for key in ("summary", "session_id", "turns", "tool_calls", "version"):
        if key in expect:
            assert getattr(result, key) == expect[key], f"{key}: {detail}"
    if "usage" in expect:
        assert result.usage == expect["usage"], detail
    if "detail_contains" in expect:
        assert expect["detail_contains"] in result.detail, detail
    counts = Counter(event.kind for event in events)
    for kind, count in expect.get("events", {}).items():
        assert counts[kind] == count, f"{kind}: {dict(counts)}"
    if "permissions" in expect:
        decided = [bool(e.data["allowed"]) for e in events if e.kind == "permission"]
        assert decided == expect["permissions"]


def test_every_adapter_has_fixtures_from_a_known_source() -> None:
    assert set(ADAPTERS) == set(STREAM_ADAPTERS) | set(ACP_ADAPTERS)
    for name in ADAPTERS:
        meta = fixture(name)
        assert meta["harness"] == name
        assert meta["source"] in {"recorded", "documented"}
        assert meta["checked"] and meta["sources"]
        for case, spec in meta["cases"].items():
            assert (FIXTURES / name / f"{case}.jsonl").is_file(), case
            assert spec["source"] in {"recorded", "documented"}
        assert ADAPTERS[name].offline_env, name


# ----------------------------------------------------------------------------------------
# Line-protocol adapters
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "case"), cases(STREAM_ADAPTERS))
def test_stream_transcripts_parse_to_the_expected_result(name: str, case: str) -> None:
    adapter = get_adapter(name)
    spec = fixture(name)["cases"][case]
    state = adapter.new_state()
    events: list[HarnessEvent] = []
    text = (FIXTURES / name / f"{case}.jsonl").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip():
            events += adapter.parse(line, state)
    result = adapter.finish(state, spec.get("exit_code", 0), "", cancelled=False)
    check(result, events, spec["expect"])


@pytest.mark.parametrize(("name", "case"), cases(STREAM_ADAPTERS))
def test_stream_transcripts_replay_through_the_subprocess_path(
    name: str, case: str, tmp_path: Path
) -> None:
    adapter = get_adapter(name)
    spec = fixture(name)["cases"][case]
    transcript = FIXTURES / name / f"{case}.jsonl"
    settings = HarnessSettings(
        command=[sys.executable, str(REPLAY), str(transcript), str(spec.get("exit_code", 0))],
        state_dir=tmp_path / "state",
        transcript=tmp_path / "out" / "attempt-1.jsonl",
        log=tmp_path / "out" / "attempt-1.stderr.log",
    )
    workdir = tmp_path / "work"
    workdir.mkdir()
    handle = adapter.start(HarnessTask(prompt="Fix add()."), workdir, endpoint(), settings)
    events = list(handle.events())
    result = handle.result(timeout=60)
    check(result, events, spec["expect"])
    # The raw lines are kept as the run's transcript.
    kept = (tmp_path / "out" / "attempt-1.jsonl").read_text(encoding="utf-8").splitlines()
    assert kept == [line for line in transcript.read_text("utf-8").splitlines() if line.strip()]


def test_claude_code_runs_against_the_proxy_with_its_own_config_dir(tmp_path: Path) -> None:
    adapter = get_adapter("claude-code")
    settings = HarnessSettings(
        state_dir=tmp_path / "state", test_command=["python", "-m", "pytest", "-q"], max_turns=30
    )
    base = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "user-key", "ANTHROPIC_AUTH_TOKEN": "user"}
    env = adapter.full_environment(HarnessTask("x"), tmp_path, endpoint(), settings, base=base)
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:47001"
    assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN
    assert "ANTHROPIC_API_KEY" not in env and "user-key" not in env.values()
    assert (
        env["ANTHROPIC_MODEL"] == "ssw:auto" and env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "ssw:plan"
    )
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "131072"
    assert env["CLAUDE_CODE_GATEWAY_HINT_HEADERS"] == "1"
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == "x-stepswitch-session: t-test"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    config_dir = Path(env["CLAUDE_CONFIG_DIR"])
    assert config_dir.is_dir() and config_dir.is_relative_to(tmp_path / "state")
    args = adapter.command(HarnessTask("x"), tmp_path, endpoint(), settings)
    assert args[:5] == ["-p", "x", "--output-format", "stream-json", "--verbose"]
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert args[args.index("--allowedTools") + 1] == "Bash(python -m pytest -q *)"
    assert args[args.index("--permission-prompts") + 1] == "none"
    assert "WebFetch" in args and "--strict-mcp-config" in args
    assert args[args.index("--max-turns") + 1] == "30" and "--session-id" in args
    assert adapter.stdin_text(HarnessTask("the prompt")) is None
    dash = adapter.command(HarnessTask("--help"), tmp_path, endpoint(), settings)
    assert dash[1] == "Task: --help"  # never read as an option
    resumed = adapter.command(HarnessTask("x", resume="abc"), tmp_path, endpoint(), settings)
    assert resumed[resumed.index("--resume") + 1] == "abc" and "--session-id" not in resumed
    every = HarnessSettings(state_dir=tmp_path / "state", permission="all")
    args = adapter.command(HarnessTask("x"), tmp_path, endpoint(), every)
    assert args[args.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--allowedTools" not in args


def test_the_test_command_is_allowed_as_the_prompt_spells_it(tmp_path: Path) -> None:
    # A live run on Windows: the rule had backslashes, the model typed forward slashes into
    # the POSIX shell tool, and the test command was denied.
    python = r"C:\proj\.venv\Scripts\python.exe"
    assert shell_command([python, "-m", "pytest"]) == "C:/proj/.venv/Scripts/python.exe -m pytest"
    assert shell_command([r"C:\Program Files\py\python.exe", "-q"]) == (
        "'C:/Program Files/py/python.exe' -q"
    )
    assert shell_command(["/usr/bin/python3", "-m", "pytest"]) == "/usr/bin/python3 -m pytest"
    settings = HarnessSettings(
        state_dir=tmp_path / "state", test_command=[python, "-m", "pytest", "-q"]
    )
    shown, written = "C:/proj/.venv/Scripts/python.exe -m pytest -q", f"{python} -m pytest -q"
    args = get_adapter("claude-code").command(HarnessTask("x"), tmp_path, endpoint(), settings)
    start = args.index("--allowedTools") + 1
    assert args[start : start + 2] == [f"Bash({shown} *)", f"Bash({written} *)"]
    env = get_adapter("opencode").full_environment(
        HarnessTask("x"), tmp_path, endpoint(), settings, base={}
    )
    rules = json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"]["bash"]
    assert rules[shown] == rules[f"{shown} *"] == rules[f"{written} *"] == "allow"
    for command, allowed in (
        (f"{shown} --tb=short", True),
        (f"{written} -x", True),
        ("rm -rf .", False),
    ):
        call = {"kind": "execute", "rawInput": {"command": command}}
        assert decide(call, tmp_path, settings)[0] is allowed, command


def test_pi_and_oh_my_pi_get_a_generated_provider_in_their_own_agent_dir(tmp_path: Path) -> None:
    for name, files in (("pi", ("models.json",)), ("oh-my-pi", ("models.yml", "config.yml"))):
        adapter = get_adapter(name)
        settings = HarnessSettings(state_dir=tmp_path / name, offline=True)
        env = adapter.full_environment(HarnessTask("x"), tmp_path, endpoint(), settings, base={})
        agent_dir = Path(env["PI_CODING_AGENT_DIR"])
        assert agent_dir.is_relative_to(tmp_path / name)
        models = json.loads((agent_dir / files[0]).read_text("utf-8"))  # JSON is valid YAML
        provider = models["providers"]["stepswitch"]
        assert provider["baseUrl"] == "http://127.0.0.1:47001/v1"
        assert TOKEN not in json.dumps(models)  # the key is read from the environment
        assert env["STEPSWITCH_PROXY_TOKEN"] == TOKEN
        ids = [model["id"] for model in provider["models"]]
        assert ids == ["ssw-auto", "ssw-plan", "ssw-review"]  # selectors: no ':' in slot IDs
        assert provider["models"][0]["contextWindow"] == 131072
        assert env["PI_TELEMETRY"] == "0" and env["PI_OFFLINE"] == "1"
        assert env["STEPSWITCH_CONTEXT_TOKENS"] == "131072"
        args = adapter.command(HarnessTask("Fix it."), tmp_path, endpoint(), settings)
        # Pi selects the extension's routed virtual model; Oh My Pi has none: the main slot.
        selected = "stepswitch/routed" if name == "pi" else "stepswitch/ssw-auto"
        assert args[args.index("--model") + 1] == selected
        assert args[-1] == "Fix it." and "--no-session" in args
        if name == "pi":
            assert "--no-extensions" in args  # never the repository's own extensions
        if name == "oh-my-pi":
            roles = json.loads((agent_dir / "config.yml").read_text("utf-8"))["modelRoles"]
            assert roles["default"] == "stepswitch/ssw-auto"
            assert roles["slow"] == "stepswitch/ssw-plan"
            assert args[args.index("--approval-mode") + 1] == "write"


def test_the_generated_provider_lists_every_worker_with_its_own_window(tmp_path: Path) -> None:
    workers = {"local/small:3b": 8192, "local/coder:32b": None, "routed": 4096}
    with_workers = replace(endpoint(), workers=workers)
    adapter = get_adapter("pi")
    settings = HarnessSettings(state_dir=tmp_path / "state")
    env = adapter.full_environment(HarnessTask("x"), tmp_path, with_workers, settings, base={})
    config = json.loads((Path(env["PI_CODING_AGENT_DIR"]) / "models.json").read_text("utf-8"))
    models = {m["id"]: m for m in config["providers"]["stepswitch"]["models"]}
    # The routed virtual model resolves to these by ID; no physical model may be "routed".
    assert list(models) == [
        "ssw-auto",
        "ssw-plan",
        "ssw-review",
        "local/small:3b",
        "local/coder:32b",
    ]
    assert models["local/small:3b"]["contextWindow"] == 8192
    assert models["local/small:3b"]["maxTokens"] == 2048
    assert models["local/coder:32b"]["contextWindow"] == 131072  # unknown: the largest window


# ----------------------------------------------------------------------------------------
# ACP adapters
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "case"), cases(ACP_ADAPTERS))
def test_acp_sessions_match_the_transcripts(name: str, case: str, tmp_path: Path) -> None:
    adapter = get_adapter(name)
    spec = fixture(name)["cases"][case]
    workdir = tmp_path / "work"
    workdir.mkdir()
    for rel, content in spec.get("setup", {}).items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    transcript = FIXTURES / name / f"{case}.jsonl"
    settings = HarnessSettings(
        command=[sys.executable, str(MOCK_AGENT), str(transcript)],
        state_dir=tmp_path / "state",
        test_command=spec.get("test_command"),
        transcript=tmp_path / "out" / "acp.jsonl",
        log=tmp_path / "out" / "acp.stderr.log",
    )
    task = HarnessTask(prompt="Fix add() in calc.py.", resume=spec.get("resume"))
    handle = adapter.start(task, workdir, endpoint(), settings)
    events = []
    for event in handle.events():
        events.append(event)
        if event.kind == spec.get("cancel_on"):
            handle.cancel()
    result = handle.result(timeout=60)
    stderr = (tmp_path / "out" / "acp.stderr.log").read_text("utf-8")
    assert "mock agent:" not in stderr, stderr
    check(result, events, spec["expect"])
    for rel, content in spec["expect"].get("files", {}).items():
        assert (workdir / rel).read_text(encoding="utf-8") == content
    # The client's own record of the session has every message in both directions.
    recorded = (tmp_path / "out" / "acp.jsonl").read_text("utf-8").splitlines()
    expected = [line for line in transcript.read_text("utf-8").splitlines() if line.strip()]
    assert len(recorded) == len(expected)


def test_opencode_and_kimi_code_are_configured_without_user_files(tmp_path: Path) -> None:
    settings = HarnessSettings(state_dir=tmp_path / "state")
    opencode = get_adapter("opencode")
    env = opencode.full_environment(HarnessTask("x"), tmp_path, endpoint(), settings, base={})
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    provider = config["provider"]["stepswitch"]
    assert provider["options"]["baseURL"] == "http://127.0.0.1:47001/v1"
    assert provider["options"]["apiKey"] == "{env:STEPSWITCH_PROXY_TOKEN}"
    assert env["STEPSWITCH_PROXY_TOKEN"] == TOKEN
    assert config["model"] == "stepswitch/ssw-auto"
    assert config["agent"]["plan"]["model"] == "stepswitch/ssw-plan"
    assert config["permission"]["bash"] == {"*": "ask"}  # no test command: the client decides
    assert config["permission"]["webfetch"] == config["permission"]["websearch"] == "deny"
    assert config["share"] == "disabled" and config["autoupdate"] is False
    tested = HarnessSettings(state_dir=tmp_path / "state", test_command=["python", "-m", "pytest"])
    rules = opencode.config(endpoint(), tested)["permission"]["bash"]
    assert list(rules.items())[-1] == ("python -m pytest *", "allow")  # the last match wins
    assert provider["models"]["ssw-auto"]["limit"]["context"] == 131072
    assert env["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
    assert opencode.command(HarnessTask("x"), tmp_path, endpoint(), settings) == ["acp"]
    kimi = get_adapter("kimi-code")
    env = kimi.full_environment(HarnessTask("x"), tmp_path, endpoint(), settings, base={})
    assert env["KIMI_MODEL_BASE_URL"] == "http://127.0.0.1:47001/v1"
    assert env["KIMI_MODEL_API_KEY"] == TOKEN and env["KIMI_MODEL_NAME"] == "ssw-auto"
    assert env["KIMI_MODEL_MAX_CONTEXT_SIZE"] == "131072"
    assert Path(env["KIMI_CODE_HOME"]).is_relative_to(tmp_path / "state")
    assert env["KIMI_DISABLE_TELEMETRY"] == "1" and env["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
    assert env["KIMI_MODEL_MAX_COMPLETION_TOKENS"] == "16384"
    import tomllib

    home = tomllib.loads((Path(env["KIMI_CODE_HOME"]) / "config.toml").read_text("utf-8"))
    assert home == {"telemetry": False, "tools": {"disabled": ["WebSearch", "FetchURL"]}}


def test_configured_extras_never_carry_credentials(tmp_path: Path) -> None:
    settings = HarnessSettings(env={"MY_API_KEY": "x"})
    with pytest.raises(ValueError, match="credentials"):
        get_adapter("acp").full_environment(HarnessTask("x"), tmp_path, endpoint(), settings)
    plain = HarnessSettings(env={"KIMI_MODEL_PROVIDER_TYPE": "other"})
    env = get_adapter("kimi-code").full_environment(
        HarnessTask("x"), tmp_path, endpoint(), plain, base={}
    )
    assert env["KIMI_MODEL_PROVIDER_TYPE"] == "other"  # local config may choose another type
