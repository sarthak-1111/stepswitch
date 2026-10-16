"""`ModelClient`: sends IR requests to one endpoint through its codec and the egress layer.

This is the only way the rest of Stepswitch talks to a model (working rule 2).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from stepswitch import net
from stepswitch.compat.codecs import get_codec
from stepswitch.compat.codecs.base import Codec, CodecOptions, HttpCall
from stepswitch.compat.errors import ModelError, backoff_delay
from stepswitch.compat.framing import iter_lines, parse_ndjson, parse_sse
from stepswitch.compat.ir import (
    ConversionReport,
    Delta,
    Origin,
    ReasoningPart,
    Request,
    Response,
)
from stepswitch.compat.stream import StreamAssembler


@dataclass
class Endpoint:
    """A configured endpoint resolved for one API format and (optionally) one model."""

    name: str
    base_url: str
    format: str = "chat-completions"
    model: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    local: bool = True
    options: CodecOptions = field(default_factory=CodecOptions)

    @classmethod
    def from_config(
        cls, config: Any, *, model: str | None = None, fmt: str | None = None
    ) -> Endpoint:
        """Build from a `stepswitch.config.EndpointConfig`."""
        headers: dict[str, str] = {}
        key = config.resolved_api_key()
        if key and config.auth == "bearer":
            headers["Authorization"] = f"Bearer {key}"
        elif key and config.auth == "header":
            headers[config.auth_header or "x-api-key"] = key
        return cls(
            name=config.name,
            base_url=config.url,
            format=fmt or config.primary_format(),
            model=model or (config.models[0] if config.models else None),
            headers=headers,
            local=config.is_local(),
            options=CodecOptions(tool_parser=getattr(config, "tool_parser", None)),
        )


@dataclass
class Completion:
    response: Response
    report: ConversionReport
    latency_s: float
    attempts: int = 1


def join_url(base: str, path: str) -> str:
    """Join a base URL and a codec path, without doubling a version prefix like /v1."""
    base = base.rstrip("/")
    for prefix in ("/v1beta", "/v1"):
        if base.endswith(prefix) and path.startswith(prefix + "/"):
            base = base[: -len(prefix)]
            break
    return base + path


class ModelClient:
    def __init__(
        self,
        endpoint: Endpoint,
        *,
        egress: net.Egress | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 600.0,
        max_retries: int = 2,
        purpose: str = "model",
        task_id: str | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.endpoint = endpoint
        # Describes the format; each request gets its own instance (see `new_codec`).
        self.codec: Codec = get_codec(endpoint.format, endpoint.options)
        self._egress = egress or net.default()
        self._transport = transport
        self._timeout = timeout
        self._max_retries = max_retries
        self._purpose = purpose
        self._task_id = task_id
        self._sleep = sleep

    # ------------------------------------------------------------------------------------

    def _http(self) -> httpx.Client:
        return self._egress.client(
            self._purpose,
            headers={"user-agent": "stepswitch", **self.endpoint.headers},
            timeout=httpx.Timeout(self._timeout, connect=10.0),
            transport=self._transport,
            task_id=self._task_id,
        )

    def new_codec(self) -> Codec:
        """A codec for one request. Codecs may keep state between encoding a request and
        decoding its response (turn numbers for synthetic tool-call IDs, whether tools were
        sent), so an instance never serves two requests at once."""
        return get_codec(self.endpoint.format, self.endpoint.options)

    def prepare(
        self, request: Request, codec: Codec | None = None
    ) -> tuple[HttpCall, ConversionReport]:
        codec = codec or self.new_codec()
        if not request.model and self.endpoint.model:
            request = request.model_copy(update={"model": self.endpoint.model})
        report = ConversionReport(target=codec.format)
        call = codec.encode_request(request, report)
        return call, report

    def _send(self, http: httpx.Client, call: HttpCall) -> httpx.Response:
        url = join_url(self.endpoint.base_url, call.path)
        try:
            return http.request(
                call.method, url, json=call.json, headers=call.headers, params=call.params or None
            )
        except net.EgressBlocked as exc:
            raise ModelError("blocked", str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise ModelError("timeout", f"timeout talking to {self.endpoint.name}: {exc}") from exc
        except httpx.TransportError as exc:
            raise ModelError("connection", f"cannot reach {self.endpoint.name}: {exc}") from exc

    def origin(self, model: str | None = None) -> Origin:
        """Where reasoning from this endpoint comes from; replayed only to the same origin."""
        model = model or self.endpoint.model
        return Origin(format=self.codec.format, endpoint=self.endpoint.name, model=model)

    def _label_reasoning(self, response: Response, request: Request) -> Response:
        """Give every reasoning part the full origin: format, endpoint name and model."""
        model = response.model or request.model or self.endpoint.model
        for part in response.message.content:
            if isinstance(part, ReasoningPart):
                origin = part.origin or self.origin(model)
                part.origin = origin.model_copy(
                    update={
                        "endpoint": origin.endpoint or self.endpoint.name,
                        "model": origin.model or model,
                    }
                )
        return response

    def _error_from(self, response: httpx.Response, codec: Codec) -> ModelError:
        try:
            body: Any = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = response.text
        return codec.decode_error(response.status_code, body, dict(response.headers))

    def complete(self, request: Request) -> Completion:
        """Non-streaming call with retries for transient errors."""
        request = request.model_copy(update={"stream": False})
        codec = self.new_codec()
        call, report = self.prepare(request, codec)
        attempt = 0
        started = time.perf_counter()
        with self._http() as http:
            while True:
                try:
                    response = self._send(http, call)
                    if response.status_code >= 400:
                        raise self._error_from(response, codec)
                    try:
                        body = response.json()
                    except json.JSONDecodeError as exc:
                        raise ModelError("invalid_response", f"non-JSON body: {exc}") from exc
                    result = self._label_reasoning(codec.decode_response(body), request)
                    return Completion(result, report, time.perf_counter() - started, attempt + 1)
                except ModelError as exc:
                    if not exc.retryable or attempt >= self._max_retries:
                        raise
                    self._sleep(backoff_delay(attempt, exc.retry_after))
                    attempt += 1

    def stream(self, request: Request) -> Iterator[Delta]:
        """Streaming call. Retries only before the first delta has been produced."""
        request = request.model_copy(update={"stream": True})
        codec = self.new_codec()
        call, _report = self.prepare(request, codec)
        call.stream = True
        attempt = 0
        with self._http() as http:
            while True:
                produced = False
                try:
                    url = join_url(self.endpoint.base_url, call.path)
                    try:
                        with http.stream(
                            call.method,
                            url,
                            json=call.json,
                            headers=call.headers,
                            params=call.params or None,
                        ) as response:
                            if response.status_code >= 400:
                                response.read()
                                raise self._error_from(response, codec)
                            lines = iter_lines(response.iter_text())
                            events = (
                                parse_sse(lines) if codec.framing == "sse" else parse_ndjson(lines)
                            )
                            for delta in codec.decode_stream(events):
                                produced = True
                                yield delta
                            return
                    except net.EgressBlocked as exc:
                        raise ModelError("blocked", str(exc)) from exc
                    except httpx.TimeoutException as exc:
                        raise ModelError(
                            "timeout", f"timeout talking to {self.endpoint.name}"
                        ) from exc
                    except httpx.TransportError as exc:
                        raise ModelError(
                            "connection", f"cannot reach {self.endpoint.name}: {exc}"
                        ) from exc
                except ModelError as exc:
                    if produced or not exc.retryable or attempt >= self._max_retries:
                        raise
                    self._sleep(backoff_delay(attempt, exc.retry_after))
                    attempt += 1

    def stream_complete(self, request: Request) -> Completion:
        """Stream and assemble; useful when a server only reports some fields while streaming."""
        started = time.perf_counter()
        assembler = StreamAssembler(origin=self.origin(None).model_copy(update={"model": None}))
        _, report = self.prepare(request)
        for delta in self.stream(request):
            assembler.feed(delta)
        if assembler.error is not None:
            raise ModelError(
                assembler.error.kind
                if assembler.error.kind in {"server", "rate_limit", "overloaded"}
                else "server",  # type: ignore[arg-type]
                assembler.error.message,
                status=assembler.error.status,
            )
        result = self._label_reasoning(assembler.result(), request)
        return Completion(result, report, time.perf_counter() - started)
