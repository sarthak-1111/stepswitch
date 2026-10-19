"""External harness adapters (SPEC §12 items 3-7, ADR 0029).

An adapter runs a third-party coding agent (a *harness*) on one task in one worktree, with every
model request of the harness going through `ssw proxy`, where the router picks the model per
request. The interface is SPEC §12's:

    handle = adapter.start(task, worktree, endpoint, settings)   # -> RunHandle
    for event in handle.events(): ...                            # HarnessEvent, as they arrive
    handle.cancel()                                              # stop the harness (any thread)
    result = handle.result()                                     # -> RunResult

* **Endpoint.** `HarnessEndpoint` is the proxy as one run sees it: its base URL, the run's own
  token, the session header value that binds requests to the task, the slot names the harness's
  model roles map to and the context window it may assume.
* **Environment.** The harness gets `context.proc.minimal_env` (plain variables only: no keys,
  tokens or proxy settings of the user), the adapter's variables pointing it at the proxy, the
  adapter's egress switches (`offline_env`: auto-update, telemetry, model-list fetch, version
  checks; set on every run, since Stepswitch sends no telemetry) and the configured extras.
  Configured extras may not name credentials: the harness authenticates to the proxy only.
* **Config.** Adapters never edit user files. Generated configuration goes into the run's state
  directory and reaches the harness through its own variables or flags.
* **Records.** Adapters with a line protocol on stdout parse it in `parse`/`finish`; the raw lines
  can be written to a transcript file (`HarnessSettings.transcript`), which is how the contract
  fixtures under `tests/fixtures/harness/` are recorded.

`ADAPTERS` maps adapter names to adapters (built on first use, since the adapter modules
import this one).
"""

from __future__ import annotations

import contextlib
import os
import queue
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import IO, Any, Literal

from stepswitch.context.deny import DenyList
from stepswitch.context.proc import SECRET_NAME_RE, kill_tree, minimal_env, popen_kwargs, which

Permission = Literal["edits", "all"]
RunStatus = Literal["done", "failed", "stopped"]

# Lines of stderr kept for the result's detail.
STDERR_TAIL = 40
# An absolute Windows path in backslashes (`shell_command`).
WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:\\")
# Seconds a cancelled harness gets to exit on its own before its process tree is killed.
CANCEL_GRACE_SECONDS = 5.0
VERSION_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class HarnessEndpoint:
    """The proxy as one harness run sees it."""

    base_url: str
    token: str
    # The `x-stepswitch-session` value: the requests of this run are steps of this task.
    session: str
    # Harness model roles -> model names the proxy reads as slot hints (`proxy.slots`).
    slots: Mapping[str, str] = field(default_factory=dict)
    # The context window the harness may assume, in tokens (None: the harness's default).
    context: int | None = None
    # The workers the proxy may route to -> their context windows, for harnesses that list
    # models one by one (Pi's routed virtual model resolves to one of them per request).
    workers: Mapping[str, int | None] = field(default_factory=dict)

    def slot(self, role: str, default: str = "ssw:auto") -> str:
        return self.slots.get(role) or self.slots.get("main") or default


@dataclass
class HarnessSettings:
    """How one harness runs: from `[harness.<name>]` in the config plus the task's options."""

    # The harness command, replacing the adapter's default (`[harness.<name>] command`).
    command: Sequence[str] | None = None
    offline: bool = False
    # "edits": file edits inside the worktree and the test command; "all": every tool without
    # asking (for sandboxed or containerized runs only).
    permission: Permission = "edits"
    max_turns: int | None = None
    timeout_seconds: float | None = None
    extra_args: Sequence[str] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    # The project's test command, which the harness may run under the "edits" permission.
    test_command: Sequence[str] | None = None
    # `privacy.deny_paths`: paths no harness may read or change, whatever the permission
    # (`context.deny`).
    deny_paths: Sequence[str] = ()
    # Where the adapter keeps generated config and isolated harness state for this task.
    state_dir: Path | None = None
    # Files for the raw stdout lines and for stderr (None: not kept).
    transcript: Path | None = None
    log: Path | None = None

    def merged_env(self) -> dict[str, str]:
        """The configured extras, without anything that looks like a credential."""
        found = [name for name in self.env if SECRET_NAME_RE.search(name)]
        if found:
            raise ValueError(
                "harness env may not set credentials (" + ", ".join(sorted(found)) + "): the"
                " harness authenticates to ssw proxy with the run's own token"
            )
        return {str(k): str(v) for k, v in self.env.items()}


