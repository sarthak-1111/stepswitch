"""llama.cpp server native API codec: `POST /completion` with SSE streams (ADR 0003, ADR 0008).

`/completion` takes a prompt string and returns raw text. The codec renders the conversation
itself (`stepswitch.compat.templates`) unless the request carries a prompt rendered earlier (for
example by the server's `/apply-template`), and parses a leading `<think>` block and tool calls
back out of the text with the catalog parsers (`stepswitch.compat.toolcall`). It also builds the
pure calls for the server's other native endpoints: tokenize, detokenize, apply-template, props,
health and slots.

Wire facts (docs/RESEARCH.md, "llama.cpp native API"; re-checked on 2026-10-16 against the
v0.7.0 `tools/server/README.md` and the server sources at that tag):

* `tokens_evaluated` is the whole prompt, cached tokens included: the server reports the same
  value as chat-completions `prompt_tokens`, with `cached_tokens` as a subset of it.
  `timings.cache_n` counts prompt tokens reused from the cache and `timings.prompt_n` the ones
  processed. `tokens_cached` is the slot's cache size after the request (prompt plus generated
  tokens), so it is kept in `Usage.raw` only.
* Streams are `data: {json}` events with `content` and `stop: false`. The final event has
  `stop: true`, an empty `content` (the text was already streamed), `stop_type`, the counts and
  `timings`. There is no `[DONE]` line; an error mid-stream is `data: {"error": {...}}`.
* `stop_type` is `eos`, `word` (a stop string matched), `limit` (`n_predict` reached) or `none`.
* Errors are `{"error": {"code", "message", "type"}}`; the types are listed in `ERROR_KINDS`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, ClassVar, Literal

from stepswitch.compat import templates
from stepswitch.compat.codecs.base import (
    Codec,
    CodecOptions,
    Framing,
    HttpCall,
    StreamState,
    refusal_as_text,
)
from stepswitch.compat.errors import ErrorKind, ModelError, kind_for_status
from stepswitch.compat.framing import StreamEvent, sse_json
from stepswitch.compat.ir import (
    ConversionError,
    ConversionReport,
    Delta,
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
    Usage,
    UsageDelta,
)
from stepswitch.compat.stream import merge_usage
from stepswitch.compat.toolcall import parsers
from stepswitch.compat.toolcall.streaming import StreamingToolCallExtractor, extract_calls

PATH = "/completion"
THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

SAMPLING_FIELDS = ("temperature", "top_k", "top_p", "seed", "presence_penalty", "frequency_penalty")
# /completion fields this codec maps; any other field of a decoded request is kept verbatim in
# `extensions["params"]`, which `encode_request` sends back unchanged.
KNOWN_FIELDS = frozenset(
    {
        "prompt",
        "model",
        "n_predict",
        "stream",
        "stop",
        "json_schema",
        "grammar",
        "id_slot",
        "cache_prompt",
        *SAMPLING_FIELDS,
    }
)
USAGE_FIELDS = ("tokens_evaluated", "tokens_predicted", "tokens_cached", "timings")

ERROR_KINDS: dict[str, ErrorKind] = {
    "invalid_request_error": "bad_request",
    "authentication_error": "authentication",
    "permission_error": "permission",
    "not_found_error": "not_found",
    "exceed_context_size_error": "too_large",
    "server_error": "server",
    "not_supported_error": "unsupported",
    "unavailable_error": "overloaded",  # 503 "Loading model"
}
# Error kind -> (error type, HTTP status) for errors this codec writes as a server.
ERROR_TYPES: dict[str, tuple[str, int]] = {
    "bad_request": ("invalid_request_error", 400),
    "authentication": ("authentication_error", 401),
    "permission": ("permission_error", 403),
    "not_found": ("not_found_error", 404),
    "too_large": ("exceed_context_size_error", 400),
    "unsupported": ("not_supported_error", 501),
    "rate_limit": ("unavailable_error", 429),
    "overloaded": ("unavailable_error", 503),
}

SlotAction = Literal["save", "restore", "erase"]


class LlamaCppCodec(Codec):
    key: ClassVar[str] = "llamacpp"
    format: ClassVar[str] = "llamacpp-native"
    framing: ClassVar[Framing] = "sse"

    def __init__(self, options: CodecOptions | None = None) -> None:
        super().__init__(options)
        # What decoding needs to know about the last encoded request (a codec instance serves
        # one client). `_tools` is None until a request is encoded: tool calls are then parsed
        # whenever the text contains them.
        self._tools: bool | None = None
        self._turn = 0
        self._model: str | None = None
        self._open_think = False

    # ---- client side ----------------------------------------------------------------------

    def encode_request(self, request: Request, report: ConversionReport) -> HttpCall:
        rendered = self._prepare(request, report)
        prompt = templates.prompt_for(rendered)
        body: dict[str, Any] = {"prompt": prompt}
        if request.model:
            body["model"] = request.model  # routes the request in router mode
        if request.max_output_tokens is not None:
            body["n_predict"] = request.max_output_tokens
        for key in SAMPLING_FIELDS:
            value = getattr(request.sampling, key)
            if value is not None:
                body[key] = value
        stop = list(request.sampling.stop)
        if request.prompt is None:
            stop.extend(templates.CHATML_STOP)
        if stop:
            body["stop"] = list(dict.fromkeys(stop))
        schema = self._json_schema(request.response_format, report)
        if schema is not None:
            body["json_schema"] = schema
        for key in ("grammar", "id_slot"):
            if key in request.extensions:
                body[key] = request.extensions[key]
        body["cache_prompt"] = True
        params = request.extensions.get("params")
        if isinstance(params, dict):
            body.update({k: v for k, v in params.items() if k not in ("prompt", "stream")})
        body["stream"] = request.stream
        self._tools = bool(request.tools) and request.tool_choice != "none"
        self._turn = sum(1 for message in request.messages if message.role == "assistant")
        self._model = request.model or None
        # A template whose generation prompt opens a think block starts the answer inside it.
        self._open_think = prompt.rstrip().endswith(THINK_OPEN)
        return HttpCall("POST", PATH, json=body, stream=request.stream)

    def decode_response(self, body: Any) -> Response:
        if not isinstance(body, dict):
            raise ModelError("invalid_response", "/completion response is not an object", raw=body)
        model = _text(body.get("model")) or self._model
        reasoning, text = split_think(_text(body.get("content")), self._open_think)
        parts: list[Part] = []
        if reasoning:
            parts.append(
                ReasoningPart(text=reasoning, origin=Origin(format=self.format, model=model))
            )
        calls: list[ToolCallPart] = []
        if self._tools is not False and text:
            text, calls = extract_calls(text, self._syntax, self._turn)
        if text:
            parts.append(TextPart(text=text))
        parts.extend(calls)
        stop_type = _text(body.get("stop_type")) or None
        return Response(
            model=model,
            message=Message(role="assistant", content=parts),
            finish_reason=_finish(stop_type, bool(calls)),
            raw_finish_reason=stop_type,
            usage=_usage(body),
        )

    def decode_stream_event(self, event: StreamEvent, state: StreamState) -> list[Delta]:
        if state.finished:
            return []
        try:
            chunk = event.json()
        except json.JSONDecodeError:
            state.finished = True
            return [
                ErrorDelta(kind="invalid_response", message=f"bad SSE data: {event.data[:200]}")
            ]
        if not isinstance(chunk, dict):
            state.finished = True
            return [ErrorDelta(kind="invalid_response", message="SSE data is not an object")]
        if chunk.get("error"):
            state.finished = True
            return [_error_delta(chunk["error"])]
        deltas: list[Delta] = []
        model = _text(chunk.get("model")) or None
        if not state.started:
            state.started = True
            state.data["model"] = model or self._model
            deltas.append(StartDelta(model=state.data["model"]))
            state.data["think"] = _ThinkSplitter(self._open_think)
            state.data["calls"] = (
                StreamingToolCallExtractor(self._syntax, turn=self._turn)
                if self._tools is not False
                else None
            )
        content = _text(chunk.get("content"))
        if content:
            deltas.extend(self._route(state, _splitter(state).feed(content)))
        if chunk.get("stop") is True:
            deltas.extend(self._flush(state))
            if model is not None and model != state.data.get("model"):
                # Only the final event names the model. Announce it: the assembler takes the
                # later model and encoders ignore a start that comes after the first one.
                deltas.append(StartDelta(model=model))
            usage = _usage(chunk)
            if usage is not None:
                deltas.append(UsageDelta(usage=usage))
            stop_type = _text(chunk.get("stop_type")) or None
            has_calls = bool(state.data.get("found"))
            deltas.append(FinishDelta(reason=_finish(stop_type, has_calls), raw=stop_type))
            state.finished = True
        return deltas

    def finish_stream(self, state: StreamState) -> list[Delta]:
        if state.finished:
            return []
        deltas: list[Delta] = self._flush(state) if state.started else []
        deltas.append(ErrorDelta(kind="server", message="/completion stream ended before stop"))
        return deltas

    def decode_error(
        self, status: int, body: Any, headers: dict[str, str] | None = None
    ) -> ModelError:
        error = super().decode_error(status, body, headers)
        detail = body.get("error") if isinstance(body, dict) else None
        if not isinstance(detail, dict):
            return error
        error_type = _text(detail.get("type")) or None
        kind = ERROR_KINDS.get(error_type or "", error.kind)
        return ModelError(
            kind,
            error.message,
            status=status,
            code=error_type,
            retry_after=error.retry_after,
            raw=body,
        )

    # ---- server side ----------------------------------------------------------------------

    def decode_request(
        self, body: Any, *, path: str = "", headers: dict[str, str] | None = None
    ) -> Request:
        if not isinstance(body, dict):
            raise ValueError("a /completion request must be a JSON object")
        prompt, images = _decode_prompt(body.get("prompt"))
        fields: dict[str, Any] = {key: body[key] for key in SAMPLING_FIELDS if key in body}
        if fields.get("seed") == -1:  # -1 asks for a random seed, the same as no seed
            del fields["seed"]
        stop = body.get("stop")
        if isinstance(stop, str):
            stop = [stop]
        if stop:
            fields["stop"] = list(stop)
        params = {key: value for key, value in body.items() if key not in KNOWN_FIELDS}
        max_output_tokens: int | None = None
        n_predict = _count(body.get("n_predict"))
        if n_predict is not None and n_predict >= 0:
            max_output_tokens = n_predict
        elif body.get("n_predict") not in (None, -1):  # -1 means no limit
            params["n_predict"] = body["n_predict"]
        if body.get("cache_prompt") is False:
            params["cache_prompt"] = False
        extensions: dict[str, Any] = {
            key: body[key] for key in ("grammar", "id_slot") if key in body
        }
        if params:
            extensions["params"] = params
        schema = body.get("json_schema")
        if schema is not None and not isinstance(schema, dict):
            raise ValueError("json_schema must be an object")
        stream = body.get("stream", False)
        if not isinstance(stream, bool):
            raise ValueError("stream must be a boolean")
        content: list[Part] = [TextPart(text=prompt), *images]
        return Request(
            model=_text(body.get("model")),
            messages=[Message(role="user", content=content)],
            prompt=prompt,
            response_format=(
                ResponseFormat(type="json_schema", json_schema=schema)
                if schema is not None
                else None
            ),
            max_output_tokens=max_output_tokens,
            sampling=Sampling.model_validate(fields),
            stream=stream,
            extensions=extensions,
        )

    def encode_response(self, response: Response) -> Any:
        content = _render_message(response.message, self._syntax)
        body: dict[str, Any] = {
            "index": 0,
            "content": content,
            "tokens": [],
            "stop": True,
            "model": response.model or "",
        }
        body.update(_usage_fields(response.usage))
        body["truncated"] = False
        body["stop_type"] = _stop_type(response.finish_reason, response.raw_finish_reason)
        body["stopping_word"] = ""
        return body

    def encode_stream(self, deltas: Iterable[Delta]) -> Iterator[StreamEvent]:
        syntax = self._syntax
        model = ""
        thinking = False
        pending: dict[int, tuple[str, list[str]]] = {}
        usage: Usage | None = None
        finish: FinishDelta | None = None

        def piece(text: str) -> StreamEvent:
            return sse_json({"index": 0, "content": text, "tokens": [], "stop": False})

        for delta in map(refusal_as_text, deltas):
            if isinstance(delta, StartDelta):
                model = delta.model or model
            elif isinstance(delta, ReasoningDelta):
                if delta.text:
                    yield piece(delta.text if thinking else f"{THINK_OPEN}\n{delta.text}")
                    thinking = True
            elif isinstance(delta, TextDelta):
                if delta.text:
                    if thinking:
                        yield piece(f"\n{THINK_CLOSE}\n\n")
                        thinking = False
                    yield piece(delta.text)
            elif isinstance(delta, ToolCallStartDelta):
                pending[delta.index] = (delta.name, [])
            elif isinstance(delta, ToolCallArgsDelta):
                pending.setdefault(delta.index, ("", []))[1].append(delta.delta)
            elif isinstance(delta, ToolCallEndDelta):
                if delta.index in pending:
                    if thinking:
                        yield piece(f"\n{THINK_CLOSE}\n\n")
                        thinking = False
                    name, args = pending.pop(delta.index)
                    yield piece(render_call(name, "".join(args), syntax))
            elif isinstance(delta, UsageDelta):
                usage = merge_usage(usage, delta.usage)
            elif isinstance(delta, FinishDelta):
                finish = delta
            elif isinstance(delta, ErrorDelta):
                error_type, default_status = ERROR_TYPES.get(delta.kind, ("server_error", 500))
                code = delta.status or default_status
                yield sse_json(
                    {"error": {"code": code, "message": delta.message, "type": error_type}}
                )
                return
        if thinking:
            yield piece(f"\n{THINK_CLOSE}\n\n")
        for index in sorted(pending):  # calls whose end never arrived
            name, args = pending[index]
            yield piece(render_call(name, "".join(args), syntax))
        final: dict[str, Any] = {
            "index": 0,
            "content": "",
            "tokens": [],
            "stop": True,
            "model": model,
        }
        final.update(_usage_fields(usage))
        final["truncated"] = False
        final["stop_type"] = _stop_type(
            finish.reason if finish else "stop", finish.raw if finish else None
        )
        final["stopping_word"] = ""
        yield sse_json(final)

    def encode_error(self, error: ModelError) -> tuple[int, Any]:
        error_type, default_status = ERROR_TYPES.get(error.kind, ("server_error", 500))
        status = error.status or default_status
        return status, {"error": {"code": status, "message": error.message, "type": error_type}}

    # ---- helper endpoints (pure: they build calls and parse bodies) -----------------------

    def tokenize_call(self, text: str, add_special: bool = False) -> HttpCall:
        """`POST /tokenize`; `add_special` inserts BOS and similar tokens (server default off)."""
        return HttpCall("POST", "/tokenize", json={"content": text, "add_special": add_special})

    @staticmethod
    def parse_tokens(body: Any) -> list[int]:
        """Token IDs from a `/tokenize` body (plain IDs, or `{id, piece}` with pieces on)."""
        tokens = body.get("tokens") if isinstance(body, dict) else None
        if not isinstance(tokens, list):
            raise ModelError("invalid_response", "tokenize response has no token list", raw=body)
        ids: list[int] = []
        for item in tokens:
            token = _count(item.get("id") if isinstance(item, dict) else item)
            if token is None:
                raise ModelError("invalid_response", f"bad token entry {item!r}", raw=body)
            ids.append(token)
        return ids

    def detokenize_call(self, tokens: Sequence[int]) -> HttpCall:
        return HttpCall("POST", "/detokenize", json={"tokens": list(tokens)})

    @staticmethod
    def parse_detokenize(body: Any) -> str:
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, str):
            raise ModelError("invalid_response", "detokenize response has no content", raw=body)
        return content

    def apply_template_call(
        self, request: Request, report: ConversionReport | None = None
    ) -> HttpCall:
        """`POST /apply-template`: the server renders `messages` (chat-completions shape) with
        the model's own template and returns `{"prompt": ...}` for use as `Request.prompt`."""
        report = report if report is not None else ConversionReport(target=self.format)
        body: dict[str, Any] = {"messages": self._chat_messages(request, report)}
        if request.tools and request.tool_choice != "none":
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]
        if request.effort is not None:
            body["reasoning_effort"] = request.effort  # passed to the template; "none" disables
        if request.model:
            body["model"] = request.model
        return HttpCall("POST", "/apply-template", json=body)

    @staticmethod
    def parse_apply_template(body: Any) -> str:
        prompt = body.get("prompt") if isinstance(body, dict) else None
        if not isinstance(prompt, str):
            raise ModelError("invalid_response", "apply-template response has no prompt", raw=body)
        return prompt

    def props_call(self, model: str | None = None) -> HttpCall:
        """`GET /props`; in router mode `model` selects the model."""
        return HttpCall("GET", "/props", params={"model": model} if model else {})

    def health_call(self) -> HttpCall:
        """`GET /health`: 200 `{"status": "ok"}`, or 503 while the model is loading."""
        return HttpCall("GET", "/health")

    def slot_call(self, slot_id: int, action: SlotAction, filename: str | None = None) -> HttpCall:
        """`POST /slots/{id}?action=save|restore|erase`; files live in `--slot-save-path`."""
        if action not in ("save", "restore", "erase"):
            raise ValueError(f"unknown slot action {action!r}")
        if slot_id < 0:
            raise ValueError("slot IDs are non-negative")
        if action != "erase" and not filename:
            raise ValueError(f"slot {action} needs a file name")
        if filename is not None and (
            "/" in filename or "\\" in filename or filename in (".", "..")
        ):
            raise ValueError("slot files are plain names inside the server's slot save path")
        body: dict[str, Any] = {"filename": filename} if filename else {}
        return HttpCall("POST", f"/slots/{slot_id}", json=body, params={"action": action})

    # ---- internals ------------------------------------------------------------------------

    @property
    def _syntax(self) -> str | None:
        """The configured tool-call syntax when the parser catalog has it, else None (detect by
        markers), so a profile naming a syntax that is not implemented yet still parses."""
        syntax = self.options.tool_parser
        return syntax if syntax in parsers.PARSERS else None

    def _loss(
        self, report: ConversionReport, field: str, detail: str, action: str = "dropped"
    ) -> None:
        if self.options.strict:
            report.add(field, detail, "rejected")
            raise ConversionError(f"{self.format}: {field}: {detail}")
        report.add(field, detail, action)

    def _prepare(self, request: Request, report: ConversionReport) -> Request:
        """Report what the prompt cannot carry; return the request to render."""
        choice = request.tool_choice
        if request.tools and (choice == "required" or isinstance(choice, NamedToolChoice)):
            self._loss(report, "tool_choice", "/completion cannot force a tool call")
        if request.tools and request.parallel_tool_calls is False:
            self._loss(report, "parallel_tool_calls", "/completion cannot limit tool calls")
        if request.effort is not None and request.prompt is None:
            # A prompt rendered by `/apply-template` carries the effort (`apply_template_call`).
            self._loss(
                report,
                "effort",
                "/completion has no effort control; render the prompt with the effort instead",
            )
        if request.budget_tokens is not None:
            self._loss(report, "budget_tokens", "/completion has no thinking budget")
        if request.prompt is not None:
            if request.tools and choice == "none":
                self._loss(
                    report,
                    "tool_choice",
                    "the prompt is rendered already; tool calls in the answer stay text",
                    "approximated",
                )
            return request  # rendered by the caller; messages and tools are not rendered again
        updates: dict[str, Any] = {}
        if request.tools and choice == "none":
            self._loss(
                report,
                "tool_choice",
                "tools are left out of the prompt so the model cannot call them",
                "approximated",
            )
            updates["tools"] = []
        for index, tool in enumerate(request.tools):
            if tool.strict:
                self._loss(
                    report, f"tools[{index}].strict", "rendered tool schemas are not enforced"
                )
        messages: list[Message] = []
        changed = False
        for index, message in enumerate(request.messages):
            fixed = self._check_message(message, f"messages[{index}]", report)
            changed = changed or fixed is not message
            messages.append(fixed)
        if changed:
            updates["messages"] = messages
        return request.model_copy(update=updates) if updates else request

    def _check_message(self, message: Message, path: str, report: ConversionReport) -> Message:
        if message.name and message.role != "tool":
            self._loss(report, f"{path}.name", "the rendered prompt has no participant names")
        if message.metadata:
            self._loss(report, f"{path}.metadata", "the rendered prompt carries no metadata")
        parts: list[Part] = []
        changed = False
        for index, part in enumerate(message.content):
            where = f"{path}.content[{index}]"
            if isinstance(part, ImagePart):
                self._loss(report, where, "images are not sent to /completion (placeholder only)")
            elif isinstance(part, FilePart):
                self._loss(report, where, "documents are not rendered into the prompt")
            elif isinstance(part, RefusalPart):
                self._loss(report, where, "refusals are not rendered into the prompt")
            elif isinstance(part, ReasoningPart):
                if part.text or part.opaque or part.summary:
                    self._loss(report, where, "reasoning is not replayed into the rendered prompt")
            elif isinstance(part, ToolResultPart):
                if part.is_error:
                    self._loss(
                        report, f"{where}.is_error", "rendered tool results have no error flag"
                    )
                if any(isinstance(item, ImagePart) for item in part.content):
                    self._loss(
                        report, f"{where}.content", "images in tool results are not rendered"
                    )
            elif isinstance(part, ToolCallPart):
                arguments, valid = _arguments_object(part.arguments)
                if not valid:
                    self._loss(
                        report,
                        f"{where}.arguments",
                        'arguments are not a JSON object; rendered as {"input": ...}',
                        "approximated",
                    )
                    part = part.model_copy(update={"arguments": json.dumps(arguments)})
                    changed = True
            parts.append(part)
        return message.model_copy(update={"content": parts}) if changed else message

    def _json_schema(
        self, response_format: ResponseFormat | None, report: ConversionReport
    ) -> dict[str, Any] | None:
        if response_format is None or response_format.type == "text":
            return None
        if response_format.type == "json_object":
            return {"type": "object"}
        if response_format.json_schema is None:
            self._loss(
                report,
                "response_format.json_schema",
                "no schema given; any JSON object is allowed",
                "approximated",
            )
            return {"type": "object"}
        return response_format.json_schema

    def _chat_messages(self, request: Request, report: ConversionReport) -> list[dict[str, Any]]:
        """Messages in chat-completions shape, for `/apply-template` (text only)."""
        out: list[dict[str, Any]] = []
        system = request.system_text()
        if system:
            out.append({"role": "system", "content": system})
        for index, message in enumerate(request.messages):
            path = f"messages[{index}]"
            texts: list[str] = []
            reasoning: list[str] = []
            calls: list[dict[str, Any]] = []
            has_results = False
            for position, part in enumerate(message.content):
                where = f"{path}.content[{position}]"
                if isinstance(part, TextPart):
                    texts.append(part.text)
                elif isinstance(part, ToolCallPart):
                    calls.append(
                        {
                            "id": part.id,
                            "type": "function",
                            "function": {"name": part.name, "arguments": part.arguments or "{}"},
                        }
                    )
                elif isinstance(part, ToolResultPart):
                    has_results = True
                    out.append(
                        {"role": "tool", "tool_call_id": part.call_id, "content": part.text()}
                    )
                    if part.is_error:
                        self._loss(report, f"{where}.is_error", "tool results have no error flag")
                elif isinstance(part, ReasoningPart) and part.text and message.role == "assistant":
                    origin = part.origin.model if part.origin is not None else None
                    if origin is None or origin == request.model:
                        reasoning.append(part.text)
                    else:
                        self._loss(report, where, "reasoning is never sent to another model")
                else:
                    self._loss(report, where, f"{part.type} parts are not sent to /apply-template")
            if message.role == "tool":
                if texts:  # loose text in a tool message, with no call to point at
                    out.append({"role": "tool", "content": "".join(texts)})
                continue
            if has_results and not (texts or calls or reasoning):
                continue
            entry: dict[str, Any] = {"role": message.role, "content": "".join(texts)}
            if reasoning:
                entry["reasoning_content"] = "".join(reasoning)
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
        return out

    def _route(self, state: StreamState, pieces: list[tuple[str, str]]) -> list[Delta]:
        """Reasoning pieces become reasoning deltas; text goes through the tool-call extractor."""
        extractor = state.data.get("calls")
        deltas: list[Delta] = []
        for kind, text in pieces:
            if kind == "reasoning":
                deltas.append(ReasoningDelta(text=text))
            elif isinstance(extractor, StreamingToolCallExtractor):
                deltas.extend(extractor.feed(text))
            else:
                deltas.append(TextDelta(text=text))
        return deltas

    def _flush(self, state: StreamState) -> list[Delta]:
        deltas = self._route(state, _splitter(state).finish())
        extractor = state.data.get("calls")
        if isinstance(extractor, StreamingToolCallExtractor):
            deltas.extend(extractor.finish())
            state.data["found"] = bool(extractor.calls)
        return deltas


