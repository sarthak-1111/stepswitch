"""text-completion codec: `POST /v1/completions` with a prompt Stepswitch renders (ADR 0003).

The format has no roles, tools or reasoning fields. `templates.prompt_for` renders the request
into one prompt: the request's pre-rendered `prompt`, or the default ChatML render with tools
described in a `<tools>` block, in which case `templates.CHATML_STOP` joins the stop sequences.
Tool calls are parsed back out of the generated text with the syntax catalog in
`stepswitch.compat.toolcall` (`CodecOptions.tool_parser`; None tries every catalog parser), also
incrementally while streaming. Their ids are synthetic (`synthetic_call_id`).

What the render cannot carry goes into the `ConversionReport`: images (an `[image]` placeholder),
files, prior reasoning, refusals, participant names, tool-result error flags, forced or
forbidden tool choice, effort and reasoning budgets. A requested response format is described in
the system prompt and reported as approximated, since the output is not constrained; engines
that can constrain output take their own fields through `Request.extensions["text-completion"]`.

The decoder never sees the request, so it parses tool calls whenever a catalog marker appears in
the output, also for requests without tools.

Server side (the proxy), `decode_request` maps the prompt to `Request(prompt=..., messages=[a
user message with the prompt text])`, and responses write tool calls back into the text as
`<tool_call>` JSON blocks. Usage, errors and finish reasons share the chat-completions
vocabulary (`stepswitch.compat.codecs.chat`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from typing import Any, ClassVar

from stepswitch.compat import templates
from stepswitch.compat.codecs.base import Codec, CodecOptions, Framing, HttpCall, StreamState
from stepswitch.compat.codecs.chat import (
    apply_extensions,
    as_dict,
    as_float,
    as_int,
    as_list,
    as_str,
    assistant_turns,
    check_body,
    decode_usage,
    encode_error_body,
    encode_usage,
    error_delta,
    error_from_http,
    finish_from_wire,
    first_choice,
    new_id,
    report_loss,
    stop_list,
    stream_error_event,
)
from stepswitch.compat.errors import ModelError
from stepswitch.compat.framing import StreamEvent, sse_json
from stepswitch.compat.ir import (
    ConversionError,
    ConversionReport,
    Delta,
    ErrorDelta,
    FilePart,
    FinishDelta,
    ImagePart,
    Message,
    Part,
    ReasoningPart,
    RefusalPart,
    Request,
    Response,
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

PATH = "/v1/completions"
FORMAT = "text-completion"

# The format's finish reasons; a turn that ends in tool calls stopped normally.
FINISH_TO_WIRE: dict[str, str] = {
    "stop": "stop",
    "length": "length",
    "content_filter": "content_filter",
}

JSON_OBJECT_INSTRUCTION = "Respond with a single JSON object and nothing else."
JSON_SCHEMA_INSTRUCTION = (
    "Respond with a single JSON object that matches this JSON Schema, and nothing else:\n"
)


def render_tool_call(name: str, arguments: str) -> str:
    """A call the way the default ChatML render writes one: `<tool_call>` JSON `</tool_call>`."""
    try:
        value: Any = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        value = arguments
    call = {"name": name, "arguments": value}
    return f"<tool_call>\n{json.dumps(call, ensure_ascii=False)}\n</tool_call>"


def _is_json(text: str) -> bool:
    try:
        json.loads(text or "{}")
    except json.JSONDecodeError:
        return False
    return True


class TextCompletionCodec(Codec):
    key: ClassVar[str] = "text"
    format: ClassVar[str] = FORMAT
    framing: ClassVar[Framing] = "sse"

    _KNOWN_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "model",
            "prompt",
            "max_tokens",
            "temperature",
            "top_p",
            "top_k",
            "seed",
            "stop",
            "presence_penalty",
            "frequency_penalty",
            "stream",
            "stream_options",
            "n",
            "best_of",
            "echo",
            "logprobs",
            "suffix",
        }
    )

    def __init__(self, options: CodecOptions | None = None) -> None:
        super().__init__(options)
        syntax = self.options.tool_parser
        if syntax is not None and syntax not in parsers.PARSERS:
            known = ", ".join(parsers.PARSERS)
            raise ValueError(f"unknown tool parser {syntax!r}; known: {known}")
        self._turn = 0

    def _lose(
        self, report: ConversionReport, where: str, detail: str, action: str = "dropped"
    ) -> None:
        report_loss(self, report, where, detail, action)

    # ---- client side: requests ---------------------------------------------------------

    def encode_request(self, request: Request, report: ConversionReport) -> HttpCall:
        self._turn = assistant_turns(request)
        if request.prompt is None:
            rendered = self._render_request(request, report)
        else:
            rendered = request
            if request.response_format is not None and request.response_format.type != "text":
                self._lose(report, "response_format", "the prompt is pre-rendered; not described")
        body: dict[str, Any] = {"model": request.model, "prompt": templates.prompt_for(rendered)}
        if request.max_output_tokens is not None:
            body["max_tokens"] = request.max_output_tokens
        sampling = request.sampling
        for name in ("temperature", "top_p", "seed", "presence_penalty", "frequency_penalty"):
            value = getattr(sampling, name)
            if value is not None:
                body[name] = value
        if sampling.top_k is not None:
            if self.options.extra.get("accepts_top_k"):
                body["top_k"] = sampling.top_k
            else:
                self._lose(
                    report, "sampling.top_k", "top_k is not part of the text-completion format"
                )
        stop = list(sampling.stop)
        if request.prompt is None:
            stop.extend(s for s in templates.CHATML_STOP if s not in stop)
        if stop:
            body["stop"] = stop
        choice = request.tool_choice
        if choice not in (None, "auto") and (choice != "none" or request.tools):
            self._lose(report, "tool_choice", "a raw completion cannot force or forbid tool calls")
        if request.parallel_tool_calls is False and request.tools:
            self._lose(report, "parallel_tool_calls", "a raw completion cannot limit tool calls")
        if request.effort is not None:
            self._lose(report, "effort", "text-completion has no reasoning effort control")
        if request.budget_tokens is not None:
            self._lose(report, "budget_tokens", "text-completion has no reasoning token budget")
        apply_extensions(self, request, body, report)
        if request.stream:
            body["stream"] = True
            if self.options.stream_usage:
                body["stream_options"] = {"include_usage": True}
        return HttpCall(method="POST", path=PATH, json=body, stream=request.stream)

    def _render_request(self, request: Request, report: ConversionReport) -> Request:
        """The request as the ChatML render will see it, with every loss reported."""
        messages: list[Message] = []
        for i, message in enumerate(request.messages):
            where = f"messages.{i}"
            if message.name:
                self._lose(report, f"{where}.name", "the prompt has no participant names")
            if message.metadata:
                self._lose(report, f"{where}.metadata", f"no field for {sorted(message.metadata)}")
            parts: list[Part] = []
            for j, part in enumerate(message.content):
                at = f"{where}.content.{j}"
                if isinstance(part, ImagePart):
                    self._lose(report, at, "rendered as an [image] placeholder", "approximated")
                elif isinstance(part, FilePart):
                    self._lose(report, at, "files are not rendered into the prompt")
                elif isinstance(part, ReasoningPart):
                    self._lose(report, at, "prior reasoning is not rendered into the prompt")
                elif isinstance(part, RefusalPart):
                    self._lose(report, at, "refusals are not rendered into the prompt")
                elif isinstance(part, ToolResultPart):
                    if part.is_error:
                        self._lose(report, f"{at}.is_error", "the prompt has no tool error flag")
                    if any(not isinstance(item, TextPart) for item in part.content):
                        self._lose(report, f"{at}.content", "only tool-result text is rendered")
                elif isinstance(part, ToolCallPart) and not _is_json(part.arguments):
                    self._lose(
                        report,
                        f"{at}.arguments",
                        "not JSON; rendered as {input: text}",
                        "approximated",
                    )
                    part = part.model_copy(
                        update={"arguments": json.dumps({"input": part.arguments})}
                    )
                parts.append(part)
            messages.append(message.model_copy(update={"content": parts}))
        system = list(request.system)
        response_format = request.response_format
        if response_format is not None and response_format.type != "text":
            instruction = JSON_OBJECT_INSTRUCTION
            if response_format.type == "json_schema":
                schema = json.dumps(response_format.json_schema or {}, ensure_ascii=False)
                instruction = JSON_SCHEMA_INSTRUCTION + schema
            system.append(TextPart(text=instruction))
            self._lose(
                report,
                "response_format",
                "described in the system prompt; the output is not constrained",
                "approximated",
            )
        for k, tool in enumerate(request.tools):
            if tool.strict:
                self._lose(
                    report, f"tools.{k}.strict", "schema adherence is not enforced", "approximated"
                )
        return request.model_copy(update={"messages": messages, "system": system})

    # ---- client side: responses ----------------------------------------------------------

    def decode_response(self, body: Any) -> Response:
        data = check_body(body, self.format)
        choice = first_choice(data, self.format)
        text = as_str(choice.get("text")) or ""
        remaining, calls = extract_calls(text, self.options.tool_parser, self._turn)
        parts: list[Part] = []
        if remaining:
            parts.append(TextPart(text=remaining))
        parts.extend(calls)
        reason, raw_reason = finish_from_wire(choice.get("finish_reason"), bool(calls))
        return Response(
            id=as_str(data.get("id")),
            model=as_str(data.get("model")),
            message=Message(role="assistant", content=parts),
            finish_reason=reason,
            raw_finish_reason=raw_reason,
            usage=decode_usage(data.get("usage")),
            created=as_int(data.get("created")),
        )

    def decode_stream_event(self, event: StreamEvent, state: StreamState) -> list[Delta]:
        data = event.data.strip()
        if not data or state.finished:
            return []
        if data == "[DONE]":
            return self._finish(state, done=True)
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            state.finished = True
            return [ErrorDelta(kind="invalid_response", message=f"bad stream event: {data[:200]}")]
        if not isinstance(chunk, dict):
            return []
        if chunk.get("error") is not None and not chunk.get("choices"):
            state.finished = True
            return [error_delta(chunk)]
        out: list[Delta] = []
        if not state.started:
            state.started = True
            out.append(StartDelta(id=as_str(chunk.get("id")), model=as_str(chunk.get("model"))))
        extractor = self._extractor(state)
        for raw_choice in as_list(chunk.get("choices")):
            choice = as_dict(raw_choice)
            if as_int(choice.get("index")) not in (None, 0):
                continue  # n > 1 is never requested
            text = as_str(choice.get("text"))
            if text:
                out.extend(self._hold_whitespace(state, extractor.feed(text)))
            finish = as_str(choice.get("finish_reason"))
            if finish:
                state.data["finish"] = finish
        usage = decode_usage(chunk.get("usage"))
        if usage is not None:
            out.append(UsageDelta(usage=usage))
        return out

    def finish_stream(self, state: StreamState) -> list[Delta]:
        return self._finish(state, done=False)

    def _extractor(self, state: StreamState) -> StreamingToolCallExtractor:
        extractor = state.data.get("extractor")
        if not isinstance(extractor, StreamingToolCallExtractor):
            extractor = StreamingToolCallExtractor(self.options.tool_parser, turn=self._turn)
            state.data["extractor"] = extractor
        return extractor

    @staticmethod
    def _hold_whitespace(state: StreamState, deltas: list[Delta]) -> list[Delta]:
        """Hold trailing whitespace back until more text follows.

        The non-streaming parser strips the text around tool calls; holding whitespace back
        lets the stream drop it too when a tool call follows, and keep it otherwise.
        """
        out: list[Delta] = []
        for delta in deltas:
            if isinstance(delta, TextDelta):
                text = str(state.data.get("held", "")) + delta.text
                kept = text.rstrip()
                state.data["held"] = text[len(kept) :]
                if kept:
                    out.append(TextDelta(text=kept))
            else:
                out.append(delta)
        return out

    def _finish(self, state: StreamState, *, done: bool) -> list[Delta]:
        if state.finished:
            return []
        state.finished = True
        tail = self._extractor(state).finish()
        has_calls = any(isinstance(delta, ToolCallStartDelta) for delta in tail)
        out = self._hold_whitespace(state, tail)
        held = str(state.data.pop("held", ""))
        if held and not has_calls:
            out.append(TextDelta(text=held))
        raw = state.data.get("finish")
        if raw is None and not done:
            out.append(
                ErrorDelta(kind="invalid_response", message="stream ended before a finish reason")
            )
            return out
        reason, raw_reason = finish_from_wire(raw, has_calls)
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
            raise ConversionError("text-completion request body must be a JSON object")
        prompt = body.get("prompt")
        if isinstance(prompt, list) and len(prompt) == 1 and isinstance(prompt[0], str):
            prompt = prompt[0]
        if not isinstance(prompt, str):
            raise ConversionError("only a single string prompt is supported (no batches or tokens)")
        for key in ("n", "best_of"):
            count = as_int(body.get(key))
            if count is not None and count != 1:
                raise ConversionError(f"{key} above 1 is not supported")
        if body.get("echo") is True:
            raise ConversionError("echo is not supported")
        if as_int(body.get("logprobs")) is not None:
            raise ConversionError("logprobs are not supported")
        if as_str(body.get("suffix")):
            raise ConversionError("suffix (insertion) is not supported")
        extensions = {k: v for k, v in body.items() if k not in self._KNOWN_FIELDS}
        return Request(
            model=as_str(body.get("model")) or "",
            prompt=prompt,
            messages=[Message.text("user", prompt)],
            max_output_tokens=as_int(body.get("max_tokens")),
            sampling=Sampling(
                temperature=as_float(body.get("temperature")),
                top_p=as_float(body.get("top_p")),
                top_k=as_int(body.get("top_k")),
                seed=as_int(body.get("seed")),
                stop=stop_list(body.get("stop")),
                presence_penalty=as_float(body.get("presence_penalty")),
                frequency_penalty=as_float(body.get("frequency_penalty")),
            ),
            stream=body.get("stream") is True,
            extensions={self.format: extensions} if extensions else {},
        )

    def encode_response(self, response: Response) -> dict[str, Any]:
        text = "".join(p.text for p in response.message.content if isinstance(p, TextPart))
        blocks = [
            render_tool_call(call.name, call.arguments) for call in response.message.tool_calls()
        ]
        body: dict[str, Any] = {
            "id": response.id or new_id("cmpl"),
            "object": "text_completion",
            "created": response.created if response.created is not None else int(time.time()),
            "model": response.model or "",
            "choices": [
                {
                    "text": "\n".join([text, *blocks] if text else blocks),
                    "index": 0,
                    "logprobs": None,
                    "finish_reason": FINISH_TO_WIRE.get(response.finish_reason, "stop"),
                }
            ],
        }
        if response.usage is not None:
            body["usage"] = encode_usage(response.usage)
        return body

    def encode_stream(self, deltas: Iterable[Delta]) -> Iterator[StreamEvent]:
        writer = _TextWriter()
        for delta in deltas:
            yield from writer.feed(delta)
        yield from writer.close(self.options.stream_usage)

    def encode_error(self, error: ModelError) -> tuple[int, Any]:
        return encode_error_body(error)


class _TextWriter:
    """IR deltas -> `text_completion` stream events (server side)."""

    def __init__(self) -> None:
        self.id: str | None = None
        self.model = ""
        self.created = int(time.time())
        self.finished = False
        self.failed = False
        self.wrote = False
        self.usage: Usage | None = None
        self.calls: dict[int, tuple[str, list[str]]] = {}

    def _chunk(self, text: str, finish: str | None = None) -> StreamEvent:
        self.id = self.id or new_id("cmpl")
        return sse_json(
            {
                "id": self.id,
                "object": "text_completion",
                "created": self.created,
                "model": self.model,
                "choices": [{"text": text, "index": 0, "logprobs": None, "finish_reason": finish}],
            }
        )

    def _text(self, text: str) -> list[StreamEvent]:
        if not text:
            return []
        self.wrote = True
        return [self._chunk(text)]

    def _call(self, index: int) -> list[StreamEvent]:
        name, pieces = self.calls.pop(index)
        block = render_tool_call(name, "".join(pieces))
        return self._text(("\n" if self.wrote else "") + block)

    def _open_calls(self) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        for index in sorted(self.calls):
            out.extend(self._call(index))
        return out

    def feed(self, delta: Delta) -> list[StreamEvent]:
        if isinstance(delta, UsageDelta):
            self.usage = merge_usage(self.usage, delta.usage)
            return []
        if self.finished or self.failed:
            return []
        if isinstance(delta, StartDelta):
            self.id = self.id or delta.id
            self.model = self.model or delta.model or ""
            return []
        if isinstance(delta, TextDelta):
            return self._text(delta.text)
        if isinstance(delta, ToolCallStartDelta):
            self.calls[delta.index] = (delta.name, [])
            return []
        if isinstance(delta, ToolCallArgsDelta):
            if delta.index in self.calls:
                self.calls[delta.index][1].append(delta.delta)
            return []
        if isinstance(delta, ToolCallEndDelta):
            return self._call(delta.index) if delta.index in self.calls else []
        if isinstance(delta, FinishDelta):
            out = self._open_calls()
            self.finished = True
            out.append(self._chunk("", FINISH_TO_WIRE.get(delta.reason, "stop")))
            return out
        if isinstance(delta, ErrorDelta):
            self.failed = True
            return [stream_error_event(delta)]
        return []  # reasoning has no place in a raw completion

    def close(self, include_usage: bool) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        if not self.finished and not self.failed:
            out.extend(self._open_calls())
            out.append(self._chunk("", "stop"))
        if self.usage is not None and include_usage and not self.failed:
            out.append(
                sse_json(
                    {
                        "id": self.id or new_id("cmpl"),
                        "object": "text_completion",
                        "created": self.created,
                        "model": self.model,
                        "choices": [],
                        "usage": encode_usage(self.usage),
                    }
                )
            )
        out.append(StreamEvent(data="[DONE]"))
        return out
