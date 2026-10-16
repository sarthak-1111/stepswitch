# ADR 0023: The local task API and dashboard server

- Status: accepted
- Date: 2026-10-16
- Phase: 4

## Context

SPEC §17 asks for a local web UI at `http://127.0.0.1:7777`, "bound to localhost with a local
token", and a task API with live updates over Server-Sent Events. This API can start tasks, and
a task runs commands in a repository. Any web page the user opens can send requests to
`127.0.0.1`, so binding to localhost alone is not enough protection. Tasks also run outside the
dashboard (`ssw run` in a terminal), and the dashboard must show and cancel those as well.

## Decision

1. **One local server.** `ssw dash` serves the API and the built dashboard from one FastAPI
   app on uvicorn, bound to `127.0.0.1` only (default port 7777). The full contract is in
   docs/API.md.
2. **Session token.**
   - Each `ssw dash` run makes a random token (`secrets.token_urlsafe(32)`) and prints a link
     that carries it.
   - Opening the link stores the token in an `HttpOnly`, `SameSite=Strict` cookie and
     redirects (303) to the same address without it.
   - Cookies are shared by all ports of a host, so the cookie name carries the port
     (`ssw_token_<port>`). Two dashboards on different ports then keep separate sessions.
   - Scripts send `Authorization: Bearer <token>` instead.
   - Every `/api/` route needs the token, including the OpenAPI document. The interactive docs
     pages are off.
3. **Request checks before routing.**
   - A `Host` header other than `127.0.0.1:<port>` or `localhost:<port>` gets 403. This blocks
     DNS-rebinding pages.
   - Unsafe methods with an `Origin` from another site get 403. This adds to the strict cookie
     as protection against cross-site requests.
   - Every response carries a Content-Security-Policy with `script-src 'self'` and
     `frame-ancestors 'none'`, plus `nosniff`, `no-referrer` and same-origin policies. API
     answers are not cached.
4. **Tasks run in the server process.**
   - Submitted tasks run on a thread pool (`--max-tasks`, default 1) through the same
     `run_task` the CLI uses, with the CLI's egress layer and audit log.
   - The task row is written as `queued` before submission, so it can be listed and cancelled
     while it waits.
   - Any exception that escapes the orchestrator still ends the task as `failed` (`error`).
5. **Cancellation through the state database.**
   - A `cancellations` table (state migration 3) records requests. The budget tracker reads it
     at every step boundary and before the worktree is taken, and the task stops with
     `stopped` / `cancelled`.
   - This works whichever process runs the task. A queued task is stopped at once.
   - When `ssw dash` exits, it asks its own tasks to stop and waits for their next step
     boundary. A second Ctrl+C records them as stopped and leaves.
6. **The live feed polls the database.**
   - Tasks write to SQLite from any process, so `GET /api/events` polls new rows (every
     0.5 s) instead of using an in-process publisher. Events and the ledger are read by ID;
     tasks are read by update time.
   - Every event's `id` is a cursor over the four sources, so `Last-Event-ID` resumes exactly.
   - Task rows are re-read five seconds back, in case another process commits an older update
     late. A resumed stream may therefore repeat a task row; clients replace tasks by ID.
   - FastAPI 0.143 has its own SSE support. The server writes the stream itself so that
     heartbeats and resumption do not depend on the FastAPI version (the floor is 0.135).
7. **No secrets or remote addresses in answers.**
   - Keys, tokens and server log paths are never returned. Remote endpoint URLs are shown as
     null.
   - Endpoint reachability is checked only on request (`?check=true`): through the egress layer
     (purpose `dashboard`), without credentials, and reported by error kind only.
8. **Run files are not rendered as pages.**
   - `GET /api/runs/{task}/files/{path}` serves files from the task's run directory only. The
     task ID and every path part are checked before the path is resolved.
   - Images are shown inline. Text files are sent as `text/plain`. Anything else (a model may
     have written HTML there) is sent as a download. Every such answer carries a `sandbox` CSP.

## Consequences

- Leaving the printed link in a terminal or in history exposes that session's token. The token
  lasts until `ssw dash` stops.
- Polling costs a few small queries per open dashboard every half second. That is negligible
  locally, but the feed is not meant for many remote clients, which the loopback bind rules
  out anyway.
- A task whose process died while running stays `running`. Cancelling it records the request,
  but no process acts on it. Recognising dead tasks needs a process heartbeat (later phase).
- Tests call the app through httpx's ASGI transport. Starlette 1.8's `TestClient` warns that
  `httpx` is deprecated for it in favour of `httpx2`. The live stream is tested against a real
  uvicorn server on a free loopback port.
