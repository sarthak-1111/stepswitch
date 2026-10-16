"""Conformance suite for the responses format (claim C-004).

Mock server payloads are written by hand in the wire shape of the open responses
specification, version 2026-04-24 (https://www.openresponses.org/specification and
https://www.openresponses.org/reference), plus the hosted reference's variants listed in
docs/RESEARCH.md ("API formats"): the `response.reasoning_text.*` event names, top-level
fields in stream `error` events, and `minimal`/`max` effort. Nothing here is produced by the
codec under test, except in the round-trip cases, which exist to feed the codec its own output.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from functools import partial
from typing import Any

import httpx
import pytest

from stepswitch.compat.codecs import get_codec
from stepswitch.compat.codecs.base import CodecOptions, HttpCall
from stepswitch.compat.errors import ModelError
from stepswitch.compat.framing import StreamEvent, format_sse, iter_lines, parse_sse
from stepswitch.compat.ir import (
    ConversionError,
    ConversionReport,
    Delta,
    ErrorDelta,
    FilePart,
    FinishDelta,
    ImagePart,
    Message,
    Origin,
    ReasoningDelta,
    ReasoningPart,
    RefusalPart,
    Request,
    Response,
    StartDelta,
    TextDelta,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from stepswitch.compat.stream import assemble, response_to_deltas
from tests.conformance.harness import (
    ANSWER,
    CLOCK,
    REQUIRED_CASES,
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

PATH = "/v1/responses"
CREATED = 1760000000

# --------------------------------------------------------------------------------------------
# Hand-written wire payloads
# --------------------------------------------------------------------------------------------


def resource(
    output: list[dict[str, Any]],
    *,
    status: str = "completed",
    usage: dict[str, Any] | None = None,
    response_id: str = "resp_test1",
    **overrides: Any,
) -> dict[str, Any]:
    """A response object with every field the open specification marks as required."""
    body: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": CREATED,
        "completed_at": CREATED + 2 if status == "completed" else None,
        "status": status,
        "incomplete_details": None,
        "model": "test-model",
        "previous_response_id": None,
        "instructions": None,
        "output": output,
        "error": None,
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
    body.update(overrides)
    return body


def usage_object(
    input_tokens: int, output_tokens: int, *, cached: int = 0, reasoning: int = 0
) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": cached},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning},
        "total_tokens": input_tokens + output_tokens,
    }


def message_item(text: str, item_id: str = "msg_test1") -> dict[str, Any]:
    return {
        "type": "message",
        "id": item_id,
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def call_item(
    item_id: str, call_id: str, name: str, arguments: str, status: str = "completed"
) -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": item_id,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": status,
    }


def sse(*events: dict[str, Any], done: bool = False) -> httpx.Response:
    """SSE body: the `event:` line equals the payload type; sequence numbers count up from 0."""
    framed = []
    for number, event in enumerate(events):
        payload = {"type": event["type"], "sequence_number": number}
        payload.update((key, value) for key, value in event.items() if key != "type")
        framed.append(StreamEvent(data=json.dumps(payload), event=payload["type"]))
    if done:
        framed.append(StreamEvent(data="[DONE]"))
    return sse_response(framed)


def serve(make: Callable[[], httpx.Response]) -> MockServer:
    """A mock server that answers every request on the responses path with a fresh response."""

    def responder(_request: Recorded) -> httpx.Response:
        return make()

    return MockServer().route("POST", PATH, responder)


def body_of(call: HttpCall) -> dict[str, Any]:
    assert call.json is not None
    return call.json


def started(status: str = "in_progress") -> list[dict[str, Any]]:
    snapshot = resource([], status=status)
    return [
        {"type": "response.created", "response": snapshot},
        {"type": "response.in_progress", "response": snapshot},
    ]


def text_stream_events(usage: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """`Hello there.` streamed in three deltas, as in the specification's item example."""
    final = message_item(ANSWER)
    where = {"item_id": "msg_test1", "output_index": 0, "content_index": 0}
    return [
        *started(),
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "msg_test1",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        },
        {
            "type": "response.content_part.added",
            **where,
            "part": {"type": "output_text", "text": "", "annotations": []},
        },
        {"type": "response.output_text.delta", **where, "delta": "Hel", "logprobs": []},
        {"type": "response.output_text.delta", **where, "delta": "lo th", "logprobs": []},
        {"type": "response.output_text.delta", **where, "delta": "ere.", "logprobs": []},
        {"type": "response.output_text.done", **where, "text": ANSWER, "logprobs": []},
        {
            "type": "response.content_part.done",
            **where,
            "part": {"type": "output_text", "text": ANSWER, "annotations": []},
        },
        {"type": "response.output_item.done", "output_index": 0, "item": final},
        {
            "type": "response.completed",
            "response": resource([final], usage=usage or usage_object(21, 4)),
        },
    ]


def args_delta(item_id: str, index: int, delta: str) -> dict[str, Any]:
    return {
        "type": "response.function_call_arguments.delta",
        "item_id": item_id,
        "output_index": index,
        "delta": delta,
    }


