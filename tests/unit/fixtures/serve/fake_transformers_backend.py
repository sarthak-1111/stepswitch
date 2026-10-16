"""Runs the real transformers wrapper server with a fake generator (stdlib only, no torch).

The wrapper file is loaded by its path, as the adapter runs it (`python -I`), so this also shows
that it works without the stepswitch package. The fake generator echoes the last message word by
word. It prints one line with its pid, the model it was given and the environment variables the
adapter sets. Environment variables steer it:

* FAKE_TRANSFORMERS_LOAD_SECONDS: time spent "loading" (default 0.3)
* FAKE_TRANSFORMERS_FAIL: raise this message while loading
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SERVER = Path(__file__).resolve().parents[4] / "src" / "stepswitch" / "serve"
SERVER_FILE = SERVER / "transformers_server.py"
REPORTED_ENV = ("HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK")


def load_wrapper() -> Any:
    spec = importlib.util.spec_from_file_location("transformers_server", SERVER_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wrapper = load_wrapper()


class EchoGenerator:
    def prepare(self, messages: list[dict[str, Any]], options: Any) -> Any:
        words = f"echo: {messages[-1]['content']}".split(" ")
        prompt = sum(len(str(message["content"]).split()) for message in messages)
        return wrapper.Prepared(prompt, options.max_tokens or 64, words)

    def run(self, prepared: Any, on_text: Callable[[str], bool]) -> Any:
        count = 0
        for index, word in enumerate(prepared.payload[: prepared.max_tokens]):
            count += 1
            if not on_text(word if index == 0 else f" {word}"):
                break
        return wrapper.Generated(count, "stop" if count == len(prepared.payload) else "length")


def factory(args: argparse.Namespace) -> EchoGenerator:
    env = {name: os.environ.get(name) for name in REPORTED_ENV}
    env["key"] = "set" if os.environ.get(wrapper.API_KEY_ENV) else None
    print(f"fake backend pid={os.getpid()} model={args.model} env={json.dumps(env)}", flush=True)
    time.sleep(float(os.environ.get("FAKE_TRANSFORMERS_LOAD_SECONDS", "0.3")))
    failure = os.environ.get("FAKE_TRANSFORMERS_FAIL")
    if failure:
        raise RuntimeError(failure)
    return EchoGenerator()


if __name__ == "__main__":
    wrapper.configure_output()
    sys.exit(wrapper.main(sys.argv[1:], factory=factory))