# ---- text helpers -----------------------------------------------------------------------------


def split_think(text: str, open_at_start: bool = False) -> tuple[str | None, str]:
    """Split a leading `<think>...</think>` block off model text.

    Returns (reasoning or None, answer). With `open_at_start` the prompt already opened the
    block, so the text starts inside it. Reasoning loses its surrounding whitespace and the
    answer the whitespace after `</think>`; an unclosed block is all reasoning.
    """
    if open_at_start:
        body = text
    else:
        stripped = text.lstrip()
        if not stripped.startswith(THINK_OPEN):
            return None, text
        body = stripped[len(THINK_OPEN) :]
    reasoning, closed, rest = body.partition(THINK_CLOSE)
    return reasoning.strip(), rest.lstrip() if closed else ""


def _partial_suffix(text: str, marker: str) -> int:
    """Length of the longest suffix of `text` that is a proper prefix of `marker`."""
    for size in range(min(len(marker) - 1, len(text)), 0, -1):
        if marker.startswith(text[-size:]):
            return size
    return 0


class _ThinkSplitter:
    """Streaming `split_think`: the same split, fed piece by piece."""

    def __init__(self, open_at_start: bool) -> None:
        self._state = "think" if open_at_start else "start"
        self._buffer = ""
        self._reasoning_started = False

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self._buffer += chunk
        return self._drain(final=False)

    def finish(self) -> list[tuple[str, str]]:
        return self._drain(final=True)

    def _reasoning(self, out: list[tuple[str, str]], text: str) -> None:
        if not self._reasoning_started:
            text = text.lstrip()
        if text:
            self._reasoning_started = True
            out.append(("reasoning", text))

    def _drain(self, final: bool) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        while True:
            if self._state == "start":
                stripped = self._buffer.lstrip()
                if stripped.startswith(THINK_OPEN):
                    self._buffer = stripped[len(THINK_OPEN) :]
                    self._state = "think"
                elif not final and THINK_OPEN.startswith(stripped):
                    return out  # only whitespace or part of "<think>" so far
                else:
                    self._state = "text"
            elif self._state == "think":
                end = self._buffer.find(THINK_CLOSE)
                if end >= 0:
                    self._reasoning(out, self._buffer[:end].rstrip())
                    self._buffer = self._buffer[end + len(THINK_CLOSE) :]
                    self._state = "after"
                elif final:
                    self._reasoning(out, self._buffer.rstrip())
                    self._buffer = ""
                    return out
                else:
                    # Hold back a possible partial "</think>" and trailing whitespace, which is
                    # dropped if the block closes right after it.
                    ready = self._buffer[
                        : len(self._buffer) - _partial_suffix(self._buffer, THINK_CLOSE)
                    ]
                    body = ready.rstrip()
                    self._reasoning(out, body)
                    self._buffer = self._buffer[len(body) :]
                    return out
            elif self._state == "after":
                self._buffer = self._buffer.lstrip()
                if not self._buffer:
                    return out
                self._state = "text"
            else:
                if self._buffer:
                    out.append(("text", self._buffer))
                    self._buffer = ""
                return out


