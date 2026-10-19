"""The proxy bound to a caller's task (ADR 0029): `ProxyService.attach`, the route-only
decisions of the Pi extension's virtual model, and requests shaped like the harnesses' own."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stepswitch.ledger import Ledger
from stepswitch.state import open_project_state
from stepswitch.trajectory.record import Trajectory, read_events
from stepswitch.workers.fake import Action
from tests.unit.test_proxy import chat, make, messages_body, says, sse, worker

TURN = {"role": "user", "content": "Fix the bug in add()."}
TOOLS = [{"name": "read", "description": "Read a file", "input_schema": {"type": "object"}}]


def attached(tmp_path: Path, client_service: Any, **fields: Any) -> tuple[Any, Any, Path]:
    client, service = client_service
    db = open_project_state(tmp_path)
    trajectory = Trajectory(tmp_path, "t-caller", db=db)
    task = service.attach(
        "t-caller",
        task_id="t-caller",
        trajectory=trajectory,
        ledger=Ledger(db, "t-caller"),
        instruction="Fix the bug.",
        **fields,
    )
    return client, task, trajectory.run_dir


def test_an_attached_task_records_into_the_callers_task(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    client, task, run_dir = attached(
        tmp_path, make(tmp_path, [worker("a/m", says())]), on_step=seen.append
    )
    # The proxy is bound: every request is the caller's, whatever its headers say.
    client.post("/v1/messages", json=messages_body(TURN), headers={"x-stepswitch-session": "x"})
    client.post("/v1/chat/completions", json=chat("hello"))
    assert task.steps == 2 and [s["step"] for s in seen] == [1, 2]
    assert seen[0]["worker"] == "a/m" and seen[0]["usage"]["input_tokens"] == 40
    events = read_events(run_dir)
    assert [e["type"] for e in events].count("step") == 2
    assert "task_start" not in [e["type"] for e in events]  # the caller's own task row
    db = open_project_state(tmp_path)
    rows = db.execute("SELECT task_id FROM ledger").fetchall()
    assert {row[0] for row in rows} == {"t-caller"}
    db.close()


def test_a_used_up_budget_refuses_further_requests(tmp_path: Path) -> None:
    state = {"limit": None}
    client, task, _ = attached(
        tmp_path, make(tmp_path, [worker("a/m", says())]), limit=lambda: state["limit"]
    )
    assert client.post("/v1/messages", json=messages_body(TURN)).status_code == 200
    state["limit"] = "budget_tokens"
    refused = client.post("/v1/messages", json=messages_body(TURN))
    assert refused.status_code == 429
    assert "budget_tokens" in refused.json()["error"]["message"]
    assert task.steps == 1


def test_attached_tasks_survive_the_proxys_end_and_detach_cleanly(tmp_path: Path) -> None:
    client_service = make(tmp_path, [worker("a/m", says())])
    client, task, _ = attached(tmp_path, client_service)
    service = client_service[1]
    client.post("/v1/messages", json=messages_body(TURN))
    assert service.end_tasks(reason="proxy stopped") == []
    assert service.detach("t-caller") is task and service.bound is None
    assert service.sessions.tasks() == []


def test_route_only_reserves_the_step_for_the_chosen_worker(tmp_path: Path) -> None:
    workers = [
        worker("a/small", says(), tier="t1", context=8192),
        worker("b/large", says(), tier="t2", context=131072),
    ]
    client, task, run_dir = attached(tmp_path, make(tmp_path, workers))
    listed = {m["id"]: m for m in client.get("/v1/models").json()["data"]}
    assert listed["b/large"]["context_window"] == 131072
    assert "context_window" not in listed["ssw:auto"]
    # A transcript that fits only the large worker's window goes there.
    route = {"model": "ssw-auto", "reason": "user", "transcript_tokens": 20000, "tools": True}
    answer = client.post("/stepswitch/v1/route", json=route).json()
    assert answer["worker"] == "b/large" and answer["context"] == 131072
    assert answer["step"] == 1
    # The harness then names that worker: the reserved decision is that step, not a new one.
    client.post("/v1/messages", json=messages_body(TURN, model="b/large"))
    events = read_events(run_dir)
    decisions = [e for e in events if e["type"] == "decision"]
    steps = [e for e in events if e["type"] == "step"]
    assert len(decisions) == 1 and [s["step"] for s in steps] == [1]
    assert task.reserved == {} and task.steps == 1
    # Inside a turn the previous worker holds; a failed one is left out.
    held = {**route, "reason": "continuation", "previous": "b/large", "transcript_tokens": 100}
    assert client.post("/stepswitch/v1/route", json=held).json()["worker"] == "b/large"
    moved = {**held, "failed": "b/large"}
    assert client.post("/stepswitch/v1/route", json=moved).json()["worker"] == "a/small"


def test_requests_shaped_like_kimi_codes_are_answered(tmp_path: Path) -> None:
    client, _ = make(tmp_path, [worker("a/m", [Action(tool="read", arguments={"path": "x"})])])
    body = {
        "model": "ssw-auto",
        "messages": [
            {"role": "user", "content": "Read x."},
            {"role": "assistant", "content": "", "reasoning_content": "Thinking about x."},
            {"role": "user", "content": "Go on."},
        ],
        "tools": [
            {
                "type": "function",
                "function": {"name": "read", "description": "Read", "parameters": {}},
            }
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "prompt_cache_key": "session-1",
        "thinking": {"type": "enabled", "effort": "high", "keep": "all"},
        "max_completion_tokens": 250000,
    }
    reply = client.post("/v1/chat/completions", json=body)
    assert reply.status_code == 200, reply.text
    chunks = [json.loads(data) for _, data in sse(reply.text) if data != "[DONE]"]
    calls = [
        call
        for chunk in chunks
        for choice in chunk.get("choices", [])
        for call in (choice.get("delta") or {}).get("tool_calls") or []
    ]
    assert calls and calls[0]["function"]["name"] == "read"
    assert any(chunk.get("usage") for chunk in chunks)  # include_usage
