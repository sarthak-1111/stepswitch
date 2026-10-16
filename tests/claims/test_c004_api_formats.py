"""C-004: speaks the chat-completions, responses, messages and generate-content API formats, plus
engine-native formats.

Proof (SPEC §18.5): conformance tests against mock servers per format (streaming, tool calls,
parallel tool calls, errors, usage); round-trip translation tests preserve messages and tool
calls.

The conformance part runs every suite in tests/conformance (one per format) through the real
client and codec against hand-written mock servers. The round-trip part converts canonical
requests between every pair of chat formats through the internal representation, checks that
the prompt formats render every message and tool exchange, and round-trips a response with
text and parallel tool calls through every format.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from stepswitch.compat.codecs import get_codec
from stepswitch.compat.codecs.base import Codec
from stepswitch.compat.ir import (
    ConversionReport,
    Message,
    Request,
    Response,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from tests.claims import evidence
from tests.conformance.harness import (
    REQUIRED_CASES,
    FormatSuite,
    conversation_request,
    normalize,
    run_suite,
    text_request,
    tool_request,
)

pytestmark = [pytest.mark.claim("C-004")]

CONFORMANCE_DIR = Path(__file__).resolve().parents[1] / "conformance"
CHAT_FORMATS = [
    "chat-completions",
    "responses",
    "messages",
    "generate-content",
    "ollama-native",
]
PROMPT_FORMATS = ["text-completion", "llamacpp-native"]
LOSSLESS_FIELDS = ("messages", "content", "tools", "tool_calls", "tool_results", "system")


def load_suites() -> dict[str, FormatSuite]:
    suites: dict[str, FormatSuite] = {}
    for path in sorted(CONFORMANCE_DIR.glob("test_*.py")):
        module = importlib.import_module(f"tests.conformance.{path.stem}")
        suite = getattr(module, "SUITE", None)
        if isinstance(suite, FormatSuite):
            suites[suite.format] = suite
    return suites


def test_conformance_against_mock_servers() -> None:
    limits = evidence.thresholds("C-004")
    suites = load_suites()
    results = {fmt: run_suite(suite) for fmt, suite in sorted(suites.items())}
    missing_formats = [fmt for fmt in limits["formats"] if fmt not in suites]
    missing_cases = {
        fmt: [case for case in limits["cases"] if case not in result]
        for fmt, result in results.items()
    }
    failed = {
        fmt: {case: outcome for case, outcome in result.items() if outcome != "passed"}
        for fmt, result in results.items()
    }
    evidence.write(
        "C-004",
        "conformance",
        {
            "formats": results,
            "required_cases": list(REQUIRED_CASES),
            "missing_formats": missing_formats,
            "failed_cases": {fmt: cases for fmt, cases in failed.items() if cases},
            "cases_run": sum(len(result) for result in results.values()),
        },
    )
    assert set(limits["cases"]) <= set(REQUIRED_CASES)
    assert not missing_formats, f"no conformance suite for: {missing_formats}"
    assert not any(missing_cases.values()), f"required cases missing: {missing_cases}"
    assert not any(failed.values()), f"failed cases: {failed}"


# --------------------------------------------------------------------------------------------
# Round trips
# --------------------------------------------------------------------------------------------


def _requests() -> dict[str, Request]:
    return {
        "text": text_request(),
        "tools": tool_request(),
        "conversation": conversation_request(),
    }


def _through(codec: Codec, request: Request) -> tuple[Request, ConversionReport]:
    """Client-side encode, then server-side decode: the format's view of the request."""
    report = ConversionReport(target=codec.format)
    call = codec.encode_request(request, report)
    decoded = codec.decode_request(call.json, path=call.path, headers=dict(call.headers))
    return decoded, report


def _diff(expected: Any, actual: Any, where: str = "") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        out: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            out += _diff(expected.get(key), actual.get(key), f"{where}.{key}")
        return out
    if isinstance(expected, list | tuple) and isinstance(actual, list | tuple):
        if len(expected) != len(actual):
            return [f"{where}: {len(expected)} items vs {len(actual)}"]
        out = []
        for index, (a, b) in enumerate(zip(expected, actual, strict=True)):
            out += _diff(a, b, f"{where}[{index}]")
        return out
    return [] if expected == actual else [f"{where}: {expected!r} != {actual!r}"]


def _lossless_losses(report: ConversionReport) -> list[str]:
    """Reported losses inside the lossless scope (field paths like `messages.1.content.0`,
    `messages[1]` or `tools[0].strict` all start with the top-level field name)."""
    return [
        f"{loss.field}: {loss.detail}"
        for loss in report.losses
        if re.split(r"[.\[]", loss.field, maxsplit=1)[0] in LOSSLESS_FIELDS
    ]


