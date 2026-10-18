"""Cross-family review: the SPEC §8.5 schema, reading answers, routing (ADR 0025)."""

from __future__ import annotations

import json

from stepswitch.router.features import TaskFeatures
from stepswitch.router.router import Decision, RouterState, route
from stepswitch.verify import review
from stepswitch.verify.review import REVIEW_SCHEMA, Issue, Review, parse, request_text
from stepswitch.workers.base import WorkerSpec

A_MID = WorkerSpec(id="gpu/a-mid", endpoint="gpu", model="a-mid", tier="t2", family="alpha")
A_BIG = WorkerSpec(id="gpu/a-big", endpoint="gpu", model="a-big", tier="t3", family="alpha")
B_MID = WorkerSpec(id="gpu/b-mid", endpoint="gpu", model="b-mid", tier="t2", family="beta")
B_BIG = WorkerSpec(id="gpu/b-big", endpoint="gpu", model="b-big", tier="t3", family="beta")
FEATURES = TaskFeatures()
ALPHA = frozenset({"alpha"})


def review_route(workers: list[WorkerSpec], level: int, **state: object) -> Decision:
    return route(
        FEATURES,
        "review",
        RouterState(step=1, level=level, avoid_families=ALPHA, **state),  # type: ignore[arg-type]
        workers,
    )


def test_cross_review_schema_is_the_spec_shape() -> None:
    assert set(REVIEW_SCHEMA["required"]) == {"approve", "issues"}
    assert REVIEW_SCHEMA["additionalProperties"] is False
    assert REVIEW_SCHEMA["properties"]["approve"] == {"type": "boolean"}
    issues = REVIEW_SCHEMA["properties"]["issues"]
    assert issues["maxItems"] == review.MAX_ISSUES
    item = issues["items"]
    assert set(item["required"]) == {"severity", "file", "line", "note"}
    assert item["additionalProperties"] is False
    assert item["properties"]["severity"]["enum"] == ["low", "medium", "high"]
    assert item["properties"]["line"] == {"type": "integer", "minimum": 0}
    assert "Step: review" in review.REVIEW_PROMPT


def test_cross_review_answer_is_read_bare_fenced_or_after_text() -> None:
    answer = {
        "approve": False,
        "issues": [{"severity": "high", "file": "calc.py", "line": 2, "note": "still subtracts"}],
    }
    text = json.dumps(answer)
    for wrapped in (text, f"```json\n{text}\n```", f"Here is my review:\n{text}\nThanks."):
        parsed = parse(wrapped)
        assert parsed is not None and parsed.constrained
        assert parsed.approve is False
        assert parsed.issues == [Issue("high", "calc.py", 2, "still subtracts")]


def test_cross_review_reading_is_lenient_about_issue_shapes() -> None:
    issues = [
        "a bare string",
        {"severity": "critical", "path": "a.py", "line": "12-14", "message": "alias keys"},
        {"severity": "nit", "file": "b.py", "line": -3, "note": "negative line"},
        {"severity": "whatever", "file": "c.py", "line": True, "note": "odd severity"},
        {"severity": "low", "file": "d.py", "line": 1, "note": ""},
        *[{"severity": "low", "file": "e.py", "line": n, "note": f"n{n}"} for n in range(10)],
    ]
    parsed = parse(json.dumps({"approved": True, "issues": issues}))
    assert parsed is not None and parsed.approve
    assert len(parsed.issues) == review.MAX_ISSUES
    first, second, third, fourth = parsed.issues[:4]
    assert first == Issue("medium", "", 0, "a bare string")
    assert second == Issue("high", "a.py", 12, "alias keys")
    assert third == Issue("low", "b.py", 0, "negative line")
    assert fourth == Issue("medium", "c.py", 0, "odd severity")
    assert all(issue.note for issue in parsed.issues)  # the empty note was dropped


