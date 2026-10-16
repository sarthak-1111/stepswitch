# Progress

Newest phase first. Each phase records what was done, what research changed, open issues and
actions that only the maintainer can take. Phase checks: `uv run python scripts/phase_check.py N`
(one phase) or `--upto N` (phases 0 to N).

## Phase 4: Dashboard and task API

Status: done on 2026-10-16. `scripts/phase_check.py 4` passes 5 of 5 checks (`--upto 4`:
34 of 35, the one failure being C-016). `uv run pytest -q` with the CPU models present and the
dashboard built: 1565 passed, 6 skipped (the same skips as in Phase 3). `ssw prove --quick --phase 4` (617 s): the ten claims of Phases 1 to 3 passing, C-016 pending CI. No
claims are assigned to Phase 4. With this phase, Stepswitch reaches the v0.1 launch candidate of
SPEC §23. The launch candidate also needs `ssw prove --quick` to pass on all three hosted
platforms (C-016).

### Done

- **Task API** (docs/API.md, ADR 0023).
  - `ssw dash` serves the SPEC §17 routes and a read model for every view on 127.0.0.1 (port
    7777 by default).
  - **Tasks:** list, detail, trajectory events, create and cancel.
  - **Work and routing:** workers, live agents, and router decisions with classifier features.
  - **Ledger:** grouped by model, task or day, with a reference-cost counter.
  - **Models:** endpoints with probe profiles and on-request reachability checks; starting and
    stopping managed models.
  - **Results and records:** bench leaderboards with charts, claims, the compatibility matrix,
    the egress audit log, and science reports with plots.
  - **Live feed:** a Server-Sent Events stream.
- **Access control.**
  - A per-session token from the printed link moves into a strict `HttpOnly` cookie, one per
    port. Scripts can send a Bearer header instead.
  - Other `Host` headers and cross-site posts are refused before routing.
  - Every answer carries a Content-Security-Policy and related headers.
  - Answers never contain keys, tokens, server log paths or remote endpoint URLs.
  - Files from a run directory are served only from that directory. Images display inline;
    everything else is sent as text or as a download.
- **Running and cancelling tasks.**
  - Submitted tasks run on a thread pool through `run_task`, one at a time by default
    (`--max-tasks`). A task always ends with a final status.
  - A cancel request is a row in the state database (state migration 3). The budget tracker
    reads it at every step boundary, so the task stops whichever process runs it. This covers
    tasks started with `ssw run` in a terminal too.
  - Ctrl+C on `ssw dash` lets its tasks stop at their next step. A second Ctrl+C, or SIGTERM,
    records them as stopped and exits.
- **Live updates.** The stream polls the state database twice a second, so the dashboard
  follows tasks from any process. A reconnecting client resumes exactly after
  `Last-Event-ID`.
- **Dashboard** (`dashboard/`; React 19.3, Vite 8.3, TypeScript 7.0).
  - Eleven views: a task board with a new-task form and cancel buttons, task detail, agents,
    router, ledger, leaderboard, trajectories, endpoints with start and stop, compatibility,
    audit, science with plots, and claims.
  - Updates arrive live. There are light and dark themes, and the layout works at phone width
    (320 px) without horizontal scrolling.
  - A mock API server (`npm run mock`) lets the dashboard run without the backend.
- **Browser smoke test** (`tests/e2e`; Playwright 1.64 with the installed Chrome).
  - It covers signing in through the link, all eleven views with no script errors, and
    starting a task from the page and cancelling it there.
  - CI builds the dashboard with Node 24 and runs the test. In CI a missing prerequisite is a
    failure, not a skip.
- **Packaging.** A wheel build hook ships the built dashboard as `stepswitch/_dashboard`.
  `ssw dash` serves it from there, or from `dashboard/dist` in a source checkout.
- **Checked on real data.** A copy of this project's state database held 102 tasks, 4,308
  trajectory events and 1,199 ledger rows. Every route answered in 2 to 140 ms. The agents
  view showed a bench task that was running in another process. `ssw dash` was also run end to
  end as a separate process: the printed link signed in, the API refused requests without the
  token, and Ctrl+Break stopped it cleanly.

