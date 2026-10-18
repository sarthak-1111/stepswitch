"""Best-of-N: candidates per difficulty, family plans and selection (SPEC §8.3, ADR 0025)."""

from __future__ import annotations

import pytest

from stepswitch.router.features import TaskFeatures
from stepswitch.router.router import RouterState, hard_exclusion, route
from stepswitch.verify.best_of_n import (
    Finished,
    candidate_count,
    changed_lines,
    explain,
    plan_candidates,
    resolve_count,
    select,
    size_band,
)
from stepswitch.verify.review import Issue, Review
from stepswitch.workers.base import WorkerSpec

A_MID = WorkerSpec(id="gpu/a-mid", endpoint="gpu", model="a-mid", tier="t2", family="alpha")
A_BIG = WorkerSpec(id="gpu/a-big", endpoint="gpu", model="a-big", tier="t3", family="alpha")
B_MID = WorkerSpec(id="gpu/b-mid", endpoint="gpu", model="b-mid", tier="t2", family="beta")
C_BIG = WorkerSpec(id="gpu/c-big", endpoint="gpu", model="c-big", tier="t3", family="gamma")
NO_FAMILY = WorkerSpec(id="gpu/plain", endpoint="gpu", model="plain-7b", tier="t2")
RANK = {A_MID.id: 0.5, A_BIG.id: 0.8, B_MID.id: 0.6, C_BIG.id: 0.7, NO_FAMILY.id: 0.1}


def rank(spec: WorkerSpec) -> float:
    return RANK[spec.id]


@pytest.mark.parametrize(("difficulty", "count"), [(1, 1), (2, 1), (3, 2), (4, 3), (5, 3)])
def test_best_of_n_count_follows_difficulty(difficulty: int, count: int) -> None:
    assert candidate_count(difficulty) == count
    assert resolve_count(0, difficulty) == count


def test_best_of_n_explicit_count_wins_and_is_capped() -> None:
    assert resolve_count(1, 5) == 1
    assert resolve_count(2, 1) == 2
    assert resolve_count(9, 3) == 5
    assert candidate_count(0) == 1 and candidate_count(7) == 3


def test_best_of_n_plans_rank_families_by_their_best_worker() -> None:
    plans = plan_candidates([A_MID, A_BIG, B_MID, C_BIG], 3, rank=rank, seed=10)
    assert [p.family for p in plans] == ["alpha", "gamma", "beta"]
    assert plans[0].workers == (A_BIG.id, A_MID.id)  # strongest first
    assert [p.seed for p in plans] == [10, 11, 12]
    assert [p.index for p in plans] == [0, 1, 2]
    assert all(p.note == "" for p in plans)


def test_best_of_n_reuses_families_with_other_seeds() -> None:
    plans = plan_candidates([A_MID, B_MID], 3, rank=rank)
    assert [p.family for p in plans] == ["beta", "alpha", "beta"]
    assert plans[2].seed == 2 and "again with another seed" in plans[2].note
    # a model without a family is its own family
    assert plan_candidates([NO_FAMILY], 1, rank=rank)[0].family == "plain-7b"
    assert plan_candidates([], 2, rank=rank) == []
    assert plan_candidates([A_MID], 0, rank=rank) == []


def test_best_of_n_plans_are_stable_as_more_are_asked_for() -> None:
    two = plan_candidates([A_MID, B_MID, C_BIG], 2, rank=rank)
    three = plan_candidates([A_MID, B_MID, C_BIG], 3, rank=rank)
    assert three[:2] == two


DIFF = """\
diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,3 @@
-def add(a, b):
+def add(a, b):  # fixed
+    return a + b
diff --git a/tests/test_calc.py b/tests/test_calc.py
--- a/tests/test_calc.py
+++ b/tests/test_calc.py
@@ -0,0 +1,2 @@
+def test_add():
+    assert add(1, 2) == 3
"""


