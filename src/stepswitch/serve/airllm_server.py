"""A chat-completions server around AirLLM, for the slow lane (SPEC §4.2, ADR 0008, ADR 0030).

AirLLM runs models far larger than GPU memory by loading one layer at a time from disk, so a
token takes seconds to minutes: it serves asynchronous jobs only (`stepswitch.core.jobs`). It has
no server of its own, so this shim gives it the HTTP surface of the transformers wrapper
(`transformers_server.py`, loaded by its path from this file's directory): `POST
/v1/chat/completions` (plain or streamed), `GET /v1/models` and `GET /health` (503 while the
model loads), the key from `STEPSWITCH_SERVER_API_KEY`, one generation at a time, and the same
refusals (tools, constrained decoding, `n > 1`, logprobs), so its models always use the fallback
tool protocol. It runs in AirLLM's own environment (`python -I airllm_server.py ...`) and imports
only the standard library until the model loads.

AirLLM facts (docs/RESEARCH.md, AirLLM 4.0.0):

* `airllm.AutoModel.from_pretrained(path, compression=None | "4bit" | "8bit",
  layer_shards_saving_path=..., prefetching=True, device="cuda:0", dtype=..., max_seq_len=512)`
  splits the model into per-layer shards on first load, into `layer_shards_saving_path`;
  `generate` passes its arguments to the wrapped transformers model, so streaming, stopping and
  sampling work as in the transformers wrapper.
* A model name that is not a local directory is treated as a model-hub id and downloaded, so
  the shim loads local directories only (the adapter also sets `HF_HUB_OFFLINE=1`).
* AirLLM always trusts the model's own code (there is no switch); only models whose code you
  trust belong here.
* `max_seq_len` is the context size (`--ctx-size`, 8192 when not given; AirLLM's own default is
  512 tokens).

Arguments besides the transformers wrapper's: `--compression none|4bit|8bit`,
`--layer-shards-dir <dir>` and `--no-prefetching`. `--trust-remote-code` changes nothing here.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

COMPRESSIONS = ("none", "4bit", "8bit")
DEFAULT_CTX = 8192
DEFAULT_DEVICE = "cuda:0"  # AirLLM's own default
WRAPPER_FILE = Path(__file__).resolve().with_name("transformers_server.py")


def load_wrapper() -> Any:
    """The transformers wrapper module, from the file next to this one (the isolated
    interpreter has neither this directory nor the stepswitch package on its path)."""
    name = "stepswitch_transformers_server"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, WRAPPER_FILE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {WRAPPER_FILE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


wrapper = load_wrapper()


class AirLLMGenerator(wrapper.TransformersGenerator):  # type: ignore[misc, name-defined]
    """The transformers wrapper's generation loop over an AirLLM model."""

    def __init__(  # noqa: PLR0913 - the server's options, one by one
        self,
        model: str,
        *,
        device: str = "auto",
        dtype: str = "auto",
        ctx_size: int | None = None,
        max_tokens: int = 1024,
        compression: str = "none",
        layer_shards_dir: str | None = None,
        prefetching: bool = True,
        loader: Any = None,
    ) -> None:
        """`loader` replaces `airllm.AutoModel.from_pretrained` (tests)."""
        path = Path(model).expanduser()
        if not path.is_dir():
            raise RuntimeError(
                f"{model} is not a local model directory; AirLLM would download any other name"
            )
        if compression not in COMPRESSIONS:
            raise RuntimeError(f"--compression is one of {', '.join(COMPRESSIONS)}")
        try:
            torch = importlib.import_module("torch")
            transformers = importlib.import_module("transformers")
            if loader is None:
                loader = importlib.import_module("airllm").AutoModel.from_pretrained
        except ImportError as exc:
            raise RuntimeError(
                f"{exc}: install airllm (with torch and transformers) in this interpreter's "
                "environment"
            ) from exc
        self.torch = torch
        self.transformers = transformers
        self.device = DEFAULT_DEVICE if device == "auto" else device
        self.ctx_size = ctx_size or DEFAULT_CTX
        self.max_tokens = max_tokens
        options: dict[str, Any] = {
            "device": self.device,
            "max_seq_len": self.ctx_size,
            "prefetching": prefetching,
            "compression": None if compression == "none" else compression,
        }
        if dtype != "auto":
            options["dtype"] = getattr(torch, dtype)
        if layer_shards_dir:
            options["layer_shards_saving_path"] = layer_shards_dir
        self.model = loader(str(path), **options)
        self.tokenizer = self.model.tokenizer
        config = getattr(self.model, "generation_config", None)
        eos = getattr(config, "eos_token_id", None)
        self.eos_ids = set(eos if isinstance(eos, list) else [] if eos is None else [eos])
        self.pad_id = self.tokenizer.pad_token_id
        if self.pad_id is None:
            self.pad_id = min(self.eos_ids) if self.eos_ids else None


def build_extra_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--compression", choices=COMPRESSIONS, default="none")
    parser.add_argument("--layer-shards-dir", help="where AirLLM writes the per-layer shards")
    parser.add_argument("--no-prefetching", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None, *, loader: Any = None) -> int:
    """The transformers wrapper's `main` with an AirLLM generator."""
    extra, rest = build_extra_parser().parse_known_args(
        list(sys.argv[1:] if argv is None else argv)
    )

    def factory(args: argparse.Namespace) -> AirLLMGenerator:
        return AirLLMGenerator(
            args.model,
            device=args.device,
            dtype=args.dtype,
            ctx_size=args.ctx_size,
            max_tokens=args.max_tokens,
            compression=extra.compression,
            layer_shards_dir=extra.layer_shards_dir,
            prefetching=not extra.no_prefetching,
            loader=loader,
        )

    result: int = wrapper.main(rest, factory=factory)
    return result


if __name__ == "__main__":
    wrapper.configure_output()
    sys.exit(main())
