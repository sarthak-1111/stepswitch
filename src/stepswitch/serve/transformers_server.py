"""A small chat-completions server around the transformers library (SPEC §4.2, ADR 0008).

The universal slow fallback: any architecture the transformers library loads, served from a
separate process in its own virtual environment, so the core never imports PyTorch or
transformers. The adapter (`stepswitch.serve.transformers`) runs this file with that
environment's interpreter in isolated mode (`python -I transformers_server.py ...`), which is
why it uses only the standard library at import time and imports transformers and torch only
when it loads the model. It also runs as `python -m stepswitch.serve.transformers_server`.

Endpoints: `POST /v1/chat/completions` (plain JSON or server-sent events), `GET /v1/models`, and
`GET /health`, which needs no key and answers 503 while the model loads. The server renders the
tokenizer's chat template, runs one generation at a time, and reports `usage` from token counts.
It parses neither tool calls nor reasoning: tools reach the model through Stepswitch's fallback
tool protocol, so requests with `tools`, a `response_format` constraint, `n > 1` or `logprobs`
are refused with HTTP 400 instead of being silently ignored. Every endpoint except `/health`
needs the key from the `STEPSWITCH_SERVER_API_KEY` environment variable when it is set; the key
never appears on the command line. Model files are read locally only (`local_files_only`).
"""

from __future__ import annotations

import argparse
import hmac
import importlib
import json
import os
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

API_KEY_ENV = "STEPSWITCH_SERVER_API_KEY"
DEFAULT_MAX_TOKENS = 1024
MAX_BODY_BYTES = 32 * 1024 * 1024
DTYPES = ("auto", "float32", "float16", "bfloat16")


