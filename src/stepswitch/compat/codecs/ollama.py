"""Ollama native API codec: `POST /api/chat` with NDJSON streams (ADR 0003, ADR 0008).

Wire facts (docs/RESEARCH.md, "Ollama native API"; re-checked on 2026-10-16 against
https://docs.ollama.com/api/chat, the request and response structs in the server's `api` package
on main, and a local Ollama 0.34.4 server):

* The server streams unless told otherwise, so every request sets `stream` explicitly.
* Tool calls are `{"id", "function": {"index", "name", "arguments"}}` with `arguments` as a JSON
  object. Current servers send an `id` (`call_` plus 8 characters on 0.34.4); the reference
  examples and older servers send none, so missing IDs are synthesized. Tool results are `tool`
  messages with `tool_name` and `tool_call_id` (servers that predate the ID ignore the field).
  Streams send every tool call whole, one call per line on 0.34.4.
* `prompt_eval_count` is the whole prompt, cached tokens included, and `prompt_eval_cached_count`
  is the cached part: 0.34.4 reported 20 and 19 for a repeated prompt, and the server's own
  chat-completions layer maps them to `prompt_tokens` and `cached_tokens`. Durations are in ns.
* `think` takes true, false or a level name. Servers accept "low", "medium", "high" and "max" and
  resolve them per model (gpt-oss renders "max" as its "high"); "minimal" and "xhigh" are
  rejected with HTTP 400. Models without thinking accept `false` and reject everything else.
* Errors are `{"error": "<message>"}` with the HTTP status. A mid-stream error is an NDJSON line
  with `error`, and the status stays 200.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any, ClassVar

from stepswitch.compat.codecs.base import Codec, CodecOptions, Framing, HttpCall, StreamState
from stepswitch.compat.errors import ModelError
from stepswitch.compat.framing import StreamEvent, sse_json
from stepswitch.compat.ir import (
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
    ToolResultPart,
    ToolSpec,
    Usage,
    UsageDelta,
    synthetic_call_id,
)
from stepswitch.compat.stream import merge_usage

PATH = "/api/chat"

# `think` levels every current server accepts; the server resolves them per model.
THINK_LEVELS: tuple[str, ...] = ("low", "medium", "high", "max")
# IR effort -> `think`. The server rejects "minimal" and "xhigh"; they are clamped the way the
# server's own chat-completions layer clamps them, and the clamp is reported.
EFFORT_THINK: dict[str, str] = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "max",
    "max": "max",
}
_RANK = {level: rank for rank, level in enumerate(THINK_LEVELS)}
# `think` level names that are also IR efforts (for requests decoded by the proxy).
_LEVEL_EFFORT: dict[str, Effort] = {"low": "low", "medium": "medium", "high": "high", "max": "max"}

SAMPLING_OPTIONS = (
    "temperature",
    "top_p",
    "top_k",
    "seed",
    "presence_penalty",
    "frequency_penalty",
)
# Top-level request fields carried in `Request.extensions` under their own names.
PASSTHROUGH = ("keep_alive", "logprobs", "top_logprobs", "truncate", "shift")
USAGE_FIELDS = (
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_cached_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
)
_STATUS_FOR_KIND: dict[str, int] = {
    "bad_request": 400,
    "unsupported": 400,
    "authentication": 401,
    "permission": 403,
    "not_found": 404,
    "too_large": 413,
    "rate_limit": 429,
    "overloaded": 503,
    "timeout": 504,
}


class OllamaCodec(Codec):
    key: ClassVar[str] = "ollama"
    format: ClassVar[str] = "ollama-native"
    framing: ClassVar[Framing] = "ndjson"

    def __init__(self, options: CodecOptions | None = None) -> None:
        super().__init__(options)
        # Turn index of the next assistant message, from the last encoded request. It seeds the
        # synthetic tool-call IDs of the answer so they stay distinct across a conversation.
        self._turn = 0

    # ---- client side ----------------------------------------------------------------------

    def encode_request(self, request: Request, report: ConversionReport) -> HttpCall:
        self._turn = sum(1 for message in request.messages if message.role == "assistant")
        body: dict[str, Any] = {
            "model": request.model,
            "messages": self._encode_messages(request, report),
        }
        tools = self._encode_tools(request, report)
        if tools:
            body["tools"] = tools
        fmt = self._encode_format(request.response_format, report)
        if fmt is not None:
            body["format"] = fmt
        options = _encode_options(request)
        if options:
            body["options"] = options
        think = self._encode_think(request, report)
        if think is not None:
            body["think"] = think
        for key in PASSTHROUGH:
            if key in request.extensions:
                body[key] = request.extensions[key]
        body["stream"] = request.stream  # always explicit: the server streams by default
        return HttpCall("POST", PATH, json=body, stream=request.stream)

    def decode_response(self, body: Any) -> Response:
        if not isinstance(body, dict):
            raise ModelError("invalid_response", "Ollama chat response is not an object", raw=body)
        if body.get("error"):
            raise ModelError("server", str(body["error"]), raw=body)
        model = _text(body.get("model")) or None
        message = body.get("message")
        parts: list[Part] = []
        calls: list[ToolCallPart] = []
        if isinstance(message, dict):
            thinking = _text(message.get("thinking"))
            if thinking:
                parts.append(
                    ReasoningPart(text=thinking, origin=Origin(format=self.format, model=model))
                )
            content = _text(message.get("content"))
            if content:
                parts.append(TextPart(text=content))
            parts.extend(_images(message.get("images")))
            try:
                calls = [
                    _decode_call(raw, self._turn, index)
                    for index, raw in enumerate(_list(message.get("tool_calls")))
                ]
            except ValueError as exc:
                raise ModelError("invalid_response", str(exc), raw=body) from exc
            parts.extend(calls)
        raw_reason = _text(body.get("done_reason")) or None
        return Response(
            model=model,
            message=Message(role="assistant", content=parts),
            finish_reason=_finish(raw_reason, bool(calls)),
            raw_finish_reason=raw_reason,
            usage=_usage(body),
            created=_parse_time(body.get("created_at")),
        )

    def decode_stream_event(self, event: StreamEvent, state: StreamState) -> list[Delta]:
        if state.finished:
            return []
        try:
            line = event.json()
        except json.JSONDecodeError:
            state.finished = True
            return [
                ErrorDelta(kind="invalid_response", message=f"bad NDJSON line: {event.data[:200]}")
            ]
        if not isinstance(line, dict):
            state.finished = True
            return [ErrorDelta(kind="invalid_response", message="NDJSON line is not an object")]
        if line.get("error"):
            # Mid-stream errors arrive as a line with `error`; the HTTP status stays 200.
            state.finished = True
            return [ErrorDelta(kind="server", message=str(line["error"]))]
        deltas: list[Delta] = []
        if not state.started:
            state.started = True
            deltas.append(StartDelta(model=_text(line.get("model")) or None))
        message = line.get("message")
        if isinstance(message, dict):
            thinking = _text(message.get("thinking"))
            if thinking:
                deltas.append(ReasoningDelta(text=thinking))
            content = _text(message.get("content"))
            if content:
                deltas.append(TextDelta(text=content))
            for raw in _list(message.get("tool_calls")):
                index = state.tool_index
                state.tool_index += 1
                try:
                    call = _decode_call(raw, self._turn, index)
                except ValueError as exc:
                    state.finished = True
                    deltas.append(ErrorDelta(kind="invalid_response", message=str(exc)))
                    return deltas
                # Tool calls arrive whole, so each one opens and closes in the same line.
                deltas.append(
                    ToolCallStartDelta(
                        index=index, id=call.id, name=call.name, synthetic=call.synthetic
                    )
                )
                deltas.append(ToolCallArgsDelta(index=index, delta=call.arguments))
                deltas.append(ToolCallEndDelta(index=index))
        if line.get("done") is True:
            usage = _usage(line)
            if usage is not None:
                deltas.append(UsageDelta(usage=usage))
            raw_reason = _text(line.get("done_reason")) or None
            deltas.append(
                FinishDelta(reason=_finish(raw_reason, state.tool_index > 0), raw=raw_reason)
            )
            state.finished = True
        return deltas

    def finish_stream(self, state: StreamState) -> list[Delta]:
        if state.finished:
            return []
        return [ErrorDelta(kind="server", message="Ollama stream ended before its final done line")]

    def decode_error(
        self, status: int, body: Any, headers: dict[str, str] | None = None
    ) -> ModelError:
        error = super().decode_error(status, body, headers)
        # A model that lacks a capability is reported as HTTP 400 "... does not support thinking"
        # or "... does not support tools"; callers treat that as a capability gap.
        if status == 400 and "does not support" in error.message:
            return ModelError(
                "unsupported",
                error.message,
                status=status,
                retry_after=error.retry_after,
                raw=body,
            )
        return error

    # ---- server side ----------------------------------------------------------------------

    def decode_request(
        self, body: Any, *, path: str = "", headers: dict[str, str] | None = None
    ) -> Request:
        if not isinstance(body, dict):
            raise ValueError("an Ollama chat request must be a JSON object")
        model = body.get("model")
        if not isinstance(model, str) or not model:
            raise ValueError("model is required")
        raw_messages = body.get("messages") or []
        if not isinstance(raw_messages, list):
            raise ValueError("messages must be a list")
        system, messages = self._decode_messages(raw_messages, model)
        tools = [_decode_tool(raw, index) for index, raw in enumerate(_list(body.get("tools")))]
        sampling, max_output_tokens, extensions = _decode_options(body.get("options"))
        effort: Effort | None = None
        think = body.get("think")
        if think is False:
            effort = "none"
        elif think is True:
            extensions["think"] = True
        elif isinstance(think, str):
            effort = _LEVEL_EFFORT.get(think)
            if effort is None:
                extensions["think"] = think
        elif think is not None:
            raise ValueError("think must be a boolean or a string")
        for key in PASSTHROUGH:
            if key in body:
                extensions[key] = body[key]
        stream = body.get("stream", True)
        if stream is None:
            stream = True
        if not isinstance(stream, bool):
            raise ValueError("stream must be a boolean")
        return Request(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            response_format=_decode_format(body.get("format")),
            max_output_tokens=max_output_tokens,
            sampling=sampling,
            effort=effort,
            stream=stream,
            extensions=extensions,
        )

    def encode_response(self, response: Response) -> Any:
        body: dict[str, Any] = {
            "model": response.model or "",
            "created_at": _iso(response.created),
            "message": _response_message(response.message),
            "done": True,
            "done_reason": _done_reason(response.finish_reason, response.raw_finish_reason),
        }
        body.update(_usage_fields(response.usage))
        return body

    def encode_stream(self, deltas: Iterable[Delta]) -> Iterator[StreamEvent]:
        model = ""
        created = _iso(None)
        pending: dict[int, tuple[str, str, list[str]]] = {}
        sent = 0
        usage: Usage | None = None
        finish: FinishDelta | None = None

        def line(message: dict[str, Any]) -> StreamEvent:
            return sse_json(
                {"model": model, "created_at": created, "message": message, "done": False}
            )

        def call_line(call: tuple[str, str, list[str]], index: int) -> StreamEvent:
            call_id, name, pieces = call
            wire = _wire_call(call_id, name, "".join(pieces) or "{}", index)
            return line({"role": "assistant", "content": "", "tool_calls": [wire]})

        for delta in deltas:
            if isinstance(delta, StartDelta):
                model = delta.model or model
            elif isinstance(delta, TextDelta):
                if delta.text:
                    yield line({"role": "assistant", "content": delta.text})
            elif isinstance(delta, ReasoningDelta):
                if delta.text:
                    yield line({"role": "assistant", "content": "", "thinking": delta.text})
            elif isinstance(delta, ToolCallStartDelta):
                pending[delta.index] = (delta.id, delta.name, [])
            elif isinstance(delta, ToolCallArgsDelta):
                pending.setdefault(delta.index, ("", "", []))[2].append(delta.delta)
            elif isinstance(delta, ToolCallEndDelta):
                if delta.index in pending:
                    yield call_line(pending.pop(delta.index), sent)
                    sent += 1
            elif isinstance(delta, UsageDelta):
                usage = merge_usage(usage, delta.usage)
            elif isinstance(delta, FinishDelta):
                finish = delta
            elif isinstance(delta, ErrorDelta):
                yield sse_json({"error": delta.message})
                return
        for index in sorted(pending):  # calls whose end never arrived
            yield call_line(pending[index], sent)
            sent += 1
        final: dict[str, Any] = {
            "model": model,
            "created_at": created,
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": _done_reason(
                finish.reason if finish else "stop", finish.raw if finish else None
            ),
        }
        final.update(_usage_fields(usage))
        yield sse_json(final)

    def encode_error(self, error: ModelError) -> tuple[int, Any]:
        status = error.status or _STATUS_FOR_KIND.get(error.kind, 500)
        return status, {"error": error.message}

    # ---- request encoding -----------------------------------------------------------------

    def _loss(
        self, report: ConversionReport, field: str, detail: str, action: str = "dropped"
    ) -> None:
        if self.options.strict:
            report.add(field, detail, "rejected")
            raise ConversionError(f"{self.format}: {field}: {detail}")
        report.add(field, detail, action)

    def _encode_messages(self, request: Request, report: ConversionReport) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        system = request.system_text()
        if system:
            out.append({"role": "system", "content": system})
        names: dict[str, str] = {}  # tool-call ID -> tool name, for results that carry no name
        for index, message in enumerate(request.messages):
            out.extend(self._encode_message(message, f"messages[{index}]", request, names, report))
        return out

    def _encode_message(
        self,
        message: Message,
        path: str,
        request: Request,
        names: dict[str, str],
        report: ConversionReport,
    ) -> list[dict[str, Any]]:
        texts: list[str] = []
        images: list[str] = []
        thinking: list[str] = []
        calls: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        for index, part in enumerate(message.content):
            where = f"{path}.content[{index}]"
            if isinstance(part, TextPart):
                texts.append(part.text)
            elif isinstance(part, ImagePart):
                image = self._encode_image(part, where, report)
                if image is not None:
                    images.append(image)
            elif isinstance(part, ToolCallPart):
                names[part.id] = part.name
                arguments, valid = _arguments_object(part.arguments)
                if not valid:
                    self._loss(
                        report,
                        f"{where}.arguments",
                        'arguments are not a JSON object; sent as {"input": ...}',
                        "approximated",
                    )
                calls.append(_wire_call(part.id, part.name, arguments, len(calls)))
            elif isinstance(part, ToolResultPart):
                results.append(self._encode_result(part, names, message.name, where, report))
            elif isinstance(part, ReasoningPart):
                text = self._reasoning_text(part, message.role, request.model, where, report)
                if text:
                    thinking.append(text)
            elif isinstance(part, RefusalPart):
                texts.append(part.text)
                self._loss(
                    report, where, "Ollama has no refusal field; sent as text", "approximated"
                )
            elif isinstance(part, FilePart):
                self._loss(report, where, "Ollama messages cannot carry documents")
        if message.name and message.role != "tool":
            self._loss(report, f"{path}.name", "Ollama messages have no participant name")
        if message.metadata:
            self._loss(report, f"{path}.metadata", "Ollama messages carry no metadata")
        if message.role == "tool":
            if texts or images:  # loose content in a tool message becomes one more tool message
                extra: dict[str, Any] = {"role": "tool", "content": "".join(texts)}
                if message.name:
                    extra["tool_name"] = message.name
                if images:
                    extra["images"] = images
                results.append(extra)
            return results
        if results and not (texts or images or thinking or calls):
            return results
        entry: dict[str, Any] = {"role": message.role, "content": "".join(texts)}
        if thinking:
            entry["thinking"] = "".join(thinking)
        if images:
            entry["images"] = images
        if calls:
            entry["tool_calls"] = calls
        return [*results, entry]

    def _encode_result(
        self,
        part: ToolResultPart,
        names: dict[str, str],
        fallback_name: str | None,
        where: str,
        report: ConversionReport,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {"role": "tool", "content": part.text()}
        name = part.name or names.get(part.call_id) or fallback_name
        if name:
            entry["tool_name"] = name
        entry["tool_call_id"] = part.call_id
        images: list[str] = []
        for index, item in enumerate(part.content):
            if isinstance(item, ImagePart):
                image = self._encode_image(item, f"{where}.content[{index}]", report)
                if image is not None:
                    images.append(image)
        if images:
            entry["images"] = images
        if part.is_error:
            self._loss(report, f"{where}.is_error", "Ollama tool results have no error flag")
        return entry

    def _encode_image(self, part: ImagePart, where: str, report: ConversionReport) -> str | None:
        if part.detail:
            self._loss(report, f"{where}.detail", "Ollama has no image detail setting")
        payload: str | None = None
        if part.data:
            payload = _data_uri_payload(part.data) if part.data.startswith("data:") else part.data
        elif part.url and part.url.startswith("data:"):
            payload = _data_uri_payload(part.url)
        elif part.url:
            self._loss(
                report, where, "Ollama takes inline base64 images only; image URLs are not fetched"
            )
            return None
        if payload is None:
            self._loss(report, where, "the image has no base64 data")
        return payload

    def _reasoning_text(
        self,
        part: ReasoningPart,
        role: str,
        model: str,
        where: str,
        report: ConversionReport,
    ) -> str | None:
        if part.opaque:
            self._loss(
                report, f"{where}.opaque", "Ollama cannot carry signed or encrypted reasoning"
            )
        if part.summary:
            self._loss(report, f"{where}.summary", "Ollama has no reasoning summary field")
        if not part.text:
            return None
        if role != "assistant":
            self._loss(report, where, "Ollama carries reasoning on assistant messages only")
            return None
        origin = part.origin.model if part.origin is not None else None
        if origin is not None and origin != model:
            self._loss(report, where, f"reasoning from {origin!r} is never sent to another model")
            return None
        return part.text

    def _encode_tools(self, request: Request, report: ConversionReport) -> list[dict[str, Any]]:
        if not request.tools:
            return []
        choice = request.tool_choice
        if choice == "none":
            self._loss(
                report,
                "tool_choice",
                "Ollama has no tool_choice; tools are left out so the model cannot call them",
                "approximated",
            )
            return []
        if choice == "required" or isinstance(choice, NamedToolChoice):
            self._loss(
                report, "tool_choice", "Ollama has no tool_choice; the model may answer in text"
            )
        if request.parallel_tool_calls is False:
            self._loss(report, "parallel_tool_calls", "Ollama cannot switch parallel calls off")
        out: list[dict[str, Any]] = []
        for index, tool in enumerate(request.tools):
            function: dict[str, Any] = {"name": tool.name}
            if tool.description is not None:
                function["description"] = tool.description
            function["parameters"] = tool.parameters
            if tool.strict:
                self._loss(report, f"tools[{index}].strict", "Ollama does not enforce tool schemas")
            out.append({"type": "function", "function": function})
        return out

    def _encode_format(
        self, response_format: ResponseFormat | None, report: ConversionReport
    ) -> str | dict[str, Any] | None:
        if response_format is None or response_format.type == "text":
            return None
        if response_format.type == "json_object":
            return "json"
        if response_format.json_schema is None:
            self._loss(
                report,
                "response_format.json_schema",
                'no schema given; sent as "json"',
                "approximated",
            )
            return "json"
        # The schema name and strict flag have no field; the schema itself is always enforced.
        return response_format.json_schema

    def _encode_think(self, request: Request, report: ConversionReport) -> bool | str | None:
        if request.budget_tokens is not None:
            self._loss(report, "budget_tokens", "Ollama has no thinking budget")
        if "think" in request.extensions:  # an explicit `think` wins over the effort mapping
            value = request.extensions["think"]
            if value is None or isinstance(value, bool | str):
                return value
            self._loss(report, "extensions.think", "think must be a boolean or a level name")
            return None
        if request.effort is None:
            return None
        return self._think_for(request.effort, report)

    def _think_for(self, effort: Effort, report: ConversionReport) -> bool | str | None:
        """`think` for an IR effort, clamped to `options.extra["think_values"]` when probed.

        `think_values` mirrors `thinking.values` from `/api/show`: booleans for an on/off switch,
        names for levels, and `[False]` for a model that does not think.
        """
        probed = self.options.extra.get("think_values")
        if isinstance(probed, list | tuple):
            switches = [value for value in probed if isinstance(value, bool)]
            levels = [value for value in probed if isinstance(value, str)]
            can_disable = False in switches
            thinks = True in switches or bool(levels)
        else:
            levels, can_disable, thinks = list(THINK_LEVELS), True, True
        if effort == "none":
            if thinks and not can_disable:
                self._loss(
                    report,
                    "effort",
                    "the model cannot switch thinking off; think=false sent anyway",
                    "approximated",
                )
            return False
        if not thinks:
            self._loss(report, "effort", "the model does not think; no think value sent")
            return None
        known = [level for level in levels if level in _RANK]
        if not known:
            self._loss(
                report,
                "effort",
                f"no known think levels; effort {effort!r} sent as true",
                "approximated",
            )
            return True
        target = EFFORT_THINK[effort]
        choice = target
        if target not in known:  # nearest supported level; ties go to the higher one
            choice = min(
                known, key=lambda level: (abs(_RANK[level] - _RANK[target]), -_RANK[level])
            )
        if choice != effort:
            self._loss(
                report, "effort", f"effort {effort!r} sent as think={choice!r}", "approximated"
            )
        return choice

    # ---- request decoding -----------------------------------------------------------------

    def _decode_messages(
        self, raw_messages: list[Any], model: str
    ) -> tuple[list[TextPart], list[Message]]:
        system: list[TextPart] = []
        messages: list[Message] = []
        pending: list[ToolCallPart] = []  # calls of the last assistant message without results
        turn = 0
        orphans = 0
        for index, raw in enumerate(raw_messages):
            if not isinstance(raw, dict):
                raise ValueError(f"messages[{index}] must be an object")
            role = _text(raw.get("role")).lower()
            content = raw.get("content") or ""
            if not isinstance(content, str):
                raise ValueError(f"messages[{index}].content must be a string")
            images = _images(raw.get("images"))
            if role == "system" and not messages and not images:
                if content:
                    system.append(TextPart(text=content))
                continue
            parts: list[Part] = []
            if role in ("system", "user"):
                parts.extend(_text_parts(content))
                parts.extend(images)
                messages.append(
                    Message(role="system" if role == "system" else "user", content=parts)
                )
            elif role == "assistant":
                thinking = _text(raw.get("thinking"))
                if thinking:
                    parts.append(
                        ReasoningPart(text=thinking, origin=Origin(format=self.format, model=model))
                    )
                parts.extend(_text_parts(content))
                parts.extend(images)
                calls = [
                    _decode_call(call, turn, position)
                    for position, call in enumerate(_list(raw.get("tool_calls")))
                ]
                parts.extend(calls)
                pending = list(calls)
                turn += 1
                messages.append(Message(role="assistant", content=parts))
            elif role == "tool":
                name = _text(raw.get("tool_name")) or None
                call_id = _text(raw.get("tool_call_id")) or None
                matched = _take_pending(pending, call_id, name)
                if call_id is None:
                    if matched is not None:
                        call_id = matched.id
                    else:  # a result that answers no call: give it an ID no call can have
                        call_id = f"{synthetic_call_id(turn, orphans)}_unmatched"
                        orphans += 1
                if name is None and matched is not None:
                    name = matched.name
                result_content: list[TextPart | ImagePart] = [*_text_parts(content), *images]
                result = ToolResultPart(call_id=call_id, name=name, content=result_content)
                messages.append(Message(role="tool", content=[result]))
            else:
                raise ValueError(f"messages[{index}]: unknown role {role!r}")
        return system, messages


# ---- module helpers ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text_parts(text: str) -> list[TextPart]:
    return [TextPart(text=text)] if text else []


def _arguments_object(arguments: str) -> tuple[dict[str, Any], bool]:
    """Tool-call arguments as the object Ollama expects, and whether they were a JSON object."""
    try:
        value = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {"input": arguments}, False
    if isinstance(value, dict):
        return value, True
    return {"input": value}, False


def _arguments_json(value: Any) -> str:
    """Wire arguments (an object, or a string from some clients) as the IR's JSON string."""
    if value is None:
        return "{}"
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError:
            return json.dumps({"input": value}, ensure_ascii=False)
        if isinstance(parsed, dict):
            return value or "{}"
        value = {"input": parsed}
    elif not isinstance(value, dict):
        value = {"input": value}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _wire_call(
    call_id: str, name: str, arguments: dict[str, Any] | str, index: int
) -> dict[str, Any]:
    if isinstance(arguments, str):
        arguments = _arguments_object(arguments)[0]
    call: dict[str, Any] = {"id": call_id} if call_id else {}
    call["function"] = {"index": index, "name": name, "arguments": arguments}
    return call


