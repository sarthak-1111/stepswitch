"""Conformance suite for the llama.cpp native format, `POST /completion` (claim C-004).

Mock bodies and SSE events are written by hand in the wire shapes of the llama.cpp server at
v0.7.0: its `tools/server/README.md` and the result builders in `tools/server/server-task.cpp`
(docs/RESEARCH.md, "llama.cpp native API"); the codec under test never produces them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

import httpx
import pytest

from stepswitch.compat.codecs import get_codec
from stepswitch.compat.codecs.base import CodecOptions
from stepswitch.compat.codecs.llamacpp import LlamaCppCodec, split_think
from stepswitch.compat.errors import ModelError
from stepswitch.compat.framing import StreamEvent, format_sse, iter_lines, parse_sse
from stepswitch.compat.ir import (
    ConversionError,
    ConversionReport,
    ErrorDelta,
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

FORMAT = "llamacpp-native"
COMPLETION = r"/completion"

# The ChatML render of text_request(), as the server receives it.
TEXT_PROMPT = (
    "<|im_start|>system\nYou are terse.<|im_end|>\n"
    "<|im_start|>user\nSay hello.<|im_end|>\n"
    "<|im_start|>assistant\n"
)
TIMINGS = {
    "cache_n": 0,
    "prompt_n": 26,
    "prompt_ms": 31.204,
    "prompt_per_token_ms": 1.2,
    "prompt_per_second": 833.23,
    "predicted_n": 4,
    "predicted_ms": 52.113,
    "predicted_per_token_ms": 13.028,
    "predicted_per_second": 76.76,
}
SETTINGS = {"n_predict": 64, "seed": 4294967295, "temperature": 0.8, "top_k": 40, "top_p": 0.95}


def result(content: str, **fields: Any) -> dict[str, Any]:
    """A final `/completion` result (also the last streamed event, with empty content)."""
    return {
        "index": 0,
        "content": content,
        "tokens": [],
        "id_slot": 0,
        "stop": True,
        "model": "test-model",
        "tokens_predicted": 4,
        "tokens_evaluated": 26,
        "generation_settings": SETTINGS,
        "prompt": TEXT_PROMPT,
        "has_new_line": False,
        "truncated": False,
        "stop_type": "eos",
        "stopping_word": "",
        "tokens_cached": 29,
        "timings": TIMINGS,
        **fields,
    }


def partial(content: str, token: int, predicted: int) -> dict[str, Any]:
    """A streamed partial result: one token of text, `stop: false`, no model or timings."""
    return {
        "index": 0,
        "content": content,
        "tokens": [token],
        "stop": False,
        "id_slot": 0,
        "tokens_predicted": predicted,
        "tokens_evaluated": 26,
    }


def pieces(*texts: str) -> list[dict[str, Any]]:
    return [partial(text, 1000 + n, n + 1) for n, text in enumerate(texts)]


def serve_json(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> MockServer:
    return MockServer().route("POST", COMPLETION, lambda _r: json_response(body, status, headers))


def serve_sse(events: list[dict[str, Any]]) -> MockServer:
    return MockServer().route("POST", COMPLETION, lambda _r: sse_response(events))


def raises(action: Callable[[], object]) -> ModelError:
    with pytest.raises(ModelError) as info:
        action()
    return info.value


def call_view(response: Response) -> list[tuple[str, dict[str, Any]]]:
    return [(call.name, json.loads(call.arguments)) for call in response.message.tool_calls()]


def wire_round_trip(events: Iterable[StreamEvent]) -> list[StreamEvent]:
    """Serialize events as SSE text and parse them back, as a client would."""
    text = "".join(format_sse(event) for event in events)
    return list(parse_sse(iter_lines([text])))


WEATHER_CALL = '<tool_call>\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n</tool_call>'
TIME_CALL = '<tool_call>\n{"name": "get_time", "arguments": {"city": "Tokyo"}}\n</tool_call>'

CONTEXT_ERROR = (
    "request (5000 tokens) exceeds the available context size (4096 tokens), try increasing it"
)

# --------------------------------------------------------------------------------------------
# Required cases
# --------------------------------------------------------------------------------------------


def case_non_streaming() -> None:
    server = serve_json(result("Hello there.", stop_type="word", stopping_word="<|im_end|>"))
    completion = client_for(FORMAT, server).complete(text_request())
    sent = server.last
    assert (sent.method, sent.path) == ("POST", "/completion")
    assert sent.body == {
        "prompt": TEXT_PROMPT,
        "model": "test-model",
        "n_predict": 64,
        "stop": ["<|im_end|>", "<|im_start|>"],
        "cache_prompt": True,
        "stream": False,
    }
    response = completion.response
    assert response.message.text_content() == ANSWER
    assert (response.finish_reason, response.raw_finish_reason) == ("stop", "word")
    assert response.model == "test-model"
    assert completion.report.lossless


def case_streaming() -> None:
    events = [*pieces("Hello", " there", "."), result("")]
    server = serve_sse(events)
    deltas = list(client_for(FORMAT, server).stream(text_request()))
    assert server.last.body["stream"] is True
    assert isinstance(deltas[0], StartDelta) and deltas[0].model == "test-model"
    assert "".join(d.text for d in deltas if isinstance(d, TextDelta)) == ANSWER
    assert len([d for d in deltas if isinstance(d, TextDelta)]) > 1
    assert isinstance(deltas[-1], FinishDelta)
    response = assemble(deltas)
    assert response.message.text_content() == ANSWER
    assert (response.finish_reason, response.raw_finish_reason) == ("stop", "eos")

    # A think block split across events becomes reasoning; the answer follows.
    events = [
        *pieces("<think>", "\n", "Short", " answer", ".\n</th", "ink>", "\n\n", "Hello", " there."),
        result(""),
    ]
    deltas = list(client_for(FORMAT, serve_sse(events)).stream(text_request()))
    assert "".join(d.text for d in deltas if isinstance(d, ReasoningDelta)) == "Short answer."
    response = assemble(deltas)
    assert [p.text for p in response.message.reasoning()] == ["Short answer."]
    assert response.message.text_content() == ANSWER


def case_tool_call() -> None:
    server = serve_json(result(WEATHER_CALL, stop_type="word", stopping_word="<|im_end|>"))
    response = client_for(FORMAT, server).complete(tool_request()).response
    prompt = server.last.body["prompt"]
    assert (
        "<tools>" in prompt and '"name": "get_weather"' in prompt and '"name": "get_time"' in prompt
    )
    [call] = response.message.tool_calls()
    assert call.name == "get_weather" and json.loads(call.arguments) == {"city": "Paris"}
    assert call.id and call.synthetic
    assert response.message.text_content() == ""
    assert (response.finish_reason, response.raw_finish_reason) == ("tool_calls", "word")

    # The XML function syntax, when the endpoint's model uses it.
    xml = (
        "<tool_call>\n<function=get_weather>\n"
        "<parameter=city>\nParis\n</parameter>\n</function>\n</tool_call>"
    )
    options = CodecOptions(tool_parser="xml_function")
    response = (
        client_for(FORMAT, serve_json(result(xml)), options=options)
        .complete(tool_request())
        .response
    )
    assert call_view(response) == [("get_weather", {"city": "Paris"})]

    # Streaming: the call is split across events, inside the marker too.
    events = [
        *pieces(
            "<tool",
            "_call>\n",
            '{"name": "get_',
            'weather", "arguments": ',
            '{"city": "Paris"}}\n</tool_call>',
        ),
        result(""),
    ]
    deltas = list(client_for(FORMAT, serve_sse(events)).stream(tool_request()))
    [start] = [d for d in deltas if isinstance(d, ToolCallStartDelta)]
    assert start.id and start.synthetic and start.name == "get_weather"
    assert not [d for d in deltas if isinstance(d, TextDelta)]
    response = assemble(deltas)
    assert call_view(response) == [("get_weather", {"city": "Paris"})]
    assert response.finish_reason == "tool_calls"


def case_parallel_tool_calls() -> None:
    content = f"I will check both.\n{WEATHER_CALL}\n{TIME_CALL}"
    response = client_for(FORMAT, serve_json(result(content))).complete(tool_request()).response
    assert response.message.text_content() == "I will check both."
    assert call_view(response) == [
        ("get_weather", {"city": "Paris"}),
        ("get_time", {"city": "Tokyo"}),
    ]
    ids = [call.id for call in response.message.tool_calls()]
    assert len(set(ids)) == 2 and all(ids)
    assert response.finish_reason == "tool_calls"

    events = [
        *pieces(
            "I will",
            " check both.",
            "\n<tool_call>\n",
            WEATHER_CALL[12:30],
            WEATHER_CALL[30:],
            "\n",
            TIME_CALL,
        ),
        result(""),
    ]
    response = assemble(client_for(FORMAT, serve_sse(events)).stream(tool_request()))
    assert response.message.text_content().strip() == "I will check both."
    assert call_view(response) == [
        ("get_weather", {"city": "Paris"}),
        ("get_time", {"city": "Tokyo"}),
    ]
    ids = [call.id for call in response.message.tool_calls()]
    assert len(set(ids)) == 2 and all(ids)
    assert response.finish_reason == "tool_calls"


def case_error() -> None:
    cases: list[tuple[int, Any, dict[str, str], str, str, float | None]] = [
        (
            400,
            {
                "error": {
                    "code": 400,
                    "message": "Failed to parse grammar",
                    "type": "invalid_request_error",
                }
            },
            {},
            "bad_request",
            "Failed to parse grammar",
            None,
        ),
        (
            401,
            {"error": {"code": 401, "message": "Invalid API Key", "type": "authentication_error"}},
            {},
            "authentication",
            "Invalid API Key",
            None,
        ),
        # The server itself never answers 429; a front proxy limiting concurrency does, in text.
        (429, "Too many requests\n", {"retry-after": "2"}, "rate_limit", "Too many requests", 2.0),
        (
            500,
            {
                "error": {
                    "code": 500,
                    "message": "Prompt contains invalid tokens",
                    "type": "server_error",
                }
            },
            {},
            "server",
            "Prompt contains invalid tokens",
            None,
        ),
        (
            503,
            {"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}},
            {"retry-after": "1"},
            "overloaded",
            "Loading model",
            1.0,
        ),
        (
            400,
            {
                "error": {
                    "code": 400,
                    "message": CONTEXT_ERROR,
                    "type": "exceed_context_size_error",
                    "n_prompt_tokens": 5000,
                    "n_ctx": 4096,
                }
            },
            {},
            "too_large",
            CONTEXT_ERROR,
            None,
        ),
    ]
    for status, body, headers, kind, message, retry_after in cases:
        if isinstance(body, str):
            server = MockServer().route(
                "POST",
                COMPLETION,
                lambda _r, b=body, s=status, h=headers: httpx.Response(s, text=b, headers=h),
            )
        else:
            server = serve_json(body, status, headers)
        error = raises(lambda s=server: client_for(FORMAT, s).complete(text_request()))
        assert (error.kind, error.status, error.message) == (kind, status, message)
        assert error.retry_after == retry_after
        if isinstance(body, dict):
            assert error.code == body["error"]["type"]
    # A mid-stream error is a data event with the same error object.
    events: list[dict[str, Any]] = [
        *pieces("Hel"),
        {"error": {"code": 500, "message": "decode failed", "type": "server_error"}},
    ]
    deltas = list(client_for(FORMAT, serve_sse(events)).stream(text_request()))
    errors = [d for d in deltas if isinstance(d, ErrorDelta)]
    assert [(e.kind, e.message, e.status) for e in errors] == [("server", "decode failed", 500)]
    assert raises(lambda: assemble(deltas)).kind == "server"


def case_usage() -> None:
    # 100 of the 120 prompt tokens came from the slot's cache; tokens_cached is the cache size
    # after the request (prompt plus generated), not the cached input.
    timings = dict(TIMINGS, cache_n=100, prompt_n=20, predicted_n=7)
    body = result(
        "Hello there.", tokens_evaluated=120, tokens_predicted=7, tokens_cached=126, timings=timings
    )
    usage = client_for(FORMAT, serve_json(body)).complete(text_request()).response.usage
    assert usage is not None
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (120, 100, 7)
    assert usage.raw is not None and usage.raw["tokens_cached"] == 126
    assert usage.raw["timings"]["prompt_ms"] == 31.204
    # Streaming: the counts arrive with the final event.
    events = [*pieces("Hello", " there."), dict(body, content="")]
    deltas = list(client_for(FORMAT, serve_sse(events)).stream(text_request()))
    [usage_delta] = [d for d in deltas if isinstance(d, UsageDelta)]
    usage = usage_delta.usage
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (120, 100, 7)
    # With `response_fields` trimming the body, the prompt size comes from the timings.
    trimmed = {"content": "Hello there.", "stop_type": "eos", "timings": timings}
    usage = client_for(FORMAT, serve_json(trimmed)).complete(text_request()).response.usage
    assert usage is not None
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (120, 100, 7)


def case_server_round_trip() -> None:
    codec = LlamaCppCodec()
    for request in (text_request(), tool_request(), conversation_request()):
        report = ConversionReport(target=FORMAT)
        call = codec.encode_request(request, report)
        assert report.lossless, report.losses
        assert call.json is not None
        prompt = call.json["prompt"]
        # The prompt flattens the conversation: every text, call and result must be in it.
        for text in [
            *(p.text for p in request.system),
            *(m.text_content() for m in request.messages),
        ]:
            assert text in prompt
        for message in request.messages:
            for tool_call in message.tool_calls():
                rendered = {"name": tool_call.name, "arguments": tool_call.parsed_arguments()}
                assert f"<tool_call>\n{json.dumps(rendered)}\n</tool_call>" in prompt
            for tool_result in message.tool_results():
                assert f"<tool_response>\n{tool_result.text()}\n</tool_response>" in prompt
        for tool in request.tools:
            assert f'"name": "{tool.name}"' in prompt and (tool.description or "") in prompt
        assert prompt.endswith("<|im_start|>assistant\n")
        assert call.json["n_predict"] == request.max_output_tokens
    call = codec.encode_request(conversation_request(), ConversionReport())
    assert call.json is not None
    assert call.json["json_schema"] == {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
    }

    # A raw prompt body, as a client of the native endpoint sends it.
    body = {
        "prompt": "<|im_start|>user\nSay hello.<|im_end|>\n<|im_start|>assistant\n",
        "n_predict": 64,
        "temperature": 0.2,
        "top_k": 40,
        "top_p": 0.95,
        "seed": 42,
        "stop": ["<|im_end|>"],
        "stream": True,
        "cache_prompt": True,
        "grammar": 'root ::= "Hello" | "Hi"',
        "id_slot": 1,
        "min_p": 0.05,
        "n_probs": 3,
    }
    request = codec.decode_request(body, path="/completion")
    assert request.prompt == body["prompt"]
    assert request.messages == [Message.text("user", body["prompt"])]
    assert normalize(request)["messages"] == [{"role": "user", "parts": [("text", body["prompt"])]}]
    assert request.sampling == Sampling(
        temperature=0.2, top_k=40, top_p=0.95, seed=42, stop=["<|im_end|>"]
    )
    assert (request.max_output_tokens, request.stream) == (64, True)
    assert request.extensions == {
        "grammar": 'root ::= "Hello" | "Hi"',
        "id_slot": 1,
        "params": {"min_p": 0.05, "n_probs": 3},
    }
    # Encoding the decoded request reproduces the body (the prompt is not rendered again).
    again = codec.encode_request(request, report := ConversionReport())
    assert again.json == body and report.lossless


def ir_response() -> Response:
    return Response(
        model="test-model",
        message=Message(
            role="assistant",
            content=[
                ReasoningPart(
                    text="Two lookups are needed.", origin=Origin(format=FORMAT, model="test-model")
                ),
                TextPart(text="Checking both cities."),
                ToolCallPart(id="call_a", name="get_weather", arguments='{"city":"Paris"}'),
                ToolCallPart(id="call_b", name="get_time", arguments='{"city":"Tokyo"}'),
            ],
        ),
        finish_reason="tool_calls",
        raw_finish_reason="eos",
        usage=Usage(input_tokens=120, output_tokens=47, cached_input_tokens=100),
    )


def case_response_round_trip() -> None:
    original = ir_response()
    body = LlamaCppCodec().encode_response(original)
    assert body["content"] == (
        "<think>\nTwo lookups are needed.\n</think>\n\nChecking both cities.\n"
        f"{WEATHER_CALL}\n{TIME_CALL}"
    )
    assert (
        body["stop"],
        body["stop_type"],
        body["tokens_evaluated"],
        body["tokens_predicted"],
    ) == (
        True,
        "eos",
        120,
        47,
    )
    assert body["timings"] == {"cache_n": 100, "prompt_n": 20, "predicted_n": 47}
    decoded = LlamaCppCodec().decode_response(json.loads(json.dumps(body)))
    content = decoded.message.content
    assert isinstance(content[0], ReasoningPart) and content[0].text == "Two lookups are needed."
    assert content[0].origin == Origin(format=FORMAT, model="test-model")
    assert decoded.message.text_content() == "Checking both cities."
    assert call_view(decoded) == [
        ("get_weather", {"city": "Paris"}),
        ("get_time", {"city": "Tokyo"}),
    ]
    assert all(call.synthetic for call in decoded.message.tool_calls())  # the text carries no IDs
    assert (decoded.model, decoded.finish_reason, decoded.raw_finish_reason) == (
        "test-model",
        "tool_calls",
        "eos",
    )
    assert decoded.usage is not None
    assert (
        decoded.usage.input_tokens,
        decoded.usage.cached_input_tokens,
        decoded.usage.output_tokens,
    ) == (
        120,
        100,
        47,
    )


def case_stream_round_trip() -> None:
    codec = LlamaCppCodec()
    original = ir_response()
    events = wire_round_trip(codec.encode_stream(response_to_deltas(original, chunk=3)))
    chunks = [json.loads(event.data) for event in events]
    assert [chunk["stop"] for chunk in chunks] == [False] * (len(chunks) - 1) + [True]
    assert chunks[-1]["content"] == ""  # the text was streamed already
    rebuilt = assemble(LlamaCppCodec().decode_stream(events))
    assert [p.text for p in rebuilt.message.reasoning()] == ["Two lookups are needed."]
    assert rebuilt.message.text_content() == "Checking both cities."
    assert call_view(rebuilt) == [
        ("get_weather", {"city": "Paris"}),
        ("get_time", {"city": "Tokyo"}),
    ]
    assert (rebuilt.model, rebuilt.finish_reason, rebuilt.raw_finish_reason) == (
        "test-model",
        "tool_calls",
        "eos",
    )
    assert rebuilt.usage is not None
    assert (
        rebuilt.usage.input_tokens,
        rebuilt.usage.cached_input_tokens,
        rebuilt.usage.output_tokens,
    ) == (
        120,
        100,
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
    assert isinstance(codec, LlamaCppCodec)
    assert (codec.key, codec.format, codec.framing) == ("llamacpp", FORMAT, "sse")


# --------------------------------------------------------------------------------------------
# Helper endpoints
# --------------------------------------------------------------------------------------------


def test_tokenize_and_detokenize_calls() -> None:
    codec = LlamaCppCodec()
    call = codec.tokenize_call("Hello world")
    assert (call.method, call.path, call.json) == (
        "POST",
        "/tokenize",
        {"content": "Hello world", "add_special": False},
    )
    assert codec.tokenize_call("Hi", add_special=True).json == {
        "content": "Hi",
        "add_special": True,
    }
    assert codec.parse_tokens({"tokens": [9707, 1879]}) == [9707, 1879]
    with_pieces = {"tokens": [{"id": 9707, "piece": "Hello"}, {"id": 1879, "piece": " world"}]}
    assert codec.parse_tokens(with_pieces) == [9707, 1879]
    with pytest.raises(ModelError):
        codec.parse_tokens({"error": {"code": 500, "message": "x", "type": "server_error"}})
    call = codec.detokenize_call([9707, 1879])
    assert (call.method, call.path, call.json) == ("POST", "/detokenize", {"tokens": [9707, 1879]})
    assert codec.parse_detokenize({"content": "Hello world"}) == "Hello world"


def test_apply_template_call() -> None:
    codec = LlamaCppCodec()
    call = codec.apply_template_call(conversation_request())
    assert (call.method, call.path) == ("POST", "/apply-template")
    assert call.json is not None
    assert call.json["messages"] == [
        {"role": "system", "content": "Use tools when needed."},
        {"role": "user", "content": "Weather in Paris and time in Tokyo?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                },
                {
                    "id": "call_b",
                    "type": "function",
                    "function": {"name": "get_time", "arguments": '{"city":"Tokyo"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "18 C, sunny"},
        {"role": "tool", "tool_call_id": "call_b", "content": "21:04"},
        {"role": "assistant", "content": "Paris is 18 C and sunny; it is 21:04 in Tokyo."},
        {"role": "user", "content": "Thanks. Answer in JSON next time."},
    ]
    assert [tool["function"]["name"] for tool in call.json["tools"]] == ["get_weather", "get_time"]
    assert call.json["model"] == "test-model"
    assert "reasoning_effort" not in call.json
    # The effort goes to the template, so the rendered prompt carries it.
    effort_call = codec.apply_template_call(text_request().model_copy(update={"effort": "high"}))
    assert effort_call.json is not None and effort_call.json["reasoning_effort"] == "high"
    rendered = "<|im_start|>user\nSay hello.<|im_end|>\n<|im_start|>assistant\n"
    assert codec.parse_apply_template({"prompt": rendered}) == rendered
    with pytest.raises(ModelError):
        codec.parse_apply_template({})
    # The rendered prompt then goes to /completion as is, without ChatML stop strings, and the
    # effort it was rendered with is not reported as lost.
    request = text_request().model_copy(update={"prompt": rendered, "effort": "high"})
    report = ConversionReport()
    sent = codec.encode_request(request, report).json
    assert sent is not None and sent["prompt"] == rendered and "stop" not in sent
    assert report.lossless


def test_props_health_and_slot_calls() -> None:
    codec = LlamaCppCodec()
    props = codec.props_call()
    assert (props.method, props.path, props.params) == ("GET", "/props", {})
    assert codec.props_call("qwen3-0.6b").params == {"model": "qwen3-0.6b"}
    health = codec.health_call()
    assert (health.method, health.path) == ("GET", "/health")
    save = codec.slot_call(0, "save", "slot_save_file.bin")
    assert (save.method, save.path, save.params, save.json) == (
        "POST",
        "/slots/0",
        {"action": "save"},
        {"filename": "slot_save_file.bin"},
    )
    restore = codec.slot_call(2, "restore", "slot_save_file.bin")
    assert (restore.path, restore.params) == ("/slots/2", {"action": "restore"})
    erase = codec.slot_call(1, "erase")
    assert (erase.path, erase.params, erase.json) == ("/slots/1", {"action": "erase"}, {})


@pytest.mark.parametrize(
    ("slot", "action", "filename"),
    [
        (0, "save", None),
        (0, "restore", ""),
        (-1, "erase", None),
        (0, "dump", "a.bin"),
        (0, "save", "../a.bin"),
    ],
)
def test_slot_call_rejects_bad_arguments(slot: int, action: Any, filename: str | None) -> None:
    with pytest.raises(ValueError):
        LlamaCppCodec().slot_call(slot, action, filename)


def test_helper_calls_through_a_mock_server() -> None:
    """The helper calls are valid requests: send them as `ModelClient` would."""
    codec = LlamaCppCodec()
    server = (
        MockServer()
        .route("POST", r"/tokenize", lambda _r: json_response({"tokens": [9707, 1879]}))
        .route("POST", r"/apply-template", lambda _r: json_response({"prompt": "rendered"}))
        .route("GET", r"/health", lambda _r: json_response({"status": "ok"}))
        .route(
            "POST",
            r"/slots/\d+",
            lambda r: json_response(
                {
                    "id_slot": 0,
                    "filename": r.body["filename"],
                    "n_saved": 1745,
                    "n_written": 14309796,
                    "timings": {"save_ms": 49.865},
                }
            ),
        )
    )
    with httpx.Client(transport=server.transport(), base_url="http://127.0.0.1:65530") as http:
        for call in (
            codec.tokenize_call("Hello world"),
            codec.apply_template_call(text_request()),
            codec.health_call(),
            codec.slot_call(0, "save", "slot_save_file.bin"),
        ):
            response = http.request(
                call.method, call.path, json=call.json, params=call.params or None
            )
            assert response.status_code == 200, call.path
    assert codec.parse_tokens(json.loads(b'{"tokens": [9707, 1879]}')) == [9707, 1879]
    assert [r.path for r in server.requests] == [
        "/tokenize",
        "/apply-template",
        "/health",
        "/slots/0",
    ]
    assert server.requests[-1].params == {"action": "save"}


# --------------------------------------------------------------------------------------------
# Focused tests
# --------------------------------------------------------------------------------------------


def encode(request: Request, **options: Any) -> tuple[dict[str, Any], ConversionReport]:
    report = ConversionReport(target=FORMAT)
    call = LlamaCppCodec(CodecOptions(**options)).encode_request(request, report)
    assert call.json is not None
    return call.json, report


def losses(report: ConversionReport) -> list[tuple[str, str]]:
    return [(loss.field, loss.action) for loss in report.losses]


def test_sampling_schema_and_extensions_are_sent() -> None:
    request = text_request().model_copy(
        update={
            "sampling": Sampling(
                temperature=0.2, top_p=0.9, top_k=40, seed=7, stop=["\n\n"], presence_penalty=0.5
            ),
            "response_format": ResponseFormat(type="json_object"),
            "extensions": {"grammar": "root ::= [a-z]+", "id_slot": 3, "params": {"n_probs": 2}},
        }
    )
    body, report = encode(request)
    assert {k: v for k, v in body.items() if k != "prompt"} == {
        "model": "test-model",
        "n_predict": 64,
        "temperature": 0.2,
        "top_k": 40,
        "top_p": 0.9,
        "seed": 7,
        "presence_penalty": 0.5,
        "stop": ["\n\n", "<|im_end|>", "<|im_start|>"],
        "json_schema": {"type": "object"},
        "grammar": "root ::= [a-z]+",
        "id_slot": 3,
        "cache_prompt": True,
        "n_probs": 2,
        "stream": False,
    }
    assert report.lossless


def test_losses_are_reported_and_strict_mode_rejects_them() -> None:
    request = Request(
        model="test-model",
        effort="high",
        messages=[
            Message(
                role="user",
                content=[TextPart(text="Look."), ImagePart(url="https://example.com/a.png")],
            ),
            Message(
                role="assistant",
                content=[
                    ReasoningPart(text="Hmm.", origin=Origin(format=FORMAT, model="test-model")),
                    ToolCallPart(id="call_1", name="get_time", arguments="not json"),
                ],
            ),
            Message(
                role="tool",
                content=[
                    ToolResultPart(call_id="call_1", content=[TextPart(text="boom")], is_error=True)
                ],
            ),
        ],
        tools=[tool_request().tools[1]],
        tool_choice=NamedToolChoice(name="get_time"),
    )
    body, report = encode(request)
    assert losses(report) == [
        ("tool_choice", "dropped"),
        ("effort", "dropped"),
        ("messages[0].content[1]", "dropped"),
        ("messages[1].content[0]", "dropped"),
        ("messages[1].content[1].arguments", "approximated"),
        ("messages[2].content[0].is_error", "dropped"),
    ]
    assert '{"name": "get_time", "arguments": {"input": "not json"}}' in body["prompt"]
    with pytest.raises(ConversionError):
        encode(request, strict=True)


def test_tool_choice_none_renders_no_tools_and_parses_no_calls() -> None:
    request = tool_request().model_copy(update={"tool_choice": "none"})
    server = serve_json(result(WEATHER_CALL))
    completion = client_for(FORMAT, server).complete(request)
    assert "<tools>" not in server.last.body["prompt"]
    assert losses(completion.report) == [("tool_choice", "approximated")]
    # Without tools the text stays text.
    assert completion.response.message.tool_calls() == []
    assert completion.response.message.text_content() == WEATHER_CALL
    # With a prompt rendered earlier the tools are in it already; the loss is still reported.
    _, report = encode(request.model_copy(update={"prompt": "<|im_start|>user\nHi<|im_end|>\n"}))
    assert losses(report) == [("tool_choice", "approximated")]


def test_unknown_tool_parser_falls_back_to_marker_detection() -> None:
    """A profile may name a syntax the catalog does not have yet; detection still parses."""
    options = CodecOptions(tool_parser="not-in-the-catalog")
    server = serve_json(result(WEATHER_CALL))
    response = client_for(FORMAT, server, options=options).complete(tool_request()).response
    assert call_view(response) == [("get_weather", {"city": "Paris"})]
    events = [*pieces(WEATHER_CALL[:9], WEATHER_CALL[9:]), result("")]
    stream = client_for(FORMAT, serve_sse(events), options=options).stream(tool_request())
    assert call_view(assemble(stream)) == [("get_weather", {"city": "Paris"})]


@pytest.mark.parametrize(
    ("stop_type", "finish"),
    [("eos", "stop"), ("word", "stop"), ("limit", "length"), ("none", "stop")],
)
def test_stop_types_map_to_finish_reasons(stop_type: str, finish: str) -> None:
    body = result("Hello there.", stop_type=stop_type)
    response = client_for(FORMAT, serve_json(body)).complete(text_request()).response
    assert (response.finish_reason, response.raw_finish_reason) == (finish, stop_type)


def test_think_block_opened_by_the_prompt() -> None:
    """A template whose generation prompt ends in `<think>` starts the answer inside it."""
    prompt = "<|im_start|>user\nWhat is 1+1?<|im_end|>\n<|im_start|>assistant\n<think>\n"
    request = Request(model="test-model", prompt=prompt, messages=[Message.text("user", prompt)])
    content = "Add the numbers.\n</think>\n\n2"
    response = client_for(FORMAT, serve_json(result(content))).complete(request).response
    assert [p.text for p in response.message.reasoning()] == ["Add the numbers."]
    assert response.message.text_content() == "2"
    events = [*pieces("Add the", " numbers.\n<", "/think>\n", "\n2"), result("")]
    response = assemble(client_for(FORMAT, serve_sse(events)).stream(request))
    assert [p.text for p in response.message.reasoning()] == ["Add the numbers."]
    assert response.message.text_content() == "2"


@pytest.mark.parametrize(
    ("text", "open_at_start"),
    [
        ("<think>\nPlan it.\n</think>\n\nHello there.", False),
        ("  <think>a  b </think>c", False),
        ("No reasoning here.", False),
        ("<think>never closed", False),
        ("<thin", False),
        ("\n\n", False),
        ("", False),
        ("Plan.\n</think>\n\nDone.", True),
        ("still thinking", True),
        ("<think>x</think><tool_call>y</tool_call>", False),
    ],
)
def test_streamed_think_split_matches_the_whole_text_split(text: str, open_at_start: bool) -> None:
    reasoning, answer = split_think(text, open_at_start)
    for size in range(1, max(len(text), 1) + 1):
        codec = LlamaCppCodec()
        codec.encode_request(
            Request(model="m", prompt="<think>\n" if open_at_start else "x"), ConversionReport()
        )
        events = [
            StreamEvent(data=json.dumps({"content": text[i : i + size], "stop": False}))
            for i in range(0, len(text), size)
        ]
        events.append(
            StreamEvent(data=json.dumps({"content": "", "stop": True, "stop_type": "eos"}))
        )
        deltas = list(codec.decode_stream(events))
        streamed_reasoning = "".join(d.text for d in deltas if isinstance(d, ReasoningDelta))
        streamed_text = "".join(d.text for d in deltas if isinstance(d, TextDelta))
        assert streamed_reasoning == (reasoning or ""), size
        assert streamed_text == answer, size


def test_stream_without_final_event_is_an_error() -> None:
    deltas = list(client_for(FORMAT, serve_sse(pieces("Hel", "lo"))).stream(text_request()))
    assert "".join(d.text for d in deltas if isinstance(d, TextDelta)) == "Hello"
    assert isinstance(deltas[-1], ErrorDelta) and deltas[-1].kind == "server"


def test_server_stream_and_error_shapes() -> None:
    codec = LlamaCppCodec()
    events = list(codec.encode_stream(response_to_deltas(ir_response())))
    chunks = [json.loads(event.data) for event in events]
    assert [c["content"] for c in chunks[:-1]] == [
        "<think>\nTwo lookups are needed.",
        "\n</think>\n\n",
        "Checking both cities.",
        WEATHER_CALL,
        TIME_CALL,
    ]
    assert all(
        c == {"index": 0, "content": c["content"], "tokens": [], "stop": False} for c in chunks[:-1]
    )
    assert {k: chunks[-1][k] for k in ("stop", "stop_type", "model", "tokens_evaluated")} == {
        "stop": True,
        "stop_type": "eos",
        "model": "test-model",
        "tokens_evaluated": 120,
    }
    status, body = codec.encode_error(ModelError("overloaded", "Loading model"))
    assert (status, body) == (
        503,
        {"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}},
    )
    status, body = codec.encode_error(ModelError("bad_request", "bad", status=400))
    assert body["error"]["type"] == "invalid_request_error" and status == 400
    stream = list(codec.encode_stream([ErrorDelta(kind="server", message="decode failed")]))
    assert json.loads(stream[0].data) == {
        "error": {"code": 500, "message": "decode failed", "type": "server_error"}
    }


@pytest.mark.parametrize(
    "prompt",
    [[1, 2, 3], ["first", "second"], None, 42],
)
def test_decode_request_rejects_prompts_it_cannot_represent(prompt: Any) -> None:
    with pytest.raises(ValueError, match="prompt"):
        LlamaCppCodec().decode_request({"prompt": prompt})


def test_decode_request_reads_the_multimodal_prompt_form() -> None:
    body = {
        "prompt": {"prompt_string": "Describe <__media__>", "multimodal_data": ["iVBORw0KGgo="]}
    }
    request = LlamaCppCodec().decode_request(body)
    assert request.prompt == "Describe <__media__>"
    assert request.messages[0].content == [
        TextPart(text="Describe <__media__>"),
        ImagePart(data="iVBORw0KGgo="),
    ]
