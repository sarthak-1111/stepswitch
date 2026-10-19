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
`stop_reason` says which). `escalations` counts moves up the escalation ladder; the task model
also defines an `escalated` status, which the orchestrator does not set today, so clients
should show any status they do not know rather than drop the task.

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
  "offline": false,
  "best_of": 1,
  "on_disagreement": "flag",
  "probe_tests": true
}
```

`best_of` is the number of candidates across model families (1 to 5; 0 picks it from the
task's difficulty) and `on_disagreement` what happens when passing candidates disagree:
`flag`, `tests`, `escalate` or `human` (SPEC §8.3, §8.4; ADR 0025).

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

`result` is null until the task ends. `events` is the number of trajectory events. `decisions`
are in step order and `ledger` rows in the order the calls were made.

A best-of-N result also has `best_of` (the candidate count), `candidates` (per candidate: its
`index`, `family`, `workers`, `seed`, `status`, `changed_files`, `changed_lines`, `branch`,
`review` and `tests_passed`), `winner` (the kept candidate's index), `disagreement` (the
comparison report: `detected`, `clusters`, `splits`, `tests`, `probe_tests`, `outputs`,
`policy`, `resolved`, `resolution`, `kept`) and `uncertain` (the passing candidates disagree
and no policy settled it; nothing was applied). `review` is the winning change's review:
`approved`, `issues` (`severity`, `file`, `line`, `note`), `score`, `blocking`, `reviewer`,
`reviewer_family` and `cross_family`.

### `POST /api/tasks/{id}/cancel`

`202 {"id": "...", "status": "...", "cancel_requested": true}`. A queued task stops at once; a
running one stops at its next step boundary with `stop_reason: "cancelled"`. Cancelling works
for tasks started from the CLI too. `404` for an unknown task, `409` for one that has ended.

### `GET /api/tasks/{id}/events?after=<seq>`

The task's trajectory, in order:

```json
{"events": [{"seq": 1, "ts": 1760600000.1, "type": "task_start", "step": null, "data": {}}]}
```

Types: `task_start`, `worktree`, `status`, `pool`, `catalog` (the workers, the summarizer
and the context settings), `classify` (`data.features`), `learning`, `ladder`, `decision`,
`switch`, `escalation`, `step` (one model call), `aux` (a brief or summary written by the
summarizer), `tool` (one tool call and its result), `compress`, `context`, `handoff`,
`model_error`, `error`, `gates` (`data.verdict`), `review`, `budget`, `cancelled`, `applied`
and `task_end`. `after` returns only events with a larger `seq`. The `data` of the most useful
ones:

- `step`: `step_type`, `worker`, `endpoint`, `model`, `tool_calls` (names), `text` (the first
  400 characters of the reply), `finish_reason`, `usage` (token counts), `wall_seconds`,
  `gpu_seconds`, `response_blob` (the whole answer, kept for replay);
- `tool`: `name`, `call_id`, `arguments` (JSON text), `is_error`, `output` (at most 2,000
  characters; `output_blob` and `raw_blob` keep longer ones whole), `changed_files`,
  `repeated`;
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

### `POST /api/runs/{id}/replay`

Replays a recorded run as a new background task, or forks it (SPEC §11, ADR 0027):

```json
{"from_step": 4, "worker": "ws-local/qwen2.5:32b"}
```

Both fields are optional. Without them the run is replayed: every model answer and tool result
comes from the recording (no model is asked), edits, commands and tests run again for their
effect on the files, and the gates run for real. `from_step` forks at that model call (counted
per run, failed attempts included): from there on models answer live and tools run, on
`worker` when one is given (`worker` alone forks at the first call). The result is never
applied to the working tree.

`201 {"id": "...", "status": "queued", "replay_of": "...", "from_step": null, "worker": null}`;
`404` for an unknown run; `422` for a run that cannot be replayed as asked (recorded before
Phase 7, a `from_step` past the run's model calls, a fork of a best-of-N run, an unknown
worker). The new task is followed like any other; when it ends, its run directory holds
`replay.json` (`GET /api/runs/{id}/files/replay.json`): `identical_diffs`,
`identical_gate_results`, the `diffs` and `gate_results` side by side, `model_calls`
(`recorded`, `from_recording`, `live`), `tools_run_again` and `departures` (every point where
the replay left the recording). `ssw replay <run> [--from-step K] [--worker X] [--json]` does
the same from the command line.

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
is that worker's context size, if known. `since` is when the task was created.

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

`features` holds the classifier's features of each task in the answer. `expected_gain` and
`switch_cost.seconds` are in seconds: the cost model values a success at a fixed number of
seconds and compares the gain in success probability with the time a switch costs
(`stepswitch/router/cost.py`).

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
what the local model work would have cost at those prices, in the prices' own currency.

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

Every connection the egress layer opened or refused, newest first. `totals` count the rows
that match the filters (`task`, `denied`), not only the ones returned under `limit`.

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

# Proxy API (`ssw proxy`)

`ssw proxy` is one local endpoint that external harnesses use as their model server (SPEC §12
item 2, ADR 0004, ADR 0029). Each request is decoded from the caller's API format, routed to a
worker by the router, re-encoded for that worker's endpoint, and answered in the caller's format,
streamed or not. `ssw run --harness <name>` starts a proxy of its own for each harness run (see
[Harness runs](#harness-runs)).

## Access

- The proxy listens on `127.0.0.1` only. The default port is 7778 (`--port`); 7777 is the
  dashboard's.
- Every route except `HEAD /` (the health check) needs the local token. The token is created on
  first start in `.stepswitch/proxy/token`, which is gitignored; `ssw proxy --show-token`
  prints it. It is accepted wherever each format usually carries a key: `Authorization:
  Bearer <token>`, `x-api-key`, `x-goog-api-key`, or the `key` query parameter. A wrong or
  missing token gets `401`.
- Requests whose `Host` is not the proxy's own address get `403`, and so do posts from a web
  page of another origin.
- Inbound credential headers are never logged, stored or forwarded. Upstream calls carry the
  configured endpoint's own key and go through `stepswitch.net`. In offline mode remote
  endpoints are refused (`--offline`, `--allow-remote`).

## Routes

| Format | Routes |
|---|---|
| chat-completions | `POST /v1/chat/completions`, `POST /v1/completions` (text) |
| responses | `POST /v1/responses` (HTTP and SSE; stored responses and `previous_response_id` are kept by the proxy) |
| messages | `POST /v1/messages`, `POST /v1/messages/count_tokens` (an estimate from the tokenizer service) |
| generate-content | `POST /v1beta/models/{model}:generateContent`, `POST /v1beta/models/{model}:streamGenerateContent` |
| Ollama-native | `POST /api/chat`, `POST /api/generate`, `GET /api/tags`, `POST /api/show` |
| listing | `GET /v1/models`: the slots, then every worker ID; a worker with a known context window also has `context_window` |
| Stepswitch | `POST /stepswitch/v1/route` (below) |

Errors use the caller's error shape. Common statuses:

- `400`: a malformed request.
- `401`: a wrong or missing token.
- `429` (`quota`): a bound task's budget is spent.
- `503` (`overloaded`): no worker can take the request.
- `500`, `502` or `504`: an upstream failed, could not be reached or timed out.

An upstream that fails before the first byte reaches the caller is replaced by another worker.
After the first byte, the failure is an error event in the stream.

## Model names

The `model` of a request is a hint, not an address:

- **Slots.** `ssw:auto` lets the router decide. `ssw:plan`, `ssw:edit`, `ssw:fast`,
  `ssw:review` and `ssw:summarize` name the step type the router routes as. Each slot also has a
  dash form (`ssw-auto`, `ssw-plan`, …) for harnesses whose model selectors read `:` as a
  thinking level.
- **Pins.** A worker ID (`<endpoint>/<model>`), a model name or a registry ID pins the request
  to that worker.
- **Aliases.** `[proxy.aliases]` maps other names, such as a harness's own model names, to a
  slot or a worker.
- **Anything else** is routed as `ssw:auto`, and the decision records that.

With `ssw:auto`, a harness's request-class header refines the step type. Compaction requests
are summaries, auxiliary ones (titles, short side questions) are short text, and the rest are
edit steps when the request offers tools and text steps when it doesn't.

## Sessions and switching

- **Session key.** Requests carrying the same `x-stepswitch-session` value belong to one task.
  Without the header, the key is a hash of the system prompt and the first user turn.
- **Steps.** Every request is a step of its task. Each one gets a decision event and a step
  event in the task's trajectory, and a ledger row, so proxied runs show up in `ssw ledger`,
  the trajectory viewer and the dashboard like built-in runs.
- **Switching.** The worker changes only at a new user turn, never while a tool-call loop is
  open. Inside a turn, a request stays on the conversation's worker unless that worker can't
  take it, for example when the transcript no longer fits its window or it failed.

## `POST /stepswitch/v1/route`

This asks the router which worker takes the next request, without sending one. Pi's routed
virtual model calls it before every request.

```json
{"model": "ssw-auto", "reason": "user", "previous": "local/coder:32b", "failed": null,
 "transcript_tokens": 20000, "tools": true}
