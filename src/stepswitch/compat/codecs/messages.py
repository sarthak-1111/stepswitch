"""Codec for the messages format (ADR 0003; docs/RESEARCH.md, "API formats").

Requests go to `POST /v1/messages` with the format's version header (`anthropic-version`,
default `2023-06-01`, overridden by `CodecOptions.api_version`). Wire details follow the
format's references for creating messages, streaming, errors, effort and thinking (checked
2026-10-16). Choices the IR leaves open:

* Content is always sent as block lists. IR messages that map to the same wire role are merged,
  because the format alternates `user` and `assistant` turns, and `tool_result` blocks lead
  their user turn. IR system messages are hoisted into the top-level `system` field.
* `max_tokens` is required: without an IR limit, 4096 is sent and the default is reported.
* Effort goes to `output_config.effort` (`low` to `max`; `minimal` is clamped to `low`). `none`
  becomes `thinking: {"type": "disabled"}` and `budget_tokens` becomes
  `thinking: {"type": "enabled", "budget_tokens": N}` (minimum 1024). Which of these a model
  accepts differs per model; the capability probe decides, not this codec.
* `thinking` and `redacted_thinking` blocks are replayed only for reasoning whose origin is this
  format. Reasoning from anywhere else is dropped and reported (ADR 0003, rule 1).
* `Usage.input_tokens` = `input_tokens` + `cache_read_input_tokens` +
  `cache_creation_input_tokens`; reasoning tokens come from `output_tokens_details`.
* `decode_request` keeps top-level fields the IR does not model in
  `Request.extensions["messages"]` as `{"body": {...}, "headers": {...}}`, and `encode_request`
  adds them back for this format only (fields produced from the IR win).
* `decode_request`, `decode_response` and `encode_response` accept an optional `report` that
  receives whatever the IR cannot carry (block-level cache hints, server-tool blocks, ...).
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import re
import uuid
import zlib
from collections.abc import Iterable, Iterator
from typing import Any, ClassVar, Literal

from stepswitch.compat.codecs.base import Codec, Framing, HttpCall, StreamState
from stepswitch.compat.errors import ErrorKind, ModelError, kind_for_status, parse_retry_after
from stepswitch.compat.framing import StreamEvent, sse_json
from stepswitch.compat.ir import (
    EFFORT_LEVELS,
    ConversionError,
    ConversionReport,
    Delta,
    Effort,
    ErrorDelta,
    FilePart,
    FinishDelta,
    FinishReason,
    ImagePart,
    Message,
    NamedToolChoice,
    Origin,
    Part,
    ReasoningDelta,
    ReasoningPart,
    RefusalPart,
    Request,
    Response,
    ResponseFormat,
    Sampling,
    StartDelta,
    TextDelta,
    TextPart,
    ToolCallArgsDelta,
    ToolCallEndDelta,
    ToolCallPart,
    ToolCallStartDelta,
    ToolChoice,
    ToolResultPart,
    ToolSpec,
    Usage,
    UsageDelta,
    synthetic_call_id,
)
from stepswitch.compat.stream import merge_usage

PATH = "/v1/messages"
VERSION_HEADER = "anthropic-version"
BETA_HEADER = "anthropic-beta"
DEFAULT_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
MIN_THINKING_BUDGET = 1024
MAX_TEMPERATURE = 1.0
WIRE_EFFORTS = ("low", "medium", "high", "xhigh", "max")

STOP_REASONS: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
    "pause_turn": "other",
}
FINISH_TO_STOP: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "refusal",
    "error": "end_turn",
    "other": "end_turn",
}
ERROR_TYPE_KINDS: dict[str, ErrorKind] = {
    "invalid_request_error": "bad_request",
    "authentication_error": "authentication",
    "billing_error": "permission",
    "permission_error": "permission",
    "not_found_error": "not_found",
    "conflict_error": "bad_request",
    "request_too_large": "too_large",
    "rate_limit_error": "rate_limit",
    "api_error": "server",
    "timeout_error": "timeout",
    "overloaded_error": "overloaded",
}
# Status and error type sent for each error kind (server side).
KIND_ERRORS: dict[str, tuple[int, str]] = {
    "bad_request": (400, "invalid_request_error"),
    "unsupported": (400, "invalid_request_error"),
    "authentication": (401, "authentication_error"),
    "permission": (403, "permission_error"),
    "blocked": (403, "permission_error"),
    "not_found": (404, "not_found_error"),
    "too_large": (413, "request_too_large"),
    "rate_limit": (429, "rate_limit_error"),
    "server": (500, "api_error"),
    "invalid_response": (502, "api_error"),
    "connection": (502, "api_error"),
    "timeout": (504, "timeout_error"),
    "overloaded": (529, "overloaded_error"),
}
# Top-level request fields the IR models; everything else goes to `Request.extensions`.
MODELED_FIELDS = frozenset(
    {
        "model",
        "messages",
        "system",
        "max_tokens",
        "stop_sequences",
        "stream",
        "temperature",
        "top_p",
        "top_k",
        "tools",
        "tool_choice",
        "thinking",
        "output_config",
    }
)
# Reply fields the IR carries; others (container, diagnostics, ...) are reported when decoding.
RESPONSE_FIELDS = frozenset(
    {
        "id",
        "type",
        "role",
        "model",
        "content",
        "stop_reason",
        "stop_sequence",
        "stop_details",
        "usage",
    }
)
REFUSAL_FIELDS = frozenset({"type", "explanation"})
MESSAGE_FIELDS = frozenset({"role", "content"})
_EFFORTS: dict[str, Effort] = {level: level for level in EFFORT_LEVELS}
_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_DATA_URI_RE = re.compile(r"^data:(?P<media>[\w.+-]+/[\w.+-]+)?(?P<params>(;[^,;]*)*);base64,")

Action = Literal["dropped", "approximated", "rejected"]


class _Losses:
    """Reports losses, or raises `ConversionError` in strict mode."""

    def __init__(self, report: ConversionReport | None, strict: bool = False) -> None:
        self.report = report
        self.strict = strict

    def add(self, field: str, detail: str, action: Action = "dropped") -> None:
        if self.strict:
            raise ConversionError(f"{field}: {detail}")
        if self.report is not None:
            self.report.add(field, detail, action)


class MessagesCodec(Codec):
    key: ClassVar[str] = "messages"
    format: ClassVar[str] = "messages"
    framing: ClassVar[Framing] = "sse"

    # ---- client side ----------------------------------------------------------------------

    def encode_request(self, request: Request, report: ConversionReport) -> HttpCall:
        loss = _Losses(report, self.options.strict)
        body: dict[str, Any] = {"model": request.model}
        system, turns = self._encode_conversation(request, loss)
        if system:
            body["system"] = system[0]["text"] if len(system) == 1 else system
        body["messages"] = turns
        if request.max_output_tokens is None:
            loss.add(
                "max_output_tokens",
                f"the messages format requires max_tokens; sent {DEFAULT_MAX_TOKENS}",
                "approximated",
            )
            body["max_tokens"] = DEFAULT_MAX_TOKENS
        else:
            body["max_tokens"] = request.max_output_tokens
        _encode_sampling(request.sampling, body, loss)
        _encode_tools(request, body, loss)
        output_config: dict[str, Any] = {}
        _encode_response_format(request.response_format, output_config, loss)
        _encode_reasoning(request, body, output_config, loss)
        if output_config:
            body["output_config"] = output_config
        if request.stream:
            body["stream"] = True
        if request.prompt is not None:
            loss.add("prompt", "pre-rendered prompts are only sent to prompt-based formats")
        headers = {VERSION_HEADER: self.options.api_version or DEFAULT_VERSION}
        _apply_extensions(request.extensions, self.format, body, headers, loss)
        return HttpCall(method="POST", path=PATH, json=body, headers=headers, stream=request.stream)

    def decode_response(self, body: Any, *, report: ConversionReport | None = None) -> Response:
        if not isinstance(body, dict):
            raise ModelError("invalid_response", "the response body is not a JSON object", raw=body)
        if body.get("type") == "error":
            raise self.decode_error(500, body)
        loss = _Losses(report)
        model = _str(body.get("model"))
        response_id = _str(body.get("id"))
        origin = Origin(format=self.format, model=model)
        turn = _turn_number(response_id)
        parts: list[Part] = []
        calls = 0
        for index, raw in enumerate(_list(body.get("content"))):
            block = _dict(raw)
            if block.get("type") == "tool_use":
                parts.append(_tool_call(block, turn, calls))
                calls += 1
                continue
            part = _decode_block(block, origin, loss, f"content[{index}]")
            if part is not None:
                parts.append(part)
        _note_extras(body, RESPONSE_FIELDS, loss, "response")
        details = _dict(body.get("stop_details"))
        explanation = _str(details.get("explanation"))
        if details.get("type") == "refusal" and explanation:
            parts.append(RefusalPart(text=explanation))
            _note_extras(details, REFUSAL_FIELDS, loss, "stop_details")
        elif details:
            loss.add("stop_details", f"stop details {sorted(details)} are not carried")
        stop = _str(body.get("stop_reason"))
        return Response(
            id=response_id,
            model=model,
            message=Message(role="assistant", content=parts),
            finish_reason=_finish(stop),
            raw_finish_reason=stop,
            usage=_usage(body.get("usage")),
        )

    def decode_stream_event(self, event: StreamEvent, state: StreamState) -> list[Delta]:
        try:
            data = json.loads(event.data)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict) or state.finished:
            return []
        kind = _str(data.get("type")) or event.event or ""
        index = _int(data.get("index")) or 0
        out: list[Delta] = []
        if kind == "message_start":
            message = _dict(data.get("message"))
            state.started = True
            state.data["turn"] = _turn_number(_str(message.get("id")))
            out.append(StartDelta(id=_str(message.get("id")), model=_str(message.get("model"))))
            usage = _dict(message.get("usage"))
            if usage:
                state.data["usage"] = dict(usage)
                out.append(UsageDelta(usage=_usage_from(usage)))
        elif kind == "content_block_start":
            out.extend(self._block_start(index, _dict(data.get("content_block")), state))
        elif kind == "content_block_delta":
            out.extend(_block_delta(index, _dict(data.get("delta")), state))
        elif kind == "content_block_stop":
            if index in state.open_tools:
                tool = state.open_tools.pop(index)
                start_input = state.data.get("start_input", {}).pop(index, None)
                if start_input and index not in state.data.get("streamed", set()):
                    out.append(ToolCallArgsDelta(index=tool, delta=_compact(start_input)))
                out.append(ToolCallEndDelta(index=tool))
        elif kind == "message_delta":
            stop = _str(_dict(data.get("delta")).get("stop_reason"))
            if stop:
                state.data["stop_reason"] = stop
            usage = _dict(data.get("usage"))
            if usage:
                merged = {**state.data.get("usage", {}), **usage}
                state.data["usage"] = merged
                out.append(UsageDelta(usage=_usage_from(merged)))
        elif kind == "message_stop":
            out.extend(_finish_deltas(state))
        elif kind == "error":
            error = _dict(data.get("error"))
            error_kind = ERROR_TYPE_KINDS.get(_str(error.get("type")) or "", "server")
            detail = _str(error.get("message")) or "error event in the stream"
            out.append(ErrorDelta(kind=error_kind, message=detail, status=_kind_status(error_kind)))
            state.finished = True
        # `ping` and unknown event types carry nothing for the IR.
        return out

    def finish_stream(self, state: StreamState) -> list[Delta]:
        if state.finished:
            return []
        if "stop_reason" in state.data:
            return _finish_deltas(state)
        state.finished = True
        detail = "before message_stop" if state.started else "without any event"
        return [ErrorDelta(kind="invalid_response", message=f"the stream ended {detail}")]

    def decode_error(
        self, status: int, body: Any, headers: dict[str, str] | None = None
    ) -> ModelError:
        error = _dict(_dict(body).get("error"))
        error_type = _str(error.get("type"))
        message = _str(error.get("message"))
        if not message:
            message = body.strip()[:500] if isinstance(body, str) and body.strip() else None
        kind = ERROR_TYPE_KINDS.get(error_type or "", kind_for_status(status))
        code = _str(_dict(error.get("details")).get("error_code")) or error_type
        return ModelError(
            kind,
            message or f"HTTP {status}",
            status=status,
            code=code,
            retry_after=parse_retry_after(_header(headers, "retry-after")),
            raw=body,
        )

    # ---- server side ----------------------------------------------------------------------

    def decode_request(
        self,
        body: Any,
        *,
        path: str = "",
        headers: dict[str, str] | None = None,
        report: ConversionReport | None = None,
    ) -> Request:
        if not isinstance(body, dict):
            raise ModelError("bad_request", "the request body must be a JSON object", status=400)
        loss = _Losses(report)
        stash = {k: copy.deepcopy(v) for k, v in body.items() if k not in MODELED_FIELDS}
        tools: list[ToolSpec] = []
        for raw in _list(body.get("tools")):
            tool = _dict(raw)
            if "input_schema" in tool and tool.get("type") in (None, "custom"):
                tools.append(_decode_tool(tool))
            else:  # server tools and other non-function tools
                stash.setdefault("tools", []).append(copy.deepcopy(raw))
        tool_choice, parallel = _decode_tool_choice(body.get("tool_choice"), stash)
        effort, budget = _decode_reasoning(body, stash)
        output_config = _dict(body.get("output_config"))
        response_format = None
        fmt = _dict(output_config.get("format"))
        if fmt.get("type") == "json_schema" and isinstance(fmt.get("schema"), dict):
            response_format = ResponseFormat(type="json_schema", json_schema=fmt["schema"])
        elif fmt:
            stash.setdefault("output_config", {})["format"] = copy.deepcopy(fmt)
        extras = {k: v for k, v in output_config.items() if k not in ("effort", "format")}
        if extras:
            stash.setdefault("output_config", {}).update(copy.deepcopy(extras))
        extension: dict[str, Any] = {}
        if stash:
            extension["body"] = stash
        beta = _header(headers, BETA_HEADER)
        if beta:
            extension["headers"] = {BETA_HEADER: beta}
        return Request(
            model=_str(body.get("model")) or "",
            system=_decode_system(body.get("system"), loss),
            messages=self._decode_conversation(_list(body.get("messages")), loss),
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel,
            response_format=response_format,
            max_output_tokens=_int(body.get("max_tokens")),
            sampling=Sampling(
                temperature=_float(body.get("temperature")),
                top_p=_float(body.get("top_p")),
                top_k=_int(body.get("top_k")),
                stop=[s for s in _list(body.get("stop_sequences")) if isinstance(s, str)],
            ),
            effort=effort,
            budget_tokens=budget,
            stream=body.get("stream") is True,
            extensions={self.format: extension} if extension else {},
        )

    def encode_response(self, response: Response, *, report: ConversionReport | None = None) -> Any:
        loss = _Losses(report)
        content: list[dict[str, Any]] = []
        refusals: list[str] = []
        for index, part in enumerate(response.message.content):
            where = f"message.content[{index}]"
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            elif isinstance(part, ReasoningPart):
                block = self._reasoning_out(part, loss, where)
                if block is not None:
                    content.append(block)
            elif isinstance(part, ToolCallPart):
                content.append(
                    {
                        "type": "tool_use",
                        "id": _wire_id(part.id, where, loss),
                        "name": part.name,
                        "input": _arguments(part, where, loss),
                    }
                )
            elif isinstance(part, RefusalPart):
                refusals.append(part.text)
            else:
                loss.add(where, f"{part.type} parts cannot appear in a messages-format reply")
        body: dict[str, Any] = {
            "id": response.id or f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": response.model or "",
            "content": content,
            "stop_reason": _stop_reason(response.finish_reason, response.raw_finish_reason),
            "stop_sequence": None,
            "usage": _wire_usage(response.usage, final=True),
        }
        if refusals:
            explanation = "\n".join(refusals)
            body["stop_details"] = {"type": "refusal", "category": None, "explanation": explanation}
        return body

    def encode_stream(self, deltas: Iterable[Delta]) -> Iterator[StreamEvent]:
        writer = _StreamWriter()
        for delta in deltas:
            writer.feed(delta)
            yield from writer.drain()
        writer.end()
        yield from writer.drain()

    def encode_error(self, error: ModelError) -> tuple[int, Any]:
        status, error_type = KIND_ERRORS.get(error.kind, (500, "api_error"))
        return status, {"type": "error", "error": {"type": error_type, "message": error.message}}

    # ---- helpers that need the codec ------------------------------------------------------

    def _encode_conversation(
        self, request: Request, loss: _Losses
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        system = [{"type": "text", "text": p.text} for p in request.system if p.text]
        turns: list[dict[str, Any]] = []
        started = False
        for index, message in enumerate(request.messages):
            field = f"messages[{index}]"
            if message.role == "system":
                if started:
                    loss.add(
                        field,
                        "system message inside the conversation moved to the top-level system",
                        "approximated",
                    )
                for j, part in enumerate(message.content):
                    if isinstance(part, TextPart):
                        if part.text:
                            system.append({"type": "text", "text": part.text})
                    else:
                        loss.add(f"{field}.content[{j}]", "only text is allowed in the system")
                continue
            started = True
            if message.name:
                loss.add(f"{field}.name", "the messages format has no participant names")
            if message.metadata:
                loss.add(f"{field}.metadata", f"not carried: {sorted(message.metadata)}")
            role = "assistant" if message.role == "assistant" else "user"
            blocks = self._encode_parts(message, role, field, loss)
            if not blocks:
                continue
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"].extend(blocks)
            else:
                turns.append({"role": role, "content": blocks})
        for turn in turns:
            if turn["role"] == "user":
                blocks = turn["content"]
                results = [b for b in blocks if b["type"] == "tool_result"]
                turn["content"] = results + [b for b in blocks if b["type"] != "tool_result"]
        return system, turns

    def _encode_parts(
        self, message: Message, role: str, field: str, loss: _Losses
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for index, part in enumerate(message.content):
            where = f"{field}.content[{index}]"
            block: dict[str, Any] | None = None
            if isinstance(part, TextPart):
                block = {"type": "text", "text": part.text} if part.text else None
            elif isinstance(part, RefusalPart):
                loss.add(where, "refusal replayed as text", "approximated")
                block = {"type": "text", "text": part.text} if part.text else None
            elif isinstance(part, (ImagePart, FilePart)):
                if role == "assistant":
                    loss.add(where, f"{part.type} parts are not allowed in assistant turns")
                elif isinstance(part, ImagePart):
                    block = _image_block(part, where, loss)
                else:
                    block = _file_block(part, where, loss)
            elif isinstance(part, ToolCallPart):
                if role != "assistant":
                    loss.add(where, "tool calls belong in assistant turns")
                else:
                    block = {
                        "type": "tool_use",
                        "id": _wire_id(part.id, where, loss),
                        "name": part.name,
                        "input": _arguments(part, where, loss),
                    }
            elif isinstance(part, ToolResultPart):
                if role != "user":
                    loss.add(where, "tool results belong in user turns")
                else:
                    block = _tool_result_block(part, where, loss)
            elif isinstance(part, ReasoningPart):
                if role != "assistant":
                    loss.add(where, "reasoning belongs in assistant turns")
                else:
                    block = self._reasoning_in(part, where, loss)
            if block is not None:
                blocks.append(block)
        return blocks

    def _reasoning_in(
        self, part: ReasoningPart, where: str, loss: _Losses
    ) -> dict[str, Any] | None:
        """A reasoning part for a request: replayed only to its own origin, with its payload."""
        if part.origin is None or part.origin.format != self.format:
            if part.text or part.summary or part.opaque:
                origin = part.origin.format if part.origin else "unknown"
                loss.add(where, f"reasoning from origin {origin!r} is not replayed (ADR 0003)")
            return None
        if part.opaque and part.opaque_kind == "redacted":
            return {"type": "redacted_thinking", "data": part.opaque}
        if part.opaque and part.opaque_kind == "signature":
            return {"type": "thinking", "thinking": part.text or "", "signature": part.opaque}
        if part.text or part.opaque:
            loss.add(where, "thinking without its signature cannot be replayed")
        return None

    def _reasoning_out(
        self, part: ReasoningPart, loss: _Losses, where: str
    ) -> dict[str, Any] | None:
        """A reasoning part for a reply. Foreign reasoning keeps its text, never a payload."""
        own = part.origin is not None and part.origin.format == self.format
        if own and part.opaque and part.opaque_kind == "redacted":
            return {"type": "redacted_thinking", "data": part.opaque}
        signature = part.opaque if own and part.opaque_kind == "signature" and part.opaque else ""
        if part.opaque and not signature:
            loss.add(where, "opaque reasoning from another origin is not passed on")
        text = part.text or part.summary or ""
        if not text and not signature:
            return None
        return {"type": "thinking", "thinking": text, "signature": signature}

    def _block_start(self, index: int, block: dict[str, Any], state: StreamState) -> list[Delta]:
        kind = block.get("type")
        if kind == "text":
            text = _str(block.get("text"))
            return [TextDelta(text=text)] if text else []
        if kind == "thinking":
            out: list[Delta] = []
            text = _str(block.get("thinking"))
            if text:
                out.append(ReasoningDelta(text=text))
            signature = _str(block.get("signature"))
            if signature:
                out.append(ReasoningDelta(text="", opaque=signature, opaque_kind="signature"))
            return out
        if kind == "redacted_thinking":
            data = _str(block.get("data"))
            return [ReasoningDelta(text="", opaque=data, opaque_kind="redacted")] if data else []
        if kind == "tool_use":
            tool = state.tool_index
            state.tool_index += 1
            state.open_tools[index] = tool
            call_id = _str(block.get("id"))
            start_input = block.get("input")
            if isinstance(start_input, dict) and start_input:
                state.data.setdefault("start_input", {})[index] = start_input
            return [
                ToolCallStartDelta(
                    index=tool,
                    id=call_id or synthetic_call_id(state.data.get("turn", 0), tool),
                    name=_str(block.get("name")) or "",
                    synthetic=not call_id,
                )
            ]
        return []  # server-tool and other blocks carry nothing the IR streams

    def _decode_conversation(self, raw_messages: list[Any], loss: _Losses) -> list[Message]:
        out: list[Message] = []
        names: dict[str, str] = {}
        unanswered: list[str] = []
        for turn, raw in enumerate(raw_messages):
            message = _dict(raw)
            role = message.get("role")
            where = f"messages[{turn}]"
            # Per-message settings such as `clear_at` or `output_config` are not carried.
            _note_extras(message, MESSAGE_FIELDS, loss, where)
            blocks = _blocks(message.get("content"))
            if role == "assistant":
                parts: list[Part] = []
                calls = 0
                for j, block in enumerate(blocks):
                    if block.get("type") == "tool_use":
                        call = _tool_call(block, turn, calls)
                        calls += 1
                        names[call.id] = call.name
                        parts.append(call)
                        continue
                    part = _decode_block(block, Origin(format=self.format), loss, f"{where}[{j}]")
                    if part is not None:
                        parts.append(part)
                unanswered = [p.id for p in parts if isinstance(p, ToolCallPart)]
                out.append(Message(role="assistant", content=parts))
            elif role == "system":
                texts: list[Part] = []
                for j, block in enumerate(blocks):
                    if block.get("type") == "text":
                        texts.append(TextPart(text=_str(block.get("text")) or ""))
                    else:
                        loss.add(f"{where}[{j}]", "only text is kept from system messages")
                out.append(Message(role="system", content=texts))
            else:
                pending: list[Part] = []
                for j, block in enumerate(blocks):
                    if block.get("type") == "tool_result":
                        if pending:
                            out.append(Message(role="user", content=pending))
                            pending = []
                        result = _tool_result(block, names, unanswered, loss, f"{where}[{j}]")
                        out.append(Message(role="tool", content=[result]))
                        continue
                    part = _decode_block(block, Origin(format=self.format), loss, f"{where}[{j}]")
                    if part is not None:
                        pending.append(part)
                if pending:
                    out.append(Message(role="user", content=pending))
        return out


# --------------------------------------------------------------------------------------------
# Request encoding helpers
# --------------------------------------------------------------------------------------------


def _encode_sampling(sampling: Sampling, body: dict[str, Any], loss: _Losses) -> None:
    if sampling.temperature is not None:
        temperature = sampling.temperature
        if temperature > MAX_TEMPERATURE:
            loss.add(
                "sampling.temperature",
                f"{temperature} is above the format's maximum of 1.0; sent 1.0",
                "approximated",
            )
            temperature = MAX_TEMPERATURE
        body["temperature"] = temperature
    if sampling.top_p is not None:
        body["top_p"] = sampling.top_p
    if sampling.top_k is not None:
        body["top_k"] = sampling.top_k
    if sampling.stop:
        body["stop_sequences"] = list(sampling.stop)
    for name in ("seed", "presence_penalty", "frequency_penalty"):
        if getattr(sampling, name) is not None:
            loss.add(f"sampling.{name}", "not supported by the messages format")


def _encode_tools(request: Request, body: dict[str, Any], loss: _Losses) -> None:
    if request.tools:
        body["tools"] = [_encode_tool(tool) for tool in request.tools]
    choice: dict[str, Any] | None = None
    if isinstance(request.tool_choice, NamedToolChoice):
        choice = {"type": "tool", "name": request.tool_choice.name}
    elif request.tool_choice is not None:
        choice = {"type": {"auto": "auto", "none": "none", "required": "any"}[request.tool_choice]}
    if request.parallel_tool_calls is False and (choice is None or choice["type"] != "none"):
        choice = {**(choice or {"type": "auto"}), "disable_parallel_tool_use": True}
    if choice is None:
        return
    if request.tools:
        body["tool_choice"] = choice
    elif choice["type"] in ("any", "tool"):
        loss.add("tool_choice", "a forced tool choice needs tools; none are defined")


def _encode_tool(tool: ToolSpec) -> dict[str, Any]:
    schema = tool.parameters if "type" in tool.parameters else {"type": "object", **tool.parameters}
    out: dict[str, Any] = {"name": tool.name}
    if tool.description is not None:
        out["description"] = tool.description
    out["input_schema"] = schema
    if tool.strict is not None:
        out["strict"] = tool.strict
    return out


def _encode_response_format(
    response_format: ResponseFormat | None, output_config: dict[str, Any], loss: _Losses
) -> None:
    if response_format is None or response_format.type == "text":
        return
    if response_format.type == "json_schema" and response_format.json_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": response_format.json_schema}
        return
    loss.add(
        "response_format",
        "JSON mode without a schema sent as a schema for any JSON object",
        "approximated",
    )
    output_config["format"] = {"type": "json_schema", "schema": {"type": "object"}}


def _encode_reasoning(
    request: Request, body: dict[str, Any], output_config: dict[str, Any], loss: _Losses
) -> None:
    effort, budget = request.effort, request.budget_tokens
    if effort is not None and effort != "none":
        level = effort
        if effort not in WIRE_EFFORTS:
            level = "low"
            loss.add(
                "effort", f"{effort!r} is not a level of this format; sent 'low'", "approximated"
            )
        output_config["effort"] = level
    if effort == "none" or budget == 0:
        body["thinking"] = {"type": "disabled"}
        if budget:
            loss.add("budget_tokens", "effort 'none' switches thinking off; budget not sent")
        return
    if budget is not None:
        if budget < MIN_THINKING_BUDGET:
            loss.add(
                "budget_tokens",
                f"{budget} is below the minimum thinking budget; sent {MIN_THINKING_BUDGET}",
                "approximated",
            )
            budget = MIN_THINKING_BUDGET
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}


def _image_block(part: ImagePart, where: str, loss: _Losses) -> dict[str, Any] | None:
    if part.detail:
        loss.add(f"{where}.detail", "the messages format has no image detail setting")
    data, media_type = part.data, part.media_type
    if data is None and part.url and part.url.startswith("data:"):
        parsed = _parse_data_uri(part.url)
        if parsed is None:
            loss.add(where, "only base64 data URIs can be sent")
            return None
        media_type, data = parsed[0] or media_type, parsed[1]
    if data is not None:
        if not media_type:
            media_type = _sniff_image(data)
            if media_type is None:
                loss.add(where, "image media type unknown; sent as image/png", "approximated")
                media_type = "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    if part.url:
        return {"type": "image", "source": {"type": "url", "url": part.url}}
    loss.add(where, "image has neither data nor a URL")
    return None


def _file_block(part: FilePart, where: str, loss: _Losses) -> dict[str, Any] | None:
    media_type = part.media_type or ""
    block: dict[str, Any] | None = None
    data = part.data
    if data is None and part.url and part.url.startswith("data:"):
        parsed = _parse_data_uri(part.url)
        if parsed is None:
            loss.add(where, "only base64 data URIs can be sent")
            return None
        media_type, data = parsed[0] or media_type, parsed[1]
    if part.file_id:
        kind = "image" if media_type.startswith("image/") else "document"
        block = {"type": kind, "source": {"type": "file", "file_id": part.file_id}}
    elif data is not None:
        if media_type.startswith("image/"):
            source = {"type": "base64", "media_type": media_type, "data": data}
            block = {"type": "image", "source": source}
        elif media_type == "application/pdf":
            source = {"type": "base64", "media_type": "application/pdf", "data": data}
            block = {"type": "document", "source": source}
        elif media_type.startswith("text/"):
            text = _b64_text(data)
            if text is None:
                loss.add(where, "text document data is not valid base64 UTF-8")
                return None
            if media_type != "text/plain":
                loss.add(where, f"{media_type} sent as text/plain", "approximated")
            source = {"type": "text", "media_type": "text/plain", "data": text}
            block = {"type": "document", "source": source}
        else:
            loss.add(where, f"documents of type {media_type or 'unknown'} are not supported")
            return None
    elif part.url:
        kind = "image" if media_type.startswith("image/") else "document"
        block = {"type": kind, "source": {"type": "url", "url": part.url}}
    else:
        loss.add(where, "file has no data, URL or file ID")
        return None
    if part.name:
        if block["type"] == "document":
            block["title"] = part.name
        else:
            loss.add(f"{where}.name", "image blocks have no title")
    return block


def _tool_result_block(part: ToolResultPart, where: str, loss: _Losses) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": _wire_id(part.call_id, where, loss),
    }
    content: list[dict[str, Any]] = []
    for index, item in enumerate(part.content):
        if isinstance(item, TextPart):
            if item.text:
                content.append({"type": "text", "text": item.text})
        else:
            image = _image_block(item, f"{where}.content[{index}]", loss)
            if image is not None:
                content.append(image)
    if len(content) == 1 and content[0]["type"] == "text":
        block["content"] = content[0]["text"]
    elif content:
        block["content"] = content
    if part.is_error:
        block["is_error"] = True
    return block


def _wire_id(call_id: str, where: str, loss: _Losses) -> str:
    """Tool-use IDs must match `^[a-zA-Z0-9_-]+$`; other characters become `_`."""
    clean = _ID_RE.sub("_", call_id) or "call"
    if clean != call_id:
        loss.add(f"{where}.id", f"tool-call ID {call_id!r} sent as {clean!r}", "approximated")
    return clean


def _arguments(part: ToolCallPart, where: str, loss: _Losses) -> dict[str, Any]:
    try:
        return part.parsed_arguments()
    except ValueError:
        loss.add(f"{where}.arguments", "arguments are not a JSON object; sent {}")
        return {}


def _apply_extensions(
    extensions: dict[str, Any],
    fmt: str,
    body: dict[str, Any],
    headers: dict[str, str],
    loss: _Losses,
) -> None:
    for name, value in extensions.items():
        if name != fmt:
            loss.add(f"extensions.{name}", f"extensions for {name!r} are not sent to {fmt!r}")
            continue
        extension = _dict(value)
        _merge_missing(body, _dict(extension.get("body")))
        for header, header_value in _dict(extension.get("headers")).items():
            if isinstance(header_value, str):
                headers.setdefault(header, header_value)


def _merge_missing(target: dict[str, Any], extra: dict[str, Any]) -> None:
    """Adds `extra` fields missing from `target`. `thinking` is atomic (a mixed object would be
    invalid), dicts merge recursively and lists are extended (server tools after functions)."""
    for key, value in extra.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif key == "thinking":
            continue
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge_missing(target[key], value)
        elif isinstance(target[key], list) and isinstance(value, list):
            target[key] = [*target[key], *copy.deepcopy(value)]


# --------------------------------------------------------------------------------------------
# Decoding helpers
# --------------------------------------------------------------------------------------------


def _decode_system(raw: Any, loss: _Losses) -> list[TextPart]:
    if isinstance(raw, str):
        return [TextPart(text=raw)] if raw else []
    parts: list[TextPart] = []
    for index, item in enumerate(_list(raw)):
        block = _dict(item)
        if block.get("type") == "text":
            parts.append(TextPart(text=_str(block.get("text")) or ""))
            _note_block_extras(block, loss, f"system[{index}]")
        else:
            loss.add(f"system[{index}]", "only text blocks are kept from the system prompt")
    return parts


def _decode_tool(tool: dict[str, Any]) -> ToolSpec:
    schema = tool.get("input_schema")
    strict = tool.get("strict")
    return ToolSpec(
        name=_str(tool.get("name")) or "",
        description=_str(tool.get("description")),
        parameters=schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
        strict=strict if isinstance(strict, bool) else None,
    )


def _decode_tool_choice(raw: Any, stash: dict[str, Any]) -> tuple[ToolChoice | None, bool | None]:
    choice = _dict(raw)
    if not choice:
        return None, None
    parallel = False if choice.get("disable_parallel_tool_use") is True else None
    kind = choice.get("type")
    name = _str(choice.get("name"))
    if kind == "auto":
        return "auto", parallel
    if kind == "none":
        return "none", parallel
    if kind == "any":
        return "required", parallel
    if kind == "tool" and name:
        return NamedToolChoice(name=name), parallel
    stash["tool_choice"] = copy.deepcopy(choice)
    return None, None


def _decode_reasoning(
    body: dict[str, Any], stash: dict[str, Any]
) -> tuple[Effort | None, int | None]:
    output_config = _dict(body.get("output_config"))
    level = _str(output_config.get("effort"))
    effort = _EFFORTS.get(level or "") if level in WIRE_EFFORTS else None
    if level and effort is None:
        stash.setdefault("output_config", {})["effort"] = level
    thinking = _dict(body.get("thinking"))
    if not thinking:
        return effort, None
    kind = thinking.get("type")
    extra_keys = set(thinking) - {"type", "budget_tokens"}
    budget = _int(thinking.get("budget_tokens"))
    if kind == "enabled" and budget is not None and not extra_keys:
        return effort, budget
    if kind == "disabled" and not extra_keys:
        if effort is not None:  # the IR says "no reasoning"; keep the separate effort level
            stash.setdefault("output_config", {})["effort"] = effort
        return "none", None
    stash["thinking"] = copy.deepcopy(thinking)  # adaptive and other modes stay as sent
    return effort, budget if kind == "enabled" else None


def _decode_block(block: dict[str, Any], origin: Origin, loss: _Losses, where: str) -> Part | None:
    kind = block.get("type")
    _note_block_extras(block, loss, where)
    if kind == "text":
        if block.get("citations"):
            loss.add(f"{where}.citations", "citations are not carried")
        return TextPart(text=_str(block.get("text")) or "")
    if kind == "image":
        return _decode_image(_dict(block.get("source")), loss, where)
    if kind == "document":
        return _decode_document(block, loss, where)
    if kind == "thinking":
        signature = _str(block.get("signature"))
        return ReasoningPart(
            text=_str(block.get("thinking")) or None,
            opaque=signature or None,
            opaque_kind="signature" if signature else None,
            origin=origin,
        )
    if kind == "redacted_thinking":
        data = _str(block.get("data"))
        if not data:
            return None
        return ReasoningPart(opaque=data, opaque_kind="redacted", origin=origin)
    loss.add(where, f"{kind!r} blocks are not carried by the IR")
    return None


def _note_extras(data: dict[str, Any], known: frozenset[str], loss: _Losses, where: str) -> None:
    """Reports non-null fields of `data` the IR does not carry."""
    extras = sorted(key for key, value in data.items() if key not in known and value is not None)
    if extras:
        loss.add(where, f"fields {extras} are not carried")


def _note_block_extras(block: dict[str, Any], loss: _Losses, where: str) -> None:
    if "cache_control" in block:
        loss.add(f"{where}.cache_control", "block-level cache hints are not carried")


def _decode_image(source: dict[str, Any], loss: _Losses, where: str) -> Part | None:
    kind = source.get("type")
    if kind == "base64":
        return ImagePart(data=_str(source.get("data")), media_type=_str(source.get("media_type")))
    if kind == "url":
        return ImagePart(url=_str(source.get("url")))
    if kind == "file":
        # The IR has no image file IDs; "image/*" marks the file as an image for re-encoding.
        return FilePart(file_id=_str(source.get("file_id")), media_type="image/*")
    loss.add(where, f"image source {kind!r} is not carried")
    return None


def _decode_document(block: dict[str, Any], loss: _Losses, where: str) -> Part | None:
    source = _dict(block.get("source"))
    kind = source.get("type")
    name = _str(block.get("title"))
    if block.get("context") or block.get("citations"):
        loss.add(where, "document context and citation settings are not carried")
    if kind == "base64":
        media_type = _str(source.get("media_type")) or "application/pdf"
        return FilePart(data=_str(source.get("data")), media_type=media_type, name=name)
    if kind == "text":
        text = _str(source.get("data")) or ""
        data = base64.b64encode(text.encode("utf-8")).decode("ascii")
        return FilePart(data=data, media_type="text/plain", name=name)
    if kind == "url":
        return FilePart(url=_str(source.get("url")), media_type="application/pdf", name=name)
    if kind == "file":
        return FilePart(file_id=_str(source.get("file_id")), name=name)
    if kind == "content":
        content = source.get("content")
        if isinstance(content, str):
            text = content
        else:
            texts = [_str(_dict(b).get("text")) or "" for b in _list(content)]
            text = "\n".join(t for t in texts if t)
            loss.add(where, "content-block document flattened to plain text", "approximated")
        data = base64.b64encode(text.encode("utf-8")).decode("ascii")
        return FilePart(data=data, media_type="text/plain", name=name)
    loss.add(where, f"document source {kind!r} is not carried")
    return None


def _tool_call(block: dict[str, Any], turn: int, index: int) -> ToolCallPart:
    call_id = _str(block.get("id"))
    arguments = block.get("input")
    return ToolCallPart(
        id=call_id or synthetic_call_id(turn, index),
        name=_str(block.get("name")) or "",
        arguments=_compact(arguments if isinstance(arguments, dict) else {}),
        synthetic=not call_id,
    )


def _tool_result(
    block: dict[str, Any],
    names: dict[str, str],
    unanswered: list[str],
    loss: _Losses,
    where: str,
) -> ToolResultPart:
    call_id = _str(block.get("tool_use_id"))
    if call_id is None:
        call_id = unanswered[0] if unanswered else synthetic_call_id(0, 0)
    if call_id in unanswered:
        unanswered.remove(call_id)
    content: list[TextPart | ImagePart] = []
    raw = block.get("content")
    if isinstance(raw, str):
        if raw:
            content.append(TextPart(text=raw))
    else:
        for index, item in enumerate(_list(raw)):
            child = _dict(item)
            kind = child.get("type")
            if kind == "text":
                content.append(TextPart(text=_str(child.get("text")) or ""))
            elif kind == "image":
                image = _decode_image(_dict(child.get("source")), loss, f"{where}[{index}]")
                if isinstance(image, ImagePart):
                    content.append(image)
                elif image is not None:
                    loss.add(f"{where}[{index}]", "image file references in tool results dropped")
            else:
                loss.add(f"{where}[{index}]", f"{kind!r} blocks in tool results are not carried")
    return ToolResultPart(
        call_id=call_id,
        name=names.get(call_id),
        content=content,
        is_error=block.get("is_error") is True,
    )


def _block_delta(index: int, delta: dict[str, Any], state: StreamState) -> list[Delta]:
    kind = delta.get("type")
    if kind == "text_delta":
        text = _str(delta.get("text"))
        return [TextDelta(text=text)] if text else []
    if kind == "input_json_delta":
        partial = _str(delta.get("partial_json"))
        if partial and index in state.open_tools:
            state.data.setdefault("streamed", set()).add(index)
            return [ToolCallArgsDelta(index=state.open_tools[index], delta=partial)]
        return []
    if kind == "thinking_delta":
        text = _str(delta.get("thinking"))
        return [ReasoningDelta(text=text)] if text else []
    if kind == "signature_delta":
        signature = _str(delta.get("signature"))
        if signature:
            return [ReasoningDelta(text="", opaque=signature, opaque_kind="signature")]
    return []  # citations and unknown delta types


def _finish_deltas(state: StreamState) -> list[Delta]:
    if state.finished:
        return []
    state.finished = True
    stop = state.data.get("stop_reason")
    return [FinishDelta(reason=_finish(stop), raw=stop)]


def _finish(stop: str | None) -> FinishReason:
    if stop is None:
        return "stop"
    return STOP_REASONS.get(stop, "other")


def _stop_reason(reason: FinishReason, raw: str | None) -> str:
    if raw in STOP_REASONS and STOP_REASONS[raw] == reason:
        return raw
    return FINISH_TO_STOP[reason]


def _usage(raw: Any) -> Usage | None:
    usage = _dict(raw)
    return _usage_from(usage) if usage else None


def _usage_from(usage: dict[str, Any]) -> Usage:
    base = _int(usage.get("input_tokens"))
    read = _int(usage.get("cache_read_input_tokens"))
    write = _int(usage.get("cache_creation_input_tokens"))
    total = None if base is None and read is None and write is None else (base or 0) + (read or 0)
    if total is not None:
        total += write or 0
    details = _dict(usage.get("output_tokens_details"))
    return Usage(
        input_tokens=total,
        output_tokens=_int(usage.get("output_tokens")),
        cached_input_tokens=read,
        cache_write_tokens=write,
        reasoning_tokens=_int(details.get("thinking_tokens")),
        raw=dict(usage),
    )


def _wire_usage(usage: Usage | None, *, final: bool) -> dict[str, Any]:
    """IR usage -> the format's usage object (`input_tokens` excludes cache reads and writes)."""
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0}
    out: dict[str, Any] = {}
    read, write = usage.cached_input_tokens, usage.cache_write_tokens
    if usage.input_tokens is not None:
        out["input_tokens"] = max(0, usage.input_tokens - (read or 0) - (write or 0))
    elif final:
        out["input_tokens"] = 0
    if write is not None:
        out["cache_creation_input_tokens"] = write
    if read is not None:
        out["cache_read_input_tokens"] = read
    out["output_tokens"] = usage.output_tokens or 0
    if usage.reasoning_tokens is not None:
        out["output_tokens_details"] = {"thinking_tokens": usage.reasoning_tokens}
    return out


