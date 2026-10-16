"""chat-completions codec: `POST /v1/chat/completions`, client and server side (ADR 0003).

Requests: `Request.system` becomes one leading `system` message (`developer` messages are read
as `system`). Content is a string or a list of `text`, `image_url` (an http(s) or `data:` URI)
and `file` (`file_data` as a `data:` URI, or `file_id`) parts. Assistant tool calls are
`{id, type: "function", function: {name, arguments}}`; every tool result is a `tool` message
with `tool_call_id`. The output-token limit uses `CodecOptions.max_tokens_field`.

Responses and streams: reasoning text arrives as `reasoning_content` or `reasoning` (open
engines use both names) on the message or on the stream delta. Streamed tool calls are keyed by
`index`; the first fragment of a call carries its id and name, later fragments carry argument
text. Missing ids are synthesized with `synthetic_call_id`. `prompt_tokens` already includes
cached tokens, as the IR requires. With `stream_options.include_usage` the usage arrives in a
last chunk whose `choices` is empty, before `data: [DONE]`.

`CodecOptions.extra` keys, filled from the endpoint's capability profile:

* `effort_levels`: the `reasoning_effort` values the endpoint accepts (default: every level of
  the reference). Other levels are clamped to the nearest accepted one and reported.
* `accepts_top_k`: send `sampling.top_k`. Several engines accept it; the reference has no such
  field, so without this key it is reported as dropped.
* `reasoning_field`: the endpoint's reasoning field (`reasoning_content` or `reasoning`). With
  it, prior reasoning text from the same model is sent back on assistant messages; without it,
  that text is reported as dropped. Server side it names the output field (default
  `reasoning_content`).

`Request.extensions["chat-completions"]` holds extra body fields sent verbatim; `decode_request`
stores the fields it does not map there. Extensions for other formats are reported as dropped.

`CodecOptions.tool_parser` names a catalog syntax (`stepswitch.compat.toolcall.parsers`) for a
model whose server does not parse its tool calls (SPEC §4.4: "otherwise parse raw text";
ADR 0022). Client side, when a request offers tools and the reply carries no `tool_calls`, the
calls are parsed out of the reply text (streams buffer a possible call section until the end),
and earlier assistant calls are sent back written in that syntax inside `content`, because
templates for such models may not render a `tool_calls` field.

Synthetic tool-call ids use the turn of the request this codec encoded last (the number of
assistant messages in its history), so a codec instance serves one conversation at a time.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from stepswitch.compat.codecs.base import Codec, CodecOptions, Framing, HttpCall, StreamState
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
    RefusalDelta,
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
from stepswitch.compat.toolcall import parsers
from stepswitch.compat.toolcall.streaming import StreamingToolCallExtractor, extract_calls

PATH = "/v1/chat/completions"
FORMAT = "chat-completions"

# Reasoning text field names used by open engines, in lookup order.
REASONING_FIELDS = ("reasoning_content", "reasoning")
DEFAULT_REASONING_FIELD = "reasoning_content"

# --------------------------------------------------------------------------------------------
# Helpers shared with the text-completion codec (same usage, error and finish vocabulary)
# --------------------------------------------------------------------------------------------


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


def assistant_turns(request: Request) -> int:
    """Turn index of the answer to `request`: the assistant messages already in its history."""
    return sum(1 for message in request.messages if message.role == "assistant")


def report_loss(
    codec: Codec, report: ConversionReport, where: str, detail: str, action: str = "dropped"
) -> None:
    """Record a loss, or raise in strict mode (SPEC §4.1: nothing is dropped silently)."""
    if codec.options.strict:
        raise ConversionError(f"{codec.format}: {where}: {detail}")
    report.add(where, detail, action)


def decode_usage(raw: Any, timings: Any = None) -> Usage | None:
    """Wire usage -> IR usage. `prompt_tokens` includes cached tokens already.

    `timings` is the llama.cpp server's top-level `timings` object (prompt_n, prompt_ms,
    predicted_n, predicted_ms, cache_n), kept in `Usage.raw["timings"]` so throughput can be
    taken from server-side figures.
    """
    if not isinstance(raw, dict):
        return None
    prompt = as_dict(raw.get("prompt_tokens_details"))
    completion = as_dict(raw.get("completion_tokens_details"))
    if isinstance(timings, dict) and "timings" not in raw:
        raw = {**raw, "timings": timings}
    return Usage(
        input_tokens=as_int(raw.get("prompt_tokens")),
        output_tokens=as_int(raw.get("completion_tokens")),
        cached_input_tokens=as_int(prompt.get("cached_tokens")),
        cache_write_tokens=as_int(prompt.get("cache_write_tokens")),
        reasoning_tokens=as_int(completion.get("reasoning_tokens")),
        raw=raw,
    )


def encode_usage(usage: Usage) -> dict[str, Any]:
    prompt = usage.input_tokens or 0
    completion = usage.output_tokens or 0
    out: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    details: dict[str, int] = {}
    if usage.cached_input_tokens is not None:
        details["cached_tokens"] = usage.cached_input_tokens
    if usage.cache_write_tokens is not None:
        details["cache_write_tokens"] = usage.cache_write_tokens
    if details:
        out["prompt_tokens_details"] = details
    if usage.reasoning_tokens is not None:
        out["completion_tokens_details"] = {"reasoning_tokens": usage.reasoning_tokens}
    return out


FINISH_FROM_WIRE: dict[str, FinishReason] = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_calls",
    "content_filter": "content_filter",
    "function_call": "tool_calls",  # deprecated value of the reference
    "error": "error",
}
FINISH_TO_WIRE: dict[str, str] = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_calls",
    "content_filter": "content_filter",
}


def finish_from_wire(raw: Any, has_tool_calls: bool) -> tuple[FinishReason, str | None]:
    """Normalized finish reason plus the raw value. Unknown values become `other`."""
    raw_reason = raw if isinstance(raw, str) and raw else None
    reason: FinishReason = "other"
    if raw_reason is not None:
        reason = FINISH_FROM_WIRE.get(raw_reason, "other")
    # Some servers report `stop` (or nothing) for a turn that ends in tool calls.
    if has_tool_calls and (reason == "stop" or raw_reason is None):
        reason = "tool_calls"
    return reason, raw_reason


# Error `type` labels written by the server side, and read back for stream errors.
ERROR_TYPES: dict[str, str] = {
    "bad_request": "invalid_request_error",
    "authentication": "authentication_error",
    "permission": "permission_error",
    "not_found": "not_found_error",
    "too_large": "invalid_request_error",
    "rate_limit": "rate_limit_error",
    "overloaded": "service_unavailable_error",
    "server": "server_error",
    "timeout": "timeout_error",
    "connection": "server_error",
    "blocked": "permission_error",
    "unsupported": "invalid_request_error",
    "invalid_response": "server_error",
}
KIND_STATUS: dict[str, int] = {
    "bad_request": 400,
    "authentication": 401,
    "permission": 403,
    "not_found": 404,
    "too_large": 413,
    "rate_limit": 429,
    "overloaded": 503,
    "server": 500,
    "timeout": 504,
    "connection": 502,
    "blocked": 403,
    "unsupported": 400,
    "invalid_response": 502,
}
_TYPE_KINDS: dict[str, ErrorKind] = {
    "invalid_request_error": "bad_request",
    "authentication_error": "authentication",
    "permission_error": "permission",
    "not_found_error": "not_found",
    "rate_limit_error": "rate_limit",
    "server_error": "server",
    "api_error": "server",
    "service_unavailable_error": "overloaded",
    "unavailable_error": "overloaded",
    "overloaded_error": "overloaded",
    "timeout_error": "timeout",
    "not_supported_error": "unsupported",
}


def error_details(body: Any) -> tuple[str | None, str | None]:
    """Message and code from `{"error": {...}}`, `{"error": "..."}`, a bare error object or text.

    The code is the body's `code` when it is a string, else its `type` (one engine puts the
    HTTP status into `code`).
    """
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, str):
            return (error.strip() or None), None
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or error.get("msg")
            code = as_str(error.get("code")) or as_str(error.get("type")) or None
            return (str(message) if message else None), code
        return None, None
    if isinstance(body, str) and body.strip():
        return body.strip()[:500], None
    return None, None


def header(headers: Mapping[str, str] | None, name: str) -> str | None:
    """Case-insensitive header lookup."""
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


# A prompt longer than the context: llama.cpp says `exceed_context_size_error`, other servers
# use the code `context_length_exceeded` or only a message.
CONTEXT_CODES = frozenset({"exceed_context_size_error", "context_length_exceeded"})
CONTEXT_MESSAGE = re.compile(
    r"context (?:length|size|window)|maximum context|exceeds the available context", re.I
)


def is_context_overflow(status: int, message: str | None, code: str | None) -> bool:
    if code in CONTEXT_CODES:
        return True
    return status in (400, 413) and bool(message and CONTEXT_MESSAGE.search(message))


def error_from_http(status: int, body: Any, headers: Mapping[str, str] | None) -> ModelError:
    message, code = error_details(body)
    overflow = is_context_overflow(status, message, code)
    return ModelError(
        "too_large" if overflow else kind_for_status(status),
        message or f"HTTP {status}",
        status=status,
        code=code,
        retry_after=parse_retry_after(header(headers, "retry-after")),
        raw=body,
    )


def error_delta(body: Any) -> ErrorDelta:
    """An error object sent inside a stream (`data: {"error": ...}`)."""
    message, _code = error_details(body)
    error = as_dict(as_dict(body).get("error"))
    status = as_int(error.get("code"))
    if status is None or not 400 <= status < 600:
        status = None
    error_type = as_str(error.get("type")) or ""
    kind: str = (
        kind_for_status(status) if status is not None else _TYPE_KINDS.get(error_type, "server")
    )
    return ErrorDelta(kind=kind, message=message or "error in stream", status=status)


def encode_error_body(error: ModelError) -> tuple[int, dict[str, Any]]:
    status = error.status or KIND_STATUS.get(error.kind, 500)
    return status, {
        "error": {
            "message": error.message,
            "type": ERROR_TYPES.get(error.kind, "server_error"),
            "param": None,
            "code": error.code,
        }
    }


def stream_error_event(delta: ErrorDelta) -> StreamEvent:
    return sse_json(
        {
            "error": {
                "message": delta.message,
                "type": ERROR_TYPES.get(delta.kind, "server_error"),
                "param": None,
                "code": delta.status,
            }
        }
    )


def response_error(body: Any) -> ModelError:
    """A 200 body that carries an error object instead of a completion."""
    error = as_dict(as_dict(body).get("error"))
    status = as_int(error.get("code"))
    if status is None or not 400 <= status < 600:
        status = 500
    return error_from_http(status, body, None)


def check_body(body: Any, fmt: str) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ModelError("invalid_response", f"{fmt} response is not a JSON object")
    if body.get("error") is not None and not body.get("choices"):
        raise response_error(body)
    return body


def first_choice(body: dict[str, Any], fmt: str) -> dict[str, Any]:
    choices = [as_dict(choice) for choice in as_list(body.get("choices"))]
    for choice in choices:
        if as_int(choice.get("index")) in (None, 0):
            return choice
    raise ModelError("invalid_response", f"{fmt} response has no choices", raw=body)


def split_data_uri(url: str) -> tuple[str | None, str] | None:
    """`(media_type, base64 data)` for a base64 `data:` URI, else None."""
    if not url.startswith("data:"):
        return None
    head, sep, data = url[5:].partition(",")
    if not sep or not head.endswith(";base64"):
        return None
    return (head[: -len(";base64")] or None), data


def reasoning_text(obj: dict[str, Any]) -> str | None:
    for key in REASONING_FIELDS:
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return None


_EFFORTS: dict[str, Effort] = {level: level for level in EFFORT_LEVELS}


def clamp_effort(effort: Effort, levels: Sequence[Effort]) -> Effort:
    """The nearest accepted level; ties go to the higher level."""
    if effort in levels:
        return effort
    position = EFFORT_LEVELS.index(effort)
    return min(
        levels,
        key=lambda level: (abs(EFFORT_LEVELS.index(level) - position), -EFFORT_LEVELS.index(level)),
    )


def stop_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [item for item in as_list(value) if isinstance(item, str)]


def apply_extensions(
    codec: Codec, request: Request, body: dict[str, Any], report: ConversionReport
) -> None:
    """Merge this format's extension fields; report extensions meant for other formats."""
    for fmt, value in request.extensions.items():
        if fmt != codec.format:
            report_loss(codec, report, f"extensions.{fmt}", f"fields for {fmt} are not sent")
            continue
        for key, item in as_dict(value).items():
            if key not in body:
                body[key] = item
            elif body[key] != item:
                report_loss(
                    codec, report, f"extensions.{fmt}.{key}", "the mapped request field is sent"
                )