def _decode_call(raw: Any, turn: int, index: int) -> ToolCallPart:
    function = raw.get("function") if isinstance(raw, dict) else None
    if not isinstance(function, dict):
        raise ValueError("tool call without a function object")
    name = _text(function.get("name"))
    if not name:
        raise ValueError("tool call without a function name")
    arguments = _arguments_json(function.get("arguments"))
    call_id = _text(raw.get("id"))
    if call_id:
        return ToolCallPart(id=call_id, name=name, arguments=arguments)
    return ToolCallPart(
        id=synthetic_call_id(turn, index), name=name, arguments=arguments, synthetic=True
    )


def _take_pending(
    pending: list[ToolCallPart], call_id: str | None, name: str | None
) -> ToolCallPart | None:
    """The call a tool result answers: by ID when given, else the first open call by name."""
    for index, call in enumerate(pending):
        if call_id is not None:
            if call.id == call_id:
                return pending.pop(index)
        elif name is None or call.name == name:
            return pending.pop(index)
    return None


def _decode_tool(raw: Any, index: int) -> ToolSpec:
    function = raw.get("function") if isinstance(raw, dict) else None
    if not isinstance(function, dict) or not _text(function.get("name")):
        raise ValueError(f"tools[{index}] needs function.name")
    spec: dict[str, Any] = {"name": function["name"]}
    description = function.get("description")
    if isinstance(description, str):
        spec["description"] = description
    parameters = function.get("parameters")
    if isinstance(parameters, dict):
        spec["parameters"] = parameters
    return ToolSpec.model_validate(spec)


