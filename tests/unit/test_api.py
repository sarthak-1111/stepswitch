"""The task API (docs/API.md): access control, read models and task submission.

Requests go through httpx's ASGI transport, so no socket is opened; the live event stream is
tested against a real server in test_api_live.py.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch.api import app as app_module
from stepswitch.api import queries
from stepswitch.api.app import create_app
from stepswitch.config import Config
from stepswitch.state import StateDB, open_project_state

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "test-token-0123456789"
PORT = 7777
BASE = f"http://127.0.0.1:{PORT}"
AUTH = {"authorization": f"Bearer {TOKEN}"}
SECRET = "very-secret-value"
REMOTE_URL = "https://remote.example.invalid/v1"
NOW = 1_760_600_000.0
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def request(
    app: Any, method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            return await client.request(method, path, headers=headers, **kwargs)

    return asyncio.run(send())


def api(app: Any, method: str, path: str, **kwargs: Any) -> httpx.Response:
    return request(app, method, path, headers=AUTH, **kwargs)


def make_config(tmp_path: Path, **extra: Any) -> Config:
    return Config.model_validate(
        {
            "storage": {"models_dir": str(tmp_path / "models")},
            "endpoints": [
                {
                    "name": "ws-local",
                    "url": "http://127.0.0.1:11434",
                    "engine": "ollama",
                    "models": ["qwen2.5:32b"],
                },
                {
                    "name": "remote",
                    "url": REMOTE_URL,
                    "auth": "bearer",
                    "api_key": SECRET,
                    "models": ["big-model"],
                },
            ],
            **extra,
        }
    )


def _task(db: StateDB, task_id: str, status: str, created: float, **fields: Any) -> None:
    spec = {"instruction": fields.get("instruction", "Fix it"), "worker": fields.get("worker")}
    db.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            created,
            fields.get("updated", created + 1.0),
            status,
            fields.get("stop_reason"),
            fields.get("track", "general"),
            spec["instruction"],
            json.dumps(spec),
            json.dumps(fields["result"]) if "result" in fields else None,
        ),
    )


def _ledger(db: StateDB, task_id: str, step: int, endpoint: str, model: str, **n: Any) -> None:
    db.execute(
        "INSERT INTO ledger (task_id, step, ts, step_type, endpoint, model, input_tokens,"
        " output_tokens, cached_input_tokens, reasoning_tokens, wall_seconds, gpu_seconds,"
        " usage_source, data) VALUES (?, ?, ?, 'edit', ?, ?, ?, ?, ?, 0, ?, ?, 'server', '{}')",
        (
            task_id,
            step,
            n.get("ts", NOW + step),
            endpoint,
            model,
            n["input"],
            n["output"],
            n.get("cached", 0),
            n.get("wall", 1.0),
            n.get("gpu", 0.0),
        ),
    )


def _event(db: StateDB, task_id: str, seq: int, kind: str, step: int | None, **data: Any) -> None:
    db.execute(
        "INSERT INTO events (task_id, ts, seq, type, step, data) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, NOW + seq, seq, kind, step, json.dumps(data)),
    )


def _decision(
    db: StateDB, task_id: str, step: int, chosen: str, switched: bool, **data: Any
) -> None:
    db.execute(
        "INSERT INTO decisions (task_id, step, ts, step_type, chosen, switched, reason, data)"
        " VALUES (?, ?, ?, 'edit', ?, ?, ?, ?)",
        (task_id, step, NOW + step, chosen, int(switched), "test", json.dumps(data)),
    )


def seed(root: Path) -> Path:
    """Two tasks (one done, one running) with their ledger, decisions, events and audit rows.
    Returns the plot file of the done task's golden gate."""
    db = open_project_state(root)
    run = root / ".stepswitch" / "runs" / "t-done"
    plot = run / "artifacts" / "gates-1" / "golden" / "case--x.png"
    plot.parent.mkdir(parents=True)
    plot.write_bytes(PNG)
    (run / "diff.patch").write_text("--- a/dates.py\n+++ b/dates.py\n", encoding="utf-8")
    (run / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (root / ".stepswitch" / "local.toml").write_text(f'api_key = "{SECRET}"\n', encoding="utf-8")
    result = {
        "task_id": "t-done",
        "status": "done",
        "summary": "Fixed the date parsing.",
        "steps": 3,
        "switches": 1,
        "escalations": 1,
        "wall_seconds": 42.5,
        "changed_files": ["dates.py"],
        "verdict": {"passed": True},
    }
    _task(db, "t-done", "done", NOW, updated=NOW + 42.5, result=result, instruction="Fix dates")
    _task(db, "t-run", "running", NOW + 100, track="science", instruction="Port the fit")
    _ledger(db, "t-done", 1, "ws-local", "qwen2.5:32b", input=1000, output=100, cached=800)
    _ledger(db, "t-done", 2, "remote", "big-model", input=500, output=50, gpu=2.0)
    _ledger(
        db, "t-run", 1, "ws-local", "qwen2.5:32b", input=6100, output=420, cached=5000, wall=9.8
    )
    _decision(db, "t-done", 1, "ws-local/qwen2.5:32b", False, level=1)
    _decision(db, "t-done", 2, "remote/big-model", True, previous="ws-local/qwen2.5:32b", level=3)
    _decision(db, "t-run", 1, "ws-local/qwen2.5:32b", False, level=1)
    _event(db, "t-done", 1, "task_start", None)
    _event(db, "t-done", 2, "classify", 0, features={"task_type": "bugfix", "difficulty": 2})
    _event(db, "t-done", 3, "escalation", 2, to="remote/big-model")
    golden = {
        "name": "golden",
        "status": "failed",
        "summary": "1 of 1 cases out of tolerance",
        "details": {
            "status": "failed",
            "cases": [{"case": "case", "status": "failed", "error": None}],
            "table": "| case | quantity |",
            "plots": [str(plot), str(root / "elsewhere.png")],
        },
    }
    _event(db, "t-done", 4, "gates", 2, run=1, verdict={"passed": False, "results": [golden]})
    _event(db, "t-done", 5, "task_end", None, status="done")
    _event(db, "t-run", 1, "task_start", None)
    for allowed, host in ((1, "127.0.0.1"), (0, "remote.example.invalid")):
        db.record_audit(
            {
                "ts": NOW,
                "method": "POST",
                "scheme": "http",
                "host": host,
                "port": 11434,
                "path": "/v1/chat/completions",
                "purpose": "model",
                "task_id": "t-done",
                "allowed": bool(allowed),
                "local": bool(allowed),
                "status": 200 if allowed else None,
                "bytes_out": 10,
                "bytes_in": 20,
                "error": None if allowed else "blocked",
            }
        )
    db.close()
    return plot


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".stepswitch").mkdir(parents=True)
    return root


