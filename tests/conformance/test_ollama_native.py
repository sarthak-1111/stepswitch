"""Conformance suite for the Ollama native format (claim C-004).

Mock bodies and NDJSON lines are written by hand in the wire shapes of the chat reference
(https://docs.ollama.com/api/chat) and of a local Ollama 0.34.4 server observed on 2026-10-16
(docs/RESEARCH.md, "Ollama native API"); the codec under test never produces them.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from typing import Any

import httpx
import pytest

from stepswitch import net
from stepswitch.compat.client import Endpoint, ModelClient
from stepswitch.compat.codecs import get_codec
from stepswitch.compat.codecs.base import CodecOptions
from stepswitch.compat.codecs.ollama import OllamaCodec
from stepswitch.compat.errors import ModelError
from stepswitch.compat.framing import StreamEvent, format_ndjson, iter_lines, parse_ndjson
from stepswitch.compat.ir import (
    ConversionError,
    ConversionReport,
    Effort,
    ErrorDelta,
    FilePart,
    FinishDelta,
    ImagePart,
    Message,
    NamedToolChoice,
    Origin,
    ReasoningDelta,
    ReasoningPart,
    Request,
    Response,
    ResponseFormat,
    Sampling,
    StartDelta,
    TextDelta,
    TextPart,
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
    ndjson_response,
    normalize,
    text_request,
    tool_request,
)

FORMAT = "ollama-native"
CHAT = r"/api/chat"

# Tool definitions exactly as the server expects them (`type: function` plus the function).
WIRE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "unit": {"type": "string", "enum": ["c", "f"]},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the local time in a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
]

# A non-streamed answer as 0.34.4 returned it (the first request after loading the model).
TEXT_BODY = {
    "model": "test-model",
    "created_at": "2026-10-16T04:51:10.7728029Z",
    "message": {"role": "assistant", "content": "Hello there."},
    "done": True,
    "done_reason": "stop",
    "total_duration": 17999963800,
    "load_duration": 17795767200,
    "prompt_eval_count": 20,
    "prompt_eval_cached_count": 0,
    "prompt_eval_duration": 106732000,
    "eval_count": 3,
    "eval_duration": 88769000,
}


def partial(content: str = "", **message: Any) -> dict[str, Any]:
    """A streamed line: the partial message with its role, and `done: false`."""
    return {
        "model": "test-model",
        "created_at": "2026-10-16T04:51:24.5484018Z",
        "message": {"role": "assistant", "content": content, **message},
        "done": False,
    }


def final(**fields: Any) -> dict[str, Any]:
    """The last streamed line: empty content, `done: true`, the reason, counts and durations."""
    return {
        "model": "test-model",
        "created_at": "2026-10-16T04:51:24.9682304Z",
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": "stop",
        "total_duration": 499399600,
        "load_duration": 6462400,
        "prompt_eval_count": 40,
        "prompt_eval_cached_count": 5,
        "prompt_eval_duration": 60398000,
        "eval_count": 14,
        "eval_duration": 419630000,
        **fields,
    }


def serve_json(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> MockServer:
    return MockServer().route("POST", CHAT, lambda _r: json_response(body, status, headers))


def serve_ndjson(lines: list[dict[str, Any]]) -> MockServer:
    return MockServer().route("POST", CHAT, lambda _r: ndjson_response(lines))


def raises(action: Callable[[], object]) -> ModelError:
    with pytest.raises(ModelError) as info:
        action()
    return info.value


def call_view(response: Response) -> list[tuple[str, dict[str, Any]]]:
    return [(call.name, json.loads(call.arguments)) for call in response.message.tool_calls()]


def wire_round_trip(events: Iterable[StreamEvent]) -> list[StreamEvent]:
    """Serialize events as NDJSON text and parse them back, as a client would."""
    text = "".join(format_ndjson(event) for event in events)
    return list(parse_ndjson(iter_lines([text])))


# --------------------------------------------------------------------------------------------
# Required cases
# --------------------------------------------------------------------------------------------


def case_non_streaming() -> None:
    server = serve_json(TEXT_BODY)
    completion = client_for(FORMAT, server).complete(text_request())
    sent = server.last
    assert (sent.method, sent.path) == ("POST", "/api/chat")
    assert sent.body == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "Say hello."},
        ],
        "options": {"num_predict": 64},
        "stream": False,  # explicit: the server streams by default
    }
    response = completion.response
    assert response.message.text_content() == ANSWER
    assert response.message.role == "assistant"
    assert (response.finish_reason, response.raw_finish_reason) == ("stop", "stop")
    assert response.model == "test-model"
    assert response.id is None  # the native format has no response ID
    assert response.created == 1792126270
    assert completion.report.lossless


def case_streaming() -> None:
    server = serve_ndjson([partial("Hel"), partial("lo the"), partial("re."), final()])
    deltas = list(client_for(FORMAT, server).stream(text_request()))
    assert server.last.body["stream"] is True
    assert isinstance(deltas[0], StartDelta) and deltas[0].model == "test-model"
    assert [d.text for d in deltas if isinstance(d, TextDelta)] == ["Hel", "lo the", "re."]
    assert isinstance(deltas[-1], FinishDelta)
    response = assemble(deltas)
    assert response.message.text_content() == ANSWER
    assert (response.finish_reason, response.raw_finish_reason) == ("stop", "stop")

    # Thinking streams first in `thinking`, then the answer in `content` (gpt-oss on 0.34.4).
    lines = [
        partial("", thinking="Just"),
        partial("", thinking=" answer"),
        partial("", thinking=" 5."),
        partial("5"),
        final(),
    ]
    server = serve_ndjson(lines)
    request = text_request().model_copy(update={"effort": "low"})
    deltas = list(client_for(FORMAT, server).stream(request))
    assert server.last.body["think"] == "low"
    assert [d.text for d in deltas if isinstance(d, ReasoningDelta)] == ["Just", " answer", " 5."]
    response = assemble(deltas)
    [reasoning] = response.message.reasoning()
    assert reasoning.text == "Just answer 5."
    assert response.message.text_content() == "5"


def case_tool_call() -> None:
    # The reference shape: no call ID, `arguments` is an object.
    body = {
        "model": "test-model",
        "created_at": "2026-10-16T04:51:33.847559Z",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "get_weather", "arguments": {"city": "Paris", "unit": "c"}}}
            ],
        },
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 196,
        "eval_count": 21,
    }
    server = serve_json(body)
    response = client_for(FORMAT, server).complete(tool_request()).response
    sent = server.last.body
    assert sent["tools"] == WIRE_TOOLS
    assert "tool_choice" not in sent  # "auto" is the server's only behaviour
    assert sent["options"] == {"num_predict": 128}
    [call] = response.message.tool_calls()
    assert call.name == "get_weather"
    assert json.loads(call.arguments) == {"city": "Paris", "unit": "c"}
    assert call.id and call.synthetic
    assert (response.finish_reason, response.raw_finish_reason) == ("tool_calls", "stop")

    # Current servers add an ID and an index (0.34.4); the ID is kept as is.
    body["message"]["tool_calls"] = [
        {
            "id": "call_u2456s1r",
            "function": {"index": 0, "name": "get_weather", "arguments": {"city": "Paris"}},
        }
    ]
    response = client_for(FORMAT, serve_json(body)).complete(tool_request()).response
    [call] = response.message.tool_calls()
    assert (call.id, call.synthetic) == ("call_u2456s1r", False)
    assert json.loads(call.arguments) == {"city": "Paris"}

    # Streaming: the call arrives whole in one line.
    lines = [
        partial(
            "",
            tool_calls=[
                {"function": {"index": 0, "name": "get_weather", "arguments": {"city": "Paris"}}}
            ],
        ),
        final(),
    ]
    deltas = list(client_for(FORMAT, serve_ndjson(lines)).stream(tool_request()))
    [start] = [d for d in deltas if isinstance(d, ToolCallStartDelta)]
    assert start.id and start.synthetic and start.name == "get_weather"
    response = assemble(deltas)
    assert call_view(response) == [("get_weather", {"city": "Paris"})]
    assert response.finish_reason == "tool_calls"


def case_parallel_tool_calls() -> None:
    body = {
        "model": "test-model",
        "created_at": "2026-10-16T04:51:33.847559Z",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "get_weather", "arguments": {"city": "Paris"}}},
                {"function": {"name": "get_time", "arguments": {"city": "Tokyo"}}},
            ],
        },
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 196,
        "eval_count": 47,
    }
    response = client_for(FORMAT, serve_json(body)).complete(tool_request()).response
    calls = response.message.tool_calls()
    assert call_view(response) == [
        ("get_weather", {"city": "Paris"}),
        ("get_time", {"city": "Tokyo"}),
    ]
    assert all(call.id and call.synthetic for call in calls)
    assert len({call.id for call in calls}) == 2
    assert response.finish_reason == "tool_calls"

    # Streaming as 0.34.4 sends it: one line per call, each with its own ID.
    lines = [
        partial(
            "",
            tool_calls=[
                {
                    "id": "call_ubrv5kvj",
                    "function": {
                        "index": 0,
                        "name": "get_weather",
                        "arguments": {"unit": "c", "city": "Paris"},
                    },
                }
            ],
        ),
        partial(
            "",
            tool_calls=[
                {
                    "id": "call_xhxu9783",
                    "function": {"index": 1, "name": "get_time", "arguments": {"city": "Tokyo"}},
                }
            ],
        ),
        final(prompt_eval_count=196, prompt_eval_cached_count=195, eval_count=47),
    ]
    response = assemble(client_for(FORMAT, serve_ndjson(lines)).stream(tool_request()))
    assert call_view(response) == [
        ("get_weather", {"unit": "c", "city": "Paris"}),
        ("get_time", {"city": "Tokyo"}),
    ]
    assert [call.id for call in response.message.tool_calls()] == ["call_ubrv5kvj", "call_xhxu9783"]
    assert response.finish_reason == "tool_calls"

    # Without IDs the streamed calls still get distinct ones.
    for line in lines[:2]:
        del line["message"]["tool_calls"][0]["id"]
    response = assemble(client_for(FORMAT, serve_ndjson(lines)).stream(tool_request()))
    ids = [call.id for call in response.message.tool_calls()]
    assert len(set(ids)) == 2 and all(ids)


def case_error() -> None:
    cases: list[tuple[int, str, dict[str, str], str, float | None]] = [
        (
            400,
            'invalid think value: "extreme" '
            '(must be "high", "medium", "low", "max", true, or false)',
            {},
            "bad_request",
            None,
        ),
        (400, '"test-model" does not support thinking', {}, "unsupported", None),
        (401, "unauthorized", {}, "authentication", None),
        (404, "model 'test-model' not found", {}, "not_found", None),
        (429, "too many requests", {"retry-after": "7"}, "rate_limit", 7.0),
        (500, "an error was encountered while running the model", {}, "server", None),
        (
            503,
            "server busy, please try again.  maximum pending requests exceeded",
            {},
            "overloaded",
            None,
        ),
    ]
    for status, message, headers, kind, retry_after in cases:
        server = serve_json({"error": message}, status, headers)
        error = raises(lambda s=server: client_for(FORMAT, s).complete(text_request()))
        assert (error.kind, error.status, error.message) == (kind, status, message)
        assert error.retry_after == retry_after
    # An error before the stream starts is a plain JSON body with the status.
    server = serve_json({"error": "model 'test-model' not found"}, 404)
    error = raises(lambda: list(client_for(FORMAT, server).stream(text_request())))
    assert (error.kind, error.status) == ("not_found", 404)
    # A mid-stream error is an NDJSON line with `error`; the status stays 200.
    server = MockServer().route(
        "POST",
        CHAT,
        lambda _r: ndjson_response(
            [partial("Hel"), {"error": "an error was encountered while running the model"}]
        ),
    )
    deltas = list(client_for(FORMAT, server).stream(text_request()))
    errors = [d for d in deltas if isinstance(d, ErrorDelta)]
    assert [(e.kind, e.message) for e in errors] == [
        ("server", "an error was encountered while running the model")
    ]
    assert raises(lambda: assemble(deltas)).kind == "server"


def case_usage() -> None:
    # A repeated prompt on 0.34.4: the whole prompt is counted, most of it from the cache.
    body = dict(TEXT_BODY, prompt_eval_count=196, prompt_eval_cached_count=195, eval_count=47)
    usage = client_for(FORMAT, serve_json(body)).complete(text_request()).response.usage
    assert usage is not None
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (196, 195, 47)
    assert usage.total_tokens == 243
    assert usage.raw is not None and usage.raw["prompt_eval_duration"] == 106732000
    assert usage.raw["load_duration"] == 17795767200
    # Streaming: the counts come with the final line only.
    lines = [partial("Hello"), partial(" there."), final()]
    deltas = list(client_for(FORMAT, serve_ndjson(lines)).stream(text_request()))
    [usage_delta] = [d for d in deltas if isinstance(d, UsageDelta)]
    usage = usage_delta.usage
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (40, 5, 14)
    assert usage.raw is not None and usage.raw["eval_duration"] == 419630000
    # Zero counts are omitted on the wire (`omitempty`); a body without the cached count is
    # from a server that predates it.
    body = {key: value for key, value in TEXT_BODY.items() if key != "prompt_eval_cached_count"}
    body["eval_count"] = 0
    del body["eval_count"]
    usage = client_for(FORMAT, serve_json(body)).complete(text_request()).response.usage
    assert usage is not None
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (20, None, 0)


def case_server_round_trip() -> None:
    codec = OllamaCodec()
    for request in (text_request(), tool_request(), conversation_request()):
        report = ConversionReport(target=FORMAT)
        call = codec.encode_request(request, report)
        assert report.lossless, report.losses
        assert call.json is not None
        decoded = codec.decode_request(json.loads(json.dumps(call.json)), path="/api/chat")
        assert normalize(decoded) == normalize(request)
        assert decoded.max_output_tokens == request.max_output_tokens
    # Current servers carry call IDs, so they survive the round trip too.
    decoded = codec.decode_request(
        codec.encode_request(conversation_request(), ConversionReport()).json
    )
    assert [c.id for c in decoded.messages[1].tool_calls()] == ["call_a", "call_b"]
    assert [m.tool_results()[0].call_id for m in decoded.messages[2:4]] == ["call_a", "call_b"]
    assert decoded.response_format is not None and decoded.response_format.type == "json_schema"

    # A request as a client writes it from the reference: results matched by `tool_name`.
    body = {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "Use tools when needed."},
            {"role": "user", "content": "Weather in Paris and time in Tokyo?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "index": 0,
                            "name": "get_weather",
                            "arguments": {"city": "Paris"},
                        }
                    },
                    {"function": {"index": 1, "name": "get_time", "arguments": {"city": "Tokyo"}}},
                ],
            },
            {"role": "tool", "tool_name": "get_time", "content": "21:04"},
            {"role": "tool", "tool_name": "get_weather", "content": "18 C, sunny"},
        ],
        "tools": WIRE_TOOLS,
        "think": False,
        "format": "json",
        "options": {"num_ctx": 8192, "num_predict": 128, "temperature": 0.2, "min_p": 0.05},
        "keep_alive": "10m",
    }
    request = codec.decode_request(body)
    assert request.stream is True  # the server's default when `stream` is absent
    assert request.system_text() == "Use tools when needed."
    calls = request.messages[1].tool_calls()
    assert [(c.name, json.loads(c.arguments), c.synthetic) for c in calls] == [
        ("get_weather", {"city": "Paris"}, True),
        ("get_time", {"city": "Tokyo"}, True),
    ]
    results = [m.tool_results()[0] for m in request.messages[2:]]
    assert [(r.name, r.call_id, r.text()) for r in results] == [
        ("get_time", calls[1].id, "21:04"),
        ("get_weather", calls[0].id, "18 C, sunny"),
    ]
    assert request.tools == [WEATHER, CLOCK]
    assert request.effort == "none"
    assert request.response_format == ResponseFormat(type="json_object")
    assert (request.max_output_tokens, request.sampling.temperature) == (128, 0.2)
    assert request.extensions == {"num_ctx": 8192, "options": {"min_p": 0.05}, "keep_alive": "10m"}
    # ... and encoding it again gives the same native request (plus the call IDs).
    again = codec.encode_request(request.model_copy(update={"stream": False}), ConversionReport())
    assert again.json is not None
    assert again.json["options"] == body["options"]
    assert (again.json["think"], again.json["format"], again.json["keep_alive"]) == (
        False,
        "json",
        "10m",
    )
    assert [m.get("tool_call_id") for m in again.json["messages"][3:]] == [calls[1].id, calls[0].id]


def ir_response() -> Response:
    return Response(
        model="test-model",
        created=1792126270,
        message=Message(
            role="assistant",
            content=[
                ReasoningPart(
                    text="Two lookups are needed.",
                    origin=Origin(format=FORMAT, model="test-model"),
                ),
                TextPart(text="Checking both cities."),
                ToolCallPart(id="call_a", name="get_weather", arguments='{"city":"Paris"}'),
                ToolCallPart(id="call_b", name="get_time", arguments='{"city":"Tokyo"}'),
            ],
        ),
        finish_reason="tool_calls",
        raw_finish_reason="stop",
        usage=Usage(input_tokens=196, output_tokens=47, cached_input_tokens=195),
    )


def case_response_round_trip() -> None:
    codec = OllamaCodec()
    original = ir_response()
    body = codec.encode_response(original)
    assert body["message"]["tool_calls"][1] == {
        "id": "call_b",
        "function": {"index": 1, "name": "get_time", "arguments": {"city": "Tokyo"}},
    }
    assert (body["done"], body["done_reason"]) == (True, "stop")
    assert (body["prompt_eval_count"], body["prompt_eval_cached_count"], body["eval_count"]) == (
        196,
        195,
        47,
    )
    decoded = codec.decode_response(json.loads(json.dumps(body)))
    assert decoded.message == original.message
    assert (decoded.model, decoded.created) == (original.model, original.created)
    assert (decoded.finish_reason, decoded.raw_finish_reason) == ("tool_calls", "stop")
    assert decoded.usage is not None
    assert (
        decoded.usage.input_tokens,
        decoded.usage.cached_input_tokens,
        decoded.usage.output_tokens,
    ) == (
        196,
        195,
        47,
    )


def case_stream_round_trip() -> None:
    codec = OllamaCodec()
    original = ir_response()
    events = wire_round_trip(codec.encode_stream(response_to_deltas(original, chunk=3)))
    lines = [json.loads(event.data) for event in events]
    assert [line["done"] for line in lines] == [False] * (len(lines) - 1) + [True]
    rebuilt = assemble(codec.decode_stream(events))
    assert rebuilt.message.text_content() == "Checking both cities."
    assert [p.text for p in rebuilt.message.reasoning()] == ["Two lookups are needed."]
    assert [(c.id, c.name, c.arguments) for c in rebuilt.message.tool_calls()] == [
        ("call_a", "get_weather", '{"city":"Paris"}'),
        ("call_b", "get_time", '{"city":"Tokyo"}'),
    ]
    assert (rebuilt.model, rebuilt.finish_reason, rebuilt.raw_finish_reason) == (
        "test-model",
        "tool_calls",
        "stop",
    )
    assert rebuilt.usage is not None
    assert (
        rebuilt.usage.input_tokens,
        rebuilt.usage.cached_input_tokens,
        rebuilt.usage.output_tokens,
    ) == (
        196,
        195,
        47,
    )


SUITE = FormatSuite(
    format=FORMAT,
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


def test_codec_lookup() -> None:
    codec = get_codec(FORMAT)
    assert isinstance(codec, OllamaCodec)
    assert (codec.key, codec.format, codec.framing) == ("ollama", FORMAT, "ndjson")


# --------------------------------------------------------------------------------------------
# Focused tests
# --------------------------------------------------------------------------------------------


def encode(request: Request, **options: Any) -> tuple[dict[str, Any], ConversionReport]:
    report = ConversionReport(target=FORMAT)
    call = OllamaCodec(CodecOptions(**options)).encode_request(request, report)
    assert (call.method, call.path) == ("POST", "/api/chat")
    assert call.json is not None
    return call.json, report


def losses(report: ConversionReport) -> list[tuple[str, str]]:
    return [(loss.field, loss.action) for loss in report.losses]


@pytest.mark.parametrize(
    ("effort", "think", "action"),
    [
        ("none", False, None),
        ("minimal", "low", "approximated"),  # the server rejects "minimal"
        ("low", "low", None),
        ("medium", "medium", None),
        ("high", "high", None),
        ("xhigh", "max", "approximated"),  # the server rejects "xhigh"
        ("max", "max", None),
    ],
)
def test_effort_maps_to_think(effort: Effort, think: bool | str, action: str | None) -> None:
    body, report = encode(text_request().model_copy(update={"effort": effort}))
    assert body["think"] == think
    assert losses(report) == ([("effort", action)] if action else [])


def test_no_effort_sends_no_think() -> None:
    body, report = encode(text_request())
    assert "think" not in body and report.lossless


@pytest.mark.parametrize(
    ("values", "effort", "think", "action"),
    [
        # gpt-oss: named levels, no off switch.
        (["low", "medium", "high"], "max", "high", "approximated"),
        (["low", "medium", "high"], "xhigh", "high", "approximated"),
        (["low", "medium", "high"], "none", False, "approximated"),
        (["low", "medium", "high"], "medium", "medium", None),
        # An on/off switch only.
        ([True, False], "high", True, "approximated"),
        ([True, False], "none", False, None),
        # A model that does not think: `false` is accepted, anything else is a 400.
        ([False], "high", None, "dropped"),
        ([False], "none", False, None),
    ],
)
def test_think_values_from_the_profile_clamp_effort(
    values: list[Any], effort: Effort, think: bool | str | None, action: str | None
) -> None:
    body, report = encode(
        text_request().model_copy(update={"effort": effort}), extra={"think_values": values}
    )
    assert body.get("think") == think
    assert losses(report) == ([("effort", action)] if action else [])


def test_explicit_think_wins_and_budget_is_reported() -> None:
    request = text_request().model_copy(
        update={"effort": "high", "budget_tokens": 2048, "extensions": {"think": True}}
    )
    body, report = encode(request)
    assert body["think"] is True
    assert losses(report) == [("budget_tokens", "dropped")]


def test_images_are_inlined_and_urls_reported() -> None:
    pixel = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    request = Request(
        model="test-model",
        messages=[
            Message(
                role="user",
                content=[
                    TextPart(text="Compare these."),
                    ImagePart(data=pixel, media_type="image/png"),
                    ImagePart(url=f"data:image/png;base64,{pixel}"),
                    ImagePart(url="https://example.com/cat.png"),
                ],
            )
        ],
    )
    body, report = encode(request)
    assert body["messages"] == [
        {"role": "user", "content": "Compare these.", "images": [pixel, pixel]}
    ]
    assert losses(report) == [("messages[0].content[3]", "dropped")]
    assert "not fetched" in report.losses[0].detail
    # Strict mode rejects the same request instead of dropping the URL.
    with pytest.raises(ConversionError):
        encode(request, strict=True)
    # Decoding a native request recovers the media type from the image bytes.
    decoded = OllamaCodec().decode_request(body)
    images = [p for p in decoded.messages[0].content if isinstance(p, ImagePart)]
    assert [(i.data, i.media_type) for i in images] == [(pixel, "image/png"), (pixel, "image/png")]


def test_options_and_extensions_pass_through() -> None:
    request = text_request().model_copy(
        update={
            "sampling": Sampling(temperature=0.2, top_p=0.9, top_k=40, seed=7, stop=["\n\n"]),
            "extensions": {"num_ctx": 8192, "keep_alive": "10m", "options": {"min_p": 0.05}},
        }
    )
    body, report = encode(request)
    assert body["options"] == {
        "min_p": 0.05,
        "temperature": 0.2,
        "top_p": 0.9,
        "top_k": 40,
        "seed": 7,
        "stop": ["\n\n"],
        "num_predict": 64,
        "num_ctx": 8192,
    }
    assert body["keep_alive"] == "10m"
    assert report.lossless


def test_response_format_maps_to_format() -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    body, report = encode(
        text_request().model_copy(update={"response_format": ResponseFormat(type="json_object")})
    )
    assert body["format"] == "json" and report.lossless
    body, report = encode(
        text_request().model_copy(
            update={"response_format": ResponseFormat(type="json_schema", json_schema=schema)}
        )
    )
    assert body["format"] == schema and report.lossless


def test_tool_choice_and_parallel_calls_are_reported() -> None:
    body, report = encode(tool_request().model_copy(update={"tool_choice": "none"}))
    assert "tools" not in body
    assert losses(report) == [("tool_choice", "approximated")]
    body, report = encode(
        tool_request().model_copy(
            update={"tool_choice": NamedToolChoice(name="get_time"), "parallel_tool_calls": False}
        )
    )
    assert body["tools"] == WIRE_TOOLS
    assert losses(report) == [("tool_choice", "dropped"), ("parallel_tool_calls", "dropped")]


def test_reasoning_goes_back_only_to_the_same_model() -> None:
    def history(origin_model: str) -> Request:
        return Request(
            model="test-model",
            messages=[
                Message.text("user", "What is 1+1?"),
                Message(
                    role="assistant",
                    content=[
                        ReasoningPart(
                            text="Just answer 2.", origin=Origin(format=FORMAT, model=origin_model)
                        ),
                        TextPart(text="2"),
                    ],
                ),
                Message.text("user", "And 2+2?"),
            ],
        )

    body, report = encode(history("test-model"))
    assert body["messages"][1] == {
        "role": "assistant",
        "content": "2",
        "thinking": "Just answer 2.",
    }
    assert report.lossless
    body, report = encode(history("other-model"))
    assert body["messages"][1] == {"role": "assistant", "content": "2"}
    assert losses(report) == [("messages[1].content[0]", "dropped")]


def test_other_losses_are_reported() -> None:
    request = Request(
        model="test-model",
        messages=[
            Message(
                role="user",
                name="alice",
                content=[TextPart(text="Read this."), FilePart(url="https://example.com/a.pdf")],
            ),
            Message(
                role="assistant",
                content=[ToolCallPart(id="call_1", name="get_time", arguments="not json")],
            ),
            Message(
                role="tool",
                content=[
                    ToolResultPart(call_id="call_1", content=[TextPart(text="boom")], is_error=True)
                ],
            ),
        ],
    )
    body, report = encode(request)
    assert losses(report) == [
        ("messages[0].content[1]", "dropped"),
        ("messages[0].name", "dropped"),
        ("messages[1].content[0].arguments", "approximated"),
        ("messages[2].content[0].is_error", "dropped"),
    ]
    assert body["messages"][1]["tool_calls"] == [
        {
            "id": "call_1",
            "function": {"index": 0, "name": "get_time", "arguments": {"input": "not json"}},
        }
    ]
    # The result names its tool from the call it answers.
    assert body["messages"][2] == {
        "role": "tool",
        "content": "boom",
        "tool_name": "get_time",
        "tool_call_id": "call_1",
    }


def test_synthetic_ids_follow_the_conversation_turn() -> None:
    body = {
        "model": "test-model",
        "created_at": "2026-10-16T04:51:33.847559Z",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "get_time", "arguments": {"city": "Lima"}}}],
        },
        "done": True,
        "done_reason": "stop",
    }
    # conversation_request() holds two assistant turns, so the answer is turn 2.
    response = client_for(FORMAT, serve_json(body)).complete(conversation_request()).response
    [call] = response.message.tool_calls()
    assert (call.id, call.synthetic) == ("call_t2_0", True)


def test_unmatched_tool_result_gets_an_id_no_call_has() -> None:
    body = {
        "model": "test-model",
        "messages": [
            {"role": "user", "content": "Time in Tokyo?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "get_time", "arguments": {"city": "Tokyo"}}}],
            },
            {"role": "tool", "tool_name": "get_time", "content": "21:04"},
            {"role": "tool", "tool_name": "get_time", "content": "21:05"},
        ],
    }
    request = OllamaCodec().decode_request(body)
    [call] = request.messages[1].tool_calls()
    first, second = (m.tool_results()[0].call_id for m in request.messages[2:])
    assert first == call.id
    assert second not in (call.id, first)


def test_server_stream_shape() -> None:
    events = list(OllamaCodec().encode_stream(response_to_deltas(ir_response(), chunk=0)))
    lines = [json.loads(event.data) for event in events]
    assert lines[0]["message"] == {
        "role": "assistant",
        "content": "",
        "thinking": "Two lookups are needed.",
    }
    assert lines[1]["message"] == {"role": "assistant", "content": "Checking both cities."}
    # Each tool call goes out whole in its own line, like the server sends them.
    assert [line["message"]["tool_calls"] for line in lines[2:4]] == [
        [
            {
                "id": "call_a",
                "function": {"index": 0, "name": "get_weather", "arguments": {"city": "Paris"}},
            }
        ],
        [
            {
                "id": "call_b",
                "function": {"index": 1, "name": "get_time", "arguments": {"city": "Tokyo"}},
            }
        ],
    ]
    last = lines[-1]
    assert {
        key: last[key] for key in ("done", "done_reason", "prompt_eval_count", "eval_count")
    } == {
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 196,
        "eval_count": 47,
    }
    assert all(line["model"] == "test-model" for line in lines)


def test_stream_without_final_line_is_an_error() -> None:
    deltas = list(client_for(FORMAT, serve_ndjson([partial("Hel")])).stream(text_request()))
    assert isinstance(deltas[-1], ErrorDelta) and deltas[-1].kind == "server"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ([], "JSON object"),
        ({"messages": []}, "model is required"),
        ({"model": "m", "messages": [{"role": "robot", "content": "hi"}]}, "unknown role"),
        ({"model": "m", "messages": [], "think": 3}, "think must be"),
        ({"model": "m", "messages": [], "format": 1}, "format must be"),
        ({"model": "m", "messages": [], "tools": [{"type": "function"}]}, "function.name"),
    ],
)
def test_decode_request_rejects_bad_bodies(body: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        OllamaCodec().decode_request(body)


def test_encode_error_shape() -> None:
    codec = OllamaCodec()
    assert codec.encode_error(ModelError("not_found", "model 'x' not found", status=404)) == (
        404,
        {"error": "model 'x' not found"},
    )
    assert codec.encode_error(ModelError("rate_limit", "slow down")) == (
        429,
        {"error": "slow down"},
    )


# --------------------------------------------------------------------------------------------
# Live check against a local server (skipped unless one with a small model is running)
# --------------------------------------------------------------------------------------------

LIVE_URL = os.environ.get("STEPSWITCH_LIVE_OLLAMA_URL", "http://127.0.0.1:11434")
SMALL_MODEL_BYTES = 6 * 1024**3


def live_model() -> str | None:
    """A local model for the live test: the one named in STEPSWITCH_LIVE_OLLAMA_MODEL, else the
    smallest installed local model up to 6 GiB."""
    try:
        with net.default().client("test", timeout=3.0) as http:
            tags = http.get(f"{LIVE_URL.rstrip('/')}/api/tags").json()
    except (httpx.HTTPError, ValueError):
        return None
    models = [
        m
        for m in (tags.get("models") or [] if isinstance(tags, dict) else [])
        if isinstance(m, dict) and isinstance(m.get("name"), str) and not m.get("remote_host")
    ]
    wanted = os.environ.get("STEPSWITCH_LIVE_OLLAMA_MODEL")
    if wanted:
        return wanted if any(m["name"] == wanted for m in models) else None
    small = [m for m in models if isinstance(m.get("size"), int) and m["size"] <= SMALL_MODEL_BYTES]
    return min(small, key=lambda m: m["size"])["name"] if small else None


@pytest.mark.live
def test_live_local_ollama() -> None:
    model = live_model()
    if model is None:
        pytest.skip("no local Ollama server with a small model")
    endpoint = Endpoint(name="local-ollama", base_url=LIVE_URL, format=FORMAT, model=model)
    client = ModelClient(endpoint, max_retries=0, timeout=300.0)
    request = Request(
        model=model,
        messages=[Message.text("user", "Reply with one word: ready")],
        max_output_tokens=16,
        sampling=Sampling(temperature=0.0),
        extensions={"num_ctx": 2048},
    )
    response = client.complete(request).response
    assert response.finish_reason in ("stop", "length")
    assert response.usage is not None and (response.usage.input_tokens or 0) > 0
    cached = response.usage.cached_input_tokens
    assert cached is None or cached <= (response.usage.input_tokens or 0)
    streamed = assemble(client.stream(request))
    assert streamed.finish_reason in ("stop", "length")
    assert streamed.message.text_content() or streamed.message.reasoning()
    try:
        tooled = client.complete(tool_request(model)).response
    except ModelError as exc:
        if exc.kind != "unsupported":  # models without tools answer HTTP 400 "does not support"
            raise
        return
    for call in tooled.message.tool_calls():
        assert call.id and call.name in ("get_weather", "get_time")
        assert isinstance(call.parsed_arguments(), dict)