# --------------------------------------------------------------------------------------------
# Small wire builders
# --------------------------------------------------------------------------------------------


def _text_content(texts: list[str]) -> str | list[dict[str, Any]]:
    """One text: a plain string. Several: a list of text parts, so part boundaries survive."""
    if len(texts) == 1:
        return texts[0]
    if not texts:
        return ""
    return [{"type": "text", "text": text} for text in texts]


def _encode_tool(tool: ToolSpec) -> dict[str, Any]:
    function: dict[str, Any] = {"name": tool.name}
    if tool.description is not None:
        function["description"] = tool.description
    function["parameters"] = tool.parameters
    if tool.strict is not None:
        function["strict"] = tool.strict
    return {"type": "function", "function": function}


def _encode_tool_choice(choice: ToolChoice) -> Any:
    if isinstance(choice, NamedToolChoice):
        return {"type": "function", "function": {"name": choice.name}}
    return choice


def _wire_call(call: ToolCallPart) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments or "{}"},
    }


@dataclass
class _StreamCall:
    """A tool call being assembled from stream fragments."""

    index: int
    id: str
    synthetic: bool
    name: str | None = None
    started: bool = False
    ended: bool = False
    pending: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------
# Codec
# --------------------------------------------------------------------------------------------


class ChatCompletionsCodec(Codec):
    key: ClassVar[str] = "chat"
    format: ClassVar[str] = FORMAT
    framing: ClassVar[Framing] = "sse"

    _KNOWN_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "response_format",
            "max_tokens",
            "max_completion_tokens",
            "temperature",
            "top_p",
            "top_k",
            "seed",
            "stop",
            "presence_penalty",
            "frequency_penalty",
            "reasoning_effort",
            "reasoning",
            "stream",
            "stream_options",
            "n",
        }
    )

    def __init__(self, options: CodecOptions | None = None) -> None:
        super().__init__(options)
        syntax = self.options.tool_parser
        if syntax is not None and syntax not in parsers.PARSERS:
            known = ", ".join(parsers.PARSERS)
            raise ValueError(f"unknown tool parser {syntax!r}; known: {known}")
        self._turn = 0
        self._tools_offered = False

    def _lose(
        self, report: ConversionReport, where: str, detail: str, action: str = "dropped"
    ) -> None:
        report_loss(self, report, where, detail, action)

    def _reasoning_field(self) -> str | None:
        value = self.options.extra.get("reasoning_field")
        return value if isinstance(value, str) and value else None

    def _text_parser(self) -> str | None:
        """The syntax to parse calls out of reply text with, for a request that offered tools."""
        return self.options.tool_parser if self._tools_offered else None

    # ---- client side: requests ---------------------------------------------------------

    def encode_request(self, request: Request, report: ConversionReport) -> HttpCall:
        self._turn = assistant_turns(request)
        self._tools_offered = bool(request.tools)
        body: dict[str, Any] = {
            "model": request.model,
            "messages": self._encode_messages(request, report),
        }
        if request.prompt is not None:
            self._lose(report, "prompt", "chat-completions sends messages; the prompt is unused")
        if request.tools:
            body["tools"] = [_encode_tool(tool) for tool in request.tools]
        choice = request.tool_choice
        if choice is not None:
            if request.tools:
                body["tool_choice"] = _encode_tool_choice(choice)
            elif choice not in ("auto", "none"):
                self._lose(report, "tool_choice", "not sent: the request has no tools")
        if request.parallel_tool_calls is not None and request.tools:
            body["parallel_tool_calls"] = request.parallel_tool_calls
        if request.response_format is not None:
            body["response_format"] = self._encode_response_format(request.response_format, report)
        if request.max_output_tokens is not None:
            body[self.options.max_tokens_field] = request.max_output_tokens
        self._encode_sampling(request.sampling, body, report)
        self._encode_effort(request, body, report)
        if request.budget_tokens is not None:
            self._lose(report, "budget_tokens", "chat-completions has no reasoning token budget")
        apply_extensions(self, request, body, report)
        if request.stream:
            body["stream"] = True
            if self.options.stream_usage:
                body["stream_options"] = {"include_usage": True}
        return HttpCall(method="POST", path=PATH, json=body, stream=request.stream)

    def _encode_sampling(
        self, sampling: Sampling, body: dict[str, Any], report: ConversionReport
    ) -> None:
        for name in ("temperature", "top_p", "seed", "presence_penalty", "frequency_penalty"):
            value = getattr(sampling, name)
            if value is not None:
                body[name] = value
        if sampling.stop:
            body["stop"] = list(sampling.stop)
        if sampling.top_k is not None:
            if self.options.extra.get("accepts_top_k"):
                body["top_k"] = sampling.top_k
            else:
                self._lose(
                    report, "sampling.top_k", "top_k is not part of the chat-completions format"
                )

    def _encode_effort(
        self, request: Request, body: dict[str, Any], report: ConversionReport
    ) -> None:
        if request.effort is None:
            return
        configured = self.options.extra.get("effort_levels")
        levels: tuple[Effort, ...] = (
            EFFORT_LEVELS
            if configured is None
            else tuple(level for level in EFFORT_LEVELS if level in configured)
        )
        if not levels:
            self._lose(report, "effort", "this endpoint accepts no reasoning_effort")
            return
        effort = clamp_effort(request.effort, levels)
        if effort != request.effort:
            self._lose(
                report,
                "effort",
                f"{request.effort!r} is not accepted by this endpoint; sent {effort!r}",
                "approximated",
            )
        body["reasoning_effort"] = effort

    def _encode_response_format(
        self, response_format: ResponseFormat, report: ConversionReport
    ) -> dict[str, Any]:
        if response_format.type != "json_schema":
            return {"type": response_format.type}
        name = response_format.name
        if not name:
            name = "response"
            self._lose(
                report,
                "response_format.name",
                "a schema name is required; sent 'response'",
                "approximated",
            )
        schema: dict[str, Any] = {"name": name, "schema": response_format.json_schema or {}}
        if response_format.json_schema is None:
            self._lose(
                report, "response_format.json_schema", "no schema given; sent {}", "approximated"
            )
        if response_format.strict is not None:
            schema["strict"] = response_format.strict
        return {"type": "json_schema", "json_schema": schema}

    def _encode_messages(self, request: Request, report: ConversionReport) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if request.system:
            out.append(
                {"role": "system", "content": _text_content([p.text for p in request.system])}
            )
        names: dict[str, str] = {}  # call id -> tool name, to check tool results
        for i, message in enumerate(request.messages):
            where = f"messages.{i}"
            if message.metadata:
                self._lose(report, f"{where}.metadata", f"no field for {sorted(message.metadata)}")
            if message.role == "system":
                out.append(self._system_message(message, where, report))
            elif message.role == "user":
                out.extend(self._user_messages(message, where, report, names))
            elif message.role == "assistant":
                out.append(self._assistant_message(message, where, report, names, request.model))
            else:
                out.extend(self._tool_messages(message, where, report, names))
        return out

    def _system_message(
        self, message: Message, where: str, report: ConversionReport
    ) -> dict[str, Any]:
        texts: list[str] = []
        for j, part in enumerate(message.content):
            if isinstance(part, TextPart):
                texts.append(part.text)
            else:
                self._lose(
                    report, f"{where}.content.{j}", f"system messages carry text, not {part.type}"
                )
        out: dict[str, Any] = {"role": "system", "content": _text_content(texts)}
        if message.name:
            out["name"] = message.name
        return out

    def _user_messages(
        self, message: Message, where: str, report: ConversionReport, names: dict[str, str]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        parts: list[dict[str, Any]] = []
        for j, part in enumerate(message.content):
            at = f"{where}.content.{j}"
            if isinstance(part, TextPart):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                image = self._encode_image(part, at, report)
                if image is not None:
                    parts.append(image)
            elif isinstance(part, FilePart):
                file = self._encode_file(part, at, report)
                if file is not None:
                    parts.append(file)
            elif isinstance(part, ToolResultPart):
                if parts:
                    self._lose(
                        report, at, "sent as a tool message before the user content", "approximated"
                    )
                results.append(self._tool_result(part, at, report, names))
            else:
                self._lose(report, at, f"user messages cannot carry {part.type} parts")
        if results and not parts:
            if message.name:
                self._lose(report, f"{where}.name", "tool messages have no name field")
            return results
        content: str | list[dict[str, Any]] = (
            parts[0]["text"] if len(parts) == 1 and parts[0]["type"] == "text" else parts or ""
        )
        out: dict[str, Any] = {"role": "user", "content": content}
        if message.name:
            out["name"] = message.name
        return [*results, out]

    def _assistant_message(
        self,
        message: Message,
        where: str,
        report: ConversionReport,
        names: dict[str, str],
        model: str,
    ) -> dict[str, Any]:
        texts: list[str] = []
        refusals: list[str] = []
        reasoning: list[str] = []
        calls: list[dict[str, Any]] = []
        for j, part in enumerate(message.content):
            at = f"{where}.content.{j}"
            if isinstance(part, TextPart):
                if calls:
                    self._lose(
                        report,
                        at,
                        "text after a tool call is sent before the calls",
                        "approximated",
                    )
                texts.append(part.text)
            elif isinstance(part, RefusalPart):
                refusals.append(part.text)
            elif isinstance(part, ToolCallPart):
                names[part.id] = part.name
                calls.append(_wire_call(part))
            elif isinstance(part, ReasoningPart):
                text = self._replayable_reasoning(part, at, report, model)
                if text:
                    reasoning.append(text)
            else:
                self._lose(report, at, f"assistant messages cannot carry {part.type} parts")
        syntax = self.options.tool_parser
        if calls and syntax is not None:
            # The model's own syntax, in the text its template renders (see the module notes).
            written = parsers.render(
                [
                    parsers.ParsedCall(name=part.name, arguments=part.arguments)
                    for part in message.content
                    if isinstance(part, ToolCallPart)
                ],
                syntax,
            )
            texts = ["\n".join([*texts, written])]
            calls = []
        out: dict[str, Any] = {"role": "assistant"}
        out["content"] = _text_content(texts) if texts else (None if calls else "")
        if refusals:
            out["refusal"] = "".join(refusals)
        if calls:
            out["tool_calls"] = calls
        field_name = self._reasoning_field()
        if reasoning and field_name:
            out[field_name] = "".join(reasoning)
        if message.name:
            out["name"] = message.name
        return out

    def _replayable_reasoning(
        self, part: ReasoningPart, at: str, report: ConversionReport, model: str
    ) -> str | None:
        if part.opaque:
            self._lose(report, f"{at}.opaque", "chat-completions has no field for opaque reasoning")
        text = part.text or part.summary
        if not text:
            return None
        field_name = self._reasoning_field()
        same_model = part.origin is not None and part.origin.model == model
        if field_name and same_model:
            return text
        if field_name:
            self._lose(report, at, "reasoning from another model is never sent back")
        else:
            self._lose(
                report, at, "prior reasoning is not sent back (no reasoning_field configured)"
            )
        return None

    def _tool_messages(
        self, message: Message, where: str, report: ConversionReport, names: dict[str, str]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for j, part in enumerate(message.content):
            at = f"{where}.content.{j}"
            if isinstance(part, ToolResultPart):
                out.append(self._tool_result(part, at, report, names))
            else:
                self._lose(report, at, f"tool messages carry tool results, not {part.type}")
        if message.name:
            self._lose(report, f"{where}.name", "tool messages have no name field")
        return out

    def _tool_result(
        self, part: ToolResultPart, at: str, report: ConversionReport, names: dict[str, str]
    ) -> dict[str, Any]:
        texts: list[str] = []
        for k, item in enumerate(part.content):
            if isinstance(item, TextPart):
                texts.append(item.text)
            else:
                self._lose(report, f"{at}.content.{k}", "tool messages carry text only")
        if part.is_error:
            self._lose(report, f"{at}.is_error", "tool messages have no error flag")
        if part.name and names.get(part.call_id) != part.name:
            self._lose(report, f"{at}.name", "tool messages carry no name; no call with that name")
        return {"role": "tool", "tool_call_id": part.call_id, "content": _text_content(texts)}

    def _encode_image(
        self, part: ImagePart, at: str, report: ConversionReport
    ) -> dict[str, Any] | None:
        if part.url:
            url = part.url
        elif part.data:
            media_type = part.media_type
            if not media_type:
                media_type = "image/png"
                self._lose(report, f"{at}.media_type", "missing; sent as image/png", "approximated")
            url = f"data:{media_type};base64,{part.data}"
        else:
            self._lose(report, at, "image has neither a URL nor data")
            return None
        image_url: dict[str, Any] = {"url": url}
        if part.detail:
            image_url["detail"] = part.detail
        return {"type": "image_url", "image_url": image_url}

    def _encode_file(
        self, part: FilePart, at: str, report: ConversionReport
    ) -> dict[str, Any] | None:
        file: dict[str, Any] = {}
        if part.file_id:
            file["file_id"] = part.file_id
        elif part.data:
            media_type = part.media_type
            if not media_type:
                media_type = "application/octet-stream"
                self._lose(
                    report, f"{at}.media_type", f"missing; sent as {media_type}", "approximated"
                )
            file["file_data"] = f"data:{media_type};base64,{part.data}"
        elif part.url and split_data_uri(part.url) is not None:
            file["file_data"] = part.url
        else:
            self._lose(report, at, "file parts take inline data or a file id, not a URL")
            return None
        if part.name:
            file["filename"] = part.name
        return {"type": "file", "file": file}

    # ---- client side: responses ----------------------------------------------------------

    def decode_response(self, body: Any) -> Response:
        data = check_body(body, self.format)
        choice = first_choice(data, self.format)
        message = as_dict(choice.get("message"))
        model = as_str(data.get("model"))
        parts: list[Part] = []
        reasoning = reasoning_text(message)
        if reasoning:
            parts.append(
                ReasoningPart(text=reasoning, origin=Origin(format=self.format, model=model))
            )
        content = message.get("content")
        if isinstance(content, str):
            if content:
                parts.append(TextPart(text=content))
        else:
            for item in as_list(content):
                text = as_str(as_dict(item).get("text"))
                if text:
                    parts.append(TextPart(text=text))
        refusal = as_str(message.get("refusal"))
        if refusal:
            parts.append(RefusalPart(text=refusal))
        calls = self._decode_calls(as_list(message.get("tool_calls")), self._turn)
        syntax = self._text_parser()
        if not calls and syntax is not None:
            text = "".join(part.text for part in parts if isinstance(part, TextPart))
            remaining, calls = extract_calls(text, syntax, self._turn)
            if calls:
                kept: list[Part] = []
                for part in parts:
                    if not isinstance(part, TextPart):
                        kept.append(part)
                    elif remaining:  # the text left over takes the place of the first text part
                        kept.append(TextPart(text=remaining))
                        remaining = ""
                parts = kept
        parts.extend(calls)
        reason, raw_reason = finish_from_wire(choice.get("finish_reason"), bool(calls))
        created = as_int(data.get("created"))
        return Response(
            id=as_str(data.get("id")),
            model=model,
            message=Message(role="assistant", content=parts),
            finish_reason=reason,
            raw_finish_reason=raw_reason,
            usage=decode_usage(data.get("usage"), data.get("timings")),
            created=created,
        )

    @staticmethod
    def _decode_calls(raw_calls: list[Any], turn: int) -> list[ToolCallPart]:
        calls: list[ToolCallPart] = []
        for index, raw in enumerate(raw_calls):
            call = as_dict(raw)
            function = as_dict(call.get("function"))
            arguments = function.get("arguments")
            if arguments is None:
                arguments = "{}"
            elif not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            call_id = as_str(call.get("id"))
            calls.append(
                ToolCallPart(
                    id=call_id or synthetic_call_id(turn, index),
                    name=as_str(function.get("name")) or "",
                    arguments=arguments,
                    synthetic=not call_id,
                )
            )
        return calls

    def decode_stream_event(self, event: StreamEvent, state: StreamState) -> list[Delta]:
        data = event.data.strip()
        if not data or state.finished:
            return []
        if data == "[DONE]":
            return self._finish(state, done=True)
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return self._fail(
                state,
                ErrorDelta(kind="invalid_response", message=f"bad stream event: {data[:200]}"),
            )
        if not isinstance(chunk, dict):
            return []
        if chunk.get("error") is not None and not chunk.get("choices"):
            return self._fail(state, error_delta(chunk))
        out: list[Delta] = []
        if not state.started:
            state.started = True
            out.append(StartDelta(id=as_str(chunk.get("id")), model=as_str(chunk.get("model"))))
        for raw_choice in as_list(chunk.get("choices")):
            choice = as_dict(raw_choice)
            if as_int(choice.get("index")) not in (None, 0):
                continue  # n > 1 is never requested
            delta = as_dict(choice.get("delta"))
            reasoning = reasoning_text(delta)
            if reasoning:
                out.append(ReasoningDelta(text=reasoning))
            content = delta.get("content")
            if isinstance(content, str) and content:
                extractor = self._extractor(state)
                out.extend(
                    extractor.feed(content) if extractor is not None else [TextDelta(text=content)]
                )
            refusal = delta.get("refusal")
            if isinstance(refusal, str) and refusal:
                out.append(RefusalDelta(text=refusal))
            for fragment in as_list(delta.get("tool_calls")):
                out.extend(self._tool_fragment(as_dict(fragment), state))
            finish = as_str(choice.get("finish_reason"))
            if finish:
                state.data["finish"] = finish
        usage = decode_usage(chunk.get("usage"), chunk.get("timings"))
        if usage is not None:
            out.append(UsageDelta(usage=usage))
        return out

    def finish_stream(self, state: StreamState) -> list[Delta]:
        return self._finish(state, done=False)

    def _extractor(self, state: StreamState) -> StreamingToolCallExtractor | None:
        syntax = self._text_parser()
        if syntax is None:
            return None
        extractor = state.data.get("extractor")
        if not isinstance(extractor, StreamingToolCallExtractor):
            extractor = StreamingToolCallExtractor(
                syntax, markers=parsers.CALL_MARKERS[syntax], turn=self._turn
            )
            state.data["extractor"] = extractor
        return extractor

    @staticmethod
    def _fail(state: StreamState, error: ErrorDelta) -> list[Delta]:
        state.finished = True
        return [error]

    def _tool_fragment(self, fragment: dict[str, Any], state: StreamState) -> list[Delta]:
        function = as_dict(fragment.get("function"))
        name = as_str(function.get("name")) or None
        arguments = function.get("arguments")
        if arguments is not None and not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        wire_id = as_str(fragment.get("id")) or None
        index = as_int(fragment.get("index"))
        out: list[Delta] = []
        call = self._find_call(state, index, wire_id, name)
        if call is None:
            number = state.tool_index
            state.tool_index += 1
            call = _StreamCall(
                index=number,
                id=wire_id or synthetic_call_id(self._turn, number),
                synthetic=wire_id is None,
                name=name,
            )
            state.data.setdefault("calls", []).append(call)
            if index is not None:
                state.data.setdefault("by_index", {})[index] = call
            if wire_id:
                state.data.setdefault("by_id", {})[wire_id] = call
        elif name and call.name is None:
            call.name = name
        current: _StreamCall | None = state.data.get("current")
        if current is not None and current is not call and current.started and not current.ended:
            current.ended = True
            out.append(ToolCallEndDelta(index=current.index))
        state.data["current"] = call
        if not call.started and call.name is not None:
            call.started = True
            out.append(
                ToolCallStartDelta(
                    index=call.index, id=call.id, name=call.name, synthetic=call.synthetic
                )
            )
            out.extend(ToolCallArgsDelta(index=call.index, delta=piece) for piece in call.pending)
            call.pending.clear()
        if arguments:
            if call.started:
                out.append(ToolCallArgsDelta(index=call.index, delta=arguments))
            else:
                call.pending.append(arguments)
        return out

    @staticmethod
    def _find_call(
        state: StreamState, index: int | None, wire_id: str | None, name: str | None
    ) -> _StreamCall | None:
        if index is not None:
            call: _StreamCall | None = state.data.get("by_index", {}).get(index)
            # A different id plus a name under a used index starts a new call (seen on servers
            # that number every call 0).
            if call is not None and wire_id and name and not call.synthetic and wire_id != call.id:
                return None
            return call
        if wire_id:
            found: _StreamCall | None = state.data.get("by_id", {}).get(wire_id)
            return found
        if name:
            return None
        last: _StreamCall | None = state.data.get("current")
        return last

    def _finish(self, state: StreamState, *, done: bool) -> list[Delta]:
        if state.finished:
            return []
        state.finished = True
        out: list[Delta] = []
        calls: list[_StreamCall] = state.data.get("calls", [])
        extractor = state.data.get("extractor")
        parsed = False
        if isinstance(extractor, StreamingToolCallExtractor):
            # Calls the server parsed win; then a held-back section is only text.
            out.extend(extractor.finish(parse=not calls))
            parsed = bool(extractor.calls)
        for call in calls:
            if not call.started:  # a call whose name never arrived is still reported
                call.started = True
                out.append(
                    ToolCallStartDelta(
                        index=call.index, id=call.id, name=call.name or "", synthetic=call.synthetic
                    )
                )
                out.extend(ToolCallArgsDelta(index=call.index, delta=p) for p in call.pending)
            if not call.ended:
                call.ended = True
                out.append(ToolCallEndDelta(index=call.index))
        raw = state.data.get("finish")
        if raw is None and not done:
            out.append(
                ErrorDelta(kind="invalid_response", message="stream ended before a finish reason")
            )
            return out
        reason, raw_reason = finish_from_wire(raw, bool(calls) or parsed)
        out.append(FinishDelta(reason=reason, raw=raw_reason))
        return out

    def decode_error(
        self, status: int, body: Any, headers: dict[str, str] | None = None
    ) -> ModelError:
        return error_from_http(status, body, headers)

    # ---- server side ---------------------------------------------------------------------

    def decode_request(
        self, body: Any, *, path: str = "", headers: dict[str, str] | None = None
    ) -> Request:
        if not isinstance(body, dict):
            raise ConversionError("chat-completions request body must be a JSON object")
        for key in ("functions", "function_call"):
            if body.get(key) is not None:
                raise ConversionError(f"deprecated `{key}` is not supported; use tools")
        n = as_int(body.get("n"))
        if n is not None and n != 1:
            raise ConversionError("n > 1 is not supported")
        if body.get("logprobs") is True or as_int(body.get("top_logprobs")):
            raise ConversionError("logprobs are not supported")
        if body.get("audio") is not None or "audio" in as_list(body.get("modalities")):
            raise ConversionError("audio output is not supported")
        model = as_str(body.get("model")) or ""
        system, messages = self._decode_messages(as_list(body.get("messages")), model)
        tools = [self._decode_tool(as_dict(tool)) for tool in as_list(body.get("tools"))]
        max_tokens = as_int(body.get("max_completion_tokens"))
        if max_tokens is None:
            max_tokens = as_int(body.get("max_tokens"))
        parallel = body.get("parallel_tool_calls")
        extensions = {k: v for k, v in body.items() if k not in self._KNOWN_FIELDS}
        raw_effort = as_str(body.get("reasoning_effort")) or as_str(
            as_dict(body.get("reasoning")).get("effort")
        )
        effort = _EFFORTS.get(raw_effort or "")
        if raw_effort is not None and effort is None:
            extensions["reasoning_effort"] = raw_effort  # a server-specific value, passed through
        return Request(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=self._decode_tool_choice(body.get("tool_choice")),
            parallel_tool_calls=parallel if isinstance(parallel, bool) else None,
            response_format=self._decode_response_format(body.get("response_format")),
            max_output_tokens=max_tokens,
            sampling=Sampling(
                temperature=as_float(body.get("temperature")),
                top_p=as_float(body.get("top_p")),
                top_k=as_int(body.get("top_k")),
                seed=as_int(body.get("seed")),
                stop=stop_list(body.get("stop")),
                presence_penalty=as_float(body.get("presence_penalty")),
                frequency_penalty=as_float(body.get("frequency_penalty")),
            ),
            effort=effort,
            stream=body.get("stream") is True,
            extensions={self.format: extensions} if extensions else {},
        )

    def _decode_messages(
        self, raw_messages: list[Any], model: str
    ) -> tuple[list[TextPart], list[Message]]:
        system: list[TextPart] = []
        messages: list[Message] = []
        names: dict[str, str] = {}
        unanswered: list[str] = []
        turn = 0
        for i, raw in enumerate(raw_messages):
            item = as_dict(raw)
            where = f"messages.{i}"
            role = item.get("role")
            name = as_str(item.get("name"))
            if role in ("system", "developer"):
                texts = self._decode_text_parts(item.get("content"), where)
                if not messages and not name:
                    system.extend(texts)
                else:
                    system_parts: list[Part] = [*texts]
                    messages.append(Message(role="system", content=system_parts, name=name))
            elif role == "user":
                parts = self._decode_user_parts(item.get("content"), where)
                messages.append(Message(role="user", content=parts, name=name))
            elif role == "assistant":
                if item.get("function_call") is not None:
                    raise ConversionError(f"{where}: deprecated function_call; use tool_calls")
                message = self._decode_assistant(item, where, turn, model)
                for call in message.tool_calls():
                    names[call.id] = call.name
                unanswered = [call.id for call in message.tool_calls()]
                messages.append(message)
                turn += 1
            elif role == "tool":
                call_id = as_str(item.get("tool_call_id")) or (
                    unanswered[0] if unanswered else synthetic_call_id(turn, 0)
                )
                if call_id in unanswered:
                    unanswered.remove(call_id)
                content: list[TextPart | ImagePart] = [
                    *self._decode_text_parts(item.get("content"), where)
                ]
                result = ToolResultPart(
                    call_id=call_id, name=names.get(call_id) or name, content=content
                )
                messages.append(Message(role="tool", content=[result]))
            else:
                raise ConversionError(f"{where}: unsupported role {role!r}")
        return system, messages

    def _decode_assistant(self, item: dict[str, Any], where: str, turn: int, model: str) -> Message:
        parts: list[Part] = []
        reasoning = reasoning_text(item)
        if reasoning:
            parts.append(
                ReasoningPart(text=reasoning, origin=Origin(format=self.format, model=model))
            )
        content = item.get("content")
        if isinstance(content, str):
            if content:
                parts.append(TextPart(text=content))
        elif content is not None:
            for j, raw in enumerate(as_list(content)):
                part = as_dict(raw)
                if part.get("type") == "text":
                    parts.append(TextPart(text=str(part.get("text", ""))))
                elif part.get("type") == "refusal":
                    parts.append(RefusalPart(text=str(part.get("refusal", ""))))
                else:
                    raise ConversionError(
                        f"{where}.content.{j}: unsupported part {part.get('type')!r}"
                    )
        refusal = as_str(item.get("refusal"))
        if refusal:
            parts.append(RefusalPart(text=refusal))
        raw_calls = as_list(item.get("tool_calls"))
        for j, raw in enumerate(raw_calls):
            if as_dict(raw).get("type", "function") != "function":
                raise ConversionError(
                    f"{where}.tool_calls.{j}: only function tool calls are supported"
                )
        parts.extend(self._decode_calls(raw_calls, turn))
        return Message(role="assistant", content=parts, name=as_str(item.get("name")))

    @staticmethod
    def _decode_text_parts(content: Any, where: str) -> list[TextPart]:
        if content is None:
            return []
        if isinstance(content, str):
            return [TextPart(text=content)] if content else []
        if not isinstance(content, list):
            raise ConversionError(f"{where}: content must be a string or a list of parts")
        out: list[TextPart] = []
        for j, raw in enumerate(content):
            part = as_dict(raw)
            if part.get("type") != "text":
                raise ConversionError(f"{where}.content.{j}: only text parts are allowed here")
            out.append(TextPart(text=str(part.get("text", ""))))
        return out

    @staticmethod
    def _decode_user_parts(content: Any, where: str) -> list[Part]:
        if content is None:
            return []
        if isinstance(content, str):
            return [TextPart(text=content)] if content else []
        if not isinstance(content, list):
            raise ConversionError(f"{where}: content must be a string or a list of parts")
        out: list[Part] = []
        for j, raw in enumerate(content):
            part = as_dict(raw)
            kind = part.get("type")
            if kind == "text":
                out.append(TextPart(text=str(part.get("text", ""))))
            elif kind == "image_url":
                image = as_dict(part.get("image_url"))
                url = as_str(image.get("url"))
                if not url:
                    raise ConversionError(f"{where}.content.{j}: image_url without a url")
                inline = split_data_uri(url)
                detail = as_str(image.get("detail"))
                if inline is not None:
                    out.append(ImagePart(media_type=inline[0], data=inline[1], detail=detail))
                else:
                    out.append(ImagePart(url=url, detail=detail))
            elif kind == "file":
                file = as_dict(part.get("file"))
                file_data = as_str(file.get("file_data"))
                inline = split_data_uri(file_data) if file_data else None
                out.append(
                    FilePart(
                        file_id=as_str(file.get("file_id")),
                        data=inline[1] if inline else file_data,
                        media_type=inline[0] if inline else None,
                        name=as_str(file.get("filename")),
                    )
                )
            else:
                raise ConversionError(f"{where}.content.{j}: {kind!r} parts are not supported")
        return out

    @staticmethod
    def _decode_tool(tool: dict[str, Any]) -> ToolSpec:
        if tool.get("type", "function") != "function":
            raise ConversionError(
                f"{tool.get('type')!r} tools are not supported; use function tools"
            )
        function = as_dict(tool.get("function"))
        name = as_str(function.get("name"))
        if not name:
            raise ConversionError("function tool without a name")
        parameters = function.get("parameters")
        strict = function.get("strict")
        spec = ToolSpec(
            name=name,
            description=as_str(function.get("description")),
            strict=strict if isinstance(strict, bool) else None,
        )
        if isinstance(parameters, dict):
            spec.parameters = parameters
        return spec

    @staticmethod
    def _decode_tool_choice(value: Any) -> ToolChoice | None:
        if value is None:
            return None
        if value in ("auto", "none", "required"):
            choice: ToolChoice = value
            return choice
        choice_dict = as_dict(value)
        if choice_dict.get("type") == "function":
            name = as_str(as_dict(choice_dict.get("function")).get("name"))
            if name:
                return NamedToolChoice(name=name)
        raise ConversionError(f"unsupported tool_choice {value!r}")

    @staticmethod
    def _decode_response_format(value: Any) -> ResponseFormat | None:
        if value is None:
            return None
        data = as_dict(value)
        kind = data.get("type")
        if kind in ("text", "json_object"):
            return ResponseFormat(type=kind)
        if kind == "json_schema":
            schema = as_dict(data.get("json_schema"))
            strict = schema.get("strict")
            return ResponseFormat(
                type="json_schema",
                name=as_str(schema.get("name")),
                json_schema=as_dict(schema.get("schema")) if "schema" in schema else None,
                strict=strict if isinstance(strict, bool) else None,
            )
        raise ConversionError(f"unsupported response_format type {kind!r}")

    def encode_response(self, response: Response) -> dict[str, Any]:
        texts: list[str] = []
        refusals: list[str] = []
        reasoning: list[str] = []
        calls: list[dict[str, Any]] = []
        for part in response.message.content:
            if isinstance(part, TextPart):
                texts.append(part.text)
            elif isinstance(part, RefusalPart):
                refusals.append(part.text)
            elif isinstance(part, ReasoningPart) and (part.text or part.summary):
                reasoning.append(part.text or part.summary or "")
            elif isinstance(part, ToolCallPart):
                calls.append(_wire_call(part))
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(texts) if texts else None,
            "refusal": "".join(refusals) if refusals else None,
        }
        if reasoning:
            message[self._reasoning_field() or DEFAULT_REASONING_FIELD] = "".join(reasoning)
        if calls:
            message["tool_calls"] = calls
        body: dict[str, Any] = {
            "id": response.id or new_id("chatcmpl"),
            "object": "chat.completion",
            "created": response.created if response.created is not None else int(time.time()),
            "model": response.model or "",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "logprobs": None,
                    "finish_reason": FINISH_TO_WIRE.get(response.finish_reason, "stop"),
                }
            ],
        }
        if response.usage is not None:
            body["usage"] = encode_usage(response.usage)
        return body

    def encode_stream(self, deltas: Iterable[Delta]) -> Iterator[StreamEvent]:
        writer = _ChunkWriter(self._reasoning_field() or DEFAULT_REASONING_FIELD)
        for delta in deltas:
            yield from writer.feed(delta)
        yield from writer.close(self.options.stream_usage)

    def encode_error(self, error: ModelError) -> tuple[int, Any]:
        return encode_error_body(error)


