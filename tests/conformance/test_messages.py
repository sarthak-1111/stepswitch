"""Conformance suite for the messages format (claim C-004).

Every mock reply below is written by hand in the wire shapes of the format's references (the
message object, named SSE events, the error body; checked 2026-10-16). None is produced by the
codec under test.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from stepswitch.compat.codecs.base import CodecOptions
from stepswitch.compat.codecs.messages import MessagesCodec
from stepswitch.compat.errors import ModelError
from stepswitch.compat.framing import StreamEvent, format_sse, iter_lines, parse_sse
from stepswitch.compat.ir import (
    ConversionError,
    ConversionReport,
    Delta,
    ErrorDelta,
    FinishDelta,
    ImagePart,
    Message,
    NamedToolChoice,
    Origin,
    ReasoningDelta,
    ReasoningPart,
    RefusalPart,
    Request,
    Response,
    TextDelta,
    TextPart,
    ToolCallArgsDelta,
    ToolCallEndDelta,
    ToolCallPart,
    ToolCallStartDelta,
    ToolResultPart,
    Usage,
)
from stepswitch.compat.stream import assemble, response_to_deltas
from tests.conformance.harness import (
    ANSWER,
    CLOCK,
    WEATHER,
    FormatSuite,
    MockServer,
    Recorded,
    client_for,
    conversation_request,
    json_response,
    normalize,
    sse_response,
    text_request,
    tool_request,
)

FORMAT = "messages"
ROUTE = r"/v1/messages"
MODEL = "test-model"
VERSION = "2023-06-01"
OWN = Origin(format="messages", model=MODEL)

# --------------------------------------------------------------------------------------------
# Hand-written wire replies
# --------------------------------------------------------------------------------------------


def _message(
    content: list[dict[str, Any]],
    *,
    stop_reason: str = "end_turn",
    usage: dict[str, Any] | None = None,
    message_id: str = "msg_01XFDUDYJgAACzvnptvVoYEL",
) -> dict[str, Any]:
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 12, "output_tokens": 6},
    }


def _start(message_id: str, usage: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    message = {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "content": [],
        "model": MODEL,
        "stop_reason": None,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 12, "output_tokens": 1},
    }
    return "message_start", {"type": "message_start", "message": message}


def _block_start(index: int, block: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return "content_block_start", {
        "type": "content_block_start",
        "index": index,
        "content_block": block,
    }


def _delta(index: int, delta: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return "content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta}


def _block_stop(index: int) -> tuple[str, dict[str, Any]]:
    return "content_block_stop", {"type": "content_block_stop", "index": index}


def _end(stop_reason: str, usage: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": usage,
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]


def _events(events: list[tuple[str, dict[str, Any]]]) -> httpx.Response:
    """Named SSE events, as the format sends them (the event name equals the JSON type)."""
    return sse_response([StreamEvent(data=json.dumps(data), event=name) for name, data in events])


def _error(
    status: int, error_type: str, message: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    body = {
        "type": "error",
        "error": {"type": error_type, "message": message},
        "request_id": "req_011CSHoEeqs5C35K2UUqR7Fy",
    }
    return json_response(body, status, headers)


def _server(responder: Callable[[Recorded], httpx.Response]) -> MockServer:
    return MockServer().route("POST", ROUTE, responder)


def _expect_error(call: Callable[[], object]) -> ModelError:
    try:
        call()
    except ModelError as exc:
        return exc
    raise AssertionError("expected a ModelError")


def _assemble(deltas: list[Delta]) -> Response:
    return assemble(deltas)


def _without_raw(response: Response) -> dict[str, Any]:
    return response.model_dump(exclude={"usage": {"raw"}})


def _stamp(response: Response, origin: Origin) -> Response:
    """Stream deltas carry no origin, so assembled reasoning parts get it from the caller."""
    for part in response.message.content:
        if isinstance(part, ReasoningPart) and part.origin is None:
            part.origin = origin
    return response


# --------------------------------------------------------------------------------------------
# Required cases
# --------------------------------------------------------------------------------------------


def case_non_streaming() -> None:
    server = _server(lambda _r: json_response(_message([{"type": "text", "text": ANSWER}])))
    completion = client_for(FORMAT, server).complete(text_request())
    sent = server.last
    assert sent.method == "POST" and sent.path == "/v1/messages"
    assert sent.headers["anthropic-version"] == VERSION
    assert sent.body["model"] == MODEL
    assert sent.body["system"] == "You are terse."
    assert sent.body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Say hello."}]}
    ]
    assert sent.body["max_tokens"] == 64
    assert "stream" not in sent.body
    response = completion.response
    assert response.message.text_content() == ANSWER
    assert response.finish_reason == "stop" and response.raw_finish_reason == "end_turn"
    assert response.id == "msg_01XFDUDYJgAACzvnptvVoYEL" and response.model == MODEL
    assert completion.report.lossless


def case_streaming() -> None:
    events = [
        _start("msg_stream_1"),
        _block_start(0, {"type": "text", "text": ""}),
        ("ping", {"type": "ping"}),
        _delta(0, {"type": "text_delta", "text": "Hel"}),
        _delta(0, {"type": "text_delta", "text": "lo the"}),
        _delta(0, {"type": "text_delta", "text": "re."}),
        _block_stop(0),
        *_end("end_turn", {"output_tokens": 6}),
    ]
    server = _server(lambda _r: _events(events))
    deltas = list(client_for(FORMAT, server).stream(text_request()))
    assert server.last.body["stream"] is True
    assert server.last.headers["anthropic-version"] == VERSION
    assert [d.text for d in deltas if isinstance(d, TextDelta)] == ["Hel", "lo the", "re."]
    response = _assemble(deltas)
    assert response.message.text_content() == ANSWER
    assert response.finish_reason == "stop" and response.raw_finish_reason == "end_turn"
    assert response.id == "msg_stream_1" and response.model == MODEL
    assert response.usage is not None
    assert (response.usage.input_tokens, response.usage.output_tokens) == (12, 6)


def case_tool_call() -> None:
    reply = _message(
        [
            {"type": "text", "text": "Checking the weather."},
            {
                "type": "tool_use",
                "id": "toolu_01A09q90qw90lq917835lq9",
                "name": "get_weather",
                "input": {"city": "Paris", "unit": "c"},
            },
        ],
        stop_reason="tool_use",
    )
    server = _server(lambda _r: json_response(reply))
    response = client_for(FORMAT, server).complete(tool_request()).response
    sent = server.last.body
    assert sent["tools"] == [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in (WEATHER, CLOCK)
    ]
    assert sent["tool_choice"] == {"type": "auto"}
    assert sent["system"] == "Use tools when needed."
    (call,) = response.message.tool_calls()
    assert call.id == "toolu_01A09q90qw90lq917835lq9" and call.name == "get_weather"
    assert json.loads(call.arguments) == {"city": "Paris", "unit": "c"}
    assert response.finish_reason == "tool_calls" and response.raw_finish_reason == "tool_use"
    assert response.message.text_content() == "Checking the weather."

    # Streamed: the arguments arrive as partial JSON across several events.
    tool_block = {"type": "tool_use", "id": "toolu_stream_1", "name": "get_weather", "input": {}}
    events = [
        _start("msg_stream_2"),
        _block_start(0, tool_block),
        _delta(0, {"type": "input_json_delta", "partial_json": ""}),
        _delta(0, {"type": "input_json_delta", "partial_json": '{"city": "Pa'}),
        _delta(0, {"type": "input_json_delta", "partial_json": 'ris", "unit"'}),
        _delta(0, {"type": "input_json_delta", "partial_json": ': "c"}'}),
        _block_stop(0),
        *_end("tool_use", {"output_tokens": 40}),
    ]
    server = _server(lambda _r: _events(events))
    deltas = list(client_for(FORMAT, server).stream(tool_request()))
    assert sum(isinstance(d, ToolCallArgsDelta) for d in deltas) == 3
    streamed = _assemble(deltas)
    (call,) = streamed.message.tool_calls()
    assert call.id == "toolu_stream_1" and call.name == "get_weather"
    assert json.loads(call.arguments) == {"city": "Paris", "unit": "c"}
    assert streamed.finish_reason == "tool_calls"


def case_parallel_tool_calls() -> None:
    reply = _message(
        [
            {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "get_weather",
                "input": {"city": "Paris"},
            },
            {"type": "tool_use", "id": "toolu_02", "name": "get_time", "input": {"city": "Tokyo"}},
        ],
        stop_reason="tool_use",
    )
    server = _server(lambda _r: json_response(reply))
    response = client_for(FORMAT, server).complete(tool_request()).response
    calls = response.message.tool_calls()
    assert [c.name for c in calls] == ["get_weather", "get_time"]
    assert [c.id for c in calls] == ["toolu_01", "toolu_02"]
    assert [json.loads(c.arguments) for c in calls] == [{"city": "Paris"}, {"city": "Tokyo"}]
    assert response.finish_reason == "tool_calls"

    events = [
        _start("msg_stream_3"),
        _block_start(0, {"type": "tool_use", "id": "toolu_11", "name": "get_weather", "input": {}}),
        _delta(0, {"type": "input_json_delta", "partial_json": '{"city":'}),
        _delta(0, {"type": "input_json_delta", "partial_json": ' "Paris"}'}),
        _block_stop(0),
        _block_start(1, {"type": "tool_use", "id": "toolu_12", "name": "get_time", "input": {}}),
        _delta(1, {"type": "input_json_delta", "partial_json": '{"city": "Tokyo"}'}),
        _block_stop(1),
        *_end("tool_use", {"output_tokens": 61}),
    ]
    server = _server(lambda _r: _events(events))
    streamed = _assemble(list(client_for(FORMAT, server).stream(tool_request())))
    calls = streamed.message.tool_calls()
    assert [(c.id, c.name) for c in calls] == [
        ("toolu_11", "get_weather"),
        ("toolu_12", "get_time"),
    ]
    assert [json.loads(c.arguments) for c in calls] == [{"city": "Paris"}, {"city": "Tokyo"}]

    # Outbound history: both calls in one assistant turn, both results in one user turn.
    server = _server(lambda _r: json_response(_message([{"type": "text", "text": ANSWER}])))
    client_for(FORMAT, server).complete(conversation_request())
    turns = server.last.body["messages"]
    assert [t["role"] for t in turns] == ["user", "assistant", "user", "assistant", "user"]
    assert [b["id"] for b in turns[1]["content"]] == ["call_a", "call_b"]
    assert turns[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_a", "content": "18 C, sunny"},
        {"type": "tool_result", "tool_use_id": "call_b", "content": "21:04"},
    ]


def case_error() -> None:
    cases = [
        (400, "invalid_request_error", "bad_request", None),
        (401, "authentication_error", "authentication", None),
        (429, "rate_limit_error", "rate_limit", "7"),
        (500, "api_error", "server", None),
        (529, "overloaded_error", "overloaded", None),
    ]
    for status, error_type, kind, retry_after in cases:
        headers = {"retry-after": retry_after} if retry_after else None
        server = _server(lambda _r, s=status, t=error_type, h=headers: _error(s, t, "boom", h))
        error = _expect_error(lambda srv=server: client_for(FORMAT, srv).complete(text_request()))
        assert (error.kind, error.status, error.code) == (kind, status, error_type), error
        assert error.message == "boom"
        assert error.retry_after == (7.0 if retry_after else None)
        assert error.retryable == (kind in {"rate_limit", "server", "overloaded"})

    # An error event in the middle of a stream.
    events = [
        _start("msg_stream_4"),
        (
            "error",
            {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
        ),
    ]
    server = _server(lambda _r: _events(events))
    deltas = list(client_for(FORMAT, server).stream(text_request()))
    last = deltas[-1]
    assert isinstance(last, ErrorDelta) and last.kind == "overloaded" and last.status == 529
    assert _expect_error(lambda: _assemble(deltas)).kind == "overloaded"


def case_usage() -> None:
    usage = {
        "input_tokens": 20,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 100,
        "output_tokens": 50,
        "output_tokens_details": {"thinking_tokens": 12},
    }
    server = _server(
        lambda _r: json_response(_message([{"type": "text", "text": ANSWER}], usage=usage))
    )
    result = client_for(FORMAT, server).complete(text_request()).response.usage
    assert result is not None
    assert result.input_tokens == 150  # input + cache reads + cache writes
    assert (result.cached_input_tokens, result.cache_write_tokens) == (100, 30)
    assert (result.output_tokens, result.reasoning_tokens) == (50, 12)
    assert result.raw == usage

    start_usage = {
        "input_tokens": 20,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 100,
        "output_tokens": 1,
    }
    events = [
        _start("msg_stream_5", start_usage),
        _block_start(0, {"type": "text", "text": ""}),
        _delta(0, {"type": "text_delta", "text": ANSWER}),
        _block_stop(0),
        *_end("end_turn", {"output_tokens": 50, "output_tokens_details": {"thinking_tokens": 12}}),
    ]
    server = _server(lambda _r: _events(events))
    streamed = _assemble(list(client_for(FORMAT, server).stream(text_request()))).usage
    assert streamed is not None
    assert (streamed.input_tokens, streamed.cached_input_tokens) == (150, 100)
    assert (streamed.cache_write_tokens, streamed.output_tokens) == (30, 50)
    assert streamed.reasoning_tokens == 12


def case_server_round_trip() -> None:
    codec = MessagesCodec()
    for request in (text_request(), tool_request(), conversation_request()):
        report = ConversionReport()
        call = codec.encode_request(request, report)
        assert report.lossless, report.losses
        decoded = codec.decode_request(call.json, path=call.path, headers=call.headers)
        assert normalize(decoded) == normalize(request)
        assert decoded.model == request.model
        assert decoded.max_output_tokens == request.max_output_tokens
        assert decoded.tool_choice == request.tool_choice
        if request.response_format is not None:
            assert decoded.response_format is not None
            assert decoded.response_format.json_schema == request.response_format.json_schema


def _rich_response() -> Response:
    return Response(
        id="msg_rt_1",
        model=MODEL,
        message=Message(
            role="assistant",
            content=[
                ReasoningPart(
                    text="Two lookups are needed.",
                    opaque="EqQBCgIYAhIM1gbcDa9GJwZA2b3h",
                    opaque_kind="signature",
                    origin=OWN,
                ),
                TextPart(text="Checking both."),
                ToolCallPart(id="toolu_rt_1", name="get_weather", arguments='{"city":"Paris"}'),
                ToolCallPart(id="toolu_rt_2", name="get_time", arguments='{"city":"Tokyo"}'),
            ],
        ),
        finish_reason="tool_calls",
        raw_finish_reason="tool_use",
        usage=Usage(
            input_tokens=150,
            output_tokens=50,
            cached_input_tokens=100,
            cache_write_tokens=30,
            reasoning_tokens=12,
        ),
    )


def case_response_round_trip() -> None:
    codec = MessagesCodec()
    original = _rich_response()
    body = codec.encode_response(original)
    assert (body["type"], body["role"], body["stop_reason"]) == ("message", "assistant", "tool_use")
    assert [b["type"] for b in body["content"]] == ["thinking", "text", "tool_use", "tool_use"]
    assert body["content"][0]["signature"] == "EqQBCgIYAhIM1gbcDa9GJwZA2b3h"
    assert body["content"][2]["input"] == {"city": "Paris"}
    assert body["usage"] == {
        "input_tokens": 20,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 100,
        "output_tokens": 50,
        "output_tokens_details": {"thinking_tokens": 12},
    }
    decoded = codec.decode_response(json.loads(json.dumps(body)))
    assert _without_raw(decoded) == _without_raw(original)


def case_stream_round_trip() -> None:
    codec = MessagesCodec()
    original = _rich_response()
    events = list(codec.encode_stream(response_to_deltas(original, chunk=3)))
    wire = "".join(format_sse(event) for event in events)
    parsed = list(parse_sse(iter_lines([wire])))
    names = [event.event for event in parsed]
    assert names[0] == "message_start" and names[-2:] == ["message_delta", "message_stop"]
    assert names.count("content_block_start") == names.count("content_block_stop") == 4
    for event in parsed:
        assert event.event == json.loads(event.data)["type"]
    assembled = _stamp(_assemble(list(codec.decode_stream(parsed))), OWN)
    assert _without_raw(assembled) == _without_raw(original)


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


def test_suite_has_required_cases() -> None:
    assert SUITE.missing_cases() == []


# --------------------------------------------------------------------------------------------
# Format quirks
# --------------------------------------------------------------------------------------------


def _encode(request: Request, options: CodecOptions | None = None) -> tuple[Any, ConversionReport]:
    report = ConversionReport()
    call = MessagesCodec(options).encode_request(request, report)
    return call.json, report


def test_roles_merge_and_tool_results_lead_the_user_turn() -> None:
    request = Request(
        model=MODEL,
        max_output_tokens=32,
        messages=[
            Message.text("user", "First."),
            Message.text("user", "Second."),
            Message(role="assistant", content=[ToolCallPart(id="t1", name="get_time")]),
            Message.text("user", "Also this."),
            Message(
                role="tool", content=[ToolResultPart(call_id="t1", content=[TextPart(text="9")])]
            ),
        ],
    )
    body, report = _encode(request)
    assert report.lossless
    assert [t["role"] for t in body["messages"]] == ["user", "assistant", "user"]
    assert [b["text"] for b in body["messages"][0]["content"]] == ["First.", "Second."]
    assert [b["type"] for b in body["messages"][2]["content"]] == ["tool_result", "text"]


def test_system_messages_are_hoisted() -> None:
    request = Request(
        model=MODEL,
        max_output_tokens=32,
        system=[TextPart(text="Rule one.")],
        messages=[
            Message.text("system", "Rule two."),
            Message.text("user", "Hi."),
            Message.text("system", "Rule three."),
        ],
    )
    body, report = _encode(request)
    assert [b["text"] for b in body["system"]] == ["Rule one.", "Rule two.", "Rule three."]
    assert [(loss.field, loss.action) for loss in report.losses] == [
        ("messages[2]", "approximated")
    ]
    # Inbound, a mid-conversation system message stays an IR system message; its per-message
    # settings are reported.
    inbound = {
        "model": MODEL,
        "max_tokens": 32,
        "messages": [
            {"role": "user", "content": "Hi."},
            {"role": "system", "content": "Short answers.", "clear_at": "next_user_message"},
        ],
    }
    report = ConversionReport()
    decoded = MessagesCodec().decode_request(inbound, report=report)
    assert [(m.role, m.text_content()) for m in decoded.messages] == [
        ("user", "Hi."),
        ("system", "Short answers."),
    ]
    assert [(loss.field, loss.detail) for loss in report.losses] == [
        ("messages[1]", "fields ['clear_at'] are not carried")
    ]


def test_reasoning_is_replayed_only_to_its_own_origin() -> None:
    foreign = Origin(format="generate-content", model="other-model")
    request = Request(
        model=MODEL,
        max_output_tokens=32,
        messages=[
            Message.text("user", "Hi."),
            Message(
                role="assistant",
                content=[
                    ReasoningPart(
                        text="Mine.", opaque="sig-1", opaque_kind="signature", origin=OWN
                    ),
                    ReasoningPart(opaque="redacted-1", opaque_kind="redacted", origin=OWN),
                    ReasoningPart(
                        text="Theirs.", opaque="sig-2", opaque_kind="signature", origin=foreign
                    ),
                    ReasoningPart(text="Unsigned.", origin=OWN),
                    ToolCallPart(id="t1", name="get_time", arguments='{"city":"Oslo"}'),
                ],
            ),
        ],
    )
    body, report = _encode(request)
    blocks = body["messages"][1]["content"]
    assert blocks[:2] == [
        {"type": "thinking", "thinking": "Mine.", "signature": "sig-1"},
        {"type": "redacted_thinking", "data": "redacted-1"},
    ]
    assert [b["type"] for b in blocks] == ["thinking", "redacted_thinking", "tool_use"]
    assert [loss.field for loss in report.losses] == [
        "messages[1].content[2]",
        "messages[1].content[3]",
    ]
    with pytest.raises(ConversionError):
        _encode(request, CodecOptions(strict=True))


def test_effort_levels_and_thinking_budget() -> None:
    for level in ("low", "medium", "high", "xhigh", "max"):
        body, report = _encode(text_request().model_copy(update={"effort": level}))
        assert body["output_config"] == {"effort": level} and report.lossless
        assert "thinking" not in body
    body, report = _encode(text_request().model_copy(update={"effort": "minimal"}))
    assert body["output_config"] == {"effort": "low"}
    assert [(loss.field, loss.action) for loss in report.losses] == [("effort", "approximated")]
    body, report = _encode(text_request().model_copy(update={"effort": "none"}))
    assert body["thinking"] == {"type": "disabled"} and "output_config" not in body
    request = text_request().model_copy(update={"budget_tokens": 2048, "max_output_tokens": 8192})
    body, report = _encode(request)
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2048} and report.lossless
    body, report = _encode(text_request().model_copy(update={"budget_tokens": 100}))
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert [loss.action for loss in report.losses] == ["approximated"]
    decoded = MessagesCodec().decode_request(
        {
            "model": MODEL,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Hi"}],
            "thinking": {"type": "disabled"},
        }
    )
    assert decoded.effort == "none"


def test_refusal_maps_to_content_filter() -> None:
    reply = _message(
        [{"type": "text", "text": "I can"}],
        stop_reason="refusal",
    )
    reply["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": "Declined."}
    reply["container"] = {"id": "container_011CPR5CNjB747bTd36fQLFk", "expires_at": "soon"}
    reply["diagnostics"] = None
    codec = MessagesCodec()
    report = ConversionReport()
    response = codec.decode_response(reply, report=report)
    assert response.finish_reason == "content_filter" and response.raw_finish_reason == "refusal"
    assert isinstance(response.message.content[-1], RefusalPart)
    assert [(loss.field, loss.detail) for loss in report.losses] == [
        ("response", "fields ['container'] are not carried"),
        ("stop_details", "fields ['category'] are not carried"),
    ]
    body = codec.encode_response(response)
    assert body["stop_reason"] == "refusal"
    assert body["stop_details"]["explanation"] == "Declined."
    assert [b["type"] for b in body["content"]] == ["text"]


def test_missing_max_tokens_gets_a_reported_default() -> None:
    request = text_request().model_copy(update={"max_output_tokens": None})
    body, report = _encode(request)
    assert body["max_tokens"] == 4096
    assert [(loss.field, loss.action) for loss in report.losses] == [
        ("max_output_tokens", "approximated")
    ]
    with pytest.raises(ConversionError):
        _encode(request, CodecOptions(strict=True))


def test_version_header_override_and_stop_reasons() -> None:
    codec = MessagesCodec(CodecOptions(api_version="2099-01-01"))
    call = codec.encode_request(text_request(), ConversionReport())
    assert call.headers == {"anthropic-version": "2099-01-01"}
    finishes = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "model_context_window_exceeded": "length",
        "tool_use": "tool_calls",
        "refusal": "content_filter",
        "pause_turn": "other",
    }
    for stop, finish in finishes.items():
        assert codec.decode_response(_message([], stop_reason=stop)).finish_reason == finish


def test_tool_choice_parallel_flag_and_sampling() -> None:
    request = tool_request().model_copy(
        update={
            "tool_choice": NamedToolChoice(name="get_time"),
            "parallel_tool_calls": False,
        }
    )
    body, report = _encode(request)
    assert body["tool_choice"] == {
        "type": "tool",
        "name": "get_time",
        "disable_parallel_tool_use": True,
    }
    decoded = MessagesCodec().decode_request(body)
    assert decoded.tool_choice == NamedToolChoice(name="get_time")
    assert decoded.parallel_tool_calls is False
    required, _ = _encode(tool_request().model_copy(update={"tool_choice": "required"}))
    assert required["tool_choice"] == {"type": "any"}
    sampling = text_request().sampling.model_copy(
        update={"temperature": 1.5, "top_k": 5, "seed": 7, "stop": ["END"]}
    )
    body, report = _encode(text_request().model_copy(update={"sampling": sampling}))
    assert (body["temperature"], body["top_k"], body["stop_sequences"]) == (1.0, 5, ["END"])
    assert [(loss.field, loss.action) for loss in report.losses] == [
        ("sampling.temperature", "approximated"),
        ("sampling.seed", "dropped"),
    ]


def test_tool_results_with_errors_images_and_odd_ids() -> None:
    image = ImagePart(data="iVBORw0KGgo=", media_type="image/png")
    request = Request(
        model=MODEL,
        max_output_tokens=32,
        messages=[
            Message.text("user", "Run it."),
            Message(role="assistant", content=[ToolCallPart(id="call:1", name="run")]),
            Message(
                role="tool",
                content=[
                    ToolResultPart(
                        call_id="call:1",
                        content=[TextPart(text="failed"), image],
                        is_error=True,
                    )
                ],
            ),
        ],
    )
    body, report = _encode(request)
    result = body["messages"][2]["content"][0]
    assert body["messages"][1]["content"][0]["id"] == result["tool_use_id"] == "call_1"
    assert result["is_error"] is True
    assert result["content"] == [
        {"type": "text", "text": "failed"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="},
        },
    ]
    assert {loss.action for loss in report.losses} == {"approximated"}
    decoded = MessagesCodec().decode_request(body)
    (part,) = decoded.messages[2].tool_results()
    assert part.is_error and part.name == "run" and part.text() == "failed"
    assert isinstance(part.content[1], ImagePart)


def test_decode_request_keeps_unmodeled_fields_for_the_same_format() -> None:
    inbound = {
        "model": MODEL,
        "max_tokens": 256,
        "system": [{"type": "text", "text": "Be brief.", "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "Hi"}],
        "metadata": {"user_id": "u-1"},
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "high"},
        "tools": [
            {"name": "get_time", "input_schema": {"type": "object", "properties": {}}},
            {"type": "web_search_20250305", "name": "web_search"},
        ],
        "stream": True,
    }
    report = ConversionReport()
    decoded = MessagesCodec().decode_request(
        inbound, headers={"anthropic-beta": "beta-1"}, report=report
    )
    assert decoded.stream and decoded.effort == "high" and decoded.system_text() == "Be brief."
    assert [t.name for t in decoded.tools] == ["get_time"]
    assert [loss.field for loss in report.losses] == ["system[0].cache_control"]
    call = MessagesCodec().encode_request(decoded, ConversionReport())
    assert call.headers["anthropic-beta"] == "beta-1"
    assert call.json is not None
    assert call.json["metadata"] == {"user_id": "u-1"}
    assert call.json["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert call.json["output_config"] == {"effort": "high"}
    assert [t["name"] for t in call.json["tools"]] == ["get_time", "web_search"]
    report = ConversionReport()
    other = MessagesCodec().encode_request(
        decoded.model_copy(update={"extensions": {"responses": {"store": True}}}), report
    )
    assert "store" not in (other.json or {})
    assert [loss.field for loss in report.losses] == ["extensions.responses"]


def test_stream_encoder_keeps_blocks_valid_when_deltas_interleave() -> None:
    deltas: list[Delta] = [
        # An encrypted payload of another format opens no (empty) thinking block.
        ReasoningDelta(opaque="opaque-from-elsewhere", opaque_kind="encrypted"),
        ToolCallStartDelta(index=0, id="t0", name="get_weather"),
        ToolCallArgsDelta(index=0, delta='{"city":'),
        TextDelta(text="Meanwhile."),
        ToolCallStartDelta(index=1, id="t1", name="get_time"),
        ToolCallArgsDelta(index=1, delta='{"city":"Oslo"}'),
        ToolCallArgsDelta(index=0, delta='"Rome"}'),
        ToolCallEndDelta(index=0),
        ToolCallEndDelta(index=1),
        FinishDelta(reason="tool_calls"),
    ]
    codec = MessagesCodec()
    events = list(codec.encode_stream(deltas))
    starts = [json.loads(e.data) for e in events if e.event == "content_block_start"]
    assert [s["content_block"]["type"] for s in starts] == ["tool_use", "text", "tool_use"]
    assert "opaque-from-elsewhere" not in "".join(e.data for e in events)
    open_index = None
    for event in events:
        data = json.loads(event.data)
        if data["type"] == "content_block_start":
            assert open_index is None
            open_index = data["index"]
        elif data["type"] in ("content_block_delta", "content_block_stop"):
            assert data["index"] == open_index
            if data["type"] == "content_block_stop":
                open_index = None
    response = _assemble(list(codec.decode_stream(events)))
    calls = response.message.tool_calls()
    assert [json.loads(c.arguments) for c in calls] == [{"city": "Rome"}, {"city": "Oslo"}]
    assert response.message.text_content() == "Meanwhile."


def test_truncated_streams_and_server_errors() -> None:
    codec = MessagesCodec()
    start_name, start_data = _start("msg_cut")
    events = [StreamEvent(data=json.dumps(start_data), event=start_name)]
    deltas = list(codec.decode_stream(events))
    assert isinstance(deltas[-1], ErrorDelta) and deltas[-1].kind == "invalid_response"
    status, body = codec.encode_error(ModelError("overloaded", "busy"))
    assert status == 529
    assert body == {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
    assert codec.encode_error(ModelError("rate_limit", "slow down"))[0] == 429
    events = list(codec.encode_stream([TextDelta(text="partial")]))
    assert json.loads(events[-1].data)["error"]["type"] == "api_error"
