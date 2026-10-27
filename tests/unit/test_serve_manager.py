"""Server manager: start/ensure/status/stop, TTL reaping, LRU eviction, engine choice, cleanup.

llama.cpp servers are stand-ins (fixtures/serve/fake_llama_server.py) started as real processes;
Ollama is a mock transport. Every test stops what it started, even when it fails.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import config as config_module
from stepswitch import net, registry
from stepswitch.config import Config
from stepswitch.serve import llamacpp as llamacpp_module
from stepswitch.serve import manager as manager_module
from stepswitch.serve import pyserver as pyserver_module
from stepswitch.serve.base import Engine, EngineError, pid_alive, terminate
from stepswitch.serve.llamacpp import LlamaCppEngine, find_binary
from stepswitch.serve.manager import ServerManager
from stepswitch.serve.mlx import MlxEngine
from stepswitch.serve.ollama import OllamaEngine
from stepswitch.serve.sglang import SglangEngine
from stepswitch.serve.transformers import TransformersEngine
from stepswitch.serve.vllm import VllmEngine

FAKE = Path(__file__).parent / "fixtures" / "serve" / "fake_llama_server.py"
ENGINE_FAKE = Path(__file__).parent / "fixtures" / "serve" / "fake_engine_server.py"

ENTRY = """\
schema = 1

[model]
id = "{id}"
name = "{name}"
hub_id = "test/{id}-GGUF"
file = "{id}.gguf"
sha256 = "{sha}"
size_bytes = {size}
quant = "Q8_0"
file_size_gb = 0.001
ram_gb = {ram}
ram_ctx = 2048
context = 32768
license = "apache-2.0"

[engines.llamacpp]
min_version = "b11669"
args = ["--ctx-size", "2048"]

