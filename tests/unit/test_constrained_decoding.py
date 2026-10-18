"""Constrained decoding (TK8): the classifier, the fallback tool protocol, the HANDOFF writer and
the reviewer ask for answers under a JSON Schema, and check what comes back against it."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from stepswitch.compat.client import Completion
from stepswitch.compat.ir import ConversionReport, Message, Request, Response, ToolSpec
from stepswitch.compat.toolcall.fallback import ACTION_SCHEMA_NAME, FallbackModel
from stepswitch.context import handoff
from stepswitch.router import classifier
from stepswitch.verify.review import REVIEW_SCHEMA, parse


class Recorder:
    """A client that records requests and answers with fixed text."""

    def __init__(self, answer: str, fmt: str = "chat-completions") -> None:
        self.answer = answer
        self.codec = SimpleNamespace(format=fmt)
        self.requests: list[Request] = []

    def complete(self, request: Request) -> Completion:
        self.requests.append(request)
        response = Response(message=Message.text("assistant", self.answer))
        return Completion(response=response, report=ConversionReport(), latency_s=0.0)


def schema_of(request: Request) -> tuple[str | None, dict[str, Any] | None]:
    fmt = request.response_format
    assert fmt is not None and fmt.type == "json_schema"
    return fmt.name, fmt.json_schema


def test_the_classifier_answers_under_its_schema(tmp_path: Path) -> None:
    answer = {
        "task_type": "bugfix",
        "difficulty": 2,
        "domain": "general",
        "risk": "low",
        "est_context_tokens": 4000,
        "needs_new_code": False,
        "frontend": False,
        "confidence": 0.8,
    }
    model = Recorder(json.dumps(answer))
    features, details = classifier.classify("Fix add().", tmp_path, model=model)  # type: ignore[arg-type]
    name, schema = schema_of(model.requests[0])
    assert name == "task_features" and schema == classifier.CLASSIFIER_SCHEMA
    assert details["classifier"] == "model" and features.difficulty == 2
    broken, details = classifier.classify("Fix add().", tmp_path, model=Recorder("{}"))  # type: ignore[arg-type]
    assert details["classifier"] == "heuristic" and "unusable" in details["reason"]


def test_the_fallback_protocol_constrains_each_action() -> None:
    tool = ToolSpec(
        name="read",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    client = Recorder(json.dumps({"tool": "read", "arguments": {"path": "a.py"}}))
    model = FallbackModel(client)  # type: ignore[arg-type]
    completion = model.complete(
        Request(model="m", messages=[Message.text("user", "read a.py")], tools=[tool])
    )
    name, schema = schema_of(client.requests[0])
    assert name == ACTION_SCHEMA_NAME and schema is not None and "anyOf" in schema
    [call] = completion.response.message.tool_calls()
    assert call.name == "read" and json.loads(call.arguments) == {"path": "a.py"}


def test_the_handoff_writer_answers_under_its_schema() -> None:
    state = {"done": ["read a.py"], "decisions": [], "open_questions": []}
    model = Recorder(json.dumps(state))
    got, details = handoff.write_state(model, "Work log:\n- read a.py")
    name, schema = schema_of(model.requests[0])
    assert name == "handoff_state" and schema == handoff.WRITER_SCHEMA
    assert model.requests[0].response_format.strict is True  # type: ignore[union-attr]
    assert got == state and details["writer"] == "model"
    bad, details = handoff.write_state(Recorder('{"done": "not a list"}'), "log")
    assert bad is None and details["writer"] == "fallback"


def test_the_reviewer_schema_is_closed_and_checked() -> None:
    assert REVIEW_SCHEMA["additionalProperties"] is False
    assert set(REVIEW_SCHEMA["required"]) == {"approve", "issues"}
    item = REVIEW_SCHEMA["properties"]["issues"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"severity", "file", "line", "note"}
    fenced = parse('```json\n{"approve": false, "issues": ["no test"]}\n```')
    assert fenced is not None and not fenced.approve and fenced.constrained
    assert [i.note for i in fenced.issues] == ["no test"]
    # The server enforces the schema; reading is lenient, so an extra key does not lose it.
    extra = parse('{"approve": false, "issues": ["x"], "extra": 1}')
    assert extra is not None and not extra.approve and extra.issues[0].severity == "medium"