def test_cross_review_falls_back_to_the_phase_2_text_format() -> None:
    approved = parse("APPROVE. Looks right.")
    assert approved is not None and approved.approve and not approved.constrained
    issues = parse("**ISSUES**\n- the empty list case is not handled")
    assert issues is not None and not issues.approve and not issues.constrained
    assert issues.issues[0].severity == "medium" and "empty list" in issues.issues[0].note
    assert parse("I think it is probably fine") is None
    assert parse('{"approve": "yes", "issues": []}') is None


def test_cross_review_score_and_blocking() -> None:
    assert Review(True).score == 1.0 and not Review(True).blocking
    low = Review(True, [Issue("low", "a.py", 1, "style")])
    assert low.score == 0.975 and not low.blocking
    high = Review(True, [Issue("high", "a.py", 1, "wrong")])
    assert high.score == 0.75 and high.blocking
    assert Review(False).blocking and Review(False).score == 0.5
    many = Review(True, [Issue("high", "a.py", n, "wrong") for n in range(4)])
    assert many.score == 0.5  # the issue penalty bottoms out at zero
    assert Review(False, [Issue("high", "a.py", 1, "x"), Issue("high", "b.py", 1, "y")]).score == 0


def test_cross_review_knows_whether_it_crossed_families() -> None:
    assert not Review(True).cross_family
    assert not Review(True, reviewer_family="beta").cross_family
    assert not Review(True, reviewer_family="alpha", author_families=("alpha",)).cross_family
    crossed = Review(
        False,
        [Issue("medium", "calc.py", 0, "no test for zero")],
        reviewer="gpu/b-mid",
        reviewer_family="beta",
        author_families=("alpha",),
    )
    assert crossed.cross_family
    data = crossed.as_dict()
    assert data["cross_family"] is True and data["reviewer_family"] == "beta"
    assert data["author_families"] == ["alpha"] and data["approved"] is False
    assert data["issues"] == [
        {"severity": "medium", "file": "calc.py", "line": 0, "note": "no test for zero"}
    ]
    assert data["text"] == "- [medium] calc.py: no test for zero"


def test_cross_review_feedback_and_request_text() -> None:
    assert Issue("high", "a.py", 3, "x").text() == "[high] a.py:3: x"
    assert Issue("low", "", 0, "y").text() == "[low] y"
    assert Review(False, text="ISSUES: vague").feedback() == "ISSUES: vague"
    assert Review(False).feedback() == "The reviewer disapproved."
    text = request_text(" Fix it. ", [], "")
    assert text.startswith("Task: Fix it.") and "- the project's tests pass" in text
    assert text.endswith("(empty)")
    long = request_text("Fix it.", ["pytest -q"], "x" * (review.DIFF_CHARS + 50))
    assert "- pytest -q" in long and long.endswith("[diff cut]")


def test_cross_review_goes_to_another_family_on_the_same_rung() -> None:
    decision = review_route([A_MID, B_MID], level=1)
    assert decision.chosen == B_MID.id
    excluded = {c.worker: c.excluded for c in decision.candidates}
    assert "same model family" in (excluded[A_MID.id] or "")


def test_cross_review_moves_to_another_rung_for_another_family() -> None:
    up = review_route([A_MID, B_BIG], level=1)
    assert up.chosen == B_BIG.id
    assert any("cross-family review on rung" in note for note in up.notes)
    assert {c.worker: c.relaxed for c in up.candidates}[B_BIG.id] is True
    down = review_route([A_BIG, B_MID], level=2)
    assert down.chosen == B_MID.id


def test_cross_review_falls_back_to_the_authors_family_when_alone() -> None:
    decision = review_route([A_MID, A_BIG], level=1)
    assert decision.chosen == A_MID.id
    assert "no worker of another model family for the review" in decision.notes


def test_cross_review_preference_yields_to_pins_and_other_steps() -> None:
    pinned = review_route([A_MID, B_MID], level=1, worker_pin=A_MID.id)
    assert pinned.chosen == A_MID.id
    edit = route(
        FEATURES,
        "edit",
        RouterState(step=1, level=1, avoid_families=ALPHA, families=ALPHA),
        [A_MID, B_MID],
    )
    assert edit.chosen == A_MID.id