[engines.ollama]
name = "{id}:q8"
"""


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeOllama:
    def __init__(self, tags: list[str]) -> None:
        self.tags = tags
        self.loaded: dict[str, int] = {}
        self.generate: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.34.4"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": t, "size": 1} for t in self.tags]})
        if path == "/api/ps":
            models = [{"name": n, "model": n, "size": s} for n, s in self.loaded.items()]
            return httpx.Response(200, json={"models": models})
        if path == "/api/generate":
            self.generate.append(body)
            if body.get("keep_alive") == 0:
                self.loaded.pop(body["model"], None)
            else:
                self.loaded[body["model"]] = 2_000_000_000
            return httpx.Response(200, json={"model": body["model"], "done": True})
        return httpx.Response(404, json={"error": "not found"})


class World:
    """A project, a models directory with verified fake GGUFs, and a registry of test models."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "project"
        self.root.mkdir()
        self.models = tmp_path / "models"
        self.registry = tmp_path / "registry"
        self.registry.mkdir()
        self.config = Config.model_validate({"storage": {"models_dir": str(self.models)}})
        self.clock = Clock()
        self.ollama = FakeOllama([])
        self.managers: list[ServerManager] = []
        self.llamacpp = LlamaCppEngine(
            command_prefix=[sys.executable, str(FAKE)],
            models_dir=self.models,
            egress=net.Egress(),
        )

    def model(self, model_id: str, *, ram: float = 1.0, installed: bool = True) -> None:
        data = f"GGUF {model_id}".encode()
        sha = hashlib.sha256(data).hexdigest()
        text = ENTRY.format(id=model_id, name=model_id.upper(), sha=sha, size=len(data), ram=ram)
        (self.registry / f"{model_id}.toml").write_text(text, encoding="utf-8")
        if installed:
            folder = self.models / f"test--{model_id}-GGUF"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{model_id}.gguf").write_bytes(data)
            (folder / f"{model_id}.gguf.sha256").write_text(f"{sha}  {model_id}.gguf\n")

    def manager(self, *, with_ollama: bool = False, **kw: Any) -> ServerManager:
        engines: dict[str, Engine] = {"llamacpp": self.llamacpp}
        if with_ollama:
            engines["ollama"] = OllamaEngine(
                "http://127.0.0.1:11434",
                egress=net.Egress(),
                transport=httpx.MockTransport(self.ollama),
            )
        kw.setdefault("ttl_s", 600.0)
        made = ServerManager(
            self.root,
            self.config,
            engines=engines,
            clock=self.clock,
            registry_dirs=[self.registry],
            **kw,
        )
        self.managers.append(made)
        return made

    @property
    def state_file(self) -> Path:
        return self.root / ".stepswitch" / "servers.json"

    def records(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {}
        servers: dict[str, Any] = json.loads(self.state_file.read_text(encoding="utf-8"))["servers"]
        return servers


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    made = World(tmp_path)
    yield made
    for manager in made.managers:
        for info in manager.status():
            if info.pid is not None:
                terminate(info.pid, timeout=5.0)
        manager.stop_all()


def server_pid(log_path: str | None) -> int:
    assert log_path is not None
    text = Path(log_path).read_text(errors="replace")
    pids = re.findall(r"fake llama-server pid=(\d+)", text)
    assert pids, text
    return int(pids[-1])


def gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while pid_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


# -- lifecycle ----------------------------------------------------------------------------------


def test_start_status_stop(world: World) -> None:
    world.model("tiny-a")
    manager = world.manager()
    info = manager.start("tiny-a")
    assert info.engine == "llamacpp" and info.managed and info.pid
    assert info.base_url == f"http://127.0.0.1:{info.port}" and info.ram_gb == 1.0
    assert info.started == info.last_used == 1000.0
    assert world.llamacpp.health(info)

    record = world.records()["tiny-a"]
    assert record["pid"] == info.pid and record["port"] == info.port
    assert record["api_key"] == info.api_key and info.api_key  # kept only in the state dir
    log = world.root / ".stepswitch" / "logs" / "tiny-a.log"
    assert record["log_path"] == str(log) and log.is_file()
    assert info.api_key not in log.read_text(errors="replace")
    assert info.api_key not in repr(info)
    assert (world.root / ".stepswitch" / "slots" / "tiny-a").is_dir()

    assert [s.pid for s in manager.status()] == [info.pid]
    assert manager.start("TINY-A").pid == info.pid  # already running: same server, by name too
    real = server_pid(info.log_path)

    assert manager.stop("tiny-a") is True
    assert gone(real) and not pid_alive(info.pid)
    assert manager.status() == [] and world.records() == {}
    assert manager.stop("tiny-a") is False


def test_ensure_starts_lazily_and_marks_use(world: World) -> None:
    world.model("tiny-a")
    manager = world.manager()
    first = manager.ensure("tiny-a")
    world.clock.now = 1500.0
    again = manager.ensure("tiny-a")
    assert again.pid == first.pid and again.last_used == 1500.0
    assert world.records()["tiny-a"]["last_used"] == 1500.0
    adopted = world.manager().ensure("tiny-a")  # a later process adopts the running server
    assert adopted.pid == first.pid and adopted.api_key == first.api_key


def test_stop_all(world: World) -> None:
    world.model("tiny-a")
    world.model("tiny-b")
    manager = world.manager()
    pids = [manager.start("tiny-a").pid, manager.start("tiny-b").pid]
    assert manager.stop_all() == 2
    assert all(not pid_alive(pid) for pid in pids) and world.records() == {}
    assert manager.stop_all() == 0


def test_dead_processes_are_dropped(world: World) -> None:
    world.model("tiny-a")
    manager = world.manager()
    info = manager.start("tiny-a")
    assert info.pid is not None
    terminate(info.pid, timeout=5.0)  # the server dies behind the manager's back
    assert manager.status() == []
    assert world.records() == {}
    restarted = manager.ensure("tiny-a")
    assert restarted.pid != info.pid and world.llamacpp.health(restarted)


def test_start_failure_reports_the_log_tail(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.model("tiny-a")
    monkeypatch.setenv("FAKE_LLAMA_FAIL", "failed to load model: corrupt tensor data")
    with pytest.raises(EngineError, match="corrupt tensor data") as caught:
        world.manager().start("tiny-a", timeout=20)
    assert "exited during startup" in str(caught.value)
    assert world.records() == {}


def test_start_timeout_stops_the_server(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.model("tiny-a")
    monkeypatch.setenv("FAKE_LLAMA_LOAD_SECONDS", "60")
    with pytest.raises(EngineError, match="not ready after") as caught:
        world.manager().start("tiny-a", timeout=1.5)
    log = world.root / ".stepswitch" / "logs" / "tiny-a.log"
    assert gone(server_pid(str(log)))
    assert world.records() == {} and "listening on" in str(caught.value)


def test_start_without_waiting_then_ensure_waits(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world.model("tiny-a")
    # The load ends when the test says so. A fixed load time can be over before the first
    # health check: on Windows a refused connection retries for about 2 s.
    loaded = tmp_path / "loaded"
    monkeypatch.setenv("FAKE_LLAMA_READY_FILE", str(loaded))
    manager = world.manager()
    info = manager.start("tiny-a", wait=False)
    assert not world.llamacpp.health(info)
    timer = threading.Timer(0.5, loaded.touch)
    timer.start()
    try:
        ready = manager.ensure("tiny-a")
    finally:
        timer.cancel()
    assert loaded.exists()  # ensure returned after the load, not before
    assert ready.pid == info.pid and world.llamacpp.health(ready)


# -- keep-alive and memory ----------------------------------------------------------------------


def test_idle_servers_are_reaped_after_the_ttl(world: World) -> None:
    world.model("tiny-a")
    world.model("tiny-b")
    manager = world.manager(ttl_s=600.0)
    a = manager.start("tiny-a")
    manager.start("tiny-b")
    world.clock.now = 1500.0
    manager.ensure("tiny-b")
    assert manager.reap_idle(now=1550.0) == []
    assert manager.reap_idle(now=1700.0) == ["tiny-a"]  # idle 700 s; tiny-b only 200 s
    assert [s.model for s in manager.status()] == ["tiny-b"]
    assert not pid_alive(a.pid)


def test_lru_eviction_against_the_ram_budget(world: World) -> None:
    for name in ("tiny-a", "tiny-b", "tiny-c"):
        world.model(name, ram=1.0)
    manager = world.manager(ram_budget_gb=2.5)
    world.clock.now = 1.0
    manager.start("tiny-a")
    world.clock.now = 2.0
    b = manager.start("tiny-b")
    world.clock.now = 3.0
    manager.ensure("tiny-a")  # tiny-a is now more recently used than tiny-b
    world.clock.now = 4.0
    manager.start("tiny-c")  # 3 GB > 2.5 GB: the least recently used server goes
    assert [s.model for s in manager.status()] == ["tiny-a", "tiny-c"]
    assert not pid_alive(b.pid)
    assert manager.evict_for(1.0) == ["tiny-a"]
    assert manager.evict_for(0.5) == []
    assert manager.evict_for(9.0) == ["tiny-c"]
    assert manager.status() == []


def test_no_budget_means_no_eviction(world: World) -> None:
    world.model("tiny-a", ram=50.0)
    manager = world.manager(ram_budget_gb=None)
    assert manager.ram_budget_gb is None
    manager.start("tiny-a")
    assert manager.evict_for(100.0) == []


def test_budget_and_ttl_from_the_configuration() -> None:
    config = Config.model_validate({"host": {"ram_gb": 8}})
    assert manager_module.configured_ram_budget(config) == 6.0
    config = Config.model_validate({"host": {"ram_gb": 8}, "serve": {"ram_budget_gb": 5}})
    assert manager_module.configured_ram_budget(config) == 5.0
    config = Config.model_validate({"profile": {"ram_budget_gb": 3.5}})
    assert manager_module.configured_ram_budget(config) == 3.5
    assert manager_module.configured_ram_budget(Config()) is None
    assert manager_module.configured_ttl(Config()) == 1800.0
    assert manager_module.configured_ttl(Config.model_validate({"serve": {"ttl_s": 60}})) == 60.0


# -- engine choice ------------------------------------------------------------------------------


def test_llamacpp_when_the_gguf_is_installed_else_ollama(world: World) -> None:
    world.model("tiny-a")
    world.model("tiny-c", installed=False)
    world.ollama.tags = ["tiny-c:q8"]
    manager = world.manager(with_ollama=True)

    assert manager.start("tiny-a").engine == "llamacpp"
    info = manager.start("tiny-c")
    assert info.engine == "ollama" and info.pid is None and info.managed
    assert info.engine_model == "tiny-c:q8" and info.api_key is None
    assert world.ollama.generate == [
        {"model": "tiny-c:q8", "keep_alive": "10m", "stream": False, "options": {"num_ctx": 2048}}
    ]
    assert {s.model for s in manager.status()} == {"tiny-a", "tiny-c"}

    assert manager.stop("tiny-c") is True
    assert world.ollama.generate[-1] == {"model": "tiny-c:q8", "keep_alive": 0, "stream": False}
    assert world.ollama.loaded == {}


def test_ollama_models_are_neither_evicted_nor_reaped(world: World) -> None:
    world.model("tiny-c", installed=False)
    world.ollama.tags = ["tiny-c:q8"]
    manager = world.manager(with_ollama=True, ram_budget_gb=0.1)
    info = manager.start("tiny-c")
    assert info.ram_gb == 2.0  # reported by Ollama, not counted against the budget
    assert manager.evict_for(1.0) == []
    assert manager.reap_idle(now=10_000_000.0) == []
    assert [s.model for s in manager.status()] == ["tiny-c"]
    world.ollama.loaded.clear()  # Ollama's own keep-alive ran out
    assert manager.status() == []


def test_models_already_loaded_in_ollama_are_only_forgotten(world: World) -> None:
    world.model("tiny-c", installed=False)
    world.ollama.tags = ["tiny-c:q8"]
    world.ollama.loaded = {"tiny-c:q8": 5}
    manager = world.manager(with_ollama=True)
    info = manager.start("tiny-c")
    assert not info.managed and world.ollama.generate == []
    assert manager.stop("tiny-c") is False
    assert world.ollama.generate == [] and "tiny-c:q8" in world.ollama.loaded


def test_explicit_engine_and_unservable_models(world: World) -> None:
    world.model("tiny-a")
    world.model("tiny-d", installed=False)
    world.ollama.tags = ["tiny-a:q8"]
    manager = world.manager(with_ollama=True)
    assert manager.start("tiny-a", engine="ollama").engine == "ollama"
    with pytest.raises(EngineError, match="already runs on ollama"):
        manager.start("tiny-a", engine="llamacpp")
    with pytest.raises(EngineError) as caught:
        manager.start("tiny-d")
    assert "not installed" in str(caught.value) and "Ollama has no tiny-d:q8" in str(caught.value)
    with pytest.raises(EngineError, match="not in the model registry"):
        manager.start("no-such-model")
    with pytest.raises(EngineError, match="not available"):
        manager.start("tiny-d", engine="vllm")


def test_models_outside_the_registry_can_come_from_ollama(world: World) -> None:
    world.ollama.tags = ["someones-model:7b"]
    manager = world.manager(with_ollama=True)
    info = manager.start("someones-model:7b")
    assert info.engine == "ollama" and info.model == "someones-model:7b"
    assert manager.stop("someones-model:7b") is True


# -- robustness ---------------------------------------------------------------------------------


def test_a_taken_free_port_is_retried(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The free port can be taken before the server binds it; the manager tries another."""
    world.model("tiny-a")
    blocker = socket.socket()
    if sys.platform == "win32":
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    taken = blocker.getsockname()[1]
    ports = iter([taken])
    real_free_port = llamacpp_module.free_port
    monkeypatch.setattr(llamacpp_module, "free_port", lambda: next(ports, None) or real_free_port())
    try:
        info = world.manager().start("tiny-a", timeout=20)
    finally:
        blocker.close()
    assert info.port != taken and world.llamacpp.health(info)
    log = Path(str(info.log_path)).read_text(errors="replace")
    assert "couldn't bind" in log  # the first attempt failed the way llama-server does


def test_malformed_runtime_file_is_ignored(world: World) -> None:
    world.state_file.parent.mkdir(parents=True)
    world.state_file.write_text("{not json", encoding="utf-8")
    manager = world.manager()
    assert manager.status() == []
    bad = {"model": "x", "engine": "llamacpp", "base_url": "http://127.0.0.1:1", "port": "1"}
    record = {**bad, "pid": "12", "started": 0, "last_used": 0}
    world.state_file.write_text(json.dumps({"version": 1, "servers": {"x": record}}))
    assert manager.status() == []


def test_state_file_is_private_on_posix(world: World) -> None:
    world.model("tiny-a")
    world.manager().start("tiny-a")
    if sys.platform != "win32":
        assert world.state_file.stat().st_mode & 0o077 == 0


# -- Python server engines ----------------------------------------------------------------------

PYTHON_ENTRY = """\
schema = 1

[model]
id = "{id}"
name = "{id}"
hub_id = "test/{id}"
quant = "bf16"
format = "safetensors"
file_size_gb = 0.1
ram_gb = 1.0
context = 4096
license = "apache-2.0"

[engines.{engine}]
args = ["--dtype", "float32"]
"""


def python_model(world: World, model_id: str, engine: str, *, local: bool = True) -> None:
    """A registry entry with only an `[engines.<engine>]` recipe, and its model directory."""
    text = PYTHON_ENTRY.format(id=model_id, engine=engine)
    (world.registry / f"{model_id}.toml").write_text(text, encoding="utf-8")
    if local:
        folder = world.models / f"test--{model_id}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "config.json").write_text("{}", encoding="utf-8")


def fake_vllm(world: World, **kw: Any) -> VllmEngine:
    kw.setdefault("check_platform", False)
    return VllmEngine(
        command_prefix=[sys.executable, str(ENGINE_FAKE)],
        models_dir=world.models,
        egress=net.Egress(),
        **kw,
    )


def python_manager(world: World, *engines: Engine) -> ServerManager:
    made = ServerManager(
        world.root,
        world.config,
        engines={"llamacpp": world.llamacpp, **{engine.name: engine for engine in engines}},
        clock=world.clock,
        registry_dirs=[world.registry],
        ttl_s=600.0,
    )
    world.managers.append(made)
    return made


def engine_pid(log_path: str | None) -> int:
    assert log_path is not None
    text = Path(log_path).read_text(errors="replace")
    pids = re.findall(r"fake engine flavor=\w+ pid=(\d+)", text)
    assert pids, text
    return int(pids[-1])


def test_default_engines_include_the_python_servers(tmp_path: Path) -> None:
    config = Config.model_validate(
        {
            "storage": {"models_dir": str(tmp_path / "models")},
            "engines": {
                "vllm": {"python": "/opt/vllm/bin/python", "env": {"OMP_NUM_THREADS": "4"}},
                "sglang": {"command": ["sglang-wrapper", "serve"]},
                "mlx": {"command": [1]},
                "transformers": "not a table",
            },
        }
    )
    engines = manager_module.default_engines(config)
    assert list(engines) == [
        "llamacpp",
        "ollama",
        "vllm",
        "sglang",
        "mlx",
        "transformers",
        "airllm",
        "ktransformers",
    ]
    vllm = engines["vllm"]
    assert isinstance(vllm, VllmEngine) and vllm.python == "/opt/vllm/bin/python"
    assert vllm.env == {"OMP_NUM_THREADS": "4"} and vllm.models_dir == tmp_path / "models"
    sglang = engines["sglang"]
    assert isinstance(sglang, SglangEngine)
    assert sglang.command_prefix == ["sglang-wrapper", "serve"]
    mlx = engines["mlx"]
    assert isinstance(mlx, MlxEngine)
    assert "[engines.mlx] in the configuration" in str(mlx.unavailable_reason())
    transformers = engines["transformers"]
    assert isinstance(transformers, TransformersEngine)
    assert transformers.unavailable_reason() == (
        "[engines.transformers] in the configuration: it must be a table"
    )
    plain = manager_module.default_engines(Config())
    assert all(
        isinstance(engine, pyserver_module.PythonServerEngine) and engine.settings_problem is None
        for name, engine in plain.items()
        if name not in ("llamacpp", "ollama")
    )


def test_a_model_with_only_a_python_recipe_runs_on_that_engine(world: World) -> None:
    world.model("tiny-a")
    python_model(world, "tiny-v", "vllm")
    vllm = fake_vllm(world)
    manager = python_manager(world, vllm)

    info = manager.start("tiny-v", ctx_size=2048)
    assert info.engine == "vllm" and info.engine_model == "tiny-v" and info.api_key
    assert info.managed and info.ram_gb == 1.0 and vllm.health(info)
    record = world.records()["tiny-v"]
    assert record["engine"] == "vllm" and record["api_key"] == info.api_key
    log = world.root / ".stepswitch" / "logs" / "tiny-v.log"
    assert info.log_path == str(log)
    text = log.read_text(errors="replace")
    assert info.api_key not in text and "--max-model-len 2048" in text
    models = httpx.get(f"{info.base_url}/v1/models", headers=info.headers())
    assert models.status_code == 200 and models.json()["data"][0]["id"] == "tiny-v"

    assert manager.start("tiny-a").engine == "llamacpp"  # GGUF entries keep llama.cpp
    assert {s.model: s.engine for s in manager.status()} == {
        "tiny-a": "llamacpp",
        "tiny-v": "vllm",
    }
    real = engine_pid(info.log_path)
    assert manager.stop("tiny-v") is True
    assert gone(real) and [s.model for s in manager.status()] == ["tiny-a"]


def test_idle_python_engine_servers_are_reaped(world: World) -> None:
    python_model(world, "tiny-v", "vllm")
    manager = python_manager(world, fake_vllm(world))
    info = manager.start("tiny-v")
    real = engine_pid(info.log_path)
    world.clock.now += 601.0
    assert manager.reap_idle() == ["tiny-v"]
    assert gone(real) and manager.status() == []


def test_why_a_python_engine_cannot_serve(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    python_model(world, "tiny-v", "vllm", local=False)
    manager = python_manager(world, fake_vllm(world))
    with pytest.raises(EngineError) as caught:
        manager.start("tiny-v")
    text = str(caught.value)
    assert "llamacpp: no llama.cpp recipe" in text
    assert "vllm: no model directory with a config.json" in text

    python_model(world, "tiny-w", "vllm")
    gated = fake_vllm(world, check_platform=True)
    monkeypatch.setattr(gated, "platform_problem", lambda system, machine: "not on this host")
    with pytest.raises(EngineError, match="vllm: not on this host"):
        python_manager(world, gated).start("tiny-w", engine="vllm")


def test_a_taken_free_port_is_retried_for_python_engines(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_model(world, "tiny-v", "vllm")
    monkeypatch.setenv("FAKE_ENGINE_LOAD_SECONDS", "0")
    blocker = socket.socket()
    if sys.platform == "win32":
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    taken = blocker.getsockname()[1]
    ports = iter([taken])
    real_free_port = pyserver_module.free_port
    monkeypatch.setattr(pyserver_module, "free_port", lambda: next(ports, None) or real_free_port())
    vllm = fake_vllm(world)
    try:
        info = python_manager(world, vllm).start("tiny-v", timeout=20)
    finally:
        blocker.close()
    assert info.port != taken and vllm.health(info)
    log = Path(str(info.log_path)).read_text(errors="replace")
    assert "address already in use" in log  # the first attempt failed the way vLLM does


# -- live: the installed llama-server -----------------------------------------------------------


@pytest.mark.live
def test_live_manager_embedding_server(tmp_path: Path) -> None:
    """ServerManager with the installed llama-server and the stamped embedding GGUF, CPU only."""
    root = Path(__file__).resolve().parents[2]
    project = config_module.load(root)
    entry = registry.find("qwen3-embedding-0.6b", [root / "registry"])
    assert entry is not None
    if find_binary() is None or not entry.installed(project.models_dir()):
        pytest.skip("needs llama-server and a verified Qwen3-Embedding-0.6B (`ssw models pull`)")
    manager = ServerManager(
        tmp_path,
        project,
        engines={"llamacpp": LlamaCppEngine(models_dir=project.models_dir())},
        registry_dirs=[root / "registry"],
    )
    info = manager.start("qwen3-embedding-0.6b", extra_args=["--device", "none"], timeout=120)
    try:
        response = httpx.post(
            f"{info.base_url}/v1/embeddings",
            json={"input": "hello world", "model": info.model},
            headers=info.headers(),
            timeout=60,
        )
        assert response.status_code == 200 and len(response.json()["data"][0]["embedding"]) == 1024
    finally:
        assert manager.stop("qwen3-embedding-0.6b")
    assert not pid_alive(info.pid) and manager.status() == []
