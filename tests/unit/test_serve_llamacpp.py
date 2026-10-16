"""llama.cpp adapter: command lines, versions, binary discovery, and a real server process.

The process tests run a stand-in server (fixtures/serve/fake_llama_server.py) through
`command_prefix`, so spawning, health checks, metrics, slots and stopping are exercised for real
without llama.cpp. The live test uses the installed llama-server and a verified GGUF.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from stepswitch import config, net, registry
from stepswitch.registry import EngineRecipe, ModelEntry
from stepswitch.serve import llamacpp
from stepswitch.serve.base import EngineError, ServerInfo, pid_alive, process_start_time, redact
from stepswitch.serve.llamacpp import LlamaCppEngine

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "serve"
FAKE = FIXTURES / "fake_llama_server.py"
VERSION_OUTPUT = (FIXTURES / "llama-server-version-b11669.txt").read_text(encoding="utf-8")
PREFIX = [sys.executable, str(FAKE)]
OWNED = ("-m", "--host", "--port", "--metrics", "--slot-save-path", "--alias", "--offline")


def real_entries() -> dict[str, ModelEntry]:
    return registry.load([ROOT / "registry"])


def tiny_entry(args: list[str] | None = None, min_version: str | None = "b11669") -> ModelEntry:
    return ModelEntry(
        id="tiny",
        name="Tiny",
        hub_id="test/tiny-GGUF",
        file="tiny.gguf",
        quant="Q8_0",
        file_size_gb=0.001,
        ram_gb=0.5,
        context=4096,
        license="apache-2.0",
        engines={"llamacpp": EngineRecipe(args=args or [], min_version=min_version)},
    )


def real_pid(log: Path, timeout: float = 10.0) -> int:
    """The stand-in server's own PID (a venv launcher may sit between it and us)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = re.search(r"fake llama-server pid=(\d+)", log.read_text(errors="replace"))
        if match:
            return int(match.group(1))
        time.sleep(0.05)
    raise AssertionError(f"no pid line in {log}")


def wait_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while pid_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def wait_healthy(engine: LlamaCppEngine, info: ServerInfo, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not engine.health(info):
        assert engine.alive(info), "server exited:\n" + Path(str(info.log_path)).read_text()
        assert time.monotonic() < deadline, "server not healthy in time"
        time.sleep(0.05)


@pytest.fixture
def started(tmp_path: Path) -> Iterator[list[tuple[LlamaCppEngine, ServerInfo]]]:
    servers: list[tuple[LlamaCppEngine, ServerInfo]] = []
    yield servers
    for engine, info in servers:
        engine.stop(info, timeout=5.0)


# -- command line -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id", ["qwen3-4b-instruct-2507", "qwen3-embedding-0.6b", "qwen2.5-coder-1.5b"]
)
def test_command_lines_for_the_registry_entries(model_id: str) -> None:
    entry = real_entries()[model_id]
    recipe = entry.engines["llamacpp"]
    engine = LlamaCppEngine(command_prefix=PREFIX)
    cmd = engine.command(
        entry,
        Path("models") / "m.gguf",
        port=41234,
        api_key="secret",
        slot_save_path=Path("slots"),
    )
    assert cmd[: len(PREFIX)] == PREFIX
    recipe_part = cmd[len(PREFIX) : len(PREFIX) + len(recipe.args)]
    assert recipe_part == recipe.args and recipe.args
    assert cmd[len(PREFIX) + len(recipe.args) :] == [
        "-m",
        str(Path("models") / "m.gguf"),
        "--host",
        "127.0.0.1",
        "--port",
        "41234",
        "--api-key",
        "secret",
        "--metrics",
        "--slot-save-path",
        "slots",
        "--alias",
        model_id,
        "--offline",
    ]
    assert cmd.count("--ctx-size") == 1
    assert redact(cmd).count("secret") == 0


