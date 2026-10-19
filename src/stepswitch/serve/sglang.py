"""The SGLang adapter (SPEC §4.2, §5.3; ADR 0008).

One SGLang server per model, started as `sglang serve --model-type llm` (the console script; the
explicit model type skips the model-hub lookup that automatic detection makes) or as
`python -m sglang.launch_server` with a configured interpreter (still supported, with a
deprecation warning). The command line is `--model-path` with the model source, the recipe's
`args` (plus extra arguments), then the flags the adapter owns: the loopback host, the port,
the served model name (the registry id), `--enable-metrics`, `--enable-cache-report` (cached
tokens in `usage`), and the parser flags from the recipe fields `tool_parser` and
`reasoning_parser` (SGLang also accepts `auto`). A context size becomes `--context-length`.

SGLang reads its key from no environment variable, so the adapter writes the key to a private
YAML file passed with `--config`, which keeps it out of process listings, and removes the file
when it stops the server. SGLang itself prints its arguments, key included, to its startup log,
which stays in the state directory next to the runtime file that holds the key anyway.
`/health`, `/ready` and `/metrics` need no key.

Readiness: SGLang listens only after the model has loaded, then warms up; `/ready` answers 200
once the server is up, and it is the adapter's health check (`/health` would run a generation on
every call). `prefix_cache_stats` compares the `sglang:cached_tokens` and `sglang:prompt_tokens`
counters (in tokens, summed over their labels) and adds the recent `sglang:cache_hit_rate`.
SGLang sends no usage statistics; downloads are off (`HF_HUB_OFFLINE=1`), and so is its
alternative model source (`SGLANG_USE_MODELSCOPE=false`).

SGLang runs on Linux only (x86-64 and aarch64 wheels; it depends on uvloop, which has no Windows
support). On Windows it runs on a Linux host or in a Linux container (SGLang documents no WSL2
setup) and is added as an endpoint. Facts checked against the v0.5.22 source.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from stepswitch.registry import EngineRecipe, ModelEntry
from stepswitch.serve.base import LOOPBACK, ServerInfo
from stepswitch.serve.pyserver import (
    FROM_RECIPE,
    OWNED,
    PythonServerEngine,
    check_args,
    counter_total,
    gauge_mean,
    with_flag,
)

ENGINE = "sglang"
CTX_FLAGS = ("--context-length",)
KEY_FILE_SUFFIX = ".key.yaml"
LINUX_MACHINES = frozenset({"x86_64", "amd64", "aarch64", "arm64"})

FORBIDDEN_FLAGS = {
    "--model-path": "the model comes from the registry entry",
    "--model": "the model comes from the registry entry",
    "--host": OWNED,
    "--port": OWNED,
    "--api-key": "the key travels in a private --config file",
    "--served-model-name": OWNED,
    "--config": "a configuration file could override the owned flags",
    "--tool-call-parser": FROM_RECIPE,
    "--reasoning-parser": FROM_RECIPE,
}


def parser_flags(recipe: EngineRecipe | None) -> list[str]:
    """`--tool-call-parser <name>` and `--reasoning-parser <name>` from the recipe fields."""
    flags: list[str] = []
    if recipe is not None and recipe.tool_parser:
        flags += ["--tool-call-parser", recipe.tool_parser]
    if recipe is not None and recipe.reasoning_parser:
        flags += ["--reasoning-parser", recipe.reasoning_parser]
    return flags


def key_file(log_path: Path) -> Path:
    """Where the server's key file lives: next to its log, in the state directory."""
    return log_path.with_name(log_path.name + KEY_FILE_SUFFIX)


def write_key_file(path: Path, api_key: str) -> None:
    """A YAML file with only `api-key`, readable by the owner alone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # created anew, so the permissions always apply
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"api-key: {json.dumps(api_key)}\n")  # a JSON string is valid YAML


class SglangEngine(PythonServerEngine):
    """Starts and controls SGLang server processes."""

    name = ENGINE
    label = "SGLang"
    dist = "sglang"
    dist_aliases: tuple[str, ...] = ("sglang-cpu",)
    script: str | None = "sglang"
    script_args: tuple[str, ...] = ("serve", "--model-type", "llm")
    module_args = ("-m", "sglang.launch_server")
    install_hint = (
        "install SGLang in its own environment and set [engines.sglang] python in "
        ".stepswitch/local.toml, or put `sglang` on PATH"
    )
    api_key_env = None  # no key variable exists; `key_args` writes a --config file
    fixed_env = {"SGLANG_USE_MODELSCOPE": "false"}
    health_path = "/ready"
    metrics_path = "/metrics"

    def platform_problem(self, system: str, machine: str) -> str | None:
        if system == "win32":
            return (
                "SGLang does not run natively on Windows; run it on a Linux host or in a Linux "
                "container (SGLang documents no WSL2 setup) and add it as an endpoint "
                "(`ssw models add <url> --engine sglang`)"
            )
        if system == "darwin":
            return "SGLang's macOS runtime needs a source install; use MLX-LM on arm64 macOS"
        if not system.startswith("linux"):
            return f"SGLang runs on Linux only (this host is {system})"
        if machine.lower() not in LINUX_MACHINES:
            return f"SGLang publishes no Linux wheels for the CPU architecture {machine}"
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
        """Launcher + `--model-path <model>` + recipe args (+ extra args) + the owned flags."""
        recipe = entry.engines.get(self.name)  # profiles (KTransformers) have their own recipe
        recipe_args = list(recipe.args) if recipe else []
        check_args(recipe_args, FORBIDDEN_FLAGS, f"the {self.label} recipe of {entry.id}")
        check_args(extra_args, FORBIDDEN_FLAGS, "extra server arguments")
        args = [*recipe_args, *extra_args]
        if ctx_size is not None:
            args = with_flag(args, CTX_FLAGS, str(ctx_size))
        cmd = [*self.launcher(), "--model-path", model, *args]
        cmd += ["--host", LOOPBACK, "--port", str(port), "--served-model-name", entry.id]
        cmd += ["--enable-metrics", "--enable-cache-report"]
        return cmd + parser_flags(recipe)

    def key_args(self, api_key: str, log_path: Path) -> list[str]:
        path = key_file(log_path)
        write_key_file(path, api_key)
        return ["--config", str(path)]

    def stop(self, info: ServerInfo, timeout: float = 10.0) -> None:
        try:
            super().stop(info, timeout)
        finally:
            if info.log_path:
                key_file(Path(info.log_path)).unlink(missing_ok=True)

    def prefix_cache_stats(self, info: ServerInfo) -> dict[str, Any] | None:
        """Cached against prompt tokens since the start; None without metrics."""
        samples = self.metric_samples(info)
        if samples is None:
            return None
        prompt = counter_total(samples, "sglang:prompt_tokens") or 0.0
        cached = counter_total(samples, "sglang:cached_tokens") or 0.0
        return {
            "source": "metrics",
            "hit_tokens": cached,
            "query_tokens": prompt,
            "hit_rate": cached / prompt if prompt else None,
            "recent_hit_rate": gauge_mean(samples, "sglang:cache_hit_rate"),
        }
