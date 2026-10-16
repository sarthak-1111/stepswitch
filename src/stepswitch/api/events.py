"""The dashboard's live feed: new state-database rows as Server-Sent Events (docs/API.md).

Tasks write to the state database from whichever process runs them (the dashboard's runner or
`ssw run` in a terminal), so the feed polls the database rather than listening inside one
process. A cursor holds the last trajectory event, ledger row and decision sent, and the newest
task change; every event's `id` is the cursor just after it, so a client that reconnects with
`Last-Event-ID` continues exactly where it stopped.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from typing import Any

import anyio
from starlette.concurrency import run_in_threadpool

from stepswitch.api import queries
from stepswitch.core.task import FINAL_STATUSES
from stepswitch.state import StateDB

BATCH = 500
# Task rows are re-read this far back: a row written by another process can commit slightly
# after a newer one; duplicates are harmless since clients replace a task by its id.
TASK_OVERLAP_SECONDS = 5.0
RETRY_MS = 2000


@dataclass(frozen=True)
class Cursor:
    event: int = 0
    ledger: int = 0
    decision: int = 0
    task: float = 0.0

    def __str__(self) -> str:
        return f"{self.event}-{self.ledger}-{self.decision}-{self.task!r}"

    @classmethod
    def parse(cls, text: str | None) -> Cursor | None:
        parts = (text or "").strip().split("-")
        if len(parts) != 4:
            return None
        try:
            return cls(int(parts[0]), int(parts[1]), int(parts[2]), float(parts[3]))
        except ValueError:
            return None


@dataclass(frozen=True)
class Event:
    name: str
    data: Any
    id: str | None = None

    def encode(self) -> bytes:
        lines = [f"event: {self.name}"]
        if self.id is not None:
            lines.append(f"id: {self.id}")
        # JSON text has no raw line breaks, so one data line carries it.
        lines.append("data: " + json.dumps(self.data, separators=(",", ":"), default=str))
        return ("\n".join(lines) + "\n\n").encode("utf-8")


def _max(db: StateDB, sql: str) -> float:
    rows = db.query(sql)
    value = rows[0]["value"] if rows else None
    return float(value) if value is not None else 0.0


def current(db: StateDB) -> Cursor:
    """The cursor at the newest row of every table."""
    return Cursor(
        event=int(_max(db, "SELECT MAX(id) AS value FROM events")),
        ledger=int(_max(db, "SELECT MAX(id) AS value FROM ledger")),
        decision=int(_max(db, "SELECT MAX(id) AS value FROM decisions")),
        task=_max(db, "SELECT MAX(updated) AS value FROM tasks"),
    )


class Feed:
    """Turns database changes after a cursor into events (one feed per client)."""

    def __init__(self, db: StateDB, cursor: Cursor) -> None:
        self.db = db
        self.cursor = cursor
        self._sent: dict[str, float] = {}

    @classmethod
    def start(cls, db: StateDB, last_event_id: str | None) -> tuple[Feed, list[Event]]:
        """A feed resuming after `last_event_id`, or one starting now with the current state
        of the tasks that have not ended."""
        resumed = Cursor.parse(last_event_id)
        if resumed is not None:
            return cls(db, resumed), []
        feed = cls(db, current(db))
        # Rows inside the overlap window are part of the starting state, not news.
        for row in db.query(
            "SELECT id, updated FROM tasks WHERE updated > ?",
            (feed.cursor.task - TASK_OVERLAP_SECONDS,),
        ):
            feed._sent[row["id"]] = row["updated"]
        final = tuple(sorted(FINAL_STATUSES))
        rows = db.query(
            f"SELECT * FROM tasks WHERE status NOT IN ({', '.join('?' for _ in final)})"
            " ORDER BY created",
            final,
        )
        events = []
        for summary in queries.summaries(db, rows):
            feed._sent[summary["id"]] = summary["updated"]
            events.append(Event("task", summary, str(feed.cursor)))
        return feed, events

    def poll(self) -> list[Event]:
        return [*self._tasks(), *self._decisions(), *self._ledger(), *self._trajectory()]

    def _tasks(self) -> list[Event]:
        rows = self.db.query(
            "SELECT * FROM tasks WHERE updated > ? ORDER BY updated LIMIT ?",
            (self.cursor.task - TASK_OVERLAP_SECONDS, BATCH),
        )
        fresh = [row for row in rows if self._sent.get(row["id"]) != row["updated"]]
        events = []
        for summary in queries.summaries(self.db, fresh):
            self._sent[summary["id"]] = summary["updated"]
            self.cursor = replace(self.cursor, task=max(self.cursor.task, summary["updated"]))
            events.append(Event("task", summary, str(self.cursor)))
        horizon = self.cursor.task - TASK_OVERLAP_SECONDS
        self._sent = {key: value for key, value in self._sent.items() if value > horizon}
        return events

    def _decisions(self) -> list[Event]:
        events = []
        for row in self.db.query(
            "SELECT * FROM decisions WHERE id > ? ORDER BY id LIMIT ?",
            (self.cursor.decision, BATCH),
        ):
            self.cursor = replace(self.cursor, decision=int(row["id"]))
            events.append(Event("decision", queries.decision(row), str(self.cursor)))
        return events

    def _ledger(self) -> list[Event]:
        events = []
        for row in self.db.query(
            "SELECT * FROM ledger WHERE id > ? ORDER BY id LIMIT ?", (self.cursor.ledger, BATCH)
        ):
            self.cursor = replace(self.cursor, ledger=int(row["id"]))
            data = {"task_id": row["task_id"], **queries.ledger_row(row)}
            events.append(Event("ledger", data, str(self.cursor)))
        return events

    def _trajectory(self) -> list[Event]:
        events = []
        for row in self.db.query(
            "SELECT id, task_id, seq, ts, type, step FROM events WHERE id > ? ORDER BY id LIMIT ?",
            (self.cursor.event, BATCH),
        ):
            self.cursor = replace(self.cursor, event=int(row["id"]))
            data = {key: row[key] for key in ("task_id", "seq", "ts", "type", "step")}
            events.append(Event("trajectory", data, str(self.cursor)))
        return events


async def stream(
    db: StateDB,
    last_event_id: str | None,
    *,
    interval: float = 0.5,
    heartbeat: float = 15.0,
    stopping: Callable[[], bool] = lambda: False,
) -> AsyncIterator[bytes]:
    """The SSE byte stream: the starting state, then new rows every `interval` seconds, with a
    comment line after `heartbeat` quiet seconds."""
    feed, events = await run_in_threadpool(Feed.start, db, last_event_id)
    yield f"retry: {RETRY_MS}\n\n".encode()
    for event in events:
        yield event.encode()
    beat = time.monotonic()
    while not stopping():
        await anyio.sleep(interval)
        events = await run_in_threadpool(feed.poll)
        for event in events:
            yield event.encode()
        now = time.monotonic()
        if events:
            beat = now
        elif now - beat >= heartbeat:
            yield b": keep-alive\n\n"
            beat = now