def _chat_pairs() -> dict[str, Any]:
    pairs = []
    for name, original in _requests().items():
        expected = normalize(original)
        for source in CHAT_FORMATS:
            first, report_a = _through(get_codec(source), original)
            for target in CHAT_FORMATS:
                second, report_b = _through(get_codec(target), first)
                differences = _diff(expected, normalize(second))
                losses = _lossless_losses(report_a) + _lossless_losses(report_b)
                pairs.append(
                    {
                        "request": name,
                        "from": source,
                        "to": target,
                        "differences": differences,
                        "lossless_scope_losses": losses,
                        "reported_losses": [
                            f"{loss.field}: {loss.detail}"
                            for loss in [*report_a.losses, *report_b.losses]
                        ],
                    }
                )
    return {"pairs": pairs}


def _expected_fragments(request: Request) -> list[str]:
    """Texts a prompt rendering must contain: every message, tool call and tool result."""
    fragments = [part.text for part in request.system]
    for message in request.messages:
        for part in message.content:
            if isinstance(part, TextPart):
                fragments.append(part.text)
            elif isinstance(part, ToolCallPart):
                fragments.append(part.name)
                fragments.extend(
                    json.dumps(value) if not isinstance(value, str) else value
                    for value in json.loads(part.arguments or "{}").values()
                )
            elif isinstance(part, ToolResultPart):
                fragments.append(part.text())
    fragments.extend(tool.name for tool in request.tools)
    return [fragment for fragment in fragments if fragment]


def _prompt_renders() -> dict[str, Any]:
    renders = []
    for name, original in _requests().items():
        for fmt in PROMPT_FORMATS:
            codec = get_codec(fmt)
            report = ConversionReport(target=codec.format)
            call = codec.encode_request(original, report)
            prompt = call.json["prompt"]
            missing = [f for f in _expected_fragments(original) if f not in prompt]
            renders.append({"request": name, "format": fmt, "missing": missing})
    return {"renders": renders}


def _sample_response() -> Response:
    return Response(
        id="resp_1",
        model="test-model",
        message=Message(
            role="assistant",
            content=[
                TextPart(text="Checking both."),
                ToolCallPart(id="call_1", name="get_weather", arguments='{"city": "Paris"}'),
                ToolCallPart(id="call_2", name="get_time", arguments='{"city": "Tokyo"}'),
            ],
        ),
        finish_reason="tool_calls",
        usage=Usage(input_tokens=120, output_tokens=40, cached_input_tokens=100),
    )


def _response_view(response: Response) -> dict[str, Any]:
    return {
        "text": response.message.text_content().strip(),
        "calls": [(c.name, json.loads(c.arguments or "{}")) for c in response.message.tool_calls()],
        "finish": response.finish_reason,
        "input_tokens": response.usage.input_tokens if response.usage else None,
        "output_tokens": response.usage.output_tokens if response.usage else None,
    }


def _response_round_trips() -> dict[str, Any]:
    expected = _response_view(_sample_response())
    rows = []
    for fmt in [*CHAT_FORMATS, *PROMPT_FORMATS]:
        codec = get_codec(fmt)
        decoded = codec.decode_response(codec.encode_response(_sample_response()))
        rows.append({"format": fmt, "differences": _diff(expected, _response_view(decoded))})
    return {"responses": rows}


def test_round_trip_preserves_messages_and_tool_calls() -> None:
    limits = evidence.thresholds("C-004")
    chat = _chat_pairs()
    prompts = _prompt_renders()
    responses = _response_round_trips()
    differences = (
        sum(len(p["differences"]) + len(p["lossless_scope_losses"]) for p in chat["pairs"])
        + sum(len(r["missing"]) for r in prompts["renders"])
        + sum(len(r["differences"]) for r in responses["responses"])
    )
    evidence.write(
        "C-004",
        "round_trip",
        {
            "chat_formats": CHAT_FORMATS,
            "prompt_formats": PROMPT_FORMATS,
            "request_pairs": len(chat["pairs"]),
            "differences": differences,
            **chat,
            **prompts,
            **responses,
        },
    )
    bad_pairs = [p for p in chat["pairs"] if p["differences"] or p["lossless_scope_losses"]]
    bad_renders = [r for r in prompts["renders"] if r["missing"]]
    bad_responses = [r for r in responses["responses"] if r["differences"]]
    assert differences <= limits["round_trip_differences"], {
        "pairs": bad_pairs[:5],
        "renders": bad_renders,
        "responses": bad_responses,
    }
