"""Incremental tool-call extraction from streamed text (prompt-based formats).

Plain text passes through as soon as it cannot be the start of a tool-call marker. Once a
marker appears, the rest of the output is buffered and parsed at the end of the stream with the
configured syntax from `stepswitch.compat.toolcall.parsers`. A syntax whose marker servers may
drop (`parsers.LEADING_MARKERS`) also buffers a reply that opens with its leading text, such
as the `[` of a bare granite call list; leading whitespace is held until that is decided.
"""

from __future__ import annotations

from stepswitch.compat.ir import (
    Delta,
    TextDelta,
    ToolCallArgsDelta,
    ToolCallEndDelta,
    ToolCallPart,
    ToolCallStartDelta,
    synthetic_call_id,
)
from stepswitch.compat.toolcall import parsers

DEFAULT_MARKERS = tuple(marker for marker, _ in parsers.MARKERS)


def extract_calls(text: str, syntax: str | None, turn: int = 0) -> tuple[str, list[ToolCallPart]]:
    """Non-streaming: split final text into remaining text and tool calls (synthetic IDs)."""
    result = parsers.parse(text, syntax)
    calls = [
        ToolCallPart(
            id=synthetic_call_id(turn, index),
            name=call.name,
            arguments=call.arguments,
            synthetic=True,
        )
        for index, call in enumerate(result.calls)
    ]
    return result.text, calls


class StreamingToolCallExtractor:
    def __init__(
        self, syntax: str | None = None, markers: tuple[str, ...] = DEFAULT_MARKERS, turn: int = 0
    ):
        self.syntax = syntax
        self.markers = markers
        self.leading = parsers.LEADING_MARKERS.get(syntax or "", ())
        self.turn = turn
        self._pending = ""
        self._at_start = bool(self.leading)
        self._capturing = False
        self._captured = ""
        self.calls: list[ToolCallPart] = []

    def _marker_start(self, text: str) -> int:
        positions = [text.find(m) for m in self.markers if m in text]
        return min(positions) if positions else -1

    def _holdback(self, text: str) -> int:
        """Length of the longest suffix of `text` that is a proper prefix of some marker."""
        best = 0
        for marker in self.markers:
            for size in range(min(len(marker) - 1, len(text)), 0, -1):
                if marker.startswith(text[-size:]):
                    best = max(best, size)
                    break
        return best

    def feed(self, chunk: str) -> list[Delta]:
        if self._capturing:
            self._captured += chunk
            return []
        text = self._pending + chunk
        if self._at_start:
            head = text.lstrip()
            if not head:
                self._pending = text
                return []
            self._at_start = False
            if head.startswith(self.leading):
                self._capturing = True
                self._captured = text
                self._pending = ""
                return []
        start = self._marker_start(text)
        if start >= 0:
            self._capturing = True
            self._captured = text[start:]
            self._pending = ""
            return [TextDelta(text=text[:start])] if start > 0 else []
        keep = self._holdback(text)
        emit, self._pending = (text[:-keep], text[-keep:]) if keep else (text, "")
        return [TextDelta(text=emit)] if emit else []

    def finish(self, *, parse: bool = True) -> list[Delta]:
        """Flush what is held back; with `parse=False` a captured section stays text."""
        deltas: list[Delta] = []
        if self._pending:
            deltas.append(TextDelta(text=self._pending))
            self._pending = ""
        if not self._capturing:
            return deltas
        if not parse:
            deltas.append(TextDelta(text=self._captured))
            return deltas
        remaining, calls = extract_calls(self._captured, self.syntax, self.turn)
        if not calls:
            deltas.append(TextDelta(text=self._captured))
            return deltas
        if remaining:
            deltas.append(TextDelta(text=remaining))
        for index, call in enumerate(calls):
            deltas.append(
                ToolCallStartDelta(index=index, id=call.id, name=call.name, synthetic=True)
            )
            deltas.append(ToolCallArgsDelta(index=index, delta=call.arguments))
            deltas.append(ToolCallEndDelta(index=index))
        self.calls = calls
        return deltas
