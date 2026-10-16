"""The codec interface (ADR 0003). One codec per API format, used in both directions:

* client side (outbound calls): `encode_request`, `decode_response`, `decode_stream`,
  `decode_error`;
* server side (the inbound proxy, SPEC §12 item 2): `decode_request`, `encode_response`,
  `encode_stream`, `encode_error`.

Codecs are pure: they never perform I/O. `stepswitch.compat.client.ModelClient` sends the
`HttpCall` through the egress layer and feeds the results back to the codec.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from stepswitch.compat.errors import ModelError, kind_for_status
from stepswitch.compat.framing import StreamEvent
from stepswitch.compat.ir import (
    ConversionReport,
    Delta,
    RefusalDelta,
    Request,
    Response,
    TextDelta,
)

Framing = Literal["sse", "ndjson"]


@dataclass
class HttpCall:
    """A request a codec wants sent, relative to the endpoint's base URL."""

    method: str
    path: str
    json: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    stream: bool = False


@dataclass
class CodecOptions:
    """Per-endpoint knobs that change the wire shape (from config or the capability profile)."""

    # chat-completions: "max_tokens" (accepted by local engines) or "max_completion_tokens".
    max_tokens_field: str = "max_tokens"
    # Include usage in streams where the format makes it optional.
    stream_usage: bool = True
    # Model's tool-call syntax for prompt-based formats ("hermes", "harmony", ...), if known.
    tool_parser: str | None = None
    # Strict mode: raise ConversionError instead of reporting a loss.
    strict: bool = False
    # Version strings some formats send as headers.
    api_version: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class StreamState:
    """Mutable per-stream scratch space for codecs (indices, open tool calls, usage so far)."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.started = False
        self.finished = False
        self.tool_index = 0
        self.open_tools: dict[Any, int] = {}


class Codec(ABC):
    key: ClassVar[str]
    format: ClassVar[str]
    framing: ClassVar[Framing] = "sse"

    def __init__(self, options: CodecOptions | None = None) -> None:
        self.options = options or CodecOptions()

    # ---- client side ----------------------------------------------------------------------

    @abstractmethod
    def encode_request(self, request: Request, report: ConversionReport) -> HttpCall:
        """IR request -> wire request. Losses go into `report`."""

    @abstractmethod
    def decode_response(self, body: Any) -> Response:
        """Wire response body (parsed JSON) -> IR response."""

    @abstractmethod
    def decode_stream_event(self, event: StreamEvent, state: StreamState) -> list[Delta]:
        """One wire stream event -> zero or more IR deltas."""

    def decode_stream(self, events: Iterable[StreamEvent]) -> Iterator[Delta]:
        state = StreamState()
        for event in events:
            yield from self.decode_stream_event(event, state)
        yield from self.finish_stream(state)

    def finish_stream(self, state: StreamState) -> list[Delta]:
        """Deltas owed at end of stream (for formats without an explicit final event)."""
        return []

    def decode_error(
        self, status: int, body: Any, headers: dict[str, str] | None = None
    ) -> ModelError:
        message = _error_message(body) or f"HTTP {status}"
        retry_after = None
        if headers:
            from stepswitch.compat.errors import parse_retry_after

            retry_after = parse_retry_after(headers.get("retry-after"))
        return ModelError(
            kind_for_status(status), message, status=status, retry_after=retry_after, raw=body
        )

    # ---- server side ----------------------------------------------------------------------

    @abstractmethod
    def decode_request(
        self, body: Any, *, path: str = "", headers: dict[str, str] | None = None
    ) -> Request:
        """Wire request body -> IR request (used by the proxy)."""

    @abstractmethod
    def encode_response(self, response: Response) -> Any:
        """IR response -> wire response body."""

    @abstractmethod
    def encode_stream(self, deltas: Iterable[Delta]) -> Iterator[StreamEvent]:
        """IR deltas -> wire stream events."""

    def encode_error(self, error: ModelError) -> tuple[int, Any]:
        status = error.status or 500
        return status, {"error": {"message": error.message, "type": error.kind, "code": error.code}}


def refusal_as_text(delta: Delta) -> Delta:
    """For stream encoders without a refusal event: a refusal streams as text, which is how
    their non-streamed encoders send it too."""
    return TextDelta(text=delta.text) if isinstance(delta, RefusalDelta) else delta


def _error_message(body: Any) -> str | None:
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail")
            return str(message) if message else None
        if isinstance(error, str):
            return error
    if isinstance(body, str) and body.strip():
        return body.strip()[:500]
    return None
