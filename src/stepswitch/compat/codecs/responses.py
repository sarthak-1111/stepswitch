"""Codec for the responses format (`POST /v1/responses`).

Follows the open responses specification, version 2026-04-24
(https://www.openresponses.org/specification, https://www.openresponses.org/reference), and
accepts the hosted reference's variants listed in docs/RESEARCH.md ("API formats"). Where the
two differ, decoding accepts both shapes and encoding follows the open specification:

* Reasoning text streams as `response.reasoning.delta`/`.done` in the open specification and as
  `response.reasoning_text.delta`/`.done` in the hosted reference. Both are decoded.
  `encode_stream` writes the open names unless `CodecOptions.extra["reasoning_event"]` is
  `"response.reasoning_text"`.
* Stream `error` events nest the error fields under `error` (open specification) or carry them
  at the top level (hosted reference). Both are decoded; `encode_stream` writes both.
* Reasoning effort: the open specification accepts `none`, `low`, `medium`, `high`, `xhigh`;
  the hosted reference also `minimal` and `max`. Other levels are clamped to the nearest
  supported level and reported. `CodecOptions.extra["effort_levels"]` overrides the supported
  set (from the endpoint's capability profile, ADR 0003 item 7).
* Input reasoning items carry `summary` and `encrypted_content`; the open specification
  requires `content` to be null on input. Raw reasoning text is replayed as `reasoning_text`
  content only when `CodecOptions.extra["reasoning_content_input"]` is true.
* `presence_penalty` and `frequency_penalty` exist in the open specification but not in the
  hosted reference. They are sent only when `CodecOptions.extra["sampling_penalties"]` is true.

State is proxy-side (ADR 0003 item 2, ADR 0004): requests always carry `store: false`. The
stateful fields `previous_response_id`, `conversation`, `store: true` and `background: true`
found in the request extensions are reported as unsupported and not sent, unless
`CodecOptions.extra["stateful_passthrough"]` is true for the endpoint.

`Request.extensions["responses"]` holds wire fields of this format that the IR does not model
(`include`, `metadata`, `truncation`, `text.verbosity`, `reasoning.summary`, non-function
tools, ...). `decode_request` fills it, and `encode_request` merges it into the body; mapped
IR fields win on conflict, and conflicts are reported. Its `"unsupported"` entry lists input
items and parts the IR cannot hold (item references, compaction items, hosted-only tool
items, images by file ID) so that the proxy can resolve or reject them; `encode_request`
reports them.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast

from stepswitch.compat.codecs.base import Codec, Framing, HttpCall, StreamState, refusal_as_text
from stepswitch.compat.errors import ErrorKind, ModelError, parse_retry_after
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

PATH = "/v1/responses"
EXTENSION_KEY = "responses"
# `ReasoningEffortEnum` of the open responses specification (2026-04-24).
SPEC_EFFORTS: tuple[Effort, ...] = ("none", "low", "medium", "high", "xhigh")
# `CreateResponseBody.max_output_tokens` has `"minimum": 16` in the open specification.
MIN_OUTPUT_TOKENS = 16
STATEFUL_FIELDS = frozenset({"previous_response_id", "conversation", "store", "background"})
REASONING_EVENT_PREFIXES = ("response.reasoning", "response.reasoning_text")
INCOMPLETE_FINISH: dict[str, FinishReason] = {
    "max_output_tokens": "length",
    "content_filter": "content_filter",
}
# Incomplete reasons of the hosted reference with no IR finish reason of their own.
OTHER_INCOMPLETE_REASONS = frozenset({"max_messages", "steered"})
TERMINAL_EVENTS = {
    "response.completed": "completed",
    "response.incomplete": "incomplete",
    "response.failed": "failed",
}

# IR error kind -> (HTTP status, error type). Types follow the open specification's table
# (invalid_request 400, not_found 404, too_many_requests 429, server_error 500, model_error
# 500); it defines none for authentication and permission errors.
_KIND_WIRE: dict[str, tuple[int, str]] = {
    "bad_request": (400, "invalid_request"),
    "authentication": (401, "authentication_error"),
    "permission": (403, "permission_error"),
    "not_found": (404, "not_found"),
    "too_large": (413, "invalid_request"),
    "rate_limit": (429, "too_many_requests"),
    "overloaded": (503, "server_error"),
    "server": (500, "server_error"),
    "timeout": (504, "server_error"),
    "connection": (502, "server_error"),
    "blocked": (403, "permission_error"),
    "unsupported": (400, "invalid_request"),
    "invalid_response": (502, "model_error"),
}
# Error codes and types (both specifications) -> IR error kind.
_CODE_KINDS: dict[str, ErrorKind] = {
    "server_error": "server",
    "model_error": "server",
    "vector_store_timeout": "server",
    "rate_limit_exceeded": "rate_limit",
    "too_many_requests": "rate_limit",
    "invalid_request": "bad_request",
    "invalid_request_error": "bad_request",
    "invalid_prompt": "bad_request",
    "not_found": "not_found",
    "model_not_found": "not_found",
    "previous_response_not_found": "not_found",
    "authentication_error": "authentication",
    "permission_error": "permission",
}


class _Losses:
    """Collects conversion losses; strict mode turns the first one into a ConversionError."""

    def __init__(self, report: ConversionReport, strict: bool) -> None:
        self.report = report
        self.strict = strict

    def add(self, where: str, detail: str, action: str = "dropped") -> None:
        if self.strict:
            raise ConversionError(f"{where}: {detail}")
        self.report.add(where, detail, action)


class ResponsesCodec(Codec):
    key: ClassVar[str] = "responses"
    format: ClassVar[str] = "responses"
    framing: ClassVar[Framing] = "sse"

    # ---- client side: requests ------------------------------------------------------------

    def encode_request(self, request: Request, report: ConversionReport) -> HttpCall:
        losses = _Losses(report, self.options.strict)
        body: dict[str, Any] = {"model": request.model}
        instructions = request.system_text()
        if instructions:
            body["instructions"] = instructions
        body["input"] = self._encode_input(request, losses)
        if request.tools:
            body["tools"] = [_encode_tool(tool) for tool in request.tools]
        if request.tool_choice is not None:
            body["tool_choice"] = _encode_tool_choice(request.tool_choice)
        if request.parallel_tool_calls is not None:
            body["parallel_tool_calls"] = request.parallel_tool_calls
        if request.response_format is not None:
            body["text"] = {"format": _encode_text_format(request.response_format, losses)}
        if request.max_output_tokens is not None:
            body["max_output_tokens"] = _max_output_tokens(request.max_output_tokens, losses)
        self._encode_sampling(request.sampling, body, losses)
        if request.effort is not None:
            body["reasoning"] = {"effort": self._clamp_effort(request.effort, losses)}
        if request.budget_tokens is not None:
            losses.add("budget_tokens", "the responses format has no reasoning token budget")
        if request.prompt is not None:
            losses.add("prompt", "pre-rendered prompts are not sent; the messages are")
        body["stream"] = request.stream
        body["store"] = False
        for name, extension in request.extensions.items():
            if name != EXTENSION_KEY:
                losses.add(f"extensions.{name}", f"fields for {name} are not sent")
            elif isinstance(extension, dict):
                self._merge_extension(body, extension, losses)
            else:
                losses.add(f"extensions.{name}", "expected an object of wire fields")
        return HttpCall(method="POST", path=PATH, json=body, stream=request.stream)

    def _encode_input(self, request: Request, losses: _Losses) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        call_names: dict[str, str] = {}
        for m_index, message in enumerate(request.messages):
            where = f"messages.{m_index}"
            if message.name:
                losses.add(f"{where}.name", "the responses format has no participant names")
            phase: str | None = None
            for key, value in message.metadata.items():
                if key == "phase" and message.role == "assistant" and isinstance(value, str):
                    phase = value
                else:
                    losses.add(f"{where}.metadata.{key}", "not carried by the responses format")
            role = "user" if message.role == "tool" else message.role
            content: list[dict[str, Any]] = []
            for p_index, part in enumerate(message.content):
                loc = f"{where}.content.{p_index}"
                if isinstance(part, ToolCallPart | ToolResultPart | ReasoningPart):
                    if content:
                        items.append(_message_item(role, content, phase))
                        content = []
                    if isinstance(part, ToolCallPart):
                        call_names[part.id] = part.name
                        items.append(_function_call_item(part, loc, losses))
                    elif isinstance(part, ToolResultPart):
                        items.append(self._function_call_output(part, call_names, loc, losses))
                    else:
                        reasoning = self._reasoning_item(part, request.model, loc, losses)
                        if reasoning is not None:
                            items.append(reasoning)
                    continue
                if message.role == "tool":
                    losses.add(
                        loc, "content outside a tool result is sent as user content", "approximated"
                    )
                encoded = _content_part(part, role, loc, losses)
                if encoded is not None:
                    content.append(encoded)
            if content:
                items.append(_message_item(role, content, phase))
        return items

    def _function_call_output(
        self, part: ToolResultPart, call_names: dict[str, str], loc: str, losses: _Losses
    ) -> dict[str, Any]:
        if part.name and call_names.get(part.call_id) != part.name:
            losses.add(f"{loc}.name", "function_call_output carries no tool name")
        if part.is_error:
            losses.add(f"{loc}.is_error", "function_call_output has no error flag")
        output: str | list[dict[str, Any]]
        if all(isinstance(piece, TextPart) for piece in part.content):
            output = part.text()
        else:
            output = []
            for c_index, piece in enumerate(part.content):
                if isinstance(piece, TextPart):
                    output.append({"type": "input_text", "text": piece.text})
                else:
                    image = _image_part(piece, f"{loc}.content.{c_index}", losses)
                    if image is not None:
                        output.append(image)
        return {"type": "function_call_output", "call_id": part.call_id, "output": output}

    def _reasoning_item(
        self, part: ReasoningPart, model: str, loc: str, losses: _Losses
    ) -> dict[str, Any] | None:
        """Replay a reasoning part, but only to the origin that produced it (ADR 0003)."""
        origin = part.origin
        if origin is None or origin.format != self.format:
            source = origin.format if origin is not None else "unknown"
            losses.add(loc, f"reasoning from another origin ({source}) is not replayed")
            return None
        if origin.model is not None and origin.model != model:
            losses.add(loc, f"reasoning of model {origin.model!r} is not replayed to {model!r}")
            return None
        summary = [{"type": "summary_text", "text": part.summary}] if part.summary else []
        item: dict[str, Any] = {"type": "reasoning", "summary": summary}
        replayable = bool(summary)
        if part.opaque is not None:
            if part.opaque_kind == "encrypted":
                if part.item_id:
                    item["id"] = part.item_id
                item["encrypted_content"] = part.opaque
                replayable = True
            else:
                losses.add(f"{loc}.opaque", f"{part.opaque_kind} payloads have no responses field")
        if part.text:
            if self.options.extra.get("reasoning_content_input"):
                item["content"] = [{"type": "reasoning_text", "text": part.text}]
                replayable = True
            else:
                losses.add(f"{loc}.text", "input reasoning items carry no reasoning text")
        return item if replayable else None

    def _encode_sampling(self, sampling: Sampling, body: dict[str, Any], losses: _Losses) -> None:
        if sampling.temperature is not None:
            body["temperature"] = sampling.temperature
        if sampling.top_p is not None:
            body["top_p"] = sampling.top_p
        penalties = bool(self.options.extra.get("sampling_penalties"))
        for name in ("presence_penalty", "frequency_penalty"):
            value = getattr(sampling, name)
            if value is None:
                continue
            if penalties:
                body[name] = value
            else:
                losses.add(f"sampling.{name}", "not accepted by the hosted reference")
        if sampling.top_k is not None:
            losses.add("sampling.top_k", "the responses format has no top_k")
        if sampling.seed is not None:
            losses.add("sampling.seed", "the responses format has no seed")
        if sampling.stop:
            losses.add("sampling.stop", "the responses format has no stop sequences")

    def _clamp_effort(self, effort: Effort, losses: _Losses) -> str:
        configured = self.options.extra.get("effort_levels") or SPEC_EFFORTS
        supported = [level for level in configured if level in EFFORT_LEVELS] or list(SPEC_EFFORTS)
        if effort in supported:
            return effort
        rank = EFFORT_LEVELS.index(effort)

        def distance(level: Effort) -> tuple[int, int]:
            other = EFFORT_LEVELS.index(level)
            return abs(other - rank), -other  # ties go to the higher level

        nearest: str = min(supported, key=distance)
        losses.add("effort", f"{effort!r} is not supported; clamped to {nearest!r}", "approximated")
        return nearest

    def _merge_extension(
        self, body: dict[str, Any], extension: dict[str, Any], losses: _Losses
    ) -> None:
        stateful_ok = bool(self.options.extra.get("stateful_passthrough"))
        for key, value in extension.items():
            where = f"extensions.{EXTENSION_KEY}.{key}"
            if value is None:
                continue
            if key == "unsupported":
                entries = value if isinstance(value, list) else [value]
                for entry in entries:
                    info = entry if isinstance(entry, dict) else {}
                    kind = info.get("type") or "item"
                    losses.add(where, f"{info.get('where') or 'input'}: {kind} is not carried")
                continue
            if key in STATEFUL_FIELDS and value is not False:
                if stateful_ok:
                    body[key] = value
                else:
                    losses.add(where, "stateful feature; Stepswitch keeps conversation state")
                continue
            if key == "tools" and isinstance(value, list):
                body.setdefault("tools", []).extend(value)
            elif key not in body:
                body[key] = value
            elif isinstance(body[key], dict) and isinstance(value, dict):
                body[key], conflicts = _merge_missing(body[key], value)
                for conflict in conflicts:
                    losses.add(f"{where}.{conflict}", "conflicts with a mapped field; not sent")
            elif body[key] != value:
                losses.add(where, "conflicts with a mapped field; not sent")

    # ---- client side: responses -----------------------------------------------------------

    def decode_response(self, body: Any) -> Response:
        if not isinstance(body, dict):
            raise ModelError("invalid_response", "responses body is not a JSON object", raw=body)
        model = _str(body.get("model"))
        parts = _decode_output(body.get("output"), model)
        finish, raw = _finish(body, any(isinstance(p, ToolCallPart) for p in parts))
        created = body.get("created_at")
        return Response(
            id=_str(body.get("id")),
            model=model,
            message=Message(role="assistant", content=parts),
            finish_reason=finish,
            raw_finish_reason=raw,
            usage=_decode_usage(body.get("usage")),
            created=created if isinstance(created, int) else None,
        )

    def decode_error(
        self, status: int, body: Any, headers: dict[str, str] | None = None
    ) -> ModelError:
        error = super().decode_error(status, body, headers)
        detail = body.get("error") if isinstance(body, dict) else None
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            error.code = detail["code"]
        if error.retry_after is None and headers:
            for name, value in headers.items():
                if name.lower() == "retry-after":
                    error.retry_after = parse_retry_after(value)
        return error

    # ---- client side: streams -------------------------------------------------------------

    def decode_stream_event(self, event: StreamEvent, state: StreamState) -> list[Delta]:
        data = event.data.strip()
        if not data or data == "[DONE]" or state.finished:
            return []
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ModelError(
                "invalid_response", f"malformed responses stream event: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            return []
        kind = _str(payload.get("type")) or event.event or ""
        dec = _decoding(state)
        if kind in ("response.created", "response.queued", "response.in_progress"):
            return _start_delta(payload.get("response"), state)
        if kind in TERMINAL_EVENTS:
            return self._terminal(kind, payload, state, dec)
        if kind == "error":
            return [_stream_error(payload, dec)]
        if kind == "response.output_item.added":
            return self._item_added(payload, state, dec)
        if kind == "response.output_item.done":
            return self._item_done(payload, state, dec)
        return self._content_event(kind, payload, dec)

    def _content_event(self, kind: str, payload: dict[str, Any], dec: _Decoding) -> list[Delta]:
        if kind == "response.function_call_arguments.delta":
            item = dec.resolve(payload)
            delta = _str(payload.get("delta")) or ""
            if item.tool is None or item.ended or not delta:
                return []
            item.args += delta
            return [ToolCallArgsDelta(index=item.tool, delta=delta)]
        if kind == "response.function_call_arguments.done":
            return _reconcile_args(dec.resolve(payload), payload.get("arguments"))
        content_index = payload.get("content_index")
        index = content_index if isinstance(content_index, int) else 0
        if kind in ("response.output_text.delta", "response.refusal.delta"):
            return dec.resolve(payload).text(("content", index), delta=payload.get("delta"))
        if kind == "response.output_text.done":
            return dec.resolve(payload).text(("content", index), full=payload.get("text"))
        if kind == "response.refusal.done":
            return dec.resolve(payload).text(("content", index), full=payload.get("refusal"))
        if kind in ("response.reasoning.delta", "response.reasoning_text.delta"):
            return dec.resolve(payload).text(("reasoning", index), delta=payload.get("delta"))
        if kind in ("response.reasoning.done", "response.reasoning_text.done"):
            return dec.resolve(payload).text(("reasoning", index), full=payload.get("text"))
        summary_index = payload.get("summary_index")
        key = ("summary", summary_index if isinstance(summary_index, int) else 0)
        if kind == "response.reasoning_summary_text.delta":
            return dec.resolve(payload).text(key, delta=payload.get("delta"))
        if kind == "response.reasoning_summary_text.done":
            return dec.resolve(payload).text(key, full=payload.get("text"))
        # content_part.*, reasoning_summary_part.*, annotations, hosted tool progress and
        # extension events carry nothing the deltas need; unknown types are ignored (spec).
        return []

    def _item_added(
        self, payload: dict[str, Any], state: StreamState, dec: _Decoding
    ) -> list[Delta]:
        raw = payload.get("item")
        if not isinstance(raw, dict):
            return []
        item = dec.register(payload.get("output_index"), raw)
        if item.kind == "function_call" and item.tool is None:
            return _open_tool(item, raw, state, dec)
        return []

    def _item_done(
        self, payload: dict[str, Any], state: StreamState, dec: _Decoding
    ) -> list[Delta]:
        raw = payload.get("item")
        if not isinstance(raw, dict):
            return []
        item = dec.find(_str(raw.get("id")), payload.get("output_index"))
        if item is None or item.kind == "unknown":
            item = dec.register(payload.get("output_index"), raw)
        deltas: list[Delta] = []
        if item.kind == "function_call":
            if item.tool is None:
                deltas += _open_tool(item, raw, state, dec)
            deltas += _reconcile_args(item, raw.get("arguments"))
            if item.tool is not None and not item.ended:
                item.ended = True
                deltas.append(ToolCallEndDelta(index=item.tool))
        elif item.kind == "message":
            for index, part in enumerate(_dicts(raw.get("content"))):
                full = part.get("refusal") if part.get("type") == "refusal" else part.get("text")
                deltas += item.text(("content", index), full=full)
        elif item.kind == "reasoning":
            for index, part in enumerate(_dicts(raw.get("content"))):
                deltas += item.text(("reasoning", index), full=part.get("text"))
            for index, part in enumerate(_dicts(raw.get("summary"))):
                deltas += item.text(("summary", index), full=part.get("text"))
            encrypted = raw.get("encrypted_content")
            if isinstance(encrypted, str) and encrypted:
                deltas.append(ReasoningDelta(text="", opaque=encrypted, opaque_kind="encrypted"))
        return deltas

    def _terminal(
        self, kind: str, payload: dict[str, Any], state: StreamState, dec: _Decoding
    ) -> list[Delta]:
        resp = payload.get("response")
        resp = resp if isinstance(resp, dict) else {}
        deltas = _start_delta(resp, state)
        if not dec.saw_items:
            # A server that streamed no item events: take the content from the final object.
            parts = _decode_output(resp.get("output"), _str(resp.get("model")))
            deltas += _part_deltas(parts, state, dec)
        deltas += dec.close_tools()
        usage = _decode_usage(resp.get("usage"))
        if usage is not None:
            deltas.append(UsageDelta(usage=usage))
        status = _str(resp.get("status")) or TERMINAL_EVENTS[kind]
        if status == "failed":
            error = resp.get("error")
            error = error if isinstance(error, dict) else {}
            code = _str(error.get("code"))
            if not dec.errored:
                dec.errored = True
                message = _str(error.get("message")) or "the response failed"
                deltas.append(
                    ErrorDelta(kind=_error_kind(code, _str(error.get("type"))), message=message)
                )
            deltas.append(FinishDelta(reason="error", raw=code or "failed"))
        else:
            has_calls = dec.has_calls or any(
                item.get("type") == "function_call" for item in _dicts(resp.get("output"))
            )
            finish, raw = _finish({**resp, "status": status}, has_calls)
            deltas.append(FinishDelta(reason=finish, raw=raw))
        state.finished = True
        return deltas

    def finish_stream(self, state: StreamState) -> list[Delta]:
        if state.finished:
            return []
        dec = _decoding(state)
        deltas: list[Delta] = dec.close_tools()
        if not dec.errored:
            message = "the stream ended before a terminal response event"
            deltas.append(ErrorDelta(kind="server", message=message))
        deltas.append(FinishDelta(reason="error", raw="stream_ended"))
        state.finished = True
        return deltas

    # ---- server side ----------------------------------------------------------------------

    def decode_request(
        self, body: Any, *, path: str = "", headers: dict[str, str] | None = None
    ) -> Request:
        if not isinstance(body, dict):
            raise ModelError("bad_request", "the request body must be a JSON object", status=400)
        extension: dict[str, Any] = {}
        unsupported: list[dict[str, Any]] = []
        model = _str(body.get("model")) or ""
        system: list[TextPart] = []
        instructions = body.get("instructions")
        if isinstance(instructions, str):
            if instructions:
                system.append(TextPart(text=instructions))
        elif instructions is not None:
            extension["instructions"] = instructions
        messages = _decode_input(body.get("input"), model, system, unsupported)
        tools: list[ToolSpec] = []
        for tool in _dicts(body.get("tools")):
            if tool.get("type") == "function" and isinstance(tool.get("name"), str):
                tools.append(_decode_tool(tool))
            else:
                extension.setdefault("tools", []).append(tool)
        tool_choice = _decode_tool_choice(body.get("tool_choice"), extension)
        response_format = _decode_text(body.get("text"), extension)
        effort = _decode_reasoning(body.get("reasoning"), extension)
        sampling = Sampling(
            temperature=_number(body.get("temperature")),
            top_p=_number(body.get("top_p")),
            presence_penalty=_number(body.get("presence_penalty")),
            frequency_penalty=_number(body.get("frequency_penalty")),
        )
        max_output_tokens = body.get("max_output_tokens")
        parallel = body.get("parallel_tool_calls")
        mapped = {
            "model",
            "instructions",
            "input",
            "tools",
            "tool_choice",
            "text",
            "reasoning",
            "temperature",
            "top_p",
            "presence_penalty",
            "frequency_penalty",
            "max_output_tokens",
            "parallel_tool_calls",
            "stream",
        }
        for key, value in body.items():
            if key not in mapped:
                extension[key] = value
        if unsupported:
            extension["unsupported"] = unsupported
        return Request(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel if isinstance(parallel, bool) else None,
            response_format=response_format,
            max_output_tokens=_int(max_output_tokens),
            sampling=sampling,
            effort=effort,
            stream=body.get("stream") is True,
            extensions={EXTENSION_KEY: extension} if extension else {},
        )

    def encode_response(self, response: Response) -> Any:
        response_id = response.id or f"resp_{uuid.uuid4().hex}"
        status, details, error = _status_for(response.finish_reason, response.raw_finish_reason)
        return _resource(
            response_id=response_id,
            model=response.model,
            created=response.created if response.created is not None else int(time.time()),
            status=status,
            output=_encode_output(response.message, _id_suffix(response_id)),
            usage=_encode_usage(response.usage),
            error=error,
            incomplete_details=details,
        )

    def encode_stream(self, deltas: Iterable[Delta]) -> Iterator[StreamEvent]:
        prefix = self.options.extra.get("reasoning_event")
        writer = _StreamWriter(
            prefix if prefix in REASONING_EVENT_PREFIXES else "response.reasoning"
        )
        for delta in deltas:
            yield from writer.feed(delta)
        yield from writer.finish()

    def encode_error(self, error: ModelError) -> tuple[int, Any]:
        status, error_type = _KIND_WIRE.get(error.kind, (500, "server_error"))
        body = {"message": error.message, "type": error_type, "param": None, "code": error.code}
        return error.status or status, {"error": body}


# --------------------------------------------------------------------------------------------
# Request helpers
# --------------------------------------------------------------------------------------------


def _message_item(role: str, content: list[dict[str, Any]], phase: str | None) -> dict[str, Any]:
    item: dict[str, Any] = {"type": "message", "role": role, "content": content}
    if phase is not None:
        item["phase"] = phase
    return item


def _content_part(part: Part, role: str, loc: str, losses: _Losses) -> dict[str, Any] | None:
    if isinstance(part, TextPart):
        return {"type": "output_text" if role == "assistant" else "input_text", "text": part.text}
    if isinstance(part, RefusalPart):
        if role == "assistant":
            return {"type": "refusal", "refusal": part.text}
        losses.add(loc, "a refusal outside an assistant message is sent as text", "approximated")
        return {"type": "input_text", "text": part.text}
    if isinstance(part, ImagePart | FilePart):
        if role != "user":
            losses.add(loc, f"{role} messages carry no images or files in the responses format")
            return None
        if isinstance(part, ImagePart):
            return _image_part(part, loc, losses)
        return _file_part(part, loc, losses)
    losses.add(loc, f"a {part.type} part cannot appear in a message item")
    return None


def _image_part(part: ImagePart, loc: str, losses: _Losses) -> dict[str, Any] | None:
    url = part.url
    if url is None and part.data is not None:
        media_type = part.media_type
        if media_type is None:
            losses.add(
                f"{loc}.media_type",
                "image data without a media type sent as image/png",
                "approximated",
            )
            media_type = "image/png"
        url = f"data:{media_type};base64,{part.data}"
    if url is None:
        losses.add(loc, "image without a URL or data")
        return None
    return {"type": "input_image", "image_url": url, "detail": part.detail or "auto"}


def _file_part(part: FilePart, loc: str, losses: _Losses) -> dict[str, Any] | None:
    item: dict[str, Any] = {"type": "input_file"}
    references = [name for name in ("file_id", "url", "data") if getattr(part, name)]
    if part.file_id:
        item["file_id"] = part.file_id  # hosted reference; absent from the open specification
    elif part.url:
        item["file_url"] = part.url
    elif part.data:
        # Data URL as in the hosted reference's examples; plain base64 without a media type.
        media_type = part.media_type
        item["file_data"] = f"data:{media_type};base64,{part.data}" if media_type else part.data
    else:
        losses.add(loc, "file without a file ID, URL or data")
        return None
    if len(references) > 1:
        losses.add(loc, f"one file reference is sent; dropped {', '.join(references[1:])}")
    if part.media_type and "file_data" not in item:
        losses.add(f"{loc}.media_type", "file references carry no media type")
    if part.name:
        item["filename"] = part.name
    return item


def _function_call_item(part: ToolCallPart, loc: str, losses: _Losses) -> dict[str, Any]:
    arguments = part.arguments or "{}"
    try:
        json.loads(arguments)
    except json.JSONDecodeError:
        losses.add(
            f"{loc}.arguments", "arguments are not valid JSON; sent unchanged", "approximated"
        )
    return {"type": "function_call", "call_id": part.id, "name": part.name, "arguments": arguments}


def _encode_tool(tool: ToolSpec) -> dict[str, Any]:
    encoded: dict[str, Any] = {"type": "function", "name": tool.name}
    if tool.description is not None:
        encoded["description"] = tool.description
    encoded["parameters"] = tool.parameters
    if tool.strict is not None:
        # Omitted when unset: the open specification leaves it to the server, and the hosted
        # reference then tries strict mode and falls back to best effort.
        encoded["strict"] = tool.strict
    return encoded


def _encode_tool_choice(choice: ToolChoice) -> Any:
    if isinstance(choice, NamedToolChoice):
        return {"type": "function", "name": choice.name}
    return choice


def _encode_text_format(fmt: ResponseFormat, losses: _Losses) -> dict[str, Any]:
    if fmt.type != "json_schema":
        # json_object is a request format of the hosted reference; the open specification
        # lists it only in the settings a response echoes.
        return {"type": fmt.type}
    schema = fmt.json_schema
    if schema is None:
        losses.add(
            "response_format.json_schema", "no schema given; any JSON object", "approximated"
        )
        schema = {"type": "object"}
    encoded: dict[str, Any] = {
        "type": "json_schema",
        "name": fmt.name or "response",
        "schema": schema,
    }
    if fmt.strict is not None:
        encoded["strict"] = fmt.strict
    return encoded


def _max_output_tokens(value: int, losses: _Losses) -> int:
    if value >= MIN_OUTPUT_TOKENS:
        return value
    detail = f"the open responses specification requires at least {MIN_OUTPUT_TOKENS}"
    losses.add("max_output_tokens", f"{detail}; raised from {value}", "approximated")
    return MIN_OUTPUT_TOKENS


def _merge_missing(base: dict[str, Any], extra: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Add the keys of `extra` that `base` lacks (recursively); return conflicting keys."""
    merged = dict(base)
    conflicts: list[str] = []
    for key, value in extra.items():
        if key not in merged:
            merged[key] = value
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key], nested = _merge_missing(merged[key], value)
            conflicts.extend(f"{key}.{name}" for name in nested)
        elif merged[key] != value:
            conflicts.append(key)
    return merged, conflicts