### What changed against the spec

- ADR 0023 decides several details:
  - The token cookie is named per port.
  - The live feed polls the state database, so tasks from other processes appear.
  - Cancel requests live in the state database.
  - The server writes the event stream itself rather than using the web framework's SSE
    support.
- The dashboard build is not copied into `src/stepswitch/api/static/` (ARCHITECTURE §12,
  updated). A source checkout serves `dashboard/dist`, and wheels carry the build through the
  build hook.
- The trajectory viewer's fork and replay arrive with Phase 7. The dashboard shows them as not
  yet available.

### Open issues

- **Tasks orphaned by a dead process.** If the process running a task dies, the task stays
  `running`. Cancelling it records the request, but nothing acts on it. Recognising dead tasks
  needs a process heartbeat.
- **Large router answers.** The default router answer (200 decisions with their candidates) is
  about 675 KB on this project's data. That is fine locally; it needs paging if projects grow.
- **npm pins.** Newer vite, react and plugin-react releases become eligible between 2026-10-19
  and 2026-10-28 under the 14-day rule (docs/RESEARCH.md).
- **Node on this machine.** The host's Node 20.12 is too old for Vite 8. The dashboard was built
  with a portable Node 24.21 in the session's scratch space. Building the dashboard needs
  Node 22.12 or later.

### Waiting on the maintainer

1. C-016 (Phase 1, unchanged): add a remote and let the `prove` workflow run on the three
   hosted platforms. Its new `dashboard` job builds the dashboard and runs the browser smoke
   test there as well.

## Phase 3: Bench, leaderboard, compatibility matrix

Status: done on 2026-10-16, except C-016, which still needs hosted CI (Phase 1, maintainer
action 1). `scripts/phase_check.py 3` passes 6 of 6 checks (`--upto 3`:
29 of 30, the one failure being C-016). `uv run pytest -q` with the CPU models present: 1520 passed,
6 skipped (C-016 pending, and tests that need an Ollama server with a small model, ripgrep, POSIX
file modes or `STEPSWITCH_LIVE_HUB=1`).
`ssw prove --quick --phase 3` (639 s): C-001, C-002, C-004, C-005, C-007, C-008, C-009,
C-018, C-019 and C-020 passing; C-016 pending CI.

### Done

- **Bench.**
  - **Suites.** `mini-general` has 15 tasks: 14 bug fixes and small features, plus `calc-bug`,
    the claims' toy task. `mini-science` has 6 numerical tasks on synthetic spectroscopy and
    signal code. Each task has golden patches and protected paths.
  - **Runner.** Each run gets an independent acceptance check on a fresh copy of the task.
  - **Metrics.** Leaderboards in Markdown, JSON and SVG carry cluster-bootstrap confidence
    intervals over tasks.
  - **Merging and rescoring.** `ssw bench report` merges runs and rescores rows written under
    an earlier rule. A run passes when every acceptance command exits with 0 on a fresh copy
    with the run's diff applied and the suite's protected paths restored. Changed tests are
    counted separately.
  - **Router pool.** A task can route within a named pool (`--router-pool`).
