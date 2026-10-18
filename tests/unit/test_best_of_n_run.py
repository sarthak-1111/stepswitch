"""Best-of-N through the orchestrator: candidates, policies, selection, results (SPEC §8.3-8.5).

The seeded tasks of the disagreement claim provide two scripted candidates per task, from the
model families "alpha" and "beta"; `round-price` is ambiguous (alpha rounds halves up, beta
to even), `add-numbers` is not.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from stepswitch.cli import app
from stepswitch.compat.ir import Request
from stepswitch.core.orchestrator import run_task
from stepswitch.core.task import Budgets, TaskResult, TaskSpec
from stepswitch.trajectory.record import read_events
from stepswitch.workers.base import Worker
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action
from tests.claims import science_fixture
from tests.claims.disagreement_fixture import FAMILIES, SEEDED, SeededTask, candidate

TASKS = {task.name: task for task in SEEDED}
REVIEW_JSON = json.dumps({"approve": True, "issues": []})
TARGETED = (
    "```python\nfrom pricing import round_price\n\n\ndef test_halves_go_up():\n"
    "    assert round_price(2.5) == 3\n    assert round_price(0.5) == 1\n```"
)
PROBE = (
    "```python\nfrom pricing import round_price\n\n\ndef test_clear_cases():\n"
    "    assert round_price(2.4) == 2\n    assert round_price(3.0) == 3\n```"
)


def answers(request: Request) -> str | None:
    """Text answers: a schema review, probe tests and targeted tests."""
    if request.response_format is not None:
        return REVIEW_JSON
    text = request.model_dump_json()
    if "settle a disagreement" in text:
        return TARGETED
    if "pin down what a coding task asks for" in text:
        return PROBE
    return None


def catalog(task: SeededTask, repo: Path, answer: Callable[[Request], str | None]) -> Catalog:
    workers: list[Worker] = []
    for family in FAMILIES:
        worker = candidate(family, task.files_for(family, repo), repo)
        model: Any = worker.model
        inner = model.script

        def script(request: Request, index: int, inner: Any = inner) -> Action:
            text = None if request.tools else answer(request)
            return Action(text=text) if text is not None else inner(request, index)

        model.script = script
        workers.append(worker)
    return Catalog(workers)


def run(
    tmp_path: Path,
    name: str,
    *,
    answer: Callable[[Request], str | None] = answers,
    **spec: Any,
) -> tuple[TaskResult, Path]:
    task = TASKS[name]
    repo = task.make(tmp_path / name)
    options: dict[str, Any] = {"best_of": 2, "max_steps": 10} | spec
    result = run_task(
        TaskSpec(instruction=task.instruction, repo=repo, track=task.track, **options),  # type: ignore[arg-type]
        root=repo,
        catalog=catalog(task, repo, answer),
    )
    return result, repo


def branches(repo: Path) -> set[str]:
    out = science_fixture.git(repo, "branch", "--list", "stepswitch/*", "--format=%(refname:short)")
    return {line.strip() for line in out.splitlines() if line.strip()}


def test_best_of_n_flags_disagreement_and_applies_nothing(tmp_path: Path) -> None:
    result, repo = run(tmp_path, "round-price", apply=True)
    assert result.status == "done" and result.best_of == 2
    assert [c["family"] for c in result.candidates] == ["alpha", "beta"]
    assert [c["status"] for c in result.candidates] == ["done", "done"]
    assert result.uncertain and result.disagreement is not None
    assert result.disagreement["detected"] and result.disagreement["policy"] == "flag"
    assert result.disagreement["probe_tests"] == 1
    # nothing applied: the repository still lacks round_price
    assert "round_price" not in (repo / "pricing.py").read_text(encoding="utf-8")
    assert result.winner in (0, 1)
    loser = 1 - result.winner
    assert branches(repo) == {
        f"stepswitch/{result.task_id}",
        f"stepswitch/{result.task_id}-c{loser}",
    }
    assert result.branch == f"stepswitch/{result.task_id}"
    run_dir = Path(result.run_dir)
    for index in (0, 1):
        assert (run_dir / f"candidate-{index}.patch").is_file()
    assert (run_dir / "diff.patch").read_text(encoding="utf-8") == result.diff
    # each candidate was reviewed by the other family, under the schema
    for entry in result.candidates:
        review = entry["review"]
        assert review["cross_family"] and review["constrained"] and review["approved"]
        assert review["reviewer_family"] != entry["family"]
    events = read_events(run_dir)
    types = [event["type"] for event in events]
    for kind in ("best_of_n", "candidate_end", "probe_tests", "disagreement", "selection"):
        assert kind in types, kind
    assert "not_applied" in types and "applied" not in types
    seqs = [event["seq"] for event in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    tagged = {event.get("candidate") for event in events if event["type"] == "decision"}
    assert tagged == {0, 1}


def test_best_of_n_agreeing_candidates_are_applied(tmp_path: Path) -> None:
    result, repo = run(tmp_path, "add-numbers", apply=True)
    assert result.status == "done" and not result.uncertain
    assert result.disagreement is not None and not result.disagreement["detected"]
    assert result.disagreement["clusters"] == [[0, 1]]
    assert result.changed_files and all(
        (repo / rel).read_text(encoding="utf-8") for rel in result.changed_files
    )
    assert "def add(" in (repo / "calc.py").read_text(encoding="utf-8")


def test_best_of_n_human_policy_ends_the_task_for_a_person(tmp_path: Path) -> None:
    result, repo = run(tmp_path, "round-price", on_disagreement="human", apply=True)
    assert result.status == "needs-human" and result.uncertain
    assert len(branches(repo)) == 2
    assert "round_price" not in (repo / "pricing.py").read_text(encoding="utf-8")


def test_best_of_n_escalation_keeps_the_majority(tmp_path: Path) -> None:
    result, repo = run(tmp_path, "round-price", on_disagreement="escalate", apply=True)
    assert [c["family"] for c in result.candidates] == ["alpha", "beta", "alpha"]
    assert "another seed" in result.candidates[2]["note"]
    report = result.disagreement or {}
    assert report["detected"] and report["resolved"] and report["kept"] == [0, 2]
    assert not result.uncertain and result.winner in (0, 2)
    assert "int(value + 0.5)" in (repo / "pricing.py").read_text(encoding="utf-8")


def test_best_of_n_targeted_tests_settle_the_split(tmp_path: Path) -> None:
    result, repo = run(tmp_path, "round-price", on_disagreement="tests", apply=True)
    report = result.disagreement or {}
    assert report["detected"] and report["resolved"] and report["kept"] == [0]
    assert "targeted tests kept candidates 0" in report["resolution"]
    assert result.winner == 0 and not result.uncertain
    assert "int(value + 0.5)" in (repo / "pricing.py").read_text(encoding="utf-8")
    types = [event["type"] for event in read_events(Path(result.run_dir))]
    assert "targeted_tests" in types


def test_best_of_n_targeted_tests_that_decide_nothing_leave_it_uncertain(tmp_path: Path) -> None:
    def no_tests(request: Request) -> str | None:
        return REVIEW_JSON if request.response_format is not None else "No tests."

    result, _ = run(tmp_path, "round-price", on_disagreement="tests", answer=no_tests)
    report = result.disagreement or {}
    assert report["detected"] and not report["resolved"] and result.uncertain
    assert "no targeted tests were written" in report["notes"]
    assert report["probe_tests"] == 0


def test_best_of_n_skips_candidates_over_the_budget(tmp_path: Path) -> None:
    result, _ = run(tmp_path, "add-numbers", budgets=Budgets(tokens=300))
    assert len(result.candidates) == 1 and result.best_of == 2
    events = read_events(Path(result.run_dir))
    skipped = [event for event in events if event["type"] == "candidate_skipped"]
    assert [event["candidate"] for event in skipped] == [1]


def test_best_of_one_keeps_the_single_candidate_path(tmp_path: Path) -> None:
    result, repo = run(tmp_path, "add-numbers", best_of=1)
    assert result.status == "done" and result.best_of == 1
    assert result.candidates == [] and result.winner is None
    assert result.disagreement is None and not result.uncertain
    assert result.review is not None and result.review["approved"]
    assert branches(repo) == {f"stepswitch/{result.task_id}"}
    types = [event["type"] for event in read_events(Path(result.run_dir))]
    assert "best_of_n" not in types and "review" in types


def test_best_of_n_cli_options_are_checked(tmp_path: Path) -> None:
    runner = CliRunner()
    for args, message in (
        (["--best-of", "9"], "--best-of must be a number from 1 to 5, or auto"),
        (["--best-of", "two"], "--best-of must be a number from 1 to 5, or auto"),
        (["--on-disagreement", "vote"], "--on-disagreement must be one of flag, tests"),
    ):
        result = runner.invoke(app, ["--project", str(tmp_path), "run", "Fix it.", *args])
        assert result.exit_code == 1, result.output
        assert message in result.output
