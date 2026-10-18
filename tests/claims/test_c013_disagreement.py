"""C-013: flags tasks where passing candidates disagree.

Proof (SPEC §18.5): seeded ambiguous tasks are flagged; unambiguous ones are not. Scope: seeded
ambiguous and unambiguous tasks with at least two passing candidates.

The seeded tasks (`disagreement_fixture`) run through the orchestrator with two candidates
(`best_of=2`) from different model families and the `flag` policy. Each task must end with at
least two candidates that passed the gates, or it does not count as a seed. The orchestrator
then compares the passing candidates (SPEC §8.4): every test a candidate added runs against
every other candidate's code, and science tasks compare the golden outputs of the candidates
with each other at the case's tolerances. The claim holds when every ambiguous task is flagged
and no unambiguous one is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stepswitch.core.orchestrator import run_task
from stepswitch.core.task import TaskSpec
from tests.claims import evidence
from tests.claims.disagreement_fixture import SEEDED

pytestmark = [pytest.mark.claim("C-013")]


def test_ambiguous_tasks_flagged_unambiguous_not(tmp_path: Path) -> None:
    limits = evidence.thresholds("C-013")
    rows: list[dict[str, Any]] = []
    for task in SEEDED:
        repo = task.make(tmp_path / task.name)
        spec = TaskSpec(
            instruction=task.instruction,
            repo=repo,
            track=task.track,  # type: ignore[arg-type]
            best_of=2,
            on_disagreement="flag",
            max_steps=10,
        )
        result = run_task(spec, root=repo, catalog=task.catalog(repo))
        passing = [c for c in result.candidates if c.get("status") == "done"]
        report = result.disagreement or {}
        rows.append(
            {
                "task": task.name,
                "track": task.track,
                "ambiguous": task.ambiguous,
                "status": result.status,
                "candidates": [
                    {"family": c.get("family"), "status": c.get("status")}
                    for c in result.candidates
                ],
                "passing": len(passing),
                "flagged": bool(report.get("detected")),
                "uncertain": result.uncertain,
                "splits": report.get("splits", [])[:5],
                "winner": result.winner,
            }
        )
    seeds = [row for row in rows if row["passing"] >= 2]
    ambiguous = [row for row in seeds if row["ambiguous"]]
    clear = [row for row in seeds if not row["ambiguous"]]
    ambiguous_flagged = (
        sum(row["flagged"] for row in ambiguous) / len(ambiguous) if ambiguous else 0
    )
    clear_flagged = sum(row["flagged"] for row in clear) / len(clear) if clear else 1
    evidence.write(
        "C-013",
        "seeded",
        {
            "tasks": rows,
            "ambiguous": len(ambiguous),
            "unambiguous": len(clear),
            "ambiguous_flagged": ambiguous_flagged,
            "unambiguous_flagged": clear_flagged,
        },
    )
    invalid = [row["task"] for row in rows if row["passing"] < 2]
    assert not invalid, f"seeds without two passing candidates: {invalid}"
    assert ambiguous_flagged >= limits["ambiguous_flagged"], rows
    assert clear_flagged <= limits["unambiguous_flagged"], rows
