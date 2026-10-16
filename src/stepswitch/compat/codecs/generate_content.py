"""Codec for the generate-content format (ADR 0003; docs/RESEARCH.md, "API formats").

The format's own reference labels it legacy but fully supported; the newer interactions format
is out of scope here. Wire details follow the format's API reference, its discovery document
(revision 20261015) and its thinking, function-calling and structured-output guides (checked
2026-10-16). Choices the IR leaves open:

* Requests go to `POST /v1beta/models/{model}:generateContent`; streams use
  `:streamGenerateContent` with `alt=sse`, which sends one response object per SSE `data:` line
  and no event names. Decoding accepts both the camelCase and snake_case spelling of fields.
* Roles are `user` and `model`. Tool results are `functionResponse` parts in `user` contents,
  consecutive contents with the same role are merged, and IR system messages are hoisted into
  `systemInstruction`. A `functionResponse` needs the function name: it comes from
  `ToolResultPart.name` or from the matching call in the history.
* The `response` object of a function response is `{"result": text}`, `{"error": text}` for
  errors, or the tool's own object when the result text is exactly a compact JSON object.
* Declarations use `parametersJsonSchema` (plain JSON Schema). The reference marks it mutually
  exclusive with the OpenAPI-subset `parameters`, which decoding also accepts.
* Function calls without an `id` get synthetic IDs, and synthetic IDs are never sent back.
* Thought parts (`thought: true`) become reasoning. A `thoughtSignature` becomes a reasoning part
  holding only the signature, placed right before the part that carried it; encoding puts it
  back on the next non-thought part (or on an empty text part at the end, as streams send it).
  A signature that arrived on a thought part itself also moves to the next non-thought part,
  because assembled stream deltas cannot tell the two cases apart. Signatures are replayed only
  for reasoning whose origin is this format; the reference makes them mandatory for function
  calling.
* Usage: `promptTokenCount` already includes cached content, and tool-use prompt tokens are
  added to input. The reference defines `totalTokenCount` as prompt + tool-use prompt + thoughts
  + candidates, so `candidatesTokenCount` excludes thoughts; IR `output_tokens` is candidates +
  thoughts (output includes reasoning, as in the other formats) and `reasoning_tokens` is
  thoughts. The raw object is kept.
* Effort maps to `thinkingConfig.thinkingLevel` (MINIMAL, LOW, MEDIUM, HIGH): `none` and
  `xhigh`/`max` are clamped and reported. `budget_tokens` maps to `thinkingBudget`; the two
  cannot be sent together, so the budget wins and the clash is reported.
* Structured output uses `responseMimeType` plus `responseJsonSchema`; decoding also accepts
  `responseSchema` and `responseFormat.text`.
* `decode_request` keeps fields the IR does not model in
  `Request.extensions["generate-content"]["body"]`; `encode_request` merges them back for this
  format only. The server-side methods and `decode_response` accept an optional `report`.
"""

from __future__ import annotations

import copy
import json
import re
import zlib
from collections.abc import Iterable, Iterator
from typing import Any, ClassVar, Literal

from stepswitch.compat.codecs.base import Codec, Framing, HttpCall, StreamState
from stepswitch.compat.errors import ErrorKind, ModelError, kind_for_status, parse_retry_after
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
    ToolChoice,
    ToolResultPart,
    ToolSpec,
    Usage,
    UsageDelta,
    synthetic_call_id,
)
from stepswitch.compat.stream import merge_usage

API_PREFIX = "/v1beta/models/"
MAX_STOP_SEQUENCES = 5
RETRY_INFO_TYPE = "type.googleapis.com/google.rpc.RetryInfo"

THINKING_LEVELS: dict[str, str] = {
    "none": "MINIMAL",
    "minimal": "MINIMAL",
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "xhigh": "HIGH",
    "max": "HIGH",
}
LEVEL_EFFORTS: dict[str, Effort] = {
    "MINIMAL": "minimal",
    "LOW": "low",
    "MEDIUM": "medium",
    "HIGH": "high",
}
CONTENT_FILTER_FINISHES = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "PROHIBITED_CONTENT",
        "BLOCKLIST",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    }
)
FINISH_TO_WIRE: dict[str, str] = {
    "stop": "STOP",
    "tool_calls": "STOP",
    "length": "MAX_TOKENS",
    "content_filter": "SAFETY",
    "error": "OTHER",
    "other": "OTHER",
}
# Canonical status names (the error body's `status`) and the error kinds they mean.
STATUS_KINDS: dict[str, ErrorKind] = {
    "INVALID_ARGUMENT": "bad_request",
    "FAILED_PRECONDITION": "bad_request",
    "OUT_OF_RANGE": "bad_request",
    "UNAUTHENTICATED": "authentication",
    "PERMISSION_DENIED": "permission",
    "NOT_FOUND": "not_found",
    "RESOURCE_EXHAUSTED": "rate_limit",
    "UNAVAILABLE": "overloaded",
    "DEADLINE_EXCEEDED": "timeout",
    "INTERNAL": "server",
    "UNKNOWN": "server",
    "DATA_LOSS": "server",
    "UNIMPLEMENTED": "unsupported",
}
KIND_STATUS: dict[str, tuple[int, str]] = {
    "bad_request": (400, "INVALID_ARGUMENT"),
    "too_large": (400, "INVALID_ARGUMENT"),
    "unsupported": (501, "UNIMPLEMENTED"),
    "authentication": (401, "UNAUTHENTICATED"),
    "permission": (403, "PERMISSION_DENIED"),
    "blocked": (403, "PERMISSION_DENIED"),
    "not_found": (404, "NOT_FOUND"),
    "rate_limit": (429, "RESOURCE_EXHAUSTED"),
    "server": (500, "INTERNAL"),
    "invalid_response": (500, "INTERNAL"),
    "overloaded": (503, "UNAVAILABLE"),
    "connection": (503, "UNAVAILABLE"),
    "timeout": (504, "DEADLINE_EXCEEDED"),
}
TOP_MODELED = frozenset(
    {"model", "contents", "systemInstruction", "tools", "toolConfig", "generationConfig"}
)
CONFIG_MODELED = frozenset(
    {
        "temperature",
        "topP",
        "topK",
        "seed",
        "stopSequences",
        "presencePenalty",
        "frequencyPenalty",
        "maxOutputTokens",
        "responseMimeType",
        "responseJsonSchema",
        "_responseJsonSchema",
        "responseSchema",
        "responseFormat",
        "thinkingConfig",
    }
)
# Fields the IR carries; any other field of a part, function response, candidate or response
# (video offsets, scheduling, citations, grounding, ...) is reported when decoding.
PART_FIELDS = frozenset(
    {
        "text",
        "thought",
        "thoughtSignature",
        "functionCall",
        "functionResponse",
        "inlineData",
        "fileData",
    }
)
FUNCTION_RESPONSE_FIELDS = frozenset({"id", "name", "response", "parts"})
CANDIDATE_FIELDS = frozenset({"content", "finishReason", "index"})
RESPONSE_FIELDS = frozenset(
    {"candidates", "usageMetadata", "modelVersion", "responseId", "promptFeedback"}
)
_PATH_RE = re.compile(
    r"/models/(?P<model>[^/:]+):(?P<method>generateContent|streamGenerateContent)$"
)
_DATA_URI_RE = re.compile(r"^data:(?P<media>[\w.+-]+/[\w.+-]+)?(?P<params>(;[^,;]*)*);base64,")
_CAMEL_RE = re.compile(r"(?<!^)([A-Z])")

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


