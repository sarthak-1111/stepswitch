"""Fallback tool protocol: tools for models without native tool calling (SPEC §4.4, ADR 0003).

The tools are described in the system prompt and the model answers every turn with exactly one
JSON action object (ADR 0003 item 6):

    {"tool": "<tool name>", "arguments": {...}}    call one tool
    {"final": "<answer>"}                           give the answer; the step is done

`FallbackModel` wraps a `ModelClient` so that any text-generating model drives tools this way:

1. `to_protocol_messages` rewrites the history for a model without native tools. Past calls
   become the assistant's JSON action text, results become user messages
   "Result of <tool> (call <n>):", earlier plain answers become `{"final": ...}`, and `tools`,
   `tool_choice`, `parallel_tool_calls` and a pre-rendered `prompt` are dropped.
   `render_protocol` is appended to the system blocks.
2. Where the endpoint constrains output, the reply is constrained to `action_schema(tools)`,
   sent as `ResponseFormat(json_schema)`, which every codec but text-completion maps to its own
   output-constraint field (chat-completions `response_format`, responses `text.format`, messages
   `output_config.format`, generate-content `responseJsonSchema`, Ollama `format`, llama.cpp
   `json_schema`; the text-completion codec only describes a schema in the prompt). The schema
   keeps to the subset that grammar conversion accepts (`GRAMMAR_KEYWORDS`).
3. Every reply is checked by `parse_action` against each tool's full JSON Schema (`jsonschema`).
   An invalid reply goes back to the model with a short repair hint, at most `max_repairs` times.
4. The result is an ordinary IR response: one `ToolCallPart` with a synthetic ID for a tool
   action, or the answer as text, so the rest of Stepswitch does not care which protocol a
   worker used.

Forced tool choice and assistant prefill are never used. Reasoning parts are left alone: only
the answer text is parsed, and the constraint applies to the answer.

Facts this relies on, checked 2026-10-16 against llama.cpp v0.7.0 and Ollama v0.40.5:

* llama.cpp turns a JSON Schema into a grammar with `common/json-schema.cpp` and
  `common/json-schema-to-grammar.cpp` (limits listed in `grammars/README.md`, "JSON Schemas ->
  GBNF"). Read: same-document `$ref`, `anyOf` and `oneOf` (both as a union; keywords beside them
  are ignored, so `properties` must not sit next to `anyOf`), `allOf`, `type` (a name or a list),
  `const`, `enum`, `properties`, `required`, `additionalProperties` (absent means no extra keys
  once `properties` is given), `items`, `prefixItems`, `minItems`, `maxItems`, `pattern` (only
  anchored with ^ and $), `format` (date, time, date-time, uuid), `minLength`, `maxLength`, and
  integer bounds. Other keywords are skipped silently. A boolean subschema, an empty `enum`, a
  non-integer count, a non-numeric bound, an unknown type name, a remote `$ref` or an invalid
  regex fails the request. Object keys keep their order (`common/json.h`), so the grammar emits
  "tool" before "arguments".
* llama.cpp chat-completions (and the responses and messages routes, which convert to it): a
  `response_format` schema goes into the chat session, and for templates handled by the
  differential autoparser the grammar is an optional reasoning block, then the schema, optionally
  inside a json code fence; it is not lazy and is fed the generation prompt first
  (`common/chat-auto-parser-generator.cpp`, `common/sampling.cpp`,
  `tools/server/server-common.cpp`). So the constraint starts after the reasoning block there.
* llama.cpp `/completion`: `json_schema` becomes an output-format grammar applied from the first
  generated token (`tools/server/server-schema.cpp`, `common/sampling.cpp`); a reasoning block
  cannot come first unless the request also sets `grammar_lazy` with `grammar_triggers`. A
  constrained reply that carries reasoning but no answer is therefore retried unconstrained.
* Ollama runs llama-server: a `format` schema is passed on as llama-server's `json_schema` (or
  as `response_format` on its chat-template path), so Ollama accepts the same subset. When a
  response starts with thinking, Ollama converts the schema to a grammar and wraps it so that the
  text before the closing tag is free (`llm/llama_server.go`, `llm/gbnf.go` `thinkingGrammar`).
  Its MLX runner path was not checked.

Capability profile keys read here, as written by `ssw probe` (SPEC §4.6; `stepswitch.compat.probe`
is not imported): `capabilities.native_tools` (else `checks.native_tool_call.ok`, else a flat
`native_tools`), `checks.fallback_tool_protocol.data.constraint_rejected` and
`checks.structured_output_under_reasoning.ok`. A check's `ok` is True (passed), False (failed)
or None (skipped or not run).
"""

from __future__ import annotations

import ast
import difflib
import functools
import importlib
import json
import re
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Final, Literal
from urllib.parse import unquote

from referencing.exceptions import Unresolvable

from stepswitch.compat.client import Completion, ModelClient
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import (
    FORMATS,
    ConversionReport,
    FinishReason,
    ImagePart,
    Message,
    NamedToolChoice,
    Part,
    ReasoningPart,
    Request,
    Response,
    ResponseFormat,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
    synthetic_call_id,
)

# jsonschema ships no type information; reach it through the module object.
_jsonschema: Any = importlib.import_module("jsonschema")
# The bundled metaschemas and nothing else: jsonschema's default registry would fetch a remote
# `$ref` over the network while validating (working rule 6). Such a ref is unresolvable here.
_OFFLINE_REFS: Any = importlib.import_module("jsonschema_specifications").REGISTRY

Constraint = Literal["json_schema", "json_object", "none"]
CONSTRAINTS: tuple[Constraint, ...] = ("json_schema", "json_object", "none")

# Formats whose codec sends ResponseFormat(json_schema) as an output constraint. The
# text-completion codec only describes the schema in the system prompt, which would repeat the
# protocol, so its default is "none".
CONSTRAINED_FORMATS: frozenset[str] = frozenset(
    {
        "chat-completions",
        "responses",
        "messages",
        "generate-content",
        "ollama-native",
        "llamacpp-native",
    }
)
_PUBLIC_FORMAT = {key: name for name, key in FORMATS.items()}

# `parse_action` returns this name, with {"answer": <text>}, for a final answer. No tool can be
# called this: tool names are letters, digits, "_", "-" (and "." or ":" in one format).
FINAL: Final = "(final)"
ACTION_SCHEMA_NAME: Final = "action"

# JSON Schema keywords `action_schema` may emit (the grammar-safe subset, see the module doc).
GRAMMAR_KEYWORDS: frozenset[str] = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "enum",
        "const",
        "anyOf",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
    }
)
JSON_TYPES: tuple[str, ...] = ("object", "array", "string", "integer", "number", "boolean", "null")

