"""The vLLM adapter (SPEC §4.2, §5.3; ADR 0008).

`vllm serve <model> ...` from the engine's own environment, one model per process. The command
line is the recipe's `args` (plus extra arguments), then the flags the adapter owns: the
loopback host (vLLM listens on every interface without `--host`), the port, the served model
name (the registry id) and the parser flags from the recipe fields `tool_parser`
(`--enable-auto-tool-choice --tool-call-parser`) and `reasoning_parser`. The model comes right
after `serve`, as vLLM requires. A context size from the caller becomes `--max-model-len`.

The API key travels in `VLLM_API_KEY`, never on the command line. vLLM checks it only on its
inference route prefixes (`/v1`, `/v2`, `/inference`, ...); `/health`, `/metrics`, `/version`,
`/ping` and `/invocations` stay open to local processes. Usage statistics are switched off with
`VLLM_NO_USAGE_STATS=1`, `VLLM_DO_NOT_TRACK=1` and `DO_NOT_TRACK=1` (there is no flag for it),
downloads with `HF_HUB_OFFLINE=1`, and the alternative model source with
`VLLM_USE_MODELSCOPE=False`.

Readiness: vLLM binds the port before it loads the model but only listens afterwards, so
`/health` refuses connections while loading, answers 200 when ready and 503 once the engine
died (the process then exits). `/metrics` is always on; `prefix_cache_stats` reads the
`vllm:prefix_cache_queries` and `vllm:prefix_cache_hits` counters (in tokens).

vLLM runs on Linux only (x86-64 and aarch64 wheels); on Windows it runs inside WSL2 or a
container and is added as an endpoint. Facts checked against the v0.31.0 source.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from stepswitch.registry import EngineRecipe, ModelEntry
from stepswitch.serve.base import LOOPBACK, ServerInfo
from stepswitch.serve.pyserver import (
    FROM_RECIPE,
    OWNED,
    PythonServerEngine,
    check_args,
    counter_total,
    with_flag,
)

ENGINE = "vllm"
API_KEY_ENV = "VLLM_API_KEY"
CTX_FLAGS = ("--max-model-len",)
LINUX_MACHINES = frozenset({"x86_64", "amd64", "aarch64", "arm64"})

FORBIDDEN_FLAGS = {
    "--host": OWNED,
    "--port": OWNED,
    "--api-key": "the key travels in VLLM_API_KEY",
    "--served-model-name": OWNED,
    "--model": "the model comes from the registry entry",
    "--enable-auto-tool-choice": FROM_RECIPE,
    "--tool-call-parser": FROM_RECIPE,
    "--reasoning-parser": FROM_RECIPE,
    "--disable-log-stats": "it removes the /metrics counters the server manager reads",
}


def parser_flags(recipe: EngineRecipe | None) -> list[str]:
    """`--enable-auto-tool-choice --tool-call-parser <name>` and `--reasoning-parser <name>`."""
    flags: list[str] = []
    if recipe is not None and recipe.tool_parser:
        flags += ["--enable-auto-tool-choice", "--tool-call-parser", recipe.tool_parser]
    reasoning = recipe.reasoning_parser if recipe is not None else None
    if reasoning:
        flags += ["--reasoning-parser", reasoning]
    return flags


class VllmEngine(PythonServerEngine):
    """Starts and controls `vllm serve` processes."""

    name = ENGINE
    label = "vLLM"
    dist = "vllm"
    script = "vllm"
    module_args = ("-m", "vllm.entrypoints.cli.main")
    install_hint = (
        "install vLLM in its own environment and set [engines.vllm] python in "
        ".stepswitch/local.toml, or put `vllm` on PATH"
    )
    api_key_env = API_KEY_ENV
    fixed_env = {
        "VLLM_NO_USAGE_STATS": "1",
        "VLLM_DO_NOT_TRACK": "1",
        "VLLM_USE_MODELSCOPE": "False",
    }
    metrics_path = "/metrics"

    def platform_problem(self, system: str, machine: str) -> str | None:
        if system == "win32":
            return (
                "vLLM does not run natively on Windows; run it in WSL2 or a Linux container "
                "and add it as an endpoint (`ssw models add <url> --engine vllm`)"
            )
        if not system.startswith("linux"):
            return f"vLLM runs on Linux only (this host is {system}); use MLX-LM on arm64 macOS"
        if machine.lower() not in LINUX_MACHINES:
            return f"vLLM publishes no Linux wheels for the CPU architecture {machine}"
        return None

    def command(
        self,
        entry: ModelEntry,
        model: str,
        *,
        port: int,
        ctx_size: int | None = None,
        extra_args: Sequence[str] = (),
    ) -> list[str]:
        """`vllm serve <model>` + recipe args (+ extra args) + the flags the adapter owns."""
        recipe = entry.engines.get(ENGINE)
        recipe_args = list(recipe.args) if recipe else []
        check_args(recipe_args, FORBIDDEN_FLAGS, f"the vLLM recipe of {entry.id}")
        check_args(extra_args, FORBIDDEN_FLAGS, "extra server arguments")
        args = [*recipe_args, *extra_args]
        if ctx_size is not None:
            args = with_flag(args, CTX_FLAGS, str(ctx_size))
        cmd = [*self.launcher(), "serve", model, *args]
        cmd += ["--host", LOOPBACK, "--port", str(port), "--served-model-name", entry.id]
        return cmd + parser_flags(recipe)

    def prefix_cache_stats(self, info: ServerInfo) -> dict[str, Any] | None:
        """Prefix-cache counters in tokens, summed over engines; None without metrics."""
        samples = self.metric_samples(info)
        if samples is None:
            return None
        hits = counter_total(samples, "vllm:prefix_cache_hits")
        queries = counter_total(samples, "vllm:prefix_cache_queries")
        if hits is None or queries is None:
            return None
        stats: dict[str, Any] = {
            "source": "metrics",
            "hit_tokens": hits,
            "query_tokens": queries,
            "hit_rate": hits / queries if queries else None,
        }
        cached = counter_total(samples, "vllm:prompt_tokens_cached")
        if cached is not None:
            stats["cached_prompt_tokens"] = cached
        external_hits = counter_total(samples, "vllm:external_prefix_cache_hits")
        external_queries = counter_total(samples, "vllm:external_prefix_cache_queries")
        if external_hits is not None and external_queries is not None:
            stats["external_hit_tokens"] = external_hits
            stats["external_query_tokens"] = external_queries
        return stats