class RequestError(Exception):
    """A request the server refuses; becomes an error body with this HTTP status."""

    def __init__(
        self, message: str, status: int = 400, kind: str = "invalid_request_error"
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind


@dataclass
class GenerateOptions:
    """Sampling settings of one request (None means the backend's default)."""

    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    stop: list[str] = field(default_factory=list)
    template_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Prepared:
    """A rendered and tokenized prompt, checked against the context size."""

    prompt_tokens: int
    max_tokens: int
    payload: Any = None


@dataclass
class Generated:
    completion_tokens: int
    finish_reason: str  # "stop" (end of sequence) or "length" (token limit)


class Generator(Protocol):
    """A model backend. `prepare` raises RequestError for prompts it cannot take; `run` passes
    each new piece of text to `on_text` and stops early once `on_text` returns False."""

    def prepare(self, messages: list[dict[str, Any]], options: GenerateOptions) -> Prepared: ...

    def run(self, prepared: Prepared, on_text: Callable[[str], bool]) -> Generated: ...


GeneratorFactory = Callable[[argparse.Namespace], Generator]


@dataclass
class ChatRequest:
    messages: list[dict[str, Any]]
    options: GenerateOptions
    stream: bool = False
    include_usage: bool = False


# -- request parsing ----------------------------------------------------------------------------


def _content_text(content: Any, index: int) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text":
                kind = part.get("type") if isinstance(part, dict) else type(part).__name__
                raise RequestError(f"messages[{index}]: only text content is supported, not {kind}")
            text = part.get("text")
            if not isinstance(text, str):
                raise RequestError(f"messages[{index}]: a text part needs a string `text`")
            texts.append(text)
        return "\n".join(texts)
    raise RequestError(f"messages[{index}].content must be a string or a list of text parts")


def _messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise RequestError("`messages` must be a non-empty list")
    messages: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise RequestError(f"messages[{index}] must be an object")
        role = item.get("role")
        if not isinstance(role, str) or not role:
            raise RequestError(f"messages[{index}].role must be a non-empty string")
        message: dict[str, Any] = {
            "role": role,
            "content": _content_text(item.get("content"), index),
        }
        for key in ("name", "tool_call_id", "tool_calls"):
            if item.get(key) is not None:
                message[key] = item[key]
        messages.append(message)
    return messages


def _number(body: dict[str, Any], key: str, low: float, high: float) -> float | None:
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
        raise RequestError(f"`{key}` must be a number between {low} and {high}")
    return float(value)


def _stops(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item for item in value if item]
    raise RequestError("`stop` must be a string or a list of strings")


def parse_chat_request(body: Any, defaults: dict[str, Any] | None = None) -> ChatRequest:
    """Validate a chat-completions request body; RequestError for anything not supported."""
    if not isinstance(body, dict):
        raise RequestError("the request body must be a JSON object")
    tools = body.get("tools")
    if tools and body.get("tool_choice") != "none":
        raise RequestError(
            "this server does no native tool calling; describe the tools in the prompt "
            "(Stepswitch's fallback tool protocol)"
        )
    response_format = body.get("response_format")
    if isinstance(response_format, dict) and response_format.get("type") not in (None, "text"):
        raise RequestError("this server has no constrained decoding (`response_format`)")
    if body.get("n") not in (None, 1):
        raise RequestError("`n` must be 1")
    if body.get("logprobs") or body.get("top_logprobs"):
        raise RequestError("this server does not return logprobs")
    limit = body.get("max_completion_tokens", body.get("max_tokens"))
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise RequestError("`max_tokens` must be a positive integer")
    seed = body.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise RequestError("`seed` must be an integer")
    kwargs = body.get("chat_template_kwargs")
    if kwargs is not None and not isinstance(kwargs, dict):
        raise RequestError("`chat_template_kwargs` must be an object")
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise RequestError("`stream` must be true or false")
    stream_options = body.get("stream_options")
    include_usage = isinstance(stream_options, dict) and stream_options.get("include_usage") is True
    options = GenerateOptions(
        max_tokens=limit,
        temperature=_number(body, "temperature", 0.0, 2.0),
        top_p=_number(body, "top_p", 0.0, 1.0),
        seed=seed,
        stop=_stops(body.get("stop")),
        template_kwargs={**(defaults or {}), **(kwargs or {})},
    )
    return ChatRequest(_messages(body.get("messages")), options, stream, include_usage)


# -- stop strings -------------------------------------------------------------------------------


class StopFilter:
    """Cuts generated text at the first stop string, holding back any tail that could still
    grow into one, so streamed text never contains a stop string or a piece of one."""

    def __init__(self, stops: Sequence[str]) -> None:
        self.stops = [stop for stop in stops if stop]
        self.pending = ""
        self.stopped = False

    def push(self, text: str) -> str:
        """Feed new text; return the part that is safe to send now."""
        if self.stopped:
            return ""
        self.pending += text
        hits = [index for index in (self.pending.find(stop) for stop in self.stops) if index >= 0]
        if hits:
            out = self.pending[: min(hits)]
            self.pending, self.stopped = "", True
            return out
        keep = self._partial()
        out = self.pending[: len(self.pending) - keep]
        self.pending = self.pending[len(self.pending) - keep :]
        return out

    def flush(self) -> str:
        """The held-back tail once generation has ended without a stop string."""
        out, self.pending = ("" if self.stopped else self.pending), ""
        return out

    def _partial(self) -> int:
        longest = 0
        for stop in self.stops:
            for size in range(min(len(stop) - 1, len(self.pending)), longest, -1):
                if self.pending.endswith(stop[:size]):
                    longest = size
                    break
        return longest


# -- response bodies ----------------------------------------------------------------------------


def usage_body(prompt_tokens: int, completion_tokens: int) -> dict[str, int]:
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def completion_body(
    cid: str, created: int, model: str, text: str, finish: str, usage: dict[str, int]
) -> dict[str, Any]:
    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish,
            }
        ],
        "usage": usage,
    }


def chunk_body(
    cid: str, created: int, model: str, delta: dict[str, Any], finish: str | None = None
) -> dict[str, Any]:
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def error_body(message: str, kind: str, status: int) -> dict[str, Any]:
    return {"error": {"message": message, "type": kind, "code": status}}


# -- HTTP server --------------------------------------------------------------------------------


