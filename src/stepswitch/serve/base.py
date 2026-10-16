"""The engine interface and process helpers of the local model plane (SPEC §5.3, ADR 0008).

An engine adapter starts, checks and stops one model server. Engines that load and unload
models themselves (Ollama) set `self_managed`, so the server manager never evicts for them.
Every HTTP request goes through `stepswitch.net`; every server binds to the loopback address.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import httpx

from stepswitch import net
from stepswitch.registry import ModelEntry

LOOPBACK = "127.0.0.1"
LOG_MAX_BYTES = 5 * 1024 * 1024
SECRET_FLAGS = frozenset({"--api-key"})


class EngineError(RuntimeError):
    """An engine could not be installed, started, reached or stopped."""


@dataclass
class ServerInfo:
    """One running model server (or one model loaded into a self-managing engine)."""

    model: str
    engine: str
    base_url: str
    port: int | None
    pid: int | None
    started: float
    last_used: float
    api_key: str | None = field(default=None, repr=False)
    managed: bool = True
    ram_gb: float | None = None
    # Engine-side model name (an Ollama tag), the server log, and a process-start fingerprint
    # that tells a live server apart from an unrelated process that reused its PID.
    engine_model: str | None = None
    log_path: str | None = None
    process_start: str | None = None

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ServerInfo:
        """A record read back from the runtime file; TypeError when it is malformed."""
        names = {item.name for item in fields(cls)}
        info = cls(**{key: value for key, value in data.items() if key in names})
        texts = (info.model, info.engine, info.base_url)
        numbers = (info.started, info.last_used)
        optional_ints = (info.port, info.pid)
        if (
            not all(isinstance(value, str) for value in texts)
            or not all(isinstance(value, int | float) for value in numbers)
            or not all(value is None or type(value) is int for value in optional_ints)
            or not (info.api_key is None or isinstance(info.api_key, str))
        ):
            raise TypeError("malformed server record")
        return info


class Engine(ABC):
    """One engine adapter. Optional capabilities return None or raise NotImplementedError."""

    name: str = ""
    #: True for engines that load and unload models on their own (SPEC §5.3).
    self_managed: bool = False

    @abstractmethod
    def available(self) -> bool:
        """The engine is installed (or, for a service, reachable)."""

    @abstractmethod
    def version(self) -> str | None:
        """The engine version, or None when it cannot be determined."""

    @abstractmethod
    def start(
        self,
        entry: ModelEntry,
        *,
        port: int | None = None,
        ctx_size: int | None = None,
        api_key: str | None = None,
        log_path: Path | None = None,
        extra_args: Sequence[str] = (),
    ) -> ServerInfo:
        """Start serving `entry` and return at once; the caller waits for `health`."""

    @abstractmethod
    def health(self, info: ServerInfo) -> bool:
        """The server answers and is ready for requests."""

    @abstractmethod
    def stop(self, info: ServerInfo, timeout: float = 10.0) -> None:
        """Stop the server (or unload the model) described by `info`."""

    def alive(self, info: ServerInfo) -> bool:
        """The server still exists, ready or not (used to prune the runtime file)."""
        return self.health(info)

    def serve_problem(self, entry: ModelEntry) -> str | None:
        """Why this engine cannot serve `entry` on this host, or None when it can."""
        return None if self.available() else "not available"

    def metrics(self, info: ServerInfo) -> dict[str, Any] | None:
        return None

    def prefix_cache_stats(self, info: ServerInfo) -> dict[str, Any] | None:
        """Engine-level prefix-cache counters; None means "derive them from responses"."""
        return None

    def slot_save(self, info: ServerInfo, slot_id: int, filename: str) -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} has no KV slot save")

    def slot_restore(self, info: ServerInfo, slot_id: int, filename: str) -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} has no KV slot restore")


# -- HTTP ---------------------------------------------------------------------------------------


def http_client(
    purpose: str,
    *,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
    **kwargs: Any,
) -> httpx.Client:
    """An audited client from the given egress layer, or from the process-wide one."""
    return (egress or net.default()).client(purpose, transport=transport, **kwargs)


def error_text(response: httpx.Response) -> str:
    """The message of an error body in the shapes engines use, else the raw text."""
    try:
        data = response.json()
    except ValueError:
        return response.text.strip()[:500]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
    return str(data)[:500]


# -- processes ----------------------------------------------------------------------------------

# Children started by this Python process, so any manager instance can reap them.
_CHILDREN: dict[int, subprocess.Popen[bytes]] = {}

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # No console window, and a process group of its own so the terminal's Ctrl+C never reaches
    # the server. CREATE_NO_WINDOW instead of DETACHED_PROCESS: a detached process gives each
    # console child it spawns (a launcher's interpreter) a new, visible console window.
    CREATION_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    _SYNCHRONIZE = 0x00100000
    _QUERY_LIMITED = 0x1000
    _WAIT_TIMEOUT = 0x102
    _ACCESS_DENIED = 5
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.c_void_p] * 4))
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL

    def _win_alive(pid: int) -> bool:
        handle = _kernel32.OpenProcess(_SYNCHRONIZE | _QUERY_LIMITED, False, pid)
        if not handle:
            return bool(ctypes.get_last_error() == _ACCESS_DENIED)
        try:
            return bool(_kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT)
        finally:
            _kernel32.CloseHandle(handle)

    def _win_start_time(pid: int) -> str | None:
        handle = _kernel32.OpenProcess(_QUERY_LIMITED, False, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            created = times[0]
            return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            _kernel32.CloseHandle(handle)


def pid_alive(pid: int | None) -> bool:
    """True while the process exists (zombies of our own children count as gone)."""
    if pid is None or pid <= 0:
        return False
    child = _CHILDREN.get(pid)
    if child is not None and child.poll() is not None:
        _CHILDREN.pop(pid, None)
        return False
    if sys.platform == "win32":
        return _win_alive(pid)
    with contextlib.suppress(ChildProcessError, OSError):
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return _proc_state(pid) != "Z"


def _proc_state(pid: int) -> str | None:
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    fields_after_name = stat_line.rsplit(")", 1)[-1].split()
    return fields_after_name[0] if fields_after_name else None


def process_start_time(pid: int | None) -> str | None:
    """An opaque process-start fingerprint, or None where the OS does not offer one cheaply."""
    if pid is None or pid <= 0:
        return None
    if sys.platform == "win32":
        return _win_start_time(pid)
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        return stat_line.rsplit(")", 1)[-1].split()[19]
    except (OSError, IndexError):
        pass
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def same_process(pid: int | None, fingerprint: str | None) -> bool:
    """The PID is alive and, when a fingerprint was recorded, still the same process.

    A fingerprint that can no longer be read counts as a different process: a server we
    started always runs as our user, so we can read its start time, and a process we cannot
    identify must never be signalled.
    """
    if not pid_alive(pid):
        return False
    if fingerprint is None:
        return True
    return process_start_time(pid) == fingerprint


def spawn(
    cmd: Sequence[str],
    *,
    log_path: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start a server process detached from the terminal, with stdout and stderr to a log.

    POSIX: a new session (its own process group, no controlling terminal). Windows: a new
    process group and no console window. The process outlives the caller.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotate_log(log_path)
    args = [str(part) for part in cmd]
    with log_path.open("ab") as log:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        log.write(f"# {stamp} starting: {' '.join(redact(args))}\n".encode())
        log.flush()
        if sys.platform == "win32":
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=None if env is None else dict(env),
                creationflags=CREATION_FLAGS,
            )
        else:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=None if env is None else dict(env),
                start_new_session=True,
            )
    _CHILDREN[proc.pid] = proc
    return proc


def _signal(pid: int, sig: int) -> None:
    if sys.platform == "win32":
        # TerminateProcess: the server runs without a console, so there is no Ctrl+Break to
        # deliver; a gone process raises OSError, which means the work is done.
        with contextlib.suppress(OSError):
            os.kill(pid, sig)
        return
    with contextlib.suppress(ProcessLookupError):
        try:
            os.killpg(pid, sig)  # the server leads its own process group (start_new_session)
        except PermissionError:
            os.kill(pid, sig)


def _wait_gone(pid: int, timeout: float) -> bool:
    child = _CHILDREN.get(pid)
    if child is not None:
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        _CHILDREN.pop(pid, None)
        return True
    deadline = time.monotonic() + timeout
    while pid_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def terminate(pid: int, *, timeout: float = 10.0) -> None:
    """Graceful terminate, wait up to `timeout`, then kill (SPEC §5.3 stop semantics)."""
    if not pid_alive(pid):
        _CHILDREN.pop(pid, None)
        return
    _signal(pid, signal.SIGTERM)
    if _wait_gone(pid, timeout):
        return
    _signal(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    if not _wait_gone(pid, 5.0):
        raise EngineError(f"process {pid} did not exit")


def free_port(host: str = LOOPBACK) -> int:
    """A TCP port that is free on the loopback interface right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


# -- logs ---------------------------------------------------------------------------------------


def redact(args: Sequence[str]) -> list[str]:
    """The command line with secret flag values masked, for logs and messages."""
    shown: list[str] = []
    hide_next = False
    for arg in args:
        if hide_next:
            shown.append("***")
            hide_next = False
            continue
        flag, sep, _ = arg.partition("=")
        if flag in SECRET_FLAGS:
            if sep:
                shown.append(f"{flag}=***")
            else:
                shown.append(arg)
                hide_next = True
            continue
        shown.append(arg)
    return shown


def rotate_log(path: Path, max_bytes: int = LOG_MAX_BYTES) -> None:
    """Keep one previous log when the current one grows past `max_bytes`."""
    with contextlib.suppress(OSError):
        if path.stat().st_size > max_bytes:
            path.replace(path.with_name(path.name + ".1"))


def log_tail(path: Path | str | None, lines: int = 30, max_bytes: int = 16384) -> str:
    """The last lines of a server log, for error messages."""
    if path is None:
        return ""
    try:
        with Path(path).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])
