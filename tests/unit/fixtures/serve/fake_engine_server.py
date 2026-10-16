"""A stand-in for the vLLM, SGLang and MLX-LM servers used by the serve tests (stdlib only).

The flavor follows from the command line, as with the real launchers: `serve <model>` is vLLM,
`--model-path` is SGLang, and `--model` is MLX-LM. Each flavor mimics what its adapter relies on:

* vLLM: listens only after "loading" (connections are refused until then); `/health` and
  `/metrics` are public, `/v1/...` needs the key from `VLLM_API_KEY`.
* SGLang: listens at once and answers `/ready` and `/health` with 503 while loading; `/metrics`
  exists only with `--enable-metrics`; everything except `/health`, `/ready` and `/metrics`
  needs the key from the `--config` YAML file (`api-key: "..."`).
* MLX-LM: listens at once and answers `/health` with 200 even while loading (like the real
  server); `/v1/completions` waits until the model is loaded; no authentication.

It prints one line with its pid, whether it got a key, its argv and the environment variables
the adapters set, so tests can check what the child received. Environment variables steer it:

* FAKE_ENGINE_LOAD_SECONDS: time spent "loading" (default 0.2)
* FAKE_ENGINE_FAIL: print this message as an error and exit with status 1 before binding
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REPORTED_ENV = (
    "HF_HUB_OFFLINE",
    "HF_HUB_DISABLE_TELEMETRY",
    "DO_NOT_TRACK",
    "VLLM_NO_USAGE_STATS",
    "VLLM_DO_NOT_TRACK",
    "VLLM_USE_MODELSCOPE",
    "SGLANG_USE_MODELSCOPE",
    "FAKE_CONFIGURED",
)
KEY_ENV = ("VLLM_API_KEY",)
PUBLIC = {
    "vllm": ("/health", "/metrics", "/version", "/ping"),
    "sglang": ("/health", "/ready", "/metrics"),
}

VLLM_METRICS = """\
# HELP vllm:prefix_cache_queries_total Prefix cache queries, in terms of number of queried tokens.
# TYPE vllm:prefix_cache_queries_total counter
vllm:prefix_cache_queries_total{engine="0",model_name="%(name)s"} 400.0
vllm:prefix_cache_queries_created{engine="0",model_name="%(name)s"} 1.7e+09
vllm:prefix_cache_hits_total{engine="0",model_name="%(name)s"} 300.0
vllm:prompt_tokens_total{engine="0",model_name="%(name)s"} 400.0
vllm:prompt_tokens_cached_total{engine="0",model_name="%(name)s"} 300.0
vllm:generation_tokens_total{engine="0",model_name="%(name)s"} 25.0
"""
SGLANG_METRICS = """\
# HELP sglang:prompt_tokens_total Number of prefill tokens processed.
# TYPE sglang:prompt_tokens_total counter
sglang:prompt_tokens_total{model_name="%(name)s"} 200.0
sglang:cached_tokens_total{model_name="%(name)s"} 150.0
sglang:generation_tokens_total{model_name="%(name)s"} 12.0
sglang:cache_hit_rate{model_name="%(name)s"} 0.75
"""


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("subcommand", nargs="?")
    parser.add_argument("model_tag", nargs="?")
    parser.add_argument("--model-path")
    parser.add_argument("--model")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--api-key")
    parser.add_argument("--config")
    parser.add_argument("--served-model-name")
    parser.add_argument("--enable-metrics", action="store_true")
    return parser.parse_known_args(argv)


def flavor_of(args: argparse.Namespace) -> str:
    if args.subcommand == "serve":
        return "vllm"
    return "sglang" if args.model_path else "mlx"


def config_key(path: str | None) -> str | None:
    """The `api-key` of an SGLang YAML configuration file (the adapter writes a JSON string)."""
    if not path:
        return None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip() == "api-key":
            return str(json.loads(value.strip()))
    return None


def key_of(flavor: str, args: argparse.Namespace) -> str | None:
    if flavor == "vllm":
        return args.api_key or os.environ.get("VLLM_API_KEY")
    if flavor == "sglang":
        return args.api_key or config_key(args.config)
    return None  # MLX-LM has no authentication


def make_handler(
    flavor: str, args: argparse.Namespace, key: str | None, loaded: threading.Event
) -> type:
    name = args.served_model_name or "default_model"

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

        def authorized(self, path: str) -> bool:
            if not key:
                return True
            if flavor == "vllm" and not path.startswith(("/v1", "/v2", "/inference")):
                return True
            if path in PUBLIC.get(flavor, ()):
                return True
            return self.headers.get("Authorization") == f"Bearer {key}"

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if not self.authorized(path):
                self.reply(401, {"error": "Unauthorized"})
            elif path == "/health" or (path == "/ready" and flavor == "sglang"):
                ready = loaded.is_set() or (flavor == "mlx" and path == "/health")
                self.reply(200 if ready else 503, {"status": "ok" if ready else "loading"})
            elif path == "/metrics" and flavor == "vllm":
                self.reply(200, VLLM_METRICS % {"name": name}, "text/plain; version=0.0.4")
            elif path == "/metrics" and flavor == "sglang" and args.enable_metrics:
                self.reply(200, SGLANG_METRICS % {"name": name}, "text/plain; version=0.0.4")
            elif path == "/v1/models":
                self.reply(200, {"object": "list", "data": [{"id": name}]})
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urlsplit(self.path).path
            if not self.authorized(path):
                self.reply(401, {"error": "Unauthorized"})
                return
            if path not in ("/v1/completions", "/v1/chat/completions"):
                self.reply(404, {"error": "not found"})
                return
            loaded.wait()  # requests wait for the model, as on MLX-LM
            print(f"generate model={body.get('model')}", flush=True)
            usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            self.reply(200, {"model": body.get("model"), "choices": [], "usage": usage})

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # never share a port that is in use (Windows would allow it)


def main(argv: list[str]) -> int:
    args, rest = parse(argv)
    flavor = flavor_of(args)
    key = key_of(flavor, args)
    pairs = zip(["", *argv], argv, strict=False)
    shown = ["***" if before == "--api-key" else arg for before, arg in pairs]
    env = {name: os.environ.get(name) for name in REPORTED_ENV}
    env |= {name: "set" if os.environ.get(name) else None for name in KEY_ENV}
    print(
        f"fake engine flavor={flavor} pid={os.getpid()} key={'set' if key else 'none'} "
        f"argv={json.dumps(shown)} env={json.dumps(env, sort_keys=True)}",
        flush=True,
    )
    failure = os.environ.get("FAKE_ENGINE_FAIL")
    if failure:
        print(f"error: {failure}", flush=True)
        return 1
    loaded = threading.Event()
    load_seconds = float(os.environ.get("FAKE_ENGINE_LOAD_SECONDS", "0.2"))
    if flavor == "vllm":
        time.sleep(load_seconds)  # vLLM listens only once the model is loaded
        loaded.set()
    else:
        threading.Timer(load_seconds, loaded.set).start()
    try:
        server = Server((args.host, args.port), make_handler(flavor, args, key, loaded))
    except OSError as exc:
        print(f"error while attempting to bind on address: address already in use ({exc})")
        return 1
    print(f"listening on http://{args.host}:{args.port} (ignored: {rest})", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