def args_done(item_id: str, index: int, arguments: str) -> dict[str, Any]:
    return {
        "type": "response.function_call_arguments.done",
        "item_id": item_id,
        "output_index": index,
        "arguments": arguments,
    }


def item_event(kind: str, index: int, item: dict[str, Any]) -> dict[str, Any]:
    return {"type": f"response.output_item.{kind}", "output_index": index, "item": item}


PARIS = '{"city":"Paris","unit":"c"}'
TOKYO = '{"city":"Tokyo"}'
ENCRYPTED = "gAAAAABpZW5jcnlwdGVkLXJlYXNvbmluZy1wYXlsb2Fk"


def reasoning_item(
    item_id: str = "rs_test1", summary: str = "Two lookups are needed."
) -> dict[str, Any]:
    return {
        "type": "reasoning",
        "id": item_id,
        "status": "completed",
        "summary": [{"type": "summary_text", "text": summary}],
        "encrypted_content": ENCRYPTED,
    }


def stream_deltas(events: Iterable[dict[str, Any]]) -> list[Delta]:
    """Decode hand-written events with the codec directly (no client), through SSE framing."""
    response = sse(*events)
    lines = iter_lines([response.content.decode()])
    return list(get_codec("responses").decode_stream(parse_sse(lines)))


# --------------------------------------------------------------------------------------------
# Required cases
# --------------------------------------------------------------------------------------------


def case_non_streaming() -> None:
    body = resource([message_item(ANSWER)], usage=usage_object(21, 4))
    server = serve(lambda: json_response(body))
    completion = client_for("responses", server).complete(text_request())

    sent = server.last
    assert sent.method == "POST" and sent.path == PATH
    assert sent.body["model"] == "test-model"
    assert sent.body["instructions"] == "You are terse."
    assert sent.body["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Say hello."}],
        }
    ]
    assert sent.body["max_output_tokens"] == 64
    assert sent.body["stream"] is False and sent.body["store"] is False
    assert completion.report.lossless, completion.report.losses

    response = completion.response
    assert response.message.text_content() == ANSWER
    assert response.finish_reason == "stop"
    assert response.raw_finish_reason == "completed"
    assert response.id == "resp_test1" and response.model == "test-model"
    assert response.created == CREATED


def case_streaming() -> None:
    server = serve(lambda: sse(*text_stream_events(), done=True))
    client = client_for("responses", server)
    deltas = list(client.stream(text_request()))

    assert server.last.body["stream"] is True
    assert isinstance(deltas[0], StartDelta) and deltas[0].id == "resp_test1"
    assert [d.text for d in deltas if isinstance(d, TextDelta)] == ["Hel", "lo th", "ere."]
    response = assemble(deltas)
    assert response.message.text_content() == ANSWER
    assert response.finish_reason == "stop"
    assert response.model == "test-model"
    assert response.usage is not None and response.usage.output_tokens == 4

    assembled = client.stream_complete(text_request()).response
    assert assembled.message.text_content() == ANSWER and assembled.finish_reason == "stop"


def case_tool_call() -> None:
    call = call_item("fc_test1", "call_w1", "get_weather", PARIS)
    server = serve(lambda: json_response(resource([call], usage=usage_object(80, 18))))
    completion = client_for("responses", server).complete(tool_request())

    sent = server.last.body
    assert sent["tools"] == [
        {
            "type": "function",
            "name": "get_weather",
            "description": WEATHER.description,
            "parameters": WEATHER.parameters,
        },
        {
            "type": "function",
            "name": "get_time",
            "description": CLOCK.description,
            "parameters": CLOCK.parameters,
        },
    ]
    assert sent["tool_choice"] == "auto"
    _assert_one_weather_call(completion.response)

    pending = call_item("fc_test1", "call_w1", "get_weather", "", status="in_progress")
    events = [
        *started(),
        item_event("added", 0, pending),
        args_delta("fc_test1", 0, '{"city":'),
        args_delta("fc_test1", 0, '"Paris",'),
        args_delta("fc_test1", 0, '"unit":"c"}'),
        args_done("fc_test1", 0, PARIS),
        item_event("done", 0, call),
        {"type": "response.completed", "response": resource([call], usage=usage_object(80, 18))},
    ]
    stream_server = serve(lambda: sse(*events))
    streamed = client_for("responses", stream_server).stream_complete(tool_request()).response
    _assert_one_weather_call(streamed)


def _assert_one_weather_call(response: Response) -> None:
    (call,) = response.message.tool_calls()
    assert call.id == "call_w1"
    assert call.name == "get_weather"
    assert json.loads(call.arguments) == {"city": "Paris", "unit": "c"}
    assert not call.synthetic
    assert response.finish_reason == "tool_calls"


