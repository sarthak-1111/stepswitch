"""`ssw proxy` (src/stepswitch/proxy): every inbound format answered in its own shape, the
local token, routing that switches only at a new user turn, failover before the first byte,
stored responses, keep-alives, and every request recorded as a step of a proxy task."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from stepswitch.compat.client import Endpoint, ModelClient
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Request, Usage
from stepswitch.ledger import task_rows
from stepswitch.proxy import app as proxy_app
from stepswitch.proxy.app import INBOUND_ROUTES, create_proxy_app
from stepswitch.proxy.service import ProxyService, ProxySettings
from stepswitch.router.rules import parse_rules
from stepswitch.state import open_project_state
from stepswitch.trajectory.record import read_events
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, ScriptedModel

TOKEN = "ssw-test-token"
AUTH = {"authorization": f"Bearer {TOKEN}"}
HELLO = "Hello from the fake worker."


def worker(worker_id: str, script: Any, *, tier: str = "t2", **spec: Any) -> Worker:
    model = ScriptedModel(script, usage=Usage(input_tokens=40, output_tokens=6))
    endpoint, _, name = worker_id.partition("/")
    return Worker(WorkerSpec(id=worker_id, endpoint=endpoint, model=name, tier=tier, **spec), model)


def says(text: str = HELLO) -> list[Action]:
    return [Action(text=text)]


class Slow:
    """A model that answers after `delay` seconds, or fails then."""

    def __init__(self, delay: float, *, error: ModelError | None = None) -> None:
        self.delay = delay
        self.error = error
        self.inner = ScriptedModel(says(), usage=Usage(input_tokens=10, output_tokens=2))

    def complete(self, request: Request) -> Any:
        time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.inner.complete(request)


class Failing:
    def __init__(self, kind: str = "server") -> None:
        self.kind = kind
        self.calls = 0

    def complete(self, request: Request) -> Any:
        self.calls += 1
        raise ModelError(self.kind, "the server is down", status=503)  # type: ignore[arg-type]


def make(
    tmp_path: Path,
    workers: list[Worker],
    *,
    settings: ProxySettings | None = None,
    rules: list[dict[str, Any]] | None = None,
) -> tuple[TestClient, ProxyService]:
    db = open_project_state(tmp_path)
    service = ProxyService(
        tmp_path,
        Catalog(workers),
        db=db,
        settings=settings or ProxySettings(heartbeat_seconds=0.2),
        rules=parse_rules(rules or []),
    )
    app = create_proxy_app(service, token=TOKEN, port=7778, hosts={"testserver"})
    return TestClient(app, headers=AUTH), service


def chat(text: str, *, model: str = "ssw:auto", **extra: Any) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": text}], **extra}


def messages_body(*turns: dict[str, Any], model: str = "ssw:auto", **extra: Any) -> dict[str, Any]:
    return {"model": model, "max_tokens": 256, "messages": list(turns), **extra}


TOOLS = [{"name": "read", "description": "Read a file", "input_schema": {"type": "object"}}]


def sse(text: str) -> list[tuple[str | None, str]]:
    events: list[tuple[str | None, str]] = []
    for block in text.strip().split("\n\n"):
        name = None
        data = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data or name:
            events.append((name, "\n".join(data)))
    return events


# -- surface ---------------------------------------------------------------------------------


def test_every_inbound_route_of_adr_0004_is_served(tmp_path: Path) -> None:
    client, _ = make(tmp_path, [worker("a/m", says())])
    guard = client.app
    routes = {route.path for route in guard.app.routes}  # type: ignore[attr-defined]
    assert set(INBOUND_ROUTES) <= routes
    assert proxy_app.HOST == "127.0.0.1" and proxy_app.DEFAULT_PORT == 7778


def test_requests_need_the_token_and_a_loopback_host(tmp_path: Path) -> None:
    client, _ = make(tmp_path, [worker("a/m", says())])
    body = chat("Hi")
    assert (
        client.post("/v1/chat/completions", json=body, headers={"authorization": ""}).status_code
        == 401
    )
    wrong = client.post("/v1/chat/completions", json=body, headers={"authorization": "Bearer x"})
    assert wrong.status_code == 401 and "token" in wrong.json()["error"]["message"]
    for headers, params in (
        ({"authorization": "", "x-api-key": TOKEN}, {}),
        ({"authorization": "", "x-goog-api-key": TOKEN}, {}),
        ({"authorization": ""}, {"key": TOKEN}),
    ):
        answer = client.post("/v1/chat/completions", json=body, headers=headers, params=params)
        assert answer.status_code == 200, answer.text
    assert (
        client.post("/v1/chat/completions", json=body, headers={"host": "evil.test"}).status_code
        == 403
    )
    cross = client.post("/v1/chat/completions", json=body, headers={"origin": "http://evil.test"})
    assert cross.status_code == 403
    assert client.head("/", headers={"authorization": ""}).status_code == 200


def test_the_token_is_created_once_in_the_state_directory(tmp_path: Path) -> None:
    first = proxy_app.load_or_create_token(tmp_path)
    assert first.startswith("ssw-") and len(first) > 30
    assert proxy_app.load_or_create_token(tmp_path) == first
    assert proxy_app.token_path(tmp_path).parent == tmp_path / ".stepswitch" / "proxy"


# -- formats ---------------------------------------------------------------------------------


def test_each_format_is_answered_in_its_own_shape(tmp_path: Path) -> None:
    client, _ = make(tmp_path, [worker("a/m", says())])
    answer = client.post("/v1/chat/completions", json=chat("Hi")).json()
    assert answer["choices"][0]["message"]["content"] == HELLO
    answer = client.post(
        "/v1/messages", json=messages_body({"role": "user", "content": "Hi"})
    ).json()
    assert answer["type"] == "message" and answer["content"][0]["text"] == HELLO
    answer = client.post("/v1/responses", json={"model": "ssw:auto", "input": "Hi"}).json()
    assert answer["id"].startswith("resp_")
    texts = [p["text"] for item in answer["output"] for p in item.get("content", [])]
    assert HELLO in texts
    content = {"contents": [{"role": "user", "parts": [{"text": "Hi"}]}]}
    answer = client.post("/v1beta/models/ssw:auto:generateContent", json=content).json()
    assert answer["candidates"][0]["content"]["parts"][0]["text"] == HELLO
    answer = client.post("/api/chat", json=chat("Hi", stream=False)).json()
    assert answer["message"]["content"] == HELLO and answer["done"] is True
    answer = client.post(
        "/api/generate", json={"model": "ssw:auto", "prompt": "Hi", "stream": False}
    )
    assert answer.json()["response"] == HELLO and "message" not in answer.json()
    answer = client.post("/v1/completions", json={"model": "ssw:auto", "prompt": "Hi"}).json()
    assert answer["choices"][0]["text"] == HELLO


def test_errors_come_back_in_the_callers_shape(tmp_path: Path) -> None:
    client, _ = make(tmp_path, [worker("a/m", says())])
    bad = client.post(
        "/v1/messages", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert bad.status_code == 400 and bad.json()["type"] == "error"
    bad = client.post("/v1/chat/completions", content=b"[1, 2]")
    assert bad.status_code == 400 and "error" in bad.json()
    raw = client.post("/api/generate", json={"model": "ssw:auto", "prompt": "x", "raw": True})
    assert raw.status_code == 400


def test_streams_follow_each_formats_event_sequence(tmp_path: Path) -> None:
    client, _ = make(tmp_path, [worker("a/m", says())])
    body = messages_body({"role": "user", "content": "Hi"}, stream=True)
    with client.stream("POST", "/v1/messages", json=body) as answer:
        assert answer.headers["content-type"].startswith("text/event-stream")
        events = sse(answer.read().decode())
    names = [name for name, _ in events]
    assert names[0] == "message_start" and names[-1] == "message_stop"
    assert "content_block_delta" in names and "message_delta" in names
    text = "".join(
        json.loads(data)["delta"].get("text", "")
        for name, data in events
        if name == "content_block_delta"
    )
    assert text == HELLO
    with client.stream("POST", "/v1/chat/completions", json=chat("Hi", stream=True)) as answer:
        lines = [e[1] for e in sse(answer.read().decode())]
    assert lines[-1] == "[DONE]"
    with client.stream("POST", "/api/chat", json=chat("Hi")) as answer:
        assert answer.headers["content-type"].startswith("application/x-ndjson")
        rows = [json.loads(line) for line in answer.read().decode().splitlines() if line]
    assert rows[-1]["done"] is True
    assert "".join(r.get("message", {}).get("content", "") for r in rows) == HELLO
    with client.stream(
        "POST", "/api/generate", json={"model": "ssw:auto", "prompt": "Hi"}
    ) as answer:
        rows = [json.loads(line) for line in answer.read().decode().splitlines() if line]
    assert "".join(r.get("response", "") for r in rows) == HELLO
    assert all("message" not in r for r in rows)


def test_quiet_streams_get_keep_alives_and_late_failures_end_in_an_error_event(
    tmp_path: Path,
) -> None:
    slow = Worker(WorkerSpec(id="a/slow", endpoint="a", model="slow", tier="t2"), Slow(0.7))
    client, _ = make(tmp_path, [slow], settings=ProxySettings(heartbeat_seconds=0.1))
    body = messages_body({"role": "user", "content": "Hi"}, stream=True)
    with client.stream("POST", "/v1/messages", json=body) as answer:
        events = sse(answer.read().decode())
    names = [name for name, _ in events]
    assert names[0] == "message_start" and "ping" in names and names[-1] == "message_stop"
    assert names.index("ping") < names.index("content_block_delta")
    late = ModelError("server", "the model crashed", status=500)
    broken = Worker(
        WorkerSpec(id="a/broken", endpoint="a", model="b", tier="t2"), Slow(0.5, error=late)
    )
    client, _ = make(tmp_path / "late", [broken], settings=ProxySettings(heartbeat_seconds=0.1))
    with client.stream("POST", "/v1/messages", json=body) as answer:
        assert answer.status_code == 200
        names = [name for name, _ in sse(answer.read().decode())]
    assert names[0] == "message_start" and "error" in names


# -- routing ---------------------------------------------------------------------------------

RULES = [
    {"name": "plans-on-a", "when": {"step_type": "plan"}, "to": {"id": "a/m"}},
    {"name": "edits-on-b", "when": {"step_type": "edit"}, "to": {"id": "b/m"}},
]


def test_the_model_switches_only_at_a_new_user_turn(tmp_path: Path) -> None:
    calls = [{"type": "tool_use", "id": "t1", "name": "read", "input": {"path": "a.py"}}]
    a = worker("a/m", [Action(tool="read", arguments={"path": "a.py"})], family="alpha")
    b = worker("b/m", says(), family="beta")
    client, service = make(tmp_path, [a, b], rules=RULES)
    first = {"role": "user", "content": "Fix the bug."}
    one = client.post("/v1/messages", json=messages_body(first, model="ssw:plan", tools=TOOLS))
    assert one.json()["content"][0]["type"] == "tool_use"
    results = [
        first,
        {"role": "assistant", "content": calls},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
    ]
    # The rule sends edit steps to b, but the turn is still open on a.
    client.post("/v1/messages", json=messages_body(*results, model="ssw:edit", tools=TOOLS))
    turn = [
        *results,
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": "Now tidy up."},
    ]
    client.post("/v1/messages", json=messages_body(*turn, model="ssw:edit", tools=TOOLS))
    [task] = service.sessions.tasks()
    events = read_events(tmp_path / ".stepswitch" / "runs" / task.task_id)
    decisions = [e for e in events if e["type"] == "decision"]
    assert [d["chosen"] for d in decisions] == ["a/m", "a/m", "b/m"]
    assert decisions[1]["rule"] == "open turn" and "still open" in decisions[1]["reason"]
    assert decisions[2]["switched"] is True
    assert len(a.model.requests) == 2 and len(b.model.requests) == 1  # type: ignore[union-attr]


def test_a_worker_that_cannot_take_the_turn_hands_it_on(tmp_path: Path) -> None:
    small = worker("a/m", [Action(tool="read", arguments={"path": "a.py"})], context=4000)
    big = worker("b/m", says(), context=200_000)
    client, _ = make(tmp_path, [small, big], rules=[RULES[0]])
    first = {"role": "user", "content": "Fix the bug."}
    client.post("/v1/messages", json=messages_body(first, model="ssw:plan", tools=TOOLS))
    long = {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 40_000}
    calls = [{"type": "tool_use", "id": "t1", "name": "read", "input": {"path": "a.py"}}]
    turn = [first, {"role": "assistant", "content": calls}, {"role": "user", "content": [long]}]
    answer = client.post("/v1/messages", json=messages_body(*turn, model="ssw:edit", tools=TOOLS))
    assert answer.status_code == 200 and answer.json()["content"][0]["text"] == HELLO


def test_failover_happens_only_before_the_first_byte(tmp_path: Path) -> None:
    down = Failing()
    a = Worker(WorkerSpec(id="a/m", endpoint="a", model="m", tier="t2", family="alpha"), down)
    b = worker("b/m", says(), family="beta")
    client, service = make(
        tmp_path,
        [a, b],
        rules=[{"name": "a-first", "when": {}, "prefer": {"id": "a/m"}, "bonus": 5.0}],
    )
    answer = client.post("/v1/chat/completions", json=chat("Hi"))
    assert answer.status_code == 200 and answer.json()["choices"][0]["message"]["content"] == HELLO
    assert down.calls == 1
    [task] = service.sessions.tasks()
    rows = task_rows(tmp_path, task.task_id)
    assert [r["data"]["error"] for r in rows] == ["server", None]
    events = read_events(tmp_path / ".stepswitch" / "runs" / task.task_id)
    notes = [n for e in events if e["type"] == "decision" for n in e.get("notes", [])]
    assert any("failover: a/m failed" in n for n in notes)
    # A pinned worker never fails over: the caller asked for that model.
    pinned = client.post("/v1/chat/completions", json=chat("Hi again", model="a/m"))
    assert pinned.status_code == 503 and down.calls == 2


def test_model_names_pin_workers_and_unknown_names_route_as_auto(tmp_path: Path) -> None:
    a = worker("a/m", says("from a"))
    b = worker("b/m", says("from b"))
    client, service = make(tmp_path, [a, b])
    assert (
        client.post("/v1/chat/completions", json=chat("x", model="b/m")).json()["choices"][0][
            "message"
        ]["content"]
        == "from b"
    )
    assert client.post("/v1/chat/completions", json=chat("y", model="m")).status_code == 200
    answer = client.post("/v1/chat/completions", json=chat("z", model="some-other-model"))
    assert answer.status_code == 200
    task_ids = [t.task_id for t in service.sessions.tasks()]
    notes = [
        n
        for task_id in task_ids
        for e in read_events(tmp_path / ".stepswitch" / "runs" / task_id)
        if e["type"] == "decision"
        for n in e.get("notes", [])
    ]
    assert any("unknown model 'some-other-model'" in n for n in notes)


def test_aliases_map_a_harness_model_name_to_a_slot(tmp_path: Path) -> None:
    a = worker("a/m", says("from a"))
    b = worker("b/m", says("from b"))
    settings = ProxySettings(heartbeat_seconds=0.2, aliases={"big-model": "b/m"})
    client, _ = make(tmp_path, [a, b], settings=settings)
    answer = client.post("/v1/chat/completions", json=chat("x", model="big-model"))
    assert answer.json()["choices"][0]["message"]["content"] == "from b"


# -- state -----------------------------------------------------------------------------------


def test_stored_responses_continue_through_previous_response_id(tmp_path: Path) -> None:
    a = worker("a/m", says("Noted."))
    client, _ = make(tmp_path, [a])
    first = client.post(
        "/v1/responses", json={"model": "ssw:auto", "input": "My name is Ada."}
    ).json()
    second = client.post(
        "/v1/responses",
        json={
            "model": "ssw:auto",
            "input": "What is my name?",
            "previous_response_id": first["id"],
        },
    )
    assert second.status_code == 200
    seen = a.model.requests[-1]  # type: ignore[union-attr]
    assert [m.role for m in seen.messages] == ["user", "assistant", "user"]
    assert seen.messages[0].text_content() == "My name is Ada."
    missing = client.post(
        "/v1/responses",
        json={"model": "ssw:auto", "input": "x", "previous_response_id": "resp_nope"},
    )
    assert missing.status_code == 404
    unstored = client.post(
        "/v1/responses", json={"model": "ssw:auto", "input": "x", "store": False}
    ).json()
    again = client.post(
        "/v1/responses",
        json={"model": "ssw:auto", "input": "y", "previous_response_id": unstored["id"]},
    )
    assert again.status_code == 404


def test_every_request_is_a_recorded_step_without_credentials(tmp_path: Path) -> None:
    client, service = make(tmp_path, [worker("a/m", says())])
    turn = {"role": "user", "content": "Fix the bug in add()."}
    headers = {"x-stepswitch-session": "harness-run-1"}
    client.post("/v1/messages", json=messages_body(turn), headers=headers)
    client.post("/v1/messages", json=messages_body(turn, stream=True), headers=headers)
    [task] = service.sessions.tasks()
    assert task.task_id.startswith("p") and task.steps == 2
    db = open_project_state(tmp_path)
    row = db.execute(
        "SELECT status, instruction FROM tasks WHERE id = ?", (task.task_id,)
    ).fetchone()
    assert tuple(row) == ("running", "Fix the bug in add().")
    rows = task_rows(tmp_path, task.task_id)
    assert [r["step"] for r in rows] == [1, 2] and all(r["data"]["proxy"] for r in rows)
    assert rows[0]["input_tokens"] == 40 and rows[0]["usage_source"] == "scripted"
    run_dir = tmp_path / ".stepswitch" / "runs" / task.task_id
    types = [e["type"] for e in read_events(run_dir)]
    assert types[0] == "task_start" and types.count("decision") == 2 and types.count("step") == 2
    text = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert TOKEN not in text and "Bearer" not in text
    service.end_tasks(reason="proxy stopped")
    row = db.execute(
        "SELECT status, stop_reason FROM tasks WHERE id = ?", (task.task_id,)
    ).fetchone()
    assert tuple(row) == ("stopped", "proxy stopped")
    db.close()


def test_listings_and_token_counts(tmp_path: Path) -> None:
    client, _ = make(tmp_path, [worker("a/m", says(), context=32_768)])
    listed = client.get("/v1/models").json()
    ids = [m["id"] for m in listed["data"]]
    assert ids[:5] == ["ssw:auto", "ssw:plan", "ssw:edit", "ssw:fast", "ssw:review"]
    assert "a/m" in ids and listed["object"] == "list" and listed["has_more"] is False
    assert [m["name"] for m in client.get("/api/tags").json()["models"]] == ids
    shown = client.post("/api/show", json={"model": "a/m"}).json()
    assert shown["model_info"] == {"stepswitch.context_length": 32_768}
    assert client.post("/api/show", json={"model": "nope"}).status_code == 404
    counted = client.post(
        "/v1/messages/count_tokens", json=messages_body({"role": "user", "content": "x" * 400})
    )
    assert counted.json()["input_tokens"] >= 100
    assert counted.headers["x-stepswitch-estimate"] == "characters/4"


def test_a_streaming_upstream_is_streamed_through_its_own_codec(tmp_path: Path) -> None:
    chunks = [
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "model": "m",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hel"}}],
        },
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "model": "m",
            "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}],
        },
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "model": "m",
            "choices": [],
            "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14},
        },
    ]
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    endpoint = Endpoint(name="local", base_url="http://127.0.0.1:9", model="m")
    model = ModelClient(endpoint, transport=httpx.MockTransport(handler))
    upstream = Worker(WorkerSpec(id="local/m", endpoint="local", model="m", tier="t2"), model)
    client, service = make(tmp_path, [upstream])
    body = messages_body({"role": "user", "content": "Hi"}, stream=True)
    with client.stream("POST", "/v1/messages", json=body) as answer:
        events = sse(answer.read().decode())
    text = "".join(
        json.loads(d)["delta"].get("text", "") for n, d in events if n == "content_block_delta"
    )
    assert text == "Hello"
    assert seen[0]["stream"] is True and seen[0]["model"] == "m"
    [task] = service.sessions.tasks()
    [row] = task_rows(tmp_path, task.task_id)
    assert (row["input_tokens"], row["output_tokens"]) == (12, 2)


def test_concurrent_conversations_are_kept_apart(tmp_path: Path) -> None:
    client, service = make(tmp_path, [worker("a/m", says())])
    errors: list[BaseException] = []

    def ask(n: int) -> None:
        try:
            answer = client.post("/v1/chat/completions", json=chat(f"Task number {n}"))
            assert answer.status_code == 200
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=ask, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(service.sessions.tasks()) == 6


@pytest.mark.parametrize("offline", [True, False])
def test_offline_mode_routes_to_local_workers_only(tmp_path: Path, offline: bool) -> None:
    local = worker("a/m", says("local"), local=True)
    remote = worker("r/m", says("remote"), local=False)
    settings = ProxySettings(heartbeat_seconds=0.2, offline=offline, allow_remote=True)
    rules = [{"name": "remote-first", "when": {}, "prefer": {"id": "r/m"}, "bonus": 5.0}]
    client, _ = make(tmp_path, [local, remote], settings=settings, rules=rules)
    text = client.post("/v1/chat/completions", json=chat("Hi")).json()["choices"][0]["message"][
        "content"
    ]
    assert text == ("local" if offline else "remote")
