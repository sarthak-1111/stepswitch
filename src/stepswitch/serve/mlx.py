"""The MLX-LM adapter (SPEC §4.2, §5.3; ADR 0008).

`mlx_lm.server` on arm64 macOS, one model per process: the console script `mlx_lm.server`, or
`python -m mlx_lm server` with a configured interpreter (`python -m mlx_lm.server` is
deprecated). The command line is `--model` with the model source, the recipe's `args` (plus
extra arguments), then the flags the adapter owns: the loopback host, the port, and
`--allowed-origins` set to the server's own origin, so that web pages on other origins get no
cross-origin approval (the server's default is `*`).

What the server lacks shapes the adapter (checked against the v0.32.2 source):

* No authentication: any local process can reach it, so ServerInfo carries no key.
* Clients must send the model name `default_model` (ServerInfo.engine_model). Any other name
  makes the server drop its model and load that one instead, from the local cache only here
  because downloads are switched off.
* It fetches http(s) image URLs found in chat messages itself, so callers must not send any.
* `/health` answers 200 as soon as the generation thread runs, before the model has loaded.
  `health` therefore also asks for one completion token, which waits for the load, and
  remembers the first success per server process.
* No context-size flag (the KV cache grows as needed), so a context size is ignored. No metrics
  endpoint (cache reuse is reported per response as `cached_tokens`). No tool or reasoning
  parser flags (the tokenizer's own parsers apply) and no constrained decoding.

MLX-LM also declares Linux builds; only arm64 macOS is supported here (ADR 0008).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from stepswitch.registry import ModelEntry
from stepswitch.serve.base import LOOPBACK, ServerInfo
from stepswitch.serve.pyserver import OWNED, PythonServerEngine, check_args

ENGINE = "mlx"
DEFAULT_MODEL = "default_model"
READY_TIMEOUT_S = 5.0

FORBIDDEN_FLAGS = {
    "--model": "the model comes from the registry entry",
    "--host": OWNED,
    "--port": OWNED,
    "--allowed-origins": OWNED,
}


class MlxEngine(PythonServerEngine):
    """Starts and controls `mlx_lm.server` processes."""

    name = ENGINE
    label = "MLX-LM"
    dist = "mlx-lm"
    script = "mlx_lm.server"
    module_args = ("-m", "mlx_lm", "server")
    install_hint = (
        "install mlx-lm in its own environment and set [engines.mlx] python in "
        ".stepswitch/local.toml, or put `mlx_lm.server` on PATH"
    )
    authenticates = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._loaded: set[tuple[int | None, str | None, int | None]] = set()

    def platform_problem(self, system: str, machine: str) -> str | None:
        if system == "darwin" and machine.lower() in ("arm64", "aarch64"):
            return None
        if system == "darwin":
            return "MLX-LM needs an arm64 Mac; this Mac has an x86-64 CPU"
        return f"MLX-LM is supported on arm64 macOS only (this host is {system} {machine})"

    def served_name(self, entry: ModelEntry) -> str:
        return DEFAULT_MODEL

    def command(
        self,
        entry: ModelEntry,
        model: str,
        *,
        port: int,
        ctx_size: int | None = None,
        extra_args: Sequence[str] = (),
    ) -> list[str]:
        """`mlx_lm.server --model <model>` + recipe args (+ extra args) + the owned flags.
        `ctx_size` does not apply: the server has no context-size option."""
        recipe = entry.engines.get(ENGINE)
        recipe_args = list(recipe.args) if recipe else []
        check_args(recipe_args, FORBIDDEN_FLAGS, f"the MLX-LM recipe of {entry.id}")
        check_args(extra_args, FORBIDDEN_FLAGS, "extra server arguments")
        origin = f"http://{LOOPBACK}:{port}"
        return [
            *self.launcher(),
            "--model",
            model,
            *recipe_args,
            *extra_args,
            "--host",
            LOOPBACK,
            "--port",
            str(port),
            "--allowed-origins",
            origin,
        ]

    def health(self, info: ServerInfo) -> bool:
        """GET /health, then one completion token the first time (it waits for the load)."""
        if not super().health(info):
            return False
        key = (info.pid, info.process_start, info.port)
        if key in self._loaded:
            return True
        response = self.request(
            info,
            "POST",
            "/v1/completions",
            timeout=READY_TIMEOUT_S,
            json={"model": DEFAULT_MODEL, "prompt": "ping", "max_tokens": 1},
        )
        if response is None or response.status_code != 200:
            return False
        self._loaded.add(key)
        return True
