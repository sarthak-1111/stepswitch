# Task API

`ssw dash` starts the task API and the dashboard on one local web server (SPEC §17, ADR 0023).
This page is the contract both sides are written against: every route, its query parameters
and the JSON it returns. Times are Unix epoch seconds (floats); token counts are integers.

## Access

- The server listens on `127.0.0.1` (default port 7777) and nowhere else.
- `ssw dash` creates a random token for the session and prints the URL to open:
  `http://127.0.0.1:7777/?token=<token>`. Opening it stores the token in an `HttpOnly`,
  `SameSite=Strict` cookie (`ssw_token_<port>`: cookies are shared by every port of a host) and
  redirects to the same page without the token. A wrong token redirects without the cookie.
- Every `/api/` route needs that cookie or an `Authorization: Bearer <token>` header; without
  it the answer is `401 {"error": "unauthorized"}`. The dashboard's own files need no token.
- Requests whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` get `403`, and
  so do `POST` requests whose `Origin` header names another site.
- The OpenAPI document is at `/api/openapi.json` (token required); there are no docs pages.
- Errors are JSON: `{"error": "<message>"}` with a 4xx or 5xx status.
- The server makes no network connections of its own: tasks and model servers it starts use the
  egress layer and its audit log like the CLI does, and endpoint status checks happen only on
  request (`GET /api/endpoints?check=true`).

## Tasks

A task moves through `queued`, `routing`, `running` and `verifying` to one final status: `done`,
`failed`, `needs-human` or `stopped` (a budget, the step limit or a cancel request stopped it;
`stop_reason` says which). `escalations` counts moves up the escalation ladder.

### `GET /api/tasks?limit=50&status=<status>`

```json
{"tasks": [TaskSummary, ...]}
```

Newest first. `status` filters by one status. `TaskSummary`:

```json
{
  "id": "t20261016-101500-a1b2c3",
  "created": 1760600000.0,
  "updated": 1760600042.5,
  "status": "running",
  "stop_reason": null,
  "track": "general",
  "instruction": "Fix the failing date test",
  "worker": null,
  "steps": 7,
  "escalations": 0,
  "switches": 1,
  "tokens": {"input": 18200, "output": 950, "cached_input": 12100, "reasoning": 0},
  "wall_seconds": 42.5,
  "gpu_seconds": 0.0,
  "summary": "",
  "cancel_requested": false
}
```

`worker` is the pinned worker, if any. `steps`, `switches` and `escalations` come from the
router decisions so far (from the result once the task has ended); `tokens` and `gpu_seconds`
from the ledger. `wall_seconds` is the task's elapsed time: so far while it runs, the measured
total once it has ended. `summary` is the final summary once the task has ended.

### `POST /api/tasks`

Body (only `instruction` is required):

```json
{
  "instruction": "Fix the failing date test",
  "repo": "C:/work/project",
  "track": "general",
  "acceptance": ["python -m pytest -q"],
  "budgets": {"tokens": 200000, "wall_seconds": 900, "gpu_seconds": null, "escalations": null},
  "worker": null,
  "workers": [],
  "max_steps": 40,
  "review": true,
  "allow_remote": false,
  "offline": false
}
```

`repo` defaults to the project the server runs in; a relative path is taken from there, and it
must be a directory. Answer: `201 {"id": "...", "status": "queued"}`. Invalid bodies (unknown
fields included) get `422`. Tasks run one at a time by default (`ssw dash --max-tasks N`); a
task whose run fails unexpectedly ends as `failed` with `stop_reason: "error"`.

### `GET /api/tasks/{id}`

A `TaskSummary` plus:

```json
{
  "spec": {"instruction": "...", "repo": "...", "track": "general", "...": "..."},
  "result": {"summary": "...", "changed_files": ["src/dates.py"], "diff": "...", "verdict": {}},
  "decisions": [Decision, ...],
  "ledger": [LedgerRow, ...],
  "events": 57
}
```

`result` is null until the task ends. `events` is the number of trajectory events.

### `POST /api/tasks/{id}/cancel`

`202 {"id": "...", "status": "...", "cancel_requested": true}`. A queued task stops at once; a
running one stops at its next step boundary with `stop_reason: "cancelled"`. Cancelling works
for tasks started from the CLI too. `404` for an unknown task, `409` for one that has ended.

### `GET /api/tasks/{id}/events?after=<seq>`

The task's trajectory, in order:

```json
{"events": [{"seq": 1, "ts": 1760600000.1, "type": "task_start", "step": null, "data": {}}]}
```

Types: `task_start`, `worktree`, `status`, `pool`, `classify` (`data.features`), `ladder`,
`decision`, `switch`, `escalation`, `step` (one model call), `tool` (one tool call and its
result), `model_error`, `error`, `gates` (`data.verdict`), `review`, `budget`, `cancelled`,
`applied` and `task_end`. `after` returns only events with a larger `seq`. The `data` of the
most useful ones:

- `step`: `step_type`, `worker`, `endpoint`, `model`, `tool_calls` (names), `text` (the first
  400 characters of the reply), `finish_reason`, `usage` (token counts), `wall_seconds`,
  `gpu_seconds`;
- `tool`: `name`, `call_id`, `arguments` (JSON text), `is_error`, `output` (at most 2,000
  characters), `changed_files`, `repeated`;
- `decision`: as in `Decision` below; `switch`: `from`, `to`, `reason`, `cost`
  (`seconds`, `tokens`), `expected_gain`;
- `gates`: `run`, `verdict` (`passed`, `failed_gate`, `report`, and `results`: per gate
  `name`, `status`, `seconds`, `summary`, `details`, `report`; the `golden` gate's `details`
  hold the numerical report, served in a friendlier form by `GET /api/science`);
- `task_end`: `status`, `stop_reason`, `detail`, `summary`, `budget`, `ledger` (totals).

### `GET /api/trajectories?limit=50`

```json
{"trajectories": [{"id": "...", "status": "done", "instruction": "...", "created": 1760600000.0, "events": 57}]}
```

Fork and replay arrive with Phase 7; the dashboard shows them as not yet available.

## Workers and agents

### `GET /api/workers`

```json
{
  "workers": [
    {
      "id": "managed/qwen3-4b-instruct-2507",
      "endpoint": "managed",
      "model": "qwen3-4b-instruct-2507",
      "format": "chat-completions",
      "tier": "cpu",
      "level": 0,
      "family": "qwen3",
      "local": true,
      "roles": ["router", "summarizer"],
      "tool_protocol": "native",
      "tool_parser": null,
      "context": 4096,
      "gpu": false,
      "pin_only": false,
      "prefill_tps": 120.0,
      "decode_tps": 18.0
    }
  ],
  "notes": ["..."]
}
```

### `GET /api/agents`

The model work of tasks that have not ended, one row per task:

```json
{
  "agents": [
    {
      "task_id": "...",
      "status": "running",
      "worker": "ws-local/qwen2.5:32b",
      "step": 7,
      "step_type": "edit",
      "tokens_per_s": 21.4,
      "context_used": 6100,
      "context": 32768,
      "cache_hit": 0.82,
      "since": 1760600000.0
    }
  ]
}
```

`worker`, `step_type`, `tokens_per_s` (output tokens per second), `context_used` (input tokens)
and `cache_hit` (cached share of input tokens) describe the task's latest model call; `context`
is that worker's context size, if known.

## Router

### `GET /api/router?task=<id>&limit=200`

```json
{"decisions": [Decision, ...], "features": {"<task id>": {"task_type": "bugfix", "difficulty": 2}}}
```

Newest first, across tasks unless `task` is given. `Decision`:

```json
{
  "task_id": "...",
  "step": 3,
  "ts": 1760600010.0,
  "step_type": "edit",
  "chosen": "ws-local/qwen2.5:32b",
  "previous": "managed/qwen3-4b-instruct-2507",
  "switched": true,
  "reason": "escalated after 2 failed gate runs",
  "rule": null,
  "level": 1,
  "expected_gain": 0.31,
  "switch_cost": {"tokens": 5200, "seconds": 4.1},
  "estimate": {"input_tokens": 6000, "output_tokens": 400, "seconds": 9.5, "tokens": 6400},
  "candidates": [
    {"worker": "...", "tier": "t2", "level": 1, "score": 0.74, "estimate": {}, "excluded": null}
  ],
  "notes": []
}
```

`features` holds the classifier's features of each task in the answer.

## Ledger

### `GET /api/ledger?group=task|model|day&since=<epoch>`

```json
{
  "totals": {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "reasoning_tokens": 0,
             "wall_seconds": 0.0, "gpu_seconds": 0.0, "steps": 0},
  "groups": [{"key": "2026-10-16", "input_tokens": 0, "...": "same fields as totals"}],
  "reference_cost": {"price_in": 3.0, "price_out": 15.0, "avoided": 1.92}
}
```

`group` defaults to `model` (`<endpoint>/<model>`); `day` uses the local date. `reference_cost`
is null unless the project sets reference prices per million tokens
(`[ledger] reference_price_in` and `reference_price_out` in `stepswitch.toml`); `avoided` is
what the local model work would have cost at those prices.

`LedgerRow` (in `GET /api/tasks/{id}`):

```json
{"step": 3, "ts": 1760600011.0, "step_type": "edit", "endpoint": "ws-local", "model": "qwen2.5:32b",
 "input_tokens": 6100, "output_tokens": 420, "cached_input_tokens": 5000, "reasoning_tokens": 0,
 "wall_seconds": 9.8, "gpu_seconds": 9.8, "usage_source": "server"}
