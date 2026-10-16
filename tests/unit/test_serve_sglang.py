"""SGLang adapter: command lines, owned flags, the private key file, the child environment,
platform gating, discovery, readiness and metrics, and a real process.

The process tests run a stand-in server (fixtures/serve/fake_engine_server.py, SGLang flavor)
through `command_prefix`; readiness and metrics parsing also run against mock transports. No
SGLang, GPU or network is needed.
"""

from __future__ import annotations

import json
import platform
import re
import stat
import sys
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import net
from stepswitch.registry import EngineRecipe, ModelEntry
from stepswitch.serve.base import EngineError, ServerInfo, pid_alive
from stepswitch.serve.sglang import SglangEngine, key_file, parser_flags, write_key_file

FAKE = Path(__file__).parent / "fixtures" / "serve" / "fake_engine_server.py"
PREFIX = [sys.executable, str(FAKE)]
KEY = "test-key-0123456789"

METRICS = """\
# HELP sglang:prompt_tokens_total Number of prefill tokens processed.
# TYPE sglang:prompt_tokens_total counter
sglang:prompt_tokens_total{is_streaming="false",model_name="tiny-chat"} 300.0
sglang:prompt_tokens_total{is_streaming="true",model_name="tiny-chat"} 100.0
sglang:cached_tokens_total{cache_source="device",model_name="tiny-chat"} 200.0
sglang:cached_tokens_total{cache_source="host",model_name="tiny-chat"} 40.0
sglang:cached_tokens_total{cache_source="total",model_name="tiny-chat"} 60.0
sglang:cache_hit_rate{engine_type="unified",model_name="tiny-chat",tp_rank="0"} 0.5
sglang:cache_hit_rate{engine_type="unified",model_name="tiny-chat",tp_rank="1"} 0.7
sglang:num_running_reqs{model_name="tiny-chat"} 2.0
"""


def entry(recipe: EngineRecipe | None = None) -> ModelEntry:
    return ModelEntry(
        id="tiny-chat",
        name="Tiny Chat",
        hub_id="test/tiny-chat",
        quant="bf16",
        format="safetensors",
        file_size_gb=0.1,
        ram_gb=1.0,
        context=4096,
        license="apache-2.0",
        engines={"sglang": recipe or EngineRecipe()},
    )


def environment(root: Path, dists: Sequence[str], scripts: Sequence[str] = ()) -> Path:
    """A virtual environment laid out as on this OS (programs are empty files); returns its
    interpreter."""
    windows = sys.platform == "win32"
    bindir = root / ("Scripts" if windows else "bin")
    site = root / ("Lib/site-packages" if windows else "lib/python3.12/site-packages")
    bindir.mkdir(parents=True)
    site.mkdir(parents=True)
    suffix = ".exe" if windows else ""
    for name in ("python", *scripts):
        program = bindir / f"{name}{suffix}"
        program.write_bytes(b"")
        program.chmod(0o755)
    for dist in dists:
        (site / f"{dist}.dist-info").mkdir()
    return bindir / f"python{suffix}"


def server_info(log_path: Path | None = None) -> ServerInfo:
    return ServerInfo(
        model="tiny-chat",
        engine="sglang",
        base_url="http://127.0.0.1:30000",
        port=30000,
        pid=None,
        started=0.0,
        last_used=0.0,
        api_key=KEY,
        engine_model="tiny-chat",
        log_path=str(log_path) if log_path else None,
    )


