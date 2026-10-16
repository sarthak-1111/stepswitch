"""The server manager (SPEC §5.3, ARCHITECTURE §6): lazy start, keep-alive TTL, LRU eviction.

Running servers are recorded in `.stepswitch/servers.json` (gitignored project state), so a later
`ssw` process adopts them instead of starting duplicates. Each record holds the server's API key,
which is why the file lives only in the state directory and is never logged.

Engine choice, in this order: llama.cpp when the model's GGUF is installed and verified, Ollama
when it has the model, then vLLM, SGLang, MLX-LM and the transformers fallback for registry
entries with a recipe for them (`[engines.<name>]`) on a host where the engine runs. Where those
Python engines live is machine configuration: `[engines.<name>]` tables with `python`, `command`
and `env` (see `stepswitch.serve.pyserver`). Memory accounting covers only servers the manager
owns; Ollama loads and unloads models itself, so it gets a keep-alive instead of evictions and is
never counted (ADR 0008).

The RAM budget is, in order: the `ram_budget_gb` argument, `[serve] ram_budget_gb` or
`[profile] ram_budget_gb` in the configuration, or 75% of `[host] ram_gb` from `ssw doctor`.
The keep-alive TTL is the `ttl_s` argument, else `[serve] ttl_s`, else 30 minutes.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from stepswitch import paths, registry
from stepswitch.config import Config
from stepswitch.registry import EngineRecipe, ModelEntry
from stepswitch.serve.base import (
    Engine,
    EngineError,
    ServerInfo,
    log_tail,
    pid_alive,
    process_start_time,
)
from stepswitch.serve.llamacpp import LlamaCppEngine
from stepswitch.serve.mlx import MlxEngine
from stepswitch.serve.ollama import OllamaEngine, keep_alive_for, model_name
from stepswitch.serve.sglang import SglangEngine
from stepswitch.serve.transformers import TransformersEngine
from stepswitch.serve.vllm import VllmEngine

STATE_FILE = "servers.json"
DEFAULT_TTL_S = 1800.0
RAM_FRACTION = 0.75
POLL_S = 0.25
STATE_LOCK_TIMEOUT_S = 30.0
PYTHON_ENGINES = (VllmEngine, SglangEngine, MlxEngine, TransformersEngine)


def default_engines(config: Config) -> dict[str, Engine]:
    """llama.cpp first (preferred for GGUF models), then Ollama, then the Python servers in
    the order vLLM, SGLang, MLX-LM, transformers, each set up from `[engines.<name>]`."""
    models_dir = config.models_dir()
    settings = _section(config, "engines")
    engines: dict[str, Engine] = {
        "llamacpp": LlamaCppEngine(models_dir=models_dir),
        "ollama": OllamaEngine(),
    }
    for engine_class in PYTHON_ENGINES:
        table = settings.get(engine_class.name)
        engine = engine_class.from_settings(
            table if isinstance(table, dict) else None, models_dir=models_dir
        )
        if table is not None and not isinstance(table, dict):
            engine.settings_problem = "it must be a table"
        engines[engine_class.name] = engine
    return engines


def _section(config: Config, name: str) -> dict[str, Any]:
    value = (config.model_extra or {}).get(name)
    return value if isinstance(value, dict) else {}


def configured_ram_budget(config: Config) -> float | None:
    """The RAM budget in GB from the configuration, or None for no budget."""
    for section in ("serve", "profile"):
        value = _section(config, section).get("ram_budget_gb")
        if isinstance(value, int | float) and value > 0:
            return float(value)
    total = config.host.get("ram_gb")
    if isinstance(total, int | float) and total > 0:
        return round(float(total) * RAM_FRACTION, 2)
    return None


def configured_ttl(config: Config) -> float:
    value = _section(config, "serve").get("ttl_s")
    return float(value) if isinstance(value, int | float) and value > 0 else DEFAULT_TTL_S


def _safe_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", model) or "model"


class _FileLock:
    """A cross-process lock: an exclusively created file holding the owner's PID.

    A lock whose owner died (or whose PID now belongs to another process) is taken over.
    """

    def __init__(self, path: Path, timeout: float) -> None:
        self.path = path
        self.timeout = timeout

    def __enter__(self) -> _FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        owner = f"{os.getpid()} {process_start_time(os.getpid()) or ''}"
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._stale():
                    with contextlib.suppress(OSError):
                        self.path.unlink()
                    continue
                if time.monotonic() >= deadline:
                    raise EngineError(f"timed out waiting for the lock {self.path}") from None
                time.sleep(0.05)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(owner)
            return self

    def __exit__(self, *exc: object) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink()

    def _stale(self) -> bool:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return False  # just released, or still being written: try again
        pid_text, _, start = text.strip().partition(" ")
        if not pid_text.isdigit():
            return False
        pid = int(pid_text)
        if not pid_alive(pid):
            return True
        current = process_start_time(pid)
        return bool(start and current and current != start)


class ServerManager:
    """Starts, adopts, reaps and evicts local model servers for one project."""

    def __init__(
        self,
        root: Path,
        config: Config,
        *,
        engines: Mapping[str, Engine] | None = None,
        state_file: Path | None = None,
        clock: Callable[[], float] = time.time,
        ram_budget_gb: float | None = None,
        ttl_s: float | None = None,
        registry_dirs: Sequence[Path] | None = None,
    ) -> None:
        self.root = root
        self.config = config
        self.state_dir = paths.state_dir(root)
        self.state_file = state_file or self.state_dir / STATE_FILE
        self.logs_dir = self.state_dir / "logs"
        self.slots_dir = self.state_dir / "slots"
        self.locks_dir = self.state_dir / "locks"
        self.clock = clock
        self.ttl_s = ttl_s if ttl_s is not None else configured_ttl(config)
        self.ram_budget_gb = (
            ram_budget_gb if ram_budget_gb is not None else configured_ram_budget(config)
        )
        self.registry_dirs = list(registry_dirs) if registry_dirs is not None else None
        self.models_dir = config.models_dir()
        self.engines: dict[str, Engine] = (
            dict(engines) if engines is not None else default_engines(config)
        )
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    # -- runtime file ---------------------------------------------------------------------------

    @contextlib.contextmanager
    def _state(self) -> Iterator[None]:
        """Serialize access to the runtime file across threads and processes (re-entrant)."""
        with self._thread_lock:
            depth = getattr(self._local, "depth", 0)
            self._local.depth = depth + 1
            try:
                if depth:
                    yield
                else:
                    lock = self.state_file.with_name(self.state_file.name + ".lock")
                    with _FileLock(lock, STATE_LOCK_TIMEOUT_S):
                        yield
            finally:
                self._local.depth = depth

    def _read(self) -> dict[str, ServerInfo]:
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        servers = raw.get("servers") if isinstance(raw, dict) else None
        records: dict[str, ServerInfo] = {}
        for key, data in (servers or {}).items():
            if not isinstance(data, dict):
                continue
            try:
                records[str(key)] = ServerInfo.from_dict(data)
            except TypeError:
                continue
        return records

    def _write(self, records: Mapping[str, ServerInfo]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "servers": {key: records[key].to_dict() for key in sorted(records)},
        }
        tmp = self.state_file.with_name(f"{self.state_file.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if sys.platform != "win32":
            tmp.chmod(0o600)  # holds API keys
        for attempt in range(20):
            try:
                tmp.replace(self.state_file)
                return
            except PermissionError:  # a reader holds the file open on Windows
                if attempt == 19:
                    raise
                time.sleep(0.05)

    def _put(self, info: ServerInfo) -> None:
        with self._state():
            records = self._read()
            records[info.model] = info
            self._write(records)

    def _drop(self, key: str) -> None:
        with self._state():
            records = self._read()
            if records.pop(key, None) is not None:
                self._write(records)

    def _record(self, key: str) -> ServerInfo | None:
        with self._state():
            return self._read().get(key)

    # -- lookup ---------------------------------------------------------------------------------

    def _resolve(self, model: str) -> tuple[str, ModelEntry | None]:
        entry = registry.find(model, self.registry_dirs)
        return (entry.id if entry is not None else model), entry

    def _engine(self, name: str) -> Engine:
        engine = self.engines.get(name)
        if engine is None:
            raise EngineError(f"engine {name!r} is not available (have: {', '.join(self.engines)})")
        return engine

    def _choose(
        self, key: str, entry: ModelEntry | None, wanted: str | None
    ) -> tuple[str, ModelEntry]:
        names = [wanted] if wanted else list(self.engines)
        for name in names:
            self._engine(name)
        if entry is None:
            # Not in the registry: only a self-managing engine that already has a model of that
            # name can serve it (SPEC §4.3: no list is needed for a model to work).
            for name in names:
                engine = self.engines[name]
                if isinstance(engine, OllamaEngine) and engine.running() and engine.has_model(key):
                    return name, _ad_hoc_entry(key, name)
            raise EngineError(f"{key} is not in the model registry (see `ssw models list`)")
        reasons: list[str] = []
        for name in names:
            ok, why = self._can_serve(name, self.engines[name], entry)
            if ok:
                return name, entry
            reasons.append(f"{name}: {why}")
        raise EngineError(f"cannot serve {entry.id}: " + "; ".join(reasons))

    def _can_serve(self, name: str, engine: Engine, entry: ModelEntry) -> tuple[bool, str]:
        if isinstance(engine, LlamaCppEngine):
            if name not in entry.engines:
                return False, "no llama.cpp recipe in the registry entry"
            if entry.format != "gguf":
                return False, f"the model format is {entry.format}, not gguf"
            if not entry.installed(self.models_dir):
                return False, "the GGUF file is not installed and verified (`ssw models pull`)"
            if not engine.available():
                return False, "llama-server is not installed"
            return True, ""
        if isinstance(engine, OllamaEngine):
            if not engine.running():
                return False, f"Ollama is not running at {engine.url}"
            tag = model_name(entry)
            if not engine.has_model(tag):
                return False, f"Ollama has no {tag} (`ollama pull {tag}`)"
            return True, ""
        problem = engine.serve_problem(entry)
        return (problem is None), problem or ""

    # -- lifecycle ------------------------------------------------------------------------------

    def start(
        self,
        model: str,
        *,
        engine: str | None = None,
        port: int | None = None,
        ctx_size: int | None = None,
        wait: bool = True,
        timeout: float = 180.0,
        extra_args: Sequence[str] = (),
    ) -> ServerInfo:
        """Start a server for `model` (registry id or name) and wait until it is healthy.

        A model that is already running is returned as it is. On failure the process is
        stopped and EngineError carries the tail of the server log.
        """
        key, entry = self._resolve(model)
        with _FileLock(self.locks_dir / f"{_safe_name(key)}.lock", timeout + 60.0):
            current = self._live(key)
            if current is not None:
                if (engine and engine != current.engine) or (port and port != current.port):
                    raise EngineError(
                        f"{key} already runs on {current.engine} at {current.base_url}; "
                        "stop it first"
                    )
                return self._ready(current, wait=wait, timeout=timeout)
            return self._start(key, entry, engine, port, ctx_size, wait, timeout, extra_args)

    def ensure(self, model: str, **kwargs: Any) -> ServerInfo:
        """The running server for `model`, started on first use; marks it as used now."""
        key, entry = self._resolve(model)
        timeout = float(kwargs.get("timeout", 180.0))
        self._reap(None, exclude={key})
        with _FileLock(self.locks_dir / f"{_safe_name(key)}.lock", timeout + 60.0):
            current = self._live(key)
            if current is None:
                return self._start(
                    key,
                    entry,
                    kwargs.get("engine"),
                    kwargs.get("port"),
                    kwargs.get("ctx_size"),
                    bool(kwargs.get("wait", True)),
                    timeout,
                    tuple(kwargs.get("extra_args", ())),
                )
            current = self._ready(current, wait=bool(kwargs.get("wait", True)), timeout=timeout)
            current.last_used = self.clock()
            self._touch(key, current.last_used)
            return current

    def _live(self, key: str) -> ServerInfo | None:
        info = self._record(key)
        if info is None:
            return None
        engine = self.engines.get(info.engine)
        if engine is not None and not engine.alive(info):
            self._drop(key)
            return None
        return info

    def _ready(self, info: ServerInfo, *, wait: bool, timeout: float) -> ServerInfo:
        engine = self.engines.get(info.engine)
        if engine is None or not wait or engine.health(info):
            return info
        try:
            self._wait_healthy(engine, info, timeout)
        except EngineError:
            self._drop(info.model)
            raise
        return info

    def _touch(self, key: str, when: float) -> None:
        with self._state():
            records = self._read()
            if key in records:
                records[key].last_used = when
                self._write(records)

    def _start(
        self,
        key: str,
        entry: ModelEntry | None,
        wanted: str | None,
        port: int | None,
        ctx_size: int | None,
        wait: bool,
        timeout: float,
        extra_args: Sequence[str],
    ) -> ServerInfo:
        name, chosen = self._choose(key, entry, wanted)
        engine = self.engines[name]
        if not engine.self_managed:
            self._evict(chosen.ram_gb, exclude={key})
        attempts = 3 if port is None and not engine.self_managed else 1
        for attempt in range(1, attempts + 1):
            info = self._spawn(engine, chosen, port, ctx_size, extra_args)
            info.started = info.last_used = self.clock()
            self._put(info)
            if not wait:
                return info
            try:
                self._wait_healthy(engine, info, timeout)
            except EngineError as exc:
                self._drop(key)
                if attempt < attempts and _port_taken(str(exc)) and not engine.alive(info):
                    continue  # the free port was taken in the meantime: try another one
                raise
            except BaseException:  # interrupted while waiting: do not leave a half-started server
                with contextlib.suppress(Exception):
                    engine.stop(info)
                self._drop(key)
                raise
            return info
        raise EngineError(f"could not start {key}")  # pragma: no cover - loop always returns

    def _spawn(
        self,
        engine: Engine,
        entry: ModelEntry,
        port: int | None,
        ctx_size: int | None,
        extra_args: Sequence[str],
    ) -> ServerInfo:
        log_path = self.logs_dir / f"{_safe_name(entry.id)}.log"
        if isinstance(engine, LlamaCppEngine):
            return engine.start(
                entry,
                port=port,
                ctx_size=ctx_size,
                api_key=secrets.token_urlsafe(32),
                log_path=log_path,
                extra_args=extra_args,
                model_path=entry.local_path(self.models_dir),
                slot_save_path=self.slots_dir / _safe_name(entry.id),
            )
        if isinstance(engine, OllamaEngine):
            return engine.start(
                entry,
                ctx_size=ctx_size,
                log_path=log_path,
                keep_alive=keep_alive_for(self.ttl_s),
            )
        return engine.start(
            entry,
            port=port,
            ctx_size=ctx_size,
            api_key=secrets.token_urlsafe(32),
            log_path=log_path,
            extra_args=extra_args,
        )

    def _wait_healthy(self, engine: Engine, info: ServerInfo, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            if engine.health(info):
                return
            if not engine.alive(info):
                tail = log_tail(info.log_path)
                raise EngineError(
                    f"{info.model} exited during startup (log: {info.log_path})\n{tail}".rstrip()
                )
            if time.monotonic() >= deadline:
                tail = log_tail(info.log_path)
                with contextlib.suppress(EngineError):
                    engine.stop(info)
                raise EngineError(
                    f"{info.model} was not ready after {timeout:.0f} s and was stopped "
                    f"(log: {info.log_path})\n{tail}".rstrip()
                )
            time.sleep(POLL_S)

    def stop(self, model: str) -> bool:
        """Stop a server this manager started; True when something was stopped.

        A model that was already loaded in Ollama before the manager saw it is only forgotten,
        never unloaded.
        """
        key, _ = self._resolve(model)
        info = self._record(key)
        if info is None:
            return False
        stopped = False
        if info.managed:
            self._engine(info.engine).stop(info)
            stopped = True
        self._drop(key)
        return stopped

    def stop_all(self) -> int:
        """Stop every server this manager started; returns how many were stopped."""
        with self._state():
            records = self._read()
        count = 0
        failures: list[str] = []
        for key in sorted(records):
            try:
                count += int(self.stop(key))
            except EngineError as exc:
                failures.append(f"{key}: {exc}")
        if failures:
            raise EngineError("some servers did not stop: " + "; ".join(failures))
        return count

    def records(self) -> list[ServerInfo]:
        """Recorded servers as the runtime file has them, without checking that they run."""
        with self._state():
            records = self._read()
        return sorted(records.values(), key=lambda info: info.model)

    def status(self) -> list[ServerInfo]:
        """Recorded servers that still exist; records of dead processes are dropped."""
        with self._state():
            records = self._read()
            kept: dict[str, ServerInfo] = {}
            for key, info in records.items():
                engine = self.engines.get(info.engine)
                try:
                    alive = engine is None or engine.alive(info)
                except EngineError:
                    alive = False
                if alive:
                    kept[key] = info
            if kept.keys() != records.keys():
                self._write(kept)
        return sorted(kept.values(), key=lambda info: info.model)

    # -- keep-alive and memory ------------------------------------------------------------------

    def _accounted(self, info: ServerInfo) -> bool:
        engine = self.engines.get(info.engine)
        return info.managed and engine is not None and not engine.self_managed

    def reap_idle(self, now: float | None = None) -> list[str]:
        """Stop servers unused for longer than the keep-alive TTL; returns their models.

        Self-managing engines are left alone: they received the TTL as their own keep-alive.
        """
        return self._reap(now, exclude=set())

    def _reap(self, now: float | None, exclude: set[str]) -> list[str]:
        moment = self.clock() if now is None else now
        reaped: list[str] = []
        for info in self.status():
            if info.model in exclude or not self._accounted(info):
                continue
            if moment - info.last_used >= self.ttl_s and self.stop(info.model):
                reaped.append(info.model)
        return reaped

    def evict_for(self, ram_gb_needed: float) -> list[str]:
        """Stop least recently used servers until `ram_gb_needed` fits the RAM budget."""
        return self._evict(ram_gb_needed, exclude=set())

    def _evict(self, ram_gb_needed: float, exclude: Iterable[str]) -> list[str]:
        budget = self.ram_budget_gb
        if budget is None:
            return []
        skip = set(exclude)
        running = [info for info in self.status() if self._accounted(info)]
        used = sum(info.ram_gb or 0.0 for info in running)
        evicted: list[str] = []
        for info in sorted(running, key=lambda item: item.last_used):
            if used + ram_gb_needed <= budget + 1e-9:
                break
            if info.model in skip:
                continue
            if self.stop(info.model):
                used -= info.ram_gb or 0.0
                evicted.append(info.model)
        return evicted


def _port_taken(message: str) -> bool:
    """The startup failure is a lost race for the port (llama-server: "couldn't bind")."""
    text = message.lower()
    return any(sign in text for sign in ("couldn't bind", "address already in use", "eaddrinuse"))


def _ad_hoc_entry(name: str, engine: str) -> ModelEntry:
    """A minimal registry entry for a model an engine already has under `name`."""
    return ModelEntry(
        id=name,
        name=name,
        hub_id=name,
        quant="unknown",
        file_size_gb=0.0,
        ram_gb=0.0,
        context=0,
        license="unknown",
        engines={engine: EngineRecipe(name=name)},
    )
