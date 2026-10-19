"""Local endpoints that forward to a hosted service count as remote (SPEC §15,
`stepswitch.compat.forwarding`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import net
from stepswitch.compat import forwarding
from stepswitch.compat.client import Endpoint, ModelClient
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Message, Request, TextPart
from stepswitch.config import Config
from stepswitch.workers.catalog import Catalog

LLAMA_SWAP_CONFIG = """\
macros:
  lan: "192.0.2.10"
models:
  local-coder:
    cmd: llama-server --port ${PORT} -m coder.gguf
    proxy: "http://127.0.0.1:${PORT}"
  plain:
    cmd: llama-server --port ${PORT} -m plain.gguf
  lan-coder:
    proxy: "http://${lan}:8080"
    aliases: [lan-alias]
  unknown-host:
    proxy: "http://${nowhere}:8080"
peers:
  office:
    proxy: https://models.example.org
    apiKey: not-a-real-key
    models: [peer-model]
  sidecar:
    proxy: http://localhost:9000
    models: [sidecar-model]
"""


@pytest.mark.parametrize(
    ("name", "cloud"),
    [
        ("kimi-k3:cloud", True),
        ("gpt-oss:120b-cloud", True),
        ("qwen2.5:32b", False),
        ("cloudy:latest", False),
        ("my-cloud", False),
        ("registry.local:5000/team/model", False),
    ],
)
def test_cloud_backed_names(name: str, cloud: bool) -> None:
    assert forwarding.cloud_backed_name(name) is cloud


def test_the_tags_listing_marks_forwarded_models() -> None:
    tags = {
        "models": [
            {"name": "qwen2.5:32b", "model": "qwen2.5:32b"},
            {"name": "renamed:latest", "model": "renamed:latest", "remote_host": "https://x"},
        ]
    }
    assert forwarding.ollama_remote(tags, "renamed:latest")
    assert not forwarding.ollama_remote(tags, "qwen2.5:32b")
    assert not forwarding.ollama_remote(tags, "absent:7b")
    assert not forwarding.ollama_remote("not a listing", "qwen2.5:32b")


def test_llama_swap_peers_and_remote_proxies_forward(tmp_path: Path) -> None:
    path = tmp_path / "llama-swap.yaml"
    path.write_text(LLAMA_SWAP_CONFIG, encoding="utf-8")
    forwards = forwarding.llama_swap_forwarding(path)
    assert set(forwards) == {"lan-coder", "lan-alias", "unknown-host", "peer-model"}
    assert forwards["peer-model"] == "llama-swap peer office at models.example.org"
    assert "192.0.2.10" in forwards["lan-coder"]
    path.write_text("models: [unclosed", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read the llama-swap config"):
        forwarding.llama_swap_forwarding(path)


def test_a_llama_swap_endpoint_needs_its_config_to_count_as_local(tmp_path: Path) -> None:
    (tmp_path / "swap.yaml").write_text(LLAMA_SWAP_CONFIG, encoding="utf-8")

    def reason(model: str, engine_config: str | None) -> str | None:
        return forwarding.static_reason(
            model=model,
            engine="llama-swap",
            fmt="chat-completions",
            engine_config=engine_config,
            root=tmp_path,
        )

    assert reason("local-coder", "swap.yaml") is None
    assert reason("sidecar-model", "swap.yaml") is None
    assert reason("peer-model", "swap.yaml") is not None
    assert "without `engine_config`" in (reason("local-coder", None) or "")
    assert "cannot read" in (reason("local-coder", "missing.yaml") or "")


def _config(tmp_path: Path, endpoints: list[dict[str, Any]]) -> Config:
    return Config.model_validate(
        {"endpoints": endpoints, "storage": {"models_dir": str(tmp_path / "models")}}
    )


def test_the_catalog_counts_forwarding_workers_as_remote(tmp_path: Path) -> None:
    (tmp_path / "swap.yaml").write_text(LLAMA_SWAP_CONFIG, encoding="utf-8")
    config = _config(
        tmp_path,
        [
            {
                "name": "box",
                "url": "http://127.0.0.1:11434",
                "engine": "ollama",
                "models": ["qwen2.5:32b", "kimi-k3:cloud"],
            },
            {
                "name": "swap",
                "url": "http://127.0.0.1:8080/v1",
                "engine": "llama-swap",
                "engine_config": "swap.yaml",
                "models": ["local-coder", "peer-model"],
            },
        ],
    )
    catalog = Catalog.from_config(config, tmp_path, include_managed=False)
    specs = {s.id: s for s in catalog.specs()}
    assert specs["box/qwen2.5:32b"].local and specs["swap/local-coder"].local
    for worker in ("box/kimi-k3:cloud", "swap/peer-model"):
        assert not specs[worker].local
        assert any(n.startswith("counts as remote:") for n in specs[worker].notes)
    model = catalog.workers["box/kimi-k3:cloud"].get_model()
    client = getattr(model, "client", model)
    assert isinstance(client, ModelClient)
    assert client.endpoint.local is False  # so the secret redactor applies to it
    swap = getattr(catalog.workers["swap/local-coder"].get_model(), "client", None)
    swap = swap or catalog.workers["swap/local-coder"].get_model()
    assert isinstance(swap, ModelClient)
    assert swap.endpoint.engine_config == str(tmp_path / "swap.yaml")


def _request(model: str) -> Request:
    return Request(model=model, messages=[Message(role="user", content=[TextPart(text="hi")])])


class _Ollama:
    """An Ollama server: /api/tags lists one model that forwards to a hosted service."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen2.5:32b", "model": "qwen2.5:32b"},
                        {"name": "renamed:latest", "remote_model": "big-model"},
                    ]
                },
            )
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "message": {"role": "assistant", "content": "ok"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 3,
                "eval_count": 1,
            },
        )


def _client(server: _Ollama, *, offline: bool, engine: str | None = "ollama") -> ModelClient:
    endpoint = Endpoint(
        name="box", base_url="http://127.0.0.1:11434", format="ollama-native", engine=engine
    )
    egress = net.Egress(net.EgressPolicy(offline=offline))
    return ModelClient(endpoint, egress=egress, transport=httpx.MockTransport(server))


def test_offline_requests_recheck_the_server_once_per_model() -> None:
    server = _Ollama()
    client = _client(server, offline=True)
    assert client.complete(_request("qwen2.5:32b")).response.message.text_content() == "ok"
    client.complete(_request("qwen2.5:32b"))
    assert server.paths == ["/api/tags", "/api/chat", "/api/chat"]
    with pytest.raises(ModelError, match="forwards renamed:latest to a hosted service") as info:
        client.complete(_request("renamed:latest"))
    assert info.value.kind == "blocked"
    with pytest.raises(ModelError, match="cloud-backed model name"):
        list(client.stream(_request("kimi-k3:cloud")))
    assert server.paths.count("/api/chat") == 2  # nothing refused was sent


def test_online_requests_are_not_rechecked() -> None:
    server = _Ollama()
    client = _client(server, offline=False)
    client.complete(_request("renamed:latest"))
    assert server.paths == ["/api/chat"]


def test_an_unreadable_listing_is_no_reason_to_refuse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(500, text="busy")
        return _Ollama()(request)

    endpoint = Endpoint(name="box", base_url="http://127.0.0.1:11434/v1", format="ollama-native")
    egress = net.Egress(net.EgressPolicy(offline=True))
    client = ModelClient(endpoint, egress=egress, transport=httpx.MockTransport(handler))
    assert client.complete(_request("qwen2.5:32b")).response.message.text_content() == "ok"
