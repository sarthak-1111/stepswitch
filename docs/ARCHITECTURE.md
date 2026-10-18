# Stepswitch architecture

Status: Phase 0 design, 2026-10-16. The product spec (docs/SPEC.md) says *what* Stepswitch does;
this document says how the parts fit, which interfaces they expose and which invariants each part
must keep. Decisions that had real alternatives are recorded in docs/decisions/. Facts about
external projects (versions, flags, formats) live in docs/RESEARCH.md with the date they were
checked; this document links to them instead of repeating them.

## 1. Design drivers

1. **The step is the unit of routing.** Model choice happens at every step boundary inside a
   task (SPEC §0, §7, §12.1), and every switch is a logged, costed event. Mid-step switching
   never happens.
2. **One compatibility layer.** Only `stepswitch.compat` knows wire formats, chat templates,
   tool-call syntaxes and reasoning fields. Everything else works on the internal representation
   (IR) and on capability profiles (SPEC §4, working rule 2).
3. **CPU-first.** The core process stays small (no inference engine imported, no heavyweight
   ML libraries at import time). Tiny-tier (T0) models run in an external llama.cpp server or
   Ollama. GPUs are an upgrade path, never a requirement (working rule 8).
4. **Claims are code.** Every public statement has a claim test and an evidence file; phase
   checks read a prove report bound to the source digest (ADR 0006).
5. **Local-first, zero telemetry.** All network I/O goes through one choke point,
   `stepswitch.net`, which enforces offline mode and writes the egress audit log (SPEC §15).
6. **Cross-platform.** Windows, macOS and Linux are first-class: pathlib everywhere, no shell
   scripts in core paths, process management abstracted per OS (section 16).

## 2. Component map and processes

```
 ssw CLI (Typer) ───────────────┐            Local service (FastAPI, long-lived, optional)
   run · agent · bench · prove │            ├─ Task API + SSE + dashboard   (ssw dash)
   probe · calibrate · doctor  │            └─ all-format proxy             (ssw proxy)
                               ▼                         │
                 Orchestrator (in-process library) ◄─────┘
   task queue · router · workers · gates · ledger · trajectories · budgets
        │                    │                      │
        ▼                    ▼                      ▼
 Context services      Compatibility layer     Verification
 repo map · search ·   IR · codecs · tool-call  gates · impact tests ·
 dedup · distill ·     parsers · fallback       best-of-N · disagreement ·
 handoff · tokens      protocol · reasoning ·   review · science gates
                       probe · onboarding
                               │
                               ▼  HTTP only, via stepswitch.net
 Model plane: server manager + engine adapters (llama.cpp server, Ollama, vLLM, SGLang,
 MLX-LM, transformers serve, slow lane) · remote endpoints (opt-in)
```

| Process | Lifetime | Started by | Owns |
|---|---|---|---|
| `ssw` CLI | one command | user | in-process orchestrator for `ssw run`, `ssw bench`, `ssw prove` |
| local service | long-lived | `ssw dash`, `ssw proxy` | Task API, SSE, dashboard assets, proxy routes, server-manager reaper |
| engine servers | lazy, TTL | server manager or the user | model weights, KV caches |
| external harnesses | one task | worker adapters | their own agent loop, pointed at the proxy |
| git worktrees | pooled | worktree pool | isolated checkouts for candidates |

The orchestrator is a library. The CLI and the local service both embed it and share state
through the per-project SQLite database, so `ssw run` works without any service running.

## 3. Orchestrator

- **Task lifecycle.** `queued → routing → running → verifying → done | escalated | failed |
  needs-human` (SPEC §17). Transitions are rows in `events`, emitted on the SSE stream.
- **Queue.** SQLite-backed; concurrency is bounded by worker slots and by model-plane capacity
  (free KV memory per server, SPEC §14 RN2).
- **Run loop.** Take a worktree from the pool → classify the task → for each step: route → run
  the step on the chosen worker → record → check budgets → after an edit cycle, run gates →
  accept, retry, escalate or ask a human (SPEC §7.3, §8).
- **Budgets** (SPEC §7.4) are checked at every step boundary: tokens, wall time, GPU-seconds,
  escalations. A step that has started may finish, so the overrun is at most one step (C-020).
  Hitting a budget ends the run with a distinct status, never silently.
- **Stuck-loop detection** (SPEC §14 RN10): no new diff, no new test outcome and no new file
  read for N steps triggers escalation or a stop.

## 4. Router

`route(task_features, step_type, state) -> Decision`, where `Decision` records the inputs, every
candidate with its prior, estimated time and tokens, switch cost and score, the chosen worker
and a human-readable reason. Every decision is stored and shown in the dashboard.

