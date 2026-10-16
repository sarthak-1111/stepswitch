"""Background execution of the tasks submitted through the task API (ADR 0023).

Tasks run on a small thread pool, `max_tasks` at a time, each through the same `run_task` the
CLI uses. A task is recorded as queued before it is submitted, so it is visible (and can be
cancelled) while it waits. Whatever happens inside, every task ends with a final status.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from typing import Any

from stepswitch.core.orchestrator import queue_task
from stepswitch.core.task import FINAL_STATUSES, TaskSpec, new_task_id
from stepswitch.state import StateDB

RunTask = Callable[[TaskSpec, str], Any]


def stop_queued(db: StateDB, task_id: str) -> bool:
    """End a task that has not started yet as stopped (cancelled); False if it had started."""
    result = {
        "task_id": task_id,
        "status": "stopped",
        "stop_reason": "cancelled",
        "summary": "cancelled before it started",
    }
    cursor = db.execute(
        "UPDATE tasks SET updated = ?, status = 'stopped', stop_reason = 'cancelled', result = ?"
        " WHERE id = ? AND status = 'queued'",
        (time.time(), json.dumps(result), task_id),
    )
    return cursor.rowcount > 0


def mark_failed(db: StateDB, task_id: str, exc: BaseException) -> None:
    """Record a failure that escaped the orchestrator, unless the task already ended."""
    final = tuple(sorted(FINAL_STATUSES))
    result = {
        "task_id": task_id,
        "status": "failed",
        "stop_reason": "error",
        "summary": f"{type(exc).__name__}: {exc}"[:2000],
    }
    db.execute(
        "UPDATE tasks SET updated = ?, status = 'failed', stop_reason = 'error', result = ?"
        f" WHERE id = ? AND status NOT IN ({', '.join('?' for _ in final)})",
        (time.time(), json.dumps(result), task_id, *final),
    )


class TaskRunner:
    """Runs submitted tasks in the background, `max_tasks` at a time."""

    def __init__(self, db: StateDB, run: RunTask, *, max_tasks: int = 1) -> None:
        if max_tasks < 1:
            raise ValueError("max_tasks must be at least 1")
        self.db = db
        self.max_tasks = max_tasks
        self._run = run
        self._pool = ThreadPoolExecutor(max_workers=max_tasks, thread_name_prefix="ssw-task")
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.Lock()
        self._closed = False

    def submit(self, spec: TaskSpec) -> str:
        """Queue a task and return its id."""
        with self._lock:
            if self._closed:
                raise RuntimeError("the task runner is shutting down")
            task_id = new_task_id()
            queue_task(self.db, task_id, spec)
            self._futures[task_id] = self._pool.submit(self._work, task_id, spec)
        return task_id

    def pending(self) -> list[str]:
        """Ids of the tasks submitted here that have not finished."""
        with self._lock:
            return sorted(key for key, future in self._futures.items() if not future.done())

    def wait(self, timeout: float | None = None) -> bool:
        """Block until every task submitted here has finished; False on timeout."""
        with self._lock:
            # A future cancelled by shutdown() is never notified, so wait() would block on it.
            futures = [f for f in self._futures.values() if not f.cancelled()]
        _, not_done = futures_wait(futures, timeout=timeout)
        return not not_done

    def abandon(self) -> list[str]:
        """Record the unfinished tasks as stopped (cancelled), for a forced exit that does not
        wait for them; returns their ids."""
        ids = self.pending()
        final = tuple(sorted(FINAL_STATUSES))
        for task_id in ids:
            result = {
                "task_id": task_id,
                "status": "stopped",
                "stop_reason": "cancelled",
                "summary": "stopped when the dashboard server exited",
            }
            self.db.execute(
                "UPDATE tasks SET updated = ?, status = 'stopped', stop_reason = 'cancelled',"
                f" result = ? WHERE id = ? AND status NOT IN ({', '.join('?' for _ in final)})",
                (time.time(), json.dumps(result), task_id, *final),
            )
        return ids

    def _work(self, task_id: str, spec: TaskSpec) -> None:
        try:
            if self.db.cancel_requested(task_id):
                stop_queued(self.db, task_id)
                return
            self._run(spec, task_id)
        except Exception as exc:  # noqa: BLE001 - the task must still end with a status
            mark_failed(self.db, task_id, exc)
        finally:
            with self._lock:
                self._futures.pop(task_id, None)

    def shutdown(self, *, wait: bool = True) -> list[str]:
        """Cancel every task submitted here that has not ended; with `wait`, block until the
        running ones stop at their next step boundary. Returns the cancelled ids."""
        with self._lock:
            self._closed = True
        ids = self.pending()
        for task_id in ids:
            self.db.request_cancel(task_id)
            stop_queued(self.db, task_id)
        self._pool.shutdown(wait=wait, cancel_futures=True)
        return ids
