"""The single egress choke point (SPEC §15, working rule 6).

Every HTTP request Stepswitch makes goes through a client from this module. The client's
transport checks the destination against the egress policy (offline mode allows only local
addresses) and records an audit row for every request, allowed or blocked.
"""

from __future__ import annotations

import contextlib
import ipaddress
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


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
            task_id=self._task_id,
            allowed=local or self._policy.permits(host),
            local=local,
            bytes_out=_content_length(request),
        )
        if not row.allowed:
            row.error = "blocked by offline mode"
            self._sink(row)
            raise EgressBlocked(f"offline mode blocks {url.scheme}://{host}", request=request)
        try:
            response = self._inner.handle_request(request)
        except Exception as exc:
            row.error = f"{type(exc).__name__}: {exc}"[:300]
            self._sink(row)
            raise
        row.status = response.status_code
        length = response.headers.get("content-length")
        row.bytes_in = int(length) if length and length.isdigit() else None
        self._sink(row)
        return response

    def close(self) -> None:
        self._inner.close()


def _content_length(request: httpx.Request) -> int:
    try:
        return len(request.content)
    except httpx.RequestNotRead:
        return 0


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
            # Proxy settings from the environment are honoured, except in offline mode.
            trust_env=not self.policy.offline,
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