- **Leaderboards.** These are the first real numbers, measured on the development workstation
  (32 CPU threads, four 48 to 51 GB GPUs). Every contestant ran each task with two seeds; the
  intervals are 95%.

  `bench/results/2026-10-16-mini-general` (15 tasks):

  | Contestant | Passed | Pass rate (95% interval) | Median time | Median tokens |
  |---|---|---|---|---|
  | gpt-oss:120b (GPU) | 29 of 30 | 97% (90% to 100%) | 57.8 s | 36,202 |
  | router over gpt-oss:120b and qwen2.5:32b | 28 of 30 | 93% (83% to 100%) | 49.2 s | 35,392 |
  | qwen2.5:32b (GPU) | 17 of 30 | 57% (37% to 77%) | 58.6 s | 11,760 |
  | Qwen3-4B-Instruct-2507 (CPU) | 9 of 30 | 30% (10% to 50%) | 69.6 s | 18,563 |
  | Phi-3.5-mini-instruct (CPU, fallback protocol) | 3 of 30 | 10% (0% to 27%) | 451.2 s | 42,674 |

  `bench/results/2026-10-16-mini-science` (6 tasks):

  | Contestant | Passed | Pass rate (95% interval) | Median time |
  |---|---|---|---|
  | gpt-oss:120b | 12 of 12 | 100% | 36.0 s |
  | router over the same two models | 11 of 12 | 92% (75% to 100%) | 45.9 s |
  | qwen2.5:32b | 6 of 12 | 50% (17% to 83%) | 88.0 s |

  Prompt-cache hits were 76% to 89% of input tokens for every contestant.
- **Engine adapters** (ADR 0020).
  - vLLM, SGLang, MLX-LM and a small transformers wrapper start from the machine's
    configuration.
  - Every child process runs with telemetry and downloads off.
  - No key ever appears on a command line.
  - `ssw models serve` names every engine.
- **Compatibility matrix** (ADR 0021).
  - `ssw probe --ci-set` probes the CI model set on the llama.cpp server, and
    `ssw probe --render-matrix` writes `docs/COMPATIBILITY.md`.
  - All four CI models pass the probe suite:
    - Qwen3-4B calls tools natively.
    - Phi-3.5-mini and SmolLM2-1.7B call tools through the fallback protocol.
    - Granite-3.3-2B calls tools natively, with Stepswitch parsing the calls (next item).
- **Tool calls parsed from reply text** (ADR 0022). Chat-completions endpoints can parse
  native calls from the reply text in the model's own syntax. This applies when the server
  leaves them there, as the llama.cpp server does for Granite 3.3. It can be set per endpoint,
  per model or per registry recipe, and the probe, the catalog and the matrix all record it.
- **Token accounting** (C-019). The toy task ran on both CPU models, 28 model calls in all.
  The ledger matched the llama.cpp server's own counters exactly for input, cached input and
  output tokens.
- **README numbers** (C-018). README claim lines render their numbers from evidence files that
  record the command, and only `ssw prove` writes committed evidence (ADR 0019).

### What changed against the spec

- ADR 0019: claim tests write committed evidence only under `ssw prove`. A plain test run
  writes the same files to a scratch directory.
- ADR 0020: transformers runs through a small wrapper instead of its own serve command. vLLM
  and SGLang are Linux-only, and MLX-LM is arm64 macOS only. Elsewhere the adapters say why and
  suggest an endpoint (WSL2 or a container).
- ADR 0021: the probe verdict. A probe passes on basic chat, system prompt, usage, formats
  served and one tool path; all other checks are informational. The CI model set has four
  families on CPU.
- ADR 0022: SPEC §4.4's "parse raw text" now covers chat-completions endpoints whose server
  leaves calls in the text.

### Open issues

- **The router did not switch models within a task.**
  - Its priors favour gpt-oss:120b for every step type. It chose that model for 95% of steps,
    and the switch cost (re-reading the context) never paid off.
  - The other 5% of steps went to qwen2.5:32b, always right after the inference server failed
    to parse a gpt-oss:120b tool call. These failovers saved those tasks, but they are not
    counted as switches.
  - The bench therefore shows routing as failover, not yet as step-level model choice. Mixed
    CPU and GPU pools, calibrated per-step priors and cache-aware switch costs are Phase 5
    work, and a learned policy is Phase 7.
- **Qwen3-4B's 30% understates the model.** 20 of its 30 runs ended with `no_worker`. The
  model's managed server is shared with its router role and started with a 4,096-token
  context, so agent turns overflowed it. Context sizes per role (or one server per role) come
  with Phase 5. The runs were not repeated.