# --------------------------------------------------------------------------------------------
# Response helpers
# --------------------------------------------------------------------------------------------


def _decode_output(output: Any, model: str | None) -> list[Part]:
    parts: list[Part] = []
    tool_index = 0
    for item in _dicts(output):
        kind = item.get("type")
        if kind == "message":
            parts.extend(_message_parts(item.get("content")))
        elif kind == "function_call":
            call_id = _str(item.get("call_id"))
            parts.append(
                ToolCallPart(
                    id=call_id or synthetic_call_id(0, tool_index),
                    name=_str(item.get("name")) or "",
                    arguments=_arguments(item.get("arguments")),
                    synthetic=not call_id,
                )
            )
            tool_index += 1
        elif kind == "reasoning":
            parts.append(_reasoning_part(item, model))
        # Other items (hosted-only tool calls, compaction, `slug:type` extensions) are not
        # part of the IR; clients must ignore unknown item types (open specification).
    return parts


def _message_parts(content: Any) -> list[Part]:
    if isinstance(content, str):
        return [TextPart(text=content)] if content else []
    parts: list[Part] = []
    for part in _dicts(content):
        kind = part.get("type")
        if kind == "refusal":
            parts.append(RefusalPart(text=_str(part.get("refusal")) or ""))
        elif kind in ("output_text", "text", "input_text"):
            text = _str(part.get("text"))
            if text:
                parts.append(TextPart(text=text))
    return parts


