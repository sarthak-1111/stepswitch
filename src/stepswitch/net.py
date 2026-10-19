"""The single egress choke point (SPEC §15, working rule 6).

Every HTTP request Stepswitch makes goes through a client from this module. The client's
transport checks the destination against the egress policy (offline mode allows only local
addresses) and records an audit row for every request, allowed or blocked, with the bytes sent
and received (streams included; the row is written when the response body closes).

Spawned processes are audited too (`audit_processes`): in-process observation cannot see their
connections, so the log says which programs ran, for which task, with which arguments.
"""

from __future__ import annotations

import contextlib
import contextvars
import ipaddress
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
# The task the current thread works for: the audit task ID of requests whose client was made
# without one, and of the processes it spawns (set by the orchestrator per task and candidate).
CURRENT_TASK: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "stepswitch_task", default=None
)


class EgressBlocked(httpx.TransportError):
    """Raised when the egress policy refuses a destination."""


def is_local_host(host: str) -> bool:
    """True for loopback addresses (127.0.0.0/8, ::1) and the localhost names."""
    host = host.strip("[]").lower()
    if host in LOCAL_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_local_url(url: str) -> bool:
    try:
        parsed = httpx.URL(url)
    except Exception:  # noqa: BLE001 - an unparsable URL is not local
        return False
    if parsed.scheme in {"unix", "http+unix"}:
        return True
    return is_local_host(parsed.host or "")


@dataclass
class AuditRow:
    """One request seen by the egress layer (SPEC §15 egress audit log)."""

    ts: float
    method: str
    scheme: str
    host: str
    port: int | None
    path: str
    purpose: str
    task_id: str | None
    allowed: bool
    local: bool
    status: int | None = None
    bytes_out: int = 0
    bytes_in: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


AuditSink = Callable[[AuditRow], None]


@dataclass
class EgressPolicy:
    offline: bool = False
    # Hosts allowed in addition to local ones while offline (never used by default).
    allow_hosts: set[str] = field(default_factory=set)

    def permits(self, host: str) -> bool:
        if is_local_host(host):
            return True
        return not self.offline or host.lower() in self.allow_hosts