def test_recipe_flags_of_the_t0_set() -> None:
    entries = real_entries()
    embed = entries["qwen3-embedding-0.6b"].engines["llamacpp"].args
    assert "--embedding" in embed and embed[embed.index("--pooling") + 1] == "last"
    router = entries["qwen3-4b-instruct-2507"].engines["llamacpp"].args
    assert router[router.index("--parallel") + 1] == "2" and "--kv-unified" in router
    assert all(e.engines["llamacpp"].min_version == "b11669" for e in entries.values())


def test_ctx_size_overrides_the_recipe() -> None:
    entry = real_entries()["qwen3-4b-instruct-2507"]
    cmd = LlamaCppEngine(command_prefix=PREFIX).command(
        entry, Path("m.gguf"), port=1, ctx_size=8192
    )
    assert cmd.count("--ctx-size") == 1 and cmd[cmd.index("--ctx-size") + 1] == "8192"
    assert "--api-key" not in cmd and "--slot-save-path" not in cmd
    assert llamacpp.with_ctx_size(["-c", "1", "--x"], 9) == ["-c", "9", "--x"]
    assert llamacpp.with_ctx_size(["--ctx-size=1"], 9) == ["--ctx-size=9"]
    assert llamacpp.with_ctx_size(["--embedding"], 9) == ["--embedding", "--ctx-size", "9"]


@pytest.mark.parametrize(
    "bad",
    [
        ["--host", "0.0.0.0"],
        ["--port=1"],
        ["--rpc", "10.0.0.2:50052"],
        ["--api-key", "x"],
        ["-hf", "someone/model"],
        ["--model-url", "https://models.example/m.gguf"],
        ["--agent"],
        ["--tools", "all"],
        ["--path", "/"],
        ["--alias", "other"],
    ],
)
def test_recipes_and_extra_args_may_not_take_over_owned_or_unsafe_flags(bad: list[str]) -> None:
    engine = LlamaCppEngine(command_prefix=PREFIX)
    with pytest.raises(EngineError, match="may not set"):
        engine.command(tiny_entry(bad), Path("m.gguf"), port=1)
    with pytest.raises(EngineError, match="may not set"):
        engine.command(tiny_entry(), Path("m.gguf"), port=1, extra_args=bad)


def test_safe_extra_args_are_kept() -> None:
    cmd = LlamaCppEngine(command_prefix=PREFIX).command(
        tiny_entry(["--ctx-size", "2048"]),
        Path("m.gguf"),
        port=1,
        extra_args=["-ngl", "0", "--threads", "4"],
    )
    assert cmd[len(PREFIX) : len(PREFIX) + 6] == [
        "--ctx-size",
        "2048",
        "-ngl",
        "0",
        "--threads",
        "4",
    ]


def test_the_key_travels_in_the_environment_not_the_command_line() -> None:
    env = llamacpp.server_env(
        "k3y", base={"PATH": "p", "LLAMA_ARG_RPC": "10.0.0.2:50052", "LLAMA_API_KEY": "old"}
    )
    assert env == {"PATH": "p", "LLAMA_API_KEY": "k3y"}
    assert "LLAMA_API_KEY" not in llamacpp.server_env(None, base={"LLAMA_API_KEY": "old"})


# -- versions and discovery ---------------------------------------------------------------------


def test_version_parsing_from_the_captured_b11669_output() -> None:
    parsed = llamacpp.parse_version(VERSION_OUTPUT)
    assert parsed == llamacpp.LlamaVersion(11669, "0.7.0-dev", "620d2225c")
    assert parsed.tag == "b11669"
    older = llamacpp.parse_version("version: 4589 (abc1234)\nbuilt with cc for x86_64")
    assert older is not None and older.tag == "b4589" and older.release is None
    assert llamacpp.parse_version("no version here") is None
    assert llamacpp.meets_minimum("b11669", "b11669")
    assert not llamacpp.meets_minimum("b11600", "b11669")
    assert llamacpp.meets_minimum(None, "b11669") and llamacpp.meets_minimum("b1", None)