def _reasoning_part(item: dict[str, Any], model: str | None) -> ReasoningPart:
    encrypted = _str(item.get("encrypted_content")) or None
    return ReasoningPart(
        text=_join_texts(item.get("content"), "") or None,
        summary=_join_texts(item.get("summary"), "\n\n") or None,
        opaque=encrypted,
        opaque_kind="encrypted" if encrypted else None,
        item_id=_str(item.get("id")),
        origin=Origin(format="responses", model=model or None),
    )


def _finish(body: dict[str, Any], has_calls: bool) -> tuple[FinishReason, str | None]:
    status = body.get("status")
    if status == "incomplete":
        details = body.get("incomplete_details")
        reason = _str(details.get("reason")) if isinstance(details, dict) else None
        finish: FinishReason = "other"
        if reason in INCOMPLETE_FINISH:
            finish = INCOMPLETE_FINISH[reason]
        return finish, reason or "incomplete"
    if status == "failed":
        error = body.get("error")
        code = _str(error.get("code")) if isinstance(error, dict) else None
        return "error", code or "failed"
    if status is None or status == "completed":
        return ("tool_calls" if has_calls else "stop"), "completed"
    return "other", _str(status)


def _decode_usage(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    inputs = raw.get("input_tokens_details")
    outputs = raw.get("output_tokens_details")
    inputs = inputs if isinstance(inputs, dict) else {}
    outputs = outputs if isinstance(outputs, dict) else {}
    return Usage(
        input_tokens=_int(raw.get("input_tokens")),
        output_tokens=_int(raw.get("output_tokens")),
        cached_input_tokens=_int(inputs.get("cached_tokens")),
        cache_write_tokens=_int(inputs.get("cache_write_tokens")),
        reasoning_tokens=_int(outputs.get("reasoning_tokens")),
        raw=dict(raw),
    )


def _encode_usage(usage: Usage | None) -> dict[str, Any] | None:
    if usage is None:
        return None
    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0
    details: dict[str, int] = {"cached_tokens": usage.cached_input_tokens or 0}
    if usage.cache_write_tokens is not None:
        details["cache_write_tokens"] = usage.cache_write_tokens  # hosted reference
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": details,
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": usage.reasoning_tokens or 0},
        "total_tokens": input_tokens + output_tokens,
    }


