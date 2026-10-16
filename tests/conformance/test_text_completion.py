"""Conformance suite for the text-completion format (claim C-004).

Mock bodies and stream events are written by hand in the shape of the text-completion reference
(`POST /v1/completions`, "text_completion" objects, data-only SSE ended by `data: [DONE]`) as
the open engines serve it (docs/RESEARCH.md, "API formats"). Model output uses the tool-call
syntax of the default ChatML render; the codec under test never produces the fixtures.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

import pytest

from stepswitch.compat.codecs import get_codec
from stepswitch.compat.codecs.base import CodecOptions
from stepswitch.compat.codecs.text import TextCompletionCodec
from stepswitch.compat.errors import ModelError
from stepswitch.compat.framing import StreamEvent, format_sse, iter_lines, parse_sse
from stepswitch.compat.ir import (
    ConversionError,
    ConversionReport,
    ErrorDelta,
    FilePart,
    ImagePart,
    Message,
    NamedToolChoice,
    Origin,
    ReasoningPart,
    RefusalPart,
    Request,
    Response,
    ResponseFormat,
    Sampling,
    TextDelta,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
    UsageDelta,
)
from stepswitch.compat.stream import assemble, response_to_deltas
from stepswitch.compat.templates import CHATML_STOP, render_chatml
from tests.conformance.harness import (
    ANSWER,
    CLOCK,
    WEATHER,
    FormatSuite,
    MockServer,
    client_for,
    conversation_request,
    json_response,
    normalize,
    sse_response,
    text_request,
    tool_request,
)

FMT = "text-completion"
ROUTE = r"/v1/completions"
MODEL = "test-model"
CREATED = 1760600000

# --------------------------------------------------------------------------------------------
# Hand-written wire fixtures
# --------------------------------------------------------------------------------------------

USAGE_SMALL = {"prompt_tokens": 25, "completion_tokens": 4, "total_tokens": 29}
USAGE_CACHED = {
    "prompt_tokens": 1830,
    "completion_tokens": 52,
    "total_tokens": 1882,
    "prompt_tokens_details": {"cached_tokens": 1792},
    "completion_tokens_details": {"reasoning_tokens": 20},
}

WEATHER_CALL = '<tool_call>\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n</tool_call>'
CLOCK_CALL = '<tool_call>\n{"name": "get_time", "arguments": {"city": "Tokyo"}}\n</tool_call>'

ERROR_400 = {
    "error": {
        "message": "This model's maximum context length is 4096 tokens.",
        "type": "invalid_request_error",
        "param": "prompt",
        "code": "context_length_exceeded",
    }
}
ERROR_400_INVALID = {
    "error": {
        "message": "Invalid value for 'temperature': expected a number between 0 and 2.",
        "type": "invalid_request_error",
        "param": "temperature",
        "code": "invalid_value",
    }
}
ERROR_401 = {
    "error": {
        "message": "Invalid API Key",
        "type": "authentication_error",
        "param": None,
        "code": None,
    }
}
ERROR_429 = {
    "error": {
        "message": "Too many requests; try again shortly.",
        "type": "rate_limit_error",
        "param": None,
        "code": "rate_limit_exceeded",
    }
}
ERROR_500 = {
    "error": {
        "message": "The server had an error while processing your request.",
        "type": "server_error",
        "param": None,
        "code": None,
    }
}


def completion_body(text: str, finish: str, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    """A `text_completion` object around hand-written model output."""
    return {
        "id": "cmpl-8nT3vYq2RkW1",
        "object": "text_completion",
        "created": CREATED,
        "model": MODEL,
        "choices": [{"text": text, "index": 0, "logprobs": None, "finish_reason": finish}],
        "usage": usage or USAGE_SMALL,
    }


def chunk(text: str, finish: str | None = None) -> dict[str, Any]:
    """A streamed `text_completion` object: same shape as the full response."""
    return {
        "id": "cmpl-s5Hq0Ze9",
        "object": "text_completion",
        "created": CREATED,
        "model": MODEL,
        "choices": [{"text": text, "index": 0, "logprobs": None, "finish_reason": finish}],
        "usage": None,
    }


def usage_chunk(usage: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "cmpl-s5Hq0Ze9",
        "object": "text_completion",
        "created": CREATED,
        "model": MODEL,
        "choices": [],
        "usage": usage,
    }


def stream_of(pieces: list[str], finish: str = "stop") -> list[dict[str, Any] | str]:
    return [*(chunk(piece) for piece in pieces), chunk("", finish), "[DONE]"]


def serve_json(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> MockServer:
    return MockServer().route("POST", ROUTE, lambda _r: json_response(body, status, headers))


def serve_sse(events: list[dict[str, Any] | str]) -> MockServer:
    return MockServer().route("POST", ROUTE, lambda _r: sse_response(events))


def raises(action: Callable[[], object]) -> ModelError:
    try:
        action()
    except ModelError as exc:
        return exc
    raise AssertionError("expected a ModelError")


def call_view(response: Response) -> list[tuple[str, dict[str, Any]]]:
    return [(call.name, call.parsed_arguments()) for call in response.message.tool_calls()]


def assert_ids(response: Response, count: int) -> None:
    ids = [call.id for call in response.message.tool_calls()]
    assert len(ids) == count and all(ids) and len(set(ids)) == count
    assert all(call.synthetic for call in response.message.tool_calls())


def usage_view(usage: Usage | None) -> tuple[int | None, ...]:
    assert usage is not None
    return (
        usage.input_tokens,
        usage.output_tokens,
        usage.cached_input_tokens,
        usage.reasoning_tokens,
    )


def wire_round_trip(events: Iterable[StreamEvent]) -> list[StreamEvent]:
    text = "".join(format_sse(event) for event in events)
    return list(parse_sse(iter_lines([text])))


def encode(request: Request, **options: Any) -> tuple[dict[str, Any], ConversionReport]:
    codec = TextCompletionCodec(CodecOptions(**options))
    report = ConversionReport(target=FMT)
    body = codec.encode_request(request, report).json
    assert body is not None
    return body, report


# --------------------------------------------------------------------------------------------
# Required cases
# --------------------------------------------------------------------------------------------


def case_non_streaming() -> None:
    body = {
        "id": "cmpl-8nT3vYq2RkW1",
        "object": "text_completion",
        "created": CREATED,
        "model": MODEL,
        "system_fingerprint": None,
        "choices": [
            {"text": "Hello there.", "index": 0, "logprobs": None, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 25, "completion_tokens": 4, "total_tokens": 29},
    }
    server = serve_json(body)
    completion = client_for(FMT, server).complete(text_request())
    sent = server.last
    assert (sent.method, sent.path) == ("POST", "/v1/completions")
    assert sent.body == {
        "model": MODEL,
        "prompt": (
            "<|im_start|>system\nYou are terse.<|im_end|>\n"
            "<|im_start|>user\nSay hello.<|im_end|>\n"
            "<|im_start|>assistant\n"
        ),
        "max_tokens": 64,
        "stop": CHATML_STOP,
    }
    response = completion.response
    assert response.message.text_content() == ANSWER
    assert (response.finish_reason, response.raw_finish_reason) == ("stop", "stop")
    assert (response.id, response.model, response.created) == ("cmpl-8nT3vYq2RkW1", MODEL, CREATED)
    assert completion.report.lossless


def case_streaming() -> None:
    events: list[dict[str, Any] | str] = [
        chunk("Hel"),
        chunk("lo the"),
        chunk("re."),
        chunk("", "stop"),
        usage_chunk(USAGE_SMALL),
        "[DONE]",
    ]
    server = serve_sse(events)
    deltas = list(client_for(FMT, server).stream(text_request()))
    sent = server.last.body
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}
    assert [d.text for d in deltas if isinstance(d, TextDelta)] == ["Hel", "lo the", "re."]
    response = assemble(deltas)
    assert response.message.text_content() == ANSWER
    assert (response.finish_reason, response.raw_finish_reason) == ("stop", "stop")
    assert (response.id, response.model) == ("cmpl-s5Hq0Ze9", MODEL)


def case_tool_call() -> None:
    server = serve_json(completion_body(WEATHER_CALL, "stop"))
    response = client_for(FMT, server).complete(tool_request()).response
    prompt = server.last.body["prompt"]
    # Tools travel inside the prompt, in the ChatML render's <tools> block.
    for tool in (WEATHER, CLOCK):
        spec = {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        assert json.dumps(spec) in prompt
    assert "<tools>" in prompt and prompt.endswith("<|im_start|>assistant\n")
    assert call_view(response) == [("get_weather", {"city": "Paris"})]
    assert (response.finish_reason, response.raw_finish_reason) == ("tool_calls", "stop")
    assert response.message.text_content() == ""
    assert_ids(response, 1)

    pieces = [
        "<tool",
        '_call>\n{"na',
        'me": "get_weather", "argu',
        'ments": {"city": "Par',
        'is"}}',
    ]
    events = stream_of([*pieces, "\n</tool_", "call>"])
    streamed = assemble(client_for(FMT, serve_sse(events)).stream(tool_request()))
    assert call_view(streamed) == [("get_weather", {"city": "Paris"})]
    assert streamed.finish_reason == "tool_calls"
    assert streamed.message.text_content() == ""
    assert_ids(streamed, 1)


def case_parallel_tool_calls() -> None:
    output = f"Checking both.\n{WEATHER_CALL}\n{CLOCK_CALL}"
    response = client_for(FMT, serve_json(completion_body(output, "stop"))).complete(tool_request())
    expected = [("get_weather", {"city": "Paris"}), ("get_time", {"city": "Tokyo"})]
    assert call_view(response.response) == expected
    assert response.response.message.text_content() == "Checking both."
    assert_ids(response.response, 2)

    pieces = [output[i : i + 7] for i in range(0, len(output), 7)]
    streamed = assemble(client_for(FMT, serve_sse(stream_of(pieces))).stream(tool_request()))
    assert call_view(streamed) == expected
    assert streamed.message.text_content() == "Checking both."
    assert streamed.finish_reason == "tool_calls"
    assert_ids(streamed, 2)


def case_error() -> None:
    cases: list[tuple[int, dict[str, Any], dict[str, str], str]] = [
        (400, ERROR_400, {}, "too_large"),  # a context overflow is its own kind
        (400, ERROR_400_INVALID, {}, "bad_request"),
        (401, ERROR_401, {}, "authentication"),
        (429, ERROR_429, {"retry-after": "12"}, "rate_limit"),
        (500, ERROR_500, {}, "server"),
    ]
    for status, body, headers, kind in cases:
        server = serve_json(body, status, headers)
        error = raises(lambda server=server: client_for(FMT, server).complete(text_request()))
        assert (error.kind, error.status) == (kind, status)
        assert error.message == body["error"]["message"]
        assert error.retry_after == (12.0 if status == 429 else None)

    server = serve_json(ERROR_429, 429, {"retry-after": "12"})
    error = raises(lambda: list(client_for(FMT, server).stream(text_request())))
    assert (error.kind, error.retry_after) == ("rate_limit", 12.0)


def case_usage() -> None:
    server = serve_json(completion_body(ANSWER, "stop", USAGE_CACHED))
    usage = client_for(FMT, server).complete(text_request()).response.usage
    assert usage_view(usage) == (1830, 52, 1792, 20)  # input includes the cached tokens
    assert usage is not None and usage.raw == USAGE_CACHED

    events: list[dict[str, Any] | str] = [
        chunk(ANSWER),
        chunk("", "stop"),
        usage_chunk(USAGE_CACHED),
        "[DONE]",
    ]
    deltas = list(client_for(FMT, serve_sse(events)).stream(text_request()))
    assert len([d for d in deltas if isinstance(d, UsageDelta)]) == 1
    streamed = assemble(deltas).usage
    assert usage_view(streamed) == (1830, 52, 1792, 20)
    assert streamed is not None and streamed.raw == USAGE_CACHED


def case_server_round_trip() -> None:
    codec = TextCompletionCodec()
    for build in (text_request, tool_request, conversation_request):
        original = build()
        report = ConversionReport(target=FMT)
        call = codec.encode_request(original, report)
        assert call.json is not None
        prompt = call.json["prompt"]
        # The prompt flattens the conversation; everything in the lossless scope is in it.
        for part in original.system:
            assert part.text in prompt
        for message in original.messages:
            for item in message.content:
                if isinstance(item, TextPart):
                    assert item.text in prompt
                elif isinstance(item, ToolCallPart):
                    rendered = {"name": item.name, "arguments": item.parsed_arguments()}
                    assert f"<tool_call>\n{json.dumps(rendered)}\n</tool_call>" in prompt
                elif isinstance(item, ToolResultPart):
                    assert f"<tool_response>\n{item.text()}\n</tool_response>" in prompt
        for tool in original.tools:
            assert json.dumps(tool.parameters) in prompt
        assert call.json["stop"] == CHATML_STOP
        expected = [] if original.response_format is None else [("response_format", "approximated")]
        assert [(loss.field, loss.action) for loss in report.losses] == expected

        # The proxy reads such a body as an opaque prompt, and sends it on unchanged.
        decoded = codec.decode_request(json.loads(json.dumps(call.json)), path=call.path)
        assert decoded.prompt == prompt
        assert decoded.messages == [Message.text("user", prompt)]
        assert (decoded.model, decoded.max_output_tokens) == (MODEL, original.max_output_tokens)
        again = ConversionReport()
        resent = codec.encode_request(decoded, again).json
        assert resent is not None and again.lossless
        assert (resent["prompt"], resent["stop"]) == (prompt, CHATML_STOP)

    # A raw prompt from a client that renders its own prompts.
    raw = {
        "model": MODEL,
        "prompt": "def fibonacci(n):",
        "max_tokens": 48,
        "temperature": 0.2,
        "stop": "\n\n",
        "stream": False,
        "user": "dev-7",
    }
    decoded = codec.decode_request(raw, path="/v1/completions")
    assert decoded.prompt == "def fibonacci(n):"
    assert normalize(decoded) == {
        "system": "",
        "messages": [{"role": "user", "parts": [("text", "def fibonacci(n):")]}],
        "tools": [],
    }
    assert decoded.sampling == Sampling(temperature=0.2, stop=["\n\n"])
    assert decoded.max_output_tokens == 48
    assert decoded.extensions == {FMT: {"user": "dev-7"}}
    report = ConversionReport()
    resent = codec.encode_request(decoded, report).json
    assert report.lossless
    assert resent == {
        "model": MODEL,
        "prompt": "def fibonacci(n):",
        "max_tokens": 48,
        "temperature": 0.2,
        "stop": ["\n\n"],
        "user": "dev-7",
    }


RESPONSES = [
    Response(
        id="cmpl-rt1",
        model=MODEL,
        created=CREATED,
        message=Message(
            role="assistant",
            content=[
                TextPart(text=ANSWER),
                ToolCallPart(id="call_rt_a", name="get_weather", arguments='{"city":"Paris"}'),
                ToolCallPart(id="call_rt_b", name="get_time", arguments='{"city":"Tokyo"}'),
            ],
        ),
        finish_reason="tool_calls",
        usage=Usage(
            input_tokens=120, output_tokens=40, cached_input_tokens=100, reasoning_tokens=12
        ),
    ),
    Response(
        id="cmpl-rt2",
        model=MODEL,
        message=Message.text("assistant", "A long answer that was cut off"),
        finish_reason="length",
        usage=Usage(input_tokens=12, output_tokens=64),
    ),
]


def assert_same_response(got: Response, want: Response) -> None:
    assert got.message.text_content() == want.message.text_content()
    assert call_view(got) == call_view(want)
    assert_ids(got, len(want.message.tool_calls()))  # ids are synthesized on the way back
    assert got.finish_reason == want.finish_reason
    assert usage_view(got.usage) == usage_view(want.usage)
    assert (got.id, got.model) == (want.id, want.model)


def case_response_round_trip() -> None:
    codec = TextCompletionCodec()
    for original in RESPONSES:
        body = json.loads(json.dumps(codec.encode_response(original)))
        assert body["object"] == "text_completion"
        assert body["choices"][0]["finish_reason"] in ("stop", "length")
        assert_same_response(codec.decode_response(body), original)


def case_stream_round_trip() -> None:
    codec = TextCompletionCodec()
    for original in RESPONSES:
        events = list(codec.encode_stream(response_to_deltas(original, chunk=3)))
        assert events[-1].data == "[DONE]"
        assert_same_response(assemble(codec.decode_stream(wire_round_trip(events))), original)


SUITE = FormatSuite(
    format=FMT,
    cases={
        "non-streaming": case_non_streaming,
        "streaming": case_streaming,
        "tool-call": case_tool_call,
        "parallel-tool-calls": case_parallel_tool_calls,
        "error": case_error,
        "usage": case_usage,
        "server-round-trip": case_server_round_trip,
        "response-round-trip": case_response_round_trip,
        "stream-round-trip": case_stream_round_trip,
    },
)


@pytest.mark.parametrize("case", sorted(SUITE.cases))
def test_case(case: str) -> None:
    SUITE.cases[case]()


def test_suite_has_every_required_case() -> None:
    assert SUITE.missing_cases() == []


# --------------------------------------------------------------------------------------------
# Quirks and losses
# --------------------------------------------------------------------------------------------


def test_codec_lookup() -> None:
    codec = get_codec(FMT)
    assert isinstance(codec, TextCompletionCodec)
    assert (codec.key, codec.format, codec.framing) == ("text", FMT, "sse")


def test_prerendered_prompt_is_sent_verbatim() -> None:
    prompt = "[INST] Say hello. [/INST]"
    request = text_request().model_copy(update={"prompt": prompt})
    request.sampling.stop = ["</s>"]
    body, report = encode(request)
    assert body["prompt"] == prompt
    assert body["stop"] == ["</s>"]  # no ChatML stops for a prompt Stepswitch did not render
    assert report.lossless
    assert body["prompt"] != render_chatml(request)


def lossy_request() -> Request:
    return Request(
        model=MODEL,
        messages=[
            Message(
                role="user",
                name="alice",
                content=[
                    TextPart(text="Describe these."),
                    ImagePart(url="https://example.invalid/a.png"),
                    FilePart(file_id="file-1"),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ReasoningPart(text="Plan.", origin=Origin(format=FMT, model=MODEL)),
                    RefusalPart(text="I cannot open files."),
                    ToolCallPart(id="call_1", name="get_weather", arguments="city=Paris"),
                ],
            ),
            Message(
                role="tool",
                content=[
                    ToolResultPart(
                        call_id="call_1",
                        is_error=True,
                        content=[
                            TextPart(text="bad input"),
                            ImagePart(url="https://example.invalid/b.png"),
                        ],
                    )
                ],
            ),
        ],
        tools=[ToolSpec(name="get_weather", parameters=WEATHER.parameters, strict=True)],
        tool_choice="required",
        parallel_tool_calls=False,
        effort="high",
        budget_tokens=1024,
        sampling=Sampling(top_k=20),
    )


def test_losses_are_reported_and_strict_mode_rejects_them() -> None:
    body, report = encode(lossy_request())
    assert {(loss.field, loss.action) for loss in report.losses} == {
        ("messages.0.name", "dropped"),
        ("messages.0.content.1", "approximated"),
        ("messages.0.content.2", "dropped"),
        ("messages.1.content.0", "dropped"),
        ("messages.1.content.1", "dropped"),
        ("messages.1.content.2.arguments", "approximated"),
        ("messages.2.content.0.is_error", "dropped"),
        ("messages.2.content.0.content", "dropped"),
        ("tools.0.strict", "approximated"),
        ("tool_choice", "dropped"),
        ("parallel_tool_calls", "dropped"),
        ("effort", "dropped"),
        ("budget_tokens", "dropped"),
        ("sampling.top_k", "dropped"),
    }
    prompt = body["prompt"]
    assert "[image]" in prompt and "Plan." not in prompt and "I cannot open files." not in prompt
    assert '{"name": "get_weather", "arguments": {"input": "city=Paris"}}' in prompt
    assert "top_k" not in body

    with pytest.raises(ConversionError):
        TextCompletionCodec(CodecOptions(strict=True)).encode_request(
            lossy_request(), ConversionReport()
        )

    body, report = encode(
        text_request().model_copy(update={"sampling": Sampling(top_k=20)}),
        extra={"accepts_top_k": True},
    )
    assert body["top_k"] == 20 and report.lossless


def test_response_format_is_described_in_the_prompt() -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    fmt = ResponseFormat(type="json_schema", name="answer", json_schema=schema)
    body, report = encode(text_request().model_copy(update={"response_format": fmt}))
    assert json.dumps(schema) in body["prompt"]
    assert [(loss.field, loss.action) for loss in report.losses] == [
        ("response_format", "approximated")
    ]

    body, report = encode(
        text_request().model_copy(
            update={"response_format": ResponseFormat(type="json_object"), "prompt": "Q: hi\nA:"}
        )
    )
    assert body["prompt"] == "Q: hi\nA:"  # a pre-rendered prompt is never edited
    assert [(loss.field, loss.action) for loss in report.losses] == [("response_format", "dropped")]


def test_tool_choice_that_needs_no_enforcement_is_not_a_loss() -> None:
    for choice in ("auto", None):
        _, report = encode(tool_request().model_copy(update={"tool_choice": choice}))
        assert report.lossless
    _, report = encode(text_request().model_copy(update={"tool_choice": "none"}))
    assert report.lossless  # no tools, so nothing can be called anyway
    _, report = encode(
        tool_request().model_copy(update={"tool_choice": NamedToolChoice(name="get_time")})
    )
    assert [loss.field for loss in report.losses] == ["tool_choice"]


def test_named_tool_parser() -> None:
    output = (
        "<tool_call>\n<function=get_weather>\n<parameter=city>\nParis\n</parameter>\n"
        "<parameter=unit>\nc\n</parameter>\n</function>\n</tool_call>"
    )
    options = CodecOptions(tool_parser="xml_function")
    server = serve_json(completion_body(output, "stop"))
    response = client_for(FMT, server, options=options).complete(tool_request()).response
    assert call_view(response) == [("get_weather", {"city": "Paris", "unit": "c"})]

    pieces = [output[i : i + 5] for i in range(0, len(output), 5)]
    client = client_for(FMT, serve_sse(stream_of(pieces)), options=options)
    streamed = assemble(client.stream(tool_request()))
    assert call_view(streamed) == [("get_weather", {"city": "Paris", "unit": "c"})]

    with pytest.raises(ValueError, match="unknown tool parser"):
        TextCompletionCodec(CodecOptions(tool_parser="no-such-syntax"))


def test_plain_text_and_whitespace_survive_streaming() -> None:
    text = "Line one.\n\n  Indented <tag> and [brackets]\n"
    response = client_for(FMT, serve_json(completion_body(text, "length"))).complete(text_request())
    assert response.response.message.text_content() == text
    assert response.response.finish_reason == "length"

    pieces = [text[i : i + 4] for i in range(0, len(text), 4)]
    streamed = assemble(
        client_for(FMT, serve_sse(stream_of(pieces, "length"))).stream(text_request())
    )
    assert streamed.message.text_content() == text
    assert streamed.finish_reason == "length"


def test_synthetic_ids_follow_the_turn() -> None:
    server = serve_json(completion_body(WEATHER_CALL, "stop"))
    response = client_for(FMT, server).complete(conversation_request()).response
    assert [call.id for call in response.message.tool_calls()] == ["call_t2_0"]


def test_errors_inside_and_at_the_end_of_a_stream() -> None:
    events: list[dict[str, Any] | str] = [chunk("Hel"), ERROR_500, "[DONE]"]
    deltas = list(client_for(FMT, serve_sse(events)).stream(text_request()))
    last = deltas[-1]
    assert isinstance(last, ErrorDelta) and last.kind == "server"
    with pytest.raises(ModelError):
        assemble(deltas)

    cut = [chunk("Hel"), chunk("lo")]
    deltas = list(TextCompletionCodec().decode_stream(StreamEvent(json.dumps(c)) for c in cut))
    last = deltas[-1]
    assert isinstance(last, ErrorDelta) and last.kind == "invalid_response"


@pytest.mark.parametrize(
    "patch",
    [
        {"prompt": [1, 2, 3]},
        {"prompt": ["one", "two"]},
        {"prompt": None},
        {"n": 3},
        {"best_of": 2},
        {"echo": True},
        {"logprobs": 2},
        {"suffix": "return x"},
    ],
)
def test_decode_request_rejects_what_the_ir_cannot_carry(patch: dict[str, Any]) -> None:
    body = {"model": MODEL, "prompt": "hi", **patch}
    with pytest.raises(ConversionError):
        TextCompletionCodec().decode_request(body)


def test_single_prompt_list_and_server_shapes() -> None:
    codec = TextCompletionCodec()
    assert codec.decode_request({"model": MODEL, "prompt": ["hi"]}).prompt == "hi"

    status, body = codec.encode_error(ModelError("bad_request", "prompt too long", status=400))
    assert status == 400 and body["error"]["type"] == "invalid_request_error"

    events = list(codec.encode_stream(response_to_deltas(RESPONSES[0])))
    chunks = [json.loads(e.data) for e in events[:-1]]
    assert all(c["object"] == "text_completion" for c in chunks)
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"]["prompt_tokens"] == 120
    text = "".join(c["choices"][0]["text"] for c in chunks if c["choices"])
    assert text == f"{ANSWER}\n{WEATHER_CALL}\n{CLOCK_CALL}"