def case_parallel_tool_calls() -> None:
    weather = call_item("fc_a", "call_a", "get_weather", PARIS)
    clock = call_item("fc_b", "call_b", "get_time", TOKYO)
    body = resource([reasoning_item(), weather, clock], usage=usage_object(90, 40))
    completion = client_for("responses", serve(lambda: json_response(body))).complete(
        tool_request()
    )
    _assert_two_calls(completion.response)

    # Streamed: a reasoning item at output index 0, then two calls whose argument deltas
    # interleave; the decoder maps output indexes 1 and 2 (by item ID) to tool indexes 0, 1.
    events = [
        *started(),
        item_event("added", 0, {"type": "reasoning", "id": "rs_test1", "summary": []}),
        {
            "type": "response.reasoning_summary_part.added",
            "item_id": "rs_test1",
            "output_index": 0,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": ""},
        },
        {
            "type": "response.reasoning_summary_text.delta",
            "item_id": "rs_test1",
            "output_index": 0,
            "summary_index": 0,
            "delta": "Two lookups are needed.",
        },
        item_event("done", 0, reasoning_item()),
        item_event("added", 1, call_item("fc_a", "call_a", "get_weather", "", "in_progress")),
        item_event("added", 2, call_item("fc_b", "call_b", "get_time", "", "in_progress")),
        args_delta("fc_b", 2, '{"city":'),
        args_delta("fc_a", 1, '{"city":"Par'),
        args_delta("fc_b", 2, '"Tokyo"}'),
        args_delta("fc_a", 1, 'is","unit":"c"}'),
        args_done("fc_a", 1, PARIS),
        args_done("fc_b", 2, TOKYO),
        item_event("done", 1, weather),
        item_event("done", 2, clock),
        {"type": "response.completed", "response": body},
    ]
    server = serve(lambda: sse(*events))
    streamed = client_for("responses", server).stream_complete(tool_request()).response
    _assert_two_calls(streamed)


def _assert_two_calls(response: Response) -> None:
    calls = response.message.tool_calls()
    assert [(c.id, c.name) for c in calls] == [("call_a", "get_weather"), ("call_b", "get_time")]
    assert json.loads(calls[0].arguments) == {"city": "Paris", "unit": "c"}
    assert json.loads(calls[1].arguments) == {"city": "Tokyo"}
    assert response.finish_reason == "tool_calls"


def _error_body(message: str, error_type: str, code: str | None) -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type, "param": None, "code": code}}


ERROR_CASES = [
    (
        400,
        _error_body("Invalid 'input': expected an array.", "invalid_request", None),
        "bad_request",
    ),
    (
        401,
        _error_body("Missing bearer token.", "invalid_request", "invalid_api_key"),
        "authentication",
    ),
    (
        429,
        _error_body("Rate limit reached.", "too_many_requests", "rate_limit_exceeded"),
        "rate_limit",
    ),
    (500, _error_body("The server had an error.", "server_error", "server_error"), "server"),
]


def case_error() -> None:
    for status, body, kind in ERROR_CASES:
        headers = {"retry-after": "7"} if status == 429 else None
        server = serve(partial(json_response, body, status=status, headers=headers))
        with pytest.raises(ModelError) as caught:
            client_for("responses", server).complete(text_request())
        error = caught.value
        assert (error.kind, error.status) == (kind, status)
        assert error.message == body["error"]["message"]
        assert error.code == body["error"]["code"]
        assert error.retry_after == (7.0 if status == 429 else None)

    # Stream errors. Open specification: fields nested under `error`, then `response.failed`.
    failed = resource([], status="failed", error={"code": "server_error", "message": "Overheated."})
    nested = {
        "type": "error",
        "error": {
            "type": "server_error",
            "code": "server_error",
            "message": "Overheated.",
            "param": None,
        },
    }
    server = serve(lambda: sse(*started(), nested, {"type": "response.failed", "response": failed}))
    with pytest.raises(ModelError) as caught:
        client_for("responses", server).stream_complete(text_request())
    assert (caught.value.kind, caught.value.message) == ("server", "Overheated.")

    # Hosted reference: fields at the top level of the event, and the stream simply ends.
    flat = {"type": "error", "code": "rate_limit_exceeded", "message": "Slow down.", "param": None}
    server = serve(lambda: sse(*started(), flat))
    with pytest.raises(ModelError) as caught:
        client_for("responses", server).stream_complete(text_request())
    assert (caught.value.kind, caught.value.message) == ("rate_limit", "Slow down.")


def case_usage() -> None:
    usage = usage_object(1200, 300, cached=1024, reasoning=256)
    body = resource([reasoning_item(), message_item(ANSWER)], usage=usage)
    response = client_for("responses", serve(lambda: json_response(body))).complete(text_request())
    _assert_usage(response.response.usage, usage)

    server = serve(lambda: sse(*text_stream_events(usage)))
    streamed = client_for("responses", server).stream_complete(text_request()).response
    _assert_usage(streamed.usage, usage)


def _assert_usage(usage: Usage | None, raw: dict[str, Any]) -> None:
    assert usage is not None
    assert usage.input_tokens == 1200  # includes the cached tokens, as the IR requires
    assert usage.cached_input_tokens == 1024
    assert usage.output_tokens == 300
    assert usage.reasoning_tokens == 256
    assert usage.total_tokens == 1500
    assert usage.raw == raw