def _status_for(
    finish: FinishReason, raw: str | None
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """IR finish reason -> (status, incomplete_details, error) of a response object."""
    if finish == "length":
        return "incomplete", {"reason": "max_output_tokens"}, None
    if finish == "content_filter":
        return "incomplete", {"reason": "content_filter"}, None
    if finish == "error":
        error = {"code": raw or "server_error", "message": "the model failed to respond"}
        return "failed", None, error
    if finish == "other" and raw in OTHER_INCOMPLETE_REASONS:
        return "incomplete", {"reason": raw}, None
    return "completed", None, None


def _encode_output(message: Message, suffix: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    content: list[dict[str, Any]] = []

    def message_item(parts: list[dict[str, Any]]) -> dict[str, Any]:
        item_id = f"msg_{suffix}_{len(items)}"
        return {
            "type": "message",
            "id": item_id,
            "status": "completed",
            "role": "assistant",
            "content": parts,
        }

    for part in message.content:
        if isinstance(part, TextPart):
            content.append({"type": "output_text", "text": part.text, "annotations": []})
            continue
        if isinstance(part, RefusalPart):
            content.append({"type": "refusal", "refusal": part.text})
            continue
        if not isinstance(part, ToolCallPart | ReasoningPart):
            continue  # images, files and tool results have no output item
        if content:
            items.append(message_item(content))
            content = []
        if isinstance(part, ToolCallPart):
            items.append(
                {
                    "type": "function_call",
                    "id": f"fc_{suffix}_{len(items)}",
                    "call_id": part.id,
                    "name": part.name,
                    "arguments": part.arguments or "{}",
                    "status": "completed",
                }
            )
        else:
            items.append(_reasoning_output(part, part.item_id or f"rs_{suffix}_{len(items)}"))
    if content:
        items.append(message_item(content))
    return items


def _reasoning_output(part: ReasoningPart, item_id: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "reasoning",
        "id": item_id,
        "summary": [{"type": "summary_text", "text": part.summary}] if part.summary else [],
        "content": [{"type": "reasoning_text", "text": part.text}] if part.text else [],
    }
    if part.opaque and part.opaque_kind == "encrypted":
        item["encrypted_content"] = part.opaque
    return item


def _resource(
    *,
    response_id: str,
    model: str | None,
    created: int,
    status: str,
    output: list[dict[str, Any]],
    usage: dict[str, Any] | None,
    error: dict[str, Any] | None = None,
    incomplete_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A complete response object (every field the open specification requires).

    The request settings it echoes are not part of the IR response; neutral values are written
    and the proxy may replace them with the caller's request settings.
    """
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "completed_at": created if status == "completed" else None,
        "status": status,
        "incomplete_details": incomplete_details,
        "model": model or "",
        "previous_response_id": None,
        "instructions": None,
        "output": output,
        "error": error,
        "tools": [],
        "tool_choice": "auto",
        "truncation": "disabled",
        "parallel_tool_calls": True,
        "text": {"format": {"type": "text"}},
        "top_p": 1.0,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
        "top_logprobs": 0,
        "temperature": 1.0,
        "reasoning": None,
        "usage": usage,
        "max_output_tokens": None,
        "max_tool_calls": None,
        "store": False,
        "background": False,
        "service_tier": "default",
        "metadata": {},
        "safety_identifier": None,
        "prompt_cache_key": None,
    }


def _error_kind(code: str | None, error_type: str | None) -> ErrorKind:
    for value in (code, error_type):
        if value is None:
            continue
        if value in _CODE_KINDS:
            return _CODE_KINDS[value]
        if "image" in value:
            return "bad_request"  # the hosted reference's image validation codes
    return "server"


# --------------------------------------------------------------------------------------------
# Stream decoding state
# --------------------------------------------------------------------------------------------


@dataclass
class _Item:
    """One streamed output item: its kind, tool index, and the text emitted per content slot."""

    kind: str
    item_id: str | None
    tool: int | None = None
    args: str = ""
    ended: bool = False
    texts: dict[tuple[str, int], str] = field(default_factory=dict)

    def text(self, key: tuple[str, int], *, delta: Any = None, full: Any = None) -> list[Delta]:
        """Emit a text delta, or the part of a final text not yet emitted (`.done` events)."""
        emitted = self.texts.get(key, "")
        if isinstance(full, str):
            if not full.startswith(emitted) or len(full) == len(emitted):
                return []
            piece = full[len(emitted) :]
        else:
            piece = delta if isinstance(delta, str) else ""
        if not piece:
            return []
        if key[0] == "content":
            self.texts[key] = emitted + piece
            return [TextDelta(text=piece)]
        # Reasoning text and summaries both become reasoning deltas; separate the parts.
        separate = not emitted and any(text for slot, text in self.texts.items() if slot != key)
        self.texts[key] = emitted + piece
        return [ReasoningDelta(text="\n\n" + piece if separate else piece)]


@dataclass
class _Decoding:
    items: dict[int, _Item] = field(default_factory=dict)
    ids: dict[str, int] = field(default_factory=dict)
    has_calls: bool = False
    errored: bool = False
    saw_items: bool = False

    def register(self, output_index: Any, raw: dict[str, Any]) -> _Item:
        """Track an announced item; adopt the placeholder of events that came before it."""
        item_id = _str(raw.get("id"))
        kind = _str(raw.get("type")) or "unknown"
        self.saw_items = True
        known = self.find(item_id, output_index)
        if known is not None and known.kind == "unknown":
            known.kind = kind
            if item_id and known.item_id is None and isinstance(output_index, int):
                known.item_id = item_id
                self.ids[item_id] = output_index
            return known
        index = output_index if isinstance(output_index, int) else len(self.items)
        item = _Item(kind=kind, item_id=item_id)
        self.items[index] = item
        if item_id:
            self.ids[item_id] = index
        return item

    def find(self, item_id: str | None, output_index: Any) -> _Item | None:
        if item_id is not None and item_id in self.ids:
            return self.items[self.ids[item_id]]
        if isinstance(output_index, int):
            return self.items.get(output_index)
        return None

    def resolve(self, payload: dict[str, Any]) -> _Item:
        """The item an event belongs to, by item ID first and output index second."""
        item_id = _str(payload.get("item_id"))
        item = self.find(item_id, payload.get("output_index"))
        if item is None:  # an item the server never announced: track it anyway
            raw = {"id": item_id, "type": "unknown"}
            item = self.register(payload.get("output_index"), raw)
        return item

    def close_tools(self) -> list[Delta]:
        deltas: list[Delta] = []
        for item in self.items.values():
            if item.tool is not None and not item.ended:
                item.ended = True
                deltas.append(ToolCallEndDelta(index=item.tool))
        return deltas


def _decoding(state: StreamState) -> _Decoding:
    current = state.data.get(EXTENSION_KEY)
    if not isinstance(current, _Decoding):
        current = _Decoding()
        state.data[EXTENSION_KEY] = current
    return current


def _start_delta(resp: Any, state: StreamState) -> list[Delta]:
    if state.started:
        return []
    state.started = True
    resp = resp if isinstance(resp, dict) else {}
    return [StartDelta(id=_str(resp.get("id")), model=_str(resp.get("model")))]


def _open_tool(item: _Item, raw: dict[str, Any], state: StreamState, dec: _Decoding) -> list[Delta]:
    index = state.tool_index
    state.tool_index += 1
    item.tool = index
    dec.has_calls = True
    call_id = _str(raw.get("call_id"))
    deltas: list[Delta] = [
        ToolCallStartDelta(
            index=index,
            id=call_id or synthetic_call_id(0, index),
            name=_str(raw.get("name")) or "",
            synthetic=not call_id,
        )
    ]
    arguments = raw.get("arguments")
    if isinstance(arguments, str) and arguments:
        item.args = arguments
        deltas.append(ToolCallArgsDelta(index=index, delta=arguments))
    return deltas


def _reconcile_args(item: _Item, full: Any) -> list[Delta]:
    """Emit the arguments a `.done` event holds beyond what the deltas already carried."""
    if item.tool is None or item.ended or not isinstance(full, str):
        return []
    if len(full) <= len(item.args) or not full.startswith(item.args):
        return []
    piece = full[len(item.args) :]
    item.args = full
    return [ToolCallArgsDelta(index=item.tool, delta=piece)]


def _part_deltas(parts: list[Part], state: StreamState, dec: _Decoding) -> list[Delta]:
    deltas: list[Delta] = []
    for part in parts:
        if isinstance(part, TextPart | RefusalPart):
            deltas.append(TextDelta(text=part.text))
        elif isinstance(part, ReasoningPart):
            if part.text or part.summary:
                deltas.append(ReasoningDelta(text=part.text or part.summary or ""))
            if part.opaque:
                deltas.append(ReasoningDelta(opaque=part.opaque, opaque_kind=part.opaque_kind))
        elif isinstance(part, ToolCallPart):
            index = state.tool_index
            state.tool_index += 1
            dec.has_calls = True
            deltas.append(
                ToolCallStartDelta(
                    index=index, id=part.id, name=part.name, synthetic=part.synthetic
                )
            )
            deltas.append(ToolCallArgsDelta(index=index, delta=part.arguments))
            deltas.append(ToolCallEndDelta(index=index))
    return deltas


def _stream_error(payload: dict[str, Any], dec: _Decoding) -> ErrorDelta:
    nested = payload.get("error")
    error = nested if isinstance(nested, dict) else payload
    error_type = _str(error.get("type"))
    dec.errored = True
    return ErrorDelta(
        kind=_error_kind(_str(error.get("code")), None if error_type == "error" else error_type),
        message=_str(error.get("message")) or "error event without a message",
    )


# --------------------------------------------------------------------------------------------
# Stream encoding
# --------------------------------------------------------------------------------------------


class _StreamWriter:
    """Turns IR deltas into a complete event sequence of the open responses specification."""

    def __init__(self, reasoning_event: str) -> None:
        self.reasoning_event = reasoning_event
        self.sequence = 0
        self.response_id = f"resp_{uuid.uuid4().hex}"
        self.model: str | None = None
        self.created = int(time.time())
        self.started = False
        self.next_index = 0
        self.done: dict[int, dict[str, Any]] = {}
        self.message: dict[str, Any] | None = None
        self.reasoning: dict[str, Any] | None = None
        self.tools: dict[int, dict[str, Any]] = {}
        self.usage: Usage | None = None
        self.finish_reason: FinishReason = "stop"
        self.raw_finish: str | None = None
        self.error: ErrorDelta | None = None

    # ---- framing ----

    def event(self, kind: str, **fields: Any) -> StreamEvent:
        payload = {"type": kind, "sequence_number": self.sequence, **fields}
        self.sequence += 1
        return sse_json(payload, event=kind)

    def resource(self, status: str, **extra: Any) -> dict[str, Any]:
        output = [self.done[index] for index in sorted(self.done)]
        usage = _encode_usage(self.usage) if status != "in_progress" else None
        return _resource(
            response_id=self.response_id,
            model=self.model,
            created=self.created,
            status=status,
            output=output,
            usage=usage,
            **extra,
        )

    def item_id(self, prefix: str, index: int) -> str:
        return f"{prefix}_{_id_suffix(self.response_id)}_{index}"

    def take_index(self) -> int:
        index = self.next_index
        self.next_index += 1
        return index

    # ---- deltas ----

    def feed(self, delta: Delta) -> list[StreamEvent]:
        delta = refusal_as_text(delta)
        if isinstance(delta, StartDelta):
            if not self.started:
                self.response_id = delta.id or self.response_id
                self.model = delta.model or self.model
            return []
        if isinstance(delta, UsageDelta):
            self.usage = merge_usage(self.usage, delta.usage)
            return []
        if isinstance(delta, FinishDelta):
            self.finish_reason, self.raw_finish = delta.reason, delta.raw
            return []
        if isinstance(delta, ErrorDelta):
            self.error = self.error or delta
            return []
        events = self.start()
        if isinstance(delta, TextDelta):
            events += self.text(delta.text)
        elif isinstance(delta, ReasoningDelta):
            events += self.reason(delta)
        elif isinstance(delta, ToolCallStartDelta):
            events += self.close_message() + self.close_reasoning()
            events += self.open_tool(delta)
        elif isinstance(delta, ToolCallArgsDelta):
            events += self.tool_args(delta)
        elif isinstance(delta, ToolCallEndDelta):
            events += self.close_tool(delta.index)
        return events

    def start(self) -> list[StreamEvent]:
        if self.started:
            return []
        self.started = True
        return [
            self.event("response.created", response=self.resource("in_progress")),
            self.event("response.in_progress", response=self.resource("in_progress")),
        ]

    def text(self, text: str) -> list[StreamEvent]:
        if not text:
            return []
        events = self.close_reasoning()
        if self.message is None:
            index = self.take_index()
            item_id = self.item_id("msg", index)
            self.message = {"index": index, "id": item_id, "text": ""}
            item = {
                "id": item_id,
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            }
            part = {"type": "output_text", "text": "", "annotations": []}
            events.append(self.event("response.output_item.added", output_index=index, item=item))
            events.append(self._part_event("response.content_part.added", self.message, part))
        self.message["text"] += text
        events.append(
            self.event(
                "response.output_text.delta",
                item_id=self.message["id"],
                output_index=self.message["index"],
                content_index=0,
                delta=text,
                logprobs=[],
            )
        )
        return events

    def close_message(self, status: str = "completed") -> list[StreamEvent]:
        if self.message is None:
            return []
        state, self.message = self.message, None
        part = {"type": "output_text", "text": state["text"], "annotations": []}
        item = {
            "id": state["id"],
            "type": "message",
            "status": status,
            "role": "assistant",
            "content": [part],
        }
        self.done[state["index"]] = item
        return [
            self.event(
                "response.output_text.done",
                item_id=state["id"],
                output_index=state["index"],
                content_index=0,
                text=state["text"],
                logprobs=[],
            ),
            self._part_event("response.content_part.done", state, part),
            self.event("response.output_item.done", output_index=state["index"], item=item),
        ]

    def reason(self, delta: ReasoningDelta) -> list[StreamEvent]:
        events = self.close_message()
        if self.reasoning is None:
            index = self.take_index()
            item_id = self.item_id("rs", index)
            self.reasoning = {"index": index, "id": item_id, "text": None, "encrypted": None}
            item = {"id": item_id, "type": "reasoning", "summary": [], "content": []}
            events.append(self.event("response.output_item.added", output_index=index, item=item))
        state = self.reasoning
        if delta.text:
            if state["text"] is None:
                state["text"] = ""
                part = {"type": "reasoning_text", "text": ""}
                events.append(self._part_event("response.content_part.added", state, part))
            state["text"] += delta.text
            events.append(
                self.event(
                    f"{self.reasoning_event}.delta",
                    item_id=state["id"],
                    output_index=state["index"],
                    content_index=0,
                    delta=delta.text,
                )
            )
        if delta.opaque and delta.opaque_kind == "encrypted":
            state["encrypted"] = (state["encrypted"] or "") + delta.opaque
        # Signatures and redacted payloads of other formats have no field here.
        return events

    def close_reasoning(self) -> list[StreamEvent]:
        if self.reasoning is None:
            return []
        state, self.reasoning = self.reasoning, None
        events: list[StreamEvent] = []
        item: dict[str, Any] = {
            "id": state["id"],
            "type": "reasoning",
            "summary": [],
            "content": [],
        }
        if state["text"] is not None:
            part = {"type": "reasoning_text", "text": state["text"]}
            item["content"] = [part]
            events.append(
                self.event(
                    f"{self.reasoning_event}.done",
                    item_id=state["id"],
                    output_index=state["index"],
                    content_index=0,
                    text=state["text"],
                )
            )
            events.append(self._part_event("response.content_part.done", state, part))
        if state["encrypted"] is not None:
            item["encrypted_content"] = state["encrypted"]
        self.done[state["index"]] = item
        events.append(
            self.event("response.output_item.done", output_index=state["index"], item=item)
        )
        return events

    def open_tool(self, delta: ToolCallStartDelta) -> list[StreamEvent]:
        index = self.take_index()
        state = {
            "index": index,
            "id": self.item_id("fc", index),
            "call_id": delta.id,
            "name": delta.name,
            "args": "",
            "done": False,
        }
        self.tools[delta.index] = state
        return [
            self.event(
                "response.output_item.added",
                output_index=index,
                item=self._call(state, "in_progress"),
            )
        ]

    def tool_args(self, delta: ToolCallArgsDelta) -> list[StreamEvent]:
        state = self.tools.get(delta.index)
        if state is None or state["done"] or not delta.delta:
            return []
        state["args"] += delta.delta
        return [
            self.event(
                "response.function_call_arguments.delta",
                item_id=state["id"],
                output_index=state["index"],
                delta=delta.delta,
            )
        ]

    def close_tool(self, tool_index: int) -> list[StreamEvent]:
        state = self.tools.get(tool_index)
        if state is None or state["done"]:
            return []
        state["done"] = True
        item = self._call(state, "completed")
        self.done[state["index"]] = item
        return [
            self.event(
                "response.function_call_arguments.done",
                item_id=state["id"],
                output_index=state["index"],
                arguments=item["arguments"],
            ),
            self.event("response.output_item.done", output_index=state["index"], item=item),
        ]

    def finish(self) -> list[StreamEvent]:
        events = self.start()
        incomplete = self.error is None and self.finish_reason in ("length", "content_filter")
        events += self.close_message("incomplete" if incomplete else "completed")
        events += self.close_reasoning()
        for tool_index in sorted(self.tools):
            events += self.close_tool(tool_index)
        if self.error is not None:
            _status, error_type = _KIND_WIRE.get(self.error.kind, (500, "server_error"))
            code = "rate_limit_exceeded" if self.error.kind == "rate_limit" else "server_error"
            message = self.error.message
            nested = {"type": error_type, "code": code, "message": message, "param": None}
            # Both shapes in one event: nested (open specification) and top level (hosted).
            events.append(self.event("error", code=code, message=message, param=None, error=nested))
            failed = self.resource("failed", error={"code": code, "message": message})
            events.append(self.event("response.failed", response=failed))
            return events
        status, details, error = _status_for(self.finish_reason, self.raw_finish)
        if status == "failed":
            events.append(
                self.event("response.failed", response=self.resource(status, error=error))
            )
        elif status == "incomplete":
            resource = self.resource(status, incomplete_details=details)
            events.append(self.event("response.incomplete", response=resource))
        else:
            events.append(self.event("response.completed", response=self.resource("completed")))
        return events

    def _part_event(self, kind: str, state: dict[str, Any], part: dict[str, Any]) -> StreamEvent:
        return self.event(
            kind, item_id=state["id"], output_index=state["index"], content_index=0, part=part
        )

    @staticmethod
    def _call(state: dict[str, Any], status: str) -> dict[str, Any]:
        arguments = state["args"] if status == "in_progress" else state["args"] or "{}"
        return {
            "id": state["id"],
            "type": "function_call",
            "status": status,
            "call_id": state["call_id"],
            "name": state["name"],
            "arguments": arguments,
        }


# --------------------------------------------------------------------------------------------
# Server-side request decoding
# --------------------------------------------------------------------------------------------


def _decode_input(
    raw: Any, model: str, system: list[TextPart], unsupported: list[dict[str, Any]]
) -> list[Message]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [Message.text("user", raw)]
    if not isinstance(raw, list):
        raise ModelError("bad_request", "input must be a string or a list of items", status=400)
    messages: list[Message] = []
    call_names: dict[str, str] = {}
    leading = True  # system and developer messages before the conversation become `system`
    for index, item in enumerate(raw):
        where = f"input[{index}]"
        if not isinstance(item, dict):
            unsupported.append({"where": where, "type": type(item).__name__, "item": item})
            continue
        kind = item.get("type") or ("message" if "role" in item else None)
        role = item.get("role")
        if kind == "message" and role in ("system", "developer"):
            texts: list[str] = []
            for part in _input_parts(item.get("content"), where, unsupported):
                if isinstance(part, TextPart):
                    texts.append(part.text)
                else:  # system and developer content is input_text only
                    unsupported.append({"where": where, "type": part.type, "item": None})
            text = "".join(texts)
            if leading:
                system.append(TextPart(text=text))
            else:
                messages.append(Message.text("system", text))
            continue
        leading = False
        if kind == "message" and role == "user":
            content = _input_parts(item.get("content"), where, unsupported)
            messages.append(Message(role="user", content=content))
        elif kind == "message" and role == "assistant":
            target = _assistant(messages, joins_after_call=False)
            target.content.extend(_input_parts(item.get("content"), where, unsupported))
            phase = item.get("phase")
            if isinstance(phase, str):
                target.metadata["phase"] = phase
        elif kind == "function_call":
            call_id = _str(item.get("call_id")) or synthetic_call_id(len(messages), index)
            name = _str(item.get("name")) or ""
            call_names[call_id] = name
            call = ToolCallPart(
                id=call_id,
                name=name,
                arguments=_arguments(item.get("arguments")),
                synthetic=not item.get("call_id"),
            )
            _assistant(messages, joins_after_call=True, any_content=True).content.append(call)
        elif kind == "function_call_output":
            call_id = _str(item.get("call_id")) or ""
            result = ToolResultPart(
                call_id=call_id,
                name=call_names.get(call_id),
                content=_tool_output(item.get("output"), where, unsupported),
            )
            messages.append(Message(role="tool", content=[result]))
        elif kind == "reasoning":
            part = _reasoning_part(item, model)
            _assistant(messages, joins_after_call=True).content.append(part)
        else:
            unsupported.append({"where": where, "type": kind or "unknown", "item": item})
    return messages


def _assistant(
    messages: list[Message], *, joins_after_call: bool, any_content: bool = False
) -> Message:
    """The assistant message the next output-like item belongs to.

    One IR assistant turn becomes several items (reasoning, message, function calls), so they
    are folded back: function calls join any assistant message; message items join one that
    holds only reasoning; reasoning items also join one that ends with a call (interleaved
    reasoning). Anything else starts a new assistant message.
    """
    last = messages[-1] if messages else None
    if last is not None and last.role == "assistant":
        only_reasoning = all(isinstance(p, ReasoningPart) for p in last.content)
        ends_with_call = bool(last.content) and isinstance(last.content[-1], ToolCallPart)
        if any_content or only_reasoning or (joins_after_call and ends_with_call):
            return last
    message = Message(role="assistant")
    messages.append(message)
    return message


def _input_parts(content: Any, where: str, unsupported: list[dict[str, Any]]) -> list[Part]:
    if content is None:
        return []
    if isinstance(content, str):
        return [TextPart(text=content)]
    parts: list[Part] = []
    for index, part in enumerate(content if isinstance(content, list) else [content]):
        loc = f"{where}.content[{index}]"
        kind = part.get("type") if isinstance(part, dict) else None
        if not isinstance(part, dict):
            unsupported.append({"where": loc, "type": "unknown", "item": part})
        elif kind in ("input_text", "output_text", "text"):
            parts.append(TextPart(text=_str(part.get("text")) or ""))
        elif kind == "refusal":
            parts.append(RefusalPart(text=_str(part.get("refusal")) or ""))
        elif kind == "input_image" and _str(part.get("image_url")):
            parts.append(ImagePart(url=part["image_url"], detail=_str(part.get("detail"))))
        elif kind == "input_file" and any(
            part.get(k) for k in ("file_id", "file_url", "file_data")
        ):
            parts.append(_decode_file(part))
        else:  # images by file ID (no IR field), video, unknown parts
            unsupported.append({"where": loc, "type": kind or "unknown", "item": part})
    return parts


def _decode_file(part: dict[str, Any]) -> FilePart:
    data = _str(part.get("file_data"))
    media_type = None
    if data and data.startswith("data:") and ";base64," in data:
        header, data = data.split(";base64,", 1)
        media_type = header[len("data:") :] or None
    return FilePart(
        file_id=_str(part.get("file_id")),
        url=_str(part.get("file_url")),
        data=data,
        media_type=media_type,
        name=_str(part.get("filename")),
    )


def _tool_output(
    output: Any, where: str, unsupported: list[dict[str, Any]]
) -> list[TextPart | ImagePart]:
    if output is None:
        return []
    if isinstance(output, str):
        return [TextPart(text=output)] if output else []
    if not isinstance(output, list):
        return [TextPart(text=json.dumps(output, ensure_ascii=False))]
    content: list[TextPart | ImagePart] = []
    for part in _input_parts(output, f"{where}.output", unsupported):
        if isinstance(part, TextPart | ImagePart):
            content.append(part)
        else:  # files in tool results have no IR field
            unsupported.append({"where": f"{where}.output", "type": part.type, "item": None})
    return content


def _decode_tool(tool: dict[str, Any]) -> ToolSpec:
    parameters = tool.get("parameters")
    strict = tool.get("strict")
    spec = ToolSpec(
        name=tool["name"],
        description=_str(tool.get("description")),
        strict=strict if isinstance(strict, bool) else None,
    )
    if isinstance(parameters, dict):
        spec.parameters = parameters
    return spec


def _decode_tool_choice(raw: Any, extension: dict[str, Any]) -> ToolChoice | None:
    if raw in ("auto", "none", "required"):
        return cast(ToolChoice, raw)
    if isinstance(raw, dict) and raw.get("type") == "function" and _str(raw.get("name")):
        return NamedToolChoice(name=raw["name"])
    if raw is not None:  # allowed_tools and hosted-only choices pass through
        extension["tool_choice"] = raw
    return None


def _decode_text(raw: Any, extension: dict[str, Any]) -> ResponseFormat | None:
    if not isinstance(raw, dict):
        return None
    rest = {key: value for key, value in raw.items() if key != "format"}
    fmt = raw.get("format")
    result: ResponseFormat | None = None
    if isinstance(fmt, dict) and fmt.get("type") in ("text", "json_object"):
        result = ResponseFormat(type=fmt["type"])
    elif isinstance(fmt, dict) and fmt.get("type") == "json_schema":
        strict = fmt.get("strict")
        schema = fmt.get("schema")
        result = ResponseFormat(
            type="json_schema",
            name=_str(fmt.get("name")),
            json_schema=schema if isinstance(schema, dict) else None,
            strict=strict if isinstance(strict, bool) else None,
        )
        extra = {k: v for k, v in fmt.items() if k not in ("type", "name", "schema", "strict")}
        if extra:
            rest["format"] = extra
    elif fmt is not None:
        rest["format"] = fmt
    if rest:
        extension["text"] = rest
    return result


def _decode_reasoning(raw: Any, extension: dict[str, Any]) -> Effort | None:
    if not isinstance(raw, dict):
        return None
    rest = {key: value for key, value in raw.items() if key != "effort"}
    effort = raw.get("effort")
    result: Effort | None = None
    if effort in EFFORT_LEVELS:
        result = cast(Effort, effort)
    elif effort is not None:
        rest["effort"] = effort
    if rest:
        extension["reasoning"] = rest
    return result


# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _join_texts(parts: Any, separator: str) -> str:
    return separator.join(t for p in _dicts(parts) if isinstance(t := p.get("text"), str) and t)


def _arguments(value: Any) -> str:
    if isinstance(value, str):
        return value or "{}"
    if value is None:
        return "{}"
    return json.dumps(value, ensure_ascii=False)


def _id_suffix(response_id: str) -> str:
    return response_id.removeprefix("resp_") or response_id