def _decode_options(options: Any) -> tuple[Sampling, int | None, dict[str, Any]]:
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    rest = dict(options)
    fields: dict[str, Any] = {key: rest.pop(key) for key in SAMPLING_OPTIONS if key in rest}
    stop = rest.pop("stop", None)
    if isinstance(stop, str):
        stop = [stop]
    if stop:
        fields["stop"] = list(stop)
    max_output_tokens: int | None = None
    raw_predict = rest.pop("num_predict", None)
    num_predict = _count(raw_predict)
    if num_predict is not None and num_predict >= 0:
        max_output_tokens = num_predict
    elif raw_predict is not None:  # -1 (no limit) and -2 (fill the context) stay native
        rest["num_predict"] = raw_predict
    extensions: dict[str, Any] = {}
    if "num_ctx" in rest:
        extensions["num_ctx"] = rest.pop("num_ctx")
    if rest:
        extensions["options"] = rest
    return Sampling.model_validate(fields), max_output_tokens, extensions


def _decode_format(value: Any) -> ResponseFormat | None:
    if value is None or value == "":
        return None
    if value == "json":
        return ResponseFormat(type="json_object")
    if isinstance(value, dict):
        return ResponseFormat(type="json_schema", json_schema=value)
    raise ValueError('format must be "json" or a JSON Schema object')