def case_server_round_trip() -> None:
    codec = get_codec("responses")
    for build in (text_request, tool_request, conversation_request):
        original = build()
        report = ConversionReport(target="responses")
        call = codec.encode_request(original, report)
        assert report.lossless, (build.__name__, report.losses)
        decoded = codec.decode_request(call.json, path=call.path)
        assert normalize(decoded) == normalize(original), build.__name__
        assert decoded.max_output_tokens == original.max_output_tokens
        assert decoded.response_format == original.response_format
        assert decoded.tool_choice == original.tool_choice
        again = ConversionReport()
        assert codec.encode_request(decoded, again).json == call.json and again.lossless


def _response_samples() -> list[Response]:
    usage = Usage(input_tokens=90, output_tokens=40, cached_input_tokens=64, reasoning_tokens=12)
    return [
        Response(
            id="resp_rt1",
            model="test-model",
            created=CREATED,
            message=Message.text("assistant", "A longer answer, so that chunks matter."),
            usage=usage,
        ),
        Response(
            id="resp_rt2",
            model="test-model",
            created=CREATED,
            message=Message(
                role="assistant",
                content=[
                    TextPart(text="Checking both."),
                    ToolCallPart(id="call_a", name="get_weather", arguments=PARIS),
                    ToolCallPart(id="call_b", name="get_time", arguments=TOKYO),
                ],
            ),
            finish_reason="tool_calls",
            usage=usage,
        ),
        Response(
            id="resp_rt3",
            model="test-model",
            message=Message.text("assistant", "Cut off mid"),
            finish_reason="length",
            usage=usage,
        ),
    ]


def _assert_same(decoded: Response, original: Response) -> None:
    assert decoded.message.text_content() == original.message.text_content()
    calls = [(c.id, c.name, json.loads(c.arguments)) for c in decoded.message.tool_calls()]
    expected = [(c.id, c.name, json.loads(c.arguments)) for c in original.message.tool_calls()]
    assert calls == expected
    assert decoded.finish_reason == original.finish_reason
    assert decoded.id == original.id and decoded.model == original.model
    assert original.usage is not None and decoded.usage is not None
    fields = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")
    for name in fields:
        assert getattr(decoded.usage, name) == getattr(original.usage, name), name


def case_response_round_trip() -> None:
    codec = get_codec("responses")
    for original in _response_samples():
        body = json.loads(json.dumps(codec.encode_response(original)))
        _assert_same(codec.decode_response(body), original)


def case_stream_round_trip() -> None:
    codec = get_codec("responses")
    for original in _response_samples():
        events = list(codec.encode_stream(response_to_deltas(original, chunk=3)))
        _assert_same(assemble(codec.decode_stream(events)), original)
        # The same events after SSE framing and parsing.
        wire = "".join(format_sse(event) for event in events)
        _assert_same(assemble(codec.decode_stream(parse_sse(iter_lines([wire])))), original)


SUITE = FormatSuite(
    format="responses",
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
    assert not SUITE.missing_cases()
    assert set(REQUIRED_CASES) <= set(SUITE.cases)


# --------------------------------------------------------------------------------------------
# Focused tests
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["response.reasoning", "response.reasoning_text"])
def test_reasoning_text_events_under_both_names(prefix: str) -> None:
    where = {"item_id": "rs_r1", "output_index": 0, "content_index": 0}
    item = {
        "type": "reasoning",
        "id": "rs_r1",
        "status": "completed",
        "summary": [],
        "content": [{"type": "reasoning_text", "text": "Need the weather."}],
        "encrypted_content": ENCRYPTED,
    }
    events = [
        *started(),
        item_event("added", 0, {"type": "reasoning", "id": "rs_r1", "summary": []}),
        {
            "type": "response.content_part.added",
            **where,
            "part": {"type": "reasoning_text", "text": ""},
        },
        {"type": f"{prefix}.delta", **where, "delta": "Need the "},
        {"type": f"{prefix}.delta", **where, "delta": "weather."},
        {"type": f"{prefix}.done", **where, "text": "Need the weather."},
        item_event("done", 0, item),
        item_event("added", 1, {**message_item(""), "status": "in_progress", "content": []}),
        {
            "type": "response.output_text.delta",
            "item_id": "msg_test1",
            "output_index": 1,
            "content_index": 0,
            "delta": ANSWER,
        },
        item_event("done", 1, message_item(ANSWER)),
        {"type": "response.completed", "response": resource([item, message_item(ANSWER)])},
    ]
    deltas = stream_deltas(events)
    reasoning = [d for d in deltas if isinstance(d, ReasoningDelta)]
    assert [d.text for d in reasoning if d.text] == ["Need the ", "weather."]
    assert [(d.opaque, d.opaque_kind) for d in reasoning if d.opaque] == [(ENCRYPTED, "encrypted")]
    response = assemble(deltas)
    part = response.message.reasoning()[0]
    assert part.text == "Need the weather." and part.opaque == ENCRYPTED
    assert response.message.text_content() == ANSWER