def mock(routes: dict[str, tuple[int, str]], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, text = routes.get(f"{request.method} {request.url.path}", (404, "not found"))
        return httpx.Response(status, text=text)

    return httpx.MockTransport(handler)


# -- command lines ------------------------------------------------------------------------------


def test_command_line_from_the_recipe() -> None:
    engine = SglangEngine(command_prefix=PREFIX)
    recipe = EngineRecipe(
        args=["--mem-fraction-static", "0.8", "--context-length", "32768"],
        tool_parser="qwen3_coder",
        reasoning_parser="qwen3",
    )
    cmd = engine.command(entry(recipe), "/models/test--tiny-chat", port=30123)
    assert cmd == [
        *PREFIX,
        "--model-path",
        "/models/test--tiny-chat",
        "--mem-fraction-static",
        "0.8",
        "--context-length",
        "32768",
        "--host",
        "127.0.0.1",
        "--port",
        "30123",
        "--served-model-name",
        "tiny-chat",
        "--enable-metrics",
        "--enable-cache-report",
        "--tool-call-parser",
        "qwen3_coder",
        "--reasoning-parser",
        "qwen3",
    ]


def test_context_size_and_extra_arguments() -> None:
    engine = SglangEngine(command_prefix=PREFIX)
    recipe = EngineRecipe(args=["--context-length", "32768"])
    cmd = engine.command(entry(recipe), "m", port=1, ctx_size=8192, extra_args=["--dp", "2"])
    assert cmd[len(PREFIX) : len(PREFIX) + 6] == [
        "--model-path",
        "m",
        "--context-length",
        "8192",
        "--dp",
        "2",
    ]
    plain = engine.command(entry(), "m", port=1, ctx_size=4096)
    assert plain[len(PREFIX) + 2 : len(PREFIX) + 4] == ["--context-length", "4096"]
    assert "--tool-call-parser" not in plain and "--reasoning-parser" not in plain


def test_parser_flags() -> None:
    assert parser_flags(None) == [] and parser_flags(EngineRecipe()) == []
    assert parser_flags(EngineRecipe(tool_parser="auto", reasoning_parser="auto")) == [
        "--tool-call-parser",
        "auto",
        "--reasoning-parser",
        "auto",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        ["--model-path", "other"],
        ["--model", "other"],
        ["--host", "0.0.0.0"],
        ["--port=9"],
        ["--api-key", "x"],
        ["--served-model-name", "x"],
        ["--config", "other.yaml"],
        ["--tool-call-parser", "x"],
        ["--reasoning-parser", "x"],
    ],
)
def test_owned_flags_cannot_be_overridden(bad: list[str]) -> None:
    engine = SglangEngine(command_prefix=PREFIX)
    with pytest.raises(EngineError, match="may not set"):
        engine.command(entry(EngineRecipe(args=bad)), "m", port=1)
    with pytest.raises(EngineError, match="may not set"):
        engine.command(entry(), "m", port=1, extra_args=bad)


# -- the key ------------------------------------------------------------------------------------


def test_the_key_goes_into_a_private_config_file(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "tiny-chat.log"
    path = key_file(log)
    assert path == tmp_path / "logs" / "tiny-chat.log.key.yaml"
    engine = SglangEngine(command_prefix=PREFIX)
    assert engine.key_args(KEY, log) == ["--config", str(path)]
    assert path.read_text(encoding="utf-8") == f'api-key: "{KEY}"\n'
    write_key_file(path, 'odd"key\\with: yaml')  # replaced, escaped as a quoted scalar
    raw = path.read_bytes()
    assert raw == b'api-key: "odd\\"key\\\\with: yaml"\n'
    assert json.loads(raw.decode().split(":", 1)[1]) == 'odd"key\\with: yaml'
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_child_environment() -> None:
    engine = SglangEngine(command_prefix=PREFIX, env={"SGLANG_USE_MODELSCOPE": "true"})
    env = engine.server_env(KEY, base={"PATH": "p", "DO_NOT_TRACK": "0"})
    assert env["SGLANG_USE_MODELSCOPE"] == "false" and env["DO_NOT_TRACK"] == "1"
    assert env["HF_HUB_OFFLINE"] == "1" and env["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert KEY not in env.values()  # no key variable exists; the key goes into --config


# -- platforms and discovery --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("linux", "x86_64", None),
        ("linux", "aarch64", None),
        ("win32", "AMD64", "WSL2"),
        ("darwin", "arm64", "MLX-LM"),
        ("freebsd14", "amd64", "Linux only"),
        ("linux", "s390x", "no Linux wheels"),
    ],
)
def test_platforms(system: str, machine: str, expected: str | None) -> None:
    problem = SglangEngine().platform_problem(system, machine)
    assert problem is None if expected is None else expected in str(problem)


def test_the_host_platform_gates_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = SglangEngine(command_prefix=PREFIX)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    reason = str(engine.unavailable_reason())
    assert "WSL2" in reason and "--engine sglang" in reason and not engine.available()
    assert "WSL2" in str(engine.serve_problem(entry()))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    assert engine.available()


