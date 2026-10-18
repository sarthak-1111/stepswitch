"""Optional prompt compression for prose (SPEC §13 TK11).

Off by default, for documentation text only, never for code: a `read` of a Markdown, reStructured
text or plain-text file longer than `min_chars` may be compressed by a local compression server
before it enters the transcript. The reference server (`extras/llmlingua-server`) runs the small
LLMLingua-2 checkpoint from a local path, out of process, so no inference engine is imported
here; Stepswitch only speaks HTTP to it, through `stepswitch.net`. It is enabled only if the
bench shows a gain (ADR 0024):

    [context.compression]
    enabled = true
    endpoint = "http://127.0.0.1:8095"
    rate = 0.5

The server contract: `POST /compress` with `{"text": str, "rate": float, "force_tokens": [str]}`
answers `{"text": str}`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import httpx

from stepswitch import net

PROSE_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc"})
FORCE_TOKENS = ("\n", "?", ".", ":", "`")
TIMEOUT = 60.0


@dataclass(frozen=True)
class CompressionSettings:
    enabled: bool = False
    endpoint: str | None = None
    rate: float = 0.5
    min_chars: int = 2000

    @classmethod
    def from_config(cls, config: Any) -> CompressionSettings:
        section = (getattr(config, "model_extra", None) or {}).get("context") or {}
        table = section.get("compression") if isinstance(section, Mapping) else None
        if not isinstance(table, Mapping):
            return cls()
        return cls(
            enabled=bool(table.get("enabled", False)),
            endpoint=str(table["endpoint"]) if table.get("endpoint") else None,
            rate=float(table.get("rate", 0.5)),
            min_chars=int(table.get("min_chars", 2000)),
        )


def compressible(path: str) -> bool:
    """Documentation text only; code and data are never compressed."""
    return PurePosixPath(path.replace("\\", "/")).suffix.lower() in PROSE_SUFFIXES


class HttpCompressor:
    def __init__(
        self,
        endpoint: str,
        *,
        egress: net.Egress | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.egress = egress or net.default()
        self.transport = transport

    def compress(self, text: str, rate: float) -> str:
        body = {"text": text, "rate": rate, "force_tokens": list(FORCE_TOKENS)}
        with self.egress.client("compress", timeout=TIMEOUT, transport=self.transport) as client:
            response = client.post(f"{self.endpoint}/compress", json=body)
        response.raise_for_status()
        answer = response.json().get("text")
        if not isinstance(answer, str):
            raise ValueError("the compression server answered without text")
        return answer


def maybe_compress(
    text: str, path: str, settings: CompressionSettings, compressor: Any | None
) -> tuple[str, dict[str, Any] | None]:
    """(text to show, details or None). Anything that goes wrong keeps the original text."""
    if not settings.enabled or compressor is None or not compressible(path):
        return text, None
    if len(text) < settings.min_chars:
        return text, None
    try:
        compressed = compressor.compress(text, settings.rate)
    except Exception as exc:  # noqa: BLE001 - compression is optional; never fail a read on it
        return text, {"compressed": False, "error": str(exc)[:200]}
    if not compressed or len(compressed) >= len(text):
        return text, {"compressed": False, "error": "no gain"}
    note = f"\n[compressed from {len(text)} to {len(compressed)} characters]"
    return compressed + note, {"compressed": True, "chars": len(text), "kept": len(compressed)}