REPAIR_PROMPT: Final = (
    "Your last reply could not be used: {error}. Reply with exactly one JSON object and nothing "
    "else."
)
MAX_BAD_REPLY_CHARS = 2_000  # a bad reply sent back for repair is cut to this length

_MAX_DEPTH = 24  # schema nesting kept in the grammar schema
_MAX_REFS = 16  # $ref expansions along one path
_MAX_COUNT = 4_096  # larger length or item counts make huge grammars; validation keeps them
_MAX_CANDIDATES = 32  # "{" positions tried per reply
_MAX_SCAN_CHARS = 200_000
_MAX_NESTING = 200  # arrays and objects inside each other in one JSON value
_REJECTION_KINDS = frozenset({"bad_request", "unsupported"})

_MISSING: Final = object()
_UNCHECKED: Final = object()  # a validation that could not be carried out

# --------------------------------------------------------------------------------------------
# Action schema
# --------------------------------------------------------------------------------------------


def action_schema(tools: Sequence[ToolSpec], *, allow_final: bool = True) -> dict[str, Any]:
    """JSON Schema of one action: an `anyOf` with one branch per tool, `{"tool": <name>,
    "arguments": <the tool's parameters, grammar-safe>}`, plus the final-answer branch
    `{"final": <string>}` unless `allow_final` is False. Every branch is a closed object with
    both keys required. A single branch is returned without the `anyOf`."""
    branches: list[dict[str, Any]] = [
        {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "enum": [tool.name]},
                "arguments": arguments_schema(tool),
            },
            "required": ["tool", "arguments"],
            "additionalProperties": False,
        }
        for tool in tools
    ]
    if allow_final:
        branches.append(
            {
                "type": "object",
                "properties": {"final": {"type": "string"}},
                "required": ["final"],
                "additionalProperties": False,
            }
        )
    if not branches:
        raise ValueError("an action needs at least one tool or the final answer")
    return branches[0] if len(branches) == 1 else {"anyOf": branches}


def arguments_schema(tool: ToolSpec) -> dict[str, Any]:
    """`tool.parameters` reduced to the grammar-safe subset, always an object schema.

    Local `$ref`s are inlined (a recursive one accepts any value), `allOf` members are merged,
    `oneOf` becomes `anyOf`, and `anyOf` beside `properties` is dropped. Keywords outside
    `GRAMMAR_KEYWORDS` are dropped (pattern, format, descriptions, ...), as are values grammar
    conversion would reject. Objects that list `properties` get `additionalProperties: false`
    unless they allow more. The result accepts at most what the full schema accepts, except
    where a dropped keyword narrowed it; `parse_action` validates against the full schema.
    """
    root: Mapping[str, Any] = tool.parameters if isinstance(tool.parameters, Mapping) else {}
    schema = _simplify(root, root, 0, ())
    kind = schema.get("type")
    if kind == "object" or "anyOf" in schema:
        return schema
    if isinstance(kind, list) and "object" in kind:
        return {**schema, "type": "object"}
    return {"type": "object"}