- **Some measurements are rough.**
  - The CPU runs shared the machine with the GPU runs, so their times are upper bounds.
  - Phi-3.5-mini needs 451 s per task (median) on this CPU, and 3 of its runs hit the
    900-second budget.
  - The mini-general single-model runs were made at commit `cd9ffff`, before the pool and
    scoring changes, and rescored under the current rule (4 qwen2.5:32b verdicts changed).
    The router runs are from commit `4766b79`.
- **Python engines are untested live.** Their adapters are tested with fakes; no model was
  served through vLLM, SGLang, MLX-LM or transformers on this Windows machine.
- **MLX-LM image URLs.** MLX-LM's server fetches image URLs itself. Keeping image URLs away
  from it in the compatibility layer is still open (ADR 0020).
- **Forced tool choice.** It is "no" for every CI model on the llama.cpp server (b11669).
- **Long Windows paths.** On Windows, bench workspaces under a long project path can exceed the
  260-character path limit. The benches ran from a short worktree path; long-path support
  needs to be on, or the project should sit near the drive root.

### Waiting on the maintainer

1. C-016 (Phase 1, unchanged): add a remote and let the `prove` workflow run on the three
   hosted platforms.

## Phase 2: Built-in agent, router v1, gates, tracks

Status: done on 2026-10-16, except C-016, which still needs hosted CI (Phase 1, maintainer
action 1). `scripts/phase_check.py 2` passes 5 of 5 checks. `uv run pytest -q`: 1273 passed,
6 skipped (C-016 pending, and live or platform tests that need a local server with a small
model, a local server with a probe model, ripgrep, POSIX file modes or `STEPSWITCH_LIVE_HUB=1`).
`ssw prove --quick --phase 2` (364 s): C-001, C-002, C-004, C-007, C-008, C-009 and C-020
passing; C-005 pending until its compatibility-matrix part (Phase 3), with its fallback part
passing; C-016 pending CI.

### Done

- **Built-in agent and router v1** (ADR 0015). One step is one model turn, with typed steps,
  routing by rules, the T0 classifier, priors and a cost model, and every switch logged with
  its reason and cost estimate. The escalation ladder moves up after two gate failures on one
  rung. Per-task budgets (tokens, wall time, GPU-seconds, escalations) are checked at step
  boundaries. `ssw run` and `ssw agent` drive it, with a worktree pool that keeps generated
  files out of diffs, a token ledger and run trajectories.
- **Fallback tool protocol** (ADR 0016). Models without native tool calls get the tools in
  the prompt, one JSON action per reply under a JSON-schema constraint, with validation and
  repair. Tool arguments are spelled out field by field, and tool examples are shown.
- **The agent's tools and context services.** Repo map, search, hash-anchored edits
  (`spec/edit-format.md`, ADR 0018), a built-in LSP client (ADR 0017), test, run and finish.
- **Verification gates.** Lint, types, impacted tests, full tests with acceptance commands,
  protected paths, and science checks with numerical tolerances and plot checks. There are
  general and science tracks; T0 never edits code in the science track.
- **Live results on the development machine.** C-001: the toy task finished inside a Linux
  container limited to 8 GB with no swap and no GPU devices: 15 steps, 29,840 tokens, 113 s,
  peak memory 6.43 GiB. C-005: Phi-3.5-mini-instruct, which has no tool calling, finished the
  toy task through the fallback protocol in 12 model turns.

### What changed against the spec

- ADR 0015: the agent, not the model server, owns the tool loop, so a step may switch workers
  inside it (ADR 0005 item 3 still holds for stored responses and for the proxy). The
  escalation rung is a band, not a single level.
- ADR 0016: the fallback protocol's reply format, constraints, history rendering and repair.
- ADR 0017: a small built-in LSP client instead of pygls and lsprotocol.
- ADR 0018: TK5 narrowed. A whole-file `text` edit replaces files of up to 100 lines. Files
  of up to 60 lines are always read whole. A stale anchor in a file of up to 100 lines shows
  the whole file again.