def _encode_options(request: Request) -> dict[str, Any]:
    options: dict[str, Any] = {}
    extra = request.extensions.get("options")
    if isinstance(extra, dict):
        options.update(extra)
    for key in SAMPLING_OPTIONS:
        value = getattr(request.sampling, key)
        if value is not None:
            options[key] = value
    if request.sampling.stop:
        options["stop"] = list(request.sampling.stop)
    if request.max_output_tokens is not None:
        options["num_predict"] = request.max_output_tokens
    if request.extensions.get("num_ctx") is not None:
        options["num_ctx"] = request.extensions["num_ctx"]
    return options


def _finish(raw: str | None, has_calls: bool) -> FinishReason:
    if raw in (None, "stop"):
        return "tool_calls" if has_calls else "stop"
    if raw == "length":
        return "length"
    return "other"  # "load" and "unload" answer requests with no messages


def _done_reason(reason: FinishReason, raw: str | None) -> str:
    if reason in ("stop", "tool_calls"):
        return "stop"  # Ollama reports tool calls with "stop"
    if reason == "length":
        return "length"
    if raw in ("load", "unload"):
        return raw
    return reason


def _usage(body: dict[str, Any]) -> Usage | None:
    raw = {key: body[key] for key in USAGE_FIELDS if key in body}
    if "prompt_eval_count" not in raw and "eval_count" not in raw:
        return None
    # Zero counts are omitted on the wire, so a missing count with the other one present is 0.
    return Usage(
        input_tokens=_count(raw.get("prompt_eval_count", 0)),
        output_tokens=_count(raw.get("eval_count", 0)),
        cached_input_tokens=_count(raw.get("prompt_eval_cached_count")),
        raw=raw,
    )