def test_best_of_n_counts_changed_lines_outside_tests() -> None:
    assert changed_lines(DIFF) == 3
    assert changed_lines("") == 0


def test_best_of_n_size_bands_tie_near_equal_sizes() -> None:
    # bands 25% wide on (lines + 10): 0-1, 2-4, 5-7, ... lines; 40 and 45 lines tie
    assert size_band(0) == size_band(1) < size_band(2) == size_band(4) < size_band(5)
    assert size_band(40) == size_band(45)
    assert size_band(10) < size_band(40) < size_band(200)


def review(approve: bool, *severities: str) -> Review:
    return Review(approve, [Issue(s, "a.py", 1, "x") for s in severities])


def test_best_of_n_selection_order() -> None:
    # 1. only candidates that passed the gates
    assert select([Finished(0, "alpha", "failed"), Finished(1, "beta", "done")]) == 1
    assert select([Finished(0, "alpha", "failed"), Finished(1, "beta", "stopped")]) is None
    # 2. more valid cross and probe tests passed beats a smaller change
    small = Finished(0, "alpha", "done", changed_lines=2, tests_passed=1)
    tested = Finished(1, "beta", "done", changed_lines=300, tests_passed=3)
    assert select([small, tested]) == 1
    # 3. a blocking review loses to a non-blocking one
    blocked = Finished(0, "alpha", "done", changed_lines=2, review=review(True, "high"))
    fine = Finished(1, "beta", "done", changed_lines=300, review=review(True, "medium"))
    assert select([blocked, fine]) == 1
    # 4. then the smaller change, in size bands
    big = Finished(0, "alpha", "done", changed_lines=300, review=review(True))
    tidy = Finished(1, "beta", "done", changed_lines=20, review=review(True, "low", "low"))
    assert select([big, tidy]) == 1
    # 5. within a band the review score decides
    near = Finished(0, "alpha", "done", changed_lines=21, review=review(True, "medium"))
    assert select([near, tidy]) == 1
    # 6. then the plan's order
    twin = Finished(2, "gamma", "done", changed_lines=20, review=review(True, "low", "low"))
    assert select([twin, tidy]) == 1


def test_best_of_n_selection_within_a_kept_cluster() -> None:
    first = Finished(0, "alpha", "done", changed_lines=5)
    second = Finished(1, "beta", "done", changed_lines=500)
    assert select([first, second]) == 0
    assert select([first, second], among=[1]) == 1
    # a kept cluster without a passing candidate does not empty the choice
    assert select([first, Finished(1, "beta", "failed")], among=[1]) == 0


def test_best_of_n_explains_the_choice() -> None:
    assert explain(None, []) == "no candidate passed the gates"
    one = [Finished(0, "alpha", "done"), Finished(1, "beta", "failed")]
    assert explain(0, one) == "candidate 0 is the only one that passed the gates"
    two = [
        Finished(0, "alpha", "done", changed_lines=4, review=review(True), tests_passed=2),
        Finished(1, "beta", "done"),
    ]
    text = explain(0, two)
    assert text.startswith("candidate 0 (alpha): 2 candidates passed the gates")
    assert "2 cross/probe tests passed" in text and "review score 1.00" in text
    assert text.endswith("4 changed lines")


def test_best_of_n_candidates_route_within_their_family() -> None:
    state = RouterState(step=1, level=1, families=frozenset({"beta"}))
    assert "another best-of-N candidate" in (hard_exclusion(A_MID, "edit", state) or "")
    assert hard_exclusion(B_MID, "edit", state) is None
    # reviews may go to any family
    assert hard_exclusion(A_MID, "review", state) is None
    decision = route(TaskFeatures(), "edit", state, [A_MID, B_MID])
    assert decision.chosen == B_MID.id
    # no family restriction outside best-of-N
    assert hard_exclusion(A_MID, "edit", RouterState(step=1)) is None
