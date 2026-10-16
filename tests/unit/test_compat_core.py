"""Unit tests for the codec-independent parts of the compatibility layer."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from stepswitch import net
from stepswitch.compat import templates
from stepswitch.compat.client import Endpoint, ModelClient, join_url
from stepswitch.compat.errors import ModelError, backoff_delay, kind_for_status, parse_retry_after
from stepswitch.compat.framing import (
    StreamEvent,
    format_ndjson,
    format_sse,
    iter_lines,
    parse_ndjson,
    parse_sse,
)
from stepswitch.compat.ir import (
    FinishDelta,
    Message,
    ReasoningDelta,
    Request,
    Response,
    TextDelta,
    TextPart,
    ToolCallArgsDelta,
    ToolCallEndDelta,
    ToolCallPart,
    ToolCallStartDelta,
    Usage,
    UsageDelta,
    synthetic_call_id,
)
from stepswitch.compat.stream import assemble, merge_usage, response_to_deltas
from stepswitch.compat.toolcall import parsers
from stepswitch.compat.toolcall.streaming import StreamingToolCallExtractor, extract_calls
from stepswitch.config import EndpointConfig
from tests.conformance.harness import MockServer, conversation_request, json_response, tool_request

# --------------------------------------------------------------------------------------------
# Tool-call parsers
# --------------------------------------------------------------------------------------------


def test_hermes_parser_extracts_calls_and_keeps_other_text() -> None:
    text = (
        'Checking.\n<tool_call>\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n'
        '</tool_call>\n<tool_call>{"name": "get_time", "arguments": "{\\"city\\": \\"Tokyo\\"}"}'
        "</tool_call>"
    )
    result = parsers.parse_hermes(text)
    assert [c.name for c in result.calls] == ["get_weather", "get_time"]
    assert [json.loads(c.arguments) for c in result.calls] == [{"city": "Paris"}, {"city": "Tokyo"}]
    assert result.text == "Checking."


def test_xml_function_parser_coerces_values() -> None:
    text = (
        "<tool_call>\n<function=search>\n<parameter=query>\nflaky test\n</parameter>\n"
        "<parameter=limit>\n5\n</parameter>\n</function>\n</tool_call>"
    )
    result = parsers.parse_xml_function(text)
    assert len(result.calls) == 1
    assert result.calls[0].name == "search"
    assert json.loads(result.calls[0].arguments) == {"query": "flaky test", "limit": 5}


def test_bracketed_parser_handles_several_calls() -> None:
    text = '[TOOL_CALLS]get_weather[ARGS]{"city": "Paris"}[TOOL_CALLS]get_time[ARGS]{"city": "X"}'
    result = parsers.parse_bracketed(text)
    assert [c.name for c in result.calls] == ["get_weather", "get_time"]
    assert result.text == ""


def test_detect_and_auto_parse() -> None:
    assert parsers.detect("{% if tools %}<tool_call>{{ x }}</tool_call>{% endif %}") == "hermes"
    assert parsers.detect("<function=name>") == "xml_function"
    assert parsers.detect("plain template") is None
    result = parsers.parse('<tool_call>{"name": "f", "arguments": {}}</tool_call>', None)
    assert [c.name for c in result.calls] == ["f"]
    assert parsers.parse("no calls here", None).calls == []


def test_extract_calls_uses_synthetic_ids_per_turn() -> None:
    text, calls = extract_calls(
        '<tool_call>{"name": "a", "arguments": {}}</tool_call>'
        '<tool_call>{"name": "b", "arguments": {}}</tool_call>',
        "hermes",
        turn=3,
    )
    assert text == ""
    assert [c.id for c in calls] == [synthetic_call_id(3, 0), synthetic_call_id(3, 1)]
    assert all(c.synthetic for c in calls)


@pytest.mark.parametrize("split", [1, 2, 3, 5, 7, 11])
def test_streaming_extractor_holds_back_partial_markers(split: int) -> None:
    text = 'Sure. <tool_call>{"name": "f", "arguments": {"a": 1}}</tool_call>'
    extractor = StreamingToolCallExtractor("hermes")
    deltas = []
    for start in range(0, len(text), split):
        deltas += extractor.feed(text[start : start + split])
    deltas += extractor.finish()
    shown = "".join(d.text for d in deltas if isinstance(d, TextDelta))
    assert shown == "Sure. "
    starts = [d for d in deltas if isinstance(d, ToolCallStartDelta)]
    args = "".join(d.delta for d in deltas if isinstance(d, ToolCallArgsDelta))
    assert [d.name for d in starts] == ["f"]
    assert json.loads(args) == {"a": 1}
    assert any(isinstance(d, ToolCallEndDelta) for d in deltas)


def test_streaming_extractor_passes_text_through_without_markers() -> None:
    extractor = StreamingToolCallExtractor()
    out = extractor.feed("a <tool") + extractor.feed(" box") + extractor.finish()
    assert "".join(d.text for d in out if isinstance(d, TextDelta)) == "a <tool box"


def test_streaming_extractor_returns_unparsable_capture_as_text() -> None:
    extractor = StreamingToolCallExtractor("hermes")
    out = extractor.feed("x <tool_call>{not json</tool_call>") + extractor.finish()
    assert "".join(d.text for d in out if isinstance(d, TextDelta)) == (
        "x <tool_call>{not json</tool_call>"
    )


# --------------------------------------------------------------------------------------------
# Prompt rendering
# --------------------------------------------------------------------------------------------


def test_chatml_render_contains_every_turn_and_tool() -> None:
    request = conversation_request()
    prompt = templates.render_chatml(request)
    assert prompt.startswith("<|im_start|>system\n")
    assert prompt.endswith("<|im_start|>assistant\n")
    for needle in ("Use tools when needed.", "get_weather", "get_time", "18 C, sunny", "21:04"):
        assert needle in prompt
    assert prompt.count("<|im_start|>user") >= 2
    assert templates.prompt_for(request.model_copy(update={"prompt": "raw"})) == "raw"


def test_chatml_render_without_generation_prompt() -> None:
    prompt = templates.render_chatml(tool_request(), add_generation_prompt=False)
    assert not prompt.endswith("<|im_start|>assistant\n")


# --------------------------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------------------------


def test_sse_parse_follows_the_field_rules() -> None:
    raw = ': comment\nevent: delta\ndata: {"a":1}\n\ndata: line1\ndata:line2\n\ndata: [DONE]\n'
    events = list(parse_sse(iter_lines([raw[:7], raw[7:30], raw[30:]])))
    assert events == [
        StreamEvent(data='{"a":1}', event="delta"),
        StreamEvent(data="line1\nline2"),
        StreamEvent(data="[DONE]"),
    ]


def test_sse_and_ndjson_round_trip() -> None:
    events = [StreamEvent(data='{"x":1}', event="e"), StreamEvent(data="a\nb")]
    text = "".join(format_sse(e) for e in events)
    assert list(parse_sse(iter_lines([text]))) == events
    lines = "".join(format_ndjson(StreamEvent(data=json.dumps({"i": i}))) for i in range(3))
    assert [e.json() for e in parse_ndjson(iter_lines([lines]))] == [{"i": i} for i in range(3)]


def test_iter_lines_handles_crlf_and_split_chunks() -> None:
    assert list(iter_lines(["a\r", "\nb\n", "\nc"])) == ["a", "b", "", "c"]


# --------------------------------------------------------------------------------------------
# Errors and retries
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (400, "bad_request", False),
        (401, "authentication", False),
        (404, "not_found", False),
        (413, "too_large", False),
        (429, "rate_limit", True),
        (500, "server", True),
        (501, "unsupported", False),
        (503, "overloaded", True),
        (529, "overloaded", True),
        (599, "server", True),
    ],
)
def test_status_kinds(status: int, kind: str, retryable: bool) -> None:
    assert kind_for_status(status) == kind
    assert ModelError(kind_for_status(status), "x").retryable is retryable


def test_quota_errors_are_not_retried() -> None:
    assert not ModelError("quota", "no quota left").retryable


def test_retry_after_seconds_and_http_dates() -> None:
    now = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after("-3") == 0.0
    assert parse_retry_after("Fri, 16 Oct 2026 12:00:30 GMT", now=now) == 30.0
    assert parse_retry_after("Fri, 16 Oct 2026 11:00:00 GMT", now=now) == 0.0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


def test_backoff_grows_and_honours_retry_after() -> None:
    assert [backoff_delay(a) for a in range(4)] == [0.5, 1.0, 2.0, 4.0]
    assert backoff_delay(10) == 20.0
    assert backoff_delay(0, retry_after=3.0) == 3.0
    assert backoff_delay(0, retry_after=600.0) == 60.0


# --------------------------------------------------------------------------------------------
# Streams
# --------------------------------------------------------------------------------------------


def test_assemble_builds_text_reasoning_calls_and_usage() -> None:
    deltas = [
        ReasoningDelta(text="think "),
        ReasoningDelta(text="more"),
        TextDelta(text="Hel"),
        TextDelta(text="lo"),
        ToolCallStartDelta(index=0, id="c1", name="f"),
        ToolCallArgsDelta(index=0, delta='{"a"'),
        ToolCallArgsDelta(index=0, delta=": 1}"),
        ToolCallEndDelta(index=0),
        UsageDelta(usage=Usage(input_tokens=10, output_tokens=2)),
        UsageDelta(usage=Usage(input_tokens=10, output_tokens=5, cached_input_tokens=4)),
        FinishDelta(reason="tool_calls", raw="tool_calls"),
    ]
    response = assemble(deltas)
    assert response.message.text_content() == "Hello"
    assert response.message.reasoning()[0].text == "think more"
    assert [(c.name, json.loads(c.arguments)) for c in response.message.tool_calls()] == [
        ("f", {"a": 1})
    ]
    assert response.finish_reason == "tool_calls"
    assert response.usage is not None
    assert (response.usage.output_tokens, response.usage.cached_input_tokens) == (5, 4)


def test_merge_usage_keeps_known_values() -> None:
    merged = merge_usage(Usage(input_tokens=10, cached_input_tokens=3), Usage(output_tokens=4))
    assert (merged.input_tokens, merged.output_tokens, merged.cached_input_tokens) == (10, 4, 3)


def test_response_to_deltas_round_trips() -> None:
    response = Response(
        id="r",
        model="m",
        message=Message(
            role="assistant",
            content=[
                TextPart(text="A longer answer."),
                ToolCallPart(id="c", name="f", arguments='{"x": [1, 2]}'),
            ],
        ),
        finish_reason="tool_calls",
        usage=Usage(input_tokens=3, output_tokens=4),
    )
    again = assemble(response_to_deltas(response, chunk=3))
    assert again.message.text_content() == "A longer answer."
    assert again.message.tool_calls()[0].arguments == '{"x": [1, 2]}'
    assert again.finish_reason == "tool_calls"
    assert again.usage is not None and again.usage.output_tokens == 4


# --------------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "path", "url"),
    [
        ("http://h:1", "/v1/chat/completions", "http://h:1/v1/chat/completions"),
        ("http://h:1/v1", "/v1/chat/completions", "http://h:1/v1/chat/completions"),
        ("http://h:1/v1/", "/v1/responses", "http://h:1/v1/responses"),
        (
            "http://h:1/v1beta",
            "/v1beta/models/m:generateContent",
            "http://h:1/v1beta/models/m:generateContent",
        ),
        ("http://h:1/proxy", "/api/chat", "http://h:1/proxy/api/chat"),
    ],
)
def test_join_url(base: str, path: str, url: str) -> None:
    assert join_url(base, path) == url


def test_endpoint_from_config_sets_auth_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSW_TEST_KEY", "from-env")
    bearer = EndpointConfig(
        name="r", url="https://example.invalid", auth="bearer", api_key_env="SSW_TEST_KEY"
    )
    endpoint = Endpoint.from_config(bearer, model="m")
    assert endpoint.headers == {"Authorization": "Bearer from-env"}
    assert endpoint.local is False and endpoint.model == "m"
    header = EndpointConfig(name="h", url="http://127.0.0.1:1", auth="header", api_key="k")
    assert Endpoint.from_config(header).headers == {"x-api-key": "k"}
    ollama = EndpointConfig(name="o", url="http://127.0.0.1:11434", engine="ollama", models=["q"])
    resolved = Endpoint.from_config(ollama)
    assert (resolved.format, resolved.model, resolved.local) == ("ollama-native", "q", True)


def _chat_body(text: str) -> dict[str, object]:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }


def test_client_retries_transient_errors_then_succeeds() -> None:
    calls = {"n": 0}

    def responder(_recorded: object) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return json_response({"error": {"message": "busy"}}, 503, {"retry-after": "1"})
        return json_response(_chat_body("ok"))

    server = MockServer().route("POST", r"/v1/chat/completions", responder)
    sleeps: list[float] = []
    client = ModelClient(
        Endpoint(name="m", base_url="http://127.0.0.1:1", model="m"),
        transport=server.transport(),
        max_retries=2,
        sleep=sleeps.append,
    )
    completion = client.complete(Request(model="m", messages=[Message.text("user", "hi")]))
    assert completion.response.message.text_content() == "ok"
    assert completion.attempts == 3
    assert sleeps == [1.0, 1.0]


def test_client_does_not_retry_client_errors() -> None:
    server = MockServer().route(
        "POST", r"/v1/chat/completions", lambda _r: json_response({"error": "bad"}, 400)
    )
    client = ModelClient(
        Endpoint(name="m", base_url="http://127.0.0.1:1", model="m"),
        transport=server.transport(),
        max_retries=3,
        sleep=lambda _s: None,
    )
    with pytest.raises(ModelError) as caught:
        client.complete(Request(model="m", messages=[Message.text("user", "hi")]))
    assert caught.value.kind == "bad_request"
    assert len(server.requests) == 1


def test_client_offline_mode_blocks_remote_endpoints() -> None:
    egress = net.Egress(net.EgressPolicy(offline=True))
    server = MockServer().route(
        "POST", r"/v1/chat/completions", lambda _r: json_response(_chat_body("never"))
    )
    client = ModelClient(
        Endpoint(name="remote", base_url="https://example.invalid", model="m", local=False),
        egress=egress,
        transport=server.transport(),
        max_retries=0,
    )
    with pytest.raises(ModelError) as caught:
        client.complete(Request(model="m", messages=[Message.text("user", "hi")]))
    assert caught.value.kind == "blocked"
    assert server.requests == []
    rows = list(egress.memory)
    assert len(rows) == 1 and rows[0].allowed is False and rows[0].host == "example.invalid"
