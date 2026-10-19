"""The llama.cpp server adapter (SPEC §4.2, §5.3; ADR 0008).

One `llama-server` process per model, built from the registry recipe plus flags the adapter
owns: the model path, the loopback host, a port, metrics, a slot directory, the alias and
`--offline`. The API key reaches the server through its `LLAMA_API_KEY` environment variable
rather than the command line, so it never shows up in process listings or process audit logs.
Recipe and extra arguments may not override the owned flags, enable the RPC backend, download
models, or turn on server-side tools (ADR 0008, RESEARCH.md "llama.cpp").

Flags and endpoints were checked against `llama-server --help` and the tag-pinned server README
of build b11669 (v0.7.0): `/health` is public and answers 503 while the model loads.

Threads: llama.cpp counts the physical cores of the whole machine (the thread-sibling sets of
every CPU in sysfs) and ignores the CPU affinity mask and the cgroup CPU quota. In a container
limited to 8 of 32 CPUs it would run 16 threads on 8 CPUs, and its spinning thread barriers
make that several times slower. When something limits the process, `start` passes `--threads`
for the physical cores it may use (`usable_cores`), unless the recipe or the extra arguments
set the thread count.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from stepswitch import net, paths
from stepswitch.registry import ModelEntry
from stepswitch.serve import install
from stepswitch.serve.base import (
    LOOPBACK,
    Engine,
    EngineError,
    ServerInfo,
    error_text,
    free_port,
    http_client,
    process_start_time,
    same_process,
    spawn,
    terminate,
)

ENGINE = "llamacpp"
PURPOSE = "local-engine"
API_KEY_ENV = "LLAMA_API_KEY"

# Flags recipes and extra arguments may not set: the adapter owns binding, authentication, the
# model source and the alias; the rest would reach the network or the file system.
FORBIDDEN_FLAGS = frozenset(
    {
        # binding and authentication
        "--host",
        "--port",
        "--reuse-port",
        "--api-key",
        "--api-key-file",
        "--api-prefix",
        "--ssl-key-file",
        "--ssl-cert-file",
        # the RPC backend is never used
        "--rpc",
        # model source and downloads (the adapter passes a verified local file)
        "-m",
        "--model",
        "-mu",
        "--model-url",
        "-dr",
        "--docker-repo",
        "-hf",
        "-hfr",
        "--hf-repo",
        "-hff",
        "--hf-file",
        "-hft",
        "--hf-token",
        "-hfd",
        "-hfrd",
        "--hf-repo-draft",
        "--spec-draft-hf",
        "--models-dir",
        "--models-preset",
        # server-side tools and file access
        "--tools",
        "--tools-runtime",
        "-ag",
        "--agent",
        "--mcp-servers-config",
        "--mcp-servers-json",
        "--ui-mcp-proxy",
        "--webui-mcp-proxy",
        "--path",
        "--media-path",
        "--props",
        # set by the adapter
        "-a",
        "--alias",
        "--slot-save-path",
    }
)
CTX_FLAGS = ("-c", "--ctx-size")

_VERSION_PATTERNS = (
    # b11669: "version: 0.7.0-dev (build 11669, commit 620d2225c)"
    re.compile(
        r"version:\s*(?P<release>\S+)\s*\(build\s+(?P<build>\d+),\s*commit\s+(?P<commit>\w+)\)"
    ),
    # older builds: "version: 4589 (abc1234)"
    re.compile(r"version:\s*(?P<build>\d+)\s*\((?P<commit>\w+)\)"),
)
_SLOT_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_METRIC_RE = re.compile(r"^(?P<name>[A-Za-z_:][\w:]*(?:\{[^}]*\})?)\s+(?P<value>\S+)")
_BUILD_DIR_RE = re.compile(r"^b(\d+)")


@dataclass(frozen=True)
class LlamaVersion:
    """What `llama-server --version` reports."""

    build: int
    release: str | None = None
    commit: str | None = None

    @property
    def tag(self) -> str:
        return f"b{self.build}"


def parse_version(text: str) -> LlamaVersion | None:
    for pattern in _VERSION_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groupdict()
            return LlamaVersion(int(groups["build"]), groups.get("release"), groups.get("commit"))
    return None


def build_number(version: str | None) -> int | None:
    """11669 for "b11669" or "11669"; None when there is no build number."""
    match = re.fullmatch(r"b?(\d+)", (version or "").strip())
    return int(match.group(1)) if match else None


def meets_minimum(version: str | None, minimum: str | None) -> bool:
    """True unless both build numbers are known and `version` is older than `minimum`."""
    have, need = build_number(version), build_number(minimum)
    return have is None or need is None or have >= need


THREAD_FLAGS = frozenset({"-t", "--threads"})
SYSFS_CPU = Path("/sys/devices/system/cpu")
CGROUP_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")


def cgroup_cpus(cpu_max: Path = CGROUP_CPU_MAX) -> int | None:
    """The CPUs a cgroup v2 quota allows (`cpu.max` holds "<quota> <period>"), rounded up;
    None without a quota."""
    try:
        quota, _, period = cpu_max.read_text(encoding="ascii").strip().partition(" ")
    except OSError:
        return None
    if not (quota.isdigit() and period.isdigit()) or int(period) == 0:
        return None  # "max": no quota
    return max(1, math.ceil(int(quota) / int(period)))


def usable_cores(
    *,
    affinity: Sequence[int] | None = None,
    online: int | None = None,
    sysfs: Path = SYSFS_CPU,
    cpu_max: Path = CGROUP_CPU_MAX,
) -> int | None:
    """The physical cores this process may use, when something limits it to fewer CPUs than
    the machine has: a CPU affinity mask, or a cgroup CPU quota below its cores. None when
    nothing does, or where affinity is unknown (not Linux): llama.cpp's own count is right."""
    if affinity is None:
        if not hasattr(os, "sched_getaffinity"):
            return None
        affinity = sorted(os.sched_getaffinity(0))
    if not affinity:
        return None
    total = online if online is not None else (os.cpu_count() or len(affinity))
    siblings: set[str] = set()
    for cpu in affinity:
        try:
            topology = sysfs / f"cpu{cpu}" / "topology" / "thread_siblings"
            siblings.add(topology.read_text(encoding="ascii").strip())
        except OSError:
            siblings = set()  # topology unknown: one thread per allowed CPU
            break
    cores = len(siblings) or len(affinity)
    quota = cgroup_cpus(cpu_max)
    if len(affinity) >= total and (quota is None or quota >= cores):
        return None
    return max(1, min(cores, quota) if quota is not None else cores)