def test_discovery_on_path_and_by_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = environment(tmp_path / "env", ["sglang-0.5.22"], scripts=["sglang"])
    monkeypatch.setenv("PATH", str(python.parent))
    found = SglangEngine(check_platform=False)
    launcher = found.launcher()
    assert Path(launcher[0]).parent == python.parent and Path(launcher[0]).stem == "sglang"
    assert launcher[1:] == ["serve", "--model-type", "llm"]
    assert found.available() and found.version() == "0.5.22"

    configured = SglangEngine(python=python, check_platform=False)
    assert configured.launcher() == [str(python), "-m", "sglang.launch_server"]

    cpu = SglangEngine(
        python=environment(tmp_path / "cpu", ["sglang_cpu-0.5.21"]), check_platform=False
    )
    assert cpu.available() and cpu.version() == "0.5.21"  # the CPU build's distribution name

    empty = SglangEngine(python=environment(tmp_path / "empty", []), check_platform=False)
    assert str(empty.unavailable_reason()).startswith("sglang not installed in")
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert "SGLang was not found" in str(SglangEngine(check_platform=False).unavailable_reason())


def test_a_too_old_engine_is_refused(tmp_path: Path) -> None:
    python = environment(tmp_path / "env", ["sglang-0.5.22"])
    engine = SglangEngine(python=python, check_platform=False)
    log = tmp_path / "logs" / "tiny-chat.log"
    with pytest.raises(EngineError, match=r"needs SGLang 0\.6 or newer; found 0\.5\.22"):
        engine.start(
            entry(EngineRecipe(min_version="0.6")), model_path=tmp_path, api_key=KEY, log_path=log
        )
    assert not key_file(log).exists()  # refused before anything was written


# -- readiness and metrics ----------------------------------------------------------------------


def test_readiness_uses_ready_not_health() -> None:
    seen: list[httpx.Request] = []
    routes = {"GET /health": (200, ""), "GET /ready": (503, "")}
    engine = SglangEngine(command_prefix=PREFIX, egress=net.Egress(), transport=mock(routes, seen))
    assert not engine.health(server_info())
    assert [request.url.path for request in seen] == ["/ready"]
    routes["GET /ready"] = (200, "")
    assert engine.health(server_info())


def test_metrics_and_prefix_cache_stats() -> None:
    seen: list[httpx.Request] = []
    egress = net.Egress(net.EgressPolicy(offline=True))
    engine = SglangEngine(
        command_prefix=PREFIX, egress=egress, transport=mock({"GET /metrics": (200, METRICS)}, seen)
    )
    info = server_info()
    metrics = engine.metrics(info)
    assert metrics is not None and metrics['sglang:num_running_reqs{model_name="tiny-chat"}'] == 2.0
    stats = engine.prefix_cache_stats(info)
    assert stats is not None
    assert stats["source"] == "metrics"
    assert stats["hit_tokens"] == 300.0 and stats["query_tokens"] == 400.0  # summed over labels
    assert stats["hit_rate"] == 0.75
    assert stats["recent_hit_rate"] == pytest.approx(0.6)
    assert all(request.headers["authorization"] == f"Bearer {KEY}" for request in seen)
    assert all(row.purpose == "local-engine" and row.local for row in egress.memory)


def test_metrics_when_missing_or_idle() -> None:
    egress = net.Egress()
    info = server_info()
    off = SglangEngine(command_prefix=PREFIX, egress=egress, transport=mock({}, []))
    assert off.metrics(info) is None and off.prefix_cache_stats(info) is None
    idle = SglangEngine(
        command_prefix=PREFIX,
        egress=egress,
        transport=mock({"GET /metrics": (200, "sglang:num_running_reqs 0\n")}, []),
    )
    assert idle.prefix_cache_stats(info) == {
        "source": "metrics",
        "hit_tokens": 0.0,
        "query_tokens": 0.0,
        "hit_rate": None,
        "recent_hit_rate": None,
    }


def test_stop_removes_the_key_file_even_when_the_server_is_gone(tmp_path: Path) -> None:
    log = tmp_path / "tiny-chat.log"
    write_key_file(key_file(log), KEY)
    engine = SglangEngine(command_prefix=PREFIX, egress=net.Egress())
    engine.stop(server_info(log))  # no pid: nothing to signal
    assert not key_file(log).exists()


# -- a real process (stand-in server) -----------------------------------------------------------


