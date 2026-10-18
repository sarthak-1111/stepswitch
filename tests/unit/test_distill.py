"""Tool-output distillation (TK4): key lines verbatim, raw output retrievable."""

from __future__ import annotations

import pytest

from stepswitch.compat.client import Completion
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import ConversionReport, Message, Request, Response
from stepswitch.context import distill
from stepswitch.context.distill import OutputStore

LOG = "\n".join(
    [f"collecting module_{i}.py ... ok" for i in range(400)]
    + [
        "tests/test_calc.py:12: in test_add",
        "E   AssertionError: assert 3 == 4",
        "FAILED tests/test_calc.py::test_add - AssertionError: assert 3 == 4",
        "1 failed, 399 passed in 2.51s",
    ]
)


def test_long_outputs_are_distilled_with_key_lines_verbatim() -> None:
    assert distill.too_long(LOG)
    text = distill.distill(LOG, tool="test", number=1)
    assert "@output/1" in text
    assert "E   AssertionError: assert 3 == 4" in text
    assert "tests/test_calc.py:12: in test_add" in text
    assert "1 failed, 399 passed in 2.51s" in text  # the last lines
    assert len(text) < len(LOG) // 4


def test_repeated_key_lines_are_folded() -> None:
    lines = distill.key_lines("\n".join(["warning: x"] * 50))
    assert lines[0] == "1|warning: x"
    assert "repeats 49 more times" in lines[1]


def test_summary_is_included_when_given() -> None:
    text = distill.distill(LOG, tool="test", number=2, summary="One test failed.")
    assert "Summary: One test failed." in text


def test_store_shows_numbered_ranges_and_rejects_unknown_outputs() -> None:
    store = OutputStore()
    number = store.put("a\nb\nc\nd")
    assert store.parse(f"@output/{number}") == number
    assert store.parse("@output/x") == -1
    assert store.parse("src/a.py") is None
    shown = store.show(number, 2, 3, 1000)
    assert shown == "@output/1 lines 2-3 of 4\n2|b\n3|c"
    assert "[more: read with start=2]" in store.show(number, 1, 4, 5)
    with pytest.raises(KeyError, match="stored: @output/1"):
        store.show(9, None, None, 100)


class Scripted:
    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self.text, self.error = text, error
        self.requests: list[Request] = []

    def complete(self, request: Request) -> Completion:
        self.requests.append(request)
        if self.error:
            raise self.error
        response = Response(message=Message.text("assistant", self.text))
        return Completion(response=response, report=ConversionReport(), latency_s=0.0)


def test_summaries_come_from_a_head_and_tail_view_and_failures_give_none() -> None:
    model = Scripted("1 of 400 tests failed: test_add (tests/test_calc.py:12).")
    summary, completion = distill.summarize(model, LOG * 3)
    assert summary is not None and "tests/test_calc.py:12" in summary
    assert completion is not None
    sent = model.requests[0].messages[0].text_content()
    assert "[... middle omitted ...]" in sent and len(sent) < len(LOG * 3)
    assert distill.summarize(Scripted(error=ModelError("server", "down")), LOG) == (None, None)
