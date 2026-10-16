"""MLX-LM adapter: command lines, owned flags, platform gating, discovery, the load-aware health
check, and a real process.

The process tests run a stand-in server (fixtures/serve/fake_engine_server.py, MLX-LM flavor)
through `command_prefix`; the health check also runs against mock transports. No MLX, arm64
Mac or network is needed.
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
from stepswitch.serve import mlx as mlx_module
from stepswitch.serve.base import EngineError, ServerInfo, pid_alive
from stepswitch.serve.mlx import DEFAULT_MODEL, MlxEngine

FAKE = Path(__file__).parent / "fixtures" / "serve" / "fake_engine_server.py"
PREFIX = [sys.executable, str(FAKE)]


def entry(recipe: EngineRecipe | None = None) -> ModelEntry:
    return ModelEntry(
        id="tiny-chat",
        name="Tiny Chat",
        hub_id="test/tiny-chat-4bit",
        quant="4bit",
        format="mlx",
        file_size_gb=0.1,
        ram_gb=1.0,
        context=4096,
        license="apache-2.0",
        engines={"mlx": recipe or EngineRecipe()},
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


def server_info(pid: int | None = None, port: int = 8080) -> ServerInfo:
    return ServerInfo(
        model="tiny-chat",
        engine="mlx",
        base_url=f"http://127.0.0.1:{port}",
        port=port,
        pid=pid,
        started=0.0,
        last_used=0.0,
        engine_model=DEFAULT_MODEL,
    )


class FakeMlx:
    """`/health` and `/v1/completions` of an MLX-LM server, with switchable answers."""

    def __init__(self) -> None:
        self.health = 200
        self.completion = 200
        self.seen: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.seen.append((request.method, request.url.path, body))
        if request.url.path == "/health":
            return httpx.Response(self.health, json={"status": "ok"})
        if request.method == "POST" and request.url.path == "/v1/completions":
            return httpx.Response(self.completion, json={"choices": [{"text": "pong"}]})
        return httpx.Response(404)


# -- command lines ------------------------------------------------------------------------------


def test_command_line_from_the_recipe() -> None:
    engine = MlxEngine(command_prefix=PREFIX)
    recipe = EngineRecipe(args=["--max-tokens", "2048", "--prompt-cache-size", "4"])
    cmd = engine.command(entry(recipe), "/models/test--tiny-chat-4bit", port=8123, ctx_size=8192)
    assert cmd == [
        *PREFIX,
        "--model",
        "/models/test--tiny-chat-4bit",
        "--max-tokens",
        "2048",
        "--prompt-cache-size",
        "4",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
        "--allowed-origins",
        "http://127.0.0.1:8123",
    ]  # no context-size option exists, so ctx_size changes nothing
    extra = engine.command(entry(), "m", port=1, extra_args=["--temp", "0.2"])
    assert extra[len(PREFIX) : len(PREFIX) + 4] == ["--model", "m", "--temp", "0.2"]


@pytest.mark.parametrize(
    "bad",
    [["--model", "other"], ["--host", "0.0.0.0"], ["--port=9"], ["--allowed-origins", "*"]],
)
def test_owned_flags_cannot_be_overridden(bad: list[str]) -> None:
    engine = MlxEngine(command_prefix=PREFIX)
    with pytest.raises(EngineError, match="may not set"):
        engine.command(entry(EngineRecipe(args=bad)), "m", port=1)
    with pytest.raises(EngineError, match="may not set"):
        engine.command(entry(), "m", port=1, extra_args=bad)


def test_no_key_and_the_default_model_name() -> None:
    engine = MlxEngine(command_prefix=PREFIX)
    assert not engine.authenticates and engine.served_name(entry()) == DEFAULT_MODEL
    env = engine.server_env(None, base={"PATH": "p"})
    assert env["HF_HUB_OFFLINE"] == "1" and env["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert env["DO_NOT_TRACK"] == "1"


# -- platforms and discovery --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("darwin", "arm64", None),
        ("darwin", "x86_64", "arm64 Mac"),
        ("linux", "aarch64", "arm64 macOS only"),
        ("linux", "x86_64", "arm64 macOS only"),
        ("win32", "ARM64", "arm64 macOS only"),
    ],
)
def test_platforms(system: str, machine: str, expected: str | None) -> None:
    problem = MlxEngine().platform_problem(system, machine)
    assert problem is None if expected is None else expected in str(problem)


def test_the_host_platform_gates_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = MlxEngine(command_prefix=PREFIX)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert engine.available() and engine.serve_problem(entry()) is not None  # no model yet
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert "arm64 Mac" in str(engine.unavailable_reason())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    assert not engine.available()
    with pytest.raises(EngineError, match="arm64 macOS only"):
        engine.start(entry(), model_path="/models/x")


def test_discovery_on_path_and_by_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = environment(tmp_path / "env", ["mlx_lm-0.32.2", "mlx-0.30.0"], ["mlx_lm.server"])
    monkeypatch.setenv("PATH", str(python.parent))
    found = MlxEngine(check_platform=False)
    launcher = found.launcher()
    assert len(launcher) == 1 and Path(launcher[0]).name.startswith("mlx_lm.server")
    assert found.available() and found.version() == "0.32.2"
    configured = MlxEngine(python=python, check_platform=False)
    assert configured.launcher() == [str(python), "-m", "mlx_lm", "server"]
    assert configured.version() == "0.32.2"
    empty = MlxEngine(python=environment(tmp_path / "empty", ["mlx-0.30.0"]), check_platform=False)
    assert str(empty.unavailable_reason()).startswith("mlx-lm not installed in")


# -- the load-aware health check ----------------------------------------------------------------


def test_health_waits_for_the_model_once_per_server() -> None:
    fake = FakeMlx()
    engine = MlxEngine(
        command_prefix=PREFIX, egress=net.Egress(), transport=httpx.MockTransport(fake)
    )
    info = server_info(pid=None)
    assert engine.health(info)
    probe = {"model": DEFAULT_MODEL, "prompt": "ping", "max_tokens": 1}
    assert fake.seen == [("GET", "/health", None), ("POST", "/v1/completions", probe)]
    assert engine.health(info)  # loaded: no second generation
    assert fake.seen[2:] == [("GET", "/health", None)]

    fake.seen.clear()
    assert engine.health(server_info(pid=None, port=8081))  # another server: probed again
    assert [path for _, path, _ in fake.seen] == ["/health", "/v1/completions"]


def test_health_is_false_until_a_completion_succeeds() -> None:
    fake = FakeMlx()
    engine = MlxEngine(
        command_prefix=PREFIX, egress=net.Egress(), transport=httpx.MockTransport(fake)
    )
    info = server_info()
    fake.health = 503
    assert not engine.health(info)
    assert [path for _, path, _ in fake.seen] == ["/health"]  # no probe while unhealthy
    fake.health, fake.completion = 200, 500
    assert not engine.health(info)
    fake.completion = 200
    assert engine.health(info)
    assert engine.metrics(info) is None and engine.prefix_cache_stats(info) is None


# -- a real process (stand-in server) -----------------------------------------------------------


@pytest.fixture
def started() -> Iterator[list[tuple[MlxEngine, ServerInfo]]]:
    servers: list[tuple[MlxEngine, ServerInfo]] = []
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
    started: list[tuple[MlxEngine, ServerInfo]],
) -> None:
    monkeypatch.setenv("FAKE_ENGINE_LOAD_SECONDS", "2.0")
    monkeypatch.setattr(mlx_module, "READY_TIMEOUT_S", 0.3)
    folder = tmp_path / "models" / "test--tiny-chat-4bit"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    engine = MlxEngine(
        command_prefix=PREFIX,
        check_platform=False,
        models_dir=tmp_path / "models",
        egress=net.Egress(),
    )
    log = tmp_path / "logs" / "tiny-chat.log"
    info = engine.start(entry(), ctx_size=8192, api_key="ignored-key", log_path=log)
    started.append((engine, info))
    assert info.api_key is None and info.headers() == {}  # the server has no authentication
    assert info.engine_model == DEFAULT_MODEL and info.engine == "mlx"

    deadline = time.monotonic() + 20
    while engine.request(info, "GET", "/health") is None:  # listening, model still loading
        assert engine.alive(info) and time.monotonic() < deadline, log.read_text()
        time.sleep(0.05)
    assert not engine.health(info)  # /health says 200, but no completion yet
    while not engine.health(info):
        assert engine.alive(info) and time.monotonic() < deadline, log.read_text()
        time.sleep(0.05)
    assert engine.metrics(info) is None and engine.prefix_cache_stats(info) is None

    line = startup_line(log)
    assert line["flavor"] == "mlx" and line["key"] == "none"
    assert line["argv"] == [
        "--model",
        str(folder),
        "--host",
        "127.0.0.1",
        "--port",
        str(info.port),
        "--allowed-origins",
        f"http://127.0.0.1:{info.port}",
    ]
    assert line["env"]["HF_HUB_OFFLINE"] == "1" and line["env"]["DO_NOT_TRACK"] == "1"
    text = log.read_text(errors="replace")
    assert f"generate model={DEFAULT_MODEL}" in text
    assert set(re.findall(r"generate model=(\S+)", text)) == {DEFAULT_MODEL}
    assert "ignored-key" not in text

    engine.stop(info, timeout=5.0)
    assert not engine.alive(info) and not engine.health(info)
    assert wait_gone(line["pid"]), "the server process outlived stop()"
