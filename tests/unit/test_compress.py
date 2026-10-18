"""Optional prose compression (TK11): off by default, docs only, never code."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from stepswitch import net
from stepswitch.context.compress import (
    CompressionSettings,
    HttpCompressor,
    compressible,
    maybe_compress,
)

TEXT = "This paragraph explains the configuration in great detail. " * 60


class Halving:
    def compress(self, text: str, rate: float) -> str:
        return text[: int(len(text) * rate)]


def test_off_by_default_and_from_config() -> None:
    assert CompressionSettings().enabled is False
    config = SimpleNamespace(
        model_extra={
            "context": {"compression": {"enabled": True, "endpoint": "http://x", "rate": 0.4}}
        }
    )
    settings = CompressionSettings.from_config(config)
    assert settings.enabled and settings.endpoint == "http://x" and settings.rate == 0.4
    assert CompressionSettings.from_config(SimpleNamespace(model_extra={})).enabled is False


def test_only_documentation_text_is_compressible() -> None:
    assert compressible("docs/guide.md") and compressible("README.rst") and compressible("a.txt")
    assert not compressible("src/app.py") and not compressible("data.json")


def test_maybe_compress_keeps_code_short_text_and_errors_unchanged() -> None:
    on = CompressionSettings(enabled=True, endpoint="http://x", rate=0.5)
    text, info = maybe_compress(TEXT, "docs/guide.md", on, Halving())
    assert info == {"compressed": True, "chars": len(TEXT), "kept": len(TEXT) // 2}
    assert text.endswith(f"[compressed from {len(TEXT)} to {len(TEXT) // 2} characters]")
    assert maybe_compress(TEXT, "src/app.py", on, Halving()) == (TEXT, None)
    assert maybe_compress("short", "a.md", on, Halving()) == ("short", None)
    assert maybe_compress(TEXT, "a.md", CompressionSettings(), Halving()) == (TEXT, None)

    class Broken:
        def compress(self, text: str, rate: float) -> str:
            raise OSError("server down")

    text, info = maybe_compress(TEXT, "a.md", on, Broken())
    assert text == TEXT and info is not None and info["compressed"] is False


def test_http_compressor_speaks_the_documented_contract() -> None:
    seen: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"text": "short"})

    compressor = HttpCompressor(
        "http://127.0.0.1:8095/", egress=net.Egress(), transport=httpx.MockTransport(handle)
    )
    assert compressor.compress("long text", 0.5) == "short"
    assert seen[0]["text"] == "long text" and seen[0]["rate"] == 0.5
    assert "\n" in seen[0]["force_tokens"]  # type: ignore[operator]
