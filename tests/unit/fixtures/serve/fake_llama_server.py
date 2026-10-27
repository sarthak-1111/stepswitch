"""A stand-in for `llama-server` used by the serve tests (standard library only).

It accepts the flags the llama.cpp adapter emits, prints the captured `--version` output of the
real b11669 build, answers `/health` with 503 while "loading" and 200 afterwards (public, like
the real server), and requires the API key (`--api-key` or `LLAMA_API_KEY`, sent as a bearer
token or `X-Api-Key`) on every other endpoint. Environment variables steer the tests:

* FAKE_LLAMA_LOAD_SECONDS: time spent "loading" before /health turns 200 (default 0.2)
* FAKE_LLAMA_READY_FILE: when set, "loading" also lasts until this file exists
* FAKE_LLAMA_FAIL: print this message as an error and exit with status 1 before binding
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION_FILE = Path(__file__).with_name("llama-server-version-b11669.txt")
SLOT_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("-m", "--model")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9931)
    parser.add_argument("--api-key")
    parser.add_argument("--alias", "-a", default="fake")
    parser.add_argument("--slot-save-path")
    parser.add_argument("--metrics", action="store_true")
    parser.add_argument("--ctx-size", "-c", type=int, default=0)
    return parser.parse_known_args(argv)


def loaded(ready_at: float) -> bool:
    gate = os.environ.get("FAKE_LLAMA_READY_FILE")
    return time.monotonic() >= ready_at and (not gate or Path(gate).exists())


def make_handler(args: argparse.Namespace, key: str | None, ready_at: float) -> type:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *items: object) -> None:  # noqa: A002
            sys.stdout.write("request: " + (format % items) + "\n")
            sys.stdout.flush()

        def reply(self, status: int, body: object, content_type: str = "application/json") -> None:
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def error(self, status: int, message: str, kind: str) -> None:
            self.reply(status, {"error": {"code": status, "message": message, "type": kind}})

        def authorized(self) -> bool:
            if not key:
                return True
            bearer = self.headers.get("Authorization", "")
            return bearer == f"Bearer {key}" or self.headers.get("X-Api-Key") == key

        def body(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            data = json.loads(raw or b"{}")
            return data if isinstance(data, dict) else {}

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/health", "/v1/health"):
                if not loaded(ready_at):
                    self.error(503, "Loading model", "unavailable_error")
                else:
                    self.reply(200, {"status": "ok"})
                return
            if not self.authorized():
                self.error(401, "Invalid API Key", "authentication_error")
                return
            if path == "/v1/models":
                self.reply(200, {"object": "list", "data": [{"id": args.alias}]})
            elif path == "/metrics":
                if not args.metrics:
                    self.error(501, "This server does not support metrics endpoint", "x")
                    return
                text = (
                    "# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.\n"
                    "# TYPE llamacpp:prompt_tokens_total counter\n"
                    "llamacpp:prompt_tokens_total 42\n"
                    "llamacpp:tokens_predicted_total 7\n"
                    'llamacpp:spec_decode_num_accepted_tokens_per_pos_total{position="0"} 3\n'
                )
                self.reply(200, text, "text/plain; version=0.0.4")
            elif path == "/slots":
                self.reply(200, [{"id": 0, "n_ctx": args.ctx_size, "is_processing": False}])
            else:
                self.error(404, "File Not Found", "not_found_error")

        def do_POST(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if not self.authorized():
                self.error(401, "Invalid API Key", "authentication_error")
                return
            data = self.body()
            match = re.fullmatch(r"/slots/(\d+)", url.path)
            if match:
                self.slot(int(match.group(1)), parse_qs(url.query).get("action", [""])[0], data)
            elif url.path == "/v1/embeddings":
                vector = [0.1, 0.2, 0.3]
                self.reply(
                    200,
                    {
                        "model": args.alias,
                        "object": "list",
                        "data": [{"embedding": vector, "index": 0, "object": "embedding"}],
                        "usage": {"prompt_tokens": 2, "total_tokens": 2},
                    },
                )
            else:
                self.error(404, "File Not Found", "not_found_error")

        def slot(self, slot_id: int, action: str, data: dict[str, object]) -> None:
            filename = str(data.get("filename", ""))
            if not args.slot_save_path:
                self.error(501, "This server does not support slots action", "x")
                return
            if not SLOT_FILE.match(filename) or ".." in filename:
                self.error(400, "Invalid filename", "invalid_request_error")
                return
            path = Path(args.slot_save_path) / filename
            if action == "save":
                path.write_bytes(b"fake-kv-state")
                self.reply(200, {"id_slot": slot_id, "filename": filename, "n_saved": 3})
            elif action == "restore":
                if not path.is_file():
                    self.error(400, "failed to restore slot", "invalid_request_error")
                    return
                self.reply(200, {"id_slot": slot_id, "filename": filename, "n_restored": 3})
            else:
                self.error(400, "Invalid action", "invalid_request_error")

    return Handler


class Server(ThreadingHTTPServer):
    # Like llama-server: never share a port that another socket already holds (SO_REUSEADDR
    # would allow that on Windows).
    allow_reuse_address = False


def main(argv: list[str]) -> int:
    args, rest = parse(argv)
    if args.version:
        sys.stderr.write(VERSION_FILE.read_text(encoding="utf-8"))
        return 0
    pairs = zip(["", *argv], argv, strict=False)
    shown = ["***" if previous == "--api-key" else arg for previous, arg in pairs]
    print(f"fake llama-server pid={os.getpid()} argv={json.dumps(shown)}", flush=True)
    failure = os.environ.get("FAKE_LLAMA_FAIL")
    if failure:
        print(f"error: {failure}", flush=True)
        return 1
    key = args.api_key or os.environ.get("LLAMA_API_KEY")
    ready_at = time.monotonic() + float(os.environ.get("FAKE_LLAMA_LOAD_SECONDS", "0.2"))
    try:
        server = Server((args.host, args.port), make_handler(args, key, ready_at))
    except OSError as exc:
        print(f"couldn't bind HTTP server socket, hostname: {args.host}, port: {args.port}: {exc}")
        return 1
    print(f"listening on http://{args.host}:{args.port} (ignored: {rest})", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