Pipeline (SPEC §7.1):

1. **Rules** (zero tokens): explicit flags, track constraints (T0 never edits science code,
   working rule 10), offline mode (no remote endpoints), budgets, protected paths, and
   capability requirements from probe profiles (a step that needs parallel tool calls only goes
   to endpoints whose probe passed that check).
2. **Classifier** (T0 model, JSON-Schema-constrained output, SPEC §7.1 item 2). Results are
   cached by input hash. When no T0 model is available, a deterministic heuristic classifier
   (file types, keywords, diff size) answers with low confidence, so routing still works with no
   models installed at all.
3. **Policy**: priors from registry roles and probe results (Phase 2); contextual Thompson
   sampling over workers from Phase 7 (SPEC §10).
4. **Cost model**: expected time and tokens per candidate, plus the cache-aware switch cost of
   SPEC §7.2 (ADR 0024):
   - A per-server prefix registry predicts how much of the prompt each server already holds.
     It is calibrated from the cached-token counts that servers report, and
     `ssw calibrate --cache` seeds it.
   - Staying costs the current worker its own unread tool results.
   - A switch past the brief threshold discounts the new worker's success by a learned
     handoff recovery.
   - Success rates measured by `ssw bench calibrate` replace tier rates for benched models.

Escalation ladder: T0 → T1/T2 → T3 → remote (opt-in), triggered by gate failures after K
attempts, disagreement, low classifier confidence or high risk (SPEC §7.3). Each escalation is a
ledger event with its reason. Decisions are deterministic for fixed inputs and seeds, which
replay relies on (SPEC §11).

### 4.1 Step-level switching, end to end

1. The built-in agent is a state machine over step types (`plan`, `navigate`, `read`, `edit`,
   `run`, `triage`, `review`, `summarize`, `write-text`). Before each step it asks the router
   for a worker.
2. Task state is kept model-independent: the IR transcript plus structured working memory
   (plan, decisions, files touched, failing checks). Each model gets a view built with the
   stable prefix layout (SPEC §13 TK1), so its server-side prefix cache stays warm.
3. When the chosen endpoint differs from the previous step's, the router estimates uncached
   tokens on the new server from the per-server prefix registry. Above the threshold (default
   24k tokens) the new model receives a HANDOFF brief (SPEC §9) instead of the transcript.
   A T0 writer fills in the brief's free-text fields under a JSON schema. The brief is then
   trimmed until the receiving model's tokenizer counts at most 1,500 tokens.
   Reasoning text never crosses models (SPEC §4.5). With `--no-optimize` (the Phase 2
   baseline) a switch always sends the transcript.
4. The switch is logged as a `switch` event with from, to, reason, estimated cost and expected
   gain; C-002 asserts on these events, and the dashboard renders them as step badges.
5. In proxy mode the same logic runs per request: the harness's model name acts as a slot hint
   (`ssw:plan`, `ssw:edit`, `ssw:fast`, or a mapped name) and the request content gives the step
   type.

## 5. Compatibility layer (`stepswitch.compat`)

Details and rationale: ADR 0003. Formats and their fields: docs/RESEARCH.md, "API formats".

- **IR** (Pydantic v2): `Request` (system blocks, messages, tools, tool choice, response format,
  sampling, reasoning effort, output-token limit, stop), `Message` (role and content parts: text,
  image, file, tool call, tool result, reasoning, refusal), `Response` (message, normalized
  finish reason, `Usage` with input, output, cached-input, cache-write and reasoning tokens) and
  a streaming `Delta` union (text, reasoning, tool-call start/arguments/end, usage, finish,
  error).
- **Codecs**, one per format: chat-completions, responses, messages, generate-content, Ollama
  native, llama.cpp native, raw text completion. Each codec works in both directions (client for
  outbound calls, server for the inbound proxy) for requests, responses and streams.
- **Conversion reports.** Anything a target format cannot carry (for example opaque or signed
  reasoning, server-side built-in tools, n>1 candidates, cache-control hints) is listed in a
  `ConversionReport` attached to the step's ledger entry. Strict mode turns any loss into an
  error. Nothing is dropped silently.
- **Tool calls.** When the engine parses tool calls server-side and the probe confirms it for
  that (engine, parser, model), its output is used. Otherwise an incremental parser from the
  syntax catalog (harmony, JSON-in-tags, XML-style function/parameter tags, key/value-tag pairs,
  special-token sections and others listed in RESEARCH.md) parses raw text, also while
  streaming. The parser is selected by hub ID plus chat-template hash, with detection by
  template markers for unknown templates. On chat-completions endpoints it is set per endpoint
  or model (`tool_parser`), or by the serving engine's registry recipe
  (`tool_parsing = "client"`). Earlier calls then go back to the model written in its own
  syntax (ADR 0022).