def _splitter(state: StreamState) -> _ThinkSplitter:
    splitter = state.data["think"]
    assert isinstance(splitter, _ThinkSplitter)
    return splitter


def render_call(name: str, arguments: str, syntax: str | None = None) -> str:
    """A tool call as model text in a catalog syntax (default: JSON in `<tool_call>` tags, the
    syntax of the ChatML render)."""
    args = _arguments_object(arguments)[0]
    if syntax == "xml_function":
        params = "".join(
            f"<parameter={key}>\n"
            f"{value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}\n"
            "</parameter>\n"
            for key, value in args.items()
        )
        return f"<tool_call>\n<function={name}>\n{params}</function>\n</tool_call>"
    if syntax == "bracketed":
        return f"[TOOL_CALLS]{name}[ARGS]{json.dumps(args, ensure_ascii=False)}"
    call = json.dumps({"name": name, "arguments": args}, ensure_ascii=False)
    return f"<tool_call>\n{call}\n</tool_call>"


def _render_message(message: Message, syntax: str | None) -> str:
    reasoning = "".join(p.text for p in message.content if isinstance(p, ReasoningPart) and p.text)
    text = "".join(p.text for p in message.content if isinstance(p, TextPart | RefusalPart))
    calls = [
        render_call(p.name, p.arguments, syntax)
        for p in message.content
        if isinstance(p, ToolCallPart)
    ]
    out = f"{THINK_OPEN}\n{reasoning}\n{THINK_CLOSE}\n\n" if reasoning else ""
    out += text
    if calls:
        out += ("\n" if text else "") + "\n".join(calls)
    return out