def test_reasoning_summary_deltas_and_late_text() -> None:
    """Summary deltas become reasoning deltas; `.done` events fill in text that was skipped."""
    events = [
        *started(),
        {
            "type": "response.reasoning_summary_text.delta",
            "item_id": "rs_s1",
            "output_index": 0,
            "summary_index": 0,
            "delta": "First part.",
        },
        item_event("done", 0, reasoning_item("rs_s1", "First part.")),
        item_event("added", 1, {**message_item(""), "status": "in_progress", "content": []}),
        item_event("done", 1, message_item(ANSWER)),  # no text deltas at all
        {"type": "response.completed", "response": resource([message_item(ANSWER)])},
    ]
    response = assemble(stream_deltas(events))
    assert response.message.reasoning()[0].text == "First part."
    assert response.message.text_content() == ANSWER


def _follow_up(message: Message, model: str = "test-model") -> Request:
    return Request(
        model=model,
        messages=[
            Message.text("user", "Weather in Paris and time in Tokyo?"),
            message,
            Message(
                role="tool",
                content=[ToolResultPart(call_id="call_a", content=[TextPart(text="18 C")])],
            ),
        ],
    )


def test_encrypted_reasoning_is_replayed_only_to_its_origin() -> None:
    codec = get_codec("responses")
    weather = call_item("fc_a", "call_a", "get_weather", PARIS)
    decoded = codec.decode_response(resource([reasoning_item(), weather]))
    part = decoded.message.reasoning()[0]
    assert part.origin == Origin(format="responses", model="test-model")
    assert (part.opaque, part.opaque_kind, part.item_id) == (ENCRYPTED, "encrypted", "rs_test1")

    report = ConversionReport()
    items = body_of(codec.encode_request(_follow_up(decoded.message), report))["input"]
    assert report.lossless, report.losses
    assert items[1] == {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "Two lookups are needed."}],
        "id": "rs_test1",
        "encrypted_content": ENCRYPTED,
    }
    assert [item["type"] for item in items] == [
        "message",
        "reasoning",
        "function_call",
        "function_call_output",
    ]

    # Another model behind the same format: dropped and reported.
    report = ConversionReport()
    request = _follow_up(decoded.message, model="other-model")
    items = body_of(codec.encode_request(request, report))["input"]
    assert "reasoning" not in [item["type"] for item in items]
    assert [loss.field for loss in report.losses] == ["messages.1.content.0"]

    # Another format's signed reasoning: dropped and reported; strict mode refuses.
    foreign = ReasoningPart(
        text="Thinking...", opaque="sig", opaque_kind="signature", origin=Origin(format="messages")
    )
    message = Message(role="assistant", content=[foreign, *decoded.message.tool_calls()])
    report = ConversionReport()
    items = body_of(codec.encode_request(_follow_up(message), report))["input"]
    assert "reasoning" not in [item["type"] for item in items]
    assert report.losses[0].action == "dropped"
    strict = get_codec("responses", CodecOptions(strict=True))
    with pytest.raises(ConversionError):
        strict.encode_request(_follow_up(message), ConversionReport())


@pytest.mark.parametrize(
    ("effort", "sent", "clamped"),
    [
        ("none", "none", False),
        ("minimal", "low", True),
        ("low", "low", False),
        ("medium", "medium", False),
        ("high", "high", False),
        ("xhigh", "xhigh", False),
        ("max", "xhigh", True),
    ],
)
def test_effort_mapping_and_clamping(effort: str, sent: str, clamped: bool) -> None:
    request = text_request().model_copy(update={"effort": effort})
    report = ConversionReport()
    body = get_codec("responses").encode_request(request, report).json
    assert body is not None and body["reasoning"] == {"effort": sent}
    assert [(loss.field, loss.action) for loss in report.losses] == (
        [("effort", "approximated")] if clamped else []
    )


def test_effort_levels_from_profile() -> None:
    """An endpoint whose profile lists `minimal` and `max` (hosted reference) gets them as is."""
    hosted = CodecOptions(extra={"effort_levels": ["none", "minimal", "low", "medium", "max"]})
    codec = get_codec("responses", hosted)
    for effort, sent in (("minimal", "minimal"), ("max", "max"), ("xhigh", "max")):
        request = text_request().model_copy(update={"effort": effort})
        body = codec.encode_request(request, ConversionReport()).json
        assert body is not None and body["reasoning"]["effort"] == sent


