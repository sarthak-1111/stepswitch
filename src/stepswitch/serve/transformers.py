"""The transformers adapter: the universal slow fallback (SPEC §4.2, §5.3; ADR 0008).

It starts the wrapper server `transformers_server.py` (next to this module) with the interpreter
of a separate environment that holds transformers and PyTorch, configured as
`[engines.transformers] python` in `.stepswitch/local.toml`; the core never imports either
library. The interpreter runs the file in isolated mode (`python -I`), so neither the script's
own directory (where this module, `transformers.py`, would shadow the library) nor user
site-packages or `PYTHON*` variables reach it.

The command line is `--model` with the model source (a local directory, or a model id already
in the hub cache), the recipe's `args` (plus extra arguments: `--device`, `--dtype`,
`--max-tokens`, `--trust-remote-code`, `--chat-template-kwargs`), then the flags the adapter
owns: the loopback host, the port and `--served-model-name`. A context size becomes
`--ctx-size`. The API key travels in `STEPSWITCH_SERVER_API_KEY`. The server parses neither tool
calls nor reasoning, so models on this engine always use the fallback tool protocol, and it has
no metrics endpoint.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from stepswitch.registry import ModelEntry
from stepswitch.serve.base import LOOPBACK
from stepswitch.serve.pyserver import OWNED, PythonServerEngine, check_args, with_flag
from stepswitch.serve.transformers_server import API_KEY_ENV

ENGINE = "transformers"
SERVER_SCRIPT = Path(__file__).with_name("transformers_server.py")
CTX_FLAGS = ("--ctx-size",)

FORBIDDEN_FLAGS = {
    "--model": "the model comes from the registry entry",
    "--host": OWNED,
    "--port": OWNED,
    "--served-model-name": OWNED,
}


class TransformersEngine(PythonServerEngine):
    """Starts and controls the transformers wrapper server."""

    name = ENGINE
    label = "transformers"
    dist = "transformers"
    requires = ("torch",)
    install_hint = (
        "create an environment with transformers and torch installed and set "
        "[engines.transformers] python in .stepswitch/local.toml"
    )
    api_key_env = API_KEY_ENV

    def __init__(self, *, server_script: Path | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.server_script = server_script if server_script is not None else SERVER_SCRIPT
        self.module_args = ("-I", str(self.server_script))

    def platform_problem(self, system: str, machine: str) -> str | None:
        return None  # wherever PyTorch runs

    def command(
        self,
        entry: ModelEntry,
        model: str,
        *,
        port: int,
        ctx_size: int | None = None,
        extra_args: Sequence[str] = (),
    ) -> list[str]:
        """`python -I transformers_server.py --model <model>` + recipe args (+ extra args) + the
        owned flags."""
        recipe = entry.engines.get(ENGINE)
        recipe_args = list(recipe.args) if recipe else []
        check_args(recipe_args, FORBIDDEN_FLAGS, f"the transformers recipe of {entry.id}")
        check_args(extra_args, FORBIDDEN_FLAGS, "extra server arguments")
        args = [*recipe_args, *extra_args]
        if ctx_size is not None:
            args = with_flag(args, CTX_FLAGS, str(ctx_size))
        cmd = [*self.launcher(), "--model", model, *args]
        return cmd + ["--host", LOOPBACK, "--port", str(port), "--served-model-name", entry.id]
