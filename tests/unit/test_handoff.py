"""HANDOFF v0.1 briefs (SPEC §9, TK7): schema, rendering, fitting and the T0 writer."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from stepswitch.compat.client import Completion
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import ConversionReport, Message, Request, Response
from stepswitch.context import handoff
from stepswitch.context.handoff import Failure, FileNote, HandoffFacts
from stepswitch.context.tokens import EstimateCounter

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "spec" / "handoff-v0.1.md"


class WordCounter:
    """An exact counter for tests: one token per whitespace-separated word."""

    name = "test:words"
    exact = True

    def count(self, text: str) -> int:
        return len(text.split())


def facts(**overrides: Any) -> HandoffFacts:
    base: dict[str, Any] = {
        "task_id": "t1",
        "goal": "Fix the off-by-one error in pager.first_page.",
        "constraints": ["Do not change files under tests/."],
        "commands": ["python -m pytest -q"],
        "done": ["read pager.py lines 1-40", "ran tests: 1 failed"],
        "decisions": [{"what": "Change the slice", "why": "Two callers rely on first_page."}],
        "files": [FileNote("pager.py", ["first_page", "Pager"], "edited")],
        "failing": [Failure("test", "FAILED tests/test_pager.py::test_first - IndexError")],
        "tokens_left": 1000,
        "seconds_left": 12.25,
        "from_worker": "w1",
        "step": 4,
    }
    base.update(overrides)
    return HandoffFacts(**base)


def test_schema_is_valid_and_the_spec_example_validates() -> None:
    schema = handoff.load_schema()
    Draft202012Validator.check_schema(schema)
    examples = re.findall(r"```json\n(.*?)```", SPEC.read_text(encoding="utf-8"), re.S)
    assert examples
    for example in examples:
        assert handoff.validate(json.loads(example)) == []


def test_built_brief_validates_and_renders_in_spec_order() -> None:
    brief = handoff.build(facts(), tokenizer="test:words", exact=True)
    assert handoff.validate(brief) == []
    assert handoff.render_markdown(brief) == (
        "# Handoff: t1\n"
        "\n"
        "Goal: Fix the off-by-one error in pager.first_page.\n"
        "Track: general\n"
        "\n"
        "## Constraints\n"
        "- Do not change files under tests/.\n"
        "\n"
        "## Acceptance\n"
        "- command: `python -m pytest -q`\n"
        "\n"
        "## Done so far\n"
        "- read pager.py lines 1-40\n"
        "- ran tests: 1 failed\n"
        "\n"
        "## Decisions\n"
        "- Change the slice: Two callers rely on first_page.\n"
        "\n"
        "## Files\n"
        "- `pager.py` (first_page, Pager): edited\n"
        "\n"
        "## Failing checks\n"
        "### test\n"
        "```text\n"
        "FAILED tests/test_pager.py::test_first - IndexError\n"
        "```\n"
        "\n"
        "Budget left: 1000 tokens, 12.2 seconds\n"
    )


def test_empty_sections_are_left_out_and_unlimited_budgets_are_not_rendered() -> None:
    brief = handoff.build(
        HandoffFacts(task_id="t2", goal="Do it."), tokenizer="test:words", exact=True
    )
    assert handoff.validate(brief) == []
    assert handoff.render_markdown(brief) == "# Handoff: t2\n\nGoal: Do it.\nTrack: general\n"


def test_excerpt_fence_is_longer_than_any_backtick_run_inside() -> None:
    brief = handoff.build(facts(failing=[Failure("lint", "x = ```y```")]))
    text = handoff.render_markdown(brief)
    assert "````text\nx = ```y```\n````" in text


def test_validate_rejects_unknown_versions_missing_and_extra_fields() -> None:
    brief = handoff.build(facts())
    assert handoff.validate({**brief, "version": "0.2"})
    assert handoff.validate({k: v for k, v in brief.items() if k != "files"})
    assert handoff.validate({**brief, "extra": 1})


def test_fit_trims_a_large_brief_under_the_limit_with_an_exact_counter() -> None:
    big = facts(
        goal="word " * 900,
        done=[f"step {i}: read module_{i}.py and found nothing useful there" for i in range(40)],
        files=[
            FileNote(f"pkg/module_{i}.py", [f"f{j}" for j in range(12)], "read") for i in range(30)
        ],
        failing=[Failure(f"test {i}", "E   AssertionError: " + "x " * 600) for i in range(5)],
    )
    fitted = handoff.fit(handoff.build(big), WordCounter())
    assert fitted.valid, fitted.errors
    assert fitted.count.exact and fitted.count.tokens <= handoff.MAX_TOKENS
    assert fitted.count.tokens == WordCounter().count(fitted.markdown)
    assert fitted.markdown == handoff.render_markdown(fitted.brief)
    assert fitted.trims, "a brief this large must be trimmed"
    assert fitted.brief["provenance"] == {
        "from_worker": "w1",
        "step": 4,
        "tokenizer": "test:words",
        "exact": True,
    }


def test_fit_keeps_an_estimate_under_the_margin_and_flags_it() -> None:
    big = facts(done=[f"item {i} " + "y" * 200 for i in range(30)])
    fitted = handoff.fit(handoff.build(big), EstimateCounter(), limit=600)
    assert not fitted.count.exact
    assert fitted.count.tokens <= int(600 * handoff.ESTIMATE_MARGIN)
    assert fitted.brief["provenance"]["exact"] is False
    assert fitted.brief["provenance"]["tokenizer"] == "estimate:chars/4"


def test_fit_falls_back_to_an_estimate_when_the_counter_fails() -> None:
    class Broken:
        name = "llama.cpp:/tokenize"
        exact = True

        def count(self, text: str) -> int:
            from stepswitch.context.tokens import TokenizerError

            raise TokenizerError("connection refused")

    fitted = handoff.fit(handoff.build(facts()), Broken())
    assert not fitted.count.exact
    assert fitted.brief["provenance"]["exact"] is False
    assert fitted.brief["provenance"]["tokenizer"].startswith("estimate:chars/4 (llama.cpp")
    assert fitted.valid


class Scripted:
    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self.text, self.error = text, error
        self.requests: list[Request] = []

    def complete(self, request: Request) -> Completion:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        response = Response(
            message=Message.text("assistant", self.text or ""), finish_reason="stop"
        )
        return Completion(response=response, report=ConversionReport(), latency_s=0.1)


def test_writer_answer_must_match_the_state_schema() -> None:
    good = Scripted('{"done": ["fixed it"], "decisions": [], "open_questions": ["why?"]}')
    state, details = handoff.write_state(good, "log")
    assert state == {"done": ["fixed it"], "decisions": [], "open_questions": ["why?"]}
    assert details["writer"] == "model"
    request = good.requests[0]
    assert request.response_format is not None
    assert request.response_format.type == "json_schema"
    assert request.response_format.json_schema == handoff.WRITER_SCHEMA

    state, details = handoff.write_state(Scripted('{"done": "not a list"}'), "log")
    assert state is None and details["writer"] == "fallback"
    state, details = handoff.write_state(Scripted("no json here"), "log")
    assert state is None
    state, details = handoff.write_state(Scripted(error=ModelError("server", "boom")), "log")
    assert state is None and "model error" in details["reason"]


@pytest.mark.parametrize("track", ["general", "science"])
def test_tracks_are_kept_and_unknown_tracks_fall_back(track: str) -> None:
    assert handoff.build(facts(track=track))["track"] == track
    assert handoff.build(facts(track="other"))["track"] == "general"
