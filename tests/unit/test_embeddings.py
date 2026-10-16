"""The embeddings client (compat layer) against mocked endpoints through stepswitch.net."""

from __future__ import annotations

import base64
import json
import struct
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from stepswitch import net
from stepswitch.compat.client import Endpoint
from stepswitch.compat.embeddings import EmbeddingClient
from stepswitch.compat.errors import ModelError
from stepswitch.context.search import SearchIndex

Handler = Callable[[httpx.Request], httpx.Response]


def make(
    handler: Handler,
    *,
    fmt: str = "chat-completions",
    base_url: str = "http://127.0.0.1:8080/v1",
    model: str | None = "embed-small",
    egress: net.Egress | None = None,
    **options: object,
) -> tuple[EmbeddingClient, list[httpx.Request], list[float]]:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    endpoint = Endpoint(
        name="local-embed",
        base_url=base_url,
        format=fmt,
        model=model,
        headers={"Authorization": "Bearer test-token"},
        local=base_url.startswith("http://127."),
    )
    client = EmbeddingClient(
        endpoint,
        egress=egress or net.Egress(),
        transport=httpx.MockTransport(record),
        sleep=sleeps.append,
        **options,  # type: ignore[arg-type]
    )
    return client, seen, sleeps


def compat_reply(vectors: list[list[float]], order: list[int] | None = None) -> httpx.Response:
    order = order if order is not None else list(range(len(vectors)))
    data = [{"object": "embedding", "index": i, "embedding": vectors[i]} for i in order]
    return httpx.Response(200, json={"object": "list", "data": data, "model": "embed-small"})


def test_chat_completions_family_uses_v1_embeddings() -> None:
    client, seen, _ = make(lambda r: compat_reply([[1.0, 0.0], [0.0, 1.0]], order=[1, 0]))
    assert client.embed(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]  # put back by index
    request = seen[0]
    assert request.method == "POST" and request.url.path == "/v1/embeddings"  # no doubled /v1
    assert json.loads(request.content) == {"model": "embed-small", "input": ["first", "second"]}
    assert request.headers["authorization"] == "Bearer test-token"
    assert request.headers["user-agent"] == "stepswitch"
    assert not client.native and client.local
    assert client.cache_key == "local-embed|chat-completions|embed-small"


def test_ollama_native_uses_api_embed() -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        count = len(json.loads(request.content)["input"])
        return httpx.Response(200, json={"model": "m", "embeddings": [[0.5] * 3] * count})

    client, seen, _ = make(reply, fmt="ollama-native", base_url="http://127.0.0.1:11434")
    assert client.embed(["a", "b"]) == [[0.5, 0.5, 0.5], [0.5, 0.5, 0.5]]
    assert seen[0].url.path == "/api/embed" and client.native


def test_texts_are_sent_in_batches() -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        return compat_reply([[float(len(t))] for t in texts])

    client, seen, _ = make(reply, batch_size=2)
    assert client.embed(["a", "bb", "ccc", "dddd", "eeeee"]) == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    assert [len(json.loads(r.content)["input"]) for r in seen] == [2, 2, 1]
    assert client.embed([]) == [] and len(seen) == 3


def test_base64_vectors_are_decoded() -> None:
    packed = base64.b64encode(struct.pack("<3f", 0.5, -1.0, 2.0)).decode()
    client, _, _ = make(lambda r: httpx.Response(200, json={"data": [{"embedding": packed}]}))
    assert client.embed(["x"]) == [[0.5, -1.0, 2.0]]


def test_the_query_prefix_is_used_for_queries_only() -> None:
    client, seen, _ = make(lambda r: compat_reply([[1.0]]), query_prefix="query: ")
    client.embed_query("where is the parser")
    client.embed(["a chunk"])
    assert [json.loads(r.content)["input"] for r in seen] == [
        ["query: where is the parser"],
        ["a chunk"],
    ]


