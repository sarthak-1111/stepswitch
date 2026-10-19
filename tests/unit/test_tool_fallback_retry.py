"""Engine tool-call parse failures (ADR 0035): the model answered, but the engine could not parse
the tool call it generated.

The client does not send such a request again as it was (the same request tends to fail the same
way), and the built-in agent asks the same worker again through the fallback tool protocol,
which Stepswitch parses itself. The mock server speaks the Ollama native format; its 500 body is
the one Ollama 0.34.4 returned for gpt-oss.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch.compat.client import Endpoint, ModelClient
from stepswitch.compat.errors import ModelError, tool_call_unparsed
from stepswitch.compat.ir import Message, Request, ToolSpec
from stepswitch.workers.base import Worker, WorkerSpec
from tests.conformance.harness import MockServer, Recorded, json_response
from tests.unit.test_builtin_agent import StubGates, events, make_loop

UNPARSED = 'error parsing tool call: raw=\'{"path":"calc.py","edits":[{"op":"replace"\''
READ = ToolSpec(
    name="read",
    description="Read a file.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


def ollama_text(text: str) -> dict[str, Any]:
    return {
        "model": "test-model",
        "message": {"role": "assistant", "content": text},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 50,
        "eval_count": 5,
    }


def client(server: MockServer, *, retries: int = 2) -> ModelClient:
    endpoint = Endpoint(
        name="mock", base_url="http://127.0.0.1:65530", format="ollama-native", model="test-model"
    )
    return ModelClient(
        endpoint, transport=server.transport(), max_retries=retries, sleep=lambda _s: None
    )


def request() -> Request:
    return Request(
        model="test-model",
        messages=[Message.text("user", "Read calc.py.")],
        tools=[READ],
    )


def test_an_unparsed_tool_call_is_told_apart_and_not_sent_again_as_it_was() -> None:
    assert tool_call_unparsed(ModelError("server", UNPARSED, status=500))
    assert not tool_call_unparsed(ModelError("server", "model runner crashed", status=500))
    assert not tool_call_unparsed(ValueError(UNPARSED))
    server = MockServer().route(
        "POST", r"/api/chat", lambda _r: json_response({"error": UNPARSED}, 500)
    )
    with pytest.raises(ModelError) as raised:
        client(server).complete(request())
    assert tool_call_unparsed(raised.value) and len(server.requests) == 1
    # Any other server error is retried as before.
    crashed = MockServer().route(
        "POST", r"/api/chat", lambda _r: json_response({"error": "model runner crashed"}, 500)
    )
    with pytest.raises(ModelError):
        client(crashed).complete(request())
    assert len(crashed.requests) == 3


def test_the_agent_asks_the_same_worker_again_through_the_fallback_protocol(
    tmp_path: Path,
) -> None:
    def respond(call: Recorded) -> httpx.Response:
        body = call.body
        if body.get("tools"):  # the native call: the engine cannot parse the model's tool call
            return json_response({"error": UNPARSED}, 500)
        assert "format" in body  # the fallback protocol constrains the reply to an action
        return json_response(
            ollama_text(json.dumps({"tool": "read", "arguments": {"path": "calc.py"}}))
        )

    server = MockServer().route("POST", r"/api/chat", respond)
    spec = WorkerSpec(id="mock/test-model", endpoint="mock", model="test-model", tier="t2")
    model_worker = Worker(spec, client(server))
    loop = make_loop(tmp_path, [model_worker], StubGates([True]))
    completion = loop._complete(model_worker, request())
    (call,) = completion.response.message.tool_calls()
    assert call.name == "read" and json.loads(call.arguments) == {"path": "calc.py"}
    assert len(server.requests) == 2 and loop.tool_fallbacks == 1
    (event,) = events(loop, "tool_fallback")
    assert event["worker"] == "mock/test-model" and "error parsing tool call" in event["message"]


def test_other_errors_and_scripted_workers_fail_over_as_before(tmp_path: Path) -> None:
    crashed = MockServer().route(
        "POST", r"/api/chat", lambda _r: json_response({"error": "model runner crashed"}, 500)
    )
    spec = WorkerSpec(id="mock/test-model", endpoint="mock", model="test-model", tier="t2")
    model_worker = Worker(spec, client(crashed, retries=0))
    loop = make_loop(tmp_path, [model_worker], StubGates([True]))
    with pytest.raises(ModelError):
        loop._complete(model_worker, request())
    assert loop.tool_fallbacks == 0 and not events(loop, "tool_fallback")
    # A worker on the fallback protocol already is not asked again the same way.
    fallback_spec = WorkerSpec(
        id="mock/other", endpoint="mock", model="test-model", tier="t2", tool_protocol="fallback"
    )
    unparsed = MockServer().route(
        "POST", r"/api/chat", lambda _r: json_response({"error": UNPARSED}, 500)
    )
    other = Worker(fallback_spec, client(unparsed, retries=0))
    with pytest.raises(ModelError):
        loop._complete(other, request())
    assert loop.tool_fallbacks == 0