def test_refusal_mapping() -> None:
    codec = get_codec("responses")
    refusal = {
        "type": "message",
        "id": "msg_ref1",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "refusal", "refusal": "I can't help with that."}],
    }
    response = codec.decode_response(resource([refusal]))
    assert response.message.content == [RefusalPart(text="I can't help with that.")]
    assert response.finish_reason == "stop"

    body = codec.encode_response(response)
    assert body["output"][0]["content"] == [
        {"type": "refusal", "refusal": "I can't help with that."}
    ]

    request = Request(
        model="test-model",
        messages=[
            Message.text("user", "Do something bad."),
            Message(role="assistant", content=[RefusalPart(text="I can't help with that.")]),
            Message.text("user", "Fine."),
        ],
    )
    report = ConversionReport()
    sent = codec.encode_request(request, report).json
    assert sent is not None and report.lossless
    assert sent["input"][1] == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "refusal", "refusal": "I can't help with that."}],
    }
    assert codec.decode_request(sent).messages[1].content == [
        RefusalPart(text="I can't help with that.")
    ]


@pytest.mark.parametrize(
    ("status", "details", "error", "finish", "raw"),
    [
        ("incomplete", {"reason": "max_output_tokens"}, None, "length", "max_output_tokens"),
        ("incomplete", {"reason": "content_filter"}, None, "content_filter", "content_filter"),
        ("incomplete", {"reason": "max_messages"}, None, "other", "max_messages"),
        ("failed", None, {"code": "server_error", "message": "Boom."}, "error", "server_error"),
    ],
)
def test_status_mapping(status: str, details: Any, error: Any, finish: str, raw: str) -> None:
    codec = get_codec("responses")
    body = resource(
        [message_item("Partial")], status=status, incomplete_details=details, error=error
    )
    response = codec.decode_response(body)
    assert (response.finish_reason, response.raw_finish_reason) == (finish, raw)
    encoded = codec.encode_response(response)
    assert (encoded["status"], encoded["incomplete_details"]) == (status, details)

    events = [*started(), {"type": f"response.{status}", "response": body}]
    deltas = stream_deltas(events)
    if status == "failed":
        with pytest.raises(ModelError):
            assemble(deltas)
    else:
        assert assemble(deltas).finish_reason == finish


def test_stream_that_ends_without_a_terminal_event_is_an_error() -> None:
    events = text_stream_events()[:-1]  # drop response.completed
    with pytest.raises(ModelError, match="terminal response event"):
        assemble(stream_deltas(events))


def test_items_announced_only_in_the_final_response() -> None:
    """A server that sends lifecycle events only still yields the final content."""
    call = call_item("fc_a", "call_a", "get_weather", PARIS)
    events = [*started(), {"type": "response.completed", "response": resource([call])}]
    response = assemble(stream_deltas(events))
    assert [(c.id, c.arguments) for c in response.message.tool_calls()] == [("call_a", PARIS)]
    assert response.finish_reason == "tool_calls"


def test_images_and_files_by_reference_round_trip() -> None:
    codec = get_codec("responses")
    parts: list[Any] = [
        TextPart(text="Compare these."),
        ImagePart(url="https://example.com/a.png", detail="high"),
        ImagePart(data="iVBORw0KGgo=", media_type="image/png"),
        FilePart(file_id="file-abc123", name="report.pdf"),
        FilePart(url="https://example.com/b.pdf"),
        FilePart(data="JVBERi0=", media_type="application/pdf", name="c.pdf"),
    ]
    request = Request(model="test-model", messages=[Message(role="user", content=parts)])
    report = ConversionReport()
    body = body_of(codec.encode_request(request, report))
    assert report.lossless, report.losses
    data_url = "data:image/png;base64,iVBORw0KGgo="
    assert body["input"][0]["content"] == [
        {"type": "input_text", "text": "Compare these."},
        {"type": "input_image", "image_url": "https://example.com/a.png", "detail": "high"},
        {"type": "input_image", "image_url": data_url, "detail": "auto"},
        {"type": "input_file", "file_id": "file-abc123", "filename": "report.pdf"},
        {"type": "input_file", "file_url": "https://example.com/b.pdf"},
        {
            "type": "input_file",
            "file_data": "data:application/pdf;base64,JVBERi0=",
            "filename": "c.pdf",
        },
    ]
    decoded = codec.decode_request(body).messages[0].content
    assert decoded[:2] == parts[:2]
    assert decoded[2] == ImagePart(url=data_url, detail="auto")  # the IR accepts data: URLs
    assert decoded[3:] == parts[3:]


def test_arguments_sent_only_in_done_events() -> None:
    """Servers that skip argument deltas: `.done` events and the final item carry them."""
    weather = call_item("fc_a", "call_a", "get_weather", PARIS)
    clock = call_item("fc_b", "call_b", "get_time", TOKYO)
    events = [
        *started(),
        item_event("added", 0, call_item("fc_a", "call_a", "get_weather", "", "in_progress")),
        args_done("fc_a", 0, PARIS),
        item_event("done", 0, weather),
        item_event("added", 1, call_item("fc_b", "call_b", "get_time", "", "in_progress")),
        args_delta("fc_b", 1, '{"ci'),
        item_event("done", 1, clock),  # no `.done` for the arguments; the item completes them
        {"type": "response.completed", "response": resource([weather, clock])},
    ]
    _assert_two_calls(assemble(stream_deltas(events)))


