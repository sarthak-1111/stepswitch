"""The slow-lane job queue (ADR 0030, `stepswitch.core.jobs`): submitting, claiming, cancelling
and requeueing jobs; a review job's comments on the reviewed task; slow-lane workers in the
catalog; and the `ssw jobs` commands and `/api/jobs`."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from stepswitch.cli import app
from stepswitch.compat.ir import Request, Usage
from stepswitch.config import Config
from stepswitch.core import jobs
from stepswitch.core.task import TaskResult, TaskSpec
from stepswitch.state import StateDB, open_project_state
from stepswitch.trajectory.record import Trajectory, read_events
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, ScriptedModel

DIFF = (
    "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n-    return a - b\n+    return a + b\n"
)
REVIEW = {
    "approve": False,
    "issues": [
        {"severity": "high", "file": "calc.py", "line": 2, "note": "no test covers add()"},
        {"severity": "low", "file": "", "line": 0, "note": "describe the fix in the commit"},
    ],
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def db(project: Path) -> Any:
    state = open_project_state(project)
    yield state
    state.close()


def finished_task(
    project: Path, db: StateDB, *, task_id: str = "t-1", status: str = "done", diff: str = DIFF
) -> str:
    spec = TaskSpec(instruction="Fix add() in calc.py.", repo=project, acceptance=["pytest -q"])
    result = TaskResult(task_id=task_id, status=status, diff=diff, endpoints=["alpha"])  # type: ignore[arg-type]
    now = time.time()
    db.execute(
        "INSERT INTO tasks (id, created, updated, status, stop_reason, track, instruction, spec,"
        " result) VALUES (?, ?, ?, ?, NULL, 'general', ?, ?, ?)",
        (
            task_id,
            now,
            now,
            status,
            spec.instruction,
            spec.model_dump_json(),
            result.model_dump_json(),
        ),
    )
    trajectory = Trajectory(project, task_id, db=db)
    trajectory.emit("task_start", instruction=spec.instruction)
    trajectory.emit("task_end", status=status)
    return task_id


def slow_worker(answer: str = json.dumps(REVIEW), *, worker_id: str = "slow/huge") -> Worker:
    model = ScriptedModel([Action(text=answer)], usage=Usage(input_tokens=900, output_tokens=60))
    spec = WorkerSpec(id=worker_id, endpoint="slow", tier="sl", pin_only=True, family="hugefam")
    return Worker(spec, model)


def author() -> Worker:
    spec = WorkerSpec(id="fake/alpha", endpoint="alpha", tier="t2", family="alphafam")
    return Worker(spec, ScriptedModel([]))


def run(project: Path, db: StateDB, catalog: Catalog, **kwargs: Any) -> list[jobs.Job]:
    return jobs.run_queue(
        root=project, config=Config(), db=db, catalog_factory=lambda: catalog, **kwargs
    )


def test_submit_checks_the_job(project: Path, db: StateDB) -> None:
    with pytest.raises(jobs.JobError, match="one of review, task"):
        jobs.submit(db, "summary")
    with pytest.raises(jobs.JobError, match="names the task"):
        jobs.submit(db, "review")
    with pytest.raises(jobs.JobError, match="no task t-404"):
        jobs.submit(db, "review", task_id="t-404")
    finished_task(project, db, task_id="t-run", status="running")
    with pytest.raises(jobs.JobError, match="review it when it has finished"):
        jobs.submit(db, "review", task_id="t-run")
    finished_task(project, db, task_id="t-empty", diff="")
    with pytest.raises(jobs.JobError, match="no diff to review"):
        jobs.submit(db, "review", task_id="t-empty")
    with pytest.raises(jobs.JobError, match="carries the task's spec"):
        jobs.submit(db, "task", payload={"spec": {}})
    job = jobs.submit(db, "review", task_id=finished_task(project, db))
    assert job.status == "queued" and jobs.get(db, job.id) == job


def test_a_review_job_puts_its_comments_on_the_task(project: Path, db: StateDB) -> None:
    task_id = finished_task(project, db)
    worker = slow_worker()
    job = jobs.submit(db, "review", task_id=task_id)
    (done,) = run(project, db, Catalog([worker, author()]))
    assert done.status == "done", done.error
    assert done.result is not None and done.result["reviewer"] == "slow/huge"
    assert done.result["comments"] == 2 and done.result["tokens"]["output_tokens"] == 60
    review = done.result["review"]
    assert review["approved"] is False and review["blocking"] is True
    assert review["reviewer_family"] == "hugefam"
    request: Request = worker.get_model().requests[0]  # type: ignore[attr-defined]
    asked = request.messages[-1].text_content()
    assert "Fix add() in calc.py." in asked and "return a + b" in asked and "pytest -q" in asked
    assert "Step: review" in request.messages[0].text_content()
    events = read_events(project / ".stepswitch" / "runs" / task_id)
    assert [e["type"] for e in events] == [
        "task_start",
        "task_end",
        "slow_review",
        "review_comment",
        "review_comment",
    ]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]  # the sequence continues
    assert events[2]["job"] == job.id and events[3]["note"] == "no test covers add()"
    rows = db.query("SELECT type FROM events WHERE task_id = ? ORDER BY seq", (task_id,))
    assert [r["type"] for r in rows][-3:] == ["slow_review", "review_comment", "review_comment"]


def test_an_answer_that_is_no_review_is_still_recorded(project: Path, db: StateDB) -> None:
    jobs.submit(db, "review", task_id=finished_task(project, db))
    (done,) = run(project, db, Catalog([slow_worker("I am not sure.")]))
    assert done.status == "done"
    assert done.result is not None and done.result["review"]["constrained"] is False
    assert "neither JSON nor a verdict" in done.result["review"]["text"]


def test_without_a_slow_lane_worker_the_job_fails_with_the_reason(
    project: Path, db: StateDB
) -> None:
    jobs.submit(db, "review", task_id=finished_task(project, db))
    (failed,) = run(project, db, Catalog([author()]))
    assert failed.status == "failed" and "no slow-lane worker is configured" in str(failed.error)
    named = jobs.submit(db, "review", task_id="t-1", worker="nowhere/model")
    (unknown,) = run(project, db, Catalog([slow_worker()]))
    assert unknown.id == named.id and "unknown worker 'nowhere/model'" in str(unknown.error)


def test_a_named_worker_reviews_even_when_it_is_not_slow(project: Path, db: StateDB) -> None:
    jobs.submit(db, "review", task_id=finished_task(project, db), worker="fake/alpha")
    reviewer = Worker(
        author().spec, ScriptedModel([Action(text='{"approve": true, "issues": []}')])
    )
    (done,) = run(project, db, Catalog([reviewer]))
    assert done.status == "done" and done.result is not None
    assert done.result["review"]["approved"] is True and done.result["comments"] == 0


def test_cancelling(project: Path, db: StateDB) -> None:
    task_id = finished_task(project, db)
    queued = jobs.submit(db, "review", task_id=task_id)
    assert jobs.cancel(db, queued.id).status == "cancelled"
    assert run(project, db, Catalog([slow_worker()])) == []  # nothing left to claim
    with pytest.raises(jobs.JobError, match="no job"):
        jobs.cancel(db, "j-404")

    during = jobs.submit(db, "review", task_id=task_id)

    def cancel_then_answer(request: Request, index: int) -> Action:
        jobs.cancel(db, during.id)  # while the slow model is still answering
        return Action(text=json.dumps(REVIEW))

    worker = Worker(slow_worker().spec, ScriptedModel(cancel_then_answer))
    (job,) = run(project, db, Catalog([worker]))
    assert job.status == "cancelled"  # the answer is dropped
    events = read_events(project / ".stepswitch" / "runs" / task_id)
    assert not [e for e in events if e["type"] == "slow_review"]


def test_claims_take_each_job_once(project: Path, db: StateDB) -> None:
    task_id = finished_task(project, db)
    first = jobs.submit(db, "review", task_id=task_id, clock=lambda: 1.0)
    second = jobs.submit(db, "review", task_id=task_id, clock=lambda: 2.0)
    a = jobs.claim(db, runner={"pid": 1})
    b = jobs.claim(db, runner={"pid": 2})
    assert a is not None and b is not None
    assert (a.id, b.id) == (first.id, second.id)
    assert a.status == "running" and a.attempts == 1 and a.runner == {"pid": 1}
    assert jobs.claim(db) is None


def test_jobs_of_a_runner_that_stopped_are_requeued_then_failed(project: Path, db: StateDB) -> None:
    job = jobs.submit(db, "review", task_id=finished_task(project, db))
    gone = {"pid": 999999, "start": None}
    for attempt in range(1, jobs.MAX_ATTEMPTS + 1):
        claimed = jobs.claim(db, runner=gone)
        assert claimed is not None and claimed.attempts == attempt
        assert jobs.requeue_stale(db, alive=lambda runner: False) == [job.id]
        status = jobs.get(db, job.id).status  # type: ignore[union-attr]
        assert status == ("failed" if attempt == jobs.MAX_ATTEMPTS else "queued")
    assert "its runner stopped 3 times" in str(jobs.get(db, job.id).error)  # type: ignore[union-attr]
    assert jobs.requeue_stale(db, alive=lambda runner: True) == []


def test_slow_lane_workers_are_pin_only_and_never_interactive(
    project: Path, tmp_path: Path
) -> None:
    from stepswitch.core.orchestrator import run_task
    from tests.claims.toy import TOY_INSTRUCTION, toy_repo

    config = Config.model_validate(
        {
            "endpoints": [
                {
                    "name": "lane",
                    "url": "http://127.0.0.1:9",
                    "engine": "airllm",
                    "models": ["huge"],
                },
                {"name": "box", "url": "http://127.0.0.1:8", "models": ["big:70b", "giant"]},
            ],
            "models": {"giant": {"tier": "sl"}},
            "storage": {"models_dir": str(tmp_path / "models")},
        }
    )
    specs = {s.id: s for s in Catalog.from_config(config, project, include_managed=False).specs()}
    for worker_id in ("lane/huge", "box/giant"):
        assert specs[worker_id].tier == "sl" and specs[worker_id].pin_only
        assert "slow lane: asynchronous jobs only (`ssw jobs`)" in specs[worker_id].notes
    assert specs["box/big:70b"].tier == "t2" and not specs["box/big:70b"].pin_only
    catalog = Catalog([slow_worker(), author()])
    assert catalog.levels() == [1]  # the ladder never reaches the slow lane

    repo = toy_repo("calc-bug", tmp_path / "repo")
    spec = TaskSpec(instruction=TOY_INSTRUCTION, repo=repo, worker="slow/huge", max_steps=4)
    result = run_task(spec, root=repo, catalog=catalog)  # every failure ends as a status
    assert (result.status, result.stop_reason) == ("failed", "error")
    assert "slow-lane worker, for asynchronous jobs only" in result.summary
    assert catalog.workers["slow/huge"].get_model().calls == 0  # type: ignore[attr-defined]


def test_cli_submit_list_show_cancel_and_run(
    project: Path, db: StateDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = finished_task(project, db)
    monkeypatch.chdir(project)
    runner = CliRunner()
    submitted = runner.invoke(app, ["jobs", "submit", "review", "--task", task_id, "--json"])
    assert submitted.exit_code == 0, submitted.output
    job_id = json.loads(submitted.output)["id"]
    listed = runner.invoke(app, ["jobs", "list", "--json"])
    assert [j["id"] for j in json.loads(listed.output)["jobs"]] == [job_id]
    shown = runner.invoke(app, ["jobs", "show", job_id])
    assert job_id in shown.output and "queued" in shown.output
    bad = runner.invoke(app, ["jobs", "submit", "task"])
    assert bad.exit_code != 0 and "needs the instruction" in bad.output
    task_job = runner.invoke(app, ["jobs", "submit", "task", "Add a docstring.", "--json"])
    assert json.loads(task_job.output)["payload"]["spec"]["instruction"] == "Add a docstring."
    cancelled = runner.invoke(app, ["jobs", "cancel", json.loads(task_job.output)["id"]])
    assert "cancelled" in cancelled.output
    # No slow-lane worker is configured in this project: the review fails with the reason.
    ran = runner.invoke(app, ["jobs", "run", "--json"])
    assert ran.exit_code == 1
    (failed,) = json.loads(ran.output)["jobs"]
    assert failed["id"] == job_id and "no slow-lane worker" in failed["error"]


def test_api_lists_and_shows_jobs(project: Path, db: StateDB) -> None:
    from stepswitch.api.app import create_app
    from tests.unit.test_api import PORT, TOKEN, api

    job = jobs.submit(db, "review", task_id=finished_task(project, db))
    application = create_app(project, token=TOKEN, port=PORT)
    listed = api(application, "GET", "/api/jobs")
    assert listed.status_code == 200 and [j["id"] for j in listed.json()["jobs"]] == [job.id]
    assert api(application, "GET", "/api/jobs?status=done").json()["jobs"] == []
    assert api(application, "GET", "/api/jobs?status=nope").status_code == 422
    assert api(application, "GET", f"/api/jobs/{job.id}").json()["kind"] == "review"
    assert api(application, "GET", "/api/jobs/j-404").status_code == 404


def test_a_task_job_runs_the_task_with_its_slow_lane_worker_pinned(tmp_path: Path) -> None:
    from tests.claims.toy import TOY_INSTRUCTION, toy_repo

    repo = toy_repo("calc-bug", tmp_path / "repo")
    state = open_project_state(repo)
    try:
        finisher = ScriptedModel(
            lambda request, index: (
                Action(tool="finish", arguments={"summary": "Done."})
                if request.tools
                else Action(text="APPROVE")
            )
        )
        worker = Worker(slow_worker().spec, finisher)
        spec = {"instruction": TOY_INSTRUCTION, "repo": str(repo), "max_steps": 4}
        job = jobs.submit(state, "task", payload={"spec": spec})
        (done,) = run(repo, state, Catalog([worker, author()]))
        assert done.id == job.id and done.status == "done", done.error
        assert done.result is not None and done.result["worker"] == "slow/huge"
        child = state.query("SELECT spec FROM tasks WHERE id = ?", (done.result["task_id"],))
        recorded = json.loads(child[0]["spec"])
        assert recorded["worker"] == "slow/huge" and recorded["slow_lane"] is True
        assert finisher.calls >= 1  # the slow-lane worker took the steps
    finally:
        state.close()


def test_cancelling_a_running_task_job_stops_its_task(tmp_path: Path) -> None:
    from tests.claims.toy import TOY_INSTRUCTION, toy_repo

    repo = toy_repo("calc-bug", tmp_path / "repo")
    state = open_project_state(repo)
    try:
        spec = {"instruction": TOY_INSTRUCTION, "repo": str(repo), "max_steps": 6}
        job = jobs.submit(state, "task", payload={"spec": spec})

        def cancel_then_read(request: Request, index: int) -> Action:
            if index == 0:
                running = jobs.get(state, job.id)
                assert running is not None and running.result  # the task id is already there
                jobs.cancel(state, job.id)
            return Action(tool="read", arguments={"path": "calc.py"})

        worker = Worker(slow_worker().spec, ScriptedModel(cancel_then_read))
        (done,) = run(repo, state, Catalog([worker, author()]))
        assert done.status == "cancelled"
        assert done.result is not None
        child = state.query(
            "SELECT status, stop_reason FROM tasks WHERE id = ?", (done.result["task_id"],)
        )
        assert (child[0]["status"], child[0]["stop_reason"]) == ("stopped", "cancelled")
        assert worker.get_model().calls == 1  # type: ignore[attr-defined]

        # A job cancelled between its claim and the start of its task never starts the task.
        late = jobs.submit(state, "task", payload={"spec": spec})
        claimed = jobs.claim(state)
        assert claimed is not None and claimed.id == late.id
        jobs.cancel(state, late.id)
        outcome = jobs.run_task_job(
            claimed, root=repo, config=Config(), db=state, catalog=Catalog([worker, author()])
        )
        assert outcome["cancelled"] is True and worker.get_model().calls == 1  # type: ignore[attr-defined]
    finally:
        state.close()
