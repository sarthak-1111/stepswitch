"""Cancelling a task (SPEC §17): a request recorded in the state database stops the task before
it starts or at its next step boundary, whichever process runs it."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import httpx

from stepswitch.api.app import create_app
from stepswitch.compat.ir import Usage
from stepswitch.core.budget import BudgetTracker
from stepswitch.core.orchestrator import run_task
from stepswitch.core.task import Budgets, TaskSpec
from stepswitch.state import open_project_state
from stepswitch.trajectory.record import read_events
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, ScriptedModel
from tests.claims.toy import TOY_INSTRUCTION, toy_repo

READ = Action(tool="read", arguments={"path": "calc.py"})
TOKEN = "cancel-test-token"


def looping_catalog(script: Any = None) -> Catalog:
    model = ScriptedModel(
        script or (lambda request, index: READ), usage=Usage(input_tokens=30, output_tokens=5)
    )
    return Catalog([Worker(WorkerSpec(id="fake/looper", harness="fake", tier="cpu"), model)])


def test_a_cancel_request_stops_the_budget() -> None:
    asked = [False]
    budget = BudgetTracker(Budgets(tokens=10), cancelled=lambda: asked[0])
    budget.charge(tokens=50)
    assert budget.exceeded() == "budget_tokens"
    asked[0] = True
    assert budget.exceeded() == "cancelled"


def test_a_task_cancelled_before_it_starts_never_runs(tmp_path: Path) -> None:
    repo = toy_repo("calc-bug", tmp_path / "repo")
    db = open_project_state(repo)
    db.request_cancel("t-early")
    catalog = looping_catalog()
    spec = TaskSpec(instruction=TOY_INSTRUCTION, repo=repo, max_steps=5)
    result = run_task(spec, root=repo, catalog=catalog, task_id="t-early")
    assert (result.status, result.stop_reason) == ("stopped", "cancelled")
    assert result.summary == "cancelled before it started" and result.steps == 0
    types = [e["type"] for e in read_events(Path(str(result.run_dir)))]
    assert "cancelled" in types and "worktree" not in types
    row = db.query("SELECT status, stop_reason FROM tasks WHERE id = 't-early'")[0]
    assert row == {"status": "stopped", "stop_reason": "cancelled"}
    db.close()


def test_a_running_task_stops_at_its_next_step(tmp_path: Path) -> None:
    repo = toy_repo("calc-bug", tmp_path / "repo")
    other_process = open_project_state(repo)  # a second connection, as `ssw dash` would have

    def script(request: Any, index: int) -> Action:
        if index == 1:
            other_process.request_cancel("t-mid")
        return READ

    spec = TaskSpec(instruction=TOY_INSTRUCTION, repo=repo, max_steps=30)
    result = run_task(spec, root=repo, catalog=looping_catalog(script), task_id="t-mid")
    assert (result.status, result.stop_reason) == ("stopped", "cancelled")
    # The step that saw the request finishes; no further model call is made.
    assert result.steps == 2
    events = read_events(Path(str(result.run_dir)))
    assert [e for e in events if e["type"] == "cancelled"][0]["step"] == 2
    other_process.close()


def _call(app: Any, method: str, path: str, **kwargs: Any) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:7777") as c:
            return await c.request(
                method, path, headers={"authorization": f"Bearer {TOKEN}"}, **kwargs
            )

    return asyncio.run(send())


def test_the_api_cancels_a_task_started_from_the_command_line(tmp_path: Path) -> None:
    repo = toy_repo("calc-bug", tmp_path / "repo")
    reached = threading.Event()
    go_on = threading.Event()

    def script(request: Any, index: int) -> Action:
        if index == 1:
            reached.set()
            go_on.wait(10)
        return READ

    spec = TaskSpec(instruction=TOY_INSTRUCTION, repo=repo, max_steps=30)
    results: list[Any] = []
    cli = threading.Thread(
        target=lambda: results.append(
            run_task(spec, root=repo, catalog=looping_catalog(script), task_id="t-cli")
        )
    )
    cli.start()
    assert reached.wait(10)
    app = create_app(repo, token=TOKEN, port=7777, static_dir=tmp_path / "none")
    cancelled = _call(app, "POST", "/api/tasks/t-cli/cancel")
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "running"
    go_on.set()
    cli.join(20)
    assert (results[0].status, results[0].stop_reason) == ("stopped", "cancelled")
    detail = _call(app, "GET", "/api/tasks/t-cli").json()
    assert (detail["status"], detail["stop_reason"]) == ("stopped", "cancelled")
    assert detail["cancel_requested"] is True
    assert _call(app, "POST", "/api/tasks/t-cli/cancel").status_code == 409
    app.state.services.close()


def test_the_api_runs_and_cancels_a_real_task(tmp_path: Path) -> None:
    repo = toy_repo("calc-bug", tmp_path / "repo")
    reached = threading.Event()
    go_on = threading.Event()

    def script(request: Any, index: int) -> Action:
        if index == 1:
            reached.set()
            go_on.wait(10)
        return READ

    catalog = looping_catalog(script)

    def run(spec: TaskSpec, task_id: str) -> Any:
        return run_task(spec, root=repo, catalog=catalog, task_id=task_id)

    app = create_app(repo, token=TOKEN, port=7777, run=run, static_dir=tmp_path / "none")
    body = {"instruction": TOY_INSTRUCTION, "max_steps": 30, "review": False}
    task_id = _call(app, "POST", "/api/tasks", json=body).json()["id"]
    assert reached.wait(10)
    assert _call(app, "GET", f"/api/tasks/{task_id}").json()["status"] == "running"
    assert _call(app, "POST", f"/api/tasks/{task_id}/cancel").status_code == 202
    go_on.set()
    assert app.state.services.wait(20)
    detail = _call(app, "GET", f"/api/tasks/{task_id}").json()
    assert (detail["status"], detail["stop_reason"]) == ("stopped", "cancelled")
    assert detail["steps"] == 2 and detail["tokens"]["input"] == 60
    types = [e["type"] for e in _call(app, "GET", f"/api/tasks/{task_id}/events").json()["events"]]
    assert types[0] == "task_start" and types[-1] == "task_end" and "cancelled" in types
    app.state.services.close()