@pytest.fixture
def started() -> Iterator[list[tuple[SglangEngine, ServerInfo]]]:
    servers: list[tuple[SglangEngine, ServerInfo]] = []
    yield servers
    for engine, info in servers:
        engine.stop(info, timeout=5.0)


def startup_line(log: Path, timeout: float = 10.0) -> dict[str, Any]:
    """What the stand-in server printed about itself."""
    pattern = r"^fake engine flavor=(\w+) pid=(\d+) key=(\w+) argv=(.*) env=(\{.*\})$"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = re.search(pattern, log.read_text(errors="replace"), re.MULTILINE)
        if match:
            return {
                "flavor": match[1],
                "pid": int(match[2]),
                "key": match[3],
                "argv": json.loads(match[4]),
                "env": json.loads(match[5]),
            }
        time.sleep(0.05)
    raise AssertionError(f"no startup line in {log}")


def wait_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while pid_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def test_stand_in_server_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    started: list[tuple[SglangEngine, ServerInfo]],
) -> None:
    monkeypatch.setenv("FAKE_ENGINE_LOAD_SECONDS", "2.0")
    folder = tmp_path / "models" / "test--tiny-chat"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    engine = SglangEngine(
        command_prefix=PREFIX,
        check_platform=False,
        models_dir=tmp_path / "models",
        egress=net.Egress(),
    )
    log = tmp_path / "logs" / "tiny-chat.log"
    recipe = EngineRecipe(args=["--mem-fraction-static", "0.5"], reasoning_parser="qwen3")
    info = engine.start(entry(recipe), api_key=KEY, log_path=log)
    started.append((engine, info))
    secret = key_file(log)
    assert secret.is_file() and KEY in secret.read_text(encoding="utf-8")
    assert info.engine == "sglang" and info.engine_model == "tiny-chat" and info.api_key == KEY

    statuses: list[int] = []
    deadline = time.monotonic() + 20
    while not engine.health(info):
        assert engine.alive(info), log.read_text(errors="replace")
        assert time.monotonic() < deadline, "server not ready in time"
        response = engine.request(info, "GET", "/ready")
        if response is not None:
            statuses.append(response.status_code)
        time.sleep(0.05)
    assert 503 in statuses  # listening, still loading

    assert httpx.get(f"{info.base_url}/health").status_code == 200  # public, like /ready
    assert httpx.get(f"{info.base_url}/v1/models").status_code == 401
    models = httpx.get(f"{info.base_url}/v1/models", headers=info.headers())
    assert models.status_code == 200 and models.json()["data"][0]["id"] == "tiny-chat"
    assert engine.prefix_cache_stats(info) == {
        "source": "metrics",
        "hit_tokens": 150.0,
        "query_tokens": 200.0,
        "hit_rate": 0.75,
        "recent_hit_rate": 0.75,
    }

    line = startup_line(log)
    assert line["flavor"] == "sglang" and line["key"] == "set"
    assert line["argv"] == [
        "--model-path",
        str(folder),
        "--mem-fraction-static",
        "0.5",
        "--host",
        "127.0.0.1",
        "--port",
        str(info.port),
        "--served-model-name",
        "tiny-chat",
        "--enable-metrics",
        "--enable-cache-report",
        "--reasoning-parser",
        "qwen3",
        "--config",
        str(secret),
    ]
    env = line["env"]
    assert env["SGLANG_USE_MODELSCOPE"] == "false" and env["HF_HUB_OFFLINE"] == "1"
    assert env["DO_NOT_TRACK"] == "1" and env["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert KEY not in log.read_text(errors="replace")

    engine.stop(info, timeout=5.0)
    assert not engine.alive(info) and not secret.exists()
    assert wait_gone(line["pid"]), "the server process outlived stop()"
    engine.stop(info)  # stopping twice is harmless


def test_without_a_key_there_is_no_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    started: list[tuple[SglangEngine, ServerInfo]],
) -> None:
    monkeypatch.setenv("FAKE_ENGINE_LOAD_SECONDS", "0")
    engine = SglangEngine(command_prefix=PREFIX, check_platform=False, egress=net.Egress())
    log = tmp_path / "open.log"
    info = engine.start(entry(), model_path=tmp_path, log_path=log)
    started.append((engine, info))
    line = startup_line(log)
    assert line["key"] == "none" and "--config" not in line["argv"]
    assert info.api_key is None and not key_file(log).exists()