def sets_threads(args: Sequence[str]) -> bool:
    return any(arg.split("=", 1)[0] in THREAD_FLAGS for arg in args)


def check_args(args: Sequence[str], source: str) -> None:
    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag in FORBIDDEN_FLAGS:
            raise EngineError(f"{source} may not set {flag} (the server manager owns it)")


def with_ctx_size(args: Sequence[str], ctx_size: int) -> list[str]:
    """`args` with every context-size flag set to `ctx_size` (appended when absent)."""
    out: list[str] = []
    replaced = False
    items = list(args)
    index = 0
    while index < len(items):
        arg = items[index]
        flag, sep, _ = arg.partition("=")
        if flag in CTX_FLAGS and sep:
            out.append(f"{flag}={ctx_size}")
            replaced = True
        elif arg in CTX_FLAGS:
            out.extend([arg, str(ctx_size)])
            replaced = True
            index += 1  # skip the old value
        else:
            out.append(arg)
        index += 1
    return out if replaced else [*out, "--ctx-size", str(ctx_size)]


def server_env(api_key: str | None, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The child environment: inherited `LLAMA_ARG_*` settings dropped, the key added.

    llama-server reads any flag from a `LLAMA_ARG_*` variable when the flag is absent, so a
    stray variable could otherwise enable the RPC backend or a download.
    """
    source = os.environ if base is None else base
    env = {
        name: value
        for name, value in source.items()
        if not name.upper().startswith("LLAMA_ARG_") and name.upper() != API_KEY_ENV
    }
    if api_key:
        env[API_KEY_ENV] = api_key
    return env


def parse_metrics(text: str) -> dict[str, float]:
    """Prometheus text format to {sample name (with labels): value}."""
    values: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _METRIC_RE.match(line)
        if match is None:
            continue
        try:
            values[match["name"]] = float(match["value"])
        except ValueError:
            continue
    return values


def _server_names() -> tuple[str, ...]:
    return ("llama-server.exe", "llama-server") if sys.platform == "win32" else ("llama-server",)


def _server_in(directory: Path) -> Path | None:
    installed = install.installed_server(directory)
    if installed is not None:
        return installed
    for name in _server_names():
        for candidate in (directory / name, *sorted(directory.glob(f"*/{name}"))):
            if candidate.is_file():
                return candidate
    return None


def _installed_at(directory: Path) -> float:
    marker = install.read_marker(directory) or {}
    value = marker.get("installed_at")
    if isinstance(value, int | float):
        return float(value)
    try:
        return directory.stat().st_mtime
    except OSError:
        return 0.0


def find_binary(root: Path | None = None) -> Path | None:
    """The newest build under `<engines>/llamacpp/<build>/`, else `llama-server` on PATH.

    Builds are ordered by their build number (`b11669`), then by install time.
    """
    base = root if root is not None else paths.engines_dir() / "llamacpp"
    found: list[tuple[int, float, str, Path]] = []
    if base.is_dir():
        for directory in base.iterdir():
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            server = _server_in(directory)
            if server is None:
                continue
            match = _BUILD_DIR_RE.match(directory.name)
            number = int(match.group(1)) if match else -1
            found.append((number, _installed_at(directory), directory.name, server))
    if found:
        return max(found)[3]
    on_path = shutil.which("llama-server")
    return Path(on_path) if on_path else None


class LlamaCppEngine(Engine):
    """Starts and controls `llama-server` processes (one model per process)."""

    name = ENGINE
    self_managed = False

    def __init__(
        self,
        binary: Path | None = None,
        *,
        command_prefix: Sequence[str] = (),
        models_dir: Path | None = None,
        egress: net.Egress | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`command_prefix` replaces the binary lookup (tests run a stand-in server this way);
        an explicit `binary` is appended after it."""
        self.binary = binary
        self.command_prefix = [str(part) for part in command_prefix]
        self.models_dir = models_dir if models_dir is not None else paths.default_models_dir()
        self.egress = egress
        self.transport = transport
        self._versions: dict[tuple[str, ...], LlamaVersion | None] = {}

    # -- discovery ------------------------------------------------------------------------------

    def find_binary(self) -> Path | None:
        if self.binary is not None:
            return self.binary if self.binary.is_file() else None
        return find_binary()

    def launcher(self) -> list[str]:
        """The program part of the command line."""
        if self.command_prefix:
            return [*self.command_prefix, *([str(self.binary)] if self.binary else [])]
        binary = self.find_binary()
        if binary is None:
            raise EngineError(
                "llama-server is not installed; run `ssw models serve --yes <model>` "
                "to install the pinned llama.cpp build"
            )
        return [str(binary)]

    def available(self) -> bool:
        return bool(self.command_prefix) or self.find_binary() is not None

    def version_info(self) -> LlamaVersion | None:
        try:
            launcher = self.launcher()
        except EngineError:
            return None
        key = tuple(launcher)
        if key not in self._versions:
            self._versions[key] = self._run_version(launcher)
        return self._versions[key]

    def version(self) -> str | None:
        """The build tag, e.g. "b11669" (the form registry recipes use for `min_version`)."""
        info = self.version_info()
        return info.tag if info else None

    @staticmethod
    def _run_version(launcher: list[str]) -> LlamaVersion | None:
        try:
            if sys.platform == "win32":
                done = subprocess.run(
                    [*launcher, "--version"],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=60,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                done = subprocess.run(
                    [*launcher, "--version"],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=60,
                    check=False,
                )
        except (OSError, subprocess.SubprocessError):
            return None
        return parse_version(f"{done.stdout}\n{done.stderr}")

    # -- command line ---------------------------------------------------------------------------

    def command(
        self,
        entry: ModelEntry,
        model_path: Path,
        *,
        port: int,
        ctx_size: int | None = None,
        api_key: str | None = None,
        slot_save_path: Path | None = None,
        extra_args: Sequence[str] = (),
        threads: int | None = None,
    ) -> list[str]:
        """Launcher + recipe args (+ extra args) + the flags the adapter owns.

        `start` passes no `api_key` here: it hands the key over in the environment instead.
        `threads` becomes `--threads` unless the recipe or the extra arguments set it.
        """
        recipe = entry.engines.get(ENGINE)
        recipe_args = list(recipe.args) if recipe else []
        check_args(recipe_args, f"the llama.cpp recipe of {entry.id}")
        check_args(extra_args, "extra server arguments")
        args = [*recipe_args, *extra_args]
        if ctx_size is not None:
            args = with_ctx_size(args, ctx_size)
        if threads is not None and not sets_threads(args):
            args += ["--threads", str(threads)]
        cmd = [*self.launcher(), *args, "-m", str(model_path)]
        cmd += ["--host", LOOPBACK, "--port", str(port)]
        if api_key:
            cmd += ["--api-key", api_key]
        cmd.append("--metrics")
        if slot_save_path is not None:
            cmd += ["--slot-save-path", str(slot_save_path)]
        cmd += ["--alias", entry.id, "--offline"]
        return cmd

    # -- lifecycle ------------------------------------------------------------------------------

    def start(
        self,
        entry: ModelEntry,
        *,
        port: int | None = None,
        ctx_size: int | None = None,
        api_key: str | None = None,
        log_path: Path | None = None,
        extra_args: Sequence[str] = (),
        model_path: Path | None = None,
        slot_save_path: Path | None = None,
    ) -> ServerInfo:
        path = model_path if model_path is not None else entry.local_path(self.models_dir)
        if path is None or not path.is_file():
            raise EngineError(f"{entry.id}: model file not found ({path}); run `ssw models pull`")
        recipe = entry.engines.get(ENGINE)
        minimum = recipe.min_version if recipe else None
        found = self.version()
        if not meets_minimum(found, minimum):
            raise EngineError(f"{entry.id} needs llama.cpp {minimum} or newer; found {found}")
        port = port if port is not None else free_port()
        log = log_path or Path(tempfile.gettempdir()) / "stepswitch" / f"{entry.id}.log"
        if slot_save_path is not None:
            slot_save_path.mkdir(parents=True, exist_ok=True)
        recipe_args = list(recipe.args) if recipe else []
        cmd = self.command(
            entry,
            path,
            port=port,
            ctx_size=ctx_size,
            slot_save_path=slot_save_path,
            extra_args=extra_args,
            threads=None if sets_threads([*recipe_args, *extra_args]) else usable_cores(),
        )
        proc = spawn(cmd, log_path=log, env=server_env(api_key))
        now = time.time()
        return ServerInfo(
            model=entry.id,
            engine=self.name,
            base_url=f"http://{LOOPBACK}:{port}",
            port=port,
            pid=proc.pid,
            started=now,
            last_used=now,
            api_key=api_key,
            managed=True,
            ram_gb=entry.ram_gb,
            log_path=str(log),
            process_start=process_start_time(proc.pid),
        )

    def _client(self, timeout: float | httpx.Timeout) -> httpx.Client:
        return http_client(PURPOSE, egress=self.egress, transport=self.transport, timeout=timeout)

    def health(self, info: ServerInfo) -> bool:
        """GET /health: 200 when ready, 503 while the model loads (public endpoint)."""
        try:
            with self._client(2.0) as client:
                response = client.get(f"{info.base_url}/health", headers=info.headers())
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def alive(self, info: ServerInfo) -> bool:
        if info.pid is None:
            return self.health(info)
        return same_process(info.pid, info.process_start)

    def stop(self, info: ServerInfo, timeout: float = 10.0) -> None:
        if info.pid is None or not same_process(info.pid, info.process_start):
            return  # already gone, or the PID now belongs to another process
        terminate(info.pid, timeout=timeout)

    # -- optional capabilities ------------------------------------------------------------------

    def metrics(self, info: ServerInfo) -> dict[str, Any] | None:
        """The `/metrics` samples (needs `--metrics`, which the adapter always sets)."""
        try:
            with self._client(5.0) as client:
                response = client.get(f"{info.base_url}/metrics", headers=info.headers())
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        return dict(parse_metrics(response.text))

    def prefix_cache_stats(self, info: ServerInfo) -> dict[str, Any] | None:
        """None: `/metrics` has no prefix-cache counter in b11669; cache reuse is reported per
        response (`timings.cache_n`, `usage.prompt_tokens_details.cached_tokens`) and is
        aggregated from the ledger instead (ADR 0008)."""
        return None

    def slot_save(self, info: ServerInfo, slot_id: int, filename: str) -> dict[str, Any]:
        """POST /slots/{id}?action=save; the file lands in the server's `--slot-save-path`."""
        return self._slot_action(info, slot_id, "save", filename)

    def slot_restore(self, info: ServerInfo, slot_id: int, filename: str) -> dict[str, Any]:
        return self._slot_action(info, slot_id, "restore", filename)

    def _slot_action(
        self, info: ServerInfo, slot_id: int, action: str, filename: str
    ) -> dict[str, Any]:
        if not _SLOT_FILE_RE.match(filename) or ".." in filename:
            raise EngineError(f"slot files must be plain file names, got {filename!r}")
        try:
            with self._client(httpx.Timeout(10.0, read=600.0)) as client:
                response = client.post(
                    f"{info.base_url}/slots/{int(slot_id)}",
                    params={"action": action},
                    json={"filename": filename},
                    headers=info.headers(),
                )
        except httpx.HTTPError as exc:
            raise EngineError(f"slot {action} on {info.model} failed: {exc}") from exc
        if response.status_code != 200:
            raise EngineError(
                f"slot {action} on {info.model} failed: HTTP {response.status_code}: "
                f"{error_text(response)}"
            )
        data = response.json()
        return dict(data) if isinstance(data, dict) else {"result": data}
