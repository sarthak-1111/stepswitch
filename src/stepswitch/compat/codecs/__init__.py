"""Codec lookup by public format name or module key (SPEC §4.1)."""

from __future__ import annotations

import importlib

from stepswitch.compat.codecs.base import Codec, CodecOptions
from stepswitch.compat.ir import FORMATS

_CLASS_NAMES = {
    "chat": "ChatCompletionsCodec",
    "responses": "ResponsesCodec",
    "messages": "MessagesCodec",
    "generate_content": "GenerateContentCodec",
    "ollama": "OllamaCodec",
    "llamacpp": "LlamaCppCodec",
    "text": "TextCompletionCodec",
}


def format_key(name: str) -> str:
    """Module key for a public format name ("chat-completions" -> "chat"); keys pass through."""
    if name in _CLASS_NAMES:
        return name
    if name in FORMATS:
        return FORMATS[name]
    raise KeyError(f"unknown API format {name!r}; known: {', '.join(FORMATS)}")


def get_codec(name: str, options: CodecOptions | None = None) -> Codec:
    key = format_key(name)
    module = importlib.import_module(f"stepswitch.compat.codecs.{key}")
    cls: type[Codec] = getattr(module, _CLASS_NAMES[key])
    return cls(options)


__all__ = ["Codec", "CodecOptions", "format_key", "get_codec"]