@pytest.fixture
def static(tmp_path: Path) -> Path:
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text("<!doctype html><title>Stepswitch</title>", "utf-8")
    (folder / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return folder


def make_app(project: Path, static: Path | None, tmp_path: Path, **kwargs: Any) -> Any:
    kwargs.setdefault("config", make_config(tmp_path))
    return create_app(project, token=TOKEN, port=PORT, static_dir=static, **kwargs)


@pytest.fixture
def app(project: Path, static: Path, tmp_path: Path) -> Any:
    seed(project)
    application = make_app(project, static, tmp_path)
    yield application
    application.state.services.shutdown()
    application.state.services.close()


# --------------------------------------------------------------------------------------------
# Access
# --------------------------------------------------------------------------------------------


def test_every_api_route_needs_the_token(app: Any) -> None:
    bare = request(app, "GET", "/api/tasks")
    assert bare.status_code == 401
    assert bare.json() == {"error": "unauthorized"}
    wrong = request(app, "GET", "/api/tasks", headers={"authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert request(app, "GET", "/api/openapi.json").status_code == 401
    assert api(app, "GET", "/api/tasks").status_code == 200
    cookie = {"cookie": f"ssw_token_{PORT}={TOKEN}"}
    assert request(app, "GET", "/api/tasks", headers=cookie).status_code == 200
    other_port = {"cookie": f"ssw_token_7778={TOKEN}"}
    assert request(app, "GET", "/api/tasks", headers=other_port).status_code == 401


def test_the_token_link_sets_a_strict_cookie_and_leaves_the_address(app: Any) -> None:
    response = request(app, "GET", f"/tasks?token={TOKEN}&tab=open")
    assert response.status_code == 303
    assert response.headers["location"] == "/tasks?tab=open"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"ssw_token_{PORT}={TOKEN}")
    assert "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    wrong = request(app, "GET", "/?token=guess")
    assert wrong.status_code == 303 and wrong.headers["location"] == "/"
    assert "set-cookie" not in wrong.headers


def test_other_hosts_and_cross_site_posts_are_refused(app: Any) -> None:
    rebound = request(app, "GET", "/api/tasks", headers={**AUTH, "host": f"evil.example:{PORT}"})
    assert rebound.status_code == 403
    page = request(app, "GET", "/", headers={"host": "evil.example"})
    assert page.status_code == 403
    cross = request(
        app,
        "POST",
        "/api/tasks",
        headers={**AUTH, "origin": "http://evil.example"},
        json={"instruction": "x"},
    )
    assert cross.status_code == 403
    assert cross.json() == {"error": "cross-site request"}
    assert request(app, "GET", "/", headers={"host": f"localhost:{PORT}"}).status_code == 200


def test_responses_carry_security_headers(app: Any) -> None:
    page = request(app, "GET", "/")
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert api(app, "GET", "/api/tasks").headers["cache-control"] == "no-store"


def test_dashboard_files_and_the_single_page_fallback(app: Any) -> None:
    assert "<title>Stepswitch</title>" in request(app, "GET", "/").text
    assert request(app, "GET", "/assets/app.js").text == "console.log('ok')"
    assert "<title>Stepswitch</title>" in request(app, "GET", "/tasks/t-done").text
    assert request(app, "GET", "/assets/missing.js").status_code == 404
    missing = api(app, "GET", "/api/nothing")
    assert missing.status_code == 404 and missing.json() == {"error": "route not found"}


def test_without_a_built_dashboard_the_page_says_how_to_build_it(
    project: Path, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    application = make_app(project, empty, tmp_path)
    page = request(application, "GET", "/")
    assert page.status_code == 503 and "npm run build" in page.text
    assert api(application, "GET", "/api/tasks").status_code == 200


# --------------------------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------------------------


def test_task_list_and_detail(app: Any) -> None:
    tasks = api(app, "GET", "/api/tasks").json()["tasks"]
    assert [t["id"] for t in tasks] == ["t-run", "t-done"]
    done = next(t for t in tasks if t["id"] == "t-done")
    assert done["tokens"] == {"input": 1500, "output": 150, "cached_input": 800, "reasoning": 0}
    assert (done["steps"], done["switches"], done["escalations"]) == (3, 1, 1)
    assert done["wall_seconds"] == 42.5 and done["gpu_seconds"] == 2.0
    assert done["summary"] == "Fixed the date parsing." and done["cancel_requested"] is False
    running = api(app, "GET", "/api/tasks?status=running").json()["tasks"]
    assert [t["id"] for t in running] == ["t-run"]
    assert running[0]["track"] == "science" and running[0]["wall_seconds"] > 0

    detail = api(app, "GET", "/api/tasks/t-done").json()
    assert detail["result"]["diff"].startswith("--- a/dates.py")
    assert detail["events"] == 5
    assert [d["chosen"] for d in detail["decisions"]] == [
        "ws-local/qwen2.5:32b",
        "remote/big-model",
    ]
    assert detail["decisions"][1]["previous"] == "ws-local/qwen2.5:32b"
    assert detail["decisions"][1]["switched"] is True
    assert [row["model"] for row in detail["ledger"]] == ["qwen2.5:32b", "big-model"]
    assert set(detail["ledger"][0]) == set(queries.LEDGER_COLUMNS)
    assert api(app, "GET", "/api/tasks/nope").status_code == 404


def test_task_events_after_a_sequence_number(app: Any) -> None:
    events = api(app, "GET", "/api/tasks/t-done/events?after=3").json()["events"]
    assert [(e["seq"], e["type"]) for e in events] == [(4, "gates"), (5, "task_end")]
    assert events[0]["data"]["run"] == 1
    assert api(app, "GET", "/api/tasks/nope/events").status_code == 404
    trajectories = api(app, "GET", "/api/trajectories").json()["trajectories"]
    assert {t["id"]: t["events"] for t in trajectories} == {"t-run": 1, "t-done": 5}


def _wait_for(check: Callable[[], bool], timeout: float = 10.0) -> None:
    pause = threading.Event()
    for _ in range(int(timeout / 0.05)):
        if check():
            return
        pause.wait(0.05)
    raise AssertionError("condition not met in time")


def test_submitted_tasks_run_in_the_background(project: Path, static: Path, tmp_path: Path) -> None:
    seen: list[tuple[str, Any]] = []

    def run(spec: Any, task_id: str) -> None:
        seen.append((task_id, spec))
        db = open_project_state(project)
        db.execute(
            "UPDATE tasks SET status = 'done', updated = updated + 1, result = ? WHERE id = ?",
            (json.dumps({"summary": "ok", "steps": 1}), task_id),
        )
        db.close()

    application = make_app(project, static, tmp_path, run=run)
    body = {
        "instruction": "Fix the failing date test",
        "acceptance": ["python -m pytest -q"],
        "budgets": {"tokens": 1000},
        "workers": ["ws-local/qwen2.5:32b"],
        "max_steps": 7,
        "best_of": 2,
        "on_disagreement": "escalate",
    }
    created = api(application, "POST", "/api/tasks", json=body)
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert created.json()["status"] == "queued"
    services = application.state.services
    assert services.wait(10)
    detail = api(application, "GET", f"/api/tasks/{task_id}").json()
    assert detail["status"] == "done" and detail["summary"] == "ok"
    ((ran_id, spec),) = seen
    assert ran_id == task_id
    assert spec.repo == project.resolve() and spec.budgets.tokens == 1000
    assert spec.max_steps == 7 and spec.workers == ["ws-local/qwen2.5:32b"]
    assert spec.best_of == 2 and spec.on_disagreement == "escalate" and spec.probe_tests
    services.shutdown()
    services.close()


def test_a_recorded_run_replays_through_the_api(tmp_path: Path) -> None:
    from tests.claims import replay_fixture

    recorded = replay_fixture.record("calc-fix", tmp_path)
    task_id = recorded.result.task_id
    application = create_app(recorded.root, token=TOKEN, port=PORT)
    created = api(application, "POST", f"/api/runs/{task_id}/replay", json={})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "queued" and body["replay_of"] == task_id
    services = application.state.services
    assert services.wait(120)
    detail = api(application, "GET", f"/api/tasks/{body['id']}").json()
    assert detail["status"] == "done", detail
    report = api(application, "GET", f"/api/runs/{body['id']}/files/replay.json").json()
    assert report["identical_diffs"] and report["identical_gate_results"], report
    assert report["model_calls"]["live"] == 0 and report["departures"] == []
    missing = api(application, "POST", "/api/runs/t1999-000000-abcdef/replay", json={})
    assert missing.status_code == 404
    assert api(application, "POST", "/api/runs/a.b/replay", json={}).status_code == 404
    assert api(application, "POST", "/api/runs/..%2F..%2Fx/replay", json={}).status_code >= 400
    late = api(application, "POST", f"/api/runs/{task_id}/replay", json={"from_step": 99})
    assert late.status_code == 422 and "between 1 and" in late.text
    nobody = api(application, "POST", f"/api/runs/{task_id}/replay", json={"worker": "nobody"})
    assert nobody.status_code == 422 and "unknown worker" in nobody.text
    zero = api(application, "POST", f"/api/runs/{task_id}/replay", json={"from_step": 0})
    assert zero.status_code == 422
    services.shutdown()
    services.close()


def test_task_bodies_are_validated(app: Any, tmp_path: Path) -> None:
    missing = api(app, "POST", "/api/tasks", json={})
    assert missing.status_code == 422 and "instruction" in missing.json()["error"]
    extra = api(app, "POST", "/api/tasks", json={"instruction": "x", "apply": True})
    assert extra.status_code == 422
    track = api(app, "POST", "/api/tasks", json={"instruction": "x", "track": "music"})
    assert track.status_code == 422
    many = api(app, "POST", "/api/tasks", json={"instruction": "x", "best_of": 6})
    assert many.status_code == 422
    policy = api(app, "POST", "/api/tasks", json={"instruction": "x", "on_disagreement": "vote"})
    assert policy.status_code == 422
    nowhere = api(
        app, "POST", "/api/tasks", json={"instruction": "x", "repo": str(tmp_path / "no")}
    )
    assert nowhere.status_code == 422 and nowhere.json() == {"error": "repo: not a directory"}


def test_a_failing_run_still_ends_the_task(project: Path, static: Path, tmp_path: Path) -> None:
    def run(spec: Any, task_id: str) -> None:
        raise RuntimeError("engine exploded")

    application = make_app(project, static, tmp_path, run=run)
    task_id = api(application, "POST", "/api/tasks", json={"instruction": "x"}).json()["id"]
    assert application.state.services.wait(10)
    detail = api(application, "GET", f"/api/tasks/{task_id}").json()
    assert (detail["status"], detail["stop_reason"]) == ("failed", "error")
    assert "engine exploded" in detail["summary"]
    application.state.services.close()


def test_cancelling_a_queued_task_stops_it_before_it_runs(
    project: Path, static: Path, tmp_path: Path
) -> None:
    release = threading.Event()
    started: list[str] = []

    def run(spec: Any, task_id: str) -> None:
        started.append(task_id)
        release.wait(10)
        db = open_project_state(project)
        db.execute(
            "UPDATE tasks SET status = 'done', updated = updated + 1 WHERE id = ?", (task_id,)
        )
        db.close()

    application = make_app(project, static, tmp_path, run=run, max_tasks=1)
    first = api(application, "POST", "/api/tasks", json={"instruction": "one"}).json()["id"]
    second = api(application, "POST", "/api/tasks", json={"instruction": "two"}).json()["id"]
    _wait_for(lambda: started == [first])
    cancelled = api(application, "POST", f"/api/tasks/{second}/cancel")
    assert cancelled.status_code == 202
    assert cancelled.json() == {"id": second, "status": "stopped", "cancel_requested": True}
    release.set()
    assert application.state.services.wait(10)
    assert started == [first]
    detail = api(application, "GET", f"/api/tasks/{second}").json()
    assert (detail["status"], detail["stop_reason"]) == ("stopped", "cancelled")
    assert detail["cancel_requested"] is True
    ended = api(application, "POST", f"/api/tasks/{first}/cancel")
    assert ended.status_code == 409
    assert api(application, "POST", "/api/tasks/nope/cancel").status_code == 404
    application.state.services.close()


def test_shutdown_cancels_the_tasks_the_server_started(
    project: Path, static: Path, tmp_path: Path
) -> None:
    release = threading.Event()

    def run(spec: Any, task_id: str) -> None:
        release.wait(10)

    application = make_app(project, static, tmp_path, run=run, max_tasks=1)
    first = api(application, "POST", "/api/tasks", json={"instruction": "one"}).json()["id"]
    second = api(application, "POST", "/api/tasks", json={"instruction": "two"}).json()["id"]
    services = application.state.services
    _wait_for(lambda: services.runner().pending() == sorted([first, second]))
    assert services.shutdown() == [first]
    db = services.db()
    assert db.cancel_requested(first) and db.cancel_requested(second)
    assert queries.task_row(db, second)["status"] == "stopped"
    assert services.abandon() == [first]
    assert queries.task_row(db, first)["status"] == "stopped"
    release.set()
    assert services.wait(10)
    services.close()


# --------------------------------------------------------------------------------------------
# Router, agents, ledger, audit, science
# --------------------------------------------------------------------------------------------


def test_router_decisions_and_features(app: Any) -> None:
    data = api(app, "GET", "/api/router?task=t-done").json()
    assert [d["step"] for d in data["decisions"]] == [2, 1]
    assert data["features"] == {"t-done": {"task_type": "bugfix", "difficulty": 2}}
    assert len(api(app, "GET", "/api/router").json()["decisions"]) == 3


def test_agents_show_the_latest_model_call_of_unfinished_tasks(app: Any) -> None:
    (agent,) = api(app, "GET", "/api/agents").json()["agents"]
    assert agent["task_id"] == "t-run" and agent["worker"] == "ws-local/qwen2.5:32b"
    assert agent["tokens_per_s"] == pytest.approx(420 / 9.8, abs=0.01)
    assert agent["context_used"] == 6100
    assert agent["cache_hit"] == pytest.approx(5000 / 6100, abs=1e-4)


def test_ledger_groups_and_reference_cost(project: Path, static: Path, tmp_path: Path) -> None:
    seed(project)
    prices = {"ledger": {"reference_price_in": 3.0, "reference_price_out": 15.0}}
    application = make_app(project, static, tmp_path, config=make_config(tmp_path, **prices))
    by_model = api(application, "GET", "/api/ledger").json()
    assert by_model["totals"]["input_tokens"] == 7600 and by_model["totals"]["steps"] == 3
    keys = [g["key"] for g in by_model["groups"]]
    assert keys == ["remote/big-model", "ws-local/qwen2.5:32b"]
    # Only local work counts as avoided: (1000 + 6100) input and (100 + 420) output tokens.
    avoided = (7100 * 3.0 + 520 * 15.0) / 1e6
    assert by_model["reference_cost"]["avoided"] == pytest.approx(avoided, abs=1e-4)
    by_task = api(application, "GET", "/api/ledger?group=task").json()
    assert [g["key"] for g in by_task["groups"]] == ["t-done", "t-run"]
    assert len(api(application, "GET", "/api/ledger?group=day").json()["groups"]) == 1
    assert api(application, "GET", "/api/ledger?group=week").status_code == 422
    later = api(application, "GET", f"/api/ledger?since={NOW + 1.5}").json()
    assert later["totals"]["steps"] == 1
    plain = api(app_with_defaults(project, static, tmp_path), "GET", "/api/ledger").json()
    assert plain["reference_cost"] is None


def app_with_defaults(project: Path, static: Path, tmp_path: Path) -> Any:
    return make_app(project, static, tmp_path)


def test_audit_rows_and_totals(app: Any) -> None:
    data = api(app, "GET", "/api/audit").json()
    assert data["totals"] == {"requests": 2, "denied": 1, "non_local": 1}
    assert [row["allowed"] for row in data["rows"]] == [False, True]
    denied = api(app, "GET", "/api/audit?denied=true").json()
    assert len(denied["rows"]) == 1 and denied["totals"]["requests"] == 1


def test_science_reports_link_their_plots(app: Any) -> None:
    (report,) = api(app, "GET", "/api/science").json()["reports"]
    assert report["status"] == "failed" and report["run"] == 1
    assert report["cases"] == [{"case": "case", "status": "failed", "error": None}]
    # The plot outside the run directory is left out.
    assert report["plots"] == ["/api/runs/t-done/files/artifacts/gates-1/golden/case--x.png"]
    image = api(app, "GET", report["plots"][0])
    assert image.status_code == 200 and image.content == PNG
    assert image.headers["content-type"] == "image/png"
    assert image.headers["content-security-policy"].startswith("sandbox")


@pytest.mark.parametrize(
    "path",
    [
        "/api/runs/t-done/files/..%2F..%2Flocal.toml",
        "/api/runs/t-done/files/..%5C..%5Clocal.toml",
        "/api/runs/%2E%2E/files/local.toml",
        "/api/runs/t-done/files/C:%2FWindows%2Fwin.ini",
        "/api/runs/t-done/files/missing.png",
    ],
)
def test_run_files_never_leave_the_run_directory(app: Any, path: str) -> None:
    response = api(app, "GET", path)
    assert response.status_code == 404
    assert SECRET not in response.text


def test_run_files_that_are_not_images_are_not_rendered(app: Any) -> None:
    page = api(app, "GET", "/api/runs/t-done/files/page.html")
    assert page.status_code == 200
    assert page.headers["content-type"] == "application/octet-stream"
    assert page.headers["content-disposition"].startswith("attachment")
    patch = api(app, "GET", "/api/runs/t-done/files/diff.patch")
    assert patch.headers["content-type"] == "text/plain; charset=utf-8"


# --------------------------------------------------------------------------------------------
# Workers, endpoints and models
# --------------------------------------------------------------------------------------------


def test_workers_come_from_the_catalog(app: Any) -> None:
    data = api(app, "GET", "/api/workers").json()
    ids = {w["id"] for w in data["workers"]}
    assert {"ws-local/qwen2.5:32b", "remote/big-model"} <= ids
    local = next(w for w in data["workers"] if w["id"] == "ws-local/qwen2.5:32b")
    assert local["local"] is True and "decode_tps" in local


class FakeEgress:
    """Answers the reachability checks without a network."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.purposes: list[str] = []

    def client(self, purpose: str, **kwargs: Any) -> httpx.Client:
        self.purposes.append(purpose)

        def handle(req: httpx.Request) -> httpx.Response:
            self.requests.append(req)
            if req.url.host != "127.0.0.1":
                raise httpx.ConnectError(f"cannot reach {req.url}")
            return httpx.Response(200, json={"data": []})

        return httpx.Client(transport=httpx.MockTransport(handle), **kwargs)


def test_endpoints_hide_remote_addresses_and_keys(
    project: Path, static: Path, tmp_path: Path
) -> None:
    egress = FakeEgress()
    application = make_app(project, static, tmp_path, egress=egress)
    plain = api(application, "GET", "/api/endpoints")
    assert SECRET not in plain.text and "remote.example.invalid" not in plain.text
    rows = {row["name"]: row for row in plain.json()["endpoints"]}
    assert rows["ws-local"]["url"] == "http://127.0.0.1:11434"
    assert rows["remote"]["url"] is None and rows["remote"]["local"] is False
    assert "reachable" not in rows["ws-local"]
    assert egress.requests == []  # nothing is contacted without check=true

    checked = api(application, "GET", "/api/endpoints?check=true")
    assert SECRET not in checked.text and "remote.example.invalid" not in checked.text
    rows = {row["name"]: row for row in checked.json()["endpoints"]}
    assert rows["ws-local"]["reachable"] is True and rows["ws-local"]["http_status"] == 200
    assert rows["remote"]["reachable"] is False and rows["remote"]["error"] == "ConnectError"
    assert set(egress.purposes) == {"dashboard"}
    assert all("authorization" not in r.headers for r in egress.requests)
    assert sorted(r.url.path for r in egress.requests) == ["/v1/models", "/v1/models"]


def test_model_routes_need_installed_registry_models(app: Any) -> None:
    unknown = api(app, "POST", "/api/models/no-such-model/start")
    assert unknown.status_code == 404
    from stepswitch import registry

    entry = next(iter(registry.load().values()))
    absent = api(app, "POST", f"/api/models/{entry.id}/start")
    assert absent.status_code == 409 and "ssw models pull" in absent.json()["error"]
    stopped = api(app, "POST", f"/api/models/{entry.id}/stop")
    assert stopped.json() == {"model": entry.id, "stopped": False}


# --------------------------------------------------------------------------------------------
# Bench results, claims and compatibility
# --------------------------------------------------------------------------------------------


def _bench_run(root: Path, name: str, finished: str, *, chart: bool) -> None:
    folder = root / "bench" / "results" / name
    folder.mkdir(parents=True)
    board = {
        "suite": "mini-general",
        "date": finished,
        "runs": 4,
        "contestants": [{"rank": 1, "contestant": "ws-local/qwen2.5:32b", "pass_rate": 0.75}],
    }
    (folder / "leaderboard.json").write_text(json.dumps(board), encoding="utf-8")
    (folder / "run.json").write_text(json.dumps({"finished": finished}), encoding="utf-8")
    if chart:
        (folder / "pass-rate.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", "utf-8")


def test_leaderboard_runs_and_chart(project: Path, static: Path, tmp_path: Path) -> None:
    _bench_run(project, "2026-10-15-old", "2026-10-15T10:00:00", chart=False)
    _bench_run(project, "2026-10-16-new", "2026-10-16T10:00:00", chart=True)
    (project / "bench" / "results" / "no-board").mkdir()
    application = make_app(project, static, tmp_path)
    data = api(application, "GET", "/api/leaderboard").json()
    assert [r["name"] for r in data["runs"]] == ["2026-10-16-new", "2026-10-15-old"]
    assert data["run"] == "2026-10-16-new"
    assert data["leaderboard"]["contestants"][0]["pass_rate"] == 0.75
    assert data["chart"] == "/api/leaderboard/2026-10-16-new/pass-rate.svg"
    chart = api(application, "GET", data["chart"])
    assert chart.status_code == 200 and chart.headers["content-type"].startswith("image/svg+xml")
    old = api(application, "GET", "/api/leaderboard?run=2026-10-15-old").json()
    assert old["chart"] is None
    assert api(application, "GET", "/api/leaderboard?run=nope").status_code == 404
    assert api(application, "GET", "/api/leaderboard/nope/pass-rate.svg").status_code == 404


def test_claims_join_the_report_and_the_registry(
    project: Path, static: Path, tmp_path: Path
) -> None:
    claims = project / "claims"
    claims.mkdir()
    (claims / "claims.yaml").write_text(
        "claims:\n"
        "  - id: C-001\n    text: Runs on a laptop.\n    phase: 1\n"
        "  - id: C-002\n    text: Never shown.\n    phase: 2\n",
        encoding="utf-8",
    )
    report = {
        "mode": "quick",
        "created": "2026-10-16T10:00:00Z",
        "claims": {"C-001": {"status": "passing", "tests": {"t::a": "passed"}}},
    }
    (claims / "prove-report.json").write_text(json.dumps(report), encoding="utf-8")
    application = make_app(project, static, tmp_path)
    data = api(application, "GET", "/api/claims").json()
    assert data["source"] == "claims/prove-report.json"
    assert data["report"]["mode"] == "quick"
    rows = {row["id"]: row for row in data["claims"]}
    assert rows["C-001"]["status"] == "passing" and rows["C-001"]["text"] == "Runs on a laptop."
    assert rows["C-002"]["status"] == "unknown"


def test_compatibility_reads_committed_probe_results(
    project: Path, static: Path, tmp_path: Path
) -> None:
    shutil.copytree(ROOT / "probes", project / "probes")
    (project / "probes" / "broken.json").write_text("{", encoding="utf-8")
    application = make_app(project, static, tmp_path)
    data = api(application, "GET", "/api/compatibility").json()
    assert data["ci_set"] and data["results"]
    assert {c["name"] for c in data["checks"]} >= {"basic_chat"}
    for result in data["results"]:
        assert set(result["checks"].values()) <= {"yes", "no", "skipped", "error"}
    assert any(p.startswith("broken.json") for p in data["problems"])


def test_create_app_without_arguments_has_a_random_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    first, second = create_app(), create_app()
    assert len(first.state.services.token) >= 32
    assert first.state.services.token != second.state.services.token
    assert request(first, "GET", "/api/tasks").status_code == 401


def test_reachability_reports_errors_by_kind_only() -> None:
    config = make_config(Path())
    remote = config.endpoint("remote")
    assert remote is not None
    reach = app_module._reach(remote, FakeEgress())  # type: ignore[arg-type]
    assert reach == {"reachable": False, "http_status": None, "error": "ConnectError"}