def test_version_runs_the_server_binary() -> None:
    engine = LlamaCppEngine(command_prefix=PREFIX)
    assert engine.version() == "b11669"
    assert engine.version_info() == llamacpp.LlamaVersion(11669, "0.7.0-dev", "620d2225c")
    assert engine.available()


def _server_file(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("llama-server.exe" if sys.platform == "win32" else "llama-server")
    path.write_bytes(b"stub")
    path.chmod(0o755)
    return path


def test_find_binary_prefers_the_newest_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STEPSWITCH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    base = tmp_path / "home" / "engines" / "llamacpp"
    assert llamacpp.find_binary() is None
    _server_file(base / "b11000")
    newest = _server_file(base / "b11669" / "llama-b11669")  # tarball layout, one level down
    _server_file(base / "custom")
    _server_file(base / ".b99999-staging")  # an unfinished install is ignored
    assert llamacpp.find_binary() == newest
    assert LlamaCppEngine().find_binary() == newest
    assert LlamaCppEngine(_server_file(tmp_path / "pinned")).find_binary() == (
        tmp_path / "pinned" / newest.name
    )


def test_find_binary_falls_back_to_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STEPSWITCH_HOME", str(tmp_path / "home"))
    on_path = _server_file(tmp_path / "bin")
    monkeypatch.setenv("PATH", str(on_path.parent))
    found = llamacpp.find_binary()
    assert found is not None and found.resolve() == on_path.resolve()


def test_missing_binary_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STEPSWITCH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    engine = LlamaCppEngine()
    assert not engine.available() and engine.version() is None
    with pytest.raises(EngineError, match="not installed"):
        engine.command(tiny_entry(), Path("m.gguf"), port=1)


def test_metrics_parsing() -> None:
    text = (
        "# HELP llamacpp:prompt_tokens_total x\n"
        "llamacpp:prompt_tokens_total 42\n"
        'llamacpp:spec{position="0"} 3\n'
        "bad line\n"
    )
    assert llamacpp.parse_metrics(text) == {
        "llamacpp:prompt_tokens_total": 42.0,
        'llamacpp:spec{position="0"}': 3.0,
    }


# -- a real process (stand-in server) -----------------------------------------------------------


def test_stand_in_server_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    started: list[tuple[LlamaCppEngine, ServerInfo]],
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_LOAD_SECONDS", "1.0")
    model = tmp_path / "tiny.gguf"
    model.write_bytes(b"GGUF")
    engine = LlamaCppEngine(command_prefix=PREFIX, models_dir=tmp_path, egress=net.Egress())
    log = tmp_path / "logs" / "tiny.log"
    key = "test-key-0123456789"
    info = engine.start(
        tiny_entry(["--ctx-size", "2048"]),
        api_key=key,
        log_path=log,
        model_path=model,
        slot_save_path=tmp_path / "slots",
    )
    started.append((engine, info))
    assert info.base_url == f"http://127.0.0.1:{info.port}" and info.pid and info.managed
    assert info.api_key == key and "test-key" not in repr(info)

    statuses: list[int] = []
    deadline = time.monotonic() + 20
    while 200 not in statuses and time.monotonic() < deadline:
        try:
            statuses.append(httpx.get(f"{info.base_url}/health", timeout=1).status_code)
        except httpx.TransportError:
            time.sleep(0.05)
    assert statuses[0] == 503 and statuses[-1] == 200  # loading, then ready; no key needed
    assert engine.health(info) and engine.alive(info)

    assert httpx.get(f"{info.base_url}/v1/models").status_code == 401
    assert httpx.get(f"{info.base_url}/v1/models", headers=info.headers()).status_code == 200
    metrics = engine.metrics(info)
    assert metrics is not None and metrics["llamacpp:prompt_tokens_total"] == 42.0
    assert engine.prefix_cache_stats(info) is None

    assert engine.slot_save(info, 0, "router.bin")["n_saved"] == 3
    assert (tmp_path / "slots" / "router.bin").is_file()
    assert engine.slot_restore(info, 0, "router.bin")["n_restored"] == 3
    with pytest.raises(EngineError, match="plain file names"):
        engine.slot_save(info, 0, "../escape.bin")
    with pytest.raises(EngineError, match="HTTP 400"):
        engine.slot_restore(info, 0, "missing.bin")

    text = log.read_text(errors="replace")
    assert key not in text
    assert "--host" in text and "127.0.0.1" in text and "--alias" in text
    server_pid = real_pid(log)

    engine.stop(info, timeout=5.0)
    assert not engine.alive(info) and not engine.health(info)
    assert wait_gone(server_pid), "the server process outlived stop()"
    engine.stop(info)  # stopping twice is harmless


def test_stop_never_signals_a_reused_pid() -> None:
    """A recorded PID whose start time no longer matches belongs to another process."""
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        stranger = ServerInfo(
            model="tiny",
            engine="llamacpp",
            base_url="http://127.0.0.1:9",
            port=9,
            pid=other.pid,
            started=0.0,
            last_used=0.0,
            process_start="not-the-real-start-time",
        )
        engine = LlamaCppEngine(command_prefix=PREFIX)
        assert not engine.alive(stranger)
        engine.stop(stranger, timeout=1.0)
        assert other.poll() is None, "stop() signalled a process it did not start"
        assert process_start_time(other.pid) is not None
    finally:
        other.kill()
        other.wait(10)


def test_start_needs_the_model_file_and_a_new_enough_engine(tmp_path: Path) -> None:
    engine = LlamaCppEngine(command_prefix=PREFIX, models_dir=tmp_path)
    with pytest.raises(EngineError, match="model file not found"):
        engine.start(tiny_entry())
    (tmp_path / "test--tiny-GGUF").mkdir()
    (tmp_path / "test--tiny-GGUF" / "tiny.gguf").write_bytes(b"GGUF")
    with pytest.raises(EngineError, match="needs llama.cpp b99999"):
        engine.start(tiny_entry(min_version="b99999"))


# -- live: the installed llama-server -----------------------------------------------------------


@pytest.mark.live
def test_live_embedding_server(tmp_path: Path) -> None:
    """Start the installed llama-server on the verified embedding GGUF (CPU only), embed, stop."""
    binary = llamacpp.find_binary()
    if binary is None:
        pytest.skip("llama-server is not installed")
    entry = real_entries()["qwen3-embedding-0.6b"]
    models_dir = config.load(ROOT).models_dir()
    path = entry.local_path(models_dir)
    if path is None or not path.is_file():
        pytest.skip("Qwen3-Embedding-0.6B is not downloaded")
    if not entry.installed(models_dir) and registry.sha256_file(path) != entry.sha256:
        pytest.skip("the Qwen3-Embedding-0.6B file does not match the registry sha256")
    engine = LlamaCppEngine(binary, models_dir=models_dir, egress=net.Egress())
    assert engine.version() is not None
    info = engine.start(
        entry,
        api_key="live-test-key-0123456789",
        log_path=tmp_path / "embed.log",
        extra_args=["-ngl", "0", "--device", "none"],
        slot_save_path=tmp_path / "slots",
    )
    try:
        deadline = time.monotonic() + 120
        while not engine.health(info):
            assert engine.alive(info), (tmp_path / "embed.log").read_text(errors="replace")
            assert time.monotonic() < deadline
            time.sleep(0.2)
        response = httpx.post(
            f"{info.base_url}/v1/embeddings",
            json={"input": "hello world", "model": entry.id},
            headers=info.headers(),
            timeout=60,
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["data"][0]["embedding"]) == 1024
        assert httpx.get(f"{info.base_url}/metrics").status_code == 401
    finally:
        engine.stop(info, timeout=10.0)
    assert not pid_alive(info.pid)