```

| Field | Meaning |
|---|---|
| `model` | A slot, worker or alias, as in a request (default `ssw:auto`). |
| `reason` | `user` starts a new turn and lets the router switch. Any other value (`continuation`, `retry`, `direct`) continues the turn. |
| `previous` | The worker of the previous request in this turn, kept unless it can't take the next one. |
| `failed` | A worker that just failed. It is left out of this decision. |
| `transcript_tokens` | The size of the transcript, so the chosen worker's window fits it. |
| `tools` | Whether the request will offer tools (default true). |

The answer:

```json
{"worker": "b/large", "context": 131072, "step": 4, "step_type": "edit", "reason": "…"}
```

The decision is recorded and reserved for the named worker. The next request of the task that
names that worker is this step, not a new decision. A spent budget answers `429`, and no
usable worker answers `503`.

## Configuration

```toml
[proxy]
aliases = { "my-harness-default" = "ssw:auto", "big" = "local/coder:32b" }
min_level = 1              # heavy steps start on the first rung at or above it (default 1)
heartbeat_seconds = 5.0    # keep-alive interval while an upstream is quiet

[harness.claude-code]      # one table per harness: claude-code, pi, oh-my-pi, opencode, kimi-code, acp
command = ["claude"]       # default: the harness's command from PATH
permission = "edits"       # or "all" (sandboxed runs only)
max_turns = 60
timeout_seconds = 1800
args = []                  # extra arguments
env = { }                  # extra variables; names that look like credentials are refused
slots = { plan = "ssw:plan" }   # harness role -> proxy model name
keep_state = false         # keep the run's generated config and harness state
```

## Harness runs

`ssw run --harness <name> "<task>"` runs the task in an external harness instead of the
built-in agent (ADR 0029). For each run:

1. **Proxy.** A proxy bound to the task is served on a free loopback port with a fresh token.
   Every request is a step of the task, whatever its session header says.
2. **Start.** The harness starts in the task's worktree with a minimal environment (none of the
   user's keys), its egress switches off, and generated config in a per-run state directory.
   It gets the proxy's address and token through its own variables. Generic ACP agents read
   `STEPSWITCH_PROXY_URL`, `STEPSWITCH_PROXY_TOKEN`, `STEPSWITCH_SESSION`, `STEPSWITCH_MODEL`
   and `STEPSWITCH_CONTEXT_TOKENS`.
3. **Gates.** When the harness ends, the gates run. On failure the harness is sent back with
   the report, continuing its session where it can. Passing changes get the cross-family
   review. The run escalates like a built-in one.
4. **Budget.** Once the task's budget runs out, the proxy refuses further requests and the
   harness is cancelled.

Harness runs are not replayable, because their tool calls run inside the harness.
