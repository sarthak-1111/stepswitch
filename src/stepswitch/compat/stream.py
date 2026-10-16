"""Assemble streaming deltas into a response, and expand a response into canonical deltas."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import (
    Delta,
    ErrorDelta,
    FinishDelta,
    Message,
    Origin,
    Part,
    ReasoningDelta,
    ReasoningPart,
    RefusalDelta,
    RefusalPart,
    Response,
    StartDelta,
    TextDelta,
    TextPart,
    ToolCallArgsDelta,
    ToolCallEndDelta,
    ToolCallPart,
    ToolCallStartDelta,
    Usage,
    UsageDelta,
)


def merge_usage(current: Usage | None, update: Usage) -> Usage:
    """Later non-null fields win; formats report usage in pieces (start, then final counts)."""
    if current is None:
        return update
    data = current.model_dump()
    for key, value in update.model_dump().items():
        if value is not None:
            data[key] = value
    return Usage.model_validate(data)


class StreamAssembler:
    """Folds a delta stream into a `Response`, preserving part order.

    `origin` labels reasoning whose deltas carry none (the client passes the endpoint's format,
    name and model), so streamed reasoning can be replayed to its own model like reasoning
    from a non-streamed response. A reasoning block ends at its opaque payload or when the
    `item_id` changes.
    """

    def __init__(self, origin: Origin | None = None) -> None:
        self.response = Response()
        self._origin = origin
        self._parts: list[Part] = []
        self._tool_args: dict[int, list[str]] = {}
        self._tool_parts: dict[int, ToolCallPart] = {}
        self.error: ErrorDelta | None = None

    def feed(self, delta: Delta) -> None:
        if isinstance(delta, StartDelta):
            self.response.id = delta.id or self.response.id
            self.response.model = delta.model or self.response.model
            if self._origin is not None and self._origin.model is None and delta.model:
                self._origin = self._origin.model_copy(update={"model": delta.model})
        elif isinstance(delta, TextDelta):
            if self._parts and isinstance(self._parts[-1], TextPart):
                self._parts[-1].text += delta.text
            else:
                self._parts.append(TextPart(text=delta.text))
        elif isinstance(delta, ReasoningDelta):
            self._reasoning(delta)
        elif isinstance(delta, RefusalDelta):
            if self._parts and isinstance(self._parts[-1], RefusalPart):
                self._parts[-1].text += delta.text
            else:
                self._parts.append(RefusalPart(text=delta.text))
        elif isinstance(delta, ToolCallStartDelta):
            part = ToolCallPart(
                id=delta.id, name=delta.name, arguments="", synthetic=delta.synthetic
            )
            self._tool_parts[delta.index] = part
            self._tool_args[delta.index] = []
            self._parts.append(part)
        elif isinstance(delta, ToolCallArgsDelta):
            self._tool_args.setdefault(delta.index, []).append(delta.delta)
        elif isinstance(delta, ToolCallEndDelta):
            self._close_tool(delta.index)
        elif isinstance(delta, UsageDelta):
            self.response.usage = merge_usage(self.response.usage, delta.usage)
        elif isinstance(delta, FinishDelta):
            self.response.finish_reason = delta.reason
            self.response.raw_finish_reason = delta.raw
        elif isinstance(delta, ErrorDelta):
            self.error = delta

    def _reasoning(self, delta: ReasoningDelta) -> None:
        last = self._parts[-1] if self._parts else None
        origin = delta.origin or self._origin
        if (
            isinstance(last, ReasoningPart)
            and last.opaque is None
            and (delta.item_id is None or delta.item_id == last.item_id)
        ):
            if delta.text:
                last.text = (last.text or "") + delta.text
            if delta.summary:
                last.summary = (last.summary or "") + delta.summary
            if delta.opaque:
                last.opaque, last.opaque_kind = delta.opaque, delta.opaque_kind
            last.item_id = last.item_id or delta.item_id
            last.origin = last.origin or origin
            return
        self._parts.append(
            ReasoningPart(
                text=delta.text or None,
                summary=delta.summary or None,
                opaque=delta.opaque,
                opaque_kind=delta.opaque_kind,
                item_id=delta.item_id,
                origin=origin,
            )
        )

    def _close_tool(self, index: int) -> None:
        part = self._tool_parts.get(index)
        if part is not None:
            part.arguments = "".join(self._tool_args.get(index, [])) or "{}"

    def result(self) -> Response:
        for index in list(self._tool_parts):
            self._close_tool(index)
        self.response.message = Message(role="assistant", content=list(self._parts))
        if self._tool_parts and self.response.finish_reason == "stop":
            self.response.finish_reason = "tool_calls"
        return self.response


def assemble(deltas: Iterable[Delta]) -> Response:
    assembler = StreamAssembler()
    for delta in deltas:
        assembler.feed(delta)
    if assembler.error is not None:
        raise ModelError(
            "server" if assembler.error.kind == "server" else assembler.error.kind,  # type: ignore[arg-type]
            assembler.error.message,
            status=assembler.error.status,
        )
    return assembler.result()


def response_to_deltas(response: Response, chunk: int = 0) -> Iterator[Delta]:
    """Canonical delta sequence for a complete response (used to stream non-streamed answers).

    `chunk` > 0 splits text and argument strings into pieces of that many characters, which
    exercises incremental consumers in tests.
    """

    def pieces(text: str) -> list[str]:
        if chunk <= 0 or len(text) <= chunk:
            return [text]
        return [text[i : i + chunk] for i in range(0, len(text), chunk)]

    yield StartDelta(id=response.id, model=response.model)
    tool_index = 0
    for part in response.message.content:
        if isinstance(part, TextPart):
            for piece in pieces(part.text):
                yield TextDelta(text=piece)
        elif isinstance(part, ReasoningPart):
            item_id, origin = part.item_id, part.origin
            if part.text:
                for piece in pieces(part.text):
                    yield ReasoningDelta(text=piece, item_id=item_id, origin=origin)
            if part.summary:
                yield ReasoningDelta(summary=part.summary, item_id=item_id, origin=origin)
            if part.opaque:
                yield ReasoningDelta(
                    opaque=part.opaque, opaque_kind=part.opaque_kind, item_id=item_id, origin=origin
                )
        elif isinstance(part, RefusalPart):
            for piece in pieces(part.text):
                yield RefusalDelta(text=piece)
        elif isinstance(part, ToolCallPart):
            yield ToolCallStartDelta(
                index=tool_index, id=part.id, name=part.name, synthetic=part.synthetic
            )
            for piece in pieces(part.arguments):
                yield ToolCallArgsDelta(index=tool_index, delta=piece)
            yield ToolCallEndDelta(index=tool_index)
            tool_index += 1
    if response.usage is not None:
        yield UsageDelta(usage=response.usage)
    yield FinishDelta(reason=response.finish_reason, raw=response.raw_finish_reason)
