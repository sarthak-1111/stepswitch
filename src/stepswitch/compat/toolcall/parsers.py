"""Parsers that extract tool calls from raw model text (ADR 0003 item 5).

Each parser handles one syntax from the catalog in docs/RESEARCH.md ("Tool-call formats") and
returns the text with the calls removed plus the calls found. Parsers are selected per model
version (hub ID plus template hash) by the registry; `detect` is the fallback by template
markers. Phase 1 ships the syntaxes the T0 models and the prompt-based codecs need; more are
added as models are onboarded.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
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


PARSERS: dict[str, Callable[[str], ParseResult]] = {
    "hermes": parse_hermes,
    "xml_function": parse_xml_function,
    "bracketed": parse_bracketed,
}

# Template markers that identify a syntax when the catalog has no entry for a model.
MARKERS: list[tuple[str, str]] = [
    ("<function=", "xml_function"),
    ("<tool_call>", "hermes"),
    ("[TOOL_CALLS]", "bracketed"),
]


def detect(template: str) -> str | None:
    for marker, name in MARKERS:
        if marker in template:
            return name
    return None


def parse(text: str, syntax: str | None) -> ParseResult:
    """Parse with the named syntax; with None, try every catalog parser and keep the first hit."""
    if syntax is not None:
        return PARSERS[syntax](text)
    for parser in PARSERS.values():
        result = parser(text)
        if result.calls:
            return result
    return ParseResult(text=text, calls=[])
