"""Parsers that extract tool calls from raw model text (ADR 0003 item 5).

Each parser handles one syntax from the catalog in docs/RESEARCH.md ("Tool-call formats") and
returns the text with the calls removed plus the calls found; `render` writes calls back in the
same syntax, for histories sent to a model whose calls are parsed from its text. Parsers are
selected per model version (hub ID plus template hash) by the registry; `detect` is the
fallback by template markers. Phase 1 ships the syntaxes the T0 models and the prompt-based
codecs need; more are added as models are onboarded.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass
class ParsedCall:
    name: str
    arguments: str  # JSON object as a string


@dataclass
class ParseResult:
    text: str
    calls: list[ParsedCall]


def _normalize_arguments(value: object) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return json.dumps({"input": value})
    if value is None:
        value = {}
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


# JSON-in-tags: <tool_call>{"name": ..., "arguments": {...}}</tool_call> (the hermes style).
_HERMES_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def parse_hermes(text: str) -> ParseResult:
    calls: list[ParsedCall] = []

    def take(match: re.Match[str]) -> str:
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return match.group(0)
        if not isinstance(payload, dict) or "name" not in payload:
            return match.group(0)
        calls.append(
            ParsedCall(
                name=str(payload["name"]),
                arguments=_normalize_arguments(payload.get("arguments", payload.get("parameters"))),
            )
        )
        return ""

    remaining = _HERMES_RE.sub(take, text)
    return ParseResult(text=remaining.strip() if calls else text, calls=calls)


# XML-style function/parameter tags:
# <tool_call><function=NAME><parameter=KEY>VALUE</parameter>...</function></tool_call>
_XML_FUNCTION_RE = re.compile(
    r"(?:<tool_call>\s*)?<function=([^>\s]+)>(.*?)</function>(?:\s*</tool_call>)?", re.S
)
_XML_PARAM_RE = re.compile(r"<parameter=([^>\s]+)>\s*(.*?)\s*</parameter>", re.S)


def _coerce(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_xml_function(text: str) -> ParseResult:
    calls: list[ParsedCall] = []

    def take(match: re.Match[str]) -> str:
        params = {key: _coerce(value) for key, value in _XML_PARAM_RE.findall(match.group(2))}
        calls.append(ParsedCall(name=match.group(1), arguments=_normalize_arguments(params)))
        return ""

    remaining = _XML_FUNCTION_RE.sub(take, text)
    return ParseResult(text=remaining.strip() if calls else text, calls=calls)


# Bracketed name/arguments markers: [TOOL_CALLS]NAME[ARGS]{...}
_BRACKET_RE = re.compile(
    r"\[TOOL_CALLS\]\s*([A-Za-z0-9_.\-]+)\s*\[ARGS\]\s*(\{.*?\})(?=\s*\[TOOL_CALLS\]|\s*$)", re.S
)


def parse_bracketed(text: str) -> ParseResult:
    calls = [
        ParsedCall(name=name, arguments=_normalize_arguments(args))
        for name, args in _BRACKET_RE.findall(text)
    ]
    remaining = _BRACKET_RE.sub("", text)
    return ParseResult(text=remaining.strip() if calls else text, calls=calls)


# Special-token sections: <|tool_call|>[{"name": ..., "arguments": {...}}, ...] (the granite
# style; Granite 3.1 writes the marker as the plain text <tool_call>). The JSON array has no
# closing marker, and text around it is content. `<|tool_call|>` is a special token, and
# servers that leave special tokens out of the reply text (the llama.cpp server does) send only
# the array, so with this syntax a reply that is nothing but such an array counts too: every
# item exactly {"name": <string>, "arguments": <object>} (ADR 0022).
GRANITE_MARKERS = ("<|tool_call|>", "<tool_call>")
_DECODER = json.JSONDecoder()


def _granite_calls(payload: object, *, bare: bool) -> list[ParsedCall]:
    if not isinstance(payload, list) or not payload:
        return []
    calls: list[ParsedCall] = []
    for item in payload:
        if not isinstance(item, dict):
            return []
        name = item.get("name")
        arguments = item.get("arguments")
        if not isinstance(name, str) or not name:
            return []
        if bare and (set(item) != {"name", "arguments"} or not isinstance(arguments, dict)):
            return []
        if arguments is not None and not isinstance(arguments, dict | str):
            return []
        calls.append(ParsedCall(name=name, arguments=_normalize_arguments(arguments)))
    return calls


def parse_granite(text: str) -> ParseResult:
    found = [(text.find(m), m) for m in GRANITE_MARKERS if m in text]
    if not found:
        stripped = text.strip()
        if not stripped.startswith("["):
            return ParseResult(text=text, calls=[])
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return ParseResult(text=text, calls=[])
        calls = _granite_calls(payload, bare=True)
        return ParseResult(text="", calls=calls) if calls else ParseResult(text=text, calls=[])
    start, marker = min(found)
    rest = text[start + len(marker) :].lstrip()
    try:
        payload, end = _DECODER.raw_decode(rest)
    except json.JSONDecodeError:
        return ParseResult(text=text, calls=[])
    calls = _granite_calls(payload, bare=False)
    if not calls:
        return ParseResult(text=text, calls=[])
    around = [piece.strip() for piece in (text[:start], rest[end:])]
    return ParseResult(text="\n".join(piece for piece in around if piece), calls=calls)


PARSERS: dict[str, Callable[[str], ParseResult]] = {
    "hermes": parse_hermes,
    "xml_function": parse_xml_function,
    "bracketed": parse_bracketed,
    "granite": parse_granite,
}

# Text that must appear in a reply before a parser can find calls in it.
CALL_MARKERS: dict[str, tuple[str, ...]] = {
    "hermes": ("<tool_call>",),
    "xml_function": ("<function=",),
    "bracketed": ("[TOOL_CALLS]",),
    "granite": GRANITE_MARKERS,
}

# Template markers that identify a syntax when the catalog has no entry for a model.
MARKERS: list[tuple[str, str]] = [
    ("<function=", "xml_function"),
    ("<tool_call>", "hermes"),
    ("[TOOL_CALLS]", "bracketed"),
    ("<|tool_call|>", "granite"),
]

# Text that opens a tool-call section only at the very start of a reply, for syntaxes whose
# marker a server may drop (see GRANITE_MARKERS).
LEADING_MARKERS: dict[str, tuple[str, ...]] = {"granite": ("[",)}


def detect(template: str) -> str | None:
    for marker, name in MARKERS:
        if marker in template:
            return name
    return None


def parse(text: str, syntax: str | None) -> ParseResult:
    """Parse with the named syntax; with None, try every catalog parser and keep the first hit.

    Without a syntax a parser runs only when its marker is in the text, so a reply that merely
    looks like a call list (the bare granite form) is never read as calls by guesswork.
    """
    if syntax is not None:
        return PARSERS[syntax](text)
    for name, parser in PARSERS.items():
        if any(marker in text for marker in CALL_MARKERS[name]):
            result = parser(text)
            if result.calls:
                return result
    return ParseResult(text=text, calls=[])


# ---- writing calls back in a syntax --------------------------------------------------------


def _arguments(call: ParsedCall) -> object:
    try:
        return json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return {"input": call.arguments}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_hermes(calls: Sequence[ParsedCall]) -> str:
    return "\n".join(
        f"<tool_call>\n{_json({'name': c.name, 'arguments': _arguments(c)})}\n</tool_call>"
        for c in calls
    )


def render_xml_function(calls: Sequence[ParsedCall]) -> str:
    blocks = []
    for call in calls:
        arguments = _arguments(call)
        items = arguments.items() if isinstance(arguments, dict) else [("input", arguments)]
        params = "".join(
            f"<parameter={key}>\n{value if isinstance(value, str) else _json(value)}\n"
            "</parameter>\n"
            for key, value in items
        )
        blocks.append(f"<tool_call>\n<function={call.name}>\n{params}</function>\n</tool_call>")
    return "\n".join(blocks)


def render_bracketed(calls: Sequence[ParsedCall]) -> str:
    return "".join(f"[TOOL_CALLS]{c.name}[ARGS]{_json(_arguments(c))}" for c in calls)


def render_granite(calls: Sequence[ParsedCall]) -> str:
    items = [{"name": c.name, "arguments": _arguments(c)} for c in calls]
    return GRANITE_MARKERS[0] + _json(items)


RENDERERS: dict[str, Callable[[Sequence[ParsedCall]], str]] = {
    "hermes": render_hermes,
    "xml_function": render_xml_function,
    "bracketed": render_bracketed,
    "granite": render_granite,
}


def render(calls: Sequence[ParsedCall], syntax: str) -> str:
    """The calls written in the named syntax, as the model itself would write them."""
    return RENDERERS[syntax](calls)
