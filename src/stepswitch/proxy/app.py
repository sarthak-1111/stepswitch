"""The proxy's HTTP side (ADR 0004 items 1 and 2, ADR 0029).

* Binds to 127.0.0.1 only, port 7778 by default (7777 is the dashboard).
* Every route except the `HEAD /` health check needs the local token, generated on first start
  in `.stepswitch/proxy/token`. It is accepted in each format's usual place: `Authorization:
  Bearer`, `x-api-key`, `x-goog-api-key` or the `key` query parameter. Requests with another
  `Host`, or posts from a web page of another origin, are refused before routing.
* Streams start as soon as the upstream answers, or after one keep-alive interval, whichever
  comes first; an upstream failure inside that interval is answered with an HTTP error in the
  caller's shape, a later one with an error event in the stream. While the upstream is quiet,
  messages-format streams get `ping` events and other SSE streams comment lines.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import secrets
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from fastapi import FastAPI
from fastapi import Request as HttpRequest
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from stepswitch.compat.codecs import get_codec
from stepswitch.compat.errors import ModelError
from stepswitch.compat.framing import StreamEvent, format_ndjson, format_sse
from stepswitch.compat.ir import Delta, ErrorDelta, StartDelta
from stepswitch.proxy import formats
from stepswitch.proxy.service import Call, ProxyService

HOST = "127.0.0.1"
DEFAULT_PORT = 7778
TOKEN_FILE = Path("proxy") / "token"
INBOUND_ROUTES = (
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1beta/models/{model}:generateContent",
    "/v1beta/models/{model}:streamGenerateContent",
    "/api/chat",
    "/api/generate",
    "/api/tags",
    "/api/show",
    "/v1/models",
    "/stepswitch/v1/route",
)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MESSAGES_PING = 'event: ping\ndata: {"type": "ping"}\n\n'
SSE_COMMENT = ": keep-alive\n\n"


def token_path(root: Path) -> Path:
    return root / ".stepswitch" / TOKEN_FILE


def load_or_create_token(root: Path) -> str:
    """The proxy's local token; created on first use, in the gitignored state directory."""
    path = token_path(root)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token:
        return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = "ssw-" + secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8", newline="\n")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return token


