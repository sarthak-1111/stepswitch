"""The AirLLM adapter: layer streaming for the slow lane (SPEC §4.2, §5; ADR 0008, ADR 0030).

AirLLM has no server, so the adapter starts the shim `airllm_server.py` (next to this module),
which serves AirLLM through the transformers wrapper's HTTP surface. It runs with the
interpreter of AirLLM's own environment (with torch and transformers), configured as
`[engines.airllm] python` in `.stepswitch/local.toml`, in isolated mode (`python -I`), so the
core never imports AirLLM.

The command line is `--model` with the model directory, the recipe's `args` (plus extra
arguments: `--compression`, `--layer-shards-dir`, `--no-prefetching`, `--device`, `--dtype`,
`--max-tokens`, `--chat-template-kwargs`), then the flags the adapter owns: the loopback host,
the port, `--served-model-name` and `--ctx-size` (the requested context, else the entry's,
else 8192). The key travels in `STEPSWITCH_SERVER_API_KEY`.

Models must be local directories: AirLLM treats any other name as a model-hub id and downloads
it. Models on this engine are slow-lane workers (tier `sl`, `workers.catalog`): asynchronous
jobs only, never an interactive step.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from stepswitch.registry import ModelEntry
from stepswitch.serve.base import LOOPBACK, EngineError, ServerInfo
from stepswitch.serve.pyserver import OWNED, PythonServerEngine, check_args, with_flag
from stepswitch.serve.transformers_server import API_KEY_ENV

ENGINE = "airllm"
SERVER_SCRIPT = Path(__file__).with_name("airllm_server.py")
CTX_FLAGS = ("--ctx-size",)
DEFAULT_CTX = 8192

FORBIDDEN_FLAGS = {
    "--model": "the model comes from the registry entry",
    "--host": OWNED,
    "--port": OWNED,
    "--served-model-name": OWNED,
    "--ctx-size": "set the context with the entry or `ssw models serve --ctx-size`",
}


class AirLLMEngine(PythonServerEngine):
    """Starts and controls the AirLLM shim server."""

    name = ENGINE
    label = "AirLLM"
    dist = "airllm"
    requires = ("torch", "transformers")
    install_hint = (
        "create an environment with airllm (and torch and transformers) installed and set "
        "[engines.airllm] python in .stepswitch/local.toml"
    )
    api_key_env = API_KEY_ENV

    def __init__(self, *, server_script: Path | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.server_script = server_script if server_script is not None else SERVER_SCRIPT
        self.module_args = ("-I", str(self.server_script))

    def platform_problem(self, system: str, machine: str) -> str | None:
        # On macOS AirLLM loads its MLX model class, whose `generate` returns decoded text and
        # takes no streamer, so the transformers wrapper's loop cannot drive it.
        if system == "darwin":
            return (
                "the AirLLM shim drives AirLLM's torch models; on macOS AirLLM uses its MLX "
                "model class, which the shim does not support (use MLX-LM there)"
            )
        return None  # elsewhere: wherever its own dependencies install

    def command(
        self,
        entry: ModelEntry,
        model: str,
        *,
        port: int,
        ctx_size: int | None = None,
        extra_args: Sequence[str] = (),
    ) -> list[str]:
        """`python -I airllm_server.py --model <dir>` + recipe args (+ extra args) + the owned
        flags."""
        recipe = entry.engines.get(ENGINE)
        recipe_args = list(recipe.args) if recipe else []
        check_args(recipe_args, FORBIDDEN_FLAGS, f"the AirLLM recipe of {entry.id}")
        check_args(extra_args, FORBIDDEN_FLAGS, "extra server arguments")
        context = ctx_size or entry.context or DEFAULT_CTX
        args = with_flag([*recipe_args, *extra_args], CTX_FLAGS, str(context))
        cmd = [*self.launcher(), "--model", model, *args]
        return cmd + ["--host", LOOPBACK, "--port", str(port), "--served-model-name", entry.id]

    def _directory_problem(self, entry: ModelEntry, source: str | None) -> str | None:
        if source is not None and "://" not in source and not Path(source).is_dir():
            return (
                f"{entry.id}: AirLLM loads a local model directory only (it would download "
                f"{source!r} from the model hub)"
            )
        return None

    def serve_problem(self, entry: ModelEntry) -> str | None:
        problem = super().serve_problem(entry)
        return problem or self._directory_problem(entry, self.model_source(entry))

    def start(self, entry: ModelEntry, **kwargs: Any) -> ServerInfo:
        problem = self._directory_problem(entry, self.model_source(entry, kwargs.get("model_path")))
        if problem:
            raise EngineError(problem)
        return super().start(entry, **kwargs)
