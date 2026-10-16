"""Shared machinery for engines that run as a Python server process (SPEC §5.3, ADR 0008).

vLLM, SGLang, MLX-LM and the transformers wrapper are Python programs. Stepswitch starts each one
as a separate process from the engine's own environment and reaches it only over HTTP on the
loopback address, so the core never imports them. An adapter builds one exact command line from
the registry recipe (`[engines.<name>]` in a registry entry: `name`, `args`, `tool_parser`,
`reasoning_parser`, `min_version`) plus the flags it owns (binding, the served model name), and
it starts the server with model downloads and usage statistics switched off in its environment:
weights come from the models directory or from the engine's existing local cache. The API key
never appears on the command line: it travels in an environment variable, or in a private file
where the engine reads no such variable.

The model source is, in order: an explicit `model_path`, the recipe's `name` (a local path, or a
model id the engine already has in its local cache), or `<models dir>/<org>--<name>/` when that
directory holds a `config.json`. Remote URIs are refused.

Where an engine lives is machine configuration (`.stepswitch/local.toml`), not registry data:

    [engines.vllm]
    python = "/opt/engines/vllm/bin/python"   # the interpreter of the engine's environment
    command = ["/opt/engines/vllm/bin/vllm"]  # or the whole launcher instead
    env = { CUDA_VISIBLE_DEVICES = "0,1" }    # added to the server's environment

`command` replaces everything before the model arguments: `vllm` (the adapter adds `serve`),
`sglang serve --model-type llm`, `mlx_lm.server`, or `python -I <path>/transformers_server.py`.
Without `python` or `command`, the adapter looks for the engine's console script on PATH.
Engines are never installed automatically, since an install would download outside
`stepswitch.net`.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import tempfile
import time
from abc import abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import httpx

from stepswitch import net, paths
from stepswitch.registry import ModelEntry
from stepswitch.serve.base import (
    LOOPBACK,
    Engine,
    EngineError,
    ServerInfo,
    free_port,
    http_client,
    process_start_time,
    same_process,
    spawn,
    terminate,
)

PURPOSE = "local-engine"

# Set for every server: no downloads from the model hub (weights must already be local) and no
# usage statistics. The hub client treats DO_NOT_TRACK like HF_HUB_DISABLE_TELEMETRY.
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"}

OWNED = "the server manager sets it"
FROM_RECIPE = "set tool_parser or reasoning_parser in the recipe instead"

_SITE_GLOBS = (
    "lib/python3*/site-packages",
    "lib/python3*/dist-packages",  # Debian system interpreters
    "lib64/python3*/site-packages",
    "Lib/site-packages",  # Windows
)
_SAMPLE_RE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)"
)
_LABEL_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"')
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


# -- Prometheus text format ---------------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    """One sample of the Prometheus text format."""

    name: str
    labels: tuple[tuple[str, str], ...]
    value: float

    @property
    def key(self) -> str:
        """The sample as the exposition writes it: `name{label="value",...}`."""
        if not self.labels:
            return self.name
        return self.name + "{" + ",".join(f'{k}="{v}"' for k, v in self.labels) + "}"


def parse_samples(text: str) -> list[Sample]:
    """Every sample in a `/metrics` body; comments and unreadable lines are skipped."""
    samples: list[Sample] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_RE.match(line)
        if match is None:
            continue
        try:
            value = float(match["value"])
        except ValueError:
            continue
        labels = tuple(_LABEL_RE.findall(match["labels"] or ""))
        samples.append(Sample(match["name"], labels, value))
    return samples


def samples_dict(samples: Sequence[Sample]) -> dict[str, float]:
    return {sample.key: sample.value for sample in samples}


def counter_total(samples: Sequence[Sample], name: str) -> float | None:
    """A counter summed over its label sets. Counters are exposed with a `_total` suffix (the
    Prometheus client adds it); the plain name is accepted too. None when the counter is absent."""
    for candidate in (f"{name}_total", name):
        values = [sample.value for sample in samples if sample.name == candidate]
        if values:
            return sum(values)
    return None


def gauge_mean(samples: Sequence[Sample], name: str) -> float | None:
    values = [sample.value for sample in samples if sample.name == name]
    return sum(values) / len(values) if values else None


# -- command lines ------------------------------------------------------------------------------


def check_args(args: Sequence[str], forbidden: Mapping[str, str], source: str) -> None:
    """Refuse flags the adapter owns (or that belong in recipe fields)."""
    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag in forbidden:
            raise EngineError(f"{source} may not set {flag} ({forbidden[flag]})")


def with_flag(args: Sequence[str], flags: Sequence[str], value: str) -> list[str]:
    """`args` with every one of `flags` set to `value`; the first flag is appended if absent."""
    out: list[str] = []
    replaced = False
    items = list(args)
    index = 0
    while index < len(items):
        arg = items[index]
        flag, sep, _ = arg.partition("=")
        if flag in flags and sep:
            out.append(f"{flag}={value}")
            replaced = True
        elif arg in flags:
            out.extend([arg, value])
            replaced = True
            index += 1  # skip the old value
        else:
            out.append(arg)
        index += 1
    return out if replaced else [*out, flags[0], value]


# -- versions and environments ------------------------------------------------------------------


def release(version: str | None) -> tuple[int, ...] | None:
    """(0, 31, 0) for "0.31.0", "v0.31.0", "0.31.0rc1" or "0.31.0+cu130"; None if unreadable."""
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)", version or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def meets_minimum(version: str | None, minimum: str | None) -> bool:
    """True unless both releases are known and `version` is older than `minimum`."""
    have, need = release(version), release(minimum)
    if have is None or need is None:
        return True
    width = max(len(have), len(need))
    return have + (0,) * (width - len(have)) >= need + (0,) * (width - len(need))


def environment_root(program: Path) -> Path | None:
    """The environment a program belongs to: `<root>` for `<root>/bin/x` or `<root>/Scripts/x`."""
    return program.parent.parent if program.parent.name.lower() in ("bin", "scripts") else None


def site_dirs(root: Path) -> list[Path]:
    return [site for pattern in _SITE_GLOBS for site in sorted(root.glob(pattern))]


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_version(root: Path, dist: str) -> str | None:
    """A distribution's version from its `.dist-info` folder in an environment, without running
    or importing anything from it."""
    wanted = _normalize(dist)
    for site in site_dirs(root):
        for info in sorted(site.glob("*.dist-info")):
            name, sep, version = info.name[: -len(".dist-info")].partition("-")
            if sep and version and _normalize(name) == wanted:
                return version
    return None


def safe_name(model: str) -> str:
    return _UNSAFE_NAME_RE.sub("_", model) or "model"


# -- the adapter base ---------------------------------------------------------------------------


class PythonServerEngine(Engine):
    """A model server started as a Python program from the engine's own environment."""

    #: Display name for messages.
    label: str = ""
    #: The distribution whose version `version()` reports and `min_version` is checked against,
    #: and other names it is published under.
    dist: str = ""
    dist_aliases: tuple[str, ...] = ()
    #: Other distributions the engine's environment must hold.
    requires: tuple[str, ...] = ()
    #: The console script that starts the server (looked up on PATH) and its arguments.
    script: str | None = None
    script_args: tuple[str, ...] = ()
    #: Arguments after a configured interpreter that start the server (`-m <module>`).
    module_args: tuple[str, ...] = ()
    #: How to install the engine, for the "not available" reason.
    install_hint: str = ""
    #: Whether the server checks an API key, and the environment variable that carries it
    #: (None: `key_args` hands it over instead).
    authenticates: bool = True
    api_key_env: str | None = None
    #: Fixed environment values (usage statistics off, alternative download sources off).
    fixed_env: Mapping[str, str] = {}
    #: The readiness and metrics endpoints.
    health_path: str = "/health"
    metrics_path: str | None = None

    def __init__(
        self,
        *,
        python: str | Path | None = None,
        command_prefix: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        models_dir: Path | None = None,
        egress: net.Egress | None = None,
        transport: httpx.BaseTransport | None = None,
        check_platform: bool = True,
    ) -> None:
        """`command_prefix` replaces the launcher lookup (tests run a stand-in server this way);
        `check_platform=False` skips the host platform check (tests only)."""
        self.python = str(python) if python is not None else None
        self.command_prefix = [str(part) for part in command_prefix]
        self.env = dict(env or {})
        self.models_dir = models_dir if models_dir is not None else paths.default_models_dir()
        self.egress = egress
        self.transport = transport
        self.check_platform = check_platform
        self.settings_problem: str | None = None
        self._versions: dict[tuple[str, ...], str | None] = {}

    @classmethod
    def from_settings(
        cls,
        settings: Mapping[str, Any] | None,
        *,
        models_dir: Path | None = None,
        egress: net.Egress | None = None,
    ) -> Self:
        """An adapter from an `[engines.<name>]` table of the configuration. Malformed values
        make the engine unavailable with a reason instead of failing every command."""
        data = dict(settings or {})
        problems: list[str] = []
        python = data.get("python")
        if python is not None and not isinstance(python, str):
            problems.append("`python` must be a path")
            python = None
        command = data.get("command")
        if isinstance(command, str):
            command = [command]
        if command is not None and not (
            isinstance(command, list) and command and all(isinstance(p, str) for p in command)
        ):
            problems.append("`command` must be a list of strings")
            command = None
        env = data.get("env")
        if env is not None and not (
            isinstance(env, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        ):
            problems.append("`env` must be a table of strings")
            env = None
        engine = cls(
            python=python,
            command_prefix=command or (),
            env=env,
            models_dir=models_dir,
            egress=egress,
        )
        engine.settings_problem = "; ".join(problems) or None
        return engine

    # -- what each engine defines -----------------------------------------------------------------

    @abstractmethod
    def platform_problem(self, system: str, machine: str) -> str | None:
        """Why the engine cannot run on this OS (`sys.platform`) and CPU (`platform.machine()`)."""

    @abstractmethod
    def command(
        self,
        entry: ModelEntry,
        model: str,
        *,
        port: int,
        ctx_size: int | None = None,
        extra_args: Sequence[str] = (),
    ) -> list[str]:
        """The exact command line: launcher, recipe args, extra args and the owned flags. It
        never holds the API key (see `api_key_env` and `key_args`)."""

    def key_args(self, api_key: str, log_path: Path) -> list[str]:
        """Arguments that hand the key over when no environment variable can (default none)."""
        return []

    def served_name(self, entry: ModelEntry) -> str:
        """The model name clients send in requests."""
        return entry.id

    # -- discovery --------------------------------------------------------------------------------

    def _interpreter(self) -> Path | None:
        if self.python is None:
            return None
        candidate = Path(self.python).expanduser()
        if candidate.is_file():
            return candidate
        found = shutil.which(self.python)
        return Path(found) if found else None

    def find_launcher(self) -> list[str] | None:
        """The program part of the command line, or None when the engine is not found."""
        if self.command_prefix:
            return list(self.command_prefix)
        if self.python is not None:
            interpreter = self._interpreter()
            return [str(interpreter), *self.module_args] if interpreter else None
        found = shutil.which(self.script) if self.script else None
        return [found, *self.script_args] if found else None

    def launcher(self) -> list[str]:
        found = self.find_launcher()
        if found is None:
            raise EngineError(self.unavailable_reason() or f"{self.label} is not installed")
        return found

    def unavailable_reason(self) -> str | None:
        """Why the engine cannot start servers on this host, or None when it can."""
        if self.settings_problem:
            return f"[engines.{self.name}] in the configuration: {self.settings_problem}"
        if self.check_platform:
            problem = self.platform_problem(sys.platform, platform.machine())
            if problem:
                return problem
        if self.command_prefix:
            return None
        launcher = self.find_launcher()
        if launcher is None:
            if self.python is not None:
                return f"the interpreter {self.python} configured for {self.label} was not found"
            return f"{self.label} was not found; {self.install_hint}"
        root = environment_root(Path(launcher[0]))
        if root is not None and site_dirs(root):
            missing = [] if self._engine_version(root) else [self.dist]
            missing += [d for d in self.requires if installed_version(root, d) is None]
            if missing:
                return f"{', '.join(missing)} not installed in {root}; {self.install_hint}"
        return None

    def available(self) -> bool:
        return self.unavailable_reason() is None

    def _engine_version(self, root: Path) -> str | None:
        for dist in (self.dist, *self.dist_aliases):
            found = installed_version(root, dist)
            if found:
                return found
        return None

    def version(self) -> str | None:
        """The engine's version from its environment's metadata (nothing is run or imported)."""
        launcher = self.find_launcher()
        if launcher is None:
            return None
        key = tuple(launcher)
        if key not in self._versions:
            root = environment_root(Path(launcher[0]))
            self._versions[key] = self._engine_version(root) if root else None
        return self._versions[key]

    # -- models -----------------------------------------------------------------------------------

    def model_source(self, entry: ModelEntry, model_path: Path | str | None = None) -> str | None:
        """What the server loads: a local path, or a model id from the engine's local cache."""
        if model_path is not None:
            return str(model_path)
        recipe = entry.engines.get(self.name)
        if recipe is not None and recipe.name:
            # Not through Path for model ids: "org/name" must keep its forward slash.
            name = recipe.name
            return str(Path(name).expanduser()) if name.startswith("~") else name
        directory = entry.local_dir(self.models_dir)
        return str(directory) if (directory / "config.json").is_file() else None

    def serve_problem(self, entry: ModelEntry) -> str | None:
        if self.name not in entry.engines:
            return f"no {self.label} recipe ([engines.{self.name}]) in the registry entry"
        reason = self.unavailable_reason()
        if reason:
            return reason
        return _source_problem(self, entry, self.model_source(entry))

    # -- lifecycle --------------------------------------------------------------------------------

    def server_env(
        self, api_key: str | None, base: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        """The child environment: configured values, then the fixed offline and no-statistics
        values, then the API key. An inherited key variable is dropped."""
        source = os.environ if base is None else base
        dropped = {self.api_key_env.upper()} if self.api_key_env else set()
        env = {name: value for name, value in source.items() if name.upper() not in dropped}
        env.update(self.env)
        env.update(OFFLINE_ENV)
        env.update(self.fixed_env)
        if api_key and self.api_key_env:
            env[self.api_key_env] = api_key
        return env

    def start(
        self,
        entry: ModelEntry,
        *,
        port: int | None = None,
        ctx_size: int | None = None,
        api_key: str | None = None,
        log_path: Path | None = None,
        extra_args: Sequence[str] = (),
        model_path: Path | str | None = None,
    ) -> ServerInfo:
        reason = self.unavailable_reason()
        if reason:
            raise EngineError(reason)
        source = self.model_source(entry, model_path)
        problem = _source_problem(self, entry, source)
        if problem or source is None:
            raise EngineError(f"{entry.id}: {problem}")
        recipe = entry.engines.get(self.name)
        minimum = recipe.min_version if recipe else None
        found = self.version()
        if not meets_minimum(found, minimum):
            raise EngineError(f"{entry.id} needs {self.label} {minimum} or newer; found {found}")
        port = port if port is not None else free_port()
        key = api_key if self.authenticates else None
        log = log_path or Path(tempfile.gettempdir()) / "stepswitch" / f"{safe_name(entry.id)}.log"
        cmd = self.command(entry, source, port=port, ctx_size=ctx_size, extra_args=extra_args)
        if key and not self.api_key_env:
            cmd += self.key_args(key, log)
        proc = spawn(cmd, log_path=log, env=self.server_env(key))
        now = time.time()
        return ServerInfo(
            model=entry.id,
            engine=self.name,
            base_url=f"http://{LOOPBACK}:{port}",
            port=port,
            pid=proc.pid,
            started=now,
            last_used=now,
            api_key=key,
            managed=True,
            ram_gb=entry.ram_gb,
            engine_model=self.served_name(entry),
            log_path=str(log),
            process_start=process_start_time(proc.pid),
        )

    def _client(self, timeout: float | httpx.Timeout) -> httpx.Client:
        return http_client(PURPOSE, egress=self.egress, transport=self.transport, timeout=timeout)

    def request(
        self, info: ServerInfo, method: str, path: str, *, timeout: float = 5.0, **kwargs: Any
    ) -> httpx.Response | None:
        """One request to the server with its key; None when it cannot be reached."""
        try:
            with self._client(timeout) as client:
                return client.request(
                    method, f"{info.base_url}{path}", headers=info.headers(), **kwargs
                )
        except httpx.HTTPError:
            return None

    def health(self, info: ServerInfo) -> bool:
        response = self.request(info, "GET", self.health_path, timeout=2.0)
        return response is not None and response.status_code == 200

    def alive(self, info: ServerInfo) -> bool:
        if info.pid is None:
            return self.health(info)
        return same_process(info.pid, info.process_start)

    def stop(self, info: ServerInfo, timeout: float = 10.0) -> None:
        if info.pid is None or not same_process(info.pid, info.process_start):
            return  # already gone, or the PID now belongs to another process
        terminate(info.pid, timeout=timeout)

    # -- metrics ----------------------------------------------------------------------------------

    def metric_samples(self, info: ServerInfo) -> list[Sample] | None:
        if self.metrics_path is None:
            return None
        response = self.request(info, "GET", self.metrics_path)
        if response is None or response.status_code != 200:
            return None
        return parse_samples(response.text)

    def metrics(self, info: ServerInfo) -> dict[str, Any] | None:
        """The `/metrics` samples as {sample name with labels: value}."""
        samples = self.metric_samples(info)
        return None if samples is None else dict(samples_dict(samples))


def _source_problem(
    engine: PythonServerEngine, entry: ModelEntry, source: str | None
) -> str | None:
    if source is None:
        directory = entry.local_dir(engine.models_dir)
        return (
            f"no model directory with a config.json at {directory}; set `name` in "
            f"[engines.{engine.name}] to a local path or a model id in the engine's cache"
        )
    if "://" in source:
        return f"the model source {source!r} is remote; {engine.label} must load local files"
    return None
