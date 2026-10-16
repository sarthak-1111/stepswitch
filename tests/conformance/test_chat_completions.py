"""Conformance suite for the chat-completions format (claim C-004).

Mock bodies and stream events are written by hand in the wire shapes of the chat-completions
reference and of the open engines that serve the format (docs/RESEARCH.md, "API formats"); the
codec under test never produces them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

import httpx
import pytest

from stepswitch.compat.codecs import get_codec
from stepswitch.compat.codecs.base import CodecOptions
from stepswitch.compat.codecs.chat import ChatCompletionsCodec
from stepswitch.compat.errors import ModelError
from stepswitch.compat.framing import StreamEvent, format_sse, iter_lines, parse_sse
from stepswitch.compat.ir import (
    ConversionError,
    ConversionReport,
    Delta,
    ErrorDelta,
    FilePart,
    ImagePart,
    Message,
    NamedToolChoice,
    Origin,
    ReasoningPart,
    Request,
    Response,
    Sampling,
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
from stepswitch.compat.stream import assemble, response_to_deltas
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

FMT = "chat-completions"
ROUTE = r"/v1/chat/completions"
MODEL = "test-model"
CREATED = 1760600000
FINGERPRINT = "fp_5e1c2a07"

# --------------------------------------------------------------------------------------------
# Hand-written wire fixtures
# --------------------------------------------------------------------------------------------

USAGE_SMALL = {
    "prompt_tokens": 19,
    "completion_tokens": 4,
    "total_tokens": 23,
    "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
    "completion_tokens_details": {
        "reasoning_tokens": 0,
        "audio_tokens": 0,
        "accepted_prediction_tokens": 0,
        "rejected_prediction_tokens": 0,
    },
}

USAGE_CACHED = {
    "prompt_tokens": 2006,
    "completion_tokens": 300,
    "total_tokens": 2306,
    "prompt_tokens_details": {"cached_tokens": 1920, "audio_tokens": 0},
    "completion_tokens_details": {
        "reasoning_tokens": 128,
        "audio_tokens": 0,
        "accepted_prediction_tokens": 0,
        "rejected_prediction_tokens": 0,
    },
}

ERROR_400 = {
    "error": {
        "message": "Invalid value for 'temperature': expected a number between 0 and 2.",
        "type": "invalid_request_error",
        "param": "temperature",
        "code": "invalid_value",
    }
}
ERROR_401 = {
    "error": {
        "message": "Incorrect API key provided.",
        "type": "invalid_request_error",
        "param": None,
        "code": "invalid_api_key",
    }
}
ERROR_429 = {
    "error": {
        "message": "Rate limit reached for requests. Please try again in 7s.",
        "type": "rate_limit_error",
        "param": None,
        "code": "slow_down",
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


def completion_body(
    message: dict[str, Any],
    finish: str,
    usage: dict[str, Any] | None = None,
    completion_id: str = "chatcmpl-9fK2qXw7LmP0",
) -> dict[str, Any]:
    """A `chat.completion` object around a hand-written assistant message."""
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": CREATED,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", **message},
                "logprobs": None,
                "finish_reason": finish,
            }
        ],
        "usage": usage or USAGE_SMALL,
        "service_tier": "default",
        "system_fingerprint": FINGERPRINT,
    }


def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    """A `chat.completion.chunk` with one choice; `usage` is null except in the usage chunk."""
    return {
        "id": "chatcmpl-Bx81sQe0Tz4u",
        "object": "chat.completion.chunk",
        "created": CREATED,
        "model": MODEL,
        "system_fingerprint": FINGERPRINT,
        "choices": [{"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish}],
        "usage": None,
    }


def usage_chunk(usage: dict[str, Any]) -> dict[str, Any]:
    """The last chunk with `stream_options.include_usage`: empty `choices`, the totals."""
    return {
        "id": "chatcmpl-Bx81sQe0Tz4u",
        "object": "chat.completion.chunk",
        "created": CREATED,
        "model": MODEL,
        "system_fingerprint": FINGERPRINT,
        "choices": [],
        "usage": usage,
    }


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


def usage_view(usage: Usage | None) -> tuple[int | None, ...]:
    assert usage is not None
    return (
        usage.input_tokens,
        usage.output_tokens,
        usage.cached_input_tokens,
        usage.reasoning_tokens,
    )


def wire_round_trip(events: Iterable[StreamEvent]) -> list[StreamEvent]:
    """Serialize events as SSE text and parse them back, as a client would."""
    text = "".join(format_sse(event) for event in events)
    return list(parse_sse(iter_lines([text])))


# --------------------------------------------------------------------------------------------
# Required cases
# --------------------------------------------------------------------------------------------


def case_non_streaming() -> None:
    body = {
        "id": "chatcmpl-9fK2qXw7LmP0",
        "object": "chat.completion",
        "created": CREATED,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello there.",
                    "refusal": None,
                    "annotations": [],
                },
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": USAGE_SMALL,
        "service_tier": "default",
        "system_fingerprint": FINGERPRINT,
    }
    server = serve_json(body)
    completion = client_for(FMT, server).complete(text_request())
    sent = server.last
    assert (sent.method, sent.path) == ("POST", "/v1/chat/completions")
    assert sent.body == {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "Say hello."},
        ],
        "max_tokens": 64,
    }
    response = completion.response
    assert response.message.text_content() == ANSWER
    assert (response.finish_reason, response.raw_finish_reason) == ("stop", "stop")
    assert (response.id, response.model, response.created) == (
        "chatcmpl-9fK2qXw7LmP0",
        MODEL,
        CREATED,
    )
    assert completion.report.lossless

    # Endpoints whose profile asks for the newer length field get it instead.
    server = serve_json(body)
    options = CodecOptions(max_tokens_field="max_completion_tokens")
    client_for(FMT, server, options=options).complete(text_request())
    assert server.last.body["max_completion_tokens"] == 64
    assert "max_tokens" not in server.last.body


def case_streaming() -> None:
    events: list[dict[str, Any] | str] = [
        chunk({"role": "assistant", "content": "", "refusal": None}),
        chunk({"content": "Hel"}),
        chunk({"content": "lo the"}),
        chunk({"content": "re."}),
        chunk({}, "stop"),
        usage_chunk(USAGE_SMALL),
        "[DONE]",
    ]
    server = serve_sse(events)
    deltas = list(client_for(FMT, server).stream(text_request()))
    sent = server.last.body
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert [d.text for d in deltas if isinstance(d, TextDelta)] == ["Hel", "lo the", "re."]
    response = assemble(deltas)
    assert response.message.text_content() == ANSWER
    assert (response.finish_reason, response.raw_finish_reason) == ("stop", "stop")
    assert (response.id, response.model) == ("chatcmpl-Bx81sQe0Tz4u", MODEL)

    server = serve_sse(events)
    list(client_for(FMT, server, options=CodecOptions(stream_usage=False)).stream(text_request()))
    assert "stream_options" not in server.last.body


def case_tool_call() -> None:
    body = completion_body(
        {
            "content": None,
            "refusal": None,
            "tool_calls": [
                {
                    "id": "call_W8xk2Lq",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city": "Paris", "unit": "c"}',
                    },
                }
            ],
        },
        "tool_calls",
    )
    server = serve_json(body)
    response = client_for(FMT, server).complete(tool_request()).response
    sent = server.last.body
    assert sent["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": WEATHER.description,
                "parameters": WEATHER.parameters,
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": CLOCK.description,
                "parameters": CLOCK.parameters,
            },
        },
    ]
    assert sent["tool_choice"] == "auto"
    assert call_view(response) == [("get_weather", {"city": "Paris", "unit": "c"})]
    assert response.message.tool_calls()[0].id == "call_W8xk2Lq"
    assert response.finish_reason == "tool_calls"

    events: list[dict[str, Any] | str] = [
        chunk(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_W8xk2Lq",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": ""},
                    }
                ],
            }
        ),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"ci'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'ty": "Par'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'is", "unit": "c"}'}}]}),
        chunk({}, "tool_calls"),
        "[DONE]",
    ]
    deltas = list(client_for(FMT, serve_sse(events)).stream(tool_request()))
    assert len([d for d in deltas if isinstance(d, ToolCallArgsDelta)]) == 3
    streamed = assemble(deltas)
    assert call_view(streamed) == [("get_weather", {"city": "Paris", "unit": "c"})]
    assert streamed.message.tool_calls()[0].id == "call_W8xk2Lq"
    assert streamed.finish_reason == "tool_calls"


def case_parallel_tool_calls() -> None:
    body = completion_body(
        {
            "content": None,
            "refusal": None,
            "tool_calls": [
                {
                    "id": "call_A1pq",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                },
                {
                    "id": "call_B2rs",
                    "type": "function",
                    "function": {"name": "get_time", "arguments": '{"city": "Tokyo"}'},
                },
            ],
        },
        "tool_calls",
    )
    response = client_for(FMT, serve_json(body)).complete(tool_request()).response
    expected = [("get_weather", {"city": "Paris"}), ("get_time", {"city": "Tokyo"})]
    assert call_view(response) == expected
    assert [call.id for call in response.message.tool_calls()] == ["call_A1pq", "call_B2rs"]

    events: list[dict[str, Any] | str] = [
        chunk({"role": "assistant", "content": None}),
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_A1pq",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": ""},
                    }
                ]
            }
        ),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"city": '}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"Paris"}'}}]}),
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 1,
                        "id": "call_B2rs",
                        "type": "function",
                        "function": {"name": "get_time", "arguments": ""},
                    }
                ]
            }
        ),
        chunk({"tool_calls": [{"index": 1, "function": {"arguments": '{"city": "Tok'}}]}),
        chunk({"tool_calls": [{"index": 1, "function": {"arguments": 'yo"}'}}]}),
        chunk({}, "tool_calls"),
        "[DONE]",
    ]
    deltas = list(client_for(FMT, serve_sse(events)).stream(tool_request()))
    streamed = assemble(deltas)
    assert call_view(streamed) == expected
    ids = [call.id for call in streamed.message.tool_calls()]
    assert ids == ["call_A1pq", "call_B2rs"]
    assert streamed.finish_reason == "tool_calls"
    # The first call is closed before the second one starts.
    marks = [
        (type(d).__name__, d.index)
        for d in deltas
        if isinstance(d, ToolCallStartDelta | ToolCallEndDelta)
    ]
    assert marks == [
        ("ToolCallStartDelta", 0),
        ("ToolCallEndDelta", 0),
        ("ToolCallStartDelta", 1),
        ("ToolCallEndDelta", 1),
    ]


def case_error() -> None:
    cases: list[tuple[int, dict[str, Any], dict[str, str], str, str]] = [
        (400, ERROR_400, {}, "bad_request", "invalid_value"),
        (401, ERROR_401, {}, "authentication", "invalid_api_key"),
        (429, ERROR_429, {"retry-after": "7"}, "rate_limit", "slow_down"),
        (500, ERROR_500, {}, "server", "server_error"),  # no code: the type is kept
    ]
    for status, body, headers, kind, code in cases:
        server = serve_json(body, status, headers)
        error = raises(lambda server=server: client_for(FMT, server).complete(text_request()))
        assert (error.kind, error.status, error.code) == (kind, status, code)
        assert error.message == body["error"]["message"]
        assert error.retry_after == (7.0 if status == 429 else None)

    # A streaming request rejected before the stream starts.
    server = serve_json(ERROR_429, 429, {"retry-after": "7"})
    error = raises(lambda: list(client_for(FMT, server).stream(text_request())))
    assert (error.kind, error.retry_after) == ("rate_limit", 7.0)


def case_usage() -> None:
    body = completion_body({"content": ANSWER, "refusal": None}, "stop", USAGE_CACHED)
    usage = client_for(FMT, serve_json(body)).complete(text_request()).response.usage
    # IR input tokens include the cached ones, as `prompt_tokens` does.
    assert usage_view(usage) == (2006, 300, 1920, 128)
    assert usage is not None and usage.raw == USAGE_CACHED

    events: list[dict[str, Any] | str] = [
        chunk({"role": "assistant", "content": ""}),
        chunk({"content": ANSWER}),
        chunk({}, "stop"),
        usage_chunk(USAGE_CACHED),
        "[DONE]",
    ]
    deltas = list(client_for(FMT, serve_sse(events)).stream(text_request()))
    assert len([d for d in deltas if isinstance(d, UsageDelta)]) == 1
    streamed = assemble(deltas).usage
    assert usage_view(streamed) == (2006, 300, 1920, 128)
    assert streamed is not None and streamed.raw == USAGE_CACHED


def case_server_round_trip() -> None:
    codec = ChatCompletionsCodec()
    for build in (text_request, tool_request, conversation_request):
        original = build()
        report = ConversionReport(target=FMT)
        call = codec.encode_request(original, report)
        assert report.lossless, report.losses
        wire = json.loads(json.dumps(call.json))
        decoded = codec.decode_request(wire, path=call.path)
        assert normalize(decoded) == normalize(original)
        assert decoded.model == original.model
        assert decoded.tool_choice == original.tool_choice
        assert decoded.response_format == original.response_format
        assert decoded.max_output_tokens == original.max_output_tokens


RESPONSES = [
    Response(
        id="chatcmpl-rt1",
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
        raw_finish_reason="tool_calls",
        usage=Usage(
            input_tokens=120, output_tokens=40, cached_input_tokens=100, reasoning_tokens=12
        ),
    ),
    Response(
        id="chatcmpl-rt2",
        model=MODEL,
        message=Message.text("assistant", "A long answer that was cut off"),
        finish_reason="length",
        usage=Usage(input_tokens=12, output_tokens=64),
    ),
]


def assert_same_response(got: Response, want: Response) -> None:
    assert got.message.text_content() == want.message.text_content()
    assert call_view(got) == call_view(want)
    assert [c.id for c in got.message.tool_calls()] == [c.id for c in want.message.tool_calls()]
    assert got.finish_reason == want.finish_reason
    assert usage_view(got.usage) == usage_view(want.usage)
    assert (got.id, got.model) == (want.id, want.model)


def case_response_round_trip() -> None:
    codec = ChatCompletionsCodec()
    for original in RESPONSES:
        body = json.loads(json.dumps(codec.encode_response(original)))
        assert body["object"] == "chat.completion"
        assert_same_response(codec.decode_response(body), original)


def case_stream_round_trip() -> None:
    codec = ChatCompletionsCodec()
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
    assert isinstance(codec, ChatCompletionsCodec)
    assert (codec.key, codec.format, codec.framing) == ("chat", FMT, "sse")


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
def test_reasoning_under_both_engine_field_names(field: str) -> None:
    body = completion_body({"content": ANSWER, field: "The user wants a greeting."}, "stop")
    response = client_for(FMT, serve_json(body)).complete(text_request()).response
    reasoning = response.message.reasoning()
    assert [part.text for part in reasoning] == ["The user wants a greeting."]
    # The client adds the endpoint, so signed reasoning goes back only where it came from.
    assert reasoning[0].origin == Origin(format=FMT, endpoint="mock", model=MODEL)
    assert response.message.text_content() == ANSWER
    # Reasoning comes first, as the model produced it.
    assert isinstance(response.message.content[0], ReasoningPart)

    events: list[dict[str, Any] | str] = [
        chunk({"role": "assistant", "content": ""}),
        chunk({"content": "", field: "The user "}),
        chunk({"content": "", field: "wants a greeting."}),
        chunk({"content": ANSWER}),
        chunk({}, "stop"),
        "[DONE]",
    ]
    streamed = assemble(client_for(FMT, serve_sse(events)).stream(text_request()))
    assert [part.text for part in streamed.message.reasoning()] == ["The user wants a greeting."]
    assert streamed.message.text_content() == ANSWER


def encode(request: Request, **extra: Any) -> tuple[dict[str, Any], ConversionReport]:
    codec = ChatCompletionsCodec(CodecOptions(extra=extra))
    report = ConversionReport(target=FMT)
    body = codec.encode_request(request, report).json
    assert body is not None
    return body, report


def test_server_timings_are_kept_with_the_usage() -> None:
    """llama.cpp adds a top-level `timings` object; it is kept in `Usage.raw["timings"]`."""
    timings = {"prompt_n": 512, "prompt_ms": 400.0, "predicted_n": 64, "predicted_ms": 2000.0}
    body = {**completion_body({"content": ANSWER}, "stop", USAGE_SMALL), "timings": timings}
    usage = client_for(FMT, serve_json(body)).complete(text_request()).response.usage
    assert usage is not None and usage.raw is not None
    assert usage.raw["timings"] == timings
    assert usage.input_tokens == USAGE_SMALL["prompt_tokens"]

    events: list[dict[str, Any] | str] = [
        chunk({"role": "assistant", "content": ""}),
        chunk({"content": ANSWER}, "stop"),
        {**usage_chunk(USAGE_SMALL), "timings": timings},
        "[DONE]",
    ]
    streamed = assemble(list(client_for(FMT, serve_sse(events)).stream(text_request()))).usage
    assert streamed is not None and streamed.raw is not None
    assert streamed.raw["timings"] == timings


def test_effort_is_sent_and_clamped_to_the_endpoint_levels() -> None:
    body, report = encode(text_request().model_copy(update={"effort": "xhigh"}))
    assert body["reasoning_effort"] == "xhigh" and report.lossless

    levels = ["low", "medium", "high"]
    expected = {"none": "low", "minimal": "low", "medium": "medium", "xhigh": "high", "max": "high"}
    for asked, sent in expected.items():
        body, report = encode(
            text_request().model_copy(update={"effort": asked}), effort_levels=levels
        )
        assert body["reasoning_effort"] == sent
        actions = [(loss.field, loss.action) for loss in report.losses]
        assert actions == ([] if asked == sent else [("effort", "approximated")])

    # Equal distance goes to the higher level.
    body, _ = encode(
        text_request().model_copy(update={"effort": "medium"}), effort_levels=["low", "high"]
    )
    assert body["reasoning_effort"] == "high"

    body, report = encode(text_request().model_copy(update={"effort": "high"}), effort_levels=[])
    assert "reasoning_effort" not in body
    assert [loss.field for loss in report.losses] == ["effort"]


def lossy_request() -> Request:
    return Request(
        model=MODEL,
        messages=[
            Message.text("user", "What is the weather?"),
            Message(
                role="assistant",
                content=[
                    ReasoningPart(
                        text="Check the tool.",
                        opaque="c2lnbmF0dXJl",
                        opaque_kind="signature",
                        origin=Origin(format="messages", model="another-model"),
                    ),
                    ToolCallPart(id="call_1", name="get_weather", arguments='{"city":"Paris"}'),
                ],
            ),
            Message(
                role="tool",
                content=[
                    ToolResultPart(
                        call_id="call_1",
                        name="get_weather",
                        is_error=True,
                        content=[
                            TextPart(text="upstream timeout"),
                            ImagePart(url="https://example.invalid/radar.png"),
                        ],
                    )
                ],
            ),
        ],
        tools=[WEATHER],
        sampling=Sampling(top_k=40),
        budget_tokens=2048,
        extensions={"messages": {"metadata": {"user_id": "u-1"}}},
    )


def test_losses_are_reported_and_strict_mode_rejects_them() -> None:
    body, report = encode(lossy_request())
    assert {(loss.field, loss.action) for loss in report.losses} == {
        ("messages.1.content.0.opaque", "dropped"),
        ("messages.1.content.0", "dropped"),
        ("messages.2.content.0.content.1", "dropped"),
        ("messages.2.content.0.is_error", "dropped"),
        ("sampling.top_k", "dropped"),
        ("budget_tokens", "dropped"),
        ("extensions.messages", "dropped"),
    }
    assert "top_k" not in body and "metadata" not in body
    assert body["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "upstream timeout",
    }
    assert "reasoning_content" not in body["messages"][1]

    with pytest.raises(ConversionError):
        ChatCompletionsCodec(CodecOptions(strict=True)).encode_request(
            lossy_request(), ConversionReport()
        )

    # Engines that accept top_k get it.
    body, report = encode(
        text_request().model_copy(update={"sampling": Sampling(top_k=40)}), accepts_top_k=True
    )
    assert body["top_k"] == 40 and report.lossless


def test_prior_reasoning_goes_back_only_to_the_same_model() -> None:
    def history(origin_model: str) -> Request:
        request = conversation_request()
        first = request.messages[1]
        thought = ReasoningPart(text="Two lookups.", origin=Origin(format=FMT, model=origin_model))
        request.messages[1] = first.model_copy(update={"content": [thought, *first.content]})
        return request

    body, report = encode(history(MODEL))
    assert "reasoning_content" not in body["messages"][2]
    assert [loss.field for loss in report.losses] == ["messages.1.content.0"]

    body, report = encode(history(MODEL), reasoning_field="reasoning")
    assert body["messages"][2]["reasoning"] == "Two lookups." and report.lossless

    body, report = encode(history("another-model"), reasoning_field="reasoning")
    assert "reasoning" not in body["messages"][2]
    assert [loss.field for loss in report.losses] == ["messages.1.content.0"]


def test_missing_tool_call_ids_are_synthesized_per_turn() -> None:
    # Some servers send no ids. The request has two assistant turns, so the answer is turn 2.
    events: list[dict[str, Any] | str] = [
        chunk(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Lyon"}'},
                    }
                ],
            }
        ),
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 1,
                        "type": "function",
                        "function": {"name": "get_time", "arguments": '{"city": "Oslo"}'},
                    }
                ]
            }
        ),
        chunk({}, "tool_calls"),
        "[DONE]",
    ]
    streamed = assemble(client_for(FMT, serve_sse(events)).stream(conversation_request()))
    calls = streamed.message.tool_calls()
    assert [(c.id, c.synthetic) for c in calls] == [("call_t2_0", True), ("call_t2_1", True)]

    body = completion_body(
        {
            "content": None,
            "tool_calls": [
                {"type": "function", "function": {"name": "get_weather", "arguments": "{}"}},
                {"type": "function", "function": {"name": "get_time", "arguments": "{}"}},
            ],
        },
        "stop",  # some servers report `stop` for a tool-call turn
    )
    response = client_for(FMT, serve_json(body)).complete(tool_request()).response
    assert [(c.id, c.synthetic) for c in response.message.tool_calls()] == [
        ("call_t0_0", True),
        ("call_t0_1", True),
    ]
    assert (response.finish_reason, response.raw_finish_reason) == ("tool_calls", "stop")


def test_calls_that_reuse_index_zero_stay_separate() -> None:
    # A server quirk: every call is numbered 0, but each has its own id.
    events: list[dict[str, Any] | str] = [
        chunk(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_x",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Rome"}'},
                    }
                ],
            }
        ),
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_y",
                        "type": "function",
                        "function": {"name": "get_time", "arguments": '{"city": "Lima"}'},
                    }
                ]
            }
        ),
        chunk({}, "tool_calls"),
        "[DONE]",
    ]
    streamed = assemble(client_for(FMT, serve_sse(events)).stream(tool_request()))
    assert call_view(streamed) == [
        ("get_weather", {"city": "Rome"}),
        ("get_time", {"city": "Lima"}),
    ]
    assert [c.id for c in streamed.message.tool_calls()] == ["call_x", "call_y"]


@pytest.mark.parametrize(
    ("status", "body", "kind", "message"),
    [
        (404, {"error": "model 'missing' not found"}, "not_found", "model 'missing' not found"),
        (
            400,
            {
                "error": {
                    "message": "max_tokens must be at least 1, got 0.",
                    "type": "BadRequestError",
                    "param": None,
                    "code": 400,
                }
            },
            "bad_request",
            "max_tokens must be at least 1, got 0.",
        ),
        (
            400,
            {
                "object": "error",
                "message": "The model does not exist.",
                "type": "NotFoundError",
                "param": None,
                "code": 404,
            },
            "bad_request",
            "The model does not exist.",
        ),
        (
            503,
            {"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}},
            "overloaded",
            "Loading model",
        ),
    ],
)
def test_error_body_variants(status: int, body: Any, kind: str, message: str) -> None:
    error = raises(lambda: client_for(FMT, serve_json(body, status)).complete(text_request()))
    assert (error.kind, error.status, error.message) == (kind, status, message)


def test_plain_text_error_and_header_case() -> None:
    server = MockServer().route(
        "POST",
        ROUTE,
        lambda _r: httpx.Response(502, text="upstream connect error", headers={"Retry-After": "3"}),
    )
    error = raises(lambda: client_for(FMT, server).complete(text_request()))
    assert (error.kind, error.message, error.retry_after) == (
        "server",
        "upstream connect error",
        3.0,
    )
    direct = ChatCompletionsCodec().decode_error(429, ERROR_429, {"Retry-After": "2"})
    assert (direct.kind, direct.retry_after, direct.code) == ("rate_limit", 2.0, "slow_down")


def test_errors_inside_a_stream() -> None:
    events: list[dict[str, Any] | str] = [
        chunk({"role": "assistant", "content": ""}),
        chunk({"content": "Hel"}),
        ERROR_500,
        "[DONE]",
    ]
    deltas = list(client_for(FMT, serve_sse(events)).stream(text_request()))
    last = deltas[-1]
    assert isinstance(last, ErrorDelta) and last.kind == "server"
    with pytest.raises(ModelError) as info:
        assemble(deltas)
    assert info.value.message == ERROR_500["error"]["message"]

    # A stream cut off before any finish reason is an error, not a silent "stop".
    cut = [chunk({"role": "assistant", "content": ""}), chunk({"content": "Hel"})]
    deltas = list(ChatCompletionsCodec().decode_stream(StreamEvent(json.dumps(c)) for c in cut))
    last = deltas[-1]
    assert isinstance(last, ErrorDelta) and last.kind == "invalid_response"


def test_server_stream_shape() -> None:
    codec = ChatCompletionsCodec()
    original = RESPONSES[0]
    events = list(codec.encode_stream(response_to_deltas(original)))
    assert events[-1].data == "[DONE]"
    chunks = [json.loads(e.data) for e in events[:-1]]
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert {c["id"] for c in chunks} == {"chatcmpl-rt1"}
    finish = [c for c in chunks if c["choices"] and c["choices"][0]["finish_reason"]]
    assert [c["choices"][0]["finish_reason"] for c in finish] == ["tool_calls"]
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"]["prompt_tokens"] == 120

    quiet = ChatCompletionsCodec(CodecOptions(stream_usage=False))
    events = list(quiet.encode_stream(response_to_deltas(original)))
    assert all(json.loads(e.data).get("choices") for e in events[:-1])

    failing: list[Delta] = [TextDelta(text="Hel"), ErrorDelta(kind="overloaded", message="busy")]
    events = list(codec.encode_stream(failing))
    assert json.loads(events[-2].data)["error"]["type"] == "service_unavailable_error"
    assert events[-1].data == "[DONE]"


def test_encode_error_shape() -> None:
    codec = ChatCompletionsCodec()
    status, body = codec.encode_error(ModelError("rate_limit", "slow down", status=429))
    assert status == 429
    assert body == {
        "error": {"message": "slow down", "type": "rate_limit_error", "param": None, "code": None}
    }
    assert codec.encode_error(ModelError("timeout", "no answer"))[0] == 504


def test_decode_request_maps_a_harness_request() -> None:
    body = {
        "model": "ssw:auto",
        "messages": [
            {"role": "developer", "content": [{"type": "text", "text": "Be brief."}]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in these?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.invalid/a.png", "detail": "low"},
                    },
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ"}},
                    {
                        "type": "file",
                        "file": {
                            "filename": "spec.pdf",
                            "file_data": "data:application/pdf;base64,JVBERi0x",
                        },
                    },
                    {"type": "file", "file": {"file_id": "file-abc123"}},
                ],
            },
            {
                "role": "assistant",
                "content": "Checking.",
                "reasoning_content": "Look at the images first.",
                "tool_calls": [
                    {
                        "id": "call_7",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Nice"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_7",
                "content": [{"type": "text", "text": "sunny"}],
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {"name": "get_weather", "parameters": WEATHER.parameters},
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        "parallel_tool_calls": False,
        "max_completion_tokens": 300,
        "temperature": 0.2,
        "top_p": 0.9,
        "seed": 7,
        "stop": "END",
        "reasoning_effort": "low",
        "stream": True,
        "stream_options": {"include_usage": True},
        "user": "dev-1",
        "metadata": {"run": "7"},
    }
    request = ChatCompletionsCodec().decode_request(body, path="/v1/chat/completions")
    assert [part.text for part in request.system] == ["Be brief."]
    user = request.messages[0].content
    assert user[1] == ImagePart(url="https://example.invalid/a.png", detail="low")
    assert user[2] == ImagePart(data="/9j/4AAQ", media_type="image/jpeg")
    assert user[3] == FilePart(data="JVBERi0x", media_type="application/pdf", name="spec.pdf")
    assert user[4] == FilePart(file_id="file-abc123")
    assistant = request.messages[1]
    assert assistant.reasoning()[0].text == "Look at the images first."
    result = request.messages[2].tool_results()[0]
    assert (result.call_id, result.name, result.text()) == ("call_7", "get_weather", "sunny")
    assert request.tool_choice == NamedToolChoice(name="get_weather")
    assert request.parallel_tool_calls is False
    assert request.max_output_tokens == 300
    assert request.sampling == Sampling(temperature=0.2, top_p=0.9, seed=7, stop=["END"])
    assert request.effort == "low" and request.stream is True
    assert request.extensions == {FMT: {"user": "dev-1", "metadata": {"run": "7"}}}

    # Same-format upstreams get the unmapped fields back verbatim.
    sent, report = encode(request.model_copy(update={"model": MODEL}))
    assert sent["user"] == "dev-1" and sent["metadata"] == {"run": "7"}
    assert (
        sent["messages"][1]["content"][3]["file"]["file_data"]
        == "data:application/pdf;base64,JVBERi0x"
    )
    assert [loss.field for loss in report.losses] == ["messages.1.content.0"]  # reasoning


@pytest.mark.parametrize(
    "patch",
    [
        {"n": 2},
        {"logprobs": True},
        {"functions": [{"name": "f", "parameters": {}}]},
        {"tools": [{"type": "custom", "custom": {"name": "grep"}}]},
        {"tool_choice": {"type": "allowed_tools", "allowed_tools": {"mode": "auto", "tools": []}}},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": "UklG", "format": "wav"}}
                    ],
                }
            ]
        },
    ],
)
def test_decode_request_rejects_what_the_ir_cannot_carry(patch: dict[str, Any]) -> None:
    body = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], **patch}
    with pytest.raises(ConversionError):
        ChatCompletionsCodec().decode_request(body)


def test_images_and_files_encode_by_reference() -> None:
    request = Request(
        model=MODEL,
        messages=[
            Message(
                role="user",
                content=[
                    TextPart(text="Compare."),
                    ImagePart(url="https://example.invalid/a.png", detail="high"),
                    ImagePart(data="iVBORw0KGgo", media_type="image/png"),
                    FilePart(file_id="file-xyz"),
                    FilePart(data="JVBERi0x", media_type="application/pdf", name="spec.pdf"),
                    FilePart(url="https://example.invalid/spec.pdf"),
                ],
            )
        ],
    )
    body, report = encode(request)
    assert body["messages"][0]["content"] == [
        {"type": "text", "text": "Compare."},
        {
            "type": "image_url",
            "image_url": {"url": "https://example.invalid/a.png", "detail": "high"},
        },
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo"}},
        {"type": "file", "file": {"file_id": "file-xyz"}},
        {
            "type": "file",
            "file": {"file_data": "data:application/pdf;base64,JVBERi0x", "filename": "spec.pdf"},
        },
    ]
    # Files by URL have no field in this format.
    assert [(loss.field, loss.action) for loss in report.losses] == [
        ("messages.0.content.5", "dropped")
    ]
    decoded = ChatCompletionsCodec().decode_request(body)
    assert decoded.messages[0].content == request.messages[0].content[:5]
