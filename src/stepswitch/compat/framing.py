"""Stream framing: server-sent events and newline-delimited JSON, in both directions."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    """One event: an SSE event (optional name + data) or one NDJSON line (data only)."""

    data: str
    event: str | None = None

    def json(self) -> Any:
        return json.loads(self.data)


def parse_sse(lines: Iterable[str]) -> Iterator[StreamEvent]:
    """Parse SSE from decoded text lines (without trailing newlines), per the HTML spec rules."""
    event: str | None = None
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data:
                yield StreamEvent(data="\n".join(data), event=event)
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if data:
        yield StreamEvent(data="\n".join(data), event=event)


def parse_ndjson(lines: Iterable[str]) -> Iterator[StreamEvent]:
    for raw in lines:
        line = raw.strip()
        if line:
            yield StreamEvent(data=line)


def format_sse(event: StreamEvent) -> str:
    head = f"event: {event.event}\n" if event.event else ""
    body = "".join(f"data: {line}\n" for line in event.data.split("\n"))
    return f"{head}{body}\n"


def format_ndjson(event: StreamEvent) -> str:
    return event.data + "\n"


def sse_json(payload: Any, event: str | None = None) -> StreamEvent:
    return StreamEvent(
        data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False), event=event
    )


def iter_lines(chunks: Iterable[str]) -> Iterator[str]:
    """Split decoded text chunks into lines, keeping empty lines (SSE event boundaries)."""
    buffer = ""
    for chunk in chunks:
        buffer += chunk
        while True:
            index = buffer.find("\n")
            if index < 0:
                break
            yield buffer[:index].rstrip("\r")
            buffer = buffer[index + 1 :]
    if buffer:
        yield buffer.rstrip("\r")