# ---- value helpers ----------------------------------------------------------------------------


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _arguments_object(arguments: str) -> tuple[dict[str, Any], bool]:
    try:
        value = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {"input": arguments}, False
    if isinstance(value, dict):
        return value, True
    return {"input": value}, False


def _decode_prompt(prompt: Any) -> tuple[str, list[ImagePart]]:
    """A single text prompt, or the multimodal object form `{prompt_string, multimodal_data}`."""
    if isinstance(prompt, list) and len(prompt) == 1:
        prompt = prompt[0]
    if isinstance(prompt, str):
        return prompt, []
    if isinstance(prompt, dict) and isinstance(prompt.get("prompt_string"), str):
        data = prompt.get("multimodal_data") or []
        images = [ImagePart(data=item) for item in data if isinstance(item, str) and item]
        return prompt["prompt_string"], images
    raise ValueError(
        "prompt must be one text prompt; token-ID prompts and prompt lists are not supported"
    )


def _finish(stop_type: str | None, has_calls: bool) -> FinishReason:
    """`limit` (n_predict reached) is a length stop; anything else ended the answer (the raw
    `stop_type` stays in `Response.raw_finish_reason`)."""
    if stop_type == "limit":
        return "length"
    return "tool_calls" if has_calls else "stop"


def _stop_type(reason: FinishReason, raw: str | None) -> str:
    if reason == "length":
        return "limit"
    if reason in ("stop", "tool_calls"):
        return raw if raw in ("eos", "word") else "eos"
    return "none"


