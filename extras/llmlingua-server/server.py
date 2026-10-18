"""Reference prompt-compression server for Stepswitch (SPEC §13 TK11, ADR 0024).

Runs the small LLMLingua-2 checkpoint on the CPU from a local directory, in its own process and
its own environment, so torch and llmlingua never become Stepswitch dependencies. Stepswitch
talks to it over HTTP through `stepswitch.net` (`stepswitch.context.compress.HttpCompressor`):

    POST /compress  {"text": str, "rate": float, "force_tokens": [str]}  ->  {"text": str}
    GET  /health                                                          ->  {"status": "ok"}

`rate` is the share of tokens to keep (0 < rate <= 1). Errors answer {"error": str} with a 4xx
or 5xx status; request text is never logged.

Offline by default: the model must be a local directory, the model-hub libraries run in offline
mode, and the process refuses connections and name lookups for anything but loopback
addresses. The LLMLingua constructor loads a BPE file through tiktoken, so run `--prefetch`
once (downloads allowed, loads the model, compresses one sentence, exits) before serving. Set
TIKTOKEN_CACHE_DIR to keep that file in a known place:

    MODEL=/models/llmlingua-2-bert-base-multilingual-cased-meetingbank
    python server.py --model-path $MODEL --prefetch
    python server.py --model-path $MODEL --port 8095

Only the standard library is imported at module level; `llmlingua` is imported when the model
loads.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
import threading
from collections.abc import Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8095
MAX_BODY_BYTES = 4 * 1024 * 1024
# An oversized body is read and dropped up to this size before the refusal is sent.
DRAIN_LIMIT_BYTES = 64 * 1024 * 1024
MAX_FORCE_TOKENS = 32
MAX_FORCE_TOKEN_CHARS = 16
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
PREFETCH_TEXT = "Stepswitch checks that the compression model loads and answers offline."
# llmlingua 0.2.2 picks how tokens join into words from the model name (`utils.
# is_begin_of_new_word`), and raises NotImplementedError on every request for any other name.
WORD_RULES = ("bert-base-multilingual-cased", "xlm-roberta-large")


class Backend(Protocol):
    name: str

    def compress(self, text: str, rate: float, force_tokens: Sequence[str]) -> str: ...


def name_problem(model_path: Path) -> str | None:
    """Why llmlingua cannot use a checkpoint stored under this directory name, or None."""
    if any(rule in model_path.name for rule in WORD_RULES):
        return None
    return (
        f"name the checkpoint directory after the checkpoint (it must contain"
        f" {' or '.join(WORD_RULES)}): llmlingua chooses its word rules by that name"
    )


class LinguaBackend:
    """LLMLingua-2 through `llmlingua.PromptCompressor` (checked against llmlingua 0.2.2)."""

    def __init__(self, model_path: Path, *, device: str = "cpu") -> None:
        problem = name_problem(model_path)
        if problem:
            raise ValueError(problem)
        from llmlingua import PromptCompressor  # type: ignore[import-not-found]  # torch here only

        self.name = model_path.name
        # One model object serves every request thread.
        self._lock = threading.Lock()
        self._compressor = PromptCompressor(
            model_name=str(model_path),
            device_map=device,
            # The library defaults to running code shipped with a checkpoint; this one has none.
            model_config={"trust_remote_code": False},
            use_llmlingua2=True,
        )

    def compress(self, text: str, rate: float, force_tokens: Sequence[str]) -> str:
        with self._lock:
            result = self._compressor.compress_prompt(
                [text], rate=rate, force_tokens=list(force_tokens)
            )
        return str(result["compressed_prompt"])


def is_loopback(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


class OfflineError(OSError):
    pass


class OfflineGuard:
    """Refuses outbound connections and name lookups for anything but loopback addresses."""

    METHODS = ("connect", "connect_ex")

    def __init__(self) -> None:
        self._saved: dict[str, Any] | None = None

    def install(self) -> None:
        if self._saved is not None:
            return
        # What `socket.socket` defines itself (the methods usually come from the C base class).
        self._saved = {name: vars(socket.socket).get(name) for name in self.METHODS}
        self._saved["getaddrinfo"] = socket.getaddrinfo
        connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
        getaddrinfo = socket.getaddrinfo

        def check(address: Any) -> None:
            # AF_UNIX addresses are paths, local by definition.
            if isinstance(address, tuple) and not is_loopback(str(address[0])):
                raise OfflineError(refusal(str(address[0])))

        def guarded_connect(sock: Any, address: Any) -> None:
            check(address)
            connect(sock, address)

        def guarded_connect_ex(sock: Any, address: Any) -> int:
            check(address)
            return connect_ex(sock, address)

        def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            name = host.decode() if isinstance(host, bytes) else host
            if name is not None and not is_loopback(str(name)):
                raise OfflineError(refusal(str(name)))
            return getaddrinfo(host, *args, **kwargs)

        socket.socket.connect = guarded_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = guarded_getaddrinfo

    def uninstall(self) -> None:
        if self._saved is None:
            return
        for name in self.METHODS:
            if self._saved[name] is None:
                delattr(socket.socket, name)
            else:
                setattr(socket.socket, name, self._saved[name])
        socket.getaddrinfo = self._saved["getaddrinfo"]
        self._saved = None


def refusal(host: str) -> str:
    return (
        f"llmlingua-server is offline and refused a connection to {host}; run it once with"
        " --prefetch to fetch the files it needs"
    )


def parse_request(body: Any) -> tuple[str, float, list[str]]:
    """(text, rate, force_tokens) of a /compress body; ValueError names the first problem."""
    if not isinstance(body, dict):
        raise ValueError("the body must be a JSON object")
    text = body.get("text")
    if not isinstance(text, str):
        raise ValueError("`text` must be a string")
    rate = body.get("rate", 0.5)
    if isinstance(rate, bool) or not isinstance(rate, int | float) or not 0 < rate <= 1:
        raise ValueError("`rate` must be a number in (0, 1]")
    force = body.get("force_tokens", [])
    if (
        not isinstance(force, list)
        or len(force) > MAX_FORCE_TOKENS
        or not all(isinstance(t, str) and 0 < len(t) <= MAX_FORCE_TOKEN_CHARS for t in force)
    ):
        raise ValueError(
            f"`force_tokens` must be a list of at most {MAX_FORCE_TOKENS} short strings"
        )
    return text, float(rate), force


def make_handler(
    backend: Backend, *, max_body: int = MAX_BODY_BYTES
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "stepswitch-llmlingua-server"

        def log_message(self, format: str, *args: Any) -> None:
            return  # request lines only; text never reaches a log

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(HTTPStatus.OK, {"status": "ok", "model": backend.name})
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = -1
            if length < 0 or length > max_body:
                # Read what was sent first: closing on unread data resets the connection, and
                # the client would see that instead of the answer.
                self._discard(min(max(length, 0), DRAIN_LIMIT_BYTES))
                self.close_connection = True
                self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"})
                return
            raw = self.rfile.read(length)
            if self.path != "/compress":
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                body = json.loads(raw.decode("utf-8"))
                text, rate, force = parse_request(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(HTTPStatus.BAD_REQUEST, {"error": "the body is not JSON"})
                return
            except ValueError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if not text.strip():
                self._send(HTTPStatus.OK, {"text": text})
                return
            try:
                answer = backend.compress(text, rate, force)
            except Exception as exc:  # noqa: BLE001 - the client gets a status, not a trace
                self._send(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": f"compression failed: {type(exc).__name__}"},
                )
                return
            self._send(HTTPStatus.OK, {"text": answer})

        def _discard(self, length: int) -> None:
            while length > 0:
                chunk = self.rfile.read(min(length, 65536))
                if not chunk:
                    return
                length -= len(chunk)

        def _send(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def create_server(
    backend: Backend, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(backend))
    server.daemon_threads = True
    return server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--model-path", type=Path, required=True, help="local checkpoint dir")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--prefetch", action="store_true", help="allow downloads, load and test once, exit"
    )
    parser.add_argument(
        "--allow-downloads", action="store_true", help="serve without the offline guard"
    )
    args = parser.parse_args(argv)
    path = args.model_path.expanduser()
    if not (path / "config.json").is_file():
        parser.error(f"{path} is not a local model directory (no config.json)")
    problem = name_problem(path)
    if problem:
        parser.error(problem)
    if not (args.prefetch or args.allow_downloads):
        os.environ.update(OFFLINE_ENV)
        OfflineGuard().install()
    backend = LinguaBackend(path, device=args.device)
    if args.prefetch:
        backend.compress(PREFETCH_TEXT, 0.5, ["\n"])
        print("prefetch done: the server can now run offline", file=sys.stderr)
        return 0
    if not is_loopback(args.host):
        print(
            f"warning: listening on {args.host}; other machines can send text to compress",
            file=sys.stderr,
        )
    server = create_server(backend, args.host, args.port)
    print(f"llmlingua-server: {backend.name} on {args.host}:{server.server_port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