```

## Endpoints and models

### `GET /api/endpoints?check=true`

```json
{
  "endpoints": [
    {"name": "ws-local", "engine": "ollama", "format": "chat-completions", "formats": ["..."],
     "local": true, "url": "http://127.0.0.1:11434", "auth": "none", "models": ["qwen2.5:32b"],
     "tool_parser": null, "reachable": true, "http_status": 200, "error": null,
     "probes": {"qwen2.5:32b": {"created": 1760600000.0, "format": "chat-completions",
                                "capabilities": {"native_tools": true}}}}
  ],
  "managed": [
    {"model": "qwen3-4b-instruct-2507", "engine": "llamacpp", "base_url": "http://127.0.0.1:50342",
     "alive": true, "started": 1760600000.0, "last_used": 1760600100.0}
  ],
  "installed": [{"id": "qwen3-4b-instruct-2507", "name": "Qwen3-4B-Instruct-2507", "ram_gb": 3.1,
                 "roles": ["router"], "running": true, "state": "running"}],
  "model_errors": {"phi-3.5-mini-instruct": "EngineError: ..."}
}
```

`url` is shown for local endpoints only (null for remote ones); keys are never returned.
`reachable`, `http_status` and `error` are present only with `check=true`: the server sends one
read-only model-listing request to each endpoint through the egress layer, without credentials,
and any HTTP answer counts as reachable; `error` names the kind of failure only.
`managed[].alive` is null unless `check=true` (the check asks each engine). `installed[].state`
is `running`, `starting`, `failed` (see `model_errors`) or `stopped`.

### `POST /api/models/{id}/start` and `POST /api/models/{id}/stop`

Start or stop the managed server of an installed registry model. Start answers `202
{"model": "...", "status": "starting"}` and loads the model in the background (`404` for an
unknown model, `409` if it is not installed); stop answers `200 {"model": "...", "stopped":
true}` (`false` if it was not running).

## Bench, claims, compatibility

### `GET /api/leaderboard?run=<name>`

```json
{
  "runs": [{"name": "2026-10-16-mini-general", "suite": "mini-general", "started": "...",
            "finished": "...", "contestants": ["..."], "rows": 120}],
  "run": "2026-10-16-mini-general",
  "leaderboard": {
    "suite": "mini-general", "date": "...", "runs": 120, "tasks": 15, "seeds": [0, 1],
    "command": "...", "commit": "...", "scoring": "...", "router_pool": [],
    "hardware": {"os": "Windows", "arch": "x86_64", "cpu_cores": 32, "ram_gb": 1098.4, "gpus": 4},
    "contestants": [
      {"rank": 1, "contestant": "ws-local/gpt-oss:120b", "pass_rate": 0.967, "passed": 29,
       "runs": 30, "tasks": 15,
       "ci": {"low": 0.9, "high": 1.0, "confidence": 0.95, "method": "cluster bootstrap over tasks (percentile)"},
       "median_wall_seconds": 57.8, "median_tokens": 36201.5, "input_tokens": 1358541,
       "output_tokens": 66457, "cached_input_tokens": 1092998, "cache_hit_rate": 0.80,
       "gpu_seconds": 1511.3, "escalation_rate": 0.0, "reference_cost": null,
       "tests_modified": 0, "statuses": {"done": 29, "failed": 1}}
    ]
  },
  "chart": "/api/leaderboard/2026-10-16-mini-general/pass-rate.svg"
}
```

Runs are the folders under `bench/results/` with a `leaderboard.json`; the default is the newest.
`leaderboard` is that file as `ssw bench report --json` writes it (fields may be missing in
runs written by older versions).

### `GET /api/claims`

```json
{
  "source": "claims/prove-report.json",
  "report": {"mode": "quick", "created": "...", "summary": {}, "platform": {}},
  "claims": [{"id": "C-001", "text": "...", "phase": 1, "status": "passing", "tests": {}}]
}
```

The newest prove report (`prove-report.json` at the project root, else
`claims/prove-report.json`), with each claim's text from `claims/claims.yaml` when present.

### `GET /api/compatibility`

```json
{
  "ci_set": ["qwen3-4b-instruct-2507"],
  "checks": [{"name": "basic_chat", "label": "basic chat"}],
  "results": [
    {"file": "qwen3-4b-instruct-2507--llamacpp.json", "model": "...", "family": "qwen3",
     "engine": "llamacpp", "engine_version": "b11669", "format": "chat-completions",
     "date": "2026-10-16", "passes": true, "failed": [], "tool_path": "native",
     "tool_parser": null, "checks": {"basic_chat": "yes", "json_schema": "error"}}
  ],
  "local": [{"endpoint": "ws-local", "model": "qwen2.5:32b", "created": 1760600000.0,
             "capabilities": {"native_tools": true}}],
  "problems": ["broken.json: ..."]
}
```

`results` are the committed probe results in `probes/` (in a Stepswitch source checkout);
`local` holds the latest probe of each endpoint and model in this project. Check cells are
`yes`, `no`, `skipped` or `error`. Files in `probes/` that cannot be read are listed in
`problems`.

## Audit and science

### `GET /api/audit?task=<id>&limit=200&denied=true`

```json
{
  "rows": [{"id": 1, "ts": 1760600000.0, "method": "POST", "scheme": "http", "host": "127.0.0.1",
            "port": 11434, "path": "/v1/chat/completions", "purpose": "model", "task_id": "...",
            "allowed": true, "local": true, "status": 200, "bytes_out": 5120, "bytes_in": 830,
            "error": null}],
  "totals": {"requests": 120, "denied": 0, "non_local": 0}
}
```

Every connection the egress layer opened or refused, newest first.

### `GET /api/science?task=<id>`

```json
{
  "reports": [
    {"task_id": "...", "run": 1, "step": 12, "status": "failed", "summary": "...",
     "table": "| case | quantity | ... |", "cases": [{"case": "raman-532", "status": "failed"}],
     "plots": ["/api/runs/<task id>/files/artifacts/gates-1/golden/raman-532--wavelength.png"]}
  ]
}
```

Numerical diff reports from the science gates (`golden`), newest first.

### `GET /api/runs/{task_id}/files/{path}`

A file from a task's run directory (`.stepswitch/runs/<task id>/`), such as a plot. Paths
outside that directory get `404`. Images (PNG, JPEG, GIF, WebP) are sent as they are, text files
as `text/plain`, anything else as a download; all with a `sandbox` Content-Security-Policy.

## Live events

### `GET /api/events` (Server-Sent Events)

One stream for the whole dashboard. Event types:

- `task`: a `TaskSummary`, whenever a task's row changes;
- `trajectory`: `{"task_id", "seq", "ts", "type", "step"}` for each new trajectory event;
- `ledger`: `{"task_id", ...LedgerRow}` for each model call;
- `decision`: a `Decision` for each routing decision.

Each event carries an `id`; a client that reconnects with `Last-Event-ID` continues after it
(a resumed stream may repeat a recent `task` event: replace tasks by `id`). A comment line is
sent after 15 quiet seconds to keep the connection open, and the stream starts with
`retry: 2000` and the current state of the tasks that have not ended. The server reads new rows
twice a second, so tasks started by `ssw run` in a terminal appear too.