class ChatServer(ThreadingHTTPServer):
    """The HTTP side: routing, authentication and one generation at a time."""

    daemon_threads = True
    # Windows lets a second socket bind a port that is in use when SO_REUSEADDR is set.
    allow_reuse_address = sys.platform != "win32"

    def __init__(
        self,
        address: tuple[str, int],
        *,
        served_model: str,
        api_key: str | None = None,
        template_kwargs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(address, ChatHandler)
        self.served_model = served_model
        self.api_key = api_key or None
        self.template_kwargs = dict(template_kwargs or {})
        self.generator: Generator | None = None
        self.failure: str | None = None
        self.created = int(time.time())
        # One generation at a time: later requests wait here in arrival order.
        self.gate = threading.Lock()


class ChatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "stepswitch-transformers"

    @property
    def app(self) -> ChatServer:
        return cast(ChatServer, self.server)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        print(f"request: {format % args}", flush=True)

    # -- plumbing

    def _send_json(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: int, message: str, kind: str) -> None:
        self._send_json(status, error_body(message, kind, status))

    def _authorized(self) -> bool:
        key = self.app.api_key
        if key is None:
            return True
        given = self.headers.get("Authorization", "")
        scheme, _, token = given.partition(" ")
        if scheme.lower() != "bearer":
            token = self.headers.get("X-Api-Key", "")
        return hmac.compare_digest(token.strip().encode(), key.encode())

    def _read_body(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise RequestError("invalid Content-Length") from None
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            raise RequestError("the request body is too large", 413)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"null")
        except ValueError:
            raise RequestError("the request body is not valid JSON") from None

    # -- routes

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = urlsplit(self.path).path
        if path in ("/health", "/v1/health"):
            if self.app.generator is None:
                self._send_error(503, "Loading model", "unavailable_error")
            else:
                self._send_json(200, {"status": "ok"})
            return
        if not self._authorized():
            self._send_error(401, "Invalid API Key", "authentication_error")
            return
        if path == "/v1/models":
            model = {
                "id": self.app.served_model,
                "object": "model",
                "created": self.app.created,
                "owned_by": "stepswitch",
            }
            self._send_json(200, {"object": "list", "data": [model]})
            return
        self._send_error(404, f"no route for GET {path}", "not_found_error")

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        path = urlsplit(self.path).path
        try:
            body = self._read_body()  # always read, so a kept-alive connection stays in step
        except RequestError as exc:
            self._send_error(exc.status, exc.message, exc.kind)
            return
        if not self._authorized():
            self._send_error(401, "Invalid API Key", "authentication_error")
            return
        if path != "/v1/chat/completions":
            self._send_error(404, f"no route for POST {path}", "not_found_error")
            return
        generator = self.app.generator
        if generator is None:
            self._send_error(503, "Loading model", "unavailable_error")
            return
        try:
            request = parse_chat_request(body, self.app.template_kwargs)
            with self.app.gate:
                prepared = generator.prepare(request.messages, request.options)
                if request.stream:
                    self._stream(generator, request, prepared)
                else:
                    self._complete(generator, request, prepared)
        except RequestError as exc:
            self._send_error(exc.status, exc.message, exc.kind)
        except Exception as exc:  # noqa: BLE001 - reported to the client and the log
            traceback.print_exc()
            sys.stdout.flush()
            self._send_error(500, f"{type(exc).__name__}: {exc}", "server_error")

    def _complete(self, generator: Generator, request: ChatRequest, prepared: Prepared) -> None:
        stops = StopFilter(request.options.stop)
        parts: list[str] = []

        def on_text(text: str) -> bool:
            parts.append(stops.push(text))
            return not stops.stopped

        result = generator.run(prepared, on_text)
        parts.append(stops.flush())
        finish = "stop" if stops.stopped else result.finish_reason
        usage = usage_body(prepared.prompt_tokens, result.completion_tokens)
        cid, created = f"chatcmpl-{uuid.uuid4().hex}", int(time.time())
        body = completion_body(cid, created, self.app.served_model, "".join(parts), finish, usage)
        self._send_json(200, body)

    def _stream(self, generator: Generator, request: ChatRequest, prepared: Prepared) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        cid, created, model = (
            f"chatcmpl-{uuid.uuid4().hex}",
            int(time.time()),
            self.app.served_model,
        )
        stops = StopFilter(request.options.stop)
        connected = True

        def send(data: str) -> bool:
            nonlocal connected
            if connected:
                try:
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                except OSError:
                    connected = False  # the client went away: stop generating
            return connected

        def send_delta(delta: dict[str, Any], finish: str | None = None) -> bool:
            return send(json.dumps(chunk_body(cid, created, model, delta, finish)))

        def on_text(text: str) -> bool:
            piece = stops.push(text)
            if piece:
                send_delta({"content": piece})
            return connected and not stops.stopped

        send_delta({"role": "assistant", "content": ""})
        try:
            result = generator.run(prepared, on_text)
        except Exception as exc:  # noqa: BLE001 - the status line is gone; report in-band
            traceback.print_exc()
            sys.stdout.flush()
            send(json.dumps(error_body(f"{type(exc).__name__}: {exc}", "server_error", 500)))
            send("[DONE]")
            return
        tail = stops.flush()
        if tail:
            send_delta({"content": tail})
        send_delta({}, "stop" if stops.stopped else result.finish_reason)
        if request.include_usage:
            usage = usage_body(prepared.prompt_tokens, result.completion_tokens)
            send(json.dumps({**chunk_body(cid, created, model, {}), "choices": [], "usage": usage}))
        send("[DONE]")


# -- the transformers backend -------------------------------------------------------------------


def _device(torch: Any, wanted: str) -> str:
    if wanted != "auto":
        return wanted
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


class TransformersGenerator:
    """Runs a causal language model with transformers and PyTorch, imported only here."""

    def __init__(
        self,
        model: str,
        *,
        device: str = "auto",
        dtype: str = "auto",
        ctx_size: int | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        trust_remote_code: bool = False,
    ) -> None:
        try:
            torch = importlib.import_module("torch")
            transformers = importlib.import_module("transformers")
        except ImportError as exc:
            raise RuntimeError(
                f"{exc}: install transformers and torch in this interpreter's environment"
            ) from exc
        self.torch = torch
        self.transformers = transformers
        common = {"local_files_only": True, "trust_remote_code": trust_remote_code}
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(model, **common)
        # transformers 5 renamed `torch_dtype` to `dtype`.
        major = int(str(transformers.__version__).split(".", 1)[0])
        dtype_key = "dtype" if major >= 5 else "torch_dtype"
        chosen = dtype if dtype == "auto" else getattr(torch, dtype)
        self.model = transformers.AutoModelForCausalLM.from_pretrained(
            model, **common, **{dtype_key: chosen}
        )
        self.device = _device(torch, device)
        self.model.to(self.device)
        self.model.eval()
        self.ctx_size = ctx_size or getattr(self.model.config, "max_position_embeddings", None)
        self.max_tokens = max_tokens
        eos = self.model.generation_config.eos_token_id
        self.eos_ids = set(eos if isinstance(eos, list) else [] if eos is None else [eos])
        self.pad_id = self.tokenizer.pad_token_id
        if self.pad_id is None:
            self.pad_id = min(self.eos_ids) if self.eos_ids else None

    def prepare(self, messages: list[dict[str, Any]], options: GenerateOptions) -> Prepared:
        if not getattr(self.tokenizer, "chat_template", None):
            raise RequestError("the model's tokenizer has no chat template")
        try:
            prompt = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True, **options.template_kwargs
            )
        except Exception as exc:  # noqa: BLE001 - template errors are request errors
            raise RequestError(f"the chat template failed: {exc}") from exc
        encoded = self.tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
        count = int(encoded["input_ids"].shape[-1])
        limit = options.max_tokens or self.max_tokens
        if self.ctx_size:
            if count >= self.ctx_size:
                raise RequestError(
                    f"the prompt has {count} tokens; the context size is {self.ctx_size}",
                    kind="exceed_context_size_error",
                )
            limit = min(limit, self.ctx_size - count)
        return Prepared(count, limit, (encoded, options))

    def run(self, prepared: Prepared, on_text: Callable[[str], bool]) -> Generated:
        torch, transformers = self.torch, self.transformers
        encoded, options = prepared.payload
        stop = threading.Event()

        class _Stop(transformers.StoppingCriteria):  # type: ignore[misc, name-defined]
            def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> Any:
                return torch.full(
                    (input_ids.shape[0],), stop.is_set(), dtype=torch.bool, device=input_ids.device
                )

        streamer = transformers.TextIteratorStreamer(
            self.tokenizer, skip_prompt=True, skip_special_tokens=True
        )
        kwargs: dict[str, Any] = {
            "input_ids": encoded["input_ids"].to(self.device),
            "max_new_tokens": prepared.max_tokens,
            "streamer": streamer,
            "stopping_criteria": transformers.StoppingCriteriaList([_Stop()]),
            "pad_token_id": self.pad_id,
        }
        if "attention_mask" in encoded:
            kwargs["attention_mask"] = encoded["attention_mask"].to(self.device)
        if options.temperature:
            kwargs.update(do_sample=True, temperature=options.temperature)
            if options.top_p is not None:
                kwargs["top_p"] = options.top_p
        else:
            kwargs["do_sample"] = False
        if options.seed is not None:
            torch.manual_seed(options.seed)
        outcome: dict[str, Any] = {}

        def work() -> None:
            try:
                with torch.inference_mode():
                    outcome["output"] = self.model.generate(**kwargs)
            except BaseException as exc:  # noqa: BLE001 - handed to the request thread
                outcome["error"] = exc
                streamer.end()

        thread = threading.Thread(target=work, name="generate", daemon=True)
        thread.start()
        for piece in streamer:
            if piece and not stop.is_set() and not on_text(piece):
                stop.set()
        thread.join()
        if "error" in outcome:
            raise outcome["error"]
        new_tokens = outcome["output"][0][int(encoded["input_ids"].shape[-1]) :]
        count = int(new_tokens.shape[-1])
        ended = count > 0 and int(new_tokens[-1]) in self.eos_ids
        finish = "length" if count >= prepared.max_tokens and not ended else "stop"
        return Generated(count, finish)


