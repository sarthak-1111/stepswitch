"""The slow-lane job queue (SPEC §14 RN9, §25; ADR 0030).

Slow-lane workers (tier `sl`: models served by AirLLM or KTransformers, or any worker the config
marks `tier = "sl"`) take minutes per answer, so the router never gives them an interactive
step. They take jobs from this queue instead:

* `review`: a second opinion on a finished task. The runner reads the task's instruction,
  acceptance commands and diff from the project state, asks the job's worker for a review in
  the cross-family review format (`verify.review`), and appends the outcome to the task's
  trajectory: one `slow_review` event with the verdict, then one `review_comment` event per
  issue. These are the review comments that the task's events, the dashboard and
  `ssw jobs show` list.
* `task`: a whole task run on the slow lane with the job's worker pinned (an overnight
  candidate or a second attempt). Its result is a task of its own, whose id the job records.

A job's worker is the one it names, else the first slow-lane worker of the catalog by id.

Jobs live in the `jobs` table and move `queued` -> `running` -> `done` | `failed` | `cancelled`.
A runner (`ssw jobs run`) claims the oldest queued job in one statement, so several runners can
share a queue, and records itself (process id and start time). A running job whose runner has
stopped goes back to the queue, up to `MAX_ATTEMPTS` attempts. Cancelling a queued job ends it at
once; a running review finishes its model call and then drops the answer, and a running task
is asked to stop like any other task.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stepswitch.state import StateDB
from stepswitch.workers.base import SLOW_TIER

KINDS = ("review", "task")
STATUSES = ("queued", "running", "done", "failed", "cancelled")
FINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
MAX_ATTEMPTS = 3
# How long one model call of a job may take (the slow lane answers in minutes per token).
DEFAULT_TIMEOUT_S = 6 * 3600.0
REVIEW_OUTPUT_TOKENS = 1024


class JobError(ValueError):
    """A job that cannot be submitted, found or run."""


def new_job_id() -> str:
    return f"j{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def _loads(text: str | None) -> Any:
    return json.loads(text) if text else None


@dataclass
class Job:
    id: str
    kind: str
    status: str
    created: float
    updated: float
    task_id: str | None = None
    worker: str | None = None
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0
    started: float | None = None
    finished: float | None = None
    runner: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Job:
        return cls(
            id=row["id"],
            kind=row["kind"],
            status=row["status"],
            created=row["created"],
            updated=row["updated"],
            task_id=row["task_id"],
            worker=row["worker"],
            payload=_loads(row["payload"]) or {},
            result=_loads(row["result"]),
            error=row["error"],
            attempts=int(row["attempts"] or 0),
            started=row["started"],
            finished=row["finished"],
            runner=_loads(row["runner"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created": self.created,
            "updated": self.updated,
            "task_id": self.task_id,
            "worker": self.worker,
            "payload": self.payload,
            "result": self.result,
            "error": self.error,
            "attempts": self.attempts,
            "started": self.started,
            "finished": self.finished,
            "runner": self.runner,
        }


# -- the queue --------------------------------------------------------------------------------


def _task_row(db: StateDB, task_id: str) -> Mapping[str, Any] | None:
    rows = db.query("SELECT id, status, spec, result FROM tasks WHERE id = ?", (task_id,))
    return rows[0] if rows else None


def submit(
    db: StateDB,
    kind: str,
    *,
    task_id: str | None = None,
    worker: str | None = None,
    payload: Mapping[str, Any] | None = None,
    clock: Callable[[], float] = time.time,
) -> Job:
    """Queue a job. A review needs a finished task with a diff; a task job needs a `spec`."""
    if kind not in KINDS:
        raise JobError(f"a job is one of {', '.join(KINDS)}, not {kind!r}")
    data = dict(payload or {})
    if kind == "review":
        if not task_id:
            raise JobError("a review job names the task to review (--task)")
        row = _task_row(db, task_id)
        if row is None:
            raise JobError(f"no task {task_id} in this project")
        result = _loads(row["result"]) or {}
        if row["status"] not in ("done", "failed", "needs-human", "stopped"):
            raise JobError(f"task {task_id} is {row['status']}; review it when it has finished")
        if not str(result.get("diff") or "").strip():
            raise JobError(f"task {task_id} changed nothing; there is no diff to review")
    elif not isinstance(data.get("spec"), Mapping) or not data["spec"].get("instruction"):
        raise JobError("a task job carries the task's spec, with its instruction")
    now = clock()
    job = Job(new_job_id(), kind, "queued", now, now, task_id, worker, data)
    db.execute(
        "INSERT INTO jobs (id, created, updated, kind, status, task_id, worker, payload)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (job.id, now, now, kind, "queued", task_id, worker, json.dumps(data, default=str)),
    )
    return job


def get(db: StateDB, job_id: str) -> Job | None:
    rows = db.query("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return Job.from_row(rows[0]) if rows else None


def list_jobs(
    db: StateDB,
    *,
    status: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
) -> list[Job]:
    """Jobs, newest first."""
    clauses, params = [], []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.query(
        f"SELECT * FROM jobs{where} ORDER BY created DESC, id DESC LIMIT ?",  # noqa: S608
        (*params, max(1, int(limit))),
    )
    return [Job.from_row(row) for row in rows]


def cancel(db: StateDB, job_id: str, *, clock: Callable[[], float] = time.time) -> Job:
    job = get(db, job_id)
    if job is None:
        raise JobError(f"no job {job_id}")
    if job.status in FINAL_STATUSES:
        return job
    now = clock()
    db.execute(
        "UPDATE jobs SET status = 'cancelled', updated = ?, finished = ? WHERE id = ?",
        (now, now, job_id),
    )
    # Read again: a runner records a task job's task id while the job is still running, so a
    # task that started before this update is found here, and one that did not never starts.
    job = get(db, job_id) or job
    child = (job.result or {}).get("task_id")
    if job.kind == "task" and child:
        db.request_cancel(str(child))
    return job


def runner_identity() -> dict[str, Any]:
    from stepswitch.serve.base import process_start_time

    return {"pid": os.getpid(), "start": process_start_time(os.getpid())}


def _runner_alive(runner: Mapping[str, Any] | None) -> bool:
    from stepswitch.serve.base import same_process

    if not runner or not isinstance(runner.get("pid"), int):
        return False
    return bool(same_process(int(runner["pid"]), runner.get("start")))


def requeue_stale(
    db: StateDB,
    *,
    alive: Callable[[Mapping[str, Any] | None], bool] = _runner_alive,
    clock: Callable[[], float] = time.time,
) -> list[str]:
    """Put running jobs whose runner has gone back in the queue (or fail them after
    `MAX_ATTEMPTS` attempts). Returns their ids."""
    moved = []
    for job in list_jobs(db, status="running", limit=1000):
        if alive(job.runner):
            continue
        now = clock()
        if job.attempts >= MAX_ATTEMPTS:
            db.execute(
                "UPDATE jobs SET status = 'failed', updated = ?, finished = ?, error = ?"
                " WHERE id = ? AND status = 'running'",
                (now, now, f"its runner stopped {job.attempts} times", job.id),
            )
        else:
            db.execute(
                "UPDATE jobs SET status = 'queued', updated = ?, runner = NULL"
                " WHERE id = ? AND status = 'running'",
                (now, job.id),
            )
        moved.append(job.id)
    return moved


def claim(
    db: StateDB,
    *,
    runner: Mapping[str, Any] | None = None,
    clock: Callable[[], float] = time.time,
) -> Job | None:
    """Take the oldest queued job for this runner, or None when the queue is empty."""
    who = json.dumps(dict(runner or runner_identity()))
    while True:
        rows = db.query("SELECT id FROM jobs WHERE status = 'queued' ORDER BY created, id LIMIT 1")
        if not rows:
            return None
        now = clock()
        claimed = db.execute(
            "UPDATE jobs SET status = 'running', updated = ?, started = ?, runner = ?,"
            " attempts = attempts + 1, error = NULL WHERE id = ? AND status = 'queued'",
            (now, now, who, rows[0]["id"]),
        )
        if claimed.rowcount == 1:
            return get(db, rows[0]["id"])
        # Another runner took it first: try the next one.


def finish(
    db: StateDB,
    job: Job,
    *,
    result: Mapping[str, Any] | None = None,
    error: str | None = None,
    clock: Callable[[], float] = time.time,
) -> Job:
    """Record a running job's outcome; a job cancelled meanwhile stays cancelled."""
    now = clock()
    db.execute(
        "UPDATE jobs SET status = ?, updated = ?, finished = ?, result = ?, error = ?"
        " WHERE id = ? AND status = 'running'",
        (
            "failed" if error else "done",
            now,
            now,
            json.dumps(dict(result), default=str) if result is not None else None,
            error,
            job.id,
        ),
    )
    return get(db, job.id) or job