- The C-005 fallback model is Phi-3.5-mini-instruct, not the 1.5B coder base model. The base
  model kept repeating tool calls and never edited correctly (ADR 0016 item 10).

### Open issues

- Small models vary from run to run. Phi-3.5-mini-instruct failed its first live run
  (`gates_failed`) before the short-file rules, and the claim test records one run. The
  Phase 3 bench measures pass rates over several seeds.
- No LSP server launch commands ship yet; the `lsp` tool works only with a server the user
  configures.
- Assumptions isolated behind interfaces (docs/RESEARCH.md, "Verified during Phase 2"):
  that one engine's batch embeddings follow the input order, and the package installer's
  offline variable.
- Probe exports still carry hardware product names; they must be generic before the Phase 3
  compatibility matrix commits them (Phase 1 issue).

### Waiting on the maintainer

Nothing new. Phase 1 actions 1 to 3 still apply.

## Phase 1: Foundation and compatibility core

Status: done on 2026-10-16, except C-016, which needs hosted CI (maintainer action 1 below).
`scripts/phase_check.py 1` passes 11 of 12 checks; the twelfth fails only because C-016 is
pending. `uv run pytest -q`: 796 passed, 4 skipped (C-016 pending, and three live tests that
need a local server, a small local model or `STEPSWITCH_LIVE_HUB=1`). `ssw prove --quick
--phase 1`: C-004 and C-007 passing, C-016 pending.

### Done

- **Rename** to Stepswitch, CLI `ssw` (ADR 0001), and the package skeleton with hatchling as
  the build backend, so the registry ships inside the wheel (ADR 0012).
- **Configuration, egress and state.** Layered TOML configuration (`stepswitch.toml` committed,
  `.stepswitch/local.toml` gitignored), `ssw init`; every HTTP client comes from
  `stepswitch.net` with a purpose string, an audit row and offline enforcement, and takes no
  proxy settings or `.netrc` credentials from the environment; SQLite state with migrations and
  the WAL safety check (ADR 0002).
- **Compatibility core.** The IR, a codec per SPEC §4.1 format (chat-completions, responses,
  messages, generate-content, Ollama native, llama.cpp native, text completion), streaming
  assembly, tool-call parsers, error mapping with retries, and `ModelClient`. Reasoning keeps its
  full origin (format, endpoint, model) and is replayed only there; refusals are their own part.
  Mock-server conformance tests cover every format (240 tests).
- **Registry and models.** `registry/` with the verified CPU tier; `ssw models pull --tier
  cpu` and `ssw models add <hub id | path | URL>` with verified downloads, GGUF metadata reading
  and chat-template hashes. Three T0 models are installed on the development machine.
- **Model plane.** Server manager with llama.cpp and Ollama adapters (start, health, stop,
  reuse); `ssw doctor` detects OS, CPU, RAM, GPUs, disk and engines and writes a hardware
  profile.
- **Probe and calibration.** `ssw probe` runs a 20-check capability battery (tool calls,
  fallback protocol, JSON schema, streaming, usage semantics, reasoning field and retention,
  context length, prefix cache, throughput and more) and stores the profile; `ssw calibrate`
  measures prefill and decode throughput and time to first token, preferring server timings.
  A full probe of a 4B model on CPU took 60 s (SPEC §4.6 asks for under 3 minutes).
- **Claims framework.** `ssw prove` (quick and full modes, phase and claim selection, fresh
  source digest), the README claims section renderer with its check mode, evidence files with
  machine-specific values scrubbed, and the connection recorder for the network claims
  (ADR 0013). C-016 is proven by evidence that CI writes (ADR 0014).

### What changed against the spec

- ADR 0012: hatchling instead of `uv_build`.
- ADR 0013: the connection recorder falls back from audit hooks to patched socket and process
  functions, because endpoint-security software on the development machine breaks interpreter
  audit hooks.
- ADR 0014: claims whose evidence comes from other machines (C-016) stay pending without it,
  and phase checks keep requiring them.
- Prove reports keep skip reasons without checkout paths, so a committed report holds nothing
  machine-specific.