def _kind_status(kind: str) -> int:
    return KIND_ERRORS.get(kind, (500, "api_error"))[0]


# --------------------------------------------------------------------------------------------
# Stream encoding (server side)
# --------------------------------------------------------------------------------------------


class _StreamWriter:
    """IR deltas -> the format's event sequence. One content block is open at a time; while a
    tool block is open, deltas for anything else wait until that tool call ends."""

    def __init__(self) -> None:
        self.events: list[StreamEvent] = []
        self.id: str | None = None
        self.model: str | None = None
        self.usage: Usage | None = None
        self.started = False
        self.closed = False
        self.block = -1
        self.open: str | None = None
        self.live_tool: int | None = None
        self.queue: list[Delta] = []

    def drain(self) -> list[StreamEvent]:
        events, self.events = self.events, []
        return events

    def emit(self, payload: dict[str, Any]) -> None:
        self.events.append(sse_json(payload, event=payload["type"]))

    def feed(self, delta: Delta) -> None:
        if self.closed:
            return
        if isinstance(delta, StartDelta):
            self.id = delta.id or self.id
            self.model = delta.model or self.model
            return
        if isinstance(delta, UsageDelta):
            self.usage = merge_usage(self.usage, delta.usage)
            return
        if isinstance(delta, ErrorDelta):
            error_type = KIND_ERRORS.get(delta.kind, (500, "api_error"))[1]
            self.emit({"type": "error", "error": {"type": error_type, "message": delta.message}})
            self.closed = True
            return
        if isinstance(delta, FinishDelta):
            self.finish(delta)
            return
        live = self.live_tool
        own = isinstance(delta, (ToolCallArgsDelta, ToolCallEndDelta)) and delta.index == live
        if live is not None and not own:
            self.queue.append(delta)
            return
        self.start()
        if isinstance(delta, TextDelta):
            if self.open != "text":
                self.open_block("text", {"type": "text", "text": ""})
            if delta.text:
                self.emit_delta({"type": "text_delta", "text": delta.text})
        elif isinstance(delta, ReasoningDelta):
            self.reasoning(delta)
        elif isinstance(delta, ToolCallStartDelta):
            block = {"type": "tool_use", "id": _clean_id(delta.id), "name": delta.name, "input": {}}
            self.open_block("tool", block)
            self.live_tool = delta.index
        elif isinstance(delta, ToolCallArgsDelta):
            if live == delta.index and delta.delta:
                self.emit_delta({"type": "input_json_delta", "partial_json": delta.delta})
        elif isinstance(delta, ToolCallEndDelta) and live == delta.index:
            self.close_block()
            self.replay()

    def reasoning(self, delta: ReasoningDelta) -> None:
        if delta.opaque and delta.opaque_kind == "redacted":
            self.open_block("redacted", {"type": "redacted_thinking", "data": delta.opaque})
            self.close_block()
            return
        signed = bool(delta.opaque) and delta.opaque_kind == "signature"
        if not delta.text and not signed:
            return  # encrypted payloads of other formats are not sent; nothing else to say
        if self.open != "thinking":
            self.open_block("thinking", {"type": "thinking", "thinking": "", "signature": ""})
        if delta.text:
            self.emit_delta({"type": "thinking_delta", "thinking": delta.text})
        if signed:  # the signature closes its block
            self.emit_delta({"type": "signature_delta", "signature": delta.opaque})
            self.close_block()

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        message: dict[str, Any] = {
            "id": self.id or f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": self.model or "",
            "stop_reason": None,
            "stop_sequence": None,
            "usage": _wire_usage(self.usage, final=True),
        }
        self.emit({"type": "message_start", "message": message})

    def open_block(self, kind: str, content_block: dict[str, Any]) -> None:
        self.close_block()
        self.block += 1
        self.open = kind
        self.emit(
            {"type": "content_block_start", "index": self.block, "content_block": content_block}
        )

    def emit_delta(self, delta: dict[str, Any]) -> None:
        self.emit({"type": "content_block_delta", "index": self.block, "delta": delta})

    def close_block(self) -> None:
        if self.open is not None:
            self.emit({"type": "content_block_stop", "index": self.block})
            self.open = None
            self.live_tool = None

    def replay(self) -> None:
        pending, self.queue = self.queue, []
        for delta in pending:
            self.feed(delta)

    def finish(self, delta: FinishDelta) -> None:
        self.start()
        while self.live_tool is not None or self.queue:
            self.close_block()
            self.replay()
        self.close_block()
        usage = _wire_usage(self.usage, final=False)
        stop = _stop_reason(delta.reason, delta.raw)
        self.emit(
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": usage,
            }
        )
        self.emit({"type": "message_stop"})
        self.closed = True

    def end(self) -> None:
        if not self.closed:
            message = "the upstream stream ended before it finished"
            self.emit({"type": "error", "error": {"type": "api_error", "message": message}})
            self.closed = True


# --------------------------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------------------------


def _clean_id(call_id: str) -> str:
    return _ID_RE.sub("_", call_id) or "call"


def _turn_number(response_id: str | None) -> int:
    """Stable turn number for synthetic tool-call IDs, so IDs from different replies differ."""
    return zlib.crc32(response_id.encode("utf-8")) % 100_000 if response_id else 0


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _parse_data_uri(url: str) -> tuple[str | None, str] | None:
    match = _DATA_URI_RE.match(url)
    if match is None:
        return None
    return match.group("media"), url[match.end() :]


def _sniff_image(data: str) -> str | None:
    prefixes = {"iVBOR": "image/png", "/9j/": "image/jpeg", "R0lGOD": "image/gif"}
    for prefix, media_type in prefixes.items():
        if data.startswith(prefix):
            return media_type
    if data.startswith("UklGR"):
        return "image/webp"
    return None


def _b64_text(data: str) -> str | None:
    try:
        return base64.b64decode(data, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    return [_dict(item) for item in _list(content)]


def _header(headers: dict[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