def _usage_fields(usage: Usage | None) -> dict[str, Any]:
    if usage is None:
        return {}
    raw = usage.raw or {}
    out: dict[str, Any] = {}
    for key in USAGE_FIELDS:
        if key == "prompt_eval_count":
            out[key] = usage.input_tokens or 0
        elif key == "prompt_eval_cached_count":
            if usage.cached_input_tokens is not None:
                out[key] = usage.cached_input_tokens
        elif key == "eval_count":
            out[key] = usage.output_tokens or 0
        elif _count(raw.get(key)) is not None:
            out[key] = raw[key]  # durations, only when the usage came from Ollama
    return out


def _response_message(message: Message) -> dict[str, Any]:
    texts: list[str] = []
    thinking: list[str] = []
    images: list[str] = []
    calls: list[dict[str, Any]] = []
    for part in message.content:
        if isinstance(part, TextPart | RefusalPart):
            texts.append(part.text)
        elif isinstance(part, ReasoningPart) and part.text:
            thinking.append(part.text)
        elif isinstance(part, ImagePart) and part.data:
            images.append(part.data)
        elif isinstance(part, ToolCallPart):
            calls.append(_wire_call(part.id, part.name, part.arguments, len(calls)))
    out: dict[str, Any] = {"role": "assistant", "content": "".join(texts)}
    if thinking:
        out["thinking"] = "".join(thinking)
    if images:
        out["images"] = images
    if calls:
        out["tool_calls"] = calls
    return out


def _images(value: Any) -> list[ImagePart]:
    return [
        ImagePart(data=item, media_type=_media_type(item))
        for item in _list(value)
        if isinstance(item, str) and item
    ]


_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
)


def _media_type(data: str) -> str | None:
    """Media type of a base64 image from its first bytes (Ollama sends no media type)."""
    try:
        head = base64.b64decode(data[:24], validate=False)
    except (binascii.Error, ValueError):
        return None
    for magic, media_type in _MAGIC:
        if head.startswith(magic):
            return media_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _data_uri_payload(uri: str) -> str | None:
    header, sep, payload = uri.partition(",")
    if not sep or not header.endswith(";base64"):
        return None
    return payload


def _parse_time(value: Any) -> int | None:
    """Epoch seconds from Ollama's RFC 3339 timestamps (nanosecond fractions, "Z" suffix)."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp())


def _iso(epoch: int | None) -> str:
    moment = datetime.fromtimestamp(epoch, UTC) if epoch is not None else datetime.now(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


__all__ = ["EFFORT_THINK", "THINK_LEVELS", "OllamaCodec"]
