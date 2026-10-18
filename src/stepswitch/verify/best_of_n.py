"""Best-of-N across model families (SPEC §8.3, ADR 0025).

* `candidate_count(difficulty)` is the SPEC's N: one candidate for difficulty 1 or 2, two for
  3, three for 4 or 5. A task's `best_of` decides whether it applies: 1 (the default) runs one
  candidate, N >= 2 runs N, and 0 means "by difficulty" (`resolve_count`).
* `plan_candidates` gives each candidate a model family: families are ranked by their best
  worker's prior for the task's edit steps, and the top N are used. A candidate routes its
  steps among the workers of its family only (`RouterState.families`). When there are fewer
  families than candidates, the remaining candidates reuse the ranked families in order with
  other seeds.
* `select` picks the winner among finished candidates. In order:
  1. it passed the gates (status `done`);
  2. it passed the most valid cross and probe tests (when the disagreement check ran);
  3. its review does not block (approved, no high-severity issue);
  4. its change is smaller: changed lines outside test files, in bands 25% wide on a log
     scale of (lines + 10), so sizes within a band tie (0-1, 2-4, 5-7, ... lines);
  5. its review score is higher;
  6. it comes first in the plan (the stronger family).

  The SPEC orders gates, then diff size, then review score. Tests passed and the review
  verdict come before size here, because a smaller change that a reviewer rejects, or that
  fails another candidate's test, is not the better one (ADR 0025).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from stepswitch.verify import impact
from stepswitch.verify.review import Review
from stepswitch.workers.base import WorkerSpec

DIFFICULTY_CANDIDATES = {1: 1, 2: 1, 3: 2, 4: 3, 5: 3}
MAX_CANDIDATES = 5
SIZE_BAND = 1.25
SIZE_OFFSET = 10
_FILE = re.compile(r"^\+\+\+ b/(.+)$")


def candidate_count(difficulty: int) -> int:
    """SPEC §8.3: candidates for a task of classifier difficulty 1 to 5."""
    return DIFFICULTY_CANDIDATES[min(5, max(1, int(difficulty)))]


def resolve_count(best_of: int, difficulty: int) -> int:
    """Candidates for a task: `best_of` when it is at least 1, else by difficulty."""
    if best_of >= 1:
        return min(best_of, MAX_CANDIDATES)
    return candidate_count(difficulty)


@dataclass(frozen=True)
class Plan:
    """One candidate: its model family, the workers it may route to, and its seed."""

    index: int
    family: str
    workers: tuple[str, ...]
    seed: int
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "family": self.family,
            "workers": list(self.workers),
            "seed": self.seed,
            "note": self.note,
        }


def plan_candidates(
    specs: Sequence[WorkerSpec],
    count: int,
    *,
    rank: Callable[[WorkerSpec], float],
    seed: int = 0,
) -> list[Plan]:
    """`count` candidates over the families of `specs` (already filtered for eligibility)."""
    if count < 1 or not specs:
        return []
    families: dict[str, list[WorkerSpec]] = {}
    for spec in specs:
        families.setdefault(spec.family_key, []).append(spec)
    ranked = sorted(
        families.items(),
        key=lambda item: (-max(rank(s) for s in item[1]), item[0]),
    )
    plans: list[Plan] = []
    for index in range(count):
        family, members = ranked[index % len(ranked)]
        repeat = index // len(ranked)
        workers = tuple(s.id for s in sorted(members, key=lambda s: (-rank(s), s.id)))
        note = "" if repeat == 0 else f"family {family} again with another seed"
        plans.append(Plan(index, family, workers, seed + index, note))
    return plans


def changed_lines(diff: str) -> int:
    """Added and removed lines of a unified diff, outside test files."""
    total = 0
    counting = False
    for line in diff.splitlines():
        match = _FILE.match(line)
        if match:
            counting = not impact.is_test_file(match.group(1)) and not match.group(1).startswith(
                ("tests/", "test/")
            )
            continue
        if line.startswith("--- ") or line.startswith("diff --git"):
            continue
        if counting and line[:1] in "+-" and not line.startswith(("+++", "---")):
            total += 1
    return total


def size_band(lines: int) -> int:
    return int(math.log(max(0, lines) + SIZE_OFFSET, SIZE_BAND))


@dataclass
class Finished:
    """A finished candidate as selection sees it."""

    index: int
    family: str
    status: str
    changed_lines: int = 0
    review: Review | None = None
    # Valid cross and probe tests it passed; None when the check did not run.
    tests_passed: int | None = None

    def key(self) -> tuple[Any, ...]:
        blocking = self.review.blocking if self.review is not None else False
        score = self.review.score if self.review is not None else 0.5
        return (
            -(self.tests_passed or 0),
            blocking,
            size_band(self.changed_lines),
            -score,
            self.index,
        )


def select(finished: Sequence[Finished], *, among: Sequence[int] | None = None) -> int | None:
    """The winner's index (see the module docstring), or None when no candidate passed.

    `among` limits the choice to these candidates (a cluster that a policy kept).
    """
    passing = [f for f in finished if f.status == "done"]
    if among is not None:
        passing = [f for f in passing if f.index in set(among)] or passing
    if not passing:
        return None
    return min(passing, key=Finished.key).index


def explain(winner: int | None, finished: Sequence[Finished]) -> str:
    if winner is None:
        return "no candidate passed the gates"
    passing = [f for f in finished if f.status == "done"]
    if len(passing) == 1:
        return f"candidate {winner} is the only one that passed the gates"
    best = next(f for f in finished if f.index == winner)
    parts = [f"{len(passing)} candidates passed the gates"]
    if best.tests_passed is not None:
        parts.append(f"{best.tests_passed} cross/probe tests passed")
    if best.review is not None:
        parts.append(f"review score {best.review.score:.2f}")
    parts.append(f"{best.changed_lines} changed lines")
    return f"candidate {winner} ({best.family}): " + ", ".join(parts)