class _ChunkWriter:
    """IR deltas -> `chat.completion.chunk` events (server side)."""

    def __init__(self, reasoning_field: str) -> None:
        self.reasoning_field = reasoning_field
        self.id: str | None = None
        self.model = ""
        self.created = int(time.time())
        self.started = False
        self.finished = False
        self.failed = False
        self.usage: Usage | None = None

    def _chunk(self, delta: dict[str, Any], finish: str | None = None) -> StreamEvent:
        return sse_json(
            {
                "id": self.id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model,
                "choices": [
                    {"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish}
                ],
            }
        )

    def _start(self) -> list[StreamEvent]:
        if self.started:
            return []
        self.started = True
        self.id = self.id or new_id("chatcmpl")
        return [self._chunk({"role": "assistant", "content": ""})]

    def feed(self, delta: Delta) -> list[StreamEvent]:
        if isinstance(delta, UsageDelta):
            self.usage = merge_usage(self.usage, delta.usage)
            return []
        if self.finished or self.failed:
            return []
        if isinstance(delta, StartDelta):
            if not self.started:
                self.id = delta.id or self.id
                self.model = delta.model or self.model
            return self._start()
        if isinstance(delta, TextDelta):
            return [*self._start(), self._chunk({"content": delta.text})] if delta.text else []
        if isinstance(delta, RefusalDelta):
            return [*self._start(), self._chunk({"refusal": delta.text})] if delta.text else []
        if isinstance(delta, ReasoningDelta):
            if not delta.text:
                return []  # opaque payloads have no field in this format
            return [*self._start(), self._chunk({self.reasoning_field: delta.text})]
        if isinstance(delta, ToolCallStartDelta):
            call = {
                "index": delta.index,
                "id": delta.id,
                "type": "function",
                "function": {"name": delta.name, "arguments": ""},
            }
            return [*self._start(), self._chunk({"tool_calls": [call]})]
        if isinstance(delta, ToolCallArgsDelta):
            if not delta.delta:
                return []
            fragment = {"index": delta.index, "function": {"arguments": delta.delta}}
            return [*self._start(), self._chunk({"tool_calls": [fragment]})]
        if isinstance(delta, FinishDelta):
            self.finished = True
            finish = FINISH_TO_WIRE.get(delta.reason, "stop")
            return [*self._start(), self._chunk({}, finish)]
        if isinstance(delta, ErrorDelta):
            self.failed = True
            return [stream_error_event(delta)]
        return []

    def close(self, include_usage: bool) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        if not self.finished and not self.failed:
            out.extend(self._start())
            out.append(self._chunk({}, "stop"))
        if self.usage is not None and include_usage and not self.failed:
            out.append(
                sse_json(
                    {
                        "id": self.id,
                        "object": "chat.completion.chunk",
                        "created": self.created,
                        "model": self.model,
                        "choices": [],
                        "usage": encode_usage(self.usage),
                    }
                )
            )
        out.append(StreamEvent(data="[DONE]"))
        return out
