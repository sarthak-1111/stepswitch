"""Browser smoke test of the dashboard (SPEC §17).

A real server with recorded state serves the built dashboard. A Chromium-family browser checks
three things:
- the token link signs in;
- every view renders without script errors;
- a task started from the page can be cancelled from the page.

Needs:
- the built dashboard (`cd dashboard && npm ci && npm run build`);
- the `e2e` dependency group (`uv sync --group e2e`);
- the installed Chrome, or `uv run playwright install chromium`.

Skipped when any of these is missing, unless STEPSWITCH_E2E_REQUIRED=1 (CI) makes that a failure.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn

import pytest

from stepswitch.api.app import create_app
from stepswitch.core.task import TaskSpec
from stepswitch.state import open_project_state
from tests.unit.test_api import TOKEN, _bench_run, make_config, seed
from tests.unit.test_api_live import free_port, serving

REQUIRED = os.environ.get("STEPSWITCH_E2E_REQUIRED") == "1"


def _missing(reason: str) -> NoReturn:
    if REQUIRED:
        pytest.fail(f"{reason} (STEPSWITCH_E2E_REQUIRED=1)")
    pytest.skip(reason, allow_module_level=True)


try:
    from playwright import sync_api
except ImportError:  # pragma: no cover - depends on the e2e dependency group
    _missing("Playwright is not installed (uv sync --group e2e)")
expect = sync_api.expect

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dashboard" / "dist"
VIEWS = [
    ("Tasks", "/tasks"),
    ("Agents", "/agents"),
    ("Router", "/router"),
    ("Ledger", "/ledger"),
    ("Leaderboard", "/leaderboard"),
    ("Trajectories", "/trajectories"),
    ("Endpoints", "/endpoints"),
    ("Compatibility", "/compatibility"),
    ("Audit", "/audit"),
    ("Science", "/science"),
    ("Claims", "/claims"),
]
# Text each view shows from the recorded state (tests/unit/test_api.py seeds it).
VIEW_TEXT = {
    "/tasks": "Fix dates",
    "/agents": "t-run",
    "/router": "remote/big-model",
    "/leaderboard": "ws-local/qwen2.5:32b",
    "/endpoints": "ws-local",
    "/claims": "C-001",
}


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    if not (DIST / "index.html").is_file():
        _missing("the dashboard is not built (cd dashboard && npm ci && npm run build)")
    with sync_api.sync_playwright() as playwright:
        launched = None
        for options in ({"channel": "chrome"}, {}):
            try:
                launched = playwright.chromium.launch(**options)
                break
            except sync_api.Error:
                continue
        if launched is None:
            _missing("no Chromium-family browser: install Chrome or `playwright install chromium`")
        yield launched
        launched.close()


@pytest.fixture
def dashboard(tmp_path: Path) -> Iterator[str]:
    root = tmp_path / "project"
    (root / ".stepswitch").mkdir(parents=True)
    seed(root)
    _bench_run(root, "2026-10-16-new", "2026-10-16T10:00:00", chart=True)
    shutil.copytree(ROOT / "probes", root / "probes")
    (root / "claims").mkdir()
    for name in ("claims.yaml", "prove-report.json"):
        shutil.copy2(ROOT / "claims" / name, root / "claims" / name)
    pause = threading.Event()

    def run(spec: TaskSpec, task_id: str) -> None:
        """Runs until someone cancels the task."""
        db = open_project_state(root)
        try:
            db.execute(
                "UPDATE tasks SET status = 'running', updated = ? WHERE id = ?",
                (time.time(), task_id),
            )
            while not db.cancel_requested(task_id):
                pause.wait(0.05)
            result = {"task_id": task_id, "status": "stopped", "summary": "cancelled on request"}
            db.execute(
                "UPDATE tasks SET status = 'stopped', stop_reason = 'cancelled', updated = ?,"
                " result = ? WHERE id = ?",
                (time.time(), json.dumps(result), task_id),
            )
        finally:
            db.close()

    port = free_port()
    stop = threading.Event()
    app = create_app(
        root,
        token=TOKEN,
        port=port,
        config=make_config(tmp_path),
        run=run,
        static_dir=DIST,
        poll_interval=0.1,
        stopping=stop.is_set,
    )
    services = app.state.services
    try:
        with serving(app, port):
            yield f"http://127.0.0.1:{port}"
            stop.set()
    finally:
        services.shutdown()
        services.wait(10)
        services.close()


def _page(browser: Any) -> tuple[Any, Any, list[str]]:
    context = browser.new_context()
    page = context.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda exc: problems.append(f"page error: {exc}"))
    page.on(
        "console",
        lambda msg: problems.append(f"console: {msg.text}") if msg.type == "error" else None,
    )
    return context, page, problems


def test_without_the_token_the_dashboard_asks_for_the_link(browser: Any, dashboard: str) -> None:
    context, page, problems = _page(browser)
    page.goto(f"{dashboard}/tasks")
    expect(page.get_by_role("heading", level=1)).to_have_text("Access token needed")
    context.close()
    # The refused API calls (401) are the only console errors.
    assert all("401" in problem for problem in problems), problems


def test_every_view_renders_without_script_errors(browser: Any, dashboard: str) -> None:
    context, page, problems = _page(browser)
    page.goto(f"{dashboard}/?token={TOKEN}")
    expect(page).to_have_url(f"{dashboard}/tasks")
    nav = page.get_by_role("navigation", name="Main")
    for label, path in VIEWS:
        nav.get_by_role("link", name=label, exact=True).click()
        expect(page).to_have_url(f"{dashboard}{path}")
        expect(page.get_by_role("heading", level=1)).to_have_text(label)
        if path in VIEW_TEXT:
            expect(page.get_by_text(VIEW_TEXT[path]).first).to_be_visible()
    page.goto(f"{dashboard}/science")
    expect(page.locator("img[src^='/api/runs/t-done/files/']").first).to_be_visible()
    page.goto(f"{dashboard}/tasks/t-done")
    expect(page.get_by_text("Fixed the date parsing.").first).to_be_visible()
    context.close()
    assert problems == []


def test_a_task_started_from_the_page_can_be_cancelled(browser: Any, dashboard: str) -> None:
    context, page, problems = _page(browser)
    page.goto(f"{dashboard}/?token={TOKEN}")
    page.get_by_role("button", name="New task").click()
    page.get_by_label("Instruction").fill("Rename the helper")
    page.get_by_role("button", name="Start task").click()
    banner = page.get_by_role("status").filter(has_text="Task ")
    expect(banner).to_be_visible()
    task_id = banner.get_by_role("link").inner_text().strip()
    expect(page.get_by_text("Rename the helper").first).to_be_visible()
    running = page.locator("section[aria-labelledby='column-running']")
    expect(running.get_by_text("Rename the helper")).to_be_visible()
    page.get_by_role("button", name=f"Cancel task {task_id}").click()
    stopped = page.locator("section[aria-labelledby='column-stopped']")
    expect(stopped.get_by_text("Rename the helper")).to_be_visible(timeout=15_000)
    answer = context.request.get(f"{dashboard}/api/tasks/{task_id}").json()
    assert (answer["status"], answer["stop_reason"]) == ("stopped", "cancelled")
    context.close()
    assert problems == []
