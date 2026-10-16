"""Fallback tool protocol (SPEC §4.4, ADR 0003 item 6; claim C-005).

Unit tests for `stepswitch.compat.toolcall.fallback`, and `FallbackModel` end to end through a
real `ModelClient` and codec against in-process mock servers (no network) for the
chat-completions, llama.cpp native, Ollama native and text-completion formats. Mock bodies use
the wire shapes of the conformance suites (tests/conformance).
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import jsonschema
import pytest

from stepswitch.compat.ir import (
    ImagePart,
    Message,
    NamedToolChoice,
    ReasoningPart,
    Request,
    ResponseFormat,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    synthetic_call_id,
)
from stepswitch.compat.toolcall.fallback import (
    FINAL,
    GRAMMAR_KEYWORDS,
    JSON_TYPES,
    ActionError,
    FallbackModel,
    action_schema,
    action_text,
    arguments_schema,
    constraint_for,
    default_constraint,
    final_text,
    needs_fallback,
    parse_action,
    render_protocol,
    to_protocol_messages,
)
from tests.conformance.harness import MockServer, Recorded, client_for, json_response

# --------------------------------------------------------------------------------------------
# Tools: the built-in agent's tool set, small schemas
# --------------------------------------------------------------------------------------------

STRING = {"type": "string"}
INTEGER = {"type": "integer"}


def tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
    **extra: Any,
) -> ToolSpec:
    parameters: dict[str, Any] = {"type": "object", "properties": properties, **extra}
    if required:
        parameters["required"] = list(required)
    return ToolSpec(name=name, description=description, parameters=parameters)


READ = tool(
    "read",
    "Read a file, optionally a range of lines.",
    {"path": STRING, "start": {"type": "integer", "minimum": 1}, "end": INTEGER},
    ("path",),
)
GREP = tool(
    "grep",
    "Search file contents with a regular expression.",
    {"pattern": STRING, "path": STRING, "ignore_case": {"type": "boolean"}},
    ("pattern",),
)
REPO_MAP = tool(
    "repo_map",
    "Show a ranked map of the repository's files and symbols.",
    {"focus": {"type": "array", "items": STRING}, "max_tokens": INTEGER},
)
LSP = tool(
    "lsp",
    "Ask the language server for a definition, references or hover text.",
    {
        "action": {"type": "string", "enum": ["definition", "references", "hover"]},
        "path": STRING,
        "line": INTEGER,
        "column": INTEGER,
    },
    ("action", "path", "line", "column"),
)
EDIT = tool(
    "edit",
    "Replace one exact snippet in a file.\nThe old text must match exactly once.",
    {"path": STRING, "old": STRING, "new": STRING},
    ("path", "old", "new"),
    additionalProperties=False,
)
RUN = tool("run", "Run a shell command in the sandbox.", {"command": STRING, "timeout_s": INTEGER})
TEST = tool(
    "test",
    "Run the test suite or selected tests.",
    {"selection": {"type": "array", "items": STRING}},
)
SEARCH = tool(
    "search",
    "Semantic search over the repository.",
    {"query": STRING, "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
    ("query",),
)
LIST = tool("list", "List the files in a directory.", {"path": STRING})
TOOLS = [READ, GREP, REPO_MAP, LSP, EDIT, RUN, TEST, SEARCH, LIST]

# A schema with most of what grammar conversion cannot take.
PATCH = ToolSpec(
    name="patch",
    description="Apply edits to one file.",
    parameters={
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Patch",
        "type": "object",
        "$defs": {
            "Edit": {
                "type": "object",
                "properties": {
                    "old": {"type": "string", "minLength": 1},
                    "new": {"type": "string"},
                    "line": {"type": "integer", "exclusiveMinimum": 0},
                },
                "required": ["old", "new"],
            },
            "Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}},
        },
        "properties": {
            "path": {
                "type": "string",
                "pattern": "^[a-z/._]+$",
                "format": "uri-reference",
                "description": "File to change",
            },
            "edits": {
                "type": "array",
                "items": {"$ref": "#/$defs/Edit"},
                "minItems": 1,
                "uniqueItems": True,
            },
            "mode": {"oneOf": [{"const": "strict"}, {"const": "fuzzy"}], "default": "strict"},
            "tags": {"type": "array", "prefixItems": [STRING], "items": False},
            "options": {
                "allOf": [
                    {"type": "object", "properties": {"dry_run": {"type": "boolean"}}},
                    {"properties": {"verbose": {"type": "boolean"}}},
                ]
            },
            "meta": {
                "type": "object",
                "patternProperties": {"^x-": STRING},
                "minProperties": 1,
            },
            "level": {"type": "number", "minimum": 0, "multipleOf": 0.5},
            "count": {"type": ["integer", "null"], "minimum": 1, "maxLength": 99_999},
            "tree": {"$ref": "#/$defs/Node"},
            "remote": {"$ref": "other.json#/definitions/Thing"},
            "either": {
                "type": "object",
                "properties": {"a": STRING},
                "anyOf": [{"required": ["a"]}, {"required": ["b"]}],
            },
            "nothing": False,
            "anything": True,
        },
        "required": ["path", "edits"],
        "dependentRequired": {"mode": ["path"]},
        "if": {"properties": {"mode": {"const": "fuzzy"}}, "required": ["mode"]},
        "then": {"required": ["level"]},
    },
)


def schema_nodes(schema: Any) -> Iterator[dict[str, Any]]:
    """Every subschema of a grammar-safe schema."""
    if not isinstance(schema, dict):
        return
    yield schema
    for sub in (schema.get("properties") or {}).values():
        yield from schema_nodes(sub)
    for key in ("items", "additionalProperties"):
        yield from schema_nodes(schema.get(key))
    for branch in schema.get("anyOf") or []:
        yield from schema_nodes(branch)


def assert_grammar_safe(schema: dict[str, Any]) -> None:
    """Only keywords and values llama.cpp's converter (and so Ollama's `format`) accepts."""
    for node in schema_nodes(schema):
        assert set(node) <= GRAMMAR_KEYWORDS, sorted(set(node) - GRAMMAR_KEYWORDS)
        assert not ("anyOf" in node and "properties" in node), node
        if "type" in node:
            kinds = node["type"] if isinstance(node["type"], list) else [node["type"]]
            assert kinds and set(kinds) <= set(JSON_TYPES), node
        if "enum" in node:
            assert isinstance(node["enum"], list) and node["enum"]
        for key in ("minItems", "maxItems", "minLength", "maxLength"):
            if key in node:
                assert type(node[key]) is int and 0 <= node[key] <= 4096, node
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if key in node:
                assert type(node[key]) in (int, float), node
        if "properties" in node:
            assert node["additionalProperties"] in (True, False) or isinstance(
                node["additionalProperties"], dict
            )


# --------------------------------------------------------------------------------------------
# action_schema
# --------------------------------------------------------------------------------------------


def test_action_schema_has_a_closed_branch_per_tool_and_a_final_branch() -> None:
    schema = action_schema(TOOLS)
    *branches, final = schema["anyOf"]
    assert len(branches) == len(TOOLS)
    for spec, branch in zip(TOOLS, branches, strict=True):
        assert branch["type"] == "object"
        assert branch["required"] == ["tool", "arguments"]
        assert branch["additionalProperties"] is False
        assert list(branch["properties"]) == ["tool", "arguments"]  # the grammar keeps the order
        assert branch["properties"]["tool"] == {"type": "string", "enum": [spec.name]}
        assert branch["properties"]["arguments"]["type"] == "object"
    assert final == {
        "type": "object",
        "properties": {"final": {"type": "string"}},
        "required": ["final"],
        "additionalProperties": False,
    }
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(
    "action",
    [
        {"tool": "read", "arguments": {"path": "calc.py"}},
        {"tool": "read", "arguments": {"path": "calc.py", "start": 1, "end": 9}},
        {"tool": "lsp", "arguments": {"action": "hover", "path": "a.py", "line": 3, "column": 1}},
        {"tool": "list", "arguments": {}},
        {"final": "Done."},
    ],
)
def test_action_schema_accepts_good_actions(action: dict[str, Any]) -> None:
    jsonschema.validate(action, action_schema(TOOLS))


@pytest.mark.parametrize(
    "action",
    [
        {"tool": "read", "arguments": {}},  # missing required argument
        {"tool": "read"},  # no arguments
        {"tool": "nope", "arguments": {}},  # unknown tool
        {"tool": "read", "arguments": {"path": "a"}, "why": "x"},  # extra top-level key
        {"tool": "read", "arguments": {"path": "a", "mode": "x"}},  # extra argument
        {"tool": "lsp", "arguments": {"action": "rename", "path": "a", "line": 1, "column": 1}},
        {"tool": "read", "arguments": {"path": 3}},
        {"final": 3},
        {"final": "x", "tool": "read"},
        {},
    ],
)
def test_action_schema_rejects_bad_actions(action: dict[str, Any]) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(action, action_schema(TOOLS))


def test_action_schema_without_final_and_with_one_tool_is_a_single_branch() -> None:
    schema = action_schema([EDIT], allow_final=False)
    assert "anyOf" not in schema
    assert schema["properties"]["tool"] == {"type": "string", "enum": ["edit"]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"final": "x"}, schema)
    with pytest.raises(ValueError, match="at least one tool"):
        action_schema([], allow_final=False)


def test_action_schema_stays_in_the_grammar_safe_subset() -> None:
    assert_grammar_safe(action_schema([*TOOLS, PATCH]))


def test_arguments_schema_simplifies_what_grammars_cannot_take() -> None:
    schema = arguments_schema(PATCH)
    assert schema["required"] == ["path", "edits"]
    assert schema["additionalProperties"] is False
    properties = schema["properties"]
    assert "nothing" not in properties  # a `false` property may not appear
    assert properties["path"] == {"type": "string"}  # pattern, format, description dropped
    assert properties["edits"] == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "old": {"type": "string", "minLength": 1},
                "new": {"type": "string"},
                "line": {"type": "integer", "exclusiveMinimum": 0},
            },
            "required": ["old", "new"],
            "additionalProperties": False,
        },
        "minItems": 1,
    }
    assert properties["mode"] == {"anyOf": [{"const": "strict"}, {"const": "fuzzy"}]}
    assert properties["tags"] == {"type": "array"}  # a tuple is not narrowed to []
    assert properties["options"] == {
        "type": "object",
        "properties": {"dry_run": {"type": "boolean"}, "verbose": {"type": "boolean"}},
        "additionalProperties": False,
    }
    assert properties["meta"] == {"type": "object"}
    assert properties["level"] == {"type": "number"}
    assert properties["count"] == {"type": ["integer", "null"], "minimum": 1}
    assert properties["tree"] == {
        "type": "object",
        "properties": {"child": {}},  # the recursive reference accepts any value
        "additionalProperties": False,
    }
    assert properties["remote"] == {}
    assert properties["either"] == {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "additionalProperties": False,
    }
    assert properties["anything"] == {}
    good = {"path": "src/a.py", "edits": [{"old": "x", "new": "y"}], "mode": "fuzzy", "level": 1}
    jsonschema.validate(good, schema)
    jsonschema.validate(good, PATCH.parameters)


def test_arguments_schema_drops_values_conversion_would_reject() -> None:
    legacy = ToolSpec(
        name="legacy",
        parameters={
            "properties": {
                "n": {"type": "integer", "minimum": 0, "exclusiveMaximum": True},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": -1},
                "choice": {"enum": []},
                "free": {"additionalProperties": {"type": "integer"}},
                "upload": {"type": "file"},
            },
            "required": "n",
        },
    )
    schema = arguments_schema(legacy)
    assert_grammar_safe(schema)
    assert schema["type"] == "object"  # implied by `properties`
    assert "required" not in schema
    assert schema["properties"]["n"] == {"type": "integer", "minimum": 0}
    assert schema["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
    assert schema["properties"]["choice"] == {}
    assert schema["properties"]["upload"] == {}
    assert schema["properties"]["free"] == {
        "type": "object",
        "additionalProperties": {"type": "integer"},
    }
    # Not valid JSON Schema as written, so arguments are checked against the grammar-safe form.
    with pytest.raises(jsonschema.SchemaError):
        jsonschema.Draft202012Validator.check_schema(legacy.parameters)
    assert parse_action(action_text("legacy", {"n": 2}), [legacy]) == ("legacy", {"n": 2})
    with pytest.raises(ActionError, match='"n" must be integer, not string'):
        parse_action(action_text("legacy", {"n": "two"}), [legacy])
    assert arguments_schema(ToolSpec(name="bare", parameters={})) == {"type": "object"}
    assert arguments_schema(ToolSpec(name="odd", parameters={"type": "string"})) == {
        "type": "object"
    }


# --------------------------------------------------------------------------------------------
# render_protocol
# --------------------------------------------------------------------------------------------


def test_render_protocol_lists_every_tool_within_the_size_budget() -> None:
    text = render_protocol(TOOLS)
    assert len(text) < 2_500, len(text)
    for spec in TOOLS:
        assert f"\n- {spec.name}(" in text
    assert "- read(path: string, start?: integer, end?: integer): Read a file" in text
    assert 'lsp(action: "definition"|"references"|"hover", path: string' in text
    assert "- test(selection?: string[])" in text
    # one line per description: the second line of edit's description is left out
    assert "- edit(path: string, old: string, new: string): Replace one exact snippet" in text
    assert "must match exactly once" not in text
    assert "Reply with exactly one JSON object" in text
    assert '{"final": "<answer>"}' in text
    assert "Result of <tool> (call <n>):" in text
    # two worked examples with the real tool names, in the exact form the history uses
    assert "Example 1" in text and "Example 2" in text
    assert f"You: {action_text('read', {'path': '<path>'})}" in text
    assert "User: Result of read (call 1):" in text
    assert f"You: {action_text('grep', {'pattern': '<pattern>'})}" in text
    assert f"You: {final_text('<answer based on that result>')}" in text


def test_render_protocol_options() -> None:
    plain = render_protocol(TOOLS, examples=False)
    assert "Example" not in plain
    assert len(plain) < len(render_protocol(TOOLS))
    required = render_protocol([EDIT], allow_final=False)
    assert '"final"' not in required
    assert "Example 2" not in required  # one tool and no final answer: one example
    single = render_protocol([LIST])
    assert "- list(path?: string): List the files in a directory." in single
    assert f"You: {final_text('<answer>')}" in single  # the second example answers directly


ANCHORED_EDIT = ToolSpec(
    name="edit",
    description="Change a file by line anchors.",
    parameters={
        "type": "object",
        "properties": {
            "path": STRING,
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["replace", "delete"]},
                        "start": STRING,
                        "text": STRING,
                    },
                    "required": ["op", "start"],
                },
            },
            "mode": {"anyOf": [{"type": "string", "enum": ["a", "b"]}, {"type": "integer"}]},
        },
        "required": ["path"],
    },
    examples=[{"path": "a.py", "edits": [{"op": "replace", "start": "3:ab", "text": "x = 1"}]}],
)


def test_render_protocol_shows_nested_fields_and_tool_examples() -> None:
    text = render_protocol([READ, ANCHORED_EDIT])
    # the fields of an object inside an array are spelled out, so a model knows what to send
    assert (
        '- edit(path: string, edits?: {op: "replace"|"delete", start: string, text?: string}[],'
        ' mode?: "a"|"b"|integer): Change a file by line anchors.'
    ) in text
    # a tool's own example is shown under its line ...
    example = ANCHORED_EDIT.examples[0] if ANCHORED_EDIT.examples else {}
    assert "\n  example: " + json.dumps(example, ensure_ascii=False) + "\n" in text
    # ... and replaces the placeholders in the worked example for that tool
    assert f"You: {action_text('edit', example)}" in text
    assert f"You: {action_text('read', {'path': '<path>'})}" in text  # read has no example
    # a union inside an array keeps its parentheses; a lone object needs none
    union = tool("pick", "Pick.", {"items": {"type": "array", "items": {"enum": ["x", "y"]}}})
    assert '- pick(items?: ("x"|"y")[])' in render_protocol([union])


# --------------------------------------------------------------------------------------------
# to_protocol_messages
# --------------------------------------------------------------------------------------------

IMAGE = ImagePart(url="data:image/png;base64,iVBORw0KGgo=")


def conversation() -> Request:
    return Request(
        model="test-model",
        system=[TextPart(text="You fix bugs.")],
        messages=[
            Message.text("user", "Fix add() in calc.py."),
            Message(
                role="assistant",
                content=[
                    ReasoningPart(text="Find it first."),
                    TextPart(text="Let me look."),
                    ToolCallPart(id="call_a", name="grep", arguments='{"pattern": "def add"}'),
                    ToolCallPart(id="call_b", name="read", arguments='{"path": "calc.py"}'),
                ],
            ),
            Message(
                role="tool",
                content=[
                    ToolResultPart(
                        call_id="call_a",
                        name="grep",
                        content=[TextPart(text="calc.py:1:def add(a, b):")],
                    )
                ],
            ),
            Message(
                role="tool",
                content=[
                    ToolResultPart(
                        call_id="call_b",
                        content=[TextPart(text="def add(a, b):\n    return a - b"), IMAGE],
                    )
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolCallPart(
                        id="call_c",
                        name="edit",
                        arguments='{"path": "calc.py", "old": "a - b", "new": "a + b"}',
                    )
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultPart(
                        call_id="call_c",
                        content=[TextPart(text="old text not found")],
                        is_error=True,
                    ),
                    TextPart(text="Use the exact line."),
                ],
            ),
            Message.text("assistant", "The edit failed: the line is `return a - b`."),
            Message.text("user", "Go on."),
        ],
        tools=[GREP, READ, EDIT],
        tool_choice="auto",
        parallel_tool_calls=True,
    )


def test_to_protocol_messages_round_trip() -> None:
    request = conversation()
    protocol = to_protocol_messages(request)
    assert (protocol.tools, protocol.tool_choice, protocol.parallel_tool_calls) == ([], None, None)
    assert [part.text for part in protocol.system] == [
        "You fix bugs.",
        render_protocol([GREP, READ, EDIT]),
    ]
    roles = [message.role for message in protocol.messages]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant", "user"]
    first, results, second, failed, answer = (protocol.messages[i] for i in (1, 2, 3, 4, 5))

    # assistant calls -> the JSON action text, after the message's own text; reasoning stays
    assert isinstance(first.content[0], ReasoningPart)
    lines = first.text_content().splitlines()
    assert lines[0] == "Let me look."
    assert [parse_action(line, request.tools) for line in lines[1:]] == [
        ("grep", {"pattern": "def add"}),
        ("read", {"path": "calc.py"}),
    ]
    assert parse_action(second.text_content(), request.tools) == (
        "edit",
        {"path": "calc.py", "old": "a - b", "new": "a + b"},
    )
    assert parse_action(answer.text_content(), request.tools) == (
        FINAL,
        {"answer": "The edit failed: the line is `return a - b`."},
    )
    assert answer.text_content() == final_text("The edit failed: the line is `return a - b`.")

    # consecutive results share one user message; calls are numbered in order; images stay
    assert results.text_content() == (
        "Result of grep (call 1):\ncalc.py:1:def add(a, b):\n\n"
        "Result of read (call 2):\ndef add(a, b):\n    return a - b"
    )
    assert results.content[-1] == IMAGE
    assert failed.text_content() == (
        "Result of edit (call 3) [error]:\nold text not found\n\nUse the exact line."
    )
    assert protocol.messages[-1] == request.messages[-1]
    # the request itself is unchanged
    assert request.tools == [GREP, READ, EDIT]
    assert request.messages[1].tool_calls()


def test_to_protocol_messages_edge_cases() -> None:
    trailing = Request(
        model="m",
        messages=[Message.text("user", "Hi"), Message.text("assistant", "Hello")],
        tools=[READ],
    )
    assert to_protocol_messages(trailing).messages[-1].text_content() == "Hello"
    required = conversation().model_copy(update={"tool_choice": "required"})
    protocol = to_protocol_messages(required)
    assert protocol.messages[5].text_content().startswith("The edit failed")
    assert '"final"' not in protocol.system[-1].text
    orphan = Request(
        model="m",
        messages=[
            Message(
                role="tool",
                content=[ToolResultPart(call_id="call_zz", content=[TextPart(text="")])],
            )
        ],
        tools=[READ],
    )
    assert to_protocol_messages(orphan).messages[0].text_content() == (
        "Result of tool (call call_zz):\n(no output)"
    )
    named = conversation().model_copy(update={"tool_choice": NamedToolChoice(name="edit")})
    assert to_protocol_messages(named).system[-1].text == render_protocol([EDIT], allow_final=False)
    rendered = conversation().model_copy(update={"prompt": "<|im_start|>user\nFix add()"})
    assert to_protocol_messages(rendered).prompt is None  # it showed the old conversation
    unknown = conversation().model_copy(update={"tool_choice": NamedToolChoice(name="nope")})
    with pytest.raises(ValueError, match="nope"):
        to_protocol_messages(unknown)
    broken = Request(
        model="m",
        messages=[
            Message(role="assistant", content=[ToolCallPart(id="x", name="run", arguments="ls")])
        ],
    )
    assert to_protocol_messages(broken).messages[0].text_content() == action_text(
        "run", {"input": "ls"}
    )
    follow_up = Request(
        model="m",
        messages=[
            Message(
                role="assistant",
                content=[ToolCallPart(id="c1", name="read", arguments='{"path": "a"}')],
            ),
            Message(
                role="tool",
                content=[ToolResultPart(call_id="c1", content=[TextPart(text="x = 1")])],
            ),
            Message(role="user", content=[TextPart(text="Now fix it.")], name="dev"),
        ],
        tools=[READ],
    )
    merged = to_protocol_messages(follow_up).messages  # roles keep alternating
    assert [message.role for message in merged] == ["assistant", "user"]
    assert merged[-1].text_content() == "Result of read (call 1):\nx = 1\n\nNow fix it."
    assert merged[-1].name == "dev"


# --------------------------------------------------------------------------------------------
# parse_action
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"tool": "read", "arguments": {"path": "a.py"}}', ("read", {"path": "a.py"})),
        (
            '```json\n{"tool": "read", "arguments": {"path": "a.py"}}\n```',
            ("read", {"path": "a.py"}),
        ),
        (
            'Sure! I will search first.\n{"tool": "grep", "arguments": {"pattern": "def add"}}\n'
            "Then I will read the file {once I know where it is}.",
            ("grep", {"pattern": "def add"}),
        ),
        (
            'I need {a plan}. {"tool": "edit", "arguments": {"path": "a.py", "old": "x = {",'
            ' "new": "x = {\\"k\\": \\"}\\"}"}}',
            ("edit", {"path": "a.py", "old": "x = {", "new": 'x = {"k": "}"}'}),
        ),
        (
            '{"tool": "read", "arguments": "{\\"path\\": \\"a.py\\", \\"start\\": 2}"}',
            ("read", {"path": "a.py", "start": 2}),
        ),
        ('{"name": "grep", "parameters": {"pattern": "x"}}', ("grep", {"pattern": "x"})),
        ('{"name": "grep", "args": {"pattern": "x"}}', ("grep", {"pattern": "x"})),
        ('{"tool": "grep", "input": {"pattern": "x"}}', ("grep", {"pattern": "x"})),
        (
            '{"type": "function", "function": {"name": "read",'
            ' "arguments": "{\\"path\\": \\"b\\"}"}}',
            ("read", {"path": "b"}),
        ),
        ('{"action": "read", "action_input": {"path": "c"}}', ("read", {"path": "c"})),
        ('{"tool": {"name": "read", "arguments": {"path": "l"}}}', ("read", {"path": "l"})),
        ('{"tool": "read", "arguments": "{\'path\': \'m\'}"}', ("read", {"path": "m"})),
        (
            '<tool_call>\n{"name": "Read", "arguments": {"path": "d"}}\n</tool_call>',
            ("read", {"path": "d"}),
        ),
        ('{"tool": "repo-map", "arguments": {}}', ("repo_map", {})),
        ('{"tool": "read", "path": "flat.py"}', ("read", {"path": "flat.py"})),
        ('{"tool": "list"}', ("list", {})),
        (
            "{'tool': 'read', 'arguments': {'path': 'e.py', 'end': None}}",
            ("read", {"path": "e.py"}),
        ),
        ('{"tool": "read", "arguments": {"path": "f.py",},}', ("read", {"path": "f.py"})),
        (
            '{"tool": "read", "arguments": {"path": "g.py", "start": "3", "end": null}}',
            ("read", {"path": "g.py", "start": 3}),
        ),
        (
            '{"tool": "grep", "arguments": {"pattern": "x", "ignore_case": "true"}}',
            ("grep", {"pattern": "x", "ignore_case": True}),
        ),
        (
            '<think>Maybe {"tool": "grep", "arguments": {"pattern": "x"}}.</think>\n'
            '{"tool": "read", "arguments": {"path": "h.py"}}',
            ("read", {"path": "h.py"}),
        ),
        (
            'it is in calc.py</think>{"tool": "read", "arguments": {"path": "i.py"}}',
            ("read", {"path": "i.py"}),
        ),
        (
            '{"tool": "edit", "arguments": {"path": "j.py", "old": "</think>", "new": "x"}}',
            ("edit", {"path": "j.py", "old": "</think>", "new": "x"}),
        ),
        (
            '{"tool": "edit", "arguments": {"path": "k", "old": "a\nb", "new": "c"}}',
            ("edit", {"path": "k", "old": "a\nb", "new": "c"}),
        ),
        ('{"final": "All tests pass."}', (FINAL, {"answer": "All tests pass."})),
        ('{"final_answer": "ok"}', (FINAL, {"answer": "ok"})),
        ('{"action": "Final Answer", "action_input": "ok"}', (FINAL, {"answer": "ok"})),
        ('{"final": {"status": "done"}}', (FINAL, {"answer": '{"status": "done"}'})),
    ],
)
def test_parse_action_takes_messy_outputs(text: str, expected: tuple[str, Any]) -> None:
    assert parse_action(text, TOOLS) == expected


@pytest.mark.parametrize(
    ("text", "hint"),
    [
        ("", "the reply is empty"),
        ("   \n", "the reply is empty"),
        ("I will read calc.py now.", "no JSON object found"),
        (
            '{"tool": "raed", "arguments": {"path": "a"}}',
            'unknown tool "raed" (did you mean "read"?)',
        ),
        (
            '{"tool": "read", "arguments": {}}',
            'arguments for "read": missing required argument "path"',
        ),
        (
            '{"tool": "lsp", "arguments": {"action": "hover"}}',
            'missing required arguments "path", "line"',
        ),
        (
            '{"tool": "read", "arguments": {"path": "a", "start": "ten"}}',
            '"start" must be integer, not string',
        ),
        ('{"tool": "read", "arguments": {"path": 3}}', '"path" must be string, not integer'),
        (
            '{"tool": "edit", "arguments": {"path": "a", "old": "x", "new": "y", "mode": 1}}',
            'unknown argument "mode"; allowed: path, old, new',
        ),
        (
            '{"tool": "lsp", "arguments":'
            ' {"action": "rename", "path": "a", "line": 1, "column": 1}}',
            '"action" must be one of: "definition", "references", "hover"',
        ),
        (
            '{"tool": "search", "arguments": {"query": "x", "limit": 99}}',
            '"limit": 99 is greater than',
        ),
        ('{"tool": "read", "arguments": "path=a.py"}', "is a string that is not a JSON object"),
        ('{"tool": "read", "arguments": ["a.py"]}', "must be a JSON object, not array"),
        ('{"tool": "read", "arguments": {"path": "a"', "is not closed (was the reply cut off?)"),
        ('{"tool": "read" "arguments": {}}', "the JSON is not valid (Expecting ',' delimiter"),
        ('{"path": "a.py"}', 'the JSON object has no "tool" key'),
        ("[1, 2, 3]", "no JSON object found"),
        pytest.param(
            '{"tool": "read", "arguments": {"path": ' + "[" * 90_000 + "]" * 90_000 + "}}",
            "the JSON is not valid (it is nested too deeply)",
            id="deeply-nested",
        ),
        pytest.param(
            '{"tool": "read", "arguments": "' + "[" * 90_000 + '"}',
            "is a string that is not a JSON object (it is nested too deeply)",
            id="deeply-nested-string",
        ),
    ],
)
def test_parse_action_errors_carry_a_repair_hint(text: str, hint: str) -> None:
    with pytest.raises(ActionError) as caught:
        parse_action(text, TOOLS)
    assert hint in str(caught.value)
    assert isinstance(caught.value, ValueError)


def test_parse_action_validates_against_the_full_schema() -> None:
    """The grammar schema drops `pattern`; validation still applies it."""
    good = {"path": "src/a.py", "edits": [{"old": "x", "new": "y"}]}
    assert parse_action(action_text("patch", good), [PATCH]) == ("patch", good)
    with pytest.raises(ActionError, match="does not match"):
        parse_action(action_text("patch", {**good, "path": "Src A.py"}), [PATCH])
    with pytest.raises(ActionError, match='"edits": \\[\\] should be non-empty'):
        parse_action(action_text("patch", {**good, "edits": []}), [PATCH])
    with pytest.raises(ActionError, match='missing required argument "level"'):
        parse_action(action_text("patch", {**good, "mode": "fuzzy"}), [PATCH])


def test_parse_action_never_fetches_a_remote_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """A remote `$ref` cannot be checked offline; the grammar-safe form is checked instead."""

    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("validation tried to fetch a remote schema")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    fetch = ToolSpec(
        name="fetch",
        parameters={
            "type": "object",
            "properties": {
                "url": STRING,
                "options": {"$ref": "https://example.invalid/options.json"},
            },
            "required": ["url"],
        },
    )
    good = {"url": "a", "options": {"depth": 1}}
    assert parse_action(action_text("fetch", good), [fetch]) == ("fetch", good)
    with pytest.raises(ActionError, match='missing required argument "url"'):
        parse_action(action_text("fetch", {"options": {}}), [fetch])
    remote = {"path": "src/a.py", "edits": [{"old": "x", "new": "y"}], "remote": {"k": 1}}
    assert parse_action(action_text("patch", remote), [PATCH]) == ("patch", remote)


def test_parse_action_reports_a_number_beyond_the_digit_limit() -> None:
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4_300)
    try:
        with pytest.raises(ActionError, match="it has a number with too many digits"):
            parse_action('{"tool": "read", "arguments": {"start": ' + "9" * 5_000 + "}}", TOOLS)
    finally:
        sys.set_int_max_str_digits(limit)


def test_parse_action_final_answer_only_when_allowed() -> None:
    with pytest.raises(ActionError, match="a tool call is required"):
        parse_action('{"final": "done"}', TOOLS, allow_final=False)
    with pytest.raises(ActionError) as caught:
        parse_action("nothing", TOOLS, allow_final=False)
    assert '"final"' not in str(caught.value)


# --------------------------------------------------------------------------------------------
# FallbackModel end to end through ModelClient and a codec, against mock servers
# --------------------------------------------------------------------------------------------


def chat_reply(
    text: str | None,
    *,
    prompt: int = 100,
    output: int = 10,
    cached: int = 0,
    reasoning: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if reasoning:
        message["reasoning_content"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-7",
        "object": "chat.completion",
        "created": 1760600000,
        "model": "test-model",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": output,
            "total_tokens": prompt + output,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }


def llamacpp_reply(
    text: str, *, prompt: int = 100, output: int = 10, cached: int = 0, reasoning: str | None = None
) -> dict[str, Any]:
    content = f"<think>\n{reasoning}\n</think>\n\n{text}" if reasoning else text
    return {
        "index": 0,
        "content": content,
        "tokens": [],
        "id_slot": 0,
        "stop": True,
        "model": "test-model",
        "tokens_predicted": output,
        "tokens_evaluated": prompt,
        "has_new_line": False,
        "truncated": False,
        "stop_type": "eos",
        "stopping_word": "",
        "tokens_cached": prompt + output,
        "timings": {"cache_n": cached, "prompt_n": prompt - cached, "predicted_n": output},
    }


def ollama_reply(
    text: str, *, prompt: int = 100, output: int = 10, cached: int = 0, reasoning: str | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if reasoning:
        message["thinking"] = reasoning
    return {
        "model": "test-model",
        "created_at": "2026-10-16T04:51:10.7728029Z",
        "message": message,
        "done": True,
        "done_reason": "stop",
        "total_duration": 499399600,
        "prompt_eval_count": prompt,
        "prompt_eval_cached_count": cached,
        "eval_count": output,
    }


def text_reply(
    text: str, *, prompt: int = 100, output: int = 10, cached: int = 0
) -> dict[str, Any]:
    return {
        "id": "cmpl-7",
        "object": "text_completion",
        "created": 1760600000,
        "model": "test-model",
        "choices": [{"text": text, "index": 0, "logprobs": None, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": output,
            "total_tokens": prompt + output,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }


def message_texts(body: dict[str, Any]) -> str:
    out = []
    for message in body["messages"]:
        content = message.get("content")
        if isinstance(content, list):
            out.append("\n".join(str(part.get("text", "")) for part in content))
        elif isinstance(content, str):
            out.append(content)
    return "\n".join(out)


@dataclass(frozen=True)
class Wire:
    """One API format as the mock server sees it."""

    fmt: str
    path: str
    reply: Callable[..., dict[str, Any]]
    schema: Callable[[dict[str, Any]], Any]  # the constraint the request carries
    text: Callable[[dict[str, Any]], str]  # all prompt text of the request


CHAT = Wire(
    "chat-completions",
    "/v1/chat/completions",
    chat_reply,
    lambda body: (body.get("response_format") or {}).get("json_schema", {}).get("schema"),
    message_texts,
)
LLAMACPP = Wire(
    "llamacpp-native",
    "/completion",
    llamacpp_reply,
    lambda body: body.get("json_schema"),
    lambda body: str(body["prompt"]),
)
OLLAMA = Wire(
    "ollama-native", "/api/chat", ollama_reply, lambda body: body.get("format"), message_texts
)
WIRES = [CHAT, LLAMACPP, OLLAMA]


def scripted(wire: Wire, *bodies: dict[str, Any], status: int = 200) -> MockServer:
    """A mock server answering with `bodies` in order."""
    server = MockServer()

    def respond(_recorded: Recorded) -> httpx.Response:
        return json_response(bodies[len(server.requests) - 1], status)

    return server.route("POST", wire.path, respond)


def fallback(wire: Wire, server: MockServer, **kwargs: Any) -> FallbackModel:
    return FallbackModel(client_for(wire.fmt, server), **kwargs)


def task_request(**updates: Any) -> Request:
    request = Request(
        model="test-model",
        system=[TextPart(text="You fix bugs.")],
        messages=[
            Message.text("user", "Fix add() in calc.py."),
            Message(
                role="assistant",
                content=[
                    ToolCallPart(id="call_9", name="grep", arguments='{"pattern": "def add"}')
                ],
            ),
            Message(
                role="tool",
                content=[
                    ToolResultPart(
                        call_id="call_9",
                        name="grep",
                        content=[TextPart(text="calc.py:1:def add(a, b):")],
                    )
                ],
            ),
        ],
        tools=[READ, GREP, EDIT],
        tool_choice="auto",
        max_output_tokens=256,
    )
    return request.model_copy(update=updates)


GOOD = '{"tool": "read", "arguments": {"path": "calc.py"}}'
BAD = "I will read calc.py now."


def tool_losses(completion: Any) -> list[str]:
    return [loss.detail for loss in completion.report.losses if loss.field == "tools"]


@pytest.mark.parametrize("wire", WIRES, ids=lambda wire: wire.fmt)
def test_fallback_repairs_and_returns_one_synthetic_tool_call(wire: Wire) -> None:
    server = scripted(
        wire,
        wire.reply(BAD, prompt=120, output=9, cached=0),
        wire.reply(GOOD, prompt=150, output=14, cached=96),
    )
    completion = fallback(wire, server).complete(task_request())

    assert len(server.requests) == 2
    first, second = (recorded.body for recorded in server.requests)
    expected = action_schema([READ, GREP, EDIT])
    assert wire.schema(first) == expected  # the constraint, in this format's own field
    assert wire.schema(second) == expected
    assert "tools" not in first and "tool_choice" not in first
    prompt = wire.text(first)
    assert "# Tools\n- read(path: string, start?: integer, end?: integer)" in prompt
    assert action_text("grep", {"pattern": "def add"}) in prompt
    assert "Result of grep (call 1):\ncalc.py:1:def add(a, b):" in prompt
    repair = wire.text(second)
    assert repair.startswith(prompt.removesuffix("<|im_start|>assistant\n"))
    assert BAD in repair
    assert "Your last reply could not be used: no JSON object found" in repair

    response = completion.response
    [call] = response.message.content
    assert isinstance(call, ToolCallPart)
    assert (call.id, call.name, call.synthetic) == (synthetic_call_id(1, 0), "read", True)
    assert json.loads(call.arguments) == {"path": "calc.py"}
    assert response.finish_reason == "tool_calls"
    usage = response.usage
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens, usage.cached_input_tokens) == (270, 23, 96)
    assert usage.raw is not None and len(usage.raw["attempts"]) == 2
    assert completion.attempts == 2
    assert completion.latency_s >= 0
    [note] = tool_losses(completion)
    assert "3 tool(s) offered through the fallback tool protocol" in note
    assert "constraint json_schema, 1 repair(s)" in note
    assert all(loss.action != "rejected" for loss in completion.report.losses)


@pytest.mark.parametrize("wire", WIRES, ids=lambda wire: wire.fmt)
def test_fallback_plain_path_without_tools(wire: Wire) -> None:
    server = scripted(wire, wire.reply("Hello there."))
    request = Request(
        model="test-model",
        system=[TextPart(text="You are terse.")],
        messages=[Message.text("user", "Say hello.")],
    )
    completion = fallback(wire, server).complete(request)
    body = server.last.body
    assert wire.schema(body) is None
    assert "# Tools" not in wire.text(body)
    assert completion.response.message.text_content() == "Hello there."
    assert completion.response.finish_reason == "stop"
    assert tool_losses(completion) == []


def test_fallback_final_answer_keeps_reasoning() -> None:
    answer = "Fixed: add() returns a + b."
    server = scripted(OLLAMA, ollama_reply(final_text(answer), reasoning="The tests pass now."))
    completion = fallback(OLLAMA, server).complete(task_request())
    message = completion.response.message
    assert [type(part) for part in message.content] == [ReasoningPart, TextPart]
    assert message.text_content() == answer
    assert completion.response.finish_reason == "stop"


def test_fallback_parses_only_the_answer_not_the_reasoning() -> None:
    draft = '{"tool": "grep", "arguments": {"pattern": "x"}}'
    server = scripted(LLAMACPP, llamacpp_reply(GOOD, reasoning=f"Perhaps {draft}? No."))
    completion = fallback(LLAMACPP, server).complete(task_request())
    reasoning, call = completion.response.message.content
    assert isinstance(reasoning, ReasoningPart) and draft in (reasoning.text or "")
    assert isinstance(call, ToolCallPart) and call.name == "read"


def test_fallback_gives_up_after_max_repairs_and_returns_the_text() -> None:
    last = '{"tool": "nope", "arguments": {}}'
    server = scripted(CHAT, chat_reply("no idea"), chat_reply("still prose"), chat_reply(last))
    completion = fallback(CHAT, server, max_repairs=2).complete(task_request())
    assert len(server.requests) == 3
    third = server.requests[2].body["messages"]
    assert [message["role"] for message in third[-4:]] == ["assistant", "user"] * 2
    assert completion.response.message.text_content() == last
    assert completion.response.finish_reason == "stop"
    notes = tool_losses(completion)
    assert any("constraint json_schema, 2 repair(s)" in note for note in notes)
    assert any(
        'no valid action after 2 repair(s): unknown tool "nope"' in note
        and "the reply text is returned" in note
        for note in notes
    )


def test_fallback_without_repairs_makes_one_attempt() -> None:
    server = scripted(CHAT, chat_reply(BAD))
    completion = fallback(CHAT, server, max_repairs=0).complete(task_request())
    assert len(server.requests) == 1
    assert completion.response.message.text_content() == BAD


def test_fallback_retries_unconstrained_when_the_endpoint_rejects_the_schema() -> None:
    server = MockServer()

    def respond(recorded: Recorded) -> httpx.Response:
        if "response_format" in recorded.body:
            error = {"message": "JSON schema conversion failed", "type": "invalid_request_error"}
            return json_response({"error": error}, 400)
        return json_response(chat_reply(GOOD))

    server.route("POST", CHAT.path, respond)
    completion = fallback(CHAT, server).complete(task_request())
    assert len(server.requests) == 2
    assert "response_format" not in server.requests[1].body
    assert completion.response.message.tool_calls()[0].name == "read"
    assert completion.attempts == 2
    notes = tool_losses(completion)
    assert any("constraint none, 0 repair(s)" in note for note in notes)
    assert any("rejected the json_schema constraint" in note for note in notes)


def test_fallback_other_errors_propagate() -> None:
    server = scripted(CHAT, {"error": {"message": "bad key"}}, status=401)
    with pytest.raises(Exception, match="bad key"):
        fallback(CHAT, server).complete(task_request())
    assert len(server.requests) == 1


def test_fallback_drops_the_constraint_when_it_swallowed_the_answer() -> None:
    """An engine that constrains from the first token leaves the JSON inside the reasoning."""
    server = scripted(CHAT, chat_reply("", reasoning=GOOD), chat_reply(GOOD))
    completion = fallback(CHAT, server).complete(task_request())
    assert "response_format" in server.requests[0].body
    assert "response_format" not in server.requests[1].body
    assert completion.response.message.tool_calls()[0].name == "read"
    assert any("had reasoning but no answer" in note for note in tool_losses(completion))


def test_fallback_accepts_a_native_call_the_server_parsed_anyway() -> None:
    native = [
        {
            "id": "call_srv",
            "type": "function",
            "function": {"name": "read", "arguments": '{"path": "calc.py"}'},
        }
    ]
    server = scripted(CHAT, chat_reply(None, tool_calls=native))
    completion = fallback(CHAT, server).complete(task_request())
    [call] = completion.response.message.content
    assert isinstance(call, ToolCallPart)
    assert (call.id, call.name, call.synthetic) == (synthetic_call_id(1, 0), "read", True)


@pytest.mark.parametrize(
    ("constraint", "expected"),
    [("json_object", {"type": "json_object"}), ("none", None)],
)
def test_fallback_explicit_constraints(constraint: Any, expected: Any) -> None:
    server = scripted(CHAT, chat_reply(GOOD))
    model = fallback(CHAT, server, constraint=constraint)
    assert model.constraint == constraint
    model.complete(task_request())
    assert server.last.body.get("response_format") == expected


def test_fallback_text_completion_defaults_to_validate_and_repair() -> None:
    server = MockServer()
    server.route("POST", "/v1/completions", lambda _r: json_response(text_reply(GOOD)))
    model = FallbackModel(client_for("text-completion", server))
    assert model.constraint == "none"
    completion = model.complete(task_request())
    body = server.last.body
    assert "response_format" not in body
    assert "# Tools\n- read(" in body["prompt"]
    assert "Respond with" not in body["prompt"]  # the codec's own schema text is not added
    assert completion.response.message.tool_calls()[0].name == "read"


def test_fallback_tool_choice_required_and_named() -> None:
    server = scripted(CHAT, chat_reply(final_text("done")), chat_reply(GOOD))
    completion = fallback(CHAT, server).complete(task_request(tool_choice="required"))
    first = server.requests[0].body
    assert CHAT.schema(first) == action_schema([READ, GREP, EDIT], allow_final=False)
    assert "a tool call is required here" in CHAT.text(server.requests[1].body)
    assert completion.response.message.tool_calls()[0].name == "read"

    edit = action_text("edit", {"path": "calc.py", "old": "a - b", "new": "a + b"})
    server = scripted(CHAT, chat_reply(edit))
    completion = fallback(CHAT, server).complete(
        task_request(tool_choice=NamedToolChoice(name="edit"))
    )
    assert CHAT.schema(server.last.body) == action_schema([EDIT], allow_final=False)
    assert completion.response.message.tool_calls()[0].name == "edit"


def test_fallback_tool_history_without_tools_is_sent_as_text() -> None:
    server = scripted(CHAT, chat_reply("Summary: the grep found add()."))
    completion = fallback(CHAT, server).complete(task_request(tools=[], tool_choice=None))
    body = server.last.body
    assert "response_format" not in body and "tools" not in body
    assert all("tool_calls" not in message for message in body["messages"])
    text = CHAT.text(body)
    assert "Result of grep (call 1):" in text and "# Tools" not in text
    assert completion.response.message.text_content() == "Summary: the grep found add()."
    assert any("the request has no tools" in note for note in tool_losses(completion))

    server = scripted(CHAT, chat_reply("No tools needed."))
    completion = fallback(CHAT, server).complete(task_request(tool_choice="none"))
    assert "# Tools" not in CHAT.text(server.last.body)
    assert any("tool_choice is none" in note for note in tool_losses(completion))


def test_fallback_reports_a_replaced_response_format() -> None:
    server = scripted(CHAT, chat_reply(final_text('{"answer": 1}')))
    request = task_request(
        response_format=ResponseFormat(type="json_schema", name="a", json_schema={"type": "object"})
    )
    completion = fallback(CHAT, server).complete(request)
    assert CHAT.schema(server.last.body) == action_schema([READ, GREP, EDIT])
    assert any(loss.field == "response_format" for loss in completion.report.losses)


def test_fallback_renders_the_rewritten_conversation_not_a_stale_prompt() -> None:
    server = scripted(LLAMACPP, llamacpp_reply(GOOD))
    stale = "<|im_start|>user\nFix add() in calc.py.<|im_end|>\n<|im_start|>assistant\n"
    completion = fallback(LLAMACPP, server).complete(task_request(prompt=stale))
    prompt = LLAMACPP.text(server.last.body)
    assert prompt != stale
    assert "# Tools\n- read(" in prompt
    assert "Result of grep (call 1):" in prompt
    assert completion.response.finish_reason == "tool_calls"
    [loss] = [loss for loss in completion.report.losses if loss.field == "prompt"]
    assert loss.action == "dropped"


def test_fallback_rejects_bad_settings() -> None:
    client = client_for("chat-completions", MockServer())
    with pytest.raises(ValueError, match="unknown constraint"):
        FallbackModel(client, constraint="grammar")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_repairs"):
        FallbackModel(client, max_repairs=-1)


# --------------------------------------------------------------------------------------------
# Choosing the protocol and the constraint
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("chat-completions", "json_schema"),
        ("responses", "json_schema"),
        ("messages", "json_schema"),
        ("generate-content", "json_schema"),
        ("ollama-native", "json_schema"),
        ("llamacpp-native", "json_schema"),
        ("text-completion", "none"),
        ("chat", "json_schema"),
        ("text", "none"),
    ],
)
def test_default_constraint_follows_the_codec(fmt: str, expected: str) -> None:
    assert default_constraint(fmt) == expected


@pytest.mark.parametrize(
    ("profile", "registry", "expected"),
    [
        ({"capabilities": {"native_tools": True}}, "fallback", False),
        ({"capabilities": {"native_tools": False}}, "native", True),
        ({"capabilities": {"native_tools": None}}, "native", False),
        ({"capabilities": {"native_tools": None}}, "fallback", True),
        ({"capabilities": {"native_tools": None}}, "none", True),
        ({"checks": {"native_tool_call": {"ok": False}}}, None, True),
        ({"checks": {"native_tool_call": {"ok": True}}}, "none", False),
        ({"checks": {"native_tool_call": {"ok": None}}}, "native", False),
        ({"native_tools": True}, None, False),
        (None, "native", False),
        (None, "fallback", True),
        (None, None, True),
    ],
)
def test_needs_fallback(profile: Any, registry: str | None, expected: bool) -> None:
    assert needs_fallback(profile, registry_tool_calls=registry) is expected


def test_constraint_for_reads_the_probe_profile() -> None:
    assert constraint_for(None, "llamacpp-native") == "json_schema"
    assert constraint_for(None, "text-completion") == "none"
    passed = {
        "checks": {
            "fallback_tool_protocol": {"ok": True, "data": {"constrained": True}},
            "structured_output_under_reasoning": {"ok": True},
        }
    }
    assert constraint_for(passed, "ollama-native") == "json_schema"
    rejected = {"checks": {"fallback_tool_protocol": {"data": {"constraint_rejected": "HTTP 400"}}}}
    assert constraint_for(rejected, "chat-completions") == "none"
    lost = {"checks": {"structured_output_under_reasoning": {"ok": False}}}
    assert constraint_for(lost, "chat-completions") == "none"
    skipped = {"checks": {"structured_output_under_reasoning": {"ok": None}}}
    assert constraint_for(skipped, "chat-completions") == "json_schema"