def _types(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value in JSON_TYPES else []
    if isinstance(value, list):
        return list(dict.fromkeys(t for t in value if isinstance(t, str) and t in JSON_TYPES))
    return []


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_COUNT


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _pointer(root: Mapping[str, Any], ref: str) -> Any:
    """The target of a same-document reference ("#" or "#/a/b"), or None."""
    if ref == "#":
        return root
    if not ref.startswith("#/"):
        return None
    target: Any = root
    for raw in ref[2:].split("/"):
        token = unquote(raw).replace("~1", "/").replace("~0", "~")
        if isinstance(target, Mapping) and token in target:
            target = target[token]
        elif isinstance(target, list) and token.isdigit() and int(token) < len(target):
            target = target[int(token)]
        else:
            return None
    return target


def _inline_refs(
    node: Mapping[str, Any], root: Mapping[str, Any], refs: tuple[str, ...]
) -> tuple[Mapping[str, Any], tuple[str, ...]] | None:
    """`node` with its `$ref` chain inlined (siblings of `$ref` win), or None when a reference
    is remote, missing or recursive."""
    while "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or ref in refs or len(refs) >= _MAX_REFS:
            return None
        target = _pointer(root, ref)
        if not isinstance(target, Mapping):
            return None
        node = {**target, **{key: value for key, value in node.items() if key != "$ref"}}
        refs = (*refs, ref)
    return node, refs


def _merge_all_of(
    node: Mapping[str, Any], root: Mapping[str, Any], refs: tuple[str, ...]
) -> dict[str, Any]:
    """`allOf` members folded into the node: properties and required lists are united, other
    keywords fill gaps. Validation keeps the exact semantics."""
    merged = {key: value for key, value in node.items() if key != "allOf"}
    for member in node["allOf"]:
        resolved = _inline_refs(member, root, refs) if isinstance(member, Mapping) else None
        if resolved is None:
            continue
        for key, value in resolved[0].items():
            if key == "properties" and isinstance(value, Mapping):
                own = merged.get("properties")
                merged["properties"] = {**(own if isinstance(own, Mapping) else {}), **value}
            elif key == "required" and isinstance(value, list):
                own = merged.get("required")
                merged["required"] = [*(own if isinstance(own, list) else []), *value]
            elif key != "allOf":
                merged.setdefault(key, value)
    return merged


def _implied_types(node: Mapping[str, Any]) -> list[str]:
    """The type grammar conversion infers for an untyped schema (same order as llama.cpp)."""
    extra = node.get("additionalProperties")
    if "properties" in node or "required" in node or (extra is not None and extra is not True):
        return ["object"]
    if "items" in node or "prefixItems" in node:
        return ["array"]
    if "minLength" in node or "maxLength" in node:
        return ["string"]
    return []


def _simplify(
    node: Any, root: Mapping[str, Any], depth: int, refs: tuple[str, ...]
) -> dict[str, Any]:
    """One subschema reduced to the grammar-safe subset; {} accepts any value."""
    if not isinstance(node, Mapping) or depth > _MAX_DEPTH:
        return {}  # boolean schemas and anything unreadable: any value
    resolved = _inline_refs(node, root, refs)
    if resolved is None:
        return {}
    node, refs = resolved
    if isinstance(node.get("allOf"), list):
        node = _merge_all_of(node, root, refs)
    out: dict[str, Any] = {}
    declared = _types(node.get("type"))
    enum = node.get("enum")
    if "const" in node or (isinstance(enum, list) and enum):
        if declared:
            out["type"] = declared[0] if len(declared) == 1 else declared
        if "const" in node:
            out["const"] = node["const"]
        else:
            out["enum"] = list(enum) if isinstance(enum, list) else []
        return out
    union = node.get("anyOf", node.get("oneOf"))
    if isinstance(union, list) and union and not isinstance(node.get("properties"), Mapping):
        outer = node.get("type")
        branches: list[dict[str, Any]] = []
        for branch in union:
            if outer is not None and isinstance(branch, Mapping) and "type" not in branch:
                branch = {"type": outer, **branch}
            branches.append(_simplify(branch, root, depth + 1, refs))
        if any(not branch for branch in branches):
            return {}  # one branch accepts anything, so the union does
        return {"anyOf": branches}
    types = declared or _implied_types(node)
    if types:
        out["type"] = types[0] if len(types) == 1 else types
    if "object" in types:
        out.update(_object_keywords(node, root, depth, refs))
    if "array" in types:
        items = node.get("items")
        if isinstance(items, Mapping):
            simple = _simplify(items, root, depth + 1, refs)
            if simple:
                out["items"] = simple
        elif items is False and "prefixItems" not in node:  # beside a tuple: no extra items
            out["maxItems"] = 0
        for key in ("minItems", "maxItems"):
            if key not in out and _is_count(node.get(key)):
                out[key] = node[key]
    if "string" in types:
        for key in ("minLength", "maxLength"):
            if _is_count(node.get(key)):
                out[key] = node[key]
    if "integer" in types:
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if _is_number(node.get(key)):
                out[key] = node[key]
    return out


def _object_keywords(
    node: Mapping[str, Any], root: Mapping[str, Any], depth: int, refs: tuple[str, ...]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    raw = node.get("properties")
    properties: dict[str, Any] = {}
    if isinstance(raw, Mapping):
        for name, sub in raw.items():
            if sub is not False:  # `false`: the property may not appear
                properties[str(name)] = _simplify(sub, root, depth + 1, refs)
    listed = node.get("required")
    names = listed if isinstance(listed, list) else []
    required = list(dict.fromkeys(name for name in names if isinstance(name, str)))
    for name in required:
        properties.setdefault(name, {})  # required but not described: any value
    extra = node.get("additionalProperties")
    if isinstance(raw, Mapping) or required:
        out["properties"] = properties
        if required:
            out["required"] = required
        if isinstance(extra, Mapping):
            out["additionalProperties"] = _simplify(extra, root, depth + 1, refs) or True
        else:
            out["additionalProperties"] = extra is True
    elif extra is False:
        out["properties"] = {}
        out["additionalProperties"] = False
    elif isinstance(extra, Mapping):
        simple = _simplify(extra, root, depth + 1, refs)
        if simple:
            out["additionalProperties"] = simple
    return out


# --------------------------------------------------------------------------------------------
# Prompt section
# --------------------------------------------------------------------------------------------


def action_text(name: str, arguments: Mapping[str, Any]) -> str:
    """The JSON action that calls `name`, as prompts and rewritten history show it."""
    return json.dumps({"tool": name, "arguments": dict(arguments)}, ensure_ascii=False)


def final_text(answer: str) -> str:
    """The JSON action that gives a final answer."""
    return json.dumps({"final": answer}, ensure_ascii=False)


def result_text(name: str, number: int | str, output: str, *, error: bool = False) -> str:
    """How a tool result reads in the rewritten history."""
    mark = " [error]" if error else ""
    return f"Result of {name} (call {number}){mark}:\n{output or '(no output)'}"


def render_protocol(
    tools: Sequence[ToolSpec], *, examples: bool = True, allow_final: bool = True
) -> str:
    """The system-prompt section for the fallback protocol: one line per tool (name, argument
    names and types, one-line description), the reply format and two short worked examples
    with the real tool names. Small models (1-4B, 4k context) read it every step, so it stays
    compact. With `allow_final` False the final-answer form is left out (a tool is required)."""
    lines = ["# Tools"]
    optional = False
    for tool in tools:
        line, has_optional = _tool_line(tool)
        optional = optional or has_optional
        lines.append(line)
    if optional:
        lines.append('("?" marks an optional argument.)')
    lines += [
        "",
        "# Reply format",
        "Reply with exactly one JSON object and no other text:",
        '- to use a tool: {"tool": "<tool name>", "arguments": {"<argument>": <value>}}',
    ]
    if allow_final:
        lines.append('- to give your final answer: {"final": "<answer>"}')
    lines.append(
        "Use one tool per reply. Its result comes back in the next message, starting"
        ' "Result of <tool> (call <n>):".'
    )
    if examples and tools:
        lines += ["", "# Examples", *_examples(tools, allow_final)]
    return "\n".join(lines)


def _tool_line(tool: ToolSpec) -> tuple[str, bool]:
    schema = arguments_schema(tool)
    properties = schema.get("properties")
    optional = False
    if isinstance(properties, Mapping) and properties:
        required = set(schema.get("required") or [])
        arguments = []
        for name, sub in properties.items():
            mark = "" if name in required else "?"
            optional = optional or bool(mark)
            arguments.append(f"{name}{mark}: {_type_text(sub)}")
        signature = ", ".join(arguments)
    elif isinstance(properties, Mapping) or schema.get("additionalProperties") is False:
        signature = ""
    else:
        signature = "..."  # a free-form object
    line = f"- {tool.name}({signature})"
    description = _one_line(tool.description)
    line = f"{line}: {description}" if description else line
    if tool.examples:
        # One real call teaches a small model more than the signature: the shape of nested
        # arguments, and what values look like.
        line += f"\n  example: {_compact_json(tool.examples[0])}"
    return line, optional


def _type_text(schema: Mapping[str, Any], depth: int = 0) -> str:
    return "|".join(_type_parts(schema, depth))


def _type_parts(schema: Mapping[str, Any], depth: int) -> list[str]:
    """The alternatives of a type, for example `["string", "integer"]`, so an array of a union
    can be put in parentheses and an array of one object cannot."""
    if "const" in schema:
        return [_short_json(schema["const"])]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        shown = [_short_json(value) for value in enum[:8]]
        return shown + (["..."] if len(enum) > 8 else [])
    union = schema.get("anyOf")
    if isinstance(union, list) and union:
        parts = [
            part
            for branch in union
            if isinstance(branch, Mapping)
            for part in _type_parts(branch, depth + 1)
        ]
        return list(dict.fromkeys(parts)) or ["any"]
    types = _types(schema.get("type"))
    if not types:
        return ["any"]
    shown_types = []
    for kind in types:
        items = schema.get("items")
        if kind == "array" and isinstance(items, Mapping) and items and depth < 3:
            inner = _type_parts(items, depth + 1)
            text = "|".join(inner)
            shown_types.append(f"({text})[]" if len(inner) > 1 else f"{text}[]")
        elif kind == "object" and depth < 3:
            shown_types.append(_object_text(schema, depth + 1) or kind)
        else:
            shown_types.append(kind)
    return shown_types


def _object_text(schema: Mapping[str, Any], depth: int) -> str:
    """`{name: type, name?: type}` for an object with known fields (at most eight)."""
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        return ""
    required = set(schema.get("required") or [])
    fields = [
        f"{name}{'' if name in required else '?'}: {_type_text(sub, depth)}"
        for name, sub in list(properties.items())[:8]
        if isinstance(sub, Mapping)
    ]
    return "{" + ", ".join(fields) + (", ..." if len(properties) > 8 else "") + "}"


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _short_json(value: Any, limit: int = 40) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _one_line(text: str | None, limit: int = 120) -> str:
    if not text:
        return ""
    line = next((row.strip() for row in text.strip().splitlines() if row.strip()), "")
    line = " ".join(line.split())
    if len(line) > limit:
        line = line[: limit - 3].rsplit(" ", 1)[0].rstrip(",.;: ") + "..."
    return line


def _examples(tools: Sequence[ToolSpec], allow_final: bool) -> list[str]:
    first = tools[0]
    lines = [
        "Example 1",
        f"User: <a request that needs {first.name}>",
        f"You: {action_text(first.name, _example_arguments(first))}",
        f"User: Result of {first.name} (call 1):",
        f"<what {first.name} returned>",
    ]
    if allow_final:
        lines.append(f"You: {final_text('<answer based on that result>')}")
    if len(tools) > 1:
        second = tools[1]
        lines += [
            "Example 2",
            f"User: <a request that needs {second.name}>",
            f"You: {action_text(second.name, _example_arguments(second))}",
        ]
    elif allow_final:
        lines += [
            "Example 2",
            "User: <a question you can answer without tools>",
            f"You: {final_text('<answer>')}",
        ]
    return lines


def _example_arguments(tool: ToolSpec) -> dict[str, Any]:
    if tool.examples:
        return dict(tool.examples[0])
    schema = arguments_schema(tool)
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        return {}
    required = schema.get("required") or []
    names = [name for name in properties if name in required] or [next(iter(properties))]
    return {name: _example_value(name, properties[name]) for name in names}


def _example_value(name: str, schema: Mapping[str, Any]) -> Any:
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    union = schema.get("anyOf")
    if isinstance(union, list) and union and isinstance(union[0], Mapping):
        return _example_value(name, union[0])
    types = _types(schema.get("type"))
    kind = types[0] if types else "string"
    if kind == "integer":
        minimum = schema.get("minimum")
        return minimum if isinstance(minimum, int) and not isinstance(minimum, bool) else 1
    if kind == "number":
        return 1
    if kind == "boolean":
        return True
    if kind == "null":
        return None
    if kind == "object":
        return {}
    if kind == "array":
        items = schema.get("items")
        return [_example_value(name, items)] if isinstance(items, Mapping) and items else []
    return f"<{name}>"


# --------------------------------------------------------------------------------------------
# History rewriting
# --------------------------------------------------------------------------------------------


def offered_tools(request: Request) -> tuple[list[ToolSpec], bool]:
    """The tools the protocol offers for `request`, and whether a final answer is allowed:
    `tool_choice` "required" or a named tool forbids it, "none" offers no tools."""
    choice = request.tool_choice
    if not request.tools or choice == "none":
        return [], False
    if isinstance(choice, NamedToolChoice):
        named = [tool for tool in request.tools if tool.name == choice.name]
        if not named:
            raise ValueError(f"tool_choice names {choice.name!r}, which is not among the tools")
        return named, False
    return list(request.tools), choice != "required"


def to_protocol_messages(request: Request) -> Request:
    """`request` rewritten for a model without native tools.

    * Assistant tool calls become the assistant's JSON action text, one action per line after
      any text of the message (several calls only come from models with parallel calls).
    * Tool results (in `tool` messages, or in user messages as some formats send them) become a
      user message `Result of <tool> (call <n>):\\n<output>`, marked `[error]` for errors;
      calls are numbered 1, 2, ... in order of appearance, and consecutive results share one
      message, together with user text that directly follows them. Images in results stay as
      image parts.
    * Earlier plain assistant answers become `{"final": ...}` when the protocol allows final
      answers, so the history matches the reply format (a trailing assistant message stays).
    * `tools`, `tool_choice` and `parallel_tool_calls` are dropped and `render_protocol` is
      appended to the system blocks when tools are offered. A pre-rendered `prompt` is dropped
      too: it shows the old conversation (render this request again to use the model's own
      template). Everything else is kept, reasoning parts included.
    """
    tools, allow_final = offered_tools(request)
    numbers: dict[str, int] = {}
    names: dict[str, str] = {}
    messages: list[Message] = []
    after_results = False
    last = len(request.messages) - 1
    for index, message in enumerate(request.messages):
        if message.role == "assistant":
            wrap = bool(tools) and allow_final and index != last
            messages.append(_protocol_assistant(message, numbers, names, wrap))
            after_results = False
        elif message.role == "tool" or message.tool_results():
            converted = _protocol_results(message, numbers, names)
            if after_results and messages:
                messages[-1] = _merge_user(messages[-1], converted)
            else:
                messages.append(converted)
            after_results = True
        elif after_results and message.role == "user" and messages:
            # Text right after results joins them: roles keep alternating, which some
            # templates require.
            messages[-1] = _merge_user(messages[-1], message)
        else:
            messages.append(message)
            after_results = False
    system = list(request.system)
    if tools:
        system.append(TextPart(text=render_protocol(tools, allow_final=allow_final)))
    return request.model_copy(
        update={
            "system": system,
            "messages": messages,
            "tools": [],
            "tool_choice": None,
            "parallel_tool_calls": None,
            "prompt": None,
        }
    )


def _call_text(call: ToolCallPart) -> str:
    try:
        arguments = call.parsed_arguments()
    except ValueError:  # not JSON, or not an object
        arguments = {"input": call.arguments}
    return action_text(call.name, arguments)


def _protocol_assistant(
    message: Message, numbers: dict[str, int], names: dict[str, str], wrap_final: bool
) -> Message:
    calls = message.tool_calls()
    if not calls and not (wrap_final and message.text_content().strip()):
        return message
    if calls:
        pieces: list[str] = []
        for part in message.content:
            if isinstance(part, TextPart) and part.text.strip():
                pieces.append(part.text.strip())
            elif isinstance(part, ToolCallPart):
                numbers.setdefault(part.id, len(numbers) + 1)
                names[part.id] = part.name
                pieces.append(_call_text(part))
        text = "\n".join(pieces)
    else:
        text = final_text(message.text_content())
    content: list[Part] = []
    placed = False
    for part in message.content:
        if isinstance(part, TextPart | ToolCallPart):
            if not placed:
                content.append(TextPart(text=text))
                placed = True
        else:
            content.append(part)
    return message.model_copy(update={"content": content})


def _protocol_results(message: Message, numbers: dict[str, int], names: dict[str, str]) -> Message:
    texts: list[str] = []
    others: list[Part] = []
    for part in message.content:
        if isinstance(part, ToolResultPart):
            number: int | str = numbers.get(part.call_id, part.call_id)
            name = names.get(part.call_id) or part.name or "tool"
            texts.append(result_text(name, number, part.text(), error=part.is_error))
            others.extend(item for item in part.content if isinstance(item, ImagePart))
        elif isinstance(part, TextPart):
            if part.text:
                texts.append(part.text)
        else:
            others.append(part)
    content: list[Part] = [TextPart(text="\n\n".join(texts))] if texts else []
    content.extend(others)
    return Message(
        role="user",
        content=content,
        name=message.name if message.role == "user" else None,
        metadata=dict(message.metadata),
    )


def _merge_user(first: Message, second: Message) -> Message:
    parts = [*first.content, *second.content]
    texts = [part.text for part in parts if isinstance(part, TextPart)]
    content: list[Part] = [TextPart(text="\n\n".join(texts))] if texts else []
    content.extend(part for part in parts if not isinstance(part, TextPart))
    return first.model_copy(
        update={
            "content": content,
            "name": first.name or second.name,
            "metadata": {**first.metadata, **second.metadata},
        }
    )


def _has_tool_parts(messages: Sequence[Message]) -> bool:
    return any(
        message.role == "tool"
        or any(isinstance(part, ToolCallPart | ToolResultPart) for part in message.content)
        for message in messages
    )


# --------------------------------------------------------------------------------------------
# Parsing and validation
# --------------------------------------------------------------------------------------------


class ActionError(ValueError):
    """A reply that is not one valid action. The message is a short, specific repair hint."""


_LEADING_THINK_RE = re.compile(r"\s*<(think|thinking)>.*?</\1>", re.S | re.I)
_THINK_END_RE = re.compile(r"</(?:think|thinking)>", re.I)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_INTEGER_RE = re.compile(r"[+-]?\d+")
_NUMBER_RE = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")

_FINAL_KEYS = ("final", "final_answer", "answer")
_NAME_KEYS = ("tool", "name", "tool_name", "function", "action")
_ARGUMENT_KEYS = ("arguments", "parameters", "args", "input", "params", "action_input")
_WRAPPER_KEYS = ("tool", "tool_call", "function_call", "call", "function", "action")
_IGNORED_KEYS = frozenset({"thought", "thoughts", "reasoning", "explanation"})
_FINAL_ACTIONS = frozenset({"final answer", "final_answer", "final", "finish"})


def parse_action(
    text: str, tools: Sequence[ToolSpec], *, allow_final: bool = True
) -> tuple[str, dict[str, Any]]:
    """The action in a model reply: (tool name, arguments), or (`FINAL`, {"answer": text}).

    Tolerant of what small models write around the object: code fences, prose before or after,
    think blocks left in the text, Python-style dicts, trailing commas, the common variants
    `{"name": ..., "arguments" | "parameters" | "args" | "input": ...}`, wrappers such as
    `{"function": {...}}`, `{"action": ..., "action_input": ...}`, arguments sent as a JSON
    string or at the top level, and tool names that differ in case or "-"/"_". The first
    object that looks like an action is used. Arguments are tidied (null optional arguments are
    dropped; numbers and booleans sent as strings are converted where the schema says so) and
    validated with `jsonschema` against the tool's full parameters schema.

    Raises `ActionError` with a short repair hint.
    """
    answer = _answer_text(text)[:_MAX_SCAN_CHARS]
    if not answer.strip():
        raise ActionError(f"the reply is empty; {_expected(tools, allow_final)}")
    json_error: str | None = None
    found_object = unclosed = False
    broken_until = -1  # objects inside a block that is not JSON are fragments of it
    for tried, (start, end) in enumerate(_object_spans(answer)):
        if tried >= _MAX_CANDIDATES:
            break
        if end < 0:
            unclosed = True
            broken_until = max(broken_until, len(answer))
            continue
        value, error = _load(answer[start:end])
        if error is not None:
            json_error = json_error or error
            broken_until = max(broken_until, end)
            continue
        if not isinstance(value, dict):
            continue
        found_object = found_object or start >= broken_until
        shape = _shape(value)
        if shape is not None:
            return _checked(shape, tools, allow_final)
    if found_object:
        raise ActionError(f'the JSON object has no "tool" key; {_expected(tools, allow_final)}')
    if json_error is not None:
        raise ActionError(f"the JSON is not valid ({json_error}); reply with one valid JSON object")
    if unclosed:
        raise ActionError(
            "the JSON object is not closed (was the reply cut off?); reply with one complete,"
            " short JSON object"
        )
    raise ActionError(f"no JSON object found; {_expected(tools, allow_final)}")


def _expected(tools: Sequence[ToolSpec], allow_final: bool) -> str:
    name = tools[0].name if tools else "<tool name>"
    hint = f'reply with one JSON object such as {{"tool": "{name}", "arguments": {{...}}}}'
    return hint + (' or {"final": "<answer>"}' if allow_final else "")


def _answer_text(text: str) -> str:
    """The reply without a leading think block that a server left in the answer text (or the
    end of one whose opening tag was in the prompt). Tags after the first "{" are content."""
    match = _LEADING_THINK_RE.match(text)
    if match:
        return text[match.end() :]
    brace = text.find("{")
    ends = list(_THINK_END_RE.finditer(text if brace < 0 else text[:brace]))
    return text[ends[-1].end() :] if ends else text


def _object_spans(text: str) -> Iterator[tuple[int, int]]:
    """(start, end) of the balanced `{...}` block at each "{" in order; end is -1 when the
    block never closes. Braces inside JSON strings do not count."""
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = escaped = False
        end = -1
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        yield start, end
        start = text.find("{", start + 1)


def _too_deep(span: str) -> bool:
    """Whether arrays and objects in `span` nest more than _MAX_NESTING deep. Brackets inside
    JSON strings do not count. The limit is set here because the depth at which `json.loads`
    gives up varies: Python 3.14 bounds it by the thread's stack, not by a fixed count."""
    if span.count("[") + span.count("{") <= _MAX_NESTING:
        return False
    depth = 0
    in_string = escaped = False
    for char in span:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > _MAX_NESTING:
                return True
        elif char in "]}":
            depth -= 1
    return False


def _load(span: str) -> tuple[Any, str | None]:
    """A JSON value from one candidate span, or the reason it is not one."""
    if _too_deep(span):
        return None, "it is nested too deeply"
    try:
        return json.loads(span, strict=False), None
    except json.JSONDecodeError as exc:
        error = f"{exc.msg} at character {exc.pos}"
    except RecursionError:
        return None, "it is nested too deeply"
    except ValueError:  # the interpreter's limit on the digits of an integer
        return None, "it has a number with too many digits"
    fixed = _TRAILING_COMMA_RE.sub(r"\1", span)
    if fixed != span:
        try:
            return json.loads(fixed, strict=False), None
        except (ValueError, RecursionError):
            pass
    try:  # a Python-style dict: single quotes, True/False/None
        value = ast.literal_eval(span)
        return json.loads(json.dumps(value)), None
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None, error


def _shape(obj: Mapping[str, Any], depth: int = 0) -> tuple[str, Any, Any] | None:
    """("final", answer, None) or ("tool", name, raw arguments) for an action-shaped object."""
    if not any(isinstance(obj.get(key), str) for key in ("tool", "name", "tool_name")):
        for key in _FINAL_KEYS:
            if key in obj:
                return "final", obj[key], None
    action = obj.get("action")
    if isinstance(action, str) and action.strip().lower() in _FINAL_ACTIONS:
        return "final", obj.get("action_input", obj.get("input", "")), None
    for key in _NAME_KEYS:
        name = obj.get(key)
        if not isinstance(name, str):
            continue
        for argument_key in _ARGUMENT_KEYS:
            if argument_key in obj:
                return "tool", name, obj[argument_key]
        # No arguments key: the arguments may sit at the top level, next to the name.
        rest = {
            k: v
            for k, v in obj.items()
            if k != key and k not in _IGNORED_KEYS and (k, v) != ("type", "function")
        }
        return "tool", name, rest if rest else _MISSING
    if depth < 2:
        for key in _WRAPPER_KEYS:
            inner = obj.get(key)
            if isinstance(inner, Mapping):
                found = _shape(inner, depth + 1)
                if found is not None:
                    return found
    return None


def _checked(
    shape: tuple[str, Any, Any], tools: Sequence[ToolSpec], allow_final: bool
) -> tuple[str, dict[str, Any]]:
    kind, value, raw_arguments = shape
    if kind == "final":
        if not allow_final:
            raise ActionError(
                'a tool call is required here; reply {"tool": "<tool name>", "arguments": {...}}'
            )
        answer = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return FINAL, {"answer": answer}
    tool = _resolve_tool(str(value), tools)
    arguments = _tidy(_arguments(raw_arguments, tool), tool.parameters)
    _validate(tool, arguments)
    return tool.name, arguments


def _normalized_name(name: str) -> str:
    name = name.strip().lower().replace("-", "_").replace(" ", "_")
    return name.removeprefix("functions.")


def _resolve_tool(name: str, tools: Sequence[ToolSpec]) -> ToolSpec:
    for tool in tools:
        if tool.name == name:
            return tool
    key = _normalized_name(name)
    matches = [tool for tool in tools if _normalized_name(tool.name) == key]
    if len(matches) == 1:
        return matches[0]
    known = [tool.name for tool in tools]
    hint = f'unknown tool "{name}"'
    close = difflib.get_close_matches(name, known, n=1)
    if close:
        hint += f' (did you mean "{close[0]}"?)'
    shown = ", ".join(known[:12]) + (", ..." if len(known) > 12 else "")
    raise ActionError(f"{hint}; tools: {shown}")


def _arguments(raw: Any, tool: ToolSpec) -> dict[str, Any]:
    if raw is _MISSING or raw is None:
        return {}
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        value, error = _load(raw.strip())
        if error is not None:
            raise ActionError(
                f'"arguments" for "{tool.name}" is a string that is not a JSON object'
                f" ({error}); send the arguments as a JSON object"
            )
        raw = value
    if not isinstance(raw, dict):
        raise ActionError(
            f'"arguments" for "{tool.name}" must be a JSON object, not {_json_type(raw)}'
        )
    return raw


def _tidy(arguments: dict[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    """Drop null optional arguments the schema does not allow as null, and convert numbers and
    booleans sent as strings where a top-level property's type says so."""
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    if not isinstance(properties, Mapping):
        return arguments
    listed = schema.get("required")
    required = (
        {name for name in listed if isinstance(name, str)} if isinstance(listed, list) else set()
    )
    out = dict(arguments)
    for key, value in arguments.items():
        sub = properties.get(key)
        types = _types(sub.get("type")) if isinstance(sub, Mapping) else []
        if not types:
            continue
        if value is None and key not in required and "null" not in types:
            del out[key]
        elif isinstance(value, str) and "string" not in types:
            converted = _scalar(value.strip(), types)
            if converted is not _MISSING:
                out[key] = converted
    return out


def _scalar(text: str, types: list[str]) -> Any:
    if "integer" in types and _INTEGER_RE.fullmatch(text):
        return int(text)
    if "number" in types and _NUMBER_RE.fullmatch(text):
        number = float(text)
        return int(number) if number.is_integer() and "." not in text else number
    if "boolean" in types and text.lower() in ("true", "false"):
        return text.lower() == "true"
    return _MISSING


@functools.lru_cache(maxsize=128)
def _cached_validator(schema_text: str) -> Any:
    schema = json.loads(schema_text)
    cls = _jsonschema.validators.validator_for(schema, default=_jsonschema.Draft202012Validator)
    try:
        cls.check_schema(schema)
    except _jsonschema.exceptions.SchemaError:
        return None
    return cls(schema, registry=_OFFLINE_REFS)


def _validator(schema: Mapping[str, Any]) -> Any:
    """A validator for `schema`, or None when it is not a valid JSON Schema."""
    try:
        return _cached_validator(json.dumps(schema))
    except (TypeError, ValueError):
        return None


def _validate(tool: ToolSpec, arguments: dict[str, Any]) -> None:
    """Validate against the tool's full schema. A schema that is not valid JSON Schema (an
    unknown type name, say) or that cannot be checked offline (a remote `$ref`) is replaced by
    its grammar-safe form, which always can be."""
    error = _first_error(_validator(tool.parameters), arguments)
    if error is _UNCHECKED:
        error = _first_error(_validator(arguments_schema(tool)), arguments)
    if error is not None and error is not _UNCHECKED:
        raise ActionError(f'arguments for "{tool.name}": {_error_hint(error)}')


def _first_error(validator: Any, arguments: dict[str, Any]) -> Any:
    """The most relevant validation error, None when valid, `_UNCHECKED` when not checkable."""
    if validator is None:
        return _UNCHECKED
    try:
        return _jsonschema.exceptions.best_match(validator.iter_errors(arguments))
    except (Unresolvable, re.error, RecursionError):
        return _UNCHECKED


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _where(path: Sequence[Any]) -> str:
    text = ""
    for item in path:
        text += f"[{item}]" if isinstance(item, int) else (f".{item}" if text else str(item))
    return f'"{text}"' if text else ""


def _quoted(names: Sequence[Any]) -> str:
    return ", ".join(f'"{name}"' for name in names)


def _error_hint(error: Any) -> str:
    """A short repair hint from one `jsonschema` validation error."""
    where = _where(list(error.absolute_path))
    inside = f" in {where}" if where else ""
    kind = error.validator
    instance = error.instance
    if kind == "required" and isinstance(instance, Mapping):
        missing = [name for name in error.validator_value if name not in instance]
        noun = "argument" if len(missing) == 1 else "arguments"
        return f"missing required {noun} {_quoted(missing)}{inside}"
    if kind == "additionalProperties" and isinstance(instance, Mapping):
        properties = error.schema.get("properties")
        allowed = list(properties) if isinstance(properties, Mapping) else []
        extra = [name for name in instance if name not in allowed]
        noun = "argument" if len(extra) == 1 else "arguments"
        hint = f"unknown {noun} {_quoted(extra)}{inside}"
        return hint + (f"; allowed: {', '.join(allowed)}" if allowed else "")
    subject = where or "the value"
    if kind == "type":
        expected = error.validator_value
        wanted = " or ".join(expected) if isinstance(expected, list) else str(expected)
        return f"{subject} must be {wanted}, not {_json_type(instance)}"
    if kind == "enum" and isinstance(error.validator_value, list):
        options = ", ".join(_short_json(value) for value in error.validator_value[:8])
        return f"{subject} must be one of: {options}"
    if kind == "const":
        return f"{subject} must be {_short_json(error.validator_value)}"
    message = str(error.message)
    message = message if len(message) <= 160 else message[:157] + "..."
    return f"{where}: {message}" if where else message


# --------------------------------------------------------------------------------------------
# The model wrapper
# --------------------------------------------------------------------------------------------


def default_constraint(fmt: str) -> Constraint:
    """json_schema where the format's codec maps it to an output constraint, else none."""
    return "json_schema" if _PUBLIC_FORMAT.get(fmt, fmt) in CONSTRAINED_FORMATS else "none"


class FallbackModel:
    """A step model (`complete(request) -> Completion`) that drives tools through the fallback
    protocol on any text-generating endpoint.

    `constraint` None picks `default_constraint(<the client's format>)`; the probe passes an
    explicit value per endpoint (`constraint_for`). "json_object" asks for any JSON object where
    that is all the endpoint offers. If the endpoint rejects a constrained request as invalid,
    the call is repeated unconstrained; if a constrained reply has reasoning but no answer (an
    engine that applies the constraint from the first token), later attempts are unconstrained.
    Streaming is not offered: the protocol needs the whole reply to check it.
    """

    def __init__(
        self, client: ModelClient, *, constraint: Constraint | None = None, max_repairs: int = 2
    ) -> None:
        if constraint is not None and constraint not in CONSTRAINTS:
            raise ValueError(f"unknown constraint {constraint!r}; known: {', '.join(CONSTRAINTS)}")
        if max_repairs < 0:
            raise ValueError("max_repairs must be 0 or more")
        self.client = client
        self.format = client.codec.format
        self.constraint: Constraint = constraint or default_constraint(self.format)
        self.max_repairs = max_repairs

    def complete(self, request: Request) -> Completion:
        """Without tools (and no tool history), a plain call. Otherwise the protocol: rewritten
        history, a constrained call, validation and at most `max_repairs` repairs. The result
        has one synthetic `ToolCallPart` (finish "tool_calls"), the final answer as text
        (finish "stop"), or, when no valid action came, the last reply's text (finish "stop",
        or "length" if it was cut off) with the failure in the report. Usage and latency are
        totals over all attempts; `Usage.raw["attempts"]` keeps each attempt's raw usage."""
        if not request.tools and not _has_tool_parts(request.messages):
            return self.client.complete(request)
        started = time.perf_counter()
        tools, allow_final = offered_tools(request)
        protocol = to_protocol_messages(request)
        if not tools:
            completion = self.client.complete(protocol)
            report = _merged_report([completion])
            reason = "tool_choice is none" if request.tools else "the request has no tools"
            report.add(
                "tools",
                f"fallback tool protocol: {reason}; tool calls and results in the history were"
                " sent as text",
                "approximated",
            )
            _report_prompt(request, report)
            elapsed = time.perf_counter() - started
            return Completion(completion.response, report, elapsed, completion.attempts)
        return self._drive(request, protocol, tools, allow_final, started)

    def _drive(
        self,
        request: Request,
        protocol: Request,
        tools: list[ToolSpec],
        allow_final: bool,
        started: float,
    ) -> Completion:
        schema_format = ResponseFormat(
            type="json_schema",
            name=ACTION_SCHEMA_NAME,
            json_schema=action_schema(tools, allow_final=allow_final),
            strict=True,
        )
        constraint: Constraint = self.constraint
        history = list(protocol.messages)
        completions: list[Completion] = []
        notes: list[str] = []
        repairs = 0
        rejected = False
        action: tuple[str, dict[str, Any]] | None = None
        error: ActionError | None = None
        while True:
            attempt = protocol.model_copy(
                update={
                    "messages": history,
                    "response_format": _response_format(constraint, schema_format),
                }
            )
            try:
                completion = self.client.complete(attempt)
            except ModelError as exc:
                if constraint == "none" or rejected or exc.kind not in _REJECTION_KINDS:
                    raise
                rejected = True
                notes.append(
                    f"the endpoint rejected the {constraint} constraint"
                    f" ({_clip(exc.message, 160)}); replies were validated and repaired instead"
                )
                constraint = "none"
                continue
            completions.append(completion)
            reply = completion.response
            try:
                action, error = _reply_action(reply, tools, allow_final), None
            except ActionError as exc:
                action, error = None, exc
            if action is not None or repairs >= self.max_repairs:
                break
            text = reply.message.text_content()
            if constraint != "none" and not text.strip() and reply.message.reasoning():
                notes.append(
                    f"a reply under the {constraint} constraint had reasoning but no answer;"
                    " later attempts were unconstrained"
                )
                constraint = "none"
            repairs += 1
            history = [
                *history,
                Message.text("assistant", _clip(text, MAX_BAD_REPLY_CHARS) or "(empty reply)"),
                Message.text("user", REPAIR_PROMPT.format(error=error)),
            ]
        response = _response(request, completions[-1].response, action, completions)
        report = _merged_report(completions)
        report.add(
            "tools",
            f"{len(tools)} tool(s) offered through the fallback tool protocol"
            f" (constraint {constraint}, {repairs} repair(s))",
            "approximated",
        )
        for note in notes:
            report.add("tools", note, "approximated")
        if action is None:
            report.add(
                "tools",
                f"no valid action after {repairs} repair(s): {error}; the reply text is returned",
                "approximated",
            )
        if request.response_format is not None and request.response_format.type != "text":
            report.add(
                "response_format",
                "replaced by the action format; the final answer is free text",
                "approximated",
            )
        _report_prompt(request, report)
        attempts = sum(completion.attempts for completion in completions) + int(rejected)
        return Completion(response, report, time.perf_counter() - started, attempts)


def _report_prompt(request: Request, report: ConversionReport) -> None:
    if request.prompt is not None:
        report.add(
            "prompt",
            "the pre-rendered prompt shows the conversation before the protocol rewrite; the"
            " rewritten conversation was rendered by the codec instead",
        )


def _response_format(
    constraint: Constraint, schema_format: ResponseFormat
) -> ResponseFormat | None:
    if constraint == "json_schema":
        return schema_format
    if constraint == "json_object":
        return ResponseFormat(type="json_object")
    return None


def _reply_action(
    reply: Response, tools: Sequence[ToolSpec], allow_final: bool
) -> tuple[str, dict[str, Any]]:
    """The action in a reply. A native tool call (a server may parse one even without tools)
    counts if it is valid; otherwise only the answer text is parsed, never reasoning."""
    calls = reply.message.tool_calls()
    if calls:
        return _checked(("tool", calls[0].name, calls[0].arguments), tools, allow_final)
    return parse_action(reply.message.text_content(), tools, allow_final=allow_final)


def _response(
    request: Request,
    last: Response,
    action: tuple[str, dict[str, Any]] | None,
    completions: Sequence[Completion],
) -> Response:
    reasoning: list[Part] = [
        part for part in last.message.content if isinstance(part, ReasoningPart)
    ]
    content: list[Part]
    finish: FinishReason
    if action is None:
        content = [part for part in last.message.content if not isinstance(part, ToolCallPart)]
        finish = (
            last.finish_reason if last.finish_reason in ("length", "content_filter") else "stop"
        )
    elif action[0] == FINAL:
        content = [*reasoning, TextPart(text=str(action[1].get("answer", "")))]
        finish = "stop"
    else:
        name, arguments = action
        turn = sum(1 for message in request.messages if message.role == "assistant")
        call = ToolCallPart(
            id=synthetic_call_id(turn, 0),
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
            synthetic=True,
        )
        content = [*reasoning, call]
        finish = "tool_calls"
    return Response(
        id=last.id,
        model=last.model,
        message=Message(role="assistant", content=content),
        finish_reason=finish,
        raw_finish_reason=last.raw_finish_reason,
        usage=_total_usage([completion.response.usage for completion in completions]),
        created=last.created,
    )


def _total_usage(usages: Sequence[Usage | None]) -> Usage | None:
    """Usage summed over attempts; each attempt's raw usage is kept in `raw["attempts"]`."""
    present = [usage for usage in usages if usage is not None]
    if not present:
        return None

    def total(field: str) -> int | None:
        values = [getattr(usage, field) for usage in present if getattr(usage, field) is not None]
        return sum(values) if values else None

    return Usage(
        input_tokens=total("input_tokens"),
        output_tokens=total("output_tokens"),
        cached_input_tokens=total("cached_input_tokens"),
        cache_write_tokens=total("cache_write_tokens"),
        reasoning_tokens=total("reasoning_tokens"),
        raw={"attempts": [usage.raw if usage is not None else None for usage in usages]},
    )


def _merged_report(completions: Sequence[Completion]) -> ConversionReport:
    """The codec reports of every attempt, each loss once."""
    report = ConversionReport(target=completions[-1].report.target if completions else None)
    seen: set[tuple[str, str, str]] = set()
    for completion in completions:
        for loss in completion.report.losses:
            key = (loss.field, loss.detail, loss.action)
            if key not in seen:
                seen.add(key)
                report.losses.append(loss.model_copy())
    return report


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " [...]"


# --------------------------------------------------------------------------------------------
# Capability profile
# --------------------------------------------------------------------------------------------


def _check(profile: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    checks = profile.get("checks")
    entry = checks.get(name) if isinstance(checks, Mapping) else None
    return entry if isinstance(entry, Mapping) else {}


def _native_tools(profile: Mapping[str, Any] | None) -> bool | None:
    """Whether the probe passed native single tool calls: True, False, or None (not tested)."""
    if not isinstance(profile, Mapping):
        return None
    capabilities = profile.get("capabilities")
    if isinstance(capabilities, Mapping) and isinstance(capabilities.get("native_tools"), bool):
        return bool(capabilities["native_tools"])
    ok = _check(profile, "native_tool_call").get("ok")
    if isinstance(ok, bool):
        return ok
    flat = profile.get("native_tools")
    return flat if isinstance(flat, bool) else None


def needs_fallback(
    profile: Mapping[str, Any] | None, *, registry_tool_calls: str | None = None
) -> bool:
    """Whether a worker should use the fallback protocol instead of native tool calls.

    False when the probe passed native single tool calls; True when it ran them and they
    failed. When they were not tested (no profile, or the check skipped), the registry decides:
    "native" means native tools, "fallback", "none" or no entry mean the fallback, which works
    with any text model.
    """
    native = _native_tools(profile)
    if native is not None:
        return not native
    return registry_tool_calls != "native"


def constraint_for(profile: Mapping[str, Any] | None, fmt: str) -> Constraint:
    """The constraint for an endpoint from its capability profile: the format's default, or
    "none" when the probe saw the endpoint reject the action schema, or a reasoning model lose
    its reasoning or its JSON under a schema constraint (validate and repair instead)."""
    constraint = default_constraint(fmt)
    if constraint == "none" or not isinstance(profile, Mapping):
        return constraint
    data = _check(profile, "fallback_tool_protocol").get("data")
    if isinstance(data, Mapping) and data.get("constraint_rejected"):
        return "none"
    if _check(profile, "structured_output_under_reasoning").get("ok") is False:
        return "none"
    return constraint
