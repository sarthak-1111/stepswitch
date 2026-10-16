"""Subprocess helpers shared by the context services, the toolbox and the worktree pool.

* `which` searches only the absolute entries of PATH. On Windows `shutil.which` looks in the
  current directory first, and a repository under work may hold any file, so the current
  directory and relative PATH entries are never searched (same rule as `doctor._which`).
* `minimal_env` builds the environment for commands the agent runs: an allowlist of plain
  variables (never anything that looks like a secret) plus explicit overrides.
* `run_capture` runs a program without a shell, kills the whole process tree on timeout and
  keeps the head and tail of large outputs.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

# Variables passed through from the parent environment. Everything else is dropped, so keys,
# tokens and proxy settings never reach commands the model chose.
PASS_THROUGH_POSIX = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TZ",
)
PASS_THROUGH_WINDOWS = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "COMMONPROGRAMFILES",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "USERNAME",
    "LANG",
)
SECRET_NAME_RE = re.compile(
    r"(?i)(key|token|secret|passw|credential|auth|cookie|session|private|signature)"
)

# Fixed settings for every command: UTF-8 output from Python children and no colour codes.
QUIET_ENV = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "NO_COLOR": "1",
    "PY_COLORS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}

# Offline switches of common tools, set for commands run while offline. Best effort: the
# enforcement for child processes is OS-level isolation (ARCHITECTURE §13, ADR 0011).
OFFLINE_ENV = {
    "UV_OFFLINE": "1",
    "PIP_NO_INDEX": "1",
    "HF_HUB_OFFLINE": "1",
    "CARGO_NET_OFFLINE": "true",
    "GOPROXY": "off",
    "npm_config_offline": "true",
}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def which(name: str, path: str | None = None) -> str | None:
    """The program `name` from the absolute entries of `path` (default: this process's PATH).

    A name with a directory component is not searched for; callers resolve those themselves.
    On Windows the PATHEXT extensions are tried, as `shutil.which` does.
    """
    if not name or Path(name).name != name:
        return None
    search = os.environ.get("PATH", "") if path is None else path
    for entry in search.split(os.pathsep):
        directory = entry.strip().strip('"')
        if directory and Path(directory).is_absolute():
            found = shutil.which(str(Path(directory) / name))
            if found:
                return found
    return None


def absolute_path_entries(path: str) -> str:
    """PATH with relative and empty entries removed (they would search the current dir)."""
    entries = [e for e in path.split(os.pathsep) if e.strip() and Path(e.strip('"')).is_absolute()]
    return os.pathsep.join(entries)


def minimal_env(
    overrides: Mapping[str, str] | None = None,
    *,
    offline: bool = False,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The environment for agent-run commands (see the module docstring)."""
    source = os.environ if base is None else base
    names = PASS_THROUGH_WINDOWS if sys.platform == "win32" else PASS_THROUGH_POSIX
    env: dict[str, str] = {}
    for name in names:
        value = source.get(name)
        if value is not None and not SECRET_NAME_RE.search(name):
            env[name] = value
    if "PATH" in env:
        env["PATH"] = absolute_path_entries(env["PATH"])
    env.update(QUIET_ENV)
    if offline:
        env.update(OFFLINE_ENV)
    env.update(overrides or {})
    return env


def popen_kwargs() -> dict[str, Any]:
    """Start children in their own process group so a timeout can stop the whole tree."""
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return {"creationflags": flags}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen[Any]) -> None:
    """Kill a child and its descendants (POSIX: its process group; Windows: taskkill /T)."""
    if sys.platform == "win32":
        taskkill = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
        if proc.poll() is None and taskkill.is_file():
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    [str(taskkill), "/F", "/T", "/PID", str(proc.pid)],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=15,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
    else:
        import signal

        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


@dataclass
class ProcResult:
    """The outcome of `run_capture`."""

    args: Sequence[str] | str
    exit_code: int | None
    output: str
    timed_out: bool = False
    duration_s: float = 0.0
    omitted_bytes: int = 0
    error: str | None = None  # the program could not be started
    extra: dict[str, Any] = field(default_factory=dict)


class _Capture(threading.Thread):
    """Reads a pipe to EOF, keeping the first and last `limit / 2` bytes."""

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._half = max(1, limit // 2)
        self.head = bytearray()
        self.tail: deque[bytes] = deque()
        self._tail_size = 0
        self.omitted = 0

    def run(self) -> None:
        read = getattr(self._stream, "read1", self._stream.read)
        try:
            while True:
                chunk = read(65536)
                if not chunk:
                    break
                room = self._half - len(self.head)
                if room > 0:
                    self.head += chunk[:room]
                    chunk = chunk[room:]
                if chunk:
                    self.tail.append(chunk)
                    self._tail_size += len(chunk)
                    while self._tail_size - len(self.tail[0]) >= self._half:
                        dropped = self.tail.popleft()
                        self._tail_size -= len(dropped)
                        self.omitted += len(dropped)
        except (OSError, ValueError):
            pass

    def text(self) -> str:
        tail = b"".join(self.tail)
        if self.omitted:
            middle = f"\n[... {self.omitted} bytes of output omitted ...]\n".encode()
            data = bytes(self.head) + middle + tail
        else:
            data = bytes(self.head) + tail
        return ANSI_RE.sub("", data.decode("utf-8", errors="replace")).replace("\r\n", "\n")


def run_capture(
    args: Sequence[str] | str,
    *,
    cwd: Path,
    env: Mapping[str, str] | None,
    timeout: float,
    executable: str | None = None,
    max_bytes: int = 1 << 20,
) -> ProcResult:
    """Run without a shell; stdout and stderr merged; the process tree is killed on timeout."""
    started = time.perf_counter()
    try:
        proc = subprocess.Popen(
            args if isinstance(args, str) else list(args),
            cwd=cwd,
            env=None if env is None else dict(env),
            executable=executable,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **popen_kwargs(),
        )
    except (OSError, ValueError) as exc:
        return ProcResult(args, None, "", error=f"{type(exc).__name__}: {exc}")
    assert proc.stdout is not None
    capture = _Capture(proc.stdout, max_bytes)
    capture.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
    capture.join(timeout=10)
    with contextlib.suppress(OSError):
        proc.stdout.close()
    return ProcResult(
        args,
        proc.returncode,
        capture.text(),
        timed_out=timed_out,
        duration_s=time.perf_counter() - started,
        omitted_bytes=capture.omitted,
    )