# -- running jobs -----------------------------------------------------------------------------


def slow_workers(catalog: Any) -> list[Any]:
    return sorted(
        (w for w in catalog.workers.values() if w.spec.tier == SLOW_TIER),
        key=lambda w: w.spec.id,
    )


def pick_worker(catalog: Any, wanted: str | None) -> Any:
    """The job's worker: the one it names (any worker), else the first slow-lane worker."""
    if wanted:
        worker = catalog.find(wanted)
        if worker is None:
            raise JobError(f"unknown worker {wanted!r}")
        return worker
    found = slow_workers(catalog)
    if not found:
        raise JobError(
            "no slow-lane worker is configured (an AirLLM or KTransformers model, or a model "
            'with tier = "sl"); name a worker with --worker'
        )
    return found[0]


def timeout_seconds(config: Any) -> float:
    table = (getattr(config, "model_extra", None) or {}).get("jobs") or {}
    value = table.get("timeout_seconds") if isinstance(table, Mapping) else None
    return float(value) if isinstance(value, int | float) and value > 0 else DEFAULT_TIMEOUT_S


def run_review(
    job: Job,
    *,
    root: Path,
    db: StateDB,
    catalog: Any,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Review the job's task with the job's worker; the comments go onto the task."""
    from stepswitch.compat.ir import Message, Request
    from stepswitch.trajectory.record import Trajectory
    from stepswitch.verify import review as review_module

    if not job.task_id:
        raise JobError("the review job names no task")
    row = _task_row(db, job.task_id)
    if row is None:
        raise JobError(f"task {job.task_id} is gone")
    spec = _loads(row["spec"]) or {}
    result = _loads(row["result"]) or {}
    worker = pick_worker(catalog, job.worker)
    request = Request(
        model=worker.spec.model,
        messages=[
            Message.text("system", review_module.REVIEW_PROMPT),
            Message.text(
                "user",
                review_module.request_text(
                    str(spec.get("instruction") or ""),
                    [str(c) for c in spec.get("acceptance") or []],
                    str(result.get("diff") or ""),
                ),
            ),
        ],
        max_output_tokens=REVIEW_OUTPUT_TOKENS,
    )
    started = clock()
    completion = worker.get_model().complete(request)
    text = completion.response.message.text_content()
    review = review_module.parse(text) or review_module.Review(
        False,
        [review_module.Issue("medium", "", 0, "the answer was neither JSON nor a verdict")],
        False,
        text[: review_module.TEXT_CHARS],
    )
    review.reviewer = worker.spec.id
    review.reviewer_family = worker.spec.family_key
    families = []
    for name in result.get("endpoints") or []:
        author = catalog.find(str(name))
        families.append(author.spec.family_key if author is not None else str(name))
    review.author_families = tuple(dict.fromkeys(families))
    current = get(db, job.id)
    if current is not None and current.status == "cancelled":
        return {"cancelled": True, "reviewer": worker.spec.id}
    trajectory = Trajectory.resume(root, job.task_id, db=db)
    verdict = review.as_dict()
    trajectory.emit("slow_review", job=job.id, **verdict)
    for issue in review.issues:
        trajectory.emit("review_comment", job=job.id, reviewer=worker.spec.id, **issue.as_dict())
    usage = completion.response.usage
    return {
        "review": verdict,
        "reviewer": worker.spec.id,
        "comments": len(review.issues),
        "seconds": round(clock() - started, 3),
        "tokens": usage.model_dump() if usage is not None else None,
    }


def run_task_job(
    job: Job,
    *,
    root: Path,
    config: Any,
    db: StateDB,
    catalog: Any,
    egress: Any = None,
    echo: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the job's task on the slow lane, with its worker pinned. The task's id goes on the
    job first, so that cancelling the job stops the task."""
    from stepswitch.core.orchestrator import run_task
    from stepswitch.core.task import TaskSpec, new_task_id

    worker = pick_worker(catalog, job.worker)
    data = dict((job.payload or {}).get("spec") or {})
    data.update(worker=worker.spec.id, slow_lane=True)
    data.setdefault("repo", str(root))
    spec = TaskSpec.model_validate(data)
    task_id = new_task_id()
    marked = db.execute(
        "UPDATE jobs SET result = ? WHERE id = ? AND status = 'running'",
        (json.dumps({"task_id": task_id}), job.id),
    )
    if marked.rowcount != 1:  # cancelled before the task started
        return {"cancelled": True, "worker": worker.spec.id}
    result = run_task(
        spec, root=root, config=config, catalog=catalog, egress=egress, echo=echo, task_id=task_id
    )
    return {
        "task_id": result.task_id,
        "status": result.status,
        "stop_reason": result.stop_reason,
        "worker": worker.spec.id,
        "changed_files": result.changed_files,
    }


def run_one(
    job: Job,
    *,
    root: Path,
    config: Any,
    db: StateDB,
    catalog: Any,
    egress: Any = None,
    echo: Callable[[str], None] | None = None,
    clock: Callable[[], float] = time.time,
) -> Job:
    """Run a claimed job to its outcome and record it."""
    try:
        if job.kind == "review":
            outcome = run_review(job, root=root, db=db, catalog=catalog, clock=clock)
        else:
            outcome = run_task_job(
                job, root=root, config=config, db=db, catalog=catalog, egress=egress, echo=echo
            )
    except Exception as exc:  # noqa: BLE001 - any failure is the job's outcome
        return finish(db, job, error=f"{type(exc).__name__}: {exc}", clock=clock)
    return finish(db, job, result=outcome, clock=clock)


def run_queue(
    *,
    root: Path,
    config: Any,
    db: StateDB,
    catalog_factory: Callable[[], Any],
    egress: Any = None,
    echo: Callable[[str], None] = lambda _msg: None,
    max_jobs: int | None = None,
    clock: Callable[[], float] = time.time,
) -> list[Job]:
    """Run queued jobs, oldest first, until the queue is empty (or `max_jobs` ran)."""
    requeued = requeue_stale(db, clock=clock)
    if requeued:
        echo(f"requeued {len(requeued)} job(s) whose runner stopped: {', '.join(requeued)}")
    done: list[Job] = []
    while max_jobs is None or len(done) < max_jobs:
        job = claim(db, clock=clock)
        if job is None:
            break
        echo(f"job {job.id}: {job.kind} (attempt {job.attempts})")
        finished = run_one(
            job,
            root=root,
            config=config,
            db=db,
            catalog=catalog_factory(),
            egress=egress,
            echo=echo,
            clock=clock,
        )
        detail = finished.error or ""
        echo(f"job {job.id}: {finished.status}{': ' + detail if detail else ''}")
        done.append(finished)
    return done