- **Fallback tool protocol.** For models without native tool calling, tools are described in
  the prompt and the model must emit one JSON action object, constrained by grammar or JSON
  Schema where the endpoint supports it (starting after the reasoning block on reasoning
  models), otherwise validated and repaired with a bounded retry. Forced tool choice and
  assistant prefill are used only where the probe confirmed them. This is what lets any
  text-generating model drive the agent (C-005).
- **Reasoning normalizer.** Maps dedicated reasoning fields (under all their current names),
  think tags and harmony analysis channels into one IR part that also carries opaque or signed
  payloads with their origin, and maps `effort: none|minimal|low|medium|high|xhigh|max` (plus an
  optional token budget) onto each endpoint's mechanism, clamping and reporting unsupported
  levels. Same-model continuations follow the model's retention policy; opaque payloads go
  back only to their origin, and reasoning is stripped on any switch.
- **Capability probe** (`ssw probe`, SPEC §4.6) produces a JSON capability profile stored in
  SQLite and consumed by router rules; `--export` writes a shareable result file.
- **Onboarding** (`ssw models add`, SPEC §4.3) reads metadata (including the template source and
  license conditions), picks engine and quant for the detected hardware, selects the tool-call
  parser, probes, and writes a registry entry.

## 6. Model plane (`stepswitch.serve`)

- **Engine adapter interface** (SPEC §5.3, ADR 0008): `start(model) / stop / health`, plus
  optional `metrics / prefix_cache_stats` (derived from per-response usage and timing fields
  where an engine has no metric) and `slot_save / slot_restore` (llama.cpp server only; other
  engines may offer `kv_offload`).