class _AuditedTransport(httpx.BaseTransport):
    def __init__(
        self,
        inner: httpx.BaseTransport,
        policy: EgressPolicy,
        sink: AuditSink,
        purpose: str,
        task_id: str | None,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._sink = sink
        self._purpose = purpose
        self._task_id = task_id

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host = url.host or ""
        local = url.scheme in {"unix", "http+unix"} or is_local_host(host)
        row = AuditRow(
            ts=time.time(),
            method=request.method,
            scheme=url.scheme,
            host=host,
            port=url.port,
            path=url.path,
            purpose=self._purpose,
            task_id=self._task_id or CURRENT_TASK.get(),
            allowed=local or self._policy.permits(host),
            local=local,
        )
        if not row.allowed:
            row.error = "blocked by offline mode"
            self._sink(row)
            raise EgressBlocked(f"offline mode blocks {url.scheme}://{host}", request=request)
        sent = _count_request(request)
        try:
            response = self._inner.handle_request(request)
        except Exception as exc:
            row.bytes_out = sent()
            row.error = f"{type(exc).__name__}: {exc}"[:300]
            self._sink(row)
            raise
        # The request body is on the wire once the response starts. The row is written when the
        # response body is closed, with the bytes that came back (streams included).
        row.bytes_out = sent()
        row.status = response.status_code

        if response.is_closed:
            # Read before it reached this layer (in-memory transports): nothing will stream.
            row.bytes_in = len(response.content)
            self._sink(row)
            return response

        def done(received: int, error: str | None) -> None:
            row.bytes_in = received
            row.error = error
            self._sink(row)

        response.stream = _CountingStream(response.stream, done)
        return response

    def close(self) -> None:
        self._inner.close()


def _count_request(request: httpx.Request) -> Callable[[], int]:
    """How many body bytes the request sent: its length when read, else what was streamed."""
    try:
        size = len(request.content)
    except httpx.RequestNotRead:
        counted = _CountingStream(request.stream, lambda _n, _e: None)
        request.stream = counted
        return lambda: counted.count
    return lambda: size


class _CountingStream(httpx.SyncByteStream):
    """A byte stream that counts what passes through and reports once, when closed."""

    def __init__(self, inner: Any, on_close: Callable[[int, str | None], None]) -> None:
        self._inner = inner
        self._on_close = on_close
        self._error: str | None = None
        self._closed = False
        self.count = 0

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self._inner:
                self.count += len(chunk)
                yield chunk
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"[:300]
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            closer = getattr(self._inner, "close", None)
            if callable(closer):
                closer()
        finally:
            self._on_close(self.count, self._error)


class MemorySink:
    """Keeps the most recent audit rows in memory; the default sink."""

    def __init__(self, maxlen: int = 10_000) -> None:
        self.rows: deque[AuditRow] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def __call__(self, row: AuditRow) -> None:
        with self._lock:
            self.rows.append(row)

    def __iter__(self) -> Iterator[AuditRow]:
        with self._lock:
            return iter(list(self.rows))


class Egress:
    """Factory for policy-enforcing, audited HTTP clients."""

    def __init__(self, policy: EgressPolicy | None = None, sinks: list[AuditSink] | None = None):
        self.policy = policy or EgressPolicy()
        self.memory = MemorySink()
        self.sinks: list[AuditSink] = [self.memory, *(sinks or [])]

    def add_sink(self, sink: AuditSink) -> None:
        self.sinks.append(sink)

    def child(self, *, offline: bool = False) -> Egress:
        """An egress layer of its own for one task: the same audit sinks, and a policy that
        starts as this one's (offline if either is) and can be made stricter without touching
        the process-wide layer. Clients made from it check the policy on every request, so
        `child.policy.offline = True` binds the clients already handed out."""
        policy = EgressPolicy(
            offline=self.policy.offline or offline, allow_hosts=set(self.policy.allow_hosts)
        )
        return Egress(policy, sinks=[self._emit])

    def _emit(self, row: AuditRow) -> None:
        for sink in self.sinks:
            with contextlib.suppress(Exception):  # auditing must never break a request
                sink(row)

    def client(
        self,
        purpose: str,
        *,
        base_url: str = "",
        headers: dict[str, str] | None = None,
        timeout: float | httpx.Timeout = 60.0,
        transport: httpx.BaseTransport | None = None,
        task_id: str | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Client:
        inner = transport or httpx.HTTPTransport(retries=0)
        audited = _AuditedTransport(inner, self.policy, self._emit, purpose, task_id)
        return httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            transport=audited,
            follow_redirects=follow_redirects,
            # No proxies or .netrc credentials from the environment: requests go exactly where
            # the policy allowed them, with only the credentials Stepswitch was given. (httpx
            # ignores proxy variables anyway whenever a transport is passed, as it is here.)
            trust_env=False,
        )


_default = Egress()
_default_lock = threading.Lock()


def default() -> Egress:
    return _default


def configure(*, offline: bool | None = None, sink: AuditSink | None = None) -> Egress:
    """Adjust the process-wide egress policy (called by the CLI from the loaded config)."""
    with _default_lock:
        if offline is not None:
            _default.policy.offline = offline
        if sink is not None:
            _default.add_sink(sink)
    return _default


def client(purpose: str, **kwargs: Any) -> httpx.Client:
    """An audited client from the process-wide egress policy."""
    return _default.client(purpose, **kwargs)


# --------------------------------------------------------------------------------------------
# Spawned processes (SPEC §15: in-process observation cannot see their connections)
# --------------------------------------------------------------------------------------------


@dataclass
class ProcessRow:
    """One process started by Stepswitch or a library it calls."""

    ts: float
    executable: str
    argv: list[str]
    cwd: str | None
    pid: int | None
    task_id: str | None
    # Whether the process-wide policy was offline when it started (OS-level isolation is the
    # enforcement layer for child processes; adapters also set their egress switches).
    offline: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


ProcessSink = Callable[[ProcessRow], None]
ARGV_LIMIT = 24
ARG_CHARS = 300


class _Processes:
    def __init__(self) -> None:
        self.memory: deque[ProcessRow] = deque(maxlen=2_000)
        self.sinks: list[ProcessSink] = []
        self.lock = threading.Lock()
        self.installed = False

    def emit(self, row: ProcessRow) -> None:
        with self.lock:
            self.memory.append(row)
            sinks = list(self.sinks)
        for sink in sinks:
            with contextlib.suppress(Exception):  # auditing must never break a launch
                sink(row)


_processes = _Processes()


def _argv(args: Any) -> list[str]:
    if isinstance(args, (str, bytes, os.PathLike)):
        return [os.fsdecode(args)]
    return [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in args]


def record_process(
    args: Any, *, cwd: Any = None, pid: int | None = None, executable: Any = None
) -> ProcessRow:
    """Record one started process (secrets in its command line are redacted)."""
    from stepswitch import redact

    argv = _argv(args)
    shown = [a[:ARG_CHARS] for a in redact.redact_argv(argv[:ARGV_LIMIT])]
    if len(argv) > ARGV_LIMIT:
        shown.append(f"... ({len(argv) - ARGV_LIMIT} more)")
    program = os.fsdecode(executable) if executable is not None else (argv[0] if argv else "")
    row = ProcessRow(
        ts=time.time(),
        executable=Path(program).name if program else "",
        argv=shown,
        cwd=os.fsdecode(cwd) if cwd is not None else None,
        pid=pid,
        task_id=CURRENT_TASK.get(),
        offline=_default.policy.offline,
    )
    _processes.emit(row)
    return row


def audit_processes(sink: ProcessSink | None = None) -> None:
    """Record every process started through `subprocess` from now on (idempotent), and send
    the rows to `sink` as well as to the in-memory log."""
    with _processes.lock:
        if sink is not None:
            _processes.sinks.append(sink)
        if _processes.installed:
            return
        _processes.installed = True
    original = subprocess.Popen.__init__

    def init(self: subprocess.Popen[Any], args: Any, *rest: Any, **kwargs: Any) -> None:
        original(self, args, *rest, **kwargs)
        with contextlib.suppress(Exception):
            record_process(
                args,
                cwd=kwargs.get("cwd"),
                pid=getattr(self, "pid", None),
                executable=kwargs.get("executable"),
            )

    subprocess.Popen.__init__ = init  # type: ignore[method-assign]


def processes() -> list[ProcessRow]:
    """The processes recorded in this process (most recent last)."""
    with _processes.lock:
        return list(_processes.memory)