def _usage(body: dict[str, Any]) -> Usage | None:
    timings = body.get("timings")
    timings = timings if isinstance(timings, dict) else {}
    evaluated = _count(body.get("tokens_evaluated"))
    predicted = _count(body.get("tokens_predicted"))
    cache_n = _count(timings.get("cache_n"))
    prompt_n = _count(timings.get("prompt_n"))
    if evaluated is None and prompt_n is not None:
        evaluated = prompt_n + (cache_n or 0)
    if predicted is None:
        predicted = _count(timings.get("predicted_n"))
    if evaluated is None and predicted is None:
        return None
    raw = {key: body[key] for key in USAGE_FIELDS if key in body}
    return Usage(
        input_tokens=evaluated, output_tokens=predicted, cached_input_tokens=cache_n, raw=raw
    )


def _usage_fields(usage: Usage | None) -> dict[str, Any]:
    if usage is None:
        return {}
    raw = usage.raw or {}
    input_tokens = usage.input_tokens or 0
    cached = usage.cached_input_tokens or 0
    output_tokens = usage.output_tokens or 0
    timings = dict(raw["timings"]) if isinstance(raw.get("timings"), dict) else {}
    timings.update(
        {"cache_n": cached, "prompt_n": input_tokens - cached, "predicted_n": output_tokens}
    )
    tokens_cached = _count(raw.get("tokens_cached"))
    return {
        "tokens_predicted": output_tokens,
        "tokens_evaluated": input_tokens,
        "tokens_cached": tokens_cached
        if tokens_cached is not None
        else input_tokens + output_tokens,
        "timings": timings,
    }


def _error_delta(error: Any) -> ErrorDelta:
    if not isinstance(error, dict):
        return ErrorDelta(kind="server", message=str(error))
    code = _count(error.get("code"))
    kind = ERROR_KINDS.get(_text(error.get("type"))) or (
        kind_for_status(code) if code is not None else "server"
    )
    return ErrorDelta(kind=kind, message=_text(error.get("message")) or "stream error", status=code)


__all__ = ["ERROR_KINDS", "LlamaCppCodec", "render_call", "split_think"]