@dataclass(frozen=True)
class HarnessTask:
    """What one harness run is asked to do."""

    prompt: str
    instruction: str = ""
    # The harness's own session to continue (adapters with `resumable`), from a previous
    # `RunResult.session_id`.
    resume: str | None = None
    attempt: int = 1


@dataclass
class HarnessEvent:
    """One thing a harness reported. `kind` is one of EVENT_KINDS."""

    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.data}


EVENT_KINDS = (
    "start",  # the harness is up: session ID, model, version
    "text",  # assistant text
    "thinking",  # reasoning the harness shows
    "tool_call",  # the harness called one of its tools
    "tool_result",  # what the tool returned
    "permission",  # a permission request and how Stepswitch answered it
    "plan",  # the harness's plan or todo list
    "usage",  # token counts the harness reported
    "compaction",  # the harness compacted its transcript
    "result",  # the harness's final report
    "error",  # an error the harness reported
    "log",  # anything else (unparsed lines, notices)
)


@dataclass
class RunResult:
    status: RunStatus
    stop_reason: str | None = None
    summary: str = ""
    session_id: str | None = None
    turns: int = 0
    tool_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    exit_code: int | None = None
    detail: str = ""
    version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stop_reason": self.stop_reason,
            "summary": self.summary[:2000],
            "session_id": self.session_id,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "usage": dict(self.usage),
            "exit_code": self.exit_code,
            "detail": self.detail[:2000],
            "version": self.version,
        }


class RunHandle:
    """A running harness (SPEC §12): `events()` once, `cancel()` from any thread, `result()`."""

    def events(self) -> Iterator[HarnessEvent]:
        raise NotImplementedError

    def cancel(self) -> None:
        raise NotImplementedError

    def result(self, timeout: float | None = None) -> RunResult:
        raise NotImplementedError


@dataclass
class ParseState:
    """What a line-protocol adapter has seen so far (read by its `finish`)."""

    session_id: str | None = None
    model: str | None = None
    version: str | None = None
    turns: int = 0
    tool_calls: int = 0
    texts: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    final: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def add_usage(self, **counts: int | None) -> None:
        for key, value in counts.items():
            if isinstance(value, int) and not isinstance(value, bool):
                self.usage[key] = self.usage.get(key, 0) + value