def test_decode_request_accepts_string_input_and_keeps_extensions() -> None:
    codec = get_codec("responses")
    body = {
        "model": "test-model",
        "instructions": "Be brief.",
        "input": "Hi",
        "previous_response_id": "resp_prev",
        "store": True,
        "include": ["reasoning.encrypted_content"],
        "reasoning": {"effort": "high", "summary": "auto"},
        "text": {"format": {"type": "json_object"}, "verbosity": "low"},
        "tools": [
            {"type": "function", "name": "get_time", "parameters": CLOCK.parameters},
            {"type": "web_search"},
        ],
        "tool_choice": {
            "type": "allowed_tools",
            "mode": "auto",
            "tools": [{"type": "function", "name": "get_time"}],
        },
        "max_output_tokens": 300,
        "temperature": 0.2,
        "stream": True,
    }
    request = codec.decode_request(body, path=PATH)
    assert request.system_text() == "Be brief."
    assert [(m.role, m.text_content()) for m in request.messages] == [("user", "Hi")]
    assert request.effort == "high" and request.stream and request.max_output_tokens == 300
    assert request.response_format is not None and request.response_format.type == "json_object"
    assert [tool.name for tool in request.tools] == ["get_time"]
    assert request.tool_choice is None and request.sampling.temperature == 0.2
    assert request.extensions == {
        "responses": {
            "previous_response_id": "resp_prev",
            "store": True,
            "include": ["reasoning.encrypted_content"],
            "reasoning": {"summary": "auto"},
            "text": {"verbosity": "low"},
            "tools": [{"type": "web_search"}],
            "tool_choice": body["tool_choice"],
        }
    }

    # Re-encoded: stateless extensions are forwarded, stateful ones are reported.
    report = ConversionReport()
    sent = codec.encode_request(request, report).json
    assert sent is not None
    assert sent["store"] is False and "previous_response_id" not in sent
    assert sent["include"] == ["reasoning.encrypted_content"]
    assert sent["reasoning"] == {"effort": "high", "summary": "auto"}
    assert sent["text"] == {"format": {"type": "json_object"}, "verbosity": "low"}
    assert sent["tools"][-1] == {"type": "web_search"}
    assert sent["tool_choice"] == body["tool_choice"]
    assert sorted(loss.field for loss in report.losses) == [
        "extensions.responses.previous_response_id",
        "extensions.responses.store",
    ]

    # An endpoint that keeps state itself may receive them when configured explicitly.
    passthrough = get_codec("responses", CodecOptions(extra={"stateful_passthrough": True}))
    report = ConversionReport()
    sent = passthrough.encode_request(request, report).json
    assert sent is not None and report.lossless
    assert sent["previous_response_id"] == "resp_prev" and sent["store"] is True