class GenerateContentCodec(Codec):
    key: ClassVar[str] = "generate_content"
    format: ClassVar[str] = "generate-content"
    framing: ClassVar[Framing] = "sse"

    # ---- client side ----------------------------------------------------------------------

    def encode_request(self, request: Request, report: ConversionReport) -> HttpCall:
        loss = _Losses(report, self.options.strict)
        system, contents = self._encode_contents(request, loss)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": system}
        if request.tools:
            declarations = [_declaration(tool, loss) for tool in request.tools]
            body["tools"] = [{"functionDeclarations": declarations}]
        tool_config = _tool_config(request, loss)
        if tool_config:
            body["toolConfig"] = tool_config
        config = self._generation_config(request, loss)
        if config:
            body["generationConfig"] = config
        if request.prompt is not None:
            loss.add("prompt", "pre-rendered prompts are only sent to prompt-based formats")
        _apply_extensions(request.extensions, self.format, body, loss)
        method = "streamGenerateContent" if request.stream else "generateContent"
        return HttpCall(
            method="POST",
            path=f"{API_PREFIX}{_model_name(request.model)}:{method}",
            json=body,
            params={"alt": "sse"} if request.stream else {},
            stream=request.stream,
        )

    def decode_response(self, body: Any, *, report: ConversionReport | None = None) -> Response:
        if isinstance(body, list) and len(body) == 1:
            body = body[0]
        if not isinstance(body, dict):
            raise ModelError("invalid_response", "the response body is not a JSON object", raw=body)
        if "error" in body and not body.get("candidates"):
            raise self.decode_error(_int(_dict(body["error"]).get("code")) or 500, body)
        loss = _Losses(report)
        model = _str(body.get("modelVersion"))
        response_id = _str(body.get("responseId"))
        candidates = _list(body.get("candidates"))
        if len(candidates) > 1:
            loss.add("candidates", f"{len(candidates) - 1} extra candidates are not carried")
        candidate = _dict(candidates[0]) if candidates else {}
        _note_extras(body, RESPONSE_FIELDS, loss, "response")
        _note_extras(candidate, CANDIDATE_FIELDS, loss, "candidates[0]")
        parts = self._decode_model_parts(
            _list(_dict(candidate.get("content")).get("parts")),
            Origin(format=self.format, model=model),
            _turn_number(response_id),
            loss,
            "candidates[0].content",
        )
        has_calls = any(isinstance(part, ToolCallPart) for part in parts)
        raw = _str(_get(candidate, "finishReason"))
        finish = _finish(raw, has_calls)
        if not candidates:
            raw = _str(_get(_dict(_get(body, "promptFeedback")), "blockReason"))
            finish = "content_filter" if raw else "other"
        return Response(
            id=response_id,
            model=model,
            message=Message(role="assistant", content=parts),
            finish_reason=finish,
            raw_finish_reason=raw,
            usage=_usage(_get(body, "usageMetadata")),
        )

    def decode_stream_event(self, event: StreamEvent, state: StreamState) -> list[Delta]:
        try:
            data = json.loads(event.data)
        except json.JSONDecodeError:
            return []
        out: list[Delta] = []
        for chunk in data if isinstance(data, list) else [data]:
            if state.finished or not isinstance(chunk, dict):
                continue
            if "error" in chunk:
                out.append(_error_delta(_dict(chunk["error"])))
                state.finished = True
                continue
            if not state.started:
                state.started = True
                response_id = _str(chunk.get("responseId"))
                state.data["turn"] = _turn_number(response_id)
                out.append(StartDelta(id=response_id, model=_str(chunk.get("modelVersion"))))
            candidates = _list(chunk.get("candidates"))
            if candidates:
                candidate = _dict(candidates[0])
                for part in _list(_dict(candidate.get("content")).get("parts")):
                    out.extend(_part_deltas(_dict(part), state))
                finish = _str(_get(candidate, "finishReason"))
                if finish:
                    state.data["finish"] = finish
            else:
                blocked = _str(_get(_dict(_get(chunk, "promptFeedback")), "blockReason"))
                if blocked:
                    state.data["finish"] = blocked
                    state.data["blocked"] = True
            usage = _usage(_get(chunk, "usageMetadata"))
            if usage is not None:
                out.append(UsageDelta(usage=usage))
        return out

    def finish_stream(self, state: StreamState) -> list[Delta]:
        if state.finished:
            return []
        state.finished = True
        raw = state.data.get("finish")
        if raw is None:
            detail = "without a finish reason" if state.started else "without any response"
            return [ErrorDelta(kind="invalid_response", message=f"the stream ended {detail}")]
        if state.data.get("blocked"):
            return [FinishDelta(reason="content_filter", raw=raw)]
        return [FinishDelta(reason=_finish(raw, bool(state.data.get("calls"))), raw=raw)]

    def decode_error(
        self, status: int, body: Any, headers: dict[str, str] | None = None
    ) -> ModelError:
        payload = body[0] if isinstance(body, list) and body else body
        error = _dict(_dict(payload).get("error"))
        name = _str(error.get("status"))
        message = _str(error.get("message"))
        if not message and isinstance(body, str) and body.strip():
            message = body.strip()[:500]
        retry_after = parse_retry_after(_header(headers, "retry-after"))
        if retry_after is None:
            retry_after = _retry_delay(error.get("details"))
        return ModelError(
            STATUS_KINDS.get(name or "", kind_for_status(status)),
            message or f"HTTP {status}",
            status=status,
            code=name,
            retry_after=retry_after,
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
        match = _PATH_RE.search(path)
        stash = {
            _camel(key): copy.deepcopy(value)
            for key, value in body.items()
            if _camel(key) not in TOP_MODELED
        }
        system: list[TextPart] = []
        instruction = _dict(_get(body, "systemInstruction"))
        for index, raw in enumerate(_list(instruction.get("parts"))):
            text = _str(_dict(raw).get("text"))
            if text is None:
                loss.add(f"systemInstruction.parts[{index}]", "only text is kept from the system")
            elif text:
                system.append(TextPart(text=text))
        config = _dict(_get(body, "generationConfig"))
        config_stash = {
            _camel(key): copy.deepcopy(value)
            for key, value in config.items()
            if _camel(key) not in CONFIG_MODELED
        }
        response_format = _decode_response_format(config, config_stash)
        effort, budget = _decode_thinking(_dict(_get(config, "thinkingConfig")), config_stash)
        if config_stash:
            stash["generationConfig"] = config_stash
        model = match.group("model") if match else _model_name(_str(body.get("model")) or "")
        return Request(
            model=model,
            system=system,
            messages=self._decode_contents(_list(body.get("contents")), loss),
            tools=_decode_tools(_get(body, "tools"), stash),
            tool_choice=_decode_tool_config(_dict(_get(body, "toolConfig")), stash),
            response_format=response_format,
            max_output_tokens=_int(_get(config, "maxOutputTokens")),
            sampling=Sampling(
                temperature=_float(config.get("temperature")),
                top_p=_float(_get(config, "topP")),
                top_k=_int(_get(config, "topK")),
                seed=_int(config.get("seed")),
                stop=[s for s in _list(_get(config, "stopSequences")) if isinstance(s, str)],
                presence_penalty=_float(_get(config, "presencePenalty")),
                frequency_penalty=_float(_get(config, "frequencyPenalty")),
            ),
            effort=effort,
            budget_tokens=budget,
            stream=match is not None and match.group("method") == "streamGenerateContent",
            extensions={self.format: {"body": stash}} if stash else {},
        )

    def encode_response(self, response: Response, *, report: ConversionReport | None = None) -> Any:
        loss = _Losses(report)
        parts = self._model_parts(response.message, "message", loss, reply=True)
        has_calls = any(isinstance(part, ToolCallPart) for part in response.message.content)
        candidate: dict[str, Any] = {
            "content": {"role": "model", "parts": parts},
            "finishReason": _wire_finish(
                response.finish_reason, response.raw_finish_reason, has_calls
            ),
            "index": 0,
        }
        body: dict[str, Any] = {"candidates": [candidate]}
        usage = _wire_usage(response.usage)
        if usage:
            body["usageMetadata"] = usage
        if response.model:
            body["modelVersion"] = response.model
        if response.id:
            body["responseId"] = response.id
        return body

    def encode_stream(self, deltas: Iterable[Delta]) -> Iterator[StreamEvent]:
        writer = _ChunkWriter()
        for delta in deltas:
            writer.feed(delta)
            yield from writer.drain()
        writer.end()
        yield from writer.drain()

    def encode_error(self, error: ModelError) -> tuple[int, Any]:
        status, name = KIND_STATUS.get(error.kind, (500, "INTERNAL"))
        payload: dict[str, Any] = {"code": status, "message": error.message, "status": name}
        if error.retry_after is not None:
            delay = f"{error.retry_after:g}s"
            payload["details"] = [{"@type": RETRY_INFO_TYPE, "retryDelay": delay}]
        return status, {"error": payload}

    # ---- helpers that need the codec ------------------------------------------------------

    def _encode_contents(
        self, request: Request, loss: _Losses
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        system = [{"text": p.text} for p in request.system if p.text]
        calls = {p.id: p for m in request.messages for p in m.tool_calls()}
        contents: list[dict[str, Any]] = []
        started = False
        for index, message in enumerate(request.messages):
            field = f"messages[{index}]"
            if message.role == "system":
                if started:
                    loss.add(
                        field,
                        "system message inside the conversation moved to systemInstruction",
                        "approximated",
                    )
                for j, part in enumerate(message.content):
                    if isinstance(part, TextPart):
                        if part.text:
                            system.append({"text": part.text})
                    else:
                        loss.add(f"{field}.content[{j}]", "only text is allowed in the system")
                continue
            started = True
            if message.name:
                loss.add(f"{field}.name", "the generate-content format has no participant names")
            if message.metadata:
                loss.add(f"{field}.metadata", f"not carried: {sorted(message.metadata)}")
            if message.role == "assistant":
                role, parts = "model", self._model_parts(message, field, loss, reply=False)
            else:
                role, parts = "user", self._user_parts(message, calls, field, loss)
            if not parts:
                continue
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})
        return system, contents

    def _model_parts(
        self, message: Message, field: str, loss: _Losses, *, reply: bool
    ) -> list[dict[str, Any]]:
        """Model-turn parts. A signature goes on the next non-thought part. In a reply, reasoning
        of another origin keeps its text as a thought part but never its payload."""
        parts: list[dict[str, Any]] = []
        pending: str | None = None
        for index, part in enumerate(message.content):
            where = f"{field}.content[{index}]"
            if isinstance(part, ReasoningPart):
                own = part.origin is not None and part.origin.format == self.format
                text = part.text if own else (part.text or part.summary) if reply else None
                if not own and (part.opaque or not reply and (part.text or part.summary)):
                    origin = part.origin.format if part.origin else "unknown"
                    loss.add(where, f"reasoning from origin {origin!r} is not replayed (ADR 0003)")
                if text:
                    parts.append({"text": text, "thought": True})
                if own and part.opaque and part.opaque_kind != "signature":
                    loss.add(where, f"{part.opaque_kind} payloads are not part of this format")
                elif own and part.opaque:
                    if pending is not None:
                        parts.append({"text": "", "thoughtSignature": pending})
                    pending = part.opaque
                continue
            wire: dict[str, Any] | None = None
            if isinstance(part, TextPart):
                wire = {"text": part.text} if part.text else None
            elif isinstance(part, ToolCallPart):
                wire = {"functionCall": _function_call_out(part, where, loss)}
            elif isinstance(part, (ImagePart, FilePart)):
                wire = _media(part, where, loss)
            elif isinstance(part, RefusalPart):
                loss.add(where, "refusal sent as text", "approximated")
                wire = {"text": part.text} if part.text else None
            else:
                loss.add(where, "tool results belong in user turns")
            if wire is None:
                continue
            if pending is not None:
                wire["thoughtSignature"] = pending
                pending = None
            parts.append(wire)
        if pending is not None:
            last = parts[-1] if parts else None
            if last is not None and last.get("thought") is True and "thoughtSignature" not in last:
                last["thoughtSignature"] = pending
            else:
                parts.append({"text": "", "thoughtSignature": pending})
        return parts

    def _user_parts(
        self, message: Message, calls: dict[str, ToolCallPart], field: str, loss: _Losses
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for index, part in enumerate(message.content):
            where = f"{field}.content[{index}]"
            wire: dict[str, Any] | None = None
            if isinstance(part, TextPart):
                wire = {"text": part.text} if part.text else None
            elif isinstance(part, (ImagePart, FilePart)):
                wire = _media(part, where, loss)
            elif isinstance(part, ToolResultPart):
                wire = {"functionResponse": _function_response(part, calls, where, loss)}
            elif isinstance(part, RefusalPart):
                loss.add(where, "refusal sent as text", "approximated")
                wire = {"text": part.text} if part.text else None
            else:
                loss.add(where, f"{part.type} parts belong in model turns")
            if wire is not None:
                parts.append(wire)
        return parts

    def _generation_config(self, request: Request, loss: _Losses) -> dict[str, Any]:
        config: dict[str, Any] = {}
        sampling = request.sampling
        if sampling.temperature is not None:
            config["temperature"] = sampling.temperature
        if sampling.top_p is not None:
            config["topP"] = sampling.top_p
        if sampling.top_k is not None:
            config["topK"] = sampling.top_k
        if sampling.seed is not None:
            config["seed"] = sampling.seed
        if sampling.stop:
            stops = list(sampling.stop)
            if len(stops) > MAX_STOP_SEQUENCES:
                loss.add(
                    "sampling.stop",
                    f"only {MAX_STOP_SEQUENCES} stop sequences are allowed; the rest are dropped",
                    "approximated",
                )
                stops = stops[:MAX_STOP_SEQUENCES]
            config["stopSequences"] = stops
        if sampling.presence_penalty is not None:
            config["presencePenalty"] = sampling.presence_penalty
        if sampling.frequency_penalty is not None:
            config["frequencyPenalty"] = sampling.frequency_penalty
        if request.max_output_tokens is not None:
            config["maxOutputTokens"] = request.max_output_tokens
        response_format = request.response_format
        if response_format is not None and response_format.type != "text":
            config["responseMimeType"] = "application/json"
            if response_format.type == "json_schema" and response_format.json_schema is not None:
                config["responseJsonSchema"] = response_format.json_schema
        extension = _dict(_dict(request.extensions.get(self.format)).get("body"))
        hint = _dict(_dict(extension.get("generationConfig")).get("thinkingConfig"))
        include = hint.get("includeThoughts")
        thinking = _thinking_config(request, include if isinstance(include, bool) else None, loss)
        if thinking:
            config["thinkingConfig"] = thinking
        return config

    def _decode_model_parts(
        self, raw_parts: list[Any], origin: Origin, turn: int, loss: _Losses, where: str
    ) -> list[Part]:
        parts: list[Part] = []
        calls = 0
        for index, raw in enumerate(raw_parts):
            part = _dict(raw)
            reported = _note_extras(part, PART_FIELDS, loss, f"{where}.parts[{index}]")
            signature = _str(_get(part, "thoughtSignature"))
            if part.get("thought") is True:
                text = _str(part.get("text"))
                if text or signature:
                    parts.append(
                        ReasoningPart(
                            text=text or None,
                            opaque=signature,
                            opaque_kind="signature" if signature else None,
                            origin=origin,
                        )
                    )
                continue
            if signature:
                parts.append(
                    ReasoningPart(opaque=signature, opaque_kind="signature", origin=origin)
                )
            call = _get(part, "functionCall")
            if isinstance(call, dict):
                parts.append(_function_call(call, turn, calls))
                calls += 1
                continue
            text = _str(part.get("text"))
            if text is not None:
                if text:
                    parts.append(TextPart(text=text))
                continue
            media = _decode_media(part)
            if media is not None:
                parts.append(media)
            elif not reported:
                loss.add(f"{where}.parts[{index}]", f"part {sorted(part)} is not carried by the IR")
        return parts

    def _decode_contents(self, contents: list[Any], loss: _Losses) -> list[Message]:
        out: list[Message] = []
        unanswered: list[ToolCallPart] = []
        for turn, raw in enumerate(contents):
            content = _dict(raw)
            raw_parts = _list(content.get("parts"))
            where = f"contents[{turn}]"
            if content.get("role") == "model":
                origin = Origin(format=self.format)
                parts = self._decode_model_parts(raw_parts, origin, turn, loss, where)
                unanswered = [part for part in parts if isinstance(part, ToolCallPart)]
                out.append(Message(role="assistant", content=parts))
                continue
            pending: list[Part] = []
            for index, item in enumerate(raw_parts):
                part = _dict(item)
                reported = _note_extras(part, PART_FIELDS, loss, f"{where}.parts[{index}]")
                response = _get(part, "functionResponse")
                if isinstance(response, dict):
                    if pending:
                        out.append(Message(role="user", content=pending))
                        pending = []
                    result = _tool_result(response, unanswered, turn, index, loss, where)
                    out.append(Message(role="tool", content=[result]))
                    continue
                text = _str(part.get("text"))
                media = _decode_media(part)
                if text:
                    pending.append(TextPart(text=text))
                elif media is not None:
                    pending.append(media)
                elif text is None and not reported:
                    loss.add(f"{where}.parts[{index}]", f"part {sorted(part)} is not carried")
            if pending:
                out.append(Message(role="user", content=pending))
        return out


# --------------------------------------------------------------------------------------------
# Request encoding helpers
# --------------------------------------------------------------------------------------------


def _model_name(model: str) -> str:
    return model.removeprefix("models/")


def _declaration(tool: ToolSpec, loss: _Losses) -> dict[str, Any]:
    out: dict[str, Any] = {"name": tool.name}
    if tool.description:
        out["description"] = tool.description
    out["parametersJsonSchema"] = tool.parameters
    if tool.strict:
        loss.add(f"tools.{tool.name}.strict", "per-function strict mode is not carried")
    return out


def _tool_config(request: Request, loss: _Losses) -> dict[str, Any] | None:
    if request.parallel_tool_calls is False:
        loss.add("parallel_tool_calls", "the generate-content format cannot forbid parallel calls")
    choice = request.tool_choice
    if choice is None:
        return None
    if not request.tools:
        if choice == "required" or isinstance(choice, NamedToolChoice):
            loss.add("tool_choice", "a forced tool choice needs tools; none are defined")
        return None
    config: dict[str, Any]
    if isinstance(choice, NamedToolChoice):
        config = {"mode": "ANY", "allowedFunctionNames": [choice.name]}
    else:
        config = {"mode": {"auto": "AUTO", "none": "NONE", "required": "ANY"}[choice]}
    return {"functionCallingConfig": config}


def _thinking_config(
    request: Request, include_hint: bool | None, loss: _Losses
) -> dict[str, Any] | None:
    effort, budget = request.effort, request.budget_tokens
    config: dict[str, Any] = {}
    if budget is not None:
        config["thinkingBudget"] = budget
        if effort is not None:
            loss.add(
                "effort",
                "thinkingBudget and thinkingLevel cannot be sent together; only the budget is sent",
                "approximated",
            )
    elif effort is not None:
        config["thinkingLevel"] = THINKING_LEVELS[effort]
        if effort == "none":
            detail = "thinking cannot be switched off with thinkingLevel; sent MINIMAL"
            loss.add("effort", detail, "approximated")
        elif effort in ("xhigh", "max"):
            loss.add("effort", f"{effort!r} is above the highest level; sent HIGH", "approximated")
    if not config:
        return None
    thinking_off = effort == "none" or budget == 0
    include = include_hint if include_hint is not None else not thinking_off
    if include or include_hint is False:
        config["includeThoughts"] = include
    return config


def _function_call_out(part: ToolCallPart, where: str, loss: _Losses) -> dict[str, Any]:
    call: dict[str, Any] = {} if part.synthetic else {"id": part.id}
    call["name"] = part.name
    call["args"] = _arguments(part, where, loss)
    return call


def _arguments(part: ToolCallPart, where: str, loss: _Losses) -> dict[str, Any]:
    try:
        return part.parsed_arguments()
    except ValueError:
        loss.add(f"{where}.arguments", "arguments are not a JSON object; sent {}")
        return {}


def _function_response(
    part: ToolResultPart, calls: dict[str, ToolCallPart], where: str, loss: _Losses
) -> dict[str, Any]:
    call = calls.get(part.call_id)
    name = part.name or (call.name if call is not None else None)
    if not name:
        loss.add(f"{where}.name", "function responses need a name; sent 'unknown'", "approximated")
        name = "unknown"
    out: dict[str, Any] = {} if call is not None and call.synthetic else {"id": part.call_id}
    out["name"] = name
    out["response"] = _response_object(part.text(), part.is_error)
    media: list[dict[str, Any]] = []
    for index, item in enumerate(part.content):
        if isinstance(item, ImagePart):
            blob = _media(item, f"{where}.content[{index}]", loss, inline_only=True)
            if blob is not None:
                media.append(blob)
    if media:
        out["parts"] = media
    return out


def _response_object(text: str, is_error: bool) -> dict[str, Any]:
    """`{"result": text}`, `{"error": text}`, or the tool's own JSON object when `text` is
    exactly its compact serialization (and would not read back as a wrapper)."""
    value = _json_object(text)
    if value is not None and len(value) == 1:
        ((key, inner),) = value.items()
        if key == "error" or (key == "result" and isinstance(inner, str)):
            value = None
    if is_error:
        return {"error": text if value is None else value}
    return {"result": text} if value is None else value


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if isinstance(value, dict) and _compact(value) == text:
        return value
    return None


def _media(
    part: ImagePart | FilePart, where: str, loss: _Losses, *, inline_only: bool = False
) -> dict[str, Any] | None:
    if isinstance(part, ImagePart) and part.detail:
        loss.add(f"{where}.detail", "no per-image detail setting is sent")
    name = part.name if isinstance(part, FilePart) else None
    data, media_type, url = part.data, part.media_type, part.url
    if data is None and url and url.startswith("data:"):
        match = _DATA_URI_RE.match(url)
        if match is None:
            loss.add(where, "only base64 data URIs can be sent")
            return None
        data, media_type = url[match.end() :], match.group("media") or media_type
    if data is not None:
        if not media_type:
            media_type = _sniff_image(data) if isinstance(part, ImagePart) else None
        if not media_type:
            loss.add(where, "media type unknown; sent as application/octet-stream", "approximated")
            media_type = "application/octet-stream"
        blob: dict[str, Any] = {"mimeType": media_type, "data": data}
        if name:
            blob["displayName"] = name
        return {"inlineData": blob}
    uri = url or (part.file_id if isinstance(part, FilePart) else None)
    if uri is None:
        loss.add(where, "media part has no data, URI or file ID")
        return None
    if inline_only:
        loss.add(where, "function responses carry inline media only")
        return None
    if isinstance(part, FilePart) and not url:
        loss.add(where, "file ID sent as fileUri", "approximated")
    file_data: dict[str, Any] = {"fileUri": uri}
    if media_type:
        file_data["mimeType"] = media_type
    if name:
        file_data["displayName"] = name
    return {"fileData": file_data}


def _apply_extensions(
    extensions: dict[str, Any], fmt: str, body: dict[str, Any], loss: _Losses
) -> None:
    for name, value in extensions.items():
        if name != fmt:
            loss.add(f"extensions.{name}", f"extensions for {name!r} are not sent to {fmt!r}")
            continue
        _merge_missing(body, _dict(_dict(value).get("body")))


def _merge_missing(target: dict[str, Any], extra: dict[str, Any]) -> None:
    """Adds `extra` fields missing from `target`; dicts merge and lists (built-in tools) extend."""
    for key, value in extra.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge_missing(target[key], value)
        elif isinstance(target[key], list) and isinstance(value, list):
            target[key] = [*target[key], *copy.deepcopy(value)]


# --------------------------------------------------------------------------------------------
# Decoding helpers
# --------------------------------------------------------------------------------------------


def _function_call(call: dict[str, Any], turn: int, index: int) -> ToolCallPart:
    call_id = _str(call.get("id"))
    args = call.get("args")
    return ToolCallPart(
        id=call_id or synthetic_call_id(turn, index),
        name=_str(call.get("name")) or "",
        arguments=_compact(args if isinstance(args, dict) else {}),
        synthetic=not call_id,
    )


def _tool_result(
    response: dict[str, Any],
    unanswered: list[ToolCallPart],
    turn: int,
    index: int,
    loss: _Losses,
    where: str,
) -> ToolResultPart:
    """Matches a function response to its call by ID, else by name in call order."""
    _note_extras(response, FUNCTION_RESPONSE_FIELDS, loss, f"{where}.parts[{index}]")
    response_id = _str(response.get("id"))
    name = _str(response.get("name"))
    if response_id:
        call = next((c for c in unanswered if c.id == response_id), None)
    else:
        call = next((c for c in unanswered if c.name == name), None)
    if call is not None:
        unanswered.remove(call)
    call_id = response_id or (call.id if call is not None else synthetic_call_id(turn, index))
    text, is_error = _decode_response_object(_dict(response.get("response")))
    content: list[TextPart | ImagePart] = [TextPart(text=text)] if text else []
    for j, raw in enumerate(_list(response.get("parts"))):
        media = _decode_media(_dict(raw))
        if isinstance(media, ImagePart):
            content.append(media)
        else:
            loss.add(f"{where}.parts[{index}].parts[{j}]", "non-image response parts dropped")
    return ToolResultPart(
        call_id=call_id,
        name=name or (call.name if call is not None else None),
        content=content,
        is_error=is_error,
    )


def _note_extras(data: dict[str, Any], known: frozenset[str], loss: _Losses, where: str) -> bool:
    """Reports non-null fields of `data` the IR does not carry; True when something was reported."""
    extras = sorted(
        key for key, value in data.items() if _camel(key) not in known and value is not None
    )
    if extras:
        loss.add(where, f"fields {extras} are not carried")
    return bool(extras)


def _decode_response_object(response: dict[str, Any]) -> tuple[str, bool]:
    if len(response) == 1 and "error" in response:
        value = response["error"]
        return (value if isinstance(value, str) else _compact(value)), True
    if len(response) == 1 and isinstance(response.get("result"), str):
        return response["result"], False
    return (_compact(response) if response else ""), False


def _decode_media(part: dict[str, Any]) -> Part | None:
    inline = _get(part, "inlineData")
    if isinstance(inline, dict):
        media_type = _str(_get(inline, "mimeType"))
        name = _str(_get(inline, "displayName"))
        data = _str(inline.get("data"))
        if media_type and media_type.startswith("image/") and not name:
            return ImagePart(data=data, media_type=media_type)
        return FilePart(data=data, media_type=media_type, name=name)
    file_data = _get(part, "fileData")
    if isinstance(file_data, dict):
        media_type = _str(_get(file_data, "mimeType"))
        name = _str(_get(file_data, "displayName"))
        uri = _str(_get(file_data, "fileUri"))
        if media_type and media_type.startswith("image/") and not name:
            return ImagePart(url=uri, media_type=media_type)
        return FilePart(url=uri, media_type=media_type, name=name)
    return None


def _decode_tools(raw: Any, stash: dict[str, Any]) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for item in _list(raw):
        for key, value in _dict(item).items():
            if _camel(key) != "functionDeclarations":  # built-in tools stay as sent
                stash.setdefault("tools", []).append({_camel(key): copy.deepcopy(value)})
                continue
            for declaration in _list(value):
                specs.append(_decode_declaration(_dict(declaration)))
    return specs


def _decode_declaration(declaration: dict[str, Any]) -> ToolSpec:
    schema = _get(declaration, "parametersJsonSchema")
    if not isinstance(schema, dict):
        schema = _get(declaration, "parameters")
        schema = _lower_types(schema) if isinstance(schema, dict) else None
    return ToolSpec(
        name=_str(declaration.get("name")) or "",
        description=_str(declaration.get("description")),
        parameters=schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
    )


def _lower_types(schema: Any) -> Any:
    """OpenAPI-subset schemas spell types in capitals (`OBJECT`); JSON Schema uses lowercase."""
    if isinstance(schema, dict):
        return {
            key: value.lower() if key == "type" and isinstance(value, str) else _lower_types(value)
            for key, value in schema.items()
        }
    if isinstance(schema, list):
        return [_lower_types(item) for item in schema]
    return schema


def _decode_tool_config(config: dict[str, Any], stash: dict[str, Any]) -> ToolChoice | None:
    for key, value in config.items():
        if _camel(key) != "functionCallingConfig":
            stash.setdefault("toolConfig", {})[_camel(key)] = copy.deepcopy(value)
    calling = _dict(_get(config, "functionCallingConfig"))
    mode = (_str(calling.get("mode")) or "").upper()
    names = [n for n in _list(_get(calling, "allowedFunctionNames")) if isinstance(n, str)]
    if mode == "AUTO" and not names:
        return "auto"
    if mode == "NONE":
        return "none"
    if mode == "ANY" and len(names) == 1:
        return NamedToolChoice(name=names[0])
    if mode == "ANY":
        if names:  # a subset of functions: the IR forces "any tool", the list stays as sent
            stash.setdefault("toolConfig", {})["functionCallingConfig"] = {
                "allowedFunctionNames": names
            }
        return "required"
    if calling:
        stash.setdefault("toolConfig", {})["functionCallingConfig"] = copy.deepcopy(calling)
    return None


def _decode_response_format(config: dict[str, Any], stash: dict[str, Any]) -> ResponseFormat | None:
    response_format = _dict(_get(config, "responseFormat"))
    text_format = _dict(response_format.get("text"))
    others = {k: copy.deepcopy(v) for k, v in response_format.items() if k != "text"}
    if others:
        stash["responseFormat"] = others
    mime = _str(text_format.get("mimeType")) or _str(_get(config, "responseMimeType")) or ""
    mime = mime.lower().replace("_", "/")
    schema = text_format.get("schema")
    for key in ("responseJsonSchema", "_responseJsonSchema", "responseSchema"):
        if not isinstance(schema, dict):
            schema = _get(config, key) if key != "_responseJsonSchema" else config.get(key)
    if mime == "application/json":
        if isinstance(schema, dict):
            return ResponseFormat(type="json_schema", json_schema=schema)
        return ResponseFormat(type="json_object")
    if mime and mime != "text/plain":  # for example text/x.enum: kept as sent
        for key in ("responseMimeType", "responseSchema", "responseJsonSchema"):
            if _get(config, key) is not None:
                stash[key] = copy.deepcopy(_get(config, key))
    return None


def _decode_thinking(
    thinking: dict[str, Any], stash: dict[str, Any]
) -> tuple[Effort | None, int | None]:
    extras: dict[str, Any] = {}
    for key, value in thinking.items():
        if _camel(key) not in ("thinkingLevel", "thinkingBudget"):
            extras[_camel(key)] = copy.deepcopy(value)
    level = _str(_get(thinking, "thinkingLevel"))
    effort = LEVEL_EFFORTS.get(level.upper()) if level else None
    if level and effort is None:
        extras["thinkingLevel"] = level
    if extras:
        stash["thinkingConfig"] = extras
    return effort, _int(_get(thinking, "thinkingBudget"))


def _part_deltas(part: dict[str, Any], state: StreamState) -> list[Delta]:
    signature = _str(_get(part, "thoughtSignature"))
    if part.get("thought") is True:
        text = _str(part.get("text")) or ""
        if not text and not signature:
            return []
        kind: Literal["signature"] | None = "signature" if signature else None
        return [ReasoningDelta(text=text, opaque=signature, opaque_kind=kind)]
    out: list[Delta] = []
    if signature:
        out.append(ReasoningDelta(text="", opaque=signature, opaque_kind="signature"))
    call = _get(part, "functionCall")
    if isinstance(call, dict):
        index = state.tool_index
        state.tool_index += 1
        state.data["calls"] = True
        tool = _function_call(call, state.data.get("turn", 0), index)
        out.append(
            ToolCallStartDelta(index=index, id=tool.id, name=tool.name, synthetic=tool.synthetic)
        )
        out.append(ToolCallArgsDelta(index=index, delta=tool.arguments))
        out.append(ToolCallEndDelta(index=index))
        return out
    plain = _str(part.get("text"))
    if plain:
        out.append(TextDelta(text=plain))
    return out


def _error_delta(error: dict[str, Any]) -> ErrorDelta:
    name = _str(error.get("status"))
    status = _int(error.get("code"))
    kind = STATUS_KINDS.get(name or "", kind_for_status(status) if status else "server")
    message = _str(error.get("message")) or "error in the stream"
    return ErrorDelta(kind=kind, message=message, status=status)


def _retry_delay(details: Any) -> float | None:
    for detail in _list(details):
        delay = _str(_dict(detail).get("retryDelay"))
        if delay and delay.endswith("s"):
            try:
                return max(0.0, float(delay[:-1]))
            except ValueError:
                continue
    return None


def _finish(raw: str | None, has_calls: bool) -> FinishReason:
    if raw is None or raw == "STOP":
        return "tool_calls" if has_calls else "stop"
    if raw == "MAX_TOKENS":
        return "length"
    if raw in CONTENT_FILTER_FINISHES:
        return "content_filter"
    return "other"


def _wire_finish(reason: FinishReason, raw: str | None, has_calls: bool) -> str:
    if raw and raw.isupper() and _finish(raw, has_calls) == reason:
        return raw
    return FINISH_TO_WIRE[reason]


def _usage(raw: Any) -> Usage | None:
    meta = _dict(raw)
    if not meta:
        return None
    prompt = _int(_get(meta, "promptTokenCount"))
    tool_prompt = _int(_get(meta, "toolUsePromptTokenCount"))
    candidates = _int(_get(meta, "candidatesTokenCount"))
    thoughts = _int(_get(meta, "thoughtsTokenCount"))
    no_input = prompt is None and tool_prompt is None
    no_output = candidates is None and thoughts is None
    return Usage(
        input_tokens=None if no_input else (prompt or 0) + (tool_prompt or 0),
        output_tokens=None if no_output else (candidates or 0) + (thoughts or 0),
        cached_input_tokens=_int(_get(meta, "cachedContentTokenCount")),
        reasoning_tokens=thoughts,
        raw=dict(meta),
    )


def _wire_usage(usage: Usage | None) -> dict[str, Any] | None:
    if usage is None:
        return None
    meta: dict[str, Any] = {}
    if usage.input_tokens is not None:
        meta["promptTokenCount"] = usage.input_tokens
    if usage.cached_input_tokens is not None:
        meta["cachedContentTokenCount"] = usage.cached_input_tokens
    if usage.output_tokens is not None:
        meta["candidatesTokenCount"] = max(0, usage.output_tokens - (usage.reasoning_tokens or 0))
    if usage.reasoning_tokens is not None:
        meta["thoughtsTokenCount"] = usage.reasoning_tokens
    if usage.total_tokens is not None:
        meta["totalTokenCount"] = usage.total_tokens
    return meta


# --------------------------------------------------------------------------------------------
# Stream encoding (server side)
# --------------------------------------------------------------------------------------------


class _ChunkWriter:
    """IR deltas -> one response chunk per text or thought delta and per completed call (the
    format sends whole function calls); the last chunk carries the finish reason and usage."""

    def __init__(self) -> None:
        self.events: list[StreamEvent] = []
        self.id: str | None = None
        self.model: str | None = None
        self.usage: Usage | None = None
        self.signature: str | None = None
        self.tools: dict[int, ToolCallPart] = {}
        self.args: dict[int, list[str]] = {}
        self.has_calls = False
        self.closed = False

    def drain(self) -> list[StreamEvent]:
        events, self.events = self.events, []
        return events

    def chunk(self, parts: list[dict[str, Any]], finish: str | None = None) -> None:
        candidate: dict[str, Any] = {"index": 0}
        if parts:
            candidate["content"] = {"role": "model", "parts": parts}
        if finish is not None:
            candidate["finishReason"] = finish
        payload: dict[str, Any] = {"candidates": [candidate]}
        if finish is not None:
            usage = _wire_usage(self.usage)
            if usage:
                payload["usageMetadata"] = usage
        if self.model:
            payload["modelVersion"] = self.model
        if self.id:
            payload["responseId"] = self.id
        self.events.append(sse_json(payload))

    def part(self, part: dict[str, Any]) -> None:
        if self.signature is not None:
            part["thoughtSignature"] = self.signature
            self.signature = None
        self.chunk([part])

    def feed(self, delta: Delta) -> None:
        if self.closed:
            return
        if isinstance(delta, StartDelta):
            self.id = delta.id or self.id
            self.model = delta.model or self.model
        elif isinstance(delta, UsageDelta):
            self.usage = merge_usage(self.usage, delta.usage)
        elif isinstance(delta, TextDelta):
            if delta.text:
                self.part({"text": delta.text})
        elif isinstance(delta, ReasoningDelta):
            if delta.text:
                self.chunk([{"text": delta.text, "thought": True}])
            if delta.opaque and delta.opaque_kind == "signature":
                if self.signature is not None:
                    self.part({"text": ""})
                self.signature = delta.opaque
        elif isinstance(delta, ToolCallStartDelta):
            self.tools[delta.index] = ToolCallPart(
                id=delta.id, name=delta.name, synthetic=delta.synthetic
            )
            self.args[delta.index] = []
        elif isinstance(delta, ToolCallArgsDelta):
            self.args.setdefault(delta.index, []).append(delta.delta)
        elif isinstance(delta, ToolCallEndDelta):
            self.emit_tool(delta.index)
        elif isinstance(delta, FinishDelta):
            for index in list(self.tools):
                self.emit_tool(index)
            parts = [{"text": "", "thoughtSignature": self.signature}] if self.signature else []
            self.signature = None
            self.chunk(parts, _wire_finish(delta.reason, delta.raw, self.has_calls))
            self.closed = True
        elif isinstance(delta, ErrorDelta):
            status, name = KIND_STATUS.get(delta.kind, (500, "INTERNAL"))
            error = {"code": delta.status or status, "message": delta.message, "status": name}
            self.events.append(sse_json({"error": error}))
            self.closed = True

    def emit_tool(self, index: int) -> None:
        tool = self.tools.pop(index, None)
        if tool is None:
            return
        tool.arguments = "".join(self.args.pop(index, [])) or "{}"
        try:
            args = tool.parsed_arguments()
        except ValueError:
            args = {}
        call: dict[str, Any] = {} if tool.synthetic else {"id": tool.id}
        call.update(name=tool.name, args=args)
        self.has_calls = True
        self.part({"functionCall": call})

    def end(self) -> None:
        if not self.closed:
            message = "the upstream stream ended before it finished"
            error = {"code": 500, "message": message, "status": "INTERNAL"}
            self.events.append(sse_json({"error": error}))
            self.closed = True


# --------------------------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------------------------


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    if not head or not rest:
        return key
    return head + "".join(word[:1].upper() + word[1:] for word in rest)


def _get(data: dict[str, Any], camel: str) -> Any:
    """A field by its camelCase name or its snake_case spelling (both are accepted on input)."""
    if camel in data:
        return data[camel]
    return data.get(_CAMEL_RE.sub(r"_\1", camel).lower())


def _turn_number(response_id: str | None) -> int:
    """Stable turn number for synthetic tool-call IDs, so IDs from different replies differ."""
    return zlib.crc32(response_id.encode("utf-8")) % 100_000 if response_id else 0


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _sniff_image(data: str) -> str | None:
    prefixes = {
        "iVBOR": "image/png",
        "/9j/": "image/jpeg",
        "R0lGOD": "image/gif",
        "UklGR": "image/webp",
    }
    for prefix, media_type in prefixes.items():
        if data.startswith(prefix):
            return media_type
    return None


def _header(headers: dict[str, str] | None, name: str) -> str | None:
    for key, value in (headers or {}).items():
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
