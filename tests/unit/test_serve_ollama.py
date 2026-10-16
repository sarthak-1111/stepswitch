"""Ollama adapter: OLLAMA_HOST rules, read API, NDJSON pulls, preload and unload semantics."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from stepswitch import net
from stepswitch.registry import EngineRecipe, ModelEntry
from stepswitch.serve import ollama
from stepswitch.serve.base import EngineError
from stepswitch.serve.ollama import DEFAULT_URL, OllamaEngine

URL = "http://127.0.0.1:11434"


def entry(model_id: str = "tiny-embed", tag: str = "tiny-embed:q8", ram_ctx: int = 2048) -> Any:
    return ModelEntry(
        id=model_id,
        name=model_id,
        hub_id=f"test/{model_id}",
        quant="Q8_0",
        file_size_gb=0.6,
        ram_gb=0.9,
        ram_ctx=ram_ctx,
        context=32768,
        license="apache-2.0",
        engines={"ollama": EngineRecipe(name=tag)},
    )


class FakeOllama:
    """The parts of the Ollama native API the adapter uses, with in-memory state."""

    def __init__(self, tags: list[str], loaded: dict[str, int] | None = None) -> None:
        self.tags = [{"name": name, "model": name, "size": 600_000_000} for name in tags]
        self.ps = dict(loaded or {})
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.pull_lines: list[dict[str, Any]] = []
        self.pull_status = 200

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.34.4"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": self.tags})
        if path == "/api/ps":
            models = [
                {"name": n, "model": n, "size": s, "size_vram": 0} for n, s in self.ps.items()
            ]
            return httpx.Response(200, json={"models": models})
        if path == "/api/show":
            if not any(t["name"] == body["model"] for t in self.tags):
                return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
            return httpx.Response(200, json={"capabilities": ["embedding"], "details": {}})
        if path == "/api/generate":
            name = body["model"]
            if not any(t["name"] == name for t in self.tags):
                return httpx.Response(404, json={"error": f"model '{name}' not found"})
            if body.get("keep_alive") == 0:
                self.ps.pop(name, None)
                return httpx.Response(
                    200, json={"model": name, "done": True, "done_reason": "unload"}
                )
            self.ps[name] = 700_000_000
            return httpx.Response(200, json={"model": name, "response": "", "done": True})
        if path == "/api/pull":
            lines = "".join(json.dumps(line) + "\n" for line in self.pull_lines)
            if self.pull_status != 200:
                return httpx.Response(self.pull_status, json={"error": "pull model manifest: 404"})
            return httpx.Response(
                200, content=lines.encode(), headers={"Content-Type": "application/x-ndjson"}
            )
        return httpx.Response(404, json={"error": "not found"})

    def generate_calls(self) -> list[dict[str, Any]]:
        return [body or {} for method, path, body in self.requests if path == "/api/generate"]


def engine(fake: FakeOllama, *, egress: net.Egress | None = None) -> OllamaEngine:
    return OllamaEngine(URL, egress=egress or net.Egress(), transport=fake.transport)


# -- OLLAMA_HOST --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_URL),
        ("", DEFAULT_URL),
        ("0.0.0.0", "http://127.0.0.1:11434"),
        ("0.0.0.0:8080", "http://127.0.0.1:8080"),
        ("localhost", "http://localhost:11434"),
        ("example.net:1234", "http://example.net:1234"),
        ("http://example.net", "http://example.net:80"),
        ("https://example.net", "https://example.net:443"),
        ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
        ("[::]:11434", "http://[::1]:11434"),
        ("::1", "http://[::1]:11434"),
        ("127.0.0.1:99999", "http://127.0.0.1:11434"),
        (":11500", "http://127.0.0.1:11500"),
    ],
)
def test_base_url_follows_ollamas_own_host_rules(value: str | None, expected: str) -> None:
    environ = {} if value is None else {"OLLAMA_HOST": value}
    assert ollama.base_url(environ) == expected


def test_keep_alive_durations() -> None:
    assert ollama.keep_alive_for(1800) == "30m"
    assert ollama.keep_alive_for(90) == "90s"
    assert ollama.keep_alive_for(0) == "0s"


# -- read API -----------------------------------------------------------------------------------


def test_read_api() -> None:
    fake = FakeOllama(["tiny-embed:q8", "other:latest"], loaded={"other:latest": 1})
    adapter = engine(fake)
    assert adapter.version() == "0.34.4"
    assert adapter.running() and adapter.available()
    assert [m["name"] for m in adapter.list_models()] == ["tiny-embed:q8", "other:latest"]
    assert [m["name"] for m in adapter.loaded()] == ["other:latest"]
    assert adapter.show("tiny-embed:q8")["capabilities"] == ["embedding"]
    assert adapter.has_model("TINY-EMBED:q8") and adapter.has_model("other")
    assert not adapter.has_model("tiny-embed")  # means tiny-embed:latest
    with pytest.raises(EngineError, match="not found"):
        adapter.show("missing:1b")


def test_unreachable_ollama() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    adapter = OllamaEngine(URL, egress=net.Egress(), transport=httpx.MockTransport(refuse))
    assert adapter.version() is None and not adapter.running()
    with pytest.raises(EngineError, match="not reachable"):
        adapter.list_models()
    with pytest.raises(EngineError, match="not reachable"):
        adapter.start(entry())


# -- pulls --------------------------------------------------------------------------------------


def test_pull_streams_ndjson_progress() -> None:
    fake = FakeOllama([])
    fake.pull_lines = [
        {"status": "pulling manifest"},
        {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 40},
        {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 100},
        {"status": "verifying sha256 digest"},
        {"status": "writing manifest"},
        {"status": "success"},
    ]
    events: list[dict[str, Any]] = []
    egress = net.Egress()
    ollama.pull_model(
        URL, "tiny-embed:q8", egress=egress, transport=fake.transport, progress=events.append
    )
    assert events == fake.pull_lines
    assert fake.requests[0] == ("POST", "/api/pull", {"model": "tiny-embed:q8", "stream": True})
    assert [row.purpose for row in egress.memory] == ["model-pull"]


def test_pull_reports_an_error_line() -> None:
    fake = FakeOllama([])
    fake.pull_lines = [
        {"status": "pulling manifest"},
        {"error": "pull model manifest: file does not exist"},
    ]
    events: list[dict[str, Any]] = []
    with pytest.raises(EngineError, match="file does not exist"):
        ollama.pull_model(
            URL, "nope:1b", egress=net.Egress(), transport=fake.transport, progress=events.append
        )
    assert events == [{"status": "pulling manifest"}]


def test_pull_reports_http_errors_and_incomplete_streams() -> None:
    fake = FakeOllama([])
    fake.pull_status = 500
    with pytest.raises(EngineError, match="HTTP 500"):
        ollama.pull_model(URL, "x:1b", egress=net.Egress(), transport=fake.transport)
    fake.pull_status = 200
    fake.pull_lines = [{"status": "pulling manifest"}]
    with pytest.raises(EngineError, match="without success"):
        ollama.pull_model(URL, "x:1b", egress=net.Egress(), transport=fake.transport)


def test_pull_is_refused_offline() -> None:
    fake = FakeOllama([])
    offline = net.Egress(net.EgressPolicy(offline=True))
    with pytest.raises(EngineError, match="offline"):
        ollama.pull_model(URL, "x:1b", egress=offline, transport=fake.transport)
    assert fake.requests == []


# -- preload and unload -------------------------------------------------------------------------


def test_start_preloads_with_keep_alive_and_num_ctx_and_no_prompt() -> None:
    fake = FakeOllama(["tiny-embed:q8"])
    adapter = engine(fake)
    info = adapter.start(entry())
    [call] = fake.generate_calls()
    assert call == {
        "model": "tiny-embed:q8",
        "keep_alive": "30m",
        "stream": False,
        "options": {"num_ctx": 2048},
    }
    assert "prompt" not in call
    assert info.engine == "ollama" and info.engine_model == "tiny-embed:q8"
    assert info.model == "tiny-embed" and info.pid is None and info.managed
    assert info.base_url == URL and info.port == 11434 and info.api_key is None
    assert info.ram_gb == 0.7  # what /api/ps reports, not the registry figure
    assert adapter.health(info) and adapter.alive(info)

    adapter.stop(info)
    assert fake.generate_calls()[-1] == {"model": "tiny-embed:q8", "keep_alive": 0, "stream": False}
    assert not adapter.alive(info)


def test_start_uses_the_explicit_context_and_keep_alive() -> None:
    fake = FakeOllama(["tiny-embed:q8"])
    engine(fake).start(entry(), ctx_size=8192, keep_alive="5m")
    [call] = fake.generate_calls()
    assert call["options"] == {"num_ctx": 8192} and call["keep_alive"] == "5m"


def test_models_loaded_by_someone_else_are_never_touched() -> None:
    fake = FakeOllama(["tiny-embed:q8"], loaded={"tiny-embed:q8": 1_000_000_000})
    adapter = engine(fake)
    info = adapter.start(entry())
    assert not info.managed and info.ram_gb == 1.0
    adapter.stop(info)
    assert fake.generate_calls() == []  # no preload (keeps its keep-alive), no unload
    assert adapter.alive(info)


def test_start_of_a_missing_model_fails() -> None:
    fake = FakeOllama([])
    with pytest.raises(EngineError, match="not found"):
        engine(fake).start(entry())


def test_offline_mode_rejects_models_served_by_a_remote_host() -> None:
    fake = FakeOllama(["tiny-embed:q8"])
    fake.tags[0]["remote_host"] = "https://remote.example"
    offline = net.Egress(net.EgressPolicy(offline=True))
    with pytest.raises(EngineError, match="remote host"):
        engine(fake, egress=offline).start(entry())
    assert fake.generate_calls() == []


def test_model_name_falls_back_to_the_entry_id() -> None:
    plain = entry()
    plain.engines = {}
    assert ollama.model_name(plain) == "tiny-embed"
    assert ollama.model_name(entry()) == "tiny-embed:q8"


# -- live, read-only ----------------------------------------------------------------------------


@pytest.mark.live
def test_live_ollama_read_only() -> None:
    """Version, tags, ps and show against a running Ollama; nothing is pulled or loaded."""
    adapter = OllamaEngine(ollama.base_url())
    if not adapter.running():
        pytest.skip(f"no Ollama at {adapter.url}")
    version = adapter.version()
    assert version and re.match(r"^\d+\.\d+\.\d+", version)
    models = adapter.list_models()
    assert all("name" in m and "size" in m for m in models)
    assert all("name" in m and "expires_at" in m for m in adapter.loaded())
    if models:
        shown = adapter.show(models[0]["name"])
        assert "details" in shown