def test_http_errors_become_model_errors() -> None:
    reply = httpx.Response(401, json={"error": {"message": "invalid key", "type": "auth"}})
    client, seen, _ = make(lambda r: reply)
    with pytest.raises(ModelError) as caught:
        client.embed(["x"])
    assert caught.value.kind == "authentication" and caught.value.status == 401
    assert "invalid key" in caught.value.message and len(seen) == 1  # not retried


def test_retryable_errors_are_retried_with_backoff() -> None:
    replies = [
        httpx.Response(503, headers={"retry-after": "2"}, json={"error": "busy"}),
        httpx.Response(500, text="oops"),
        compat_reply([[1.0, 2.0]]),
    ]
    client, seen, sleeps = make(lambda r: replies[len(seen) - 1])
    assert client.embed(["x"]) == [[1.0, 2.0]]
    assert len(seen) == 3 and sleeps == [2.0, 1.0]  # Retry-After, then exponential backoff

    failing, attempts, _ = make(lambda r: httpx.Response(500, text="down"), max_retries=2)
    with pytest.raises(ModelError, match="failed \\(500\\)"):
        failing.embed(["x"])
    assert len(attempts) == 3


def test_connection_problems_are_retried() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, seen, _ = make(refuse, max_retries=1)
    with pytest.raises(ModelError) as caught:
        client.embed(["x"])
    assert caught.value.kind == "connection" and len(seen) == 2


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("not json", "non-JSON"),
        ([1, 2], "not a JSON object"),
        ({"data": "nope"}, "no `data` list"),
        ({"data": [{"embedding": [1.0]}, {"embedding": [2.0]}]}, "asked for 1 embeddings"),
        ({"data": [{"embedding": []}]}, "non-empty list"),
        ({"data": [{"embedding": ["a"]}]}, "non-number"),
        ({"data": [{"embedding": "@@@"}]}, "bad base64"),
    ],
)
def test_bad_responses_are_reported(body: object, message: str) -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    client, _, _ = make(reply)
    with pytest.raises(ModelError, match=message) as caught:
        client.embed(["x"])
    assert caught.value.kind == "invalid_response"


def test_configuration_problems() -> None:
    with pytest.raises(ModelError, match="no embeddings request") as unsupported:
        make(lambda r: compat_reply([[1.0]]), fmt="messages")
    assert unsupported.value.kind == "unsupported"
    client, seen, _ = make(lambda r: compat_reply([[1.0]]), model=None)
    with pytest.raises(ModelError, match="no embedding model configured"):
        client.embed(["x"])
    assert seen == []


def test_offline_mode_blocks_remote_endpoints_and_everything_is_audited() -> None:
    egress = net.Egress(net.EgressPolicy(offline=True))
    client, seen, _ = make(
        lambda r: compat_reply([[1.0]]),
        base_url="https://embeddings.example.test/v1",
        egress=egress,
        task_id="task-7",
    )
    with pytest.raises(ModelError) as caught:
        client.embed(["x"])
    assert caught.value.kind == "blocked" and seen == []  # nothing left the machine
    blocked = list(egress.memory)[-1]
    assert (blocked.purpose, blocked.task_id, blocked.allowed) == ("embeddings", "task-7", False)

    local, _, _ = make(lambda r: compat_reply([[1.0]]), egress=egress, purpose="search-index")
    local.embed(["x"])
    row = list(egress.memory)[-1]
    assert (row.purpose, row.allowed, row.local, row.path) == (
        "search-index",
        True,
        True,
        "/v1/embeddings",
    )


def test_the_client_plugs_into_semantic_search(tmp_path: Path) -> None:
    (tmp_path / "net.py").write_text("def open_socket(host):\n    return host\n", "utf-8")
    (tmp_path / "fmt.py").write_text("def render_table(rows):\n    return rows\n", "utf-8")

    def reply(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        vectors = [[1.0, 0.0] if ("socket" in t or "network" in t) else [0.0, 1.0] for t in texts]
        return compat_reply(vectors)

    client, _, _ = make(reply)
    result = SearchIndex(tmp_path).search("network connection", embedder=client, k=1)
    assert result.mode == "hybrid" and result.hits[0].path == "net.py"
