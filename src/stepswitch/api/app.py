"""The task API and the dashboard on one local web server (SPEC §17, docs/API.md, ADR 0023).

`create_app` builds the application; `ssw dash` serves it on 127.0.0.1 only. Every `/api/`
route needs the session token (a cookie set from the URL `ssw dash` prints, or a Bearer
header); requests for another host name or from another site are refused before routing.
Nothing here contacts a model or an endpoint except where a route says so
(`GET /api/endpoints?check=true`, and starting a model server on request).
"""

from __future__ import annotations

import mimetypes
import secrets
import threading
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qsl, urlencode

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from stepswitch.api import events, queries
from stepswitch.api.runner import RunTask, TaskRunner, stop_queued
from stepswitch.core.task import FINAL_STATUSES, Budgets, TaskSpec

if TYPE_CHECKING:
    from stepswitch.config import Config, EndpointConfig
    from stepswitch.net import Egress
    from stepswitch.state import StateDB
    from stepswitch.workers.catalog import Catalog

HOST = "127.0.0.1"
DEFAULT_PORT = 7777
COOKIE_PREFIX = "ssw_token_"
CATALOG_TTL_SECONDS = 10.0
REACH_TIMEOUT_SECONDS = 3.0
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Files from a task's run directory are shown inline only when they are images; anything else
# (a model could have written HTML there) is sent as plain text or as a download.
INLINE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
TEXT_SUFFIXES = frozenset({".txt", ".json", ".jsonl", ".patch", ".diff", ".md", ".csv", ".log"})
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
SECURITY_HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}
NOT_BUILT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Stepswitch</title></head>
<body style="font-family: sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem">
<h1>The dashboard is not built</h1>
<p>The task API is running. Build the dashboard once from the Stepswitch source checkout:</p>
<pre>cd dashboard
npm ci
npm run build</pre>
<p>then restart <code>ssw dash</code>.</p>
</body></html>
"""


def cookie_name(port: int) -> str:
    """Cookies are shared by every port of a host, so the name carries the port."""
    return f"{COOKIE_PREFIX}{port}"


def _built(folder: Path) -> Path | None:
    return folder if (folder / "index.html").is_file() else None


def default_static_dir() -> Path | None:
    """The built dashboard: packaged with Stepswitch, or `dashboard/dist` in a source checkout."""
    package = Path(__file__).resolve().parents[1]
    for candidate in (package / "_dashboard", package.parents[1] / "dashboard" / "dist"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


# --------------------------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------------------------


class Guard:
    """Host and Origin checks, the session token and the security headers (docs/API.md)."""

    def __init__(self, app: ASGIApp, *, token: str, port: int) -> None:
        self.app = app
        self.token = token
        self.cookie = cookie_name(port)
        self.hosts = {f"{HOST}:{port}", f"localhost:{port}"}
        self.origins = {f"http://{host}" for host in self.hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        path: str = scope["path"]
        method: str = scope["method"]
        if headers.get("host", "") not in self.hosts:
            await self._refuse(scope, receive, send, 403, "unknown host")
            return
        origin = headers.get("origin")
        if method not in SAFE_METHODS and origin is not None and origin not in self.origins:
            await self._refuse(scope, receive, send, 403, "cross-site request")
            return
        is_api = path == "/api" or path.startswith("/api/")
        if is_api and not self._authorized(headers):
            await self._refuse(scope, receive, send, 401, "unauthorized")
            return
        query = scope.get("query_string", b"").decode("latin-1")
        if not is_api and method == "GET" and "token" in dict(parse_qsl(query)):
            await self._bootstrap(scope, receive, send, path, query)
            return
        await self.app(scope, receive, self._with_headers(send, api=is_api))

    def _authorized(self, headers: Headers) -> bool:
        supplied = ""
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        else:
            for part in headers.get("cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == self.cookie:
                    supplied = value
                    break
        return bool(supplied) and secrets.compare_digest(supplied, self.token)

    async def _bootstrap(
        self, scope: Scope, receive: Receive, send: Send, path: str, query: str
    ) -> None:
        """Store a correct token in the cookie and drop it from the address."""
        pairs = parse_qsl(query, keep_blank_values=True)
        supplied = next((value for key, value in pairs if key == "token"), "")
        rest = urlencode([(key, value) for key, value in pairs if key != "token"])
        headers = {"location": path + (f"?{rest}" if rest else ""), "cache-control": "no-store"}
        response = HTMLResponse("", status_code=303, headers=headers)
        if supplied and secrets.compare_digest(supplied, self.token):
            response.set_cookie(self.cookie, self.token, httponly=True, samesite="strict")
        await response(scope, receive, self._with_headers(send, api=False))

    async def _refuse(
        self, scope: Scope, receive: Receive, send: Send, status: int, message: str
    ) -> None:
        response = JSONResponse({"error": message}, status_code=status)
        await response(scope, receive, self._with_headers(send, api=True))

    def _with_headers(self, send: Send, *, api: bool) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if api:
                    headers.setdefault("cache-control", "no-store")
            await send(message)

        return wrapped


# --------------------------------------------------------------------------------------------
# Services shared by the routes
# --------------------------------------------------------------------------------------------


@dataclass
class Services:
    root: Path
    token: str
    port: int
    config_value: Config | None = None
    egress: Egress | None = None
    run: RunTask | None = None
    max_tasks: int = 1
    static_dir: Path | None = None
    stopping: Callable[[], bool] = lambda: False
    poll_interval: float = 0.5
    heartbeat: float = 15.0
    model_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    _db: StateDB | None = None
    _runner: TaskRunner | None = None
    _catalog: tuple[float, Catalog] | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def config(self) -> Config:
        """The configuration given to the app, else the project's as it is on disk now."""
        if self.config_value is not None:
            return self.config_value
        from stepswitch import config as config_module

        return config_module.load(self.root)

    def db(self) -> StateDB:
        with self.lock:
            if self._db is None:
                from stepswitch.state import open_project_state

                self._db = open_project_state(self.root)
            return self._db

    def runner(self) -> TaskRunner:
        with self.lock:
            if self._runner is None:
                self._runner = TaskRunner(
                    self.db(), self.run or self._run_task, max_tasks=self.max_tasks
                )
            return self._runner

    def _run_task(self, spec: TaskSpec, task_id: str) -> Any:
        from stepswitch.core.orchestrator import run_task

        return run_task(
            spec, root=self.root, config=self.config(), egress=self.egress, task_id=task_id
        )

    def catalog(self) -> Catalog:
        """The worker catalog, rebuilt at most every few seconds."""
        from stepswitch.workers.catalog import Catalog

        with self.lock:
            now = time.monotonic()
            if self._catalog is None or now - self._catalog[0] > CATALOG_TTL_SECONDS:
                built = Catalog.from_config(
                    self.config(), self.root, egress=self.egress, db=self.db()
                )
                self._catalog = (now, built)
            return self._catalog[1]

    def shutdown(self) -> list[str]:
        """Ask every task this server started to stop; returns the ids still running. Does not
        wait: running tasks stop at their next step boundary (see `wait`)."""
        with self.lock:
            runner = self._runner
        if runner is None:
            return []
        runner.shutdown(wait=False)
        return runner.pending()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the tasks this server started have stopped; False on timeout."""
        with self.lock:
            runner = self._runner
        return runner.wait(timeout) if runner is not None else True

    def abandon(self) -> list[str]:
        """Record the tasks still running as stopped (cancelled) before a forced exit."""
        with self.lock:
            runner = self._runner
        return runner.abandon() if runner is not None else []

    def close(self) -> None:
        with self.lock:
            if self._db is not None:
                self._db.close()
                self._db = None


# --------------------------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------------------------


class BudgetsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: int | None = Field(default=None, ge=1)
    wall_seconds: float | None = Field(default=None, gt=0)
    gpu_seconds: float | None = Field(default=None, gt=0)
    escalations: int | None = Field(default=None, ge=0)


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=20_000)
    repo: str | None = None
    track: Literal["general", "science"] = "general"
    acceptance: list[str] = Field(default_factory=list, max_length=20)
    budgets: BudgetsIn = Field(default_factory=BudgetsIn)
    worker: str | None = None
    workers: list[str] = Field(default_factory=list, max_length=50)
    max_steps: int = Field(default=40, ge=1, le=1000)
    review: bool = True
    allow_remote: bool = False
    offline: bool = False
    # Best-of-N (SPEC §8.3 to §8.5, ADR 0025): 0 picks the count from the task's difficulty.
    best_of: int = Field(default=1, ge=0, le=5)
    on_disagreement: Literal["flag", "tests", "escalate", "human"] = "flag"
    probe_tests: bool = True


def _validation_message(errors: list[dict[str, Any]]) -> str:
    parts = []
    for error in errors[:5]:
        where = ".".join(str(p) for p in error.get("loc", ()) if p not in ("body", "query", "path"))
        message = str(error.get("msg", "invalid"))
        parts.append(f"{where}: {message}" if where else message)
    return "; ".join(parts) or "invalid request"


def _not_found(what: str) -> HTTPException:
    return HTTPException(404, f"{what} not found")


def _reach(endpoint: EndpointConfig, egress: Egress) -> dict[str, Any]:
    """Whether the endpoint answers a read-only model listing (any HTTP status counts).
    No credentials are sent, and errors are reported by kind only: their text can hold the
    URL, which is not shown for remote endpoints."""
    import httpx

    from stepswitch import net
    from stepswitch.compat.client import join_url

    path = "/v1beta/models" if endpoint.primary_format() == "generate-content" else "/v1/models"
    try:
        with egress.client("dashboard", timeout=REACH_TIMEOUT_SECONDS) as client:
            status = client.get(join_url(endpoint.url, path)).status_code
    except net.EgressBlocked:
        return {"reachable": False, "http_status": None, "error": "blocked by the egress policy"}
    except httpx.HTTPError as exc:
        return {"reachable": False, "http_status": None, "error": type(exc).__name__}
    return {"reachable": True, "http_status": status, "error": None}


def _run_file_response(path: Path) -> FileResponse:
    media, _ = mimetypes.guess_type(path.name)
    headers = {"content-security-policy": "sandbox; default-src 'none'"}
    if media in INLINE_TYPES:
        return FileResponse(path, media_type=media, headers=headers)
    if path.suffix.lower() in TEXT_SUFFIXES:
        return FileResponse(path, media_type="text/plain; charset=utf-8", headers=headers)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers=headers,
        content_disposition_type="attachment",
        filename=path.name,
    )


def _static_file(base: Path, path: str) -> Path | None:
    parts = PurePosixPath(path).parts
    if not parts or "\\" in path or ":" in path or any(p in ("..", ".") for p in parts):
        return None
    root = base.resolve()
    target = root.joinpath(*parts).resolve()
    return target if target.is_relative_to(root) and target.is_file() else None


# --------------------------------------------------------------------------------------------
# The application
# --------------------------------------------------------------------------------------------


def create_app(
    root: Path | None = None,
    *,
    token: str | None = None,
    port: int = DEFAULT_PORT,
    config: Config | None = None,
    egress: Egress | None = None,
    run: RunTask | None = None,
    max_tasks: int = 1,
    static_dir: Path | None = None,
    stopping: Callable[[], bool] | None = None,
    poll_interval: float = 0.5,
    heartbeat: float = 15.0,
) -> FastAPI:
    """The task API and dashboard for the project at `root` (default: the current project).

    Without `token` a random one is made (`app.state.services.token`): the API is never open.
    `run` replaces the orchestrator (tests); `stopping` tells live streams to end (the server
    is shutting down).
    """
    from stepswitch import paths

    services = Services(
        root=(root or paths.find_project_root()).resolve(),
        token=token or secrets.token_urlsafe(32),
        port=port,
        config_value=config,
        egress=egress,
        run=run,
        max_tasks=max_tasks,
        static_dir=_built(static_dir) if static_dir is not None else default_static_dir(),
        stopping=stopping or (lambda: False),
        poll_interval=poll_interval,
        heartbeat=heartbeat,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        services.shutdown()

    app = FastAPI(
        title="Stepswitch task API",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.services = services
    app.add_middleware(Guard, token=services.token, port=port)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"error": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": _validation_message(list(exc.errors()))}, status_code=422)

    _task_routes(app, services)
    _info_routes(app, services)
    _model_routes(app, services)

    @app.get("/api/events", include_in_schema=False)
    async def live(request: Request) -> StreamingResponse:
        last = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
        body = events.stream(
            services.db(),
            last,
            interval=services.poll_interval,
            heartbeat=services.heartbeat,
            stopping=services.stopping,
        )
        return StreamingResponse(
            body,
            media_type="text/event-stream",
            headers={"cache-control": "no-store", "x-accel-buffering": "no"},
        )

    @app.get("/{path:path}", include_in_schema=False)
    def dashboard(path: str) -> Any:
        if path == "api" or path.startswith("api/"):
            raise _not_found("route")
        base = services.static_dir
        if base is None:
            return HTMLResponse(NOT_BUILT, status_code=503, headers={"cache-control": "no-store"})
        found = _static_file(base, path) if path else None
        if found is not None:
            return FileResponse(found)
        if path and "." in PurePosixPath(path).name:
            raise _not_found("file")
        return FileResponse(base / "index.html", headers={"cache-control": "no-store"})

    return app


def _task_routes(app: FastAPI, services: Services) -> None:
    @app.get("/api/tasks")
    def list_tasks(
        limit: int = Query(50, ge=1, le=500), status: str | None = None
    ) -> dict[str, Any]:
        return {"tasks": queries.list_tasks(services.db(), limit=limit, status=status)}

    @app.post("/api/tasks", status_code=201)
    def create_task(body: TaskIn) -> JSONResponse:
        repo = Path(body.repo).expanduser() if body.repo else services.root
        if not repo.is_absolute():
            repo = services.root / repo
        if not repo.is_dir():
            raise HTTPException(422, "repo: not a directory")
        spec = TaskSpec(
            instruction=body.instruction,
            repo=repo.resolve(),
            track=body.track,
            acceptance=body.acceptance,
            budgets=Budgets(**body.budgets.model_dump()),
            worker=body.worker or None,
            workers=body.workers,
            max_steps=body.max_steps,
            review=body.review,
            allow_remote=body.allow_remote,
            offline=body.offline,
            best_of=body.best_of,
            on_disagreement=body.on_disagreement,
            probe_tests=body.probe_tests,
        )
        try:
            task_id = services.runner().submit(spec)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return JSONResponse({"id": task_id, "status": "queued"}, status_code=201)

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        detail = queries.task_detail(services.db(), services.root, task_id)
        if detail is None:
            raise _not_found("task")
        return detail

    @app.post("/api/tasks/{task_id}/cancel", status_code=202)
    def cancel_task(task_id: str) -> dict[str, Any]:
        db = services.db()
        row = queries.task_row(db, task_id)
        if row is None:
            raise _not_found("task")
        if row["status"] in FINAL_STATUSES:
            raise HTTPException(409, f"task has ended ({row['status']})")
        db.request_cancel(task_id)
        stop_queued(db, task_id)
        row = queries.task_row(db, task_id) or row
        return {"id": task_id, "status": row["status"], "cancel_requested": True}

    @app.get("/api/tasks/{task_id}/events")
    def task_events(task_id: str, after: int = Query(0, ge=0)) -> dict[str, Any]:
        db = services.db()
        if queries.task_row(db, task_id) is None:
            raise _not_found("task")
        return {"events": queries.task_events(db, task_id, after=after)}

    @app.get("/api/trajectories")
    def trajectories(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        return {"trajectories": queries.trajectories(services.db(), limit=limit)}

    @app.get("/api/runs/{task_id}/files/{path:path}")
    def run_file(task_id: str, path: str) -> FileResponse:
        found = queries.run_file(services.root, task_id, path)
        if found is None:
            raise _not_found("file")
        return _run_file_response(found)


def _info_routes(app: FastAPI, services: Services) -> None:
    @app.get("/api/workers")
    def workers() -> dict[str, Any]:
        return queries.workers(services.catalog())

    @app.get("/api/agents")
    def agents() -> dict[str, Any]:
        contexts = {spec.id: spec.context for spec in services.catalog().specs()}
        return {"agents": queries.agents(services.db(), contexts)}

    @app.get("/api/router")
    def router(task: str | None = None, limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
        return queries.router(services.db(), task=task, limit=limit)

    @app.get("/api/ledger")
    def ledger(
        group: Literal["model", "task", "day"] = "model", since: float | None = None
    ) -> dict[str, Any]:
        config = services.config()
        local = [e.name for e in config.endpoints if e.is_local()] + ["managed"]
        return queries.ledger(
            services.db(),
            group=group,
            since=since,
            prices=queries.reference_prices(config),
            local_endpoints=local,
        )

    @app.get("/api/leaderboard")
    def leaderboard(run: str | None = None) -> dict[str, Any]:
        try:
            return queries.leaderboard(services.root, run)
        except KeyError as exc:
            raise _not_found("bench run") from exc

    @app.get("/api/leaderboard/{run}/pass-rate.svg")
    def leaderboard_chart(run: str) -> FileResponse:
        found = queries.chart_file(services.root, run)
        if found is None:
            raise _not_found("chart")
        return FileResponse(found, media_type="image/svg+xml")

    @app.get("/api/claims")
    def claims() -> dict[str, Any]:
        return queries.claims(services.root)

    @app.get("/api/compatibility")
    def compatibility() -> dict[str, Any]:
        return queries.compatibility(services.root, services.db())

    @app.get("/api/audit")
    def audit(
        task: str | None = None, limit: int = Query(200, ge=1, le=5000), denied: bool = False
    ) -> dict[str, Any]:
        return queries.audit(services.db(), task=task, limit=limit, denied=denied)

    @app.get("/api/science")
    def science(task: str | None = None) -> dict[str, Any]:
        return {"reports": queries.science(services.db(), services.root, task=task)}


def _model_routes(app: FastAPI, services: Services) -> None:
    @app.get("/api/endpoints")
    def endpoints(check: bool = False) -> dict[str, Any]:
        from stepswitch import net, registry
        from stepswitch.serve.manager import ServerManager

        config = services.config()
        rows = queries.endpoint_rows(config, services.db())
        if check and config.endpoints:
            egress = services.egress or net.default()
            with ThreadPoolExecutor(max_workers=min(8, len(config.endpoints))) as pool:
                for row, reach in zip(
                    rows, pool.map(lambda e: _reach(e, egress), config.endpoints), strict=True
                ):
                    row.update(reach)
        manager = ServerManager(services.root, config)
        servers = manager.status() if check else manager.records()
        with services.lock:
            states = {key: value["state"] for key, value in services.model_states.items()}
        return {
            "endpoints": rows,
            "managed": queries.managed_rows(servers, checked=check),
            "installed": queries.installed_rows(
                registry.load().values(),
                config.models_dir(),
                {info.model for info in servers},
                states,
            ),
            "model_errors": {
                key: value["error"]
                for key, value in services.model_states.items()
                if value.get("error")
            },
        }

    @app.post("/api/models/{model_id}/start", status_code=202)
    def start_model(model_id: str) -> dict[str, Any]:
        from stepswitch import registry

        config = services.config()
        entry = registry.find(model_id)
        if entry is None:
            raise _not_found("model")
        if not entry.installed(config.models_dir()):
            raise HTTPException(409, f"{entry.id} is not installed (ssw models pull {entry.id})")
        with services.lock:
            if (services.model_states.get(entry.id) or {}).get("state") == "starting":
                return {"model": entry.id, "status": "starting"}
            services.model_states[entry.id] = {"state": "starting", "error": None}
        thread = threading.Thread(
            target=_start_model, args=(services, entry.id), name=f"ssw-start-{entry.id}"
        )
        thread.daemon = True
        thread.start()
        return {"model": entry.id, "status": "starting"}

    @app.post("/api/models/{model_id}/stop")
    def stop_model(model_id: str) -> dict[str, Any]:
        from stepswitch import registry
        from stepswitch.serve.base import EngineError
        from stepswitch.serve.manager import ServerManager

        entry = registry.find(model_id)
        key = entry.id if entry is not None else model_id
        try:
            stopped = ServerManager(services.root, services.config()).stop(key)
        except EngineError as exc:
            raise HTTPException(500, f"could not stop {key}: {exc}") from exc
        with services.lock:
            services.model_states.pop(key, None)
        return {"model": key, "stopped": stopped}


def _start_model(services: Services, model_id: str) -> None:
    from stepswitch.serve.manager import ServerManager

    try:
        ServerManager(services.root, services.config()).start(model_id)
    except Exception as exc:  # noqa: BLE001 - shown on the endpoints page
        state: dict[str, Any] = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}
        with services.lock:
            services.model_states[model_id] = state
        return
    with services.lock:
        services.model_states.pop(model_id, None)