### Open issues

- IR additions requested by the codec work and not made yet: an image reference by file ID,
  an in-band response error, a description on `ResponseFormat`, and a way for
  `decode_response` to record losses in the conversion report.
- Probe exports carry hardware product names from `ssw doctor`; before exports are committed
  (Phase 3 compatibility matrix) they must be reduced to generic descriptions so that the
  hygiene test passes (ADR 0007 item 3).
- No engine tested enforces a named `tool_choice`, so the router never relies on forced tool
  choice; the fallback protocol uses JSON-schema constrained decoding instead.
- Probe profiles are stored under the public model label; a lookup by a configured file-path
  model ID would miss. The Phase 2 catalog looks profiles up by both.

### Waiting on the maintainer

1. **Remote repository and CI.** C-016 (Linux, macOS and Windows) needs the CI workflow
   (`.github/workflows/prove.yml`) to run on a hosted remote and commit its evidence. Until
   then `phase_check.py 1` and `--upto N` report C-016 as not passing.
2. **Name registration.** Register the package-index and npm names before any public
   announcement.
3. **Development tools.** Node 22.12 or later before Phase 4.

## Phase 0: Research and design

Status: done on 2026-10-16. `scripts/phase_check.py 0` passes all seven checks. Phases 1 to 4
fail, as they should before any product code exists.

### Done

- **Spec.** `docs/SPEC.md` v0.3: the kickoff spec with Phase 0 research applied in place. A
  change table at the top maps every changed section to its ADR or research entry. The claims
  table (SPEC §18.5) is unchanged, because the integrity tests check claim wording against it.
- **Research.** `docs/RESEARCH.md`, produced by thirteen parallel research tasks (one per SPEC §22
  row, plus API formats, open-weight model families, the SPEC §5 model candidates, and the name
  and stack check). It holds an index of 63 projects and items, each marked verified yes or no
  with a date, and tables for API formats, format conversion limits, engines, tool-call formats,
  engine-side tool parsers, open-weight model families, default model candidates, harnesses,
  orchestration, positioning, token counting, network isolation, the name check and the stack.
  It ends with a table of SPEC corrections and how each was resolved.
- **Architecture.** `docs/ARCHITECTURE.md`: components, processes, interfaces and invariants.
- **Decisions.** `docs/decisions/`:
  - 0001 Project name and CLI name: rename to Stepswitch (CLI `ssw`) at the start of Phase 1
  - 0002 Stack and minimum versions
  - 0003 Compatibility layer
  - 0004 Proxy design
  - 0005 Router design
  - 0006 Claims registry, public-text lint and prove contracts
  - 0007 Scope and enforcement of the no-company-names rule
  - 0008 Engine targets and platform scope
  - 0009 Default models per tier
  - 0010 Positioning against existing projects
  - 0011 Scopes of the platform and isolation claims
- **Claims.** `claims/claims.yaml` registers C-001 to C-020 as `pending`, with the public-text
  lint configuration. Claim texts match SPEC §18.5. Four scopes were made precise (ADR 0011).
- **Tests.** `tests/claims/test_integrity.py` implements the SPEC §18.3 meta-tests, with
  self-tests on synthetic text. `tests/unit/test_repo_hygiene.py` enforces working rules 4 and 5:
  no company names (hashed denylist) and nothing machine-specific in committable files.
- **Phase checks.** `scripts/phase_check.py` covers phases 0 to 4 (`N`, `--upto N`, `--json`).
  From Phase 1 on, a phase also requires a fresh `sy prove` report whose source digest matches
  the working tree.
- **Development environment.** uv with a uv-managed Python 3.12 interpreter, because
  interpreters that bundle SQLite older than 3.51.3 have a WAL corruption bug.

### What research changed

Details are in the SPEC change table and the ADRs. The main points:

- **Name.** The package-index name is taken, a same-domain project uses the name, and `sy`
  collides with a same-domain tool. The project becomes Stepswitch (`ssw`) as the first change
  of Phase 1 (ADR 0001).