class HarnessAdapter:
    """One harness. Subclasses set the class attributes and implement `command`, `environment`
    and the line protocol (`parse`, `finish`), or override `start` (the ACP adapters)."""

    name: str = ""
    title: str = ""
    # The executable looked up on PATH when no command is configured.
    binary: str = ""
    # How the harness is supported (shown by `ssw doctor` and the docs).
    support: str = ""
    # Egress switches of the harness (auto-update, telemetry, model lists, version checks).
    offline_env: Mapping[str, str] = {}
    # The harness can continue a previous run's session (`HarnessTask.resume`).
    resumable: bool = False
    # The task prompt goes to the harness on stdin (never as an argument: a Windows batch shim
    # would reparse it).
    prompt_on_stdin: bool = True

    # -- discovery ---------------------------------------------------------------------------

    def executable(self, settings: HarnessSettings) -> list[str] | None:
        """The harness command: the configured one, else `binary` from PATH."""
        if settings.command:
            return [str(part) for part in settings.command]
        found = which(self.binary) if self.binary else None
        return [found] if found else None

    def version(self, settings: HarnessSettings | None = None) -> str | None:
        """The installed version (`<command> --version`), or None when not installed."""
        command = self.executable(settings or HarnessSettings())
        if command is None:
            return None
        try:
            out = subprocess.run(
                [*command, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=VERSION_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
                env=minimal_env(),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        text = (out.stdout or out.stderr).strip().splitlines()
        return text[0].strip() if text else None

    # -- one run -----------------------------------------------------------------------------

    def command(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> list[str]:
        raise NotImplementedError

    def environment(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> dict[str, str]:
        """The harness's own variables (pointing it at the proxy)."""
        return {}

    def full_environment(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
        *,
        base: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        env = minimal_env(offline=settings.offline, base=base)
        env.update(self.environment(task, workdir, endpoint, settings))
        env.update(self.offline_env)
        env.update(settings.merged_env())
        return env

    def stdin_text(self, task: HarnessTask) -> str | None:
        return task.prompt if self.prompt_on_stdin else None

    def new_state(self) -> ParseState:
        return ParseState()

    def parse(self, line: str, state: ParseState) -> list[HarnessEvent]:
        raise NotImplementedError

    def finish(
        self, state: ParseState, exit_code: int | None, stderr: str, *, cancelled: bool
    ) -> RunResult:
        raise NotImplementedError

    def start(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> RunHandle:
        command = self.executable(settings)
        if command is None:
            raise HarnessMissing(self)
        argv = [*command, *self.command(task, workdir, endpoint, settings)]
        env = self.full_environment(task, workdir, endpoint, settings)
        return SubprocessRun(self, argv, env, workdir, self.stdin_text(task), settings)


class HarnessMissing(RuntimeError):
    def __init__(self, adapter: HarnessAdapter) -> None:
        title = adapter.title or adapter.name
        if adapter.binary:
            message = (
                f"{title} is not installed: `{adapter.binary}` is not on PATH (or set"
                f" [harness.{adapter.name}] command)"
            )
        else:
            message = f"{title}: set [harness.{adapter.name}] command to the agent's command"
        super().__init__(message)


SETTING_KEYS = frozenset(
    {"command", "permission", "max_turns", "timeout_seconds", "args", "env", "slots", "keep_state"}
)


def harness_settings(config: Any, name: str) -> tuple[HarnessSettings, dict[str, str], bool]:
    """`[harness.<name>]` of the config: the run settings, the slot overrides (harness role ->
    proxy model name) and whether to keep the harness's state directory after the run.

        [harness.claude-code]
        command = ["claude"]            # default: the harness's command from PATH
        permission = "edits"            # or "all" (sandboxed runs only)
        max_turns = 60
        timeout_seconds = 1800
        args = []                       # extra arguments
        env = { }                       # extra variables (never credentials)
        slots = { plan = "ssw:plan" }   # harness role -> proxy model name
        keep_state = false
    """
    extra = getattr(config, "model_extra", None) or {}
    table = (extra.get("harness") or {}).get(name) or {}
    if not isinstance(table, dict):
        raise ValueError(f"[harness.{name}] must be a table")
    unknown = sorted(set(table) - SETTING_KEYS)
    if unknown:
        raise ValueError(f"[harness.{name}] has unknown keys: {', '.join(unknown)}")
    command = table.get("command")
    if isinstance(command, str):
        command = [command]
    if command is not None and not (
        isinstance(command, list) and command and all(isinstance(p, str) for p in command)
    ):
        raise ValueError(f"[harness.{name}] command is a list of strings")
    permission = table.get("permission", "edits")
    if permission not in ("edits", "all"):
        raise ValueError(f"[harness.{name}] permission is 'edits' or 'all'")
    env = table.get("env") or {}
    slots = table.get("slots") or {}
    args = table.get("args") or []
    for key, value in (("env", env), ("slots", slots)):
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise ValueError(f"[harness.{name}.{key}] maps names to strings")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError(f"[harness.{name}] args is a list of strings")
    max_turns = table.get("max_turns")
    timeout = table.get("timeout_seconds")
    settings = HarnessSettings(
        command=command,
        permission=permission,
        max_turns=int(max_turns) if max_turns is not None else None,
        timeout_seconds=float(timeout) if timeout is not None else None,
        extra_args=list(args),
        env={str(k): v for k, v in env.items()},
        deny_paths=tuple(DenyList.from_config(config).patterns),
    )
    settings.merged_env()  # refuses credentials now rather than at the first run
    return settings, {str(k): v for k, v in slots.items()}, bool(table.get("keep_state"))


_END = object()


class SubprocessRun(RunHandle):
    """A harness that reports on stdout, one JSON value per line (records split on LF only)."""

    def __init__(
        self,
        adapter: HarnessAdapter,
        argv: Sequence[str],
        env: Mapping[str, str],
        workdir: Path,
        stdin_text: str | None,
        settings: HarnessSettings,
    ) -> None:
        self.adapter = adapter
        self.argv = list(argv)
        self.settings = settings
        self.state = adapter.new_state()
        self._queue: queue.Queue[Any] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=STDERR_TAIL)
        self._cancelled = threading.Event()
        self._result: RunResult | None = None
        self._lock = threading.Lock()
        self.started = time.monotonic()
        self.proc = subprocess.Popen(
            self.argv,
            cwd=str(workdir),
            env=dict(env),
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **popen_kwargs(),
        )
        self._threads = [
            threading.Thread(target=self._read_stdout, daemon=True, name="harness-stdout"),
            threading.Thread(target=self._read_stderr, daemon=True, name="harness-stderr"),
        ]
        for thread in self._threads:
            thread.start()
        if stdin_text is not None:
            threading.Thread(target=self._write_stdin, args=(stdin_text,), daemon=True).start()
        self._timer: threading.Timer | None = None
        if settings.timeout_seconds:
            self._timer = threading.Timer(settings.timeout_seconds, self._time_out)
            self._timer.daemon = True
            self._timer.start()
        self.timed_out = False

    def _write_stdin(self, text: str) -> None:
        stdin = self.proc.stdin
        if stdin is None:
            return
        with contextlib.suppress(OSError, ValueError):
            stdin.write(text.encode("utf-8"))
            stdin.flush()
        with contextlib.suppress(OSError, ValueError):
            stdin.close()

    def _read_stdout(self) -> None:
        transcript: IO[str] | None = None
        try:
            if self.settings.transcript is not None:
                self.settings.transcript.parent.mkdir(parents=True, exist_ok=True)
                transcript = self.settings.transcript.open("w", encoding="utf-8", newline="\n")
            assert self.proc.stdout is not None
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.rstrip(b"\n").rstrip(b"\r").decode("utf-8", errors="replace")
                if not line.strip():
                    continue
                if transcript is not None:
                    transcript.write(line + "\n")
                    transcript.flush()
                try:
                    events = self.adapter.parse(line, self.state)
                except Exception as exc:  # noqa: BLE001 - one bad line never ends the run
                    events = [HarnessEvent("log", {"unparsed": line[:500], "error": str(exc)})]
                for event in events:
                    self._queue.put(event)
        finally:
            if transcript is not None:
                transcript.close()
            self._queue.put(_END)

    def _read_stderr(self) -> None:
        log: IO[str] | None = None
        try:
            if self.settings.log is not None:
                self.settings.log.parent.mkdir(parents=True, exist_ok=True)
                log = self.settings.log.open("a", encoding="utf-8", newline="\n")
            assert self.proc.stderr is not None
            for raw in iter(self.proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                self._stderr.append(line)
                if log is not None:
                    log.write(line + "\n")
                    log.flush()
        finally:
            if log is not None:
                log.close()

    def _time_out(self) -> None:
        self.timed_out = True
        self.cancel()

    def events(self) -> Iterator[HarnessEvent]:
        while True:
            item = self._queue.get()
            if item is _END:
                return
            yield item

    def cancel(self) -> None:
        if self._cancelled.is_set() or self.proc.poll() is not None:
            self._cancelled.set()
            return
        self._cancelled.set()
        kill_tree(self.proc)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def result(self, timeout: float | None = None) -> RunResult:
        with self._lock:
            if self._result is not None:
                return self._result
            try:
                code = self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_tree(self.proc)
                code = self.proc.wait(timeout=CANCEL_GRACE_SECONDS)
            for thread in self._threads:
                thread.join(timeout=CANCEL_GRACE_SECONDS)
            if self._timer is not None:
                self._timer.cancel()
            result = self.adapter.finish(
                self.state, code, "\n".join(self._stderr), cancelled=self.cancelled
            )
            if self.timed_out and result.status != "done":
                result.status, result.stop_reason = "stopped", "budget_wall_seconds"
                result.detail = (
                    f"timed out after {self.settings.timeout_seconds:g} s; " + result.detail
                ).strip("; ")
            self._result = result
            return result


def model_id(slot: str) -> str:
    """A slot name without ':' (the proxy accepts both forms), for model selectors of the form
    `provider/model:thinking`."""
    return slot.replace(":", "-")


def proxy_headers(endpoint: HarnessEndpoint) -> dict[str, str]:
    """Headers every adapter has its harness send to the proxy."""
    return {"x-stepswitch-session": endpoint.session}


def shell_command(parts: Sequence[str]) -> str:
    """A command as a model types it into a harness's shell tool.

    Windows paths are written with forward slashes, which every Windows shell accepts. The
    harnesses' shell tools are POSIX shells, which read a backslash as an escape, so a model
    rewrites `C:\\venv\\python.exe` as `C:/venv/python.exe`. Parts are quoted for a POSIX shell.
    """
    return shlex.join(
        PureWindowsPath(part).as_posix() if WINDOWS_PATH_RE.match(part) else part
        for part in map(str, parts)
    )


def allowed_test_prefixes(settings: HarnessSettings) -> list[str]:
    """The test command as harnesses that allow commands by prefix match it: as the prompt
    shows it (`shell_command`) and, when that differs, as written."""
    if not settings.test_command:
        return []
    shown = shell_command(settings.test_command)
    written = " ".join(str(part) for part in settings.test_command)
    return [shown] if written == shown else [shown, written]


def tail(text: str, limit: int = 1200) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


# ----------------------------------------------------------------------------------------
# The registry
# ----------------------------------------------------------------------------------------

_ADAPTERS: dict[str, HarnessAdapter] | None = None


def adapters() -> dict[str, HarnessAdapter]:
    global _ADAPTERS
    if _ADAPTERS is None:
        from stepswitch.workers.acp import AcpAdapter
        from stepswitch.workers.claude_code import ClaudeCodeAdapter
        from stepswitch.workers.kimi_code import KimiCodeAdapter
        from stepswitch.workers.omp import OhMyPiAdapter
        from stepswitch.workers.opencode import OpenCodeAdapter
        from stepswitch.workers.pi import PiAdapter

        found: list[HarnessAdapter] = [
            ClaudeCodeAdapter(),
            PiAdapter(),
            OhMyPiAdapter(),
            OpenCodeAdapter(),
            KimiCodeAdapter(),
            AcpAdapter(),
        ]
        _ADAPTERS = {adapter.name: adapter for adapter in found}
    return _ADAPTERS


def get_adapter(name: str) -> HarnessAdapter:
    found = adapters().get(name)
    if found is None:
        raise ValueError(f"unknown harness {name!r} (harnesses: {', '.join(sorted(adapters()))})")
    return found


def __getattr__(name: str) -> Any:
    # `ADAPTERS`, built on first use: the adapter modules import this one.
    if name == "ADAPTERS":
        return adapters()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def environ_names(env: Mapping[str, str]) -> list[str]:
    """Variable names only, for records (values may hold the run's proxy token)."""
    return sorted(env)


def path_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def os_name() -> str:
    return os.name
