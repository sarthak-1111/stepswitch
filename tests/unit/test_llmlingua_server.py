"""The reference compression server in extras/ against the client contract (SPEC §13 TK11)."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
import types
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import net
from stepswitch.context.compress import (
    FORCE_TOKENS,
    CompressionSettings,
    HttpCompressor,
    maybe_compress,
)

SERVER = Path(__file__).resolve().parents[2] / "extras" / "llmlingua-server" / "server.py"


def load_server() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("llmlingua_server", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


server_module = load_server()


class HalfBackend:
    """Keeps every other word, and remembers what it was asked."""

    name = "half"

    def __init__(self) -> None:
        self.calls: list[tuple[str, float, list[str]]] = []

    def compress(self, text: str, rate: float, force_tokens: Sequence[str]) -> str:
        self.calls.append((text, rate, list(force_tokens)))
        return " ".join(text.split()[::2])


class FailingBackend:
    name = "failing"

    def compress(self, text: str, rate: float, force_tokens: Sequence[str]) -> str:
        raise RuntimeError("model crashed on private text")


@pytest.fixture
def serve() -> Iterator[Any]:
    servers: list[Any] = []

    def start(backend: Any) -> str:
        server = server_module.create_server(backend, "127.0.0.1", 0)
        poll = {"poll_interval": 0.05}  # shutdown() waits up to one poll
        threading.Thread(target=server.serve_forever, kwargs=poll, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_client_and_reference_server_agree_on_the_contract(serve: Any) -> None:
    backend = HalfBackend()
    url = serve(backend)
    egress = net.Egress(net.EgressPolicy(offline=True))  # a loopback server works offline too
    compressor = HttpCompressor(url, egress=egress)
    text = "one two three four five six"
    assert compressor.compress(text, 0.5) == "one three five"
    assert backend.calls == [(text, 0.5, list(FORCE_TOKENS))]
    rows = list(egress.memory)
    assert rows and all(row.allowed and row.local for row in rows)


def test_a_long_markdown_read_is_compressed_end_to_end(serve: Any) -> None:
    url = serve(HalfBackend())
    settings = CompressionSettings(enabled=True, endpoint=url, rate=0.5, min_chars=100)
    prose = "The runner writes one row per task and seed. " * 10
    text, info = maybe_compress(prose, "docs/guide.md", settings, HttpCompressor(url))
    assert info is not None and info["compressed"] is True
    assert info["kept"] < info["chars"] and "[compressed from" in text
    code, none = maybe_compress(prose, "src/app.py", settings, HttpCompressor(url))
    assert code == prose and none is None  # code is never sent


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        (b"not json", 400, "not JSON"),
        (json.dumps([1, 2]).encode(), 400, "JSON object"),
        (json.dumps({"rate": 0.5}).encode(), 400, "`text`"),
        (json.dumps({"text": "a b", "rate": 0}).encode(), 400, "`rate`"),
        (json.dumps({"text": "a b", "rate": 1.5}).encode(), 400, "`rate`"),
        (json.dumps({"text": "a b", "rate": True}).encode(), 400, "`rate`"),
        (json.dumps({"text": "a b", "force_tokens": "\n"}).encode(), 400, "`force_tokens`"),
        (json.dumps({"text": "a b", "force_tokens": ["x" * 40]}).encode(), 400, "`force_tokens`"),
    ],
)
def test_bad_requests_get_a_reason_and_never_reach_the_model(
    serve: Any, body: bytes, status: int, error: str
) -> None:
    backend = HalfBackend()
    url = serve(backend)
    response = httpx.post(f"{url}/compress", content=body)
    assert response.status_code == status
    assert error in response.json()["error"]
    assert backend.calls == []


def test_errors_unknown_paths_and_health(serve: Any) -> None:
    url = serve(FailingBackend())
    failed = httpx.post(f"{url}/compress", json={"text": "some words", "rate": 0.5})
    assert failed.status_code == 500
    assert failed.json() == {"error": "compression failed: RuntimeError"}  # no text, no trace
    assert httpx.get(f"{url}/health").json() == {"status": "ok", "model": "failing"}
    assert httpx.get(f"{url}/nothing").status_code == 404
    assert httpx.post(f"{url}/other", json={}).status_code == 404
    blank = httpx.post(f"{url}/compress", json={"text": "  ", "rate": 0.5})
    assert blank.json() == {"text": "  "}  # nothing to compress, the model is not called


def test_oversized_bodies_are_refused(serve: Any) -> None:
    url = serve(HalfBackend())
    big = json.dumps({"text": "x" * (server_module.MAX_BODY_BYTES + 1)})
    response = httpx.post(f"{url}/compress", content=big)
    assert response.status_code == 413


def test_lingua_backend_loads_a_local_llmlingua2_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    made: list[dict[str, Any]] = []
    asked: list[tuple[Any, dict[str, Any]]] = []

    class PromptCompressor:
        def __init__(self, **kwargs: Any) -> None:
            made.append(kwargs)

        def compress_prompt(self, context: Any, **kwargs: Any) -> dict[str, Any]:
            asked.append((context, kwargs))
            return {"compressed_prompt": "short", "origin_tokens": 9, "compressed_tokens": 2}

    fake = types.SimpleNamespace(PromptCompressor=PromptCompressor)
    monkeypatch.setitem(sys.modules, "llmlingua", fake)
    checkpoint = tmp_path / "llmlingua-2-bert-base-multilingual-cased-meetingbank"
    backend = server_module.LinguaBackend(checkpoint)
    assert made == [
        {
            "model_name": str(checkpoint),
            "device_map": "cpu",
            "model_config": {"trust_remote_code": False},
            "use_llmlingua2": True,
        }
    ]
    assert backend.compress("a long text", 0.4, ["\n", "?"]) == "short"
    assert asked == [(["a long text"], {"rate": 0.4, "force_tokens": ["\n", "?"]})]
    assert backend.name == "llmlingua-2-bert-base-multilingual-cased-meetingbank"


def test_offline_guard_refuses_remote_hosts_and_restores_sockets() -> None:
    before = (vars(socket.socket).get("connect"), socket.getaddrinfo)
    guard = server_module.OfflineGuard()
    guard.install()
    try:
        with pytest.raises(OSError, match="refused a connection to 192.0.2.1"):
            socket.create_connection(("192.0.2.1", 9), timeout=1)
        with pytest.raises(OSError, match="example.invalid"):
            socket.getaddrinfo("example.invalid", 443)
        assert socket.getaddrinfo("127.0.0.1", 80)  # loopback still resolves
    finally:
        guard.uninstall()
    assert (vars(socket.socket).get("connect"), socket.getaddrinfo) == before


def test_loopback_detection() -> None:
    assert server_module.is_loopback("127.0.0.1")
    assert server_module.is_loopback("::1") and server_module.is_loopback("[::1]")
    assert server_module.is_loopback("localhost")
    assert not server_module.is_loopback("192.0.2.1")
    assert not server_module.is_loopback("example.invalid")


def test_main_refuses_a_hub_id_or_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        server_module.main(["--model-path", str(tmp_path / "org-name-model")])
    assert exit_info.value.code == 2


def test_a_checkpoint_directory_must_carry_the_checkpoint_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # llmlingua 0.2.2 picks its word rules from the name and fails every request otherwise.
    renamed = tmp_path / "llmlingua-2-small"
    renamed.mkdir()
    (renamed / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        server_module.main(["--model-path", str(renamed)])
    assert exit_info.value.code == 2
    assert "bert-base-multilingual-cased" in capsys.readouterr().err
    with pytest.raises(ValueError, match="name the checkpoint directory"):
        server_module.LinguaBackend(renamed)
    large = tmp_path / "llmlingua-2-xlm-roberta-large-meetingbank"
    assert server_module.name_problem(large) is None
