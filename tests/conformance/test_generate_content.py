"""Conformance suite for the generate-content format (claim C-004).

Every mock reply below is written by hand in the wire shapes of the format's API reference and
discovery document (response objects, `alt=sse` data-only chunks, the canonical error body;
checked 2026-10-16). None is produced by the codec under test.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from stepswitch.compat.codecs.base import CodecOptions
from stepswitch.compat.codecs.generate_content import GenerateContentCodec
from stepswitch.compat.errors import ModelError
from stepswitch.compat.framing import StreamEvent, format_sse, iter_lines, parse_sse
from stepswitch.compat.ir import (
    ConversionError,
    ConversionReport,
    Delta,
    ErrorDelta,
    ImagePart,
    Message,
    NamedToolChoice,
    Origin,
    ReasoningPart,
    Request,
    Response,
    ResponseFormat,
    TextDelta,
    TextPart,
    ToolCallArgsDelta,
    ToolCallPart,
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

FORMAT = "generate-content"
MODEL = "test-model"
ROUTE = r"/v1beta/models/test-model:generateContent"
STREAM_ROUTE = r"/v1beta/models/test-model:streamGenerateContent"
OWN = Origin(format="generate-content", model=MODEL)
SIGNATURE = "CiQBcsjafM2ktzzMJqI0GmvC0KFwVsfsZLv7pQ1o"

# --------------------------------------------------------------------------------------------
# Hand-written wire replies
# --------------------------------------------------------------------------------------------


def _reply(
    parts: list[dict[str, Any]],
    *,
    finish: str = "STOP",
    usage: dict[str, Any] | None = None,
    response_id: str = "rsp-7Hc2aIzLAqLp",
) -> dict[str, Any]:
    return {
        "candidates": [
            {"content": {"parts": parts, "role": "model"}, "finishReason": finish, "index": 0}
        ],
        "usageMetadata": usage
        or {"promptTokenCount": 9, "candidatesTokenCount": 3, "totalTokenCount": 12},
        "modelVersion": MODEL,
        "responseId": response_id,
    }


def _chunk(
    parts: list[dict[str, Any]],
    *,
    finish: str | None = None,
    usage: dict[str, Any] | None = None,
    response_id: str = "rsp-stream-1",
) -> dict[str, Any]:
    candidate: dict[str, Any] = {"content": {"parts": parts, "role": "model"}, "index": 0}
    if finish:
        candidate["finishReason"] = finish
    chunk: dict[str, Any] = {
        "candidates": [candidate],
        "modelVersion": MODEL,
        "responseId": response_id,
    }
    if usage:
        chunk["usageMetadata"] = usage
    return chunk


def _error(
    status: int, name: str, message: str, *, headers: dict[str, str] | None = None, **extra: Any
) -> httpx.Response:
    return json_response(
        {"error": {"code": status, "message": message, "status": name, **extra}}, status, headers
    )


def _server(responder: Callable[[Recorded], httpx.Response], *, stream: bool = False) -> MockServer:
    return MockServer().route("POST", STREAM_ROUTE if stream else ROUTE, responder)


def _expect_error(call: Callable[[], object]) -> ModelError:
    try:
        call()
    except ModelError as exc:
        return exc
    raise AssertionError("expected a ModelError")


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
    server = _server(lambda _r: json_response(_reply([{"text": ANSWER}])))
    completion = client_for(FORMAT, server).complete(text_request())
    sent = server.last
    assert sent.method == "POST" and sent.path == "/v1beta/models/test-model:generateContent"
    assert sent.params == {} and "model" not in sent.body
    assert sent.body["systemInstruction"] == {"parts": [{"text": "You are terse."}]}
    assert sent.body["contents"] == [{"role": "user", "parts": [{"text": "Say hello."}]}]
    assert sent.body["generationConfig"] == {"maxOutputTokens": 64}
    response = completion.response
    assert response.message.text_content() == ANSWER
    assert response.finish_reason == "stop" and response.raw_finish_reason == "STOP"
    assert response.id == "rsp-7Hc2aIzLAqLp" and response.model == MODEL
    assert completion.report.lossless


def case_streaming() -> None:
    chunks = [
        _chunk([{"text": "Hel"}], usage={"promptTokenCount": 9, "totalTokenCount": 9}),
        _chunk([{"text": "lo the"}]),
        _chunk(
            [{"text": "re."}],
            finish="STOP",
            usage={"promptTokenCount": 9, "candidatesTokenCount": 3, "totalTokenCount": 12},
        ),
    ]
    server = _server(lambda _r: sse_response(chunks), stream=True)
    deltas = list(client_for(FORMAT, server).stream(text_request()))
    sent = server.last
    assert sent.path == "/v1beta/models/test-model:streamGenerateContent"
    assert sent.params == {"alt": "sse"}
    assert sent.body["generationConfig"] == {"maxOutputTokens": 64}
    assert [d.text for d in deltas if isinstance(d, TextDelta)] == ["Hel", "lo the", "re."]
    response = assemble(deltas)
    assert response.message.text_content() == ANSWER
    assert response.finish_reason == "stop" and response.raw_finish_reason == "STOP"
    assert response.id == "rsp-stream-1" and response.model == MODEL
    assert response.usage is not None
    assert (response.usage.input_tokens, response.usage.output_tokens) == (9, 3)


def case_tool_call() -> None:
    call_part = {
        "functionCall": {"name": "get_weather", "args": {"city": "Paris", "unit": "c"}},
        "thoughtSignature": SIGNATURE,
    }
    server = _server(lambda _r: json_response(_reply([call_part])))
    response = client_for(FORMAT, server).complete(tool_request()).response
    sent = server.last.body
    assert sent["tools"] == [
        {
            "functionDeclarations": [
                {"name": t.name, "description": t.description, "parametersJsonSchema": t.parameters}
                for t in (WEATHER, CLOCK)
            ]
        }
    ]
    assert sent["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}
    (call,) = response.message.tool_calls()
    assert call.name == "get_weather" and call.id and call.synthetic
    assert json.loads(call.arguments) == {"city": "Paris", "unit": "c"}
    assert response.finish_reason == "tool_calls" and response.raw_finish_reason == "STOP"
    (reasoning,) = response.message.reasoning()
    assert (reasoning.opaque, reasoning.opaque_kind, reasoning.origin) == (
        SIGNATURE,
        "signature",
        OWN,
    )

    # Streamed: the format sends each function call whole, in one chunk.
    chunks = [
        _chunk(
            [
                {
                    "functionCall": {
                        "id": "fc-7",
                        "name": "get_weather",
                        "args": {"city": "Paris"},
                    },
                    "thoughtSignature": SIGNATURE,
                }
            ]
        ),
        _chunk([{"text": ""}], finish="STOP"),
    ]
    server = _server(lambda _r: sse_response(chunks), stream=True)
    deltas = list(client_for(FORMAT, server).stream(tool_request()))
    assert [d.delta for d in deltas if isinstance(d, ToolCallArgsDelta)] == ['{"city":"Paris"}']
    streamed = assemble(deltas)
    (call,) = streamed.message.tool_calls()
    assert (call.id, call.name, call.synthetic) == ("fc-7", "get_weather", False)
    assert json.loads(call.arguments) == {"city": "Paris"}
    assert streamed.finish_reason == "tool_calls"


def case_parallel_tool_calls() -> None:
    parts = [
        {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
        {"functionCall": {"name": "get_time", "args": {"city": "Tokyo"}}},
    ]
    server = _server(lambda _r: json_response(_reply(parts)))
    response = client_for(FORMAT, server).complete(tool_request()).response
    calls = response.message.tool_calls()
    assert [c.name for c in calls] == ["get_weather", "get_time"]
    assert all(c.id and c.synthetic for c in calls) and len({c.id for c in calls}) == 2
    assert [json.loads(c.arguments) for c in calls] == [{"city": "Paris"}, {"city": "Tokyo"}]
    assert response.finish_reason == "tool_calls"

    chunks = [
        _chunk(
            [
                {"functionCall": {"id": "fc-1", "name": "get_weather", "args": {"city": "Paris"}}},
                {"functionCall": {"id": "fc-2", "name": "get_time", "args": {"city": "Tokyo"}}},
            ],
            finish="STOP",
        )
    ]
    server = _server(lambda _r: sse_response(chunks), stream=True)
    streamed = assemble(list(client_for(FORMAT, server).stream(tool_request())))
    calls = streamed.message.tool_calls()
    assert [(c.id, c.name) for c in calls] == [("fc-1", "get_weather"), ("fc-2", "get_time")]

    # Outbound history: calls in one model content, both results in one user content.
    server = _server(lambda _r: json_response(_reply([{"text": ANSWER}])))
    client_for(FORMAT, server).complete(conversation_request())
    contents = server.last.body["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user", "model", "user"]
    assert [p["functionCall"]["id"] for p in contents[1]["parts"]] == ["call_a", "call_b"]
    assert contents[2]["parts"] == [
        {
            "functionResponse": {
                "id": "call_a",
                "name": "get_weather",
                "response": {"result": "18 C, sunny"},
            }
        },
        {"functionResponse": {"id": "call_b", "name": "get_time", "response": {"result": "21:04"}}},
    ]


def case_error() -> None:
    cases = [
        (400, "INVALID_ARGUMENT", "bad_request", None),
        (401, "UNAUTHENTICATED", "authentication", None),
        (429, "RESOURCE_EXHAUSTED", "rate_limit", "7"),
        (500, "INTERNAL", "server", None),
        (503, "UNAVAILABLE", "overloaded", None),
    ]
    for status, name, kind, retry_after in cases:
        headers = {"retry-after": retry_after} if retry_after else None
        server = _server(lambda _r, s=status, n=name, h=headers: _error(s, n, "boom", headers=h))
        error = _expect_error(lambda srv=server: client_for(FORMAT, srv).complete(text_request()))
        assert (error.kind, error.status, error.code) == (kind, status, name), error
        assert error.message == "boom"
        assert error.retry_after == (7.0 if retry_after else None)
        assert error.retryable == (kind in {"rate_limit", "server", "overloaded"})

    # Without a retry-after header the retry-info detail gives the delay.
    detail = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "13s"}
    server = _server(lambda _r: _error(429, "RESOURCE_EXHAUSTED", "quota", details=[detail]))
    error = _expect_error(lambda: client_for(FORMAT, server).complete(text_request()))
    assert (error.kind, error.retry_after) == ("rate_limit", 13.0)

    # An error chunk in the middle of a stream.
    chunks: list[dict[str, Any]] = [
        _chunk([{"text": "Hel"}]),
        {"error": {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}},
    ]
    server = _server(lambda _r: sse_response(chunks), stream=True)
    deltas = list(client_for(FORMAT, server).stream(text_request()))
    last = deltas[-1]
    assert isinstance(last, ErrorDelta) and last.kind == "overloaded" and last.status == 503
    assert _expect_error(lambda: assemble(deltas)).kind == "overloaded"


def case_usage() -> None:
    usage = {
        "promptTokenCount": 120,
        "cachedContentTokenCount": 100,
        "candidatesTokenCount": 30,
        "thoughtsTokenCount": 25,
        "totalTokenCount": 175,
    }
    server = _server(lambda _r: json_response(_reply([{"text": ANSWER}], usage=usage)))
    result = client_for(FORMAT, server).complete(text_request()).response.usage
    assert result is not None
    assert (result.input_tokens, result.cached_input_tokens) == (120, 100)  # input has cached
    assert (result.output_tokens, result.reasoning_tokens) == (55, 25)  # output has thoughts
    assert result.total_tokens == 175 and result.raw == usage

    chunks = [
        _chunk(
            [{"text": "Hello"}],
            usage={"promptTokenCount": 120, "cachedContentTokenCount": 100, "totalTokenCount": 120},
        ),
        _chunk([{"text": " there."}], finish="STOP", usage=usage),
    ]
    server = _server(lambda _r: sse_response(chunks), stream=True)
    streamed = assemble(list(client_for(FORMAT, server).stream(text_request()))).usage
    assert streamed is not None
    assert (streamed.input_tokens, streamed.cached_input_tokens) == (120, 100)
    assert (streamed.output_tokens, streamed.reasoning_tokens) == (55, 25)


def case_server_round_trip() -> None:
    codec = GenerateContentCodec()
    for request in (text_request(), tool_request(), conversation_request()):
        report = ConversionReport()
        call = codec.encode_request(request, report)
        assert report.lossless, report.losses
        assert call.path == "/v1beta/models/test-model:generateContent"
        decoded = codec.decode_request(call.json, path=call.path)
        assert normalize(decoded) == normalize(request)
        assert decoded.model == request.model and not decoded.stream
        assert decoded.max_output_tokens == request.max_output_tokens
        assert decoded.tool_choice == request.tool_choice
        if request.response_format is not None:
            assert decoded.response_format is not None
            assert decoded.response_format.json_schema == request.response_format.json_schema
    streaming = text_request().model_copy(update={"stream": True})
    call = codec.encode_request(streaming, ConversionReport())
    assert (call.path, call.params) == (
        "/v1beta/models/test-model:streamGenerateContent",
        {"alt": "sse"},
    )
    assert codec.decode_request(call.json, path=call.path).stream


def _tool_response(content: list[Any]) -> Response:
    return Response(
        id="rsp-rt-1",
        model=MODEL,
        message=Message(role="assistant", content=content),
        finish_reason="tool_calls",
        raw_finish_reason="STOP",
        usage=Usage(
            input_tokens=120, output_tokens=55, cached_input_tokens=100, reasoning_tokens=25
        ),
    )


def case_response_round_trip() -> None:
    codec = GenerateContentCodec()
    original = _tool_response(
        [
            ReasoningPart(text="Two lookups are needed.", origin=OWN),
            ReasoningPart(opaque=SIGNATURE, opaque_kind="signature", origin=OWN),
            ToolCallPart(id="fc-1", name="get_weather", arguments='{"city":"Paris"}'),
            ToolCallPart(id="fc-2", name="get_time", arguments='{"city":"Tokyo"}'),
        ]
    )
    body = codec.encode_response(original)
    (candidate,) = body["candidates"]
    assert candidate["finishReason"] == "STOP" and candidate["content"]["role"] == "model"
    assert candidate["content"]["parts"] == [
        {"text": "Two lookups are needed.", "thought": True},
        {
            "functionCall": {"id": "fc-1", "name": "get_weather", "args": {"city": "Paris"}},
            "thoughtSignature": SIGNATURE,
        },
        {"functionCall": {"id": "fc-2", "name": "get_time", "args": {"city": "Tokyo"}}},
    ]
    assert body["usageMetadata"] == {
        "promptTokenCount": 120,
        "cachedContentTokenCount": 100,
        "candidatesTokenCount": 30,
        "thoughtsTokenCount": 25,
        "totalTokenCount": 175,
    }
    decoded = codec.decode_response(json.loads(json.dumps(body)))
    assert _without_raw(decoded) == _without_raw(original)


def case_stream_round_trip() -> None:
    codec = GenerateContentCodec()
    original = _tool_response(
        [
            ReasoningPart(
                text="Two lookups are needed.",
                opaque=SIGNATURE,
                opaque_kind="signature",
                origin=OWN,
            ),
            TextPart(text="Checking both."),
            ToolCallPart(id="fc-1", name="get_weather", arguments='{"city":"Paris"}'),
            ToolCallPart(id="fc-2", name="get_time", arguments='{"city":"Tokyo"}'),
        ]
    )
    events = list(codec.encode_stream(response_to_deltas(original, chunk=3)))
    wire = "".join(format_sse(event) for event in events)
    parsed = list(parse_sse(iter_lines([wire])))
    assert all(event.event is None for event in parsed)
    chunks = [json.loads(event.data) for event in parsed]
    signed = [p for c in chunks for p in c["candidates"][0].get("content", {}).get("parts", [])]
    assert [p for p in signed if "thoughtSignature" in p] == [
        {"text": "Che", "thoughtSignature": SIGNATURE}
    ]
    assert chunks[-1]["candidates"][0]["finishReason"] == "STOP"
    assert chunks[-1]["usageMetadata"]["totalTokenCount"] == 175
    assembled = _stamp(assemble(list(codec.decode_stream(parsed))), OWN)
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
    call = GenerateContentCodec(options).encode_request(request, report)
    return call.json, report


def test_signatures_go_back_on_their_own_part() -> None:
    codec = GenerateContentCodec()
    reply = _reply(
        [
            {"text": "Planning the lookups.", "thought": True},
            {
                "functionCall": {"name": "get_weather", "args": {"city": "Paris"}},
                "thoughtSignature": SIGNATURE,
            },
            {"functionCall": {"name": "get_time", "args": {"city": "Tokyo"}}},
        ]
    )
    answer = codec.decode_response(reply).message
    calls = answer.tool_calls()
    results = [
        Message(role="tool", content=[ToolResultPart(call_id=c.id, content=[TextPart(text="ok")])])
        for c in calls
    ]
    request = Request(model=MODEL, messages=[Message.text("user", "Q?"), answer, *results])
    body, report = _encode(request)
    assert report.lossless, report.losses
    assert body["contents"][1]["parts"] == [
        {"text": "Planning the lookups.", "thought": True},
        {
            "functionCall": {"name": "get_weather", "args": {"city": "Paris"}},
            "thoughtSignature": SIGNATURE,
        },
        {"functionCall": {"name": "get_time", "args": {"city": "Tokyo"}}},
    ]
    assert body["contents"][2]["parts"] == [
        {"functionResponse": {"name": "get_weather", "response": {"result": "ok"}}},
        {"functionResponse": {"name": "get_time", "response": {"result": "ok"}}},
    ]

    # Reasoning from another origin is dropped and reported; strict mode rejects it.
    foreign = Origin(format="messages", model="other-model")
    moved = [
        p.model_copy(update={"origin": foreign}) if isinstance(p, ReasoningPart) else p
        for p in answer.content
    ]
    request = request.model_copy(
        update={"messages": [request.messages[0], Message(role="assistant", content=moved)]}
    )
    body, report = _encode(request)
    assert all(
        "thought" not in p and "thoughtSignature" not in p for p in body["contents"][1]["parts"]
    )
    assert [loss.field for loss in report.losses] == [
        "messages[1].content[0]",
        "messages[1].content[1]",
    ]
    with pytest.raises(ConversionError):
        _encode(request, CodecOptions(strict=True))

    # A trailing signature (streams send it last) goes back on an empty text part.
    trailing = Message(
        role="assistant",
        content=[
            TextPart(text="Done."),
            ReasoningPart(opaque=SIGNATURE, opaque_kind="signature", origin=OWN),
        ],
    )
    body, _ = _encode(Request(model=MODEL, messages=[Message.text("user", "Go."), trailing]))
    assert body["contents"][1]["parts"] == [
        {"text": "Done."},
        {"text": "", "thoughtSignature": SIGNATURE},
    ]


@pytest.mark.parametrize(
    ("text", "is_error", "wire"),
    [
        ("18 C", False, {"result": "18 C"}),
        ("boom", True, {"error": "boom"}),
        ('{"temp":18}', False, {"temp": 18}),
        ('{"temp": 18}', False, {"result": '{"temp": 18}'}),
        ('{"result":"x"}', False, {"result": '{"result":"x"}'}),
        ('{"code":1}', True, {"error": {"code": 1}}),
        ("", False, {"result": ""}),
    ],
)
def test_function_response_objects_round_trip(text: str, is_error: bool, wire: Any) -> None:
    content = [TextPart(text=text)] if text else []
    request = Request(
        model=MODEL,
        messages=[
            Message.text("user", "Go."),
            Message(role="assistant", content=[ToolCallPart(id="c1", name="run")]),
            Message(
                role="tool",
                content=[ToolResultPart(call_id="c1", content=content, is_error=is_error)],
            ),
        ],
    )
    body, report = _encode(request)
    assert report.lossless
    (part,) = body["contents"][2]["parts"]
    assert part == {"functionResponse": {"id": "c1", "name": "run", "response": wire}}
    (result,) = GenerateContentCodec().decode_request(body).messages[2].tool_results()
    assert (result.text(), result.is_error, result.name) == (text, is_error, "run")


def test_function_response_without_a_name() -> None:
    request = Request(
        model=MODEL,
        messages=[Message(role="tool", content=[ToolResultPart(call_id="x")])],
    )
    body, report = _encode(request)
    assert body["contents"][0]["parts"][0]["functionResponse"]["name"] == "unknown"
    assert [(loss.field, loss.action) for loss in report.losses] == [
        ("messages[0].content[0].name", "approximated")
    ]


def test_effort_maps_to_thinking_levels_and_budgets() -> None:
    levels = {"minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
    for effort, level in levels.items():
        body, report = _encode(text_request().model_copy(update={"effort": effort}))
        thinking = body["generationConfig"]["thinkingConfig"]
        assert thinking == {"thinkingLevel": level, "includeThoughts": True} and report.lossless
    for effort, level in (("none", "MINIMAL"), ("xhigh", "HIGH"), ("max", "HIGH")):
        body, report = _encode(text_request().model_copy(update={"effort": effort}))
        assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == level
        assert [(loss.field, loss.action) for loss in report.losses] == [("effort", "approximated")]
    body, _ = _encode(text_request().model_copy(update={"effort": "none"}))
    assert "includeThoughts" not in body["generationConfig"]["thinkingConfig"]
    body, report = _encode(text_request().model_copy(update={"budget_tokens": 2048}))
    assert body["generationConfig"]["thinkingConfig"] == {
        "thinkingBudget": 2048,
        "includeThoughts": True,
    }
    both = text_request().model_copy(update={"budget_tokens": 0, "effort": "high"})
    body, report = _encode(both)
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert [loss.action for loss in report.losses] == ["approximated"]
    inbound = {
        "contents": [{"role": "user", "parts": [{"text": "Hi"}]}],
        "generationConfig": {"thinkingConfig": {"thinkingLevel": "low", "includeThoughts": False}},
    }
    decoded = GenerateContentCodec().decode_request(
        inbound, path="/v1beta/models/m:generateContent"
    )
    assert (decoded.model, decoded.effort) == ("m", "low")
    body, _ = _encode(decoded)
    assert body["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "LOW",
        "includeThoughts": False,
    }


def test_roles_merge_into_alternating_contents() -> None:
    request = Request(
        model=MODEL,
        messages=[
            Message.text("user", "First."),
            Message.text("user", "Second."),
            Message(role="assistant", content=[ToolCallPart(id="t1", name="get_time")]),
            Message(
                role="tool", content=[ToolResultPart(call_id="t1", content=[TextPart(text="9")])]
            ),
            Message.text("user", "And now?"),
            Message.text("system", "Late rule."),
        ],
    )
    body, report = _encode(request)
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["contents"][0]["parts"] == [{"text": "First."}, {"text": "Second."}]
    assert [next(iter(p)) for p in body["contents"][2]["parts"]] == ["functionResponse", "text"]
    assert body["systemInstruction"] == {"parts": [{"text": "Late rule."}]}
    assert [(loss.field, loss.action) for loss in report.losses] == [
        ("messages[5]", "approximated")
    ]


def test_decode_request_accepts_snake_case_and_keeps_unmodeled_fields() -> None:
    inbound = {
        "system_instruction": {"parts": [{"text": "Be brief."}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "Describe."},
                    {"inline_data": {"mime_type": "image/png", "data": "iVBORw0KGgo="}},
                ],
            }
        ],
        "tools": [
            {
                "function_declarations": [
                    {
                        "name": "lookup",
                        "description": "Look up.",
                        "parameters": {"type": "OBJECT", "properties": {"q": {"type": "STRING"}}},
                    }
                ]
            },
            {"code_execution": {}},
        ],
        "tool_config": {
            "function_calling_config": {"mode": "ANY", "allowed_function_names": ["lookup", "x"]}
        },
        "safety_settings": [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}],
        "generation_config": {"max_output_tokens": 100, "temperature": 0.2, "candidate_count": 1},
    }
    path = "/v1beta/models/test-model:streamGenerateContent"
    decoded = GenerateContentCodec().decode_request(inbound, path=path)
    assert (decoded.model, decoded.stream, decoded.system_text()) == (MODEL, True, "Be brief.")
    user = decoded.messages[0].content
    assert isinstance(user[1], ImagePart) and user[1].media_type == "image/png"
    (tool,) = decoded.tools
    assert tool.parameters == {"type": "object", "properties": {"q": {"type": "string"}}}
    assert decoded.tool_choice == "required"
    assert (decoded.max_output_tokens, decoded.sampling.temperature) == (100, 0.2)
    body, report = _encode(decoded)
    assert report.lossless
    assert body["safetySettings"] == inbound["safety_settings"]
    assert body["tools"][1:] == [{"codeExecution": {}}]
    assert body["toolConfig"] == {
        "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["lookup", "x"]}
    }
    assert body["generationConfig"] == {
        "temperature": 0.2,
        "maxOutputTokens": 100,
        "candidateCount": 1,
    }
    named = GenerateContentCodec().decode_request(
        {"toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["f"]}}}
    )
    assert named.tool_choice == NamedToolChoice(name="f")


def test_finish_reasons_and_blocked_prompts() -> None:
    codec = GenerateContentCodec()
    finishes = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
        "PROHIBITED_CONTENT": "content_filter",
        "BLOCKLIST": "content_filter",
        "SPII": "content_filter",
        "MALFORMED_FUNCTION_CALL": "other",
        "OTHER": "other",
    }
    for raw, finish in finishes.items():
        response = codec.decode_response(_reply([{"text": "x"}], finish=raw))
        assert (response.finish_reason, response.raw_finish_reason) == (finish, raw)
    blocked = {
        "promptFeedback": {"blockReason": "SAFETY"},
        "usageMetadata": {"promptTokenCount": 8, "totalTokenCount": 8},
        "modelVersion": MODEL,
        "responseId": "rsp-blocked",
    }
    response = codec.decode_response(blocked)
    assert (response.finish_reason, response.raw_finish_reason) == ("content_filter", "SAFETY")
    assert response.message.content == []


def test_fields_outside_the_ir_are_reported() -> None:
    codec = GenerateContentCodec()
    reply = _reply([{"text": "Clip.", "videoMetadata": {"startOffset": "1s"}}])
    reply["candidates"][0]["finishMessage"] = "done"
    reply["candidates"][0]["citationMetadata"] = {"citationSources": []}
    report = ConversionReport()
    response = codec.decode_response(reply, report=report)
    assert response.message.text_content() == "Clip."
    assert [loss.field for loss in report.losses] == [
        "candidates[0]",
        "candidates[0].content.parts[0]",
    ]
    assert "citationMetadata" in report.losses[0].detail
    inbound = {
        "contents": [
            {"role": "model", "parts": [{"functionCall": {"name": "lookup", "args": {}}}]},
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": "lookup",
                            "response": {"result": "ok"},
                            "willContinue": False,
                        }
                    },
                    {"executableCode": {"language": "PYTHON", "code": "print(1)"}},
                ],
            },
        ]
    }
    report = ConversionReport()
    decoded = codec.decode_request(inbound, report=report)
    assert [m.role for m in decoded.messages] == ["assistant", "tool"]
    result = decoded.messages[1].content[0]
    assert isinstance(result, ToolResultPart) and result.text() == "ok"
    assert [(loss.field, loss.detail) for loss in report.losses] == [
        ("contents[1].parts[0]", "fields ['willContinue'] are not carried"),
        ("contents[1].parts[1]", "fields ['executableCode'] are not carried"),
    ]


def test_structured_output_fields() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    body, report = _encode(
        text_request().model_copy(update={"response_format": ResponseFormat(type="json_object")})
    )
    assert body["generationConfig"]["responseMimeType"] == "application/json" and report.lossless
    assert "responseJsonSchema" not in body["generationConfig"]
    codec = GenerateContentCodec()
    variants = [
        {"responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": schema}}},
        {"responseMimeType": "application/json", "responseSchema": schema},
        {"responseMimeType": "application/json", "responseJsonSchema": schema},
    ]
    for config in variants:
        decoded = codec.decode_request({"contents": [], "generationConfig": config})
        assert decoded.response_format == ResponseFormat(type="json_schema", json_schema=schema)
    decoded = codec.decode_request(
        {"contents": [], "generationConfig": {"responseMimeType": "application/json"}}
    )
    assert decoded.response_format == ResponseFormat(type="json_object")


def test_unsupported_settings_are_reported() -> None:
    sampling = text_request().sampling.model_copy(
        update={"stop": ["a", "b", "c", "d", "e", "f"], "seed": 3}
    )
    request = tool_request().model_copy(update={"sampling": sampling, "parallel_tool_calls": False})
    body, report = _encode(request)
    assert body["generationConfig"]["stopSequences"] == ["a", "b", "c", "d", "e"]
    assert body["generationConfig"]["seed"] == 3
    assert [(loss.field, loss.action) for loss in report.losses] == [
        ("parallel_tool_calls", "dropped"),
        ("sampling.stop", "approximated"),
    ]


def test_streams_without_a_finish_and_server_errors() -> None:
    codec = GenerateContentCodec()
    cut = [StreamEvent(data=json.dumps(_chunk([{"text": "Hel"}])))]
    deltas: list[Delta] = list(codec.decode_stream(cut))
    assert isinstance(deltas[-1], ErrorDelta) and deltas[-1].kind == "invalid_response"
    status, body = codec.encode_error(ModelError("rate_limit", "slow down", retry_after=7.0))
    assert status == 429
    assert body == {
        "error": {
            "code": 429,
            "message": "slow down",
            "status": "RESOURCE_EXHAUSTED",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}],
        }
    }
    decoded = codec.decode_error(status, body)
    assert (decoded.kind, decoded.retry_after) == ("rate_limit", 7.0)
    assert codec.encode_error(ModelError("overloaded", "busy"))[0] == 503
    events = list(codec.encode_stream([TextDelta(text="partial")]))
    assert json.loads(events[-1].data)["error"]["status"] == "INTERNAL"
