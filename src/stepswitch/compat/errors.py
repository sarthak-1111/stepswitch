"""Normalized model-server errors and the retry policy shared by every codec."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

ErrorKind = Literal[
    "bad_request",
    "authentication",
    "permission",
    "not_found",
    "too_large",
    "rate_limit",
    "quota",
    "overloaded",
    "server",
    "timeout",
    "connection",
    "blocked",
    "unsupported",
    "invalid_response",
]

RETRYABLE: frozenset[str] = frozenset(
    {"rate_limit", "overloaded", "server", "timeout", "connection"}
)

# An engine's report that it could not parse the tool call a model generated. The model
# answered, but its call was malformed, and the same request tends to fail the same way, so
# it is not retried as it was sent; the built-in agent asks again through the fallback tool
# protocol, which Stepswitch parses itself (ADR 0035). Observed on Ollama 0.34.4 with
# gpt-oss: HTTP 500 "error parsing tool call: raw='...'" (docs/RESEARCH.md, Phase 11).
TOOL_CALL_UNPARSED_RE = re.compile(r"error parsing tool call", re.IGNORECASE)

STATUS_KINDS: dict[int, ErrorKind] = {
    400: "bad_request",
    401: "authentication",
    403: "permission",
    404: "not_found",
    408: "timeout",
    409: "bad_request",
    413: "too_large",
    422: "bad_request",
    429: "rate_limit",
    500: "server",
    501: "unsupported",
    502: "server",
    503: "overloaded",
    504: "timeout",
    529: "overloaded",
}


class ModelError(Exception):
    """An error reported by (or while talking to) a model endpoint."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        retry_after: float | None = None,
        raw: Any = None,
    ) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.message = message
        self.status = status
        self.code = code
        self.retry_after = retry_after
        self.raw = raw

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

    def __repr__(self) -> str:
        return f"ModelError({self.kind!r}, {self.message!r}, status={self.status})"


def tool_call_unparsed(exc: BaseException) -> bool:
    """Whether `exc` is an engine's report that it could not parse a model's tool call."""
    return isinstance(exc, ModelError) and bool(TOOL_CALL_UNPARSED_RE.search(exc.message))


def kind_for_status(status: int) -> ErrorKind:
    if status in STATUS_KINDS:
        return STATUS_KINDS[status]
    if 500 <= status < 600:
        return "server"
    return "bad_request"


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait from a Retry-After header: delay-seconds or an HTTP-date (RFC 9110)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def backoff_delay(
    attempt: int, retry_after: float | None = None, base: float = 0.5, cap: float = 20.0
) -> float:
    """Exponential backoff (0.5 s, 1 s, 2 s, ...), honouring a server's Retry-After."""
    if retry_after is not None:
        return min(retry_after, 60.0)
    return float(min(cap, base * (2**attempt)))