def test_decode_request_folds_items_into_turns() -> None:
    codec = get_codec("responses")
    body = {
        "model": "test-model",
        "input": [
            {"type": "message", "role": "developer", "content": "Use tools."},
            {"role": "user", "content": [{"type": "input_text", "text": "Weather?"}]},
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"type": "summary_text", "text": "Look it up."}],
                "encrypted_content": ENCRYPTED,
            },
            {
                "type": "function_call",
                "call_id": "call_a",
                "name": "get_weather",
                "arguments": PARIS,
            },
            {"type": "function_call_output", "call_id": "call_a", "output": "18 C"},
            {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "18 C in Paris."}],
            },
            {"type": "item_reference", "id": "msg_old"},
        ],
    }
    request = codec.decode_request(body)
    assert request.system_text() == "Use tools."
    roles = [message.role for message in request.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    turn = request.messages[1]
    assert [part.type for part in turn.content] == ["reasoning", "tool_call"]
    result = request.messages[2].tool_results()[0]
    assert (result.call_id, result.name, result.text()) == ("call_a", "get_weather", "18 C")
    assert request.messages[3].metadata == {"phase": "final_answer"}
    unsupported = request.extensions["responses"]["unsupported"]
    assert [(entry["where"], entry["type"]) for entry in unsupported] == [
        ("input[6]", "item_reference")
    ]

    report = ConversionReport()
    sent = codec.encode_request(request, report).json
    assert sent is not None
    assert sent["input"][-1]["phase"] == "final_answer"
    assert [loss.field for loss in report.losses] == ["extensions.responses.unsupported"]


def test_encode_stream_produces_a_valid_event_sequence() -> None:
    """Event order and required fields of the open specification (2026-04-24)."""
    required = {
        "response.created": {"response"},
        "response.in_progress": {"response"},
        "response.completed": {"response"},
        "response.output_item.added": {"output_index", "item"},
        "response.output_item.done": {"output_index", "item"},
        "response.content_part.added": {"item_id", "output_index", "content_index", "part"},
        "response.content_part.done": {"item_id", "output_index", "content_index", "part"},
        "response.output_text.delta": {"item_id", "output_index", "content_index", "delta"},
        "response.output_text.done": {"item_id", "output_index", "content_index", "text"},
        "response.reasoning.delta": {"item_id", "output_index", "content_index", "delta"},
        "response.reasoning.done": {"item_id", "output_index", "content_index", "text"},
        "response.function_call_arguments.delta": {"item_id", "output_index", "delta"},
        "response.function_call_arguments.done": {"item_id", "output_index", "arguments"},
    }
    item_fields = {
        "message": {"type", "id", "status", "role", "content"},
        "function_call": {"type", "id", "call_id", "name", "arguments", "status"},
        "reasoning": {"type", "id", "summary"},
    }
    resource_fields = set(resource([]))
    original = Response(
        id="resp_seq",
        model="test-model",
        message=Message(
            role="assistant",
            content=[
                ReasoningPart(text="Plan it.", opaque=ENCRYPTED, opaque_kind="encrypted"),
                TextPart(text="Calling a tool."),
                ToolCallPart(id="call_a", name="get_weather", arguments=PARIS),
            ],
        ),
        finish_reason="tool_calls",
        usage=Usage(input_tokens=50, output_tokens=20),
    )
    events = list(get_codec("responses").encode_stream(response_to_deltas(original, chunk=4)))
    payloads = [json.loads(event.data) for event in events]

    assert [event.event for event in events] == [payload["type"] for payload in payloads]
    assert [payload["sequence_number"] for payload in payloads] == list(range(len(payloads)))
    assert payloads[0]["type"] == "response.created"
    assert payloads[-1]["type"] == "response.completed"
    opened: set[str] = set()
    for payload in payloads:
        kind = payload["type"]
        assert required[kind] <= set(payload), kind
        if "response" in payload:
            assert set(payload["response"]) == resource_fields
        if "item" in payload:
            item = payload["item"]
            assert item_fields[item["type"]] <= set(item)
            if kind.endswith(".added"):
                opened.add(item["id"])
        elif "item_id" in payload:
            assert payload["item_id"] in opened, f"{kind} before its output_item.added"

    final = payloads[-1]["response"]
    assert final["status"] == "completed" and final["id"] == "resp_seq"
    assert [item["type"] for item in final["output"]] == ["reasoning", "message", "function_call"]
    assert final["output"][0]["encrypted_content"] == ENCRYPTED
    assert final["output"][2]["arguments"] == PARIS
    assert final["usage"] == usage_object(50, 20)


def test_encode_stream_reports_errors_and_incomplete_responses() -> None:
    codec = get_codec("responses")
    failed = [
        json.loads(e.data)
        for e in codec.encode_stream([ErrorDelta(kind="rate_limit", message="Slow.")])
    ]
    assert [p["type"] for p in failed][-2:] == ["error", "response.failed"]
    error_event = failed[-2]
    assert error_event["error"]["type"] == "too_many_requests"  # nested: open specification
    assert error_event["message"] == "Slow."  # top level: hosted reference
    assert failed[-1]["response"]["status"] == "failed"

    cut = [TextDelta(text="Partial"), FinishDelta(reason="length")]
    incomplete = [json.loads(e.data) for e in codec.encode_stream(cut)]
    assert incomplete[-1]["type"] == "response.incomplete"
    assert incomplete[-1]["response"]["incomplete_details"] == {"reason": "max_output_tokens"}
    assert incomplete[-1]["response"]["output"][0]["status"] == "incomplete"

    hosted = get_codec(
        "responses", CodecOptions(extra={"reasoning_event": "response.reasoning_text"})
    )
    names = [json.loads(e.data)["type"] for e in hosted.encode_stream([ReasoningDelta(text="Hm.")])]
    assert "response.reasoning_text.delta" in names


def test_encode_error_uses_the_specification_shape() -> None:
    codec = get_codec("responses")
    status, body = codec.encode_error(
        ModelError("rate_limit", "Slow down.", code="rate_limit_exceeded")
    )
    assert status == 429
    assert body == {
        "error": {
            "message": "Slow down.",
            "type": "too_many_requests",
            "param": None,
            "code": "rate_limit_exceeded",
        }
    }
    assert codec.encode_error(ModelError("bad_request", "No."))[0] == 400
    assert codec.encode_error(ModelError("server", "Boom.", status=502))[0] == 502


def test_losses_are_reported() -> None:
    codec = get_codec("responses")
    request = text_request().model_copy(
        update={
            "max_output_tokens": 8,
            "budget_tokens": 1024,
            "sampling": text_request().sampling.model_copy(
                update={"stop": ["END"], "seed": 7, "top_k": 40, "presence_penalty": 0.5}
            ),
        }
    )
    report = ConversionReport()
    body = codec.encode_request(request, report).json
    assert body is not None and body["max_output_tokens"] == 16
    assert "presence_penalty" not in body
    assert sorted(loss.field for loss in report.losses) == [
        "budget_tokens",
        "max_output_tokens",
        "sampling.presence_penalty",
        "sampling.seed",
        "sampling.stop",
        "sampling.top_k",
    ]
    with pytest.raises(ConversionError):
        get_codec("responses", CodecOptions(strict=True)).encode_request(
            request, ConversionReport()
        )