class ProxyGuard:
    """Host and Origin checks and the local token, before any route runs."""

    def __init__(
        self, app: ASGIApp, *, token: str, port: int, hosts: set[str] | None = None
    ) -> None:
        self.app = app
        self.token = token
        self.hosts = hosts or {f"{HOST}:{port}", f"localhost:{port}"}
        self.origins = {f"http://{host}" for host in self.hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        method: str = scope["method"]
        if headers.get("host", "") not in self.hosts:
            await JSONResponse(_error_body("unknown host"), status_code=403)(scope, receive, send)
            return
        origin = headers.get("origin")
        if method not in SAFE_METHODS and origin is not None and origin not in self.origins:
            body = _error_body("cross-site request")
            await JSONResponse(body, status_code=403)(scope, receive, send)
            return
        if method == "HEAD" and scope["path"] in {"/", "/api/hello"}:
            await Response(status_code=200)(scope, receive, send)
            return
        query = scope.get("query_string", b"").decode("latin-1")
        if not self._authorized(headers, query):
            body = _error_body("missing or wrong proxy token", "authentication_error")
            await JSONResponse(body, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _authorized(self, headers: Headers, query: str) -> bool:
        supplied = []
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied.append(auth[7:].strip())
        supplied += [headers.get("x-api-key", ""), headers.get("x-goog-api-key", "")]
        supplied += [value for key, value in parse_qsl(query) if key == "key"]
        return any(s and secrets.compare_digest(s, self.token) for s in supplied)


def _error_body(message: str, kind: str = "permission_error") -> dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": message}}


def _error_response(fmt: str, error: ModelError) -> JSONResponse:
    status, body = get_codec(fmt).encode_error(error)
    return JSONResponse(body, status_code=status or 500)


async def _json_body(http: HttpRequest) -> Any:
    raw = await http.body()
    try:
        return json.loads(raw or b"{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ModelError("bad_request", f"the body is not JSON: {exc}", status=400) from exc


def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run blocking work in a thread, with this request's context variables."""
    context = contextvars.copy_context()
    return asyncio.to_thread(context.run, fn, *args, **kwargs)


class _Stream:
    """Bridges the upstream (blocking, in a thread) to the response (async)."""

    def __init__(
        self,
        service: ProxyService,
        call: Call,
        transform: Callable[[Any], Any] | None,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.service = service
        self.call = call
        self.transform = transform
        self.loop = loop
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self.cancelled = threading.Event()
        self.heartbeat = service.settings.heartbeat_seconds
        self.ndjson = call.codec.framing == "ndjson"

    def put(self, kind: str, value: Any = None) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, (kind, value))

    # -- the producer thread ---------------------------------------------------------------

    def produce(self) -> None:
        result: dict[str, Any] = {}
        opened = threading.Event()
        context = contextvars.copy_context()

        def opener() -> None:
            try:
                result["deltas"] = self.service.open_stream(self.call)
            except ModelError as exc:
                result["error"] = exc
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                result["error"] = ModelError("server", f"proxy error: {exc}", status=500)
            finally:
                opened.set()

        threading.Thread(target=context.run, args=(opener,), daemon=True).start()
        early = opened.wait(self.heartbeat)
        if early and "error" in result:
            self.put("error", result["error"])
            return
        deltas: Iterator[Delta] = result["deltas"] if early else self._late(result, opened)
        self.put("commit")
        try:
            for event in self.call.codec.encode_stream(deltas):
                if self.cancelled.is_set():
                    break
                self.put("event", event)
        except Exception as exc:  # noqa: BLE001 - the stream already started
            self.put("event", self._error_event(str(exc)))
        finally:
            close = getattr(deltas, "close", None)
            if callable(close):
                close()
            source = result.get("deltas")
            if source is not None and source is not deltas:
                getattr(source, "close", lambda: None)()
            self.put("end")

    def _late(self, result: dict[str, Any], opened: threading.Event) -> Iterator[Delta]:
        """The stream begins before the upstream answered: start it now, keep it alive, and
        drop the upstream's own start."""
        yield StartDelta(
            id=self.call.response_id or f"msg_{uuid.uuid4().hex[:24]}",
            model=self.call.request.model or None,
        )
        while not opened.wait(0.5):
            if self.cancelled.is_set():
                return
        if "error" in result:
            error: ModelError = result["error"]
            yield ErrorDelta(kind=error.kind, message=error.message, status=error.status)
            return
        for delta in result["deltas"]:
            if not isinstance(delta, StartDelta):
                yield delta

    def _error_event(self, message: str) -> StreamEvent:
        status, body = self.call.codec.encode_error(ModelError("server", message))
        return StreamEvent(data=json.dumps(body), event="error" if not self.ndjson else None)

    # -- the response side ------------------------------------------------------------------

    def text(self, event: StreamEvent) -> str:
        if self.transform is not None and self.ndjson:
            with contextlib.suppress(ValueError):
                event = StreamEvent(data=json.dumps(self.transform(event.json())))
        return format_ndjson(event) if self.ndjson else format_sse(event)

    async def body(self) -> AsyncIterator[str]:
        sent = 0
        try:
            while True:
                try:
                    kind, value = await asyncio.wait_for(self.queue.get(), self.heartbeat)
                except TimeoutError:
                    beat = self._beat(sent)
                    if beat:
                        yield beat
                    continue
                if kind == "end":
                    return
                if kind == "event":
                    sent += 1
                    yield self.text(value)
        finally:
            self.cancelled.set()

    def _beat(self, sent: int) -> str:
        if self.ndjson:
            return ""
        if self.call.fmt == "messages":
            return MESSAGES_PING if sent else ""
        return SSE_COMMENT


async def _stream(
    service: ProxyService, call: Call, transform: Callable[[Any], Any] | None
) -> Response:
    stream = _Stream(service, call, transform, asyncio.get_running_loop())
    context = contextvars.copy_context()
    threading.Thread(target=context.run, args=(stream.produce,), daemon=True).start()
    kind, value = await stream.queue.get()
    if kind == "error":
        return _error_response(call.fmt, value)
    media = "application/x-ndjson" if stream.ndjson else "text/event-stream"
    headers = {"cache-control": "no-cache", "x-stepswitch-task": call.task_id}
    return StreamingResponse(stream.body(), media_type=media, headers=headers)


async def _serve(
    service: ProxyService,
    fmt: str,
    http: HttpRequest,
    *,
    body_transform: Callable[[Any], Any] | None = None,
    transform: Callable[[Any], Any] | None = None,
) -> Response:
    try:
        body = await _json_body(http)
        if body_transform is not None:
            body = body_transform(body)
        call: Call = await _run(
            service.prepare, fmt, body, path=http.url.path, headers=dict(http.headers)
        )
    except ModelError as exc:
        return _error_response(fmt, exc)
    if call.stream:
        return await _stream(service, call, transform)
    try:
        response = await _run(service.complete, call)
    except ModelError as exc:
        return _error_response(fmt, exc)
    answer = call.codec.encode_response(response)
    if transform is not None:
        answer = transform(answer)
    return JSONResponse(answer, headers={"x-stepswitch-task": call.task_id})


def create_proxy_app(
    service: ProxyService,
    *,
    token: str,
    port: int = DEFAULT_PORT,
    hosts: set[str] | None = None,
) -> ASGIApp:
    app = FastAPI(title="Stepswitch proxy", docs_url=None, redoc_url=None, openapi_url=None)

    def model_route(path: str, fmt: str, **options: Any) -> None:
        async def handler(http: HttpRequest) -> Response:
            return await _serve(service, fmt, http, **options)

        app.add_api_route(path, handler, methods=["POST"], include_in_schema=False)

    model_route("/v1/chat/completions", "chat-completions")
    model_route("/v1/completions", "text-completion")
    model_route("/v1/responses", "responses")
    model_route("/v1/messages", "messages")
    model_route("/v1beta/models/{model}:generateContent", "generate-content")
    model_route("/v1beta/models/{model}:streamGenerateContent", "generate-content")
    model_route("/api/chat", "ollama-native")
    model_route(
        "/api/generate",
        "ollama-native",
        body_transform=formats.generate_as_chat,
        transform=formats.chat_as_generate,
    )

    @app.post("/v1/messages/count_tokens", include_in_schema=False)
    async def count_tokens(http: HttpRequest) -> Response:
        try:
            body = await _json_body(http)
            _, request = service.decode("messages", body, path=http.url.path)
        except ModelError as exc:
            return _error_response("messages", exc)
        headers = {formats.ESTIMATE_HEADER: "characters/4"}
        return JSONResponse(formats.count_tokens(request), headers=headers)

    @app.post("/stepswitch/v1/route", include_in_schema=False)
    async def route_only(http: HttpRequest) -> Response:
        try:
            body = await _json_body(http)
            if not isinstance(body, dict):
                raise ModelError("bad_request", "the body is a JSON object", status=400)
            answer = await _run(service.route_only, body, dict(http.headers))
        except ModelError as exc:
            return _error_response("chat-completions", exc)
        return JSONResponse(answer)

    @app.get("/v1/models", include_in_schema=False)
    async def models() -> Response:
        return JSONResponse(formats.models_list(service.catalog))

    @app.get("/api/tags", include_in_schema=False)
    async def tags() -> Response:
        return JSONResponse(formats.ollama_tags(service.catalog))

    @app.post("/api/show", include_in_schema=False)
    async def show(http: HttpRequest) -> Response:
        try:
            return JSONResponse(formats.ollama_show(service.catalog, await _json_body(http)))
        except ModelError as exc:
            return _error_response("ollama-native", exc)

    @app.get("/", include_in_schema=False)
    async def root() -> Response:
        return JSONResponse({"service": "stepswitch proxy", "routes": list(INBOUND_ROUTES)})

    return ProxyGuard(app, token=token, port=port, hosts=hosts)
