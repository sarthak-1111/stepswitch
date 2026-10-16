"""The dashboard's live feed (docs/API.md, Live events): the cursor logic on its own, and the
Server-Sent Events stream from a real server on a free loopback port."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from stepswitch.api.app import create_app
from stepswitch.api.events import Cursor, Feed
from stepswitch.state import StateDB, open_project_state

TOKEN = "live-test-token"
NOW = 1_760_600_000.0


def _task(db: StateDB, task_id: str, status: str, updated: float) -> None:
    db.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, NULL, 'general', 'x', '{}', NULL)"
        " ON CONFLICT(id) DO UPDATE SET status = excluded.status, updated = excluded.updated",
        (task_id, NOW, updated, status),
    )


def _rows(db: StateDB, task_id: str, step: int) -> None:
    """One decision, one model call and one trajectory event for a step."""
    db.execute(
        "INSERT INTO decisions (task_id, step, ts, step_type, chosen, switched, reason, data)"
        " VALUES (?, ?, ?, 'edit', 'w/m', 0, 'r', '{\"level\": 1}')",
        (task_id, step, NOW + step),
    )
    db.execute(
        "INSERT INTO ledger (task_id, step, ts, step_type, endpoint, model, input_tokens,"
        " output_tokens, cached_input_tokens, reasoning_tokens, wall_seconds, gpu_seconds,"
        " usage_source, data) VALUES (?, ?, ?, 'edit', 'w', 'm', 100, 10, 0, 0, 1.0, 0.0,"
        " 'server', '{}')",
        (task_id, step, NOW + step),
    )
    db.execute(
        "INSERT INTO events (task_id, ts, seq, type, step, data) VALUES (?, ?, ?, 'step', ?, '{}')",
        (task_id, NOW + step, step, step),
    )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[StateDB]:
    state = open_project_state(tmp_path)
    yield state
    state.close()


def test_cursor_round_trip() -> None:
    cursor = Cursor(event=3, ledger=2, decision=1, task=NOW + 0.25)
    assert Cursor.parse(str(cursor)) == cursor
    for text in (None, "", "1-2-3", "a-b-c-d", "1-2-3-4-5"):
        assert Cursor.parse(text) is None


def test_feed_starts_with_open_tasks_and_follows_new_rows(db: StateDB) -> None:
    _task(db, "t-old", "done", NOW + 1)
    _task(db, "t-open", "running", NOW + 2)
    _rows(db, "t-open", 1)
    feed, first = Feed.start(db, None)
    assert [(e.name, e.data["id"]) for e in first] == [("task", "t-open")]
    assert feed.poll() == []  # rows written before the start are not repeated

    _rows(db, "t-open", 2)
    _task(db, "t-open", "verifying", NOW + 3)
    events = feed.poll()
    assert [e.name for e in events] == ["task", "decision", "ledger", "trajectory"]
    assert events[0].data["status"] == "verifying"
    assert events[2].data["task_id"] == "t-open" and events[2].data["input_tokens"] == 100
    assert events[3].data == {
        "task_id": "t-open",
        "seq": 2,
        "ts": NOW + 2,
        "type": "step",
        "step": 2,
    }
    assert feed.poll() == []

    # A client that reconnects after the decision gets the ledger row and the event again.
    resumed, start = Feed.start(db, events[1].id)
    assert start == []
    again = [e.name for e in resumed.poll()]
    assert again.count("decision") == 0
    assert "ledger" in again and "trajectory" in again


def test_feed_events_are_server_sent_event_blocks(db: StateDB) -> None:
    _task(db, "t-open", "running", NOW + 2)
    _, events = Feed.start(db, None)
    text = events[0].encode().decode("utf-8")
    assert text.endswith("\n\n")
    lines = text.strip().split("\n")
    assert lines[0] == "event: task" and lines[1].startswith("id: ")
    assert json.loads(lines[2].removeprefix("data: "))["id"] == "t-open"


# --------------------------------------------------------------------------------------------
# A real server
# --------------------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def serving(app: Any, port: int) -> Iterator[uvicorn.Server]:
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", access_log=False, lifespan="off"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    ready = threading.Event()
    for _ in range(200):
        if server.started:
            break
        ready.wait(0.05)
    assert server.started, "the server did not start"
    try:
        yield server
    finally:
        server.should_exit = True
        thread.join(10)


def read_blocks(lines: Iterator[str], count: int) -> list[dict[str, str]]:
    """The next `count` SSE blocks: fields by name, comments under `comment`."""
    blocks: list[dict[str, str]] = []
    block: dict[str, str] = {}
    for line in lines:
        if line == "":
            if block:
                blocks.append(block)
                block = {}
            if len(blocks) == count:
                return blocks
            continue
        if line.startswith(":"):
            block["comment"] = line[1:].strip()
            continue
        name, _, value = line.partition(":")
        block[name] = value.removeprefix(" ")
    return blocks


def test_live_stream_over_http(tmp_path: Path) -> None:
    root = tmp_path / "project"
    db = open_project_state(root)
    _task(db, "t-open", "running", NOW + 2)
    port = free_port()
    stop = threading.Event()
    app = create_app(
        root,
        token=TOKEN,
        port=port,
        static_dir=tmp_path / "none",
        poll_interval=0.05,
        heartbeat=0.3,
        stopping=stop.is_set,
    )
    base = f"http://127.0.0.1:{port}"
    auth = {"authorization": f"Bearer {TOKEN}"}
    with serving(app, port), httpx.Client(base_url=base, timeout=10.0) as client:
        assert client.get("/api/events").status_code == 401
        with client.stream("GET", "/api/events", headers=auth) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.iter_lines()
            retry, task = read_blocks(lines, 2)
            assert retry == {"retry": "2000"}
            assert task["event"] == "task" and json.loads(task["data"])["id"] == "t-open"
            (beat,) = read_blocks(lines, 1)
            assert beat == {"comment": "keep-alive"}
            _rows(db, "t-open", 1)
            blocks = read_blocks(lines, 3)
            assert [b["event"] for b in blocks] == ["decision", "ledger", "trajectory"]
            last = blocks[0]["id"]
        headers = {**auth, "last-event-id": last}
        with client.stream("GET", "/api/events", headers=headers) as response:
            lines = response.iter_lines()
            # A resumed stream may repeat recent task rows (clients replace tasks by id).
            retry, *blocks = read_blocks(lines, 4)
            names = [b["event"] for b in blocks]
            assert [n for n in names if n != "task"] == ["ledger", "trajectory"]
        stop.set()
    db.close()
    app.state.services.close()