def transformers_factory(args: argparse.Namespace) -> Generator:
    return TransformersGenerator(
        args.model,
        device=args.device,
        dtype=args.dtype,
        ctx_size=args.ctx_size,
        max_tokens=args.max_tokens,
        trust_remote_code=args.trust_remote_code,
    )


# -- entry point --------------------------------------------------------------------------------


def _json_object(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("expected a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transformers_server",
        description="Serve one local model over chat-completions with transformers.",
    )
    parser.add_argument("--model", required=True, help="local model directory or cached id")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--served-model-name", help="model name reported to clients")
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, cuda:0, mps, ...")
    parser.add_argument("--dtype", default="auto", choices=DTYPES)
    parser.add_argument("--ctx-size", type=int, help="context size (default: the model's)")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="completion tokens when a request sets no limit",
    )
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument(
        "--chat-template-kwargs",
        type=_json_object,
        default=None,
        help="default chat template arguments, e.g. '{\"enable_thinking\": false}'",
    )
    return parser


def _load(server: ChatServer, factory: GeneratorFactory, args: argparse.Namespace) -> None:
    try:
        generator = factory(args)
    except BaseException as exc:  # noqa: BLE001 - any failure ends the server
        server.failure = f"{type(exc).__name__}: {exc}"
        print(f"error: failed to load {args.model}: {server.failure}", flush=True)
        traceback.print_exc()
        sys.stdout.flush()
        server.shutdown()
        return
    server.generator = generator
    print(f"model loaded: {args.model}", flush=True)


def configure_output() -> None:
    """UTF-8, line-buffered output for the server log (isolated mode ignores PYTHONIOENCODING
    and PYTHONUNBUFFERED, and a log file would otherwise get the locale's encoding)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def main(argv: Sequence[str] | None = None, *, factory: GeneratorFactory | None = None) -> int:
    """Bind, load the model in the background (`/health` answers 503 meanwhile), and serve until
    stopped. Returns 1 when the port cannot be bound or the model cannot be loaded."""
    args = build_parser().parse_args(argv)
    try:
        server = ChatServer(
            (args.host, args.port),
            served_model=args.served_model_name or args.model,
            api_key=os.environ.get(API_KEY_ENV),
            template_kwargs=args.chat_template_kwargs,
        )
    except OSError as exc:
        print(f"error: couldn't bind {args.host}:{args.port}: {exc}", flush=True)
        return 1
    print(
        f"stepswitch transformers server pid={os.getpid()} "
        f"listening on http://{args.host}:{server.server_port}",
        flush=True,
    )
    loader = threading.Thread(
        target=_load, args=(server, factory or transformers_factory, args), daemon=True
    )
    loader.start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 1 if server.failure else 0


if __name__ == "__main__":
    configure_output()
    sys.exit(main())