- **Positioning.** Per-turn routing inside agent sessions, cache-aware switching and per-task
  (harness, model) selection already exist elsewhere. Our novelty is the combination: step-type
  routing with a policy that learns from the user's own verified outcomes, plus verification,
  claims and the science track. Public text says "combines", never "first" (ADR 0010).
- **Compatibility layer.** The responses codec follows the open, vendor-neutral specification.
  The generate-content reference is labelled legacy, and the newer interactions format is a
  watch item. Reasoning keeps its origin, because signed reasoning may only be replayed to the
  model that produced it. Effort has seven levels. Every server-dependent capability is probed
  before it is trusted. Tool-call parsers are chosen per model version (ADR 0003).
- **Engines.** TensorRT-LLM is dropped as an adapter. vLLM, SGLang and KTransformers are
  Linux-only, so on Windows they run under WSL2 or in a container. The llama.cpp server's router
  mode and slot files cover hot-swap and resume (ADR 0008).
- **Models.** The default CPU set takes 4.78 GB on disk and about 5.7 GB of RAM. One candidate
  ID was renamed and several sizes and fits were corrected. Licenses with commercial conditions
  are flagged in the registry (ADR 0009).
- **Supply chain.** The gateway library named in the spec shipped compromised releases in 2026.
  The bridge to it is optional, off by default and isolated out of process (ADR 0003).
- **Claim scopes.** Hosted CI offers containers and OS-level network isolation only on Linux.
  The container and isolation claims are scoped to Linux, and the platform claim uses pinned
  runner labels (ADR 0011).
- **Company-name rule.** Identifiers that other software requires verbatim (protocol headers,
  environment variables, parser and package names) are allowlisted explicitly. Prose never names
  companies (ADR 0007).

### Open issues

- Items marked `verified: no` in RESEARCH.md:
  - the interactions format, whose version path is not settled;
  - embeddings, rerank and realtime endpoints, which are outside the C-004 wording;
  - CI network isolation, which can only be confirmed by running hosted CI;
  - one model family (LongCat).
- The SPEC §0 pitch contains unregistered superlatives, so the README must use claim-backed
  wording (ADR 0006).
- Spikes still to run:
  - whether testmon data copied between worktrees at the same base commit stays valid (Phase 2);
  - harmony rendering fidelity against the server's prefix cache (Phase 1 codecs).
- The Linux-only claim parts (C-001, and the OS-isolated part of C-006) will report "not
  applicable here" on Windows and macOS development machines.

### Waiting on the maintainer

1. **Working rules file.** The kickoff package's working-rules file (`CLAUDE.md`) is not in the
   repository yet. Copying it in from the kickoff archive was held back for the maintainer's
   approval, because that file gives standing instructions to every future agent session.
   Add it yourself or approve the copy, then commit it.
2. **Name.** Confirm Stepswitch (`ssw`), or pick the runner-up Shuntyard, which keeps `sy`
   (ADR 0001). The rename is the first change of Phase 1, and from then on the phase goal
   commands use the new CLI name.
3. **Name registration.** Register the package-index and npm names before any public
   announcement.
4. **Remote repository and CI.** C-016 (macOS, Linux and Windows) needs hosted CI, so a remote
   repository has to be created.
5. **Development tools.** Install uv 0.13 or later (it matches CI's Python 3.15 default) and
   Node 22.12 or later (the current LTS line is recommended) before Phase 4. The Ollama release
   that was researched is 0.40.5; older installs may lack routes the research relies on.

## Next: Phase 5 (after the maintainer confirms)

Token and cache optimization (SPEC §23): TK1 to TK12, HANDOFF v0.1 briefs, and cache-aware
switching with calibration. Claims due: C-003, C-010 and C-011. The Phase 3 bench gives the
baseline that C-011 compares against. It also showed two things for this phase to fix: the
router moved a step to another model only after a server error, never by choice, and one
server shared by a model's router and worker roles caps the context of agent turns.
