"""vLLM adapter: command lines, owned flags, the child environment, platform gating, discovery,
health and metrics, and a real process.

The process tests run a stand-in server (fixtures/serve/fake_engine_server.py, vLLM flavor)
through `command_prefix`; health and metrics parsing also run against mock transports. No vLLM,
GPU or network is needed.
"""

from __future__ import annotations

import json
import platform
import re
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
from stepswitch.serve.vllm import API_KEY_ENV, VllmEngine, parser_flags

FAKE = Path(__file__).parent / "fixtures" / "serve" / "fake_engine_server.py"
PREFIX = [sys.executable, str(FAKE)]
KEY = "test-key-0123456789"

METRICS = """\
# HELP vllm:prefix_cache_queries_total Prefix cache queries, in terms of number of queried tokens.
# TYPE vllm:prefix_cache_queries_total counter
vllm:prefix_cache_queries_total{engine="0",model_name="tiny-chat"} 400.0
vllm:prefix_cache_queries_total{engine="1",model_name="tiny-chat"} 200.0
vllm:prefix_cache_queries_created{engine="0",model_name="tiny-chat"} 1.7e+09
vllm:prefix_cache_hits_total{engine="0",model_name="tiny-chat"} 300.0
vllm:prefix_cache_hits_total{engine="1",model_name="tiny-chat"} 100.0
vllm:prompt_tokens_cached_total{engine="0",model_name="tiny-chat"} 280.0
vllm:external_prefix_cache_queries_total{engine="0",model_name="tiny-chat"} 50.0
vllm:external_prefix_cache_hits_total{engine="0",model_name="tiny-chat"} 20.0
vllm:num_requests_running{engine="0",model_name="tiny-chat"} 1.0
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
        engines={"vllm": recipe or EngineRecipe()},
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


def server_info() -> ServerInfo:
    return ServerInfo(
        model="tiny-chat",
        engine="vllm",
        base_url="http://127.0.0.1:8000",
        port=8000,
        pid=None,
        started=0.0,
        last_used=0.0,
        api_key=KEY,
        engine_model="tiny-chat",
    )


def mock(routes: dict[str, tuple[int, str]], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, text = routes.get(f"{request.method} {request.url.path}", (404, "not found"))
        return httpx.Response(status, text=text)

    return httpx.MockTransport(handler)


# -- command lines ------------------------------------------------------------------------------


def test_command_line_from_the_recipe() -> None:
    engine = VllmEngine(command_prefix=PREFIX)
    recipe = EngineRecipe(
        args=[
            "--dtype",
            "bfloat16",
            "--max-model-len",
            "32768",
            "--gpu-memory-utilization",
            "0.85",
        ],
        tool_parser="qwen3_coder",
        reasoning_parser="qwen3",
    )
    cmd = engine.command(entry(recipe), "/models/test--tiny-chat", port=41234)
    assert cmd == [
        *PREFIX,
        "serve",
        "/models/test--tiny-chat",
        "--dtype",
        "bfloat16",
        "--max-model-len",
        "32768",
        "--gpu-memory-utilization",
        "0.85",
        "--host",
        "127.0.0.1",
        "--port",
        "41234",
        "--served-model-name",
        "tiny-chat",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "qwen3_coder",
        "--reasoning-parser",
        "qwen3",
    ]


def test_context_size_and_extra_arguments() -> None:
    engine = VllmEngine(command_prefix=PREFIX)
    recipe = EngineRecipe(args=["--max-model-len=32768", "--enforce-eager"])
    cmd = engine.command(entry(recipe), "m", port=1, ctx_size=8192, extra_args=["--seed", "3"])
    assert cmd[len(PREFIX) :] == [
        "serve",
        "m",
        "--max-model-len=8192",
        "--enforce-eager",
        "--seed",
        "3",
        "--host",
        "127.0.0.1",
        "--port",
        "1",
        "--served-model-name",
        "tiny-chat",
    ]
    plain = engine.command(entry(), "m", port=1, ctx_size=4096)
    assert plain[len(PREFIX) + 2 : len(PREFIX) + 4] == ["--max-model-len", "4096"]


def test_parser_flags() -> None:
    assert parser_flags(None) == [] and parser_flags(EngineRecipe()) == []
    assert parser_flags(EngineRecipe(tool_parser="qwen3_coder")) == [
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "qwen3_coder",
    ]
    assert parser_flags(EngineRecipe(reasoning_parser="qwen3")) == ["--reasoning-parser", "qwen3"]


@pytest.mark.parametrize(
    "bad",
    [
        ["--host", "0.0.0.0"],
        ["--port", "9"],
        ["--api-key", "x"],
        ["--served-model-name", "x"],
        ["--model=other"],
        ["--enable-auto-tool-choice"],
        ["--tool-call-parser", "x"],
        ["--reasoning-parser=x"],
        ["--disable-log-stats"],
    ],
)
def test_owned_flags_cannot_be_overridden(bad: list[str]) -> None:
    engine = VllmEngine(command_prefix=PREFIX)
    with pytest.raises(EngineError, match="may not set"):
        engine.command(entry(EngineRecipe(args=bad)), "m", port=1)
    with pytest.raises(EngineError, match="may not set"):
        engine.command(entry(), "m", port=1, extra_args=bad)


def test_the_key_and_the_privacy_settings_travel_in_the_environment() -> None:
    engine = VllmEngine(
        command_prefix=PREFIX, env={"VLLM_NO_USAGE_STATS": "0", "OMP_NUM_THREADS": "4"}
    )
    base = {"PATH": "p", "vllm_api_key": "inherited", "HF_HUB_OFFLINE": "0"}
    env = engine.server_env(KEY, base=base)
    assert env[API_KEY_ENV] == KEY and "vllm_api_key" not in env
    assert env["VLLM_NO_USAGE_STATS"] == "1" and env["VLLM_DO_NOT_TRACK"] == "1"
    assert env["DO_NOT_TRACK"] == "1" and env["VLLM_USE_MODELSCOPE"] == "False"
    assert env["HF_HUB_OFFLINE"] == "1" and env["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert env["OMP_NUM_THREADS"] == "4" and env["PATH"] == "p"
    assert API_KEY_ENV not in engine.server_env(None, base=base)
    assert KEY not in engine.command(entry(), "m", port=1)


# -- platforms and discovery --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("linux", "x86_64", None),
        ("linux", "aarch64", None),
        ("win32", "AMD64", "WSL2"),
        ("darwin", "arm64", "Linux only"),
        ("freebsd14", "amd64", "Linux only"),
        ("linux", "ppc64le", "no Linux wheels"),
    ],
)
def test_platforms(system: str, machine: str, expected: str | None) -> None:
    problem = VllmEngine().platform_problem(system, machine)
    assert problem is None if expected is None else expected in str(problem)


def test_the_host_platform_gates_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = VllmEngine(command_prefix=PREFIX)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    reason = engine.unavailable_reason()
    assert reason is not None and "WSL2" in reason and "--engine vllm" in reason
    assert not engine.available()
    with pytest.raises(EngineError, match="WSL2"):
        engine.start(entry(), model_path="/models/x")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert engine.available()
    monkeypatch.setattr(platform, "machine", lambda: "riscv64")
    assert "riscv64" in str(engine.unavailable_reason())


def test_discovery_on_path_and_by_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = environment(tmp_path / "env", ["vllm-0.31.0"], scripts=["vllm"])
    monkeypatch.setenv("PATH", str(python.parent))
    found = VllmEngine(check_platform=False)
    launcher = found.launcher()
    assert len(launcher) == 1 and Path(launcher[0]).parent == python.parent
    assert Path(launcher[0]).stem == "vllm"
    assert found.available() and found.version() == "0.31.0"

    configured = VllmEngine(python=python, check_platform=False)
    assert configured.launcher() == [str(python), "-m", "vllm.entrypoints.cli.main"]
    assert configured.available() and configured.version() == "0.31.0"

    empty = VllmEngine(python=environment(tmp_path / "empty", []), check_platform=False)
    reason = str(empty.unavailable_reason())
    assert reason.startswith("vllm not installed in") and "[engines.vllm] python" in reason

    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    missing = VllmEngine(check_platform=False)
    assert "vLLM was not found" in str(missing.unavailable_reason())
    assert missing.version() is None
    with pytest.raises(EngineError, match="vLLM was not found"):
        missing.start(entry(), model_path=tmp_path)


def test_a_too_old_engine_is_refused(tmp_path: Path) -> None:
    engine = VllmEngine(python=environment(tmp_path / "env", ["vllm-0.31.0"]), check_platform=False)
    with pytest.raises(EngineError, match=r"needs vLLM 0\.40\.0 or newer; found 0\.31\.0"):
        engine.start(entry(EngineRecipe(min_version="0.40.0")), model_path=tmp_path)


def test_model_sources(tmp_path: Path) -> None:
    engine = VllmEngine(command_prefix=PREFIX, models_dir=tmp_path, check_platform=False)
    assert engine.model_source(entry(EngineRecipe(name="test/tiny-chat"))) == "test/tiny-chat"
    home = engine.model_source(entry(EngineRecipe(name="~/models/tiny-chat")))
    assert home == str(Path.home() / "models" / "tiny-chat")
    assert engine.serve_problem(entry()) is not None  # nothing local yet
    folder = tmp_path / "test--tiny-chat"
    folder.mkdir()
    (folder / "config.json").write_text("{}", encoding="utf-8")
    assert engine.model_source(entry()) == str(folder)
    assert engine.serve_problem(entry()) is None


# -- health and metrics -------------------------------------------------------------------------


def test_metrics_and_prefix_cache_stats() -> None:
    seen: list[httpx.Request] = []
    egress = net.Egress(net.EgressPolicy(offline=True))  # loopback stays reachable offline
    routes = {"GET /metrics": (200, METRICS), "GET /health": (200, "")}
    engine = VllmEngine(command_prefix=PREFIX, egress=egress, transport=mock(routes, seen))
    info = server_info()
    metrics = engine.metrics(info)
    assert metrics is not None
    assert metrics['vllm:prefix_cache_hits_total{engine="1",model_name="tiny-chat"}'] == 100.0
    assert metrics['vllm:num_requests_running{engine="0",model_name="tiny-chat"}'] == 1.0
    assert engine.prefix_cache_stats(info) == {
        "source": "metrics",
        "hit_tokens": 400.0,
        "query_tokens": 600.0,
        "hit_rate": 400.0 / 600.0,
        "cached_prompt_tokens": 280.0,
        "external_hit_tokens": 20.0,
        "external_query_tokens": 50.0,
    }
    assert engine.health(info) and engine.alive(info)  # no pid: alive means healthy
    assert [r.headers["authorization"] for r in seen] == [f"Bearer {KEY}"] * len(seen)
    rows = list(egress.memory)
    assert len(rows) == len(seen)
    assert all(row.purpose == "local-engine" and row.local and row.allowed for row in rows)


def test_a_server_that_is_not_ready_or_gone() -> None:
    egress = net.Egress()
    info = server_info()
    loading = VllmEngine(
        command_prefix=PREFIX, egress=egress, transport=mock({"GET /health": (503, "")}, [])
    )
    assert not loading.health(info) and loading.metrics(info) is None
    assert loading.prefix_cache_stats(info) is None

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    down = VllmEngine(command_prefix=PREFIX, egress=egress, transport=httpx.MockTransport(refuse))
    assert not down.health(info) and not down.alive(info) and down.metrics(info) is None

    routes = {"GET /metrics": (200, "vllm:num_requests_running 1\n")}
    partial = VllmEngine(command_prefix=PREFIX, egress=egress, transport=mock(routes, []))
    assert partial.metrics(info) == {"vllm:num_requests_running": 1.0}
    assert partial.prefix_cache_stats(info) is None

    routes = {
        "GET /metrics": (
            200,
            "vllm:prefix_cache_queries_total 0\nvllm:prefix_cache_hits_total 0\n",
        )
    }
    idle = VllmEngine(command_prefix=PREFIX, egress=egress, transport=mock(routes, []))
    assert idle.prefix_cache_stats(info) == {
        "source": "metrics",
        "hit_tokens": 0.0,
        "query_tokens": 0.0,
        "hit_rate": None,
    }


# -- a real process (stand-in server) -----------------------------------------------------------


@pytest.fixture
def started() -> Iterator[list[tuple[VllmEngine, ServerInfo]]]:
    servers: list[tuple[VllmEngine, ServerInfo]] = []
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


def wait_healthy(engine: VllmEngine, info: ServerInfo, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not engine.health(info):
        assert engine.alive(info), "server exited:\n" + Path(str(info.log_path)).read_text()
        assert time.monotonic() < deadline, "server not healthy in time"
        time.sleep(0.05)


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
    started: list[tuple[VllmEngine, ServerInfo]],
) -> None:
    monkeypatch.setenv("FAKE_ENGINE_LOAD_SECONDS", "2.0")
    monkeypatch.setenv(API_KEY_ENV, "inherited-must-not-leak")
    folder = tmp_path / "models" / "test--tiny-chat"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    engine = VllmEngine(
        command_prefix=PREFIX,
        check_platform=False,
        env={"FAKE_CONFIGURED": "yes"},
        models_dir=tmp_path / "models",
        egress=net.Egress(),
    )
    log = tmp_path / "logs" / "tiny-chat.log"
    recipe = EngineRecipe(args=["--dtype", "float32"], tool_parser="qwen3_coder")
    info = engine.start(entry(recipe), ctx_size=2048, api_key=KEY, log_path=log)
    started.append((engine, info))
    assert info.engine == "vllm" and info.engine_model == "tiny-chat" and info.managed
    assert info.api_key == KEY and info.base_url == f"http://127.0.0.1:{info.port}"
    assert info.log_path == str(log) and info.ram_gb == 1.0
    assert not engine.health(info)  # vLLM listens only once the model is loaded
    wait_healthy(engine, info)

    assert httpx.get(f"{info.base_url}/v1/models").status_code == 401
    models = httpx.get(f"{info.base_url}/v1/models", headers=info.headers())
    assert models.status_code == 200 and models.json()["data"][0]["id"] == "tiny-chat"
    metrics = engine.metrics(info)
    assert metrics is not None
    assert metrics['vllm:prefix_cache_hits_total{engine="0",model_name="tiny-chat"}'] == 300.0
    assert engine.prefix_cache_stats(info) == {
        "source": "metrics",
        "hit_tokens": 300.0,
        "query_tokens": 400.0,
        "hit_rate": 0.75,
        "cached_prompt_tokens": 300.0,
    }

    line = startup_line(log)
    assert line["flavor"] == "vllm" and line["key"] == "set"
    assert line["argv"] == [
        "serve",
        str(folder),
        "--dtype",
        "float32",
        "--max-model-len",
        "2048",
        "--host",
        "127.0.0.1",
        "--port",
        str(info.port),
        "--served-model-name",
        "tiny-chat",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "qwen3_coder",
    ]
    env = line["env"]
    assert env["VLLM_API_KEY"] == "set" and env["FAKE_CONFIGURED"] == "yes"
    for name in ("VLLM_NO_USAGE_STATS", "VLLM_DO_NOT_TRACK", "DO_NOT_TRACK", "HF_HUB_OFFLINE"):
        assert env[name] == "1", name
    assert env["HF_HUB_DISABLE_TELEMETRY"] == "1" and env["VLLM_USE_MODELSCOPE"] == "False"
    text = log.read_text(errors="replace")
    assert KEY not in text and "inherited-must-not-leak" not in text

    engine.stop(info, timeout=5.0)
    assert not engine.alive(info) and not engine.health(info)
    assert wait_gone(line["pid"]), "the server process outlived stop()"
    engine.stop(info)  # stopping twice is harmless


def test_a_server_that_fails_to_start_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    started: list[tuple[VllmEngine, ServerInfo]],
) -> None:
    monkeypatch.setenv("FAKE_ENGINE_FAIL", "not enough memory for the KV cache")
    engine = VllmEngine(command_prefix=PREFIX, check_platform=False, egress=net.Egress())
    log = tmp_path / "broken.log"
    info = engine.start(entry(), model_path=tmp_path, api_key=KEY, log_path=log)
    started.append((engine, info))
    deadline = time.monotonic() + 20
    while engine.alive(info) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not engine.alive(info) and not engine.health(info)
    assert "not enough memory for the KV cache" in log.read_text(errors="replace")