- **Server manager**: lazy start on first request, keep-alive TTL, LRU eviction under memory
  pressure, health checks, hot-swap. Engines that already multiplex models (Ollama, the
  llama.cpp server's router mode) are treated as one managed server hosting several models; the
  manager tracks what is loaded instead of starting one process per model, and it does not
  evict for engines that load and unload models themselves (it sets their keep-alive instead).
  Engine recipes in the registry carry exact flags, parser names, plugins and minimum versions.
- **Runtime registry**: running servers (pid, port, models, owner, last use) are recorded in a
  machine-level runtime file, so a later `ssw` process adopts them instead of starting duplicates.
  The long-lived local service runs the TTL/LRU reaper; without it, `ssw models stop` and the next
  `ssw` command clean up.
- **Hardware detection** (`ssw doctor`, SPEC §5.1) feeds tier selection and quant choice (for
  example: no native FP8/FP4 on compute capability below 8.9/10.0, so INT4 builds are preferred).
- **Engine availability by OS** is data, not code: vLLM, SGLang and KTransformers are
  Linux-only, so on a Windows workstation they run under WSL2 or a container and are reached over
  HTTP like any other endpoint (RESEARCH.md, "Engines").
- **Slow lane** (SPEC §5 "SL"): asynchronous jobs only, behind the same HTTP interface; engines
  without a server get a thin local HTTP shim.

## 7. Workers and harness adapters (`stepswitch.workers`)

Interface (SPEC §12): `start(task, worktree, endpoint, settings) -> RunHandle`, `events()`,
`cancel()`, `result() -> RunResult`.

- **Built-in lean agent**: tools `read(path, range)`, `grep`, `repo_map`, `lsp`, `edit`
  (hash-anchored), `run(cmd)`, `test(selector)`, `search(semantic)`, exposed as `TOOL_NAMES`.
  Step-level routing lives here.
- **Fake workers**: deterministic scripted workers for CI, the bench and replay tests.
- **External harnesses** run as subprocesses pointed at `ssw proxy`, so the router can still
  choose a model per request. Harnesses that speak the Agent Client Protocol are driven by one
  generic ACP client (ACP v1 over stdio, stable methods only, model choice through a session
  config option when offered); others get small adapters whose generated config is passed
  through environment variables or an isolated config directory. A harness with an in-process
  routing hook (Pi's virtual models) calls the router directly. In offline mode every adapter
  sets its harness's own egress switches (SPEC §12).

## 8. Context services (`stepswitch.context`)

- **Repo map** (TK2): tree-sitter tags, ranking over definition/reference edges, token-budgeted,
  cached per file content hash. Grammars come from pinned per-language wheels that work offline
  (RESEARCH.md, "tree-sitter grammar wheels").
- **Hybrid search** (TK9): ripgrep when present, a Python fallback otherwise, plus T0 embeddings.
- **Read dedup** (TK3): one stub per unchanged range, for reads of 500 tokens or more.
- **Tool-output distillation** (TK4): error lines, file:line references and the tail, plus an
  optional T0 summary. The raw output stays readable as `@output/<n>` for the rest of the task.
- **Context pruning** (TK6): superseded results become stubs in batches. At 60% of the
  transcript budget, older turns are replaced by a HANDOFF brief.
- **HANDOFF briefs** (TK7, SPEC §9).
- **Prompt compression** (TK11, off): Markdown and text reads only, through an
  out-of-process server (`extras/llmlingua-server`).

  All of these run only when the task's `optimize` flag is on (ADR 0024).
- **LSP**: a JSON-RPC client written for Stepswitch, launching language servers that the user
  has installed (ADR 0017).
- **Token counting** (`stepswitch.context.tokens`, SPEC §9): counts with the receiving model's
  tokenizer in a fixed order and records which one was used and whether the count is exact.
  The order: local `tokenizer.json` from the registry, then the llama.cpp server's
  `/tokenize`, then a `tokenizer.json` mapped from the model name (`ssw models pull
  --tokenizers`), then a flagged estimate.

## 9. Verification (`stepswitch.verify`)

Gate order, cheapest first (SPEC §8.1): format → lint → typecheck → impacted tests →
golden/numerical (science) → full suite → review. The first hard failure stops the chain and a
distilled failure report goes back to the worker. Test impact analysis selects tests affected by
changed files (SPEC §8.2). Science gates add golden fixtures with per-quantity tolerances,
numerical diff reports with plots, invariant tests, protected zones and reproducibility
records (SPEC §6.2).

Best-of-N, the disagreement signal and cross-family review (SPEC §8.3 to §8.5, ADR 0025):

- **Candidates** (`verify.best_of_n`). `ssw run --best-of N` (or `auto`, N by difficulty) plans
  one candidate per model family, ranked by the family's best edit prior. Candidates run one
  after another, each in its own worktree, sharing the task's budget. Each routes its steps
  only among its family's workers (`RouterState.families`).
- **Comparison** (`verify.disagreement`). When two or more candidates pass, each one's added
  tests run against every other passing candidate's code, together with one probe test file
  that a model writes from the task and an outline of the new names. In the science track the
  candidates' golden outputs are also compared pairwise at the case tolerances. Candidates that
  agree form clusters; more than one cluster flags the task. The `--on-disagreement` policy
  then flags it (uncertain, never applied), asks for targeted tests, runs one more candidate
  for a majority, or ends it as `needs-human`.
- **Review** (`verify.review`). A reviewer from a family that did not write the change answers
  the §8.5 schema; the router moves the review to another rung if that is where another
  family is. Every passing candidate is reviewed, and selection takes gates → cross and probe
  tests passed → a non-blocking review → change size band → review score → plan order.
- **Records.** The winner's branch is `stepswitch/<task_id>`, the others'
  `stepswitch/<task_id>-c<i>`; the run directory keeps every candidate's patch, and the
  trajectory events of a candidate carry its index.

## 10. State, ledger and trajectories

- **State**: one SQLite database per project at `.stepswitch/state.db`, WAL mode, versioned
  migrations. Tables: tasks, steps, events, decisions, ledger, endpoints, probes, bandit,
  egress_audit, prove_runs. WAL needs SQLite 3.51.3 or later and a local disk; `ssw doctor`
  checks both, and on network volumes the database uses `journal_mode=DELETE` (ADR 0002).
- **Blobs**: `.stepswitch/blobs/<aa>/<sha256>` for raw tool outputs and diffs.
- **Ledger** (TK12): per step tokens in/out, cached tokens, wall time, GPU-seconds and cache
  hits, taken from server-reported usage; estimates are flagged as such. C-019 compares ledger
  totals with server-reported usage.
- **Trajectories**: every step is a JSONL event (prompt hash, worker, tokens, cache hits, tool
  calls, blob refs, diffs, gate results). `ssw replay <run> --from-step k --worker X` forks a new
  run (SPEC §11).

## 11. Proxy (`ssw proxy`)

Details: ADR 0004. Inbound routes for every supported format decode to IR, the router picks an
endpoint per request, the request is re-encoded for the target, and the (streamed) response is
translated back into the caller's format. The proxy binds to 127.0.0.1 (default port 7778) with
a local token, keeps per-session state for cache-aware switching, records every request in the
ledger, and makes all upstream calls through `stepswitch.net`. It switches models only at a new
user turn, never inside an open tool-call loop or while the session depends on state held by the
current endpoint, and it keeps its own response store so stateful responses-format features work
against stateless backends.

## 12. Task API and dashboard

FastAPI app from `stepswitch.api.app.create_app()`; routes per SPEC §17 (`/api/tasks`,
`/api/workers`, `/api/endpoints`, `/api/leaderboard`, `/api/ledger`, `/api/claims`,
`/api/events` as SSE) plus read models for every view (docs/API.md). `ssw dash` binds it to
127.0.0.1:7777 with a per-session token (ADR 0023). Submitted tasks run on a thread pool through
`run_task`; cancel requests and every change go through the state database, so the dashboard
also shows and cancels tasks started in a terminal.

The dashboard is React + Vite + TypeScript in `dashboard/` (one route per SPEC §17 view; see
`dashboard/README.md`). `npm run build` writes `dashboard/dist/`, which the app serves from a
source checkout with an `index.html` fallback for every path outside `/api/`. A wheel build hook
(`hatch_build.py`) ships it as `stepswitch/_dashboard` when it has been built.

## 13. Network egress (`stepswitch.net`)

- One factory returns httpx clients whose transport enforces policy and records audit rows (host,
  port, bytes, purpose, task ID). In offline mode only local addresses (127.0.0.0/8, ::1, Unix
  sockets) are reachable.
- Egress behind a local URL is closed too: in offline mode, engine models that forward to a
  hosted service and hot-swap configs with remote peers are rejected (SPEC §15).
- The secret scanner and redactor run before any remote call; per-path deny lists apply.
- Downloads (model weights, grammars, tokenizer files, BPE tables) happen only in explicit
  commands that go through `stepswitch.net`, including an install-time prefetch into caches
  under `.stepswitch/`. Lazy downloads by libraries are switched off; in offline mode they are
  forbidden, and context services are tested with networking disabled.
- Tests add an interpreter audit hook on socket connects and process spawns as an independent
  observer (C-006, C-007). It cannot see child processes' own connections, so Linux CI also runs
  the flow under OS-level network isolation (ADR 0011).

## 14. Claims and prove

`claims/claims.yaml` (registry) → claim tests in `tests/claims/` marked `claim("C-0xx")` → evidence
JSON in `claims/evidence/` → `ssw prove` runs the selected claims through pytest, records outcomes
per test, writes `claims/prove-report.json|md` with the source digest, updates statuses, and
`--render-readme` regenerates the README claims section. Contracts: ADR 0006.

## 15. Configuration and files

| File | Committed | Holds |
|---|---|---|
| `stepswitch.toml` | yes | tracks, policies, acceptance commands, budgets, science tolerances, protected zones |
| `.stepswitch/local.toml` | no | endpoints, keys, hardware overrides, model directory |
| `registry/*.toml` | yes (package data, user-overridable) | model and worker definitions |
| `claims/claims.yaml` | yes | claims registry |
| `.stepswitch/state.db`, `.stepswitch/blobs/` | no | per-project state |

Precedence: CLI flags > environment > `.stepswitch/local.toml` > `stepswitch.toml` > packaged
defaults. Secrets are only ever read from `local.toml` or the environment.

## 16. Cross-platform notes

- **Processes**: POSIX starts engines in a new session and stops them with SIGTERM then SIGKILL;
  Windows uses a new process group and job objects so children die with their owner. Ports are
  chosen by binding to port 0 on 127.0.0.1.
- **Text and paths**: UTF-8 everywhere (Windows consoles reconfigured), pathlib only, long-path
  awareness for worktrees on Windows.
- **Engines**: see section 6; the doctor reports which engines are usable natively and which need
  WSL2 or a container on the current OS.
- **Isolation for claims**: hosted CI offers containers and OS-level network isolation on Linux
  only (RESEARCH.md, "Network isolation"). On macOS and Windows the evidence comes from the
  in-process recorder alone, and claims say which mechanism was used (ADR 0011).

## 17. Invariants checked by tests

- No module outside `stepswitch.compat` imports a codec or names a wire format.
- No network call bypasses `stepswitch.net`; with offline mode on, only local connections happen.
- Every router decision and every switch has a stored reason and cost estimate.
- Budgets never overrun by more than one step.
- Every public claim has a claim test; README text inside markers matches the registry.
- Nothing machine-specific or company-named is committed (repository hygiene test).
