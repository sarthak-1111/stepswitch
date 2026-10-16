# Switchyard: Product Spec (v0.2 draft)

Status: draft for Phase 0 review. Anything marked (verify) must be confirmed in Phase 0 and
recorded in docs/RESEARCH.md before code depends on it.

---

## §0 Name and pitch

- Working name: Switchyard. CLI: `sy`. Phase 0 checks the name on the package index and on code
  hosting. If it's taken, propose three alternatives in an ADR and pick one.
- One-line pitch: "Routes every task, and every step inside it, to the best model and harness for
  the job, works with any open model or API, and proves every claim it makes."
- Headline feature: **step-level model switching inside a single task.** Planning, navigation,
  editing, test triage and review can each run on a different model, chosen at runtime, with the
  cost of switching taken into account.
- Second headline: **`sy prove`.** Every public claim maps to an automated test that anyone can
  run on their own machine.

## §1 Positioning: what exists vs what is new

Already exists elsewhere (we integrate rather than compete): role-based model assignment in
harnesses, manual mid-session model switching, request-type routing proxies for coding agents,
multi-provider gateways, parallel agent fleets in git worktrees, deterministic YAML agent workflows.

New in Switchyard (each must be real, tested and demoable):
1. Automatic step-level routing inside one task, driven by a classifier plus a learned policy.
2. Routing over (harness, model) pairs, not just models.
3. Universal compatibility: any open-weight model servable by a supported engine and any endpoint
   in a supported API format, including models with no native tool calling (§4).
4. Cache-aware switching: a cost model that weighs re-reading context on the new model against
   the expected quality gain.
5. Verification-driven ensembles: best-of-N across different model families, chosen by tests.
6. Cross-model disagreement used as an uncertainty signal for escalation.
7. A router that learns from the user's own pass/fail history.
8. Fork and replay of agent trajectories from any step, with a different worker.
9. An open handoff format (HANDOFF v0.1) for compact context transfer between models.
10. A built-in benchmark that runs the user's own tasks across combinations and prints a leaderboard.
11. A science track with tolerance-aware numerical verification.
12. Verifiable zero-egress mode with an audit log.
13. Runs on anything from a CPU-only laptop to a GPU cluster, plus a slow lane that runs very large
    models on small GPUs for asynchronous jobs.
14. A claims registry and `sy prove`: the README shows only claims whose tests currently pass (§18).

## §2 Core concepts

- **Task**: a unit of work with an instruction, a repo or worktree, a track and acceptance checks.
- **Step**: one agent turn within a task. Step types: `plan`, `navigate`, `read`, `edit`, `run`,
  `triage`, `review`, `summarize`, `write-text`.
- **Track**: `general` or `science`. Selects policies, gates, glossaries and defaults.
- **Role**: what a model is used for (router, summarizer, embedder, planner, editor, reviewer...).
- **Endpoint**: a reachable model, either a local engine server or a remote API, with a format.
- **Capability profile**: what an endpoint can actually do, measured by `sy probe` (§4.6).
- **Worker**: a (harness, endpoint, settings) triple that can execute steps or whole tasks.
- **Tier**: a hardware class that determines which models are practical locally (§5).
- **Policy**: rules plus learned parameters mapping (task features, step type) to a worker.
- **Gate**: an ordered set of verification checks producing a Verdict.
- **Trajectory**: the recorded event stream of a run, which can be replayed or forked.
- **Ledger**: per-step accounting of tokens, wall time, GPU-seconds and cache hits.
- **Claim**: a public statement about Switchyard with an ID, exact wording, scope and tests (§18).

## §3 Architecture

```
            ┌──────────────────────────── Dashboard (React) ────────────────────────────┐
            │ task board · live agents · router decisions · ledger · leaderboard         │
            │ trajectories (fork/replay) · endpoints & probes · egress audit · science   │
            └───────────────────────────────────▲──────────────────────────────────────┘
                                                │ REST + SSE
                                    Task API (FastAPI)
                                                ▼
            ┌──────────────────────────────── Orchestrator ─────────────────────────────┐
            │ Task queue → Router (rules → T0 classifier → policy/bandit → cost model)  │
            │ Workers: built-in lean agent · external harness adapters (via proxy)      │
            │ Gates: impact tests → golden/numerical → best-of-N → disagreement →       │
            │        cross-family review → accept / escalate / ask human                │
            └──────────┬─────────────────────────────────────────────────┬─────────────┘
                       ▼                                                 ▼
       Context services                                  Compatibility layer (§4)
       repo map · hybrid search · read dedup ·           API format codecs · tool-call parsers ·
       output distillation · handoff                     fallback tool protocol · reasoning
                                                         normalizer · capability probes
                                                                         ▼
                                                         Model plane: server manager + engines
                                                         T0 CPU · T1 · T2 · T3 · slow lane · remote
```

All state (tasks, steps, ledger, trajectories, probe results, bandit parameters) lives in one
SQLite file per project at `.switchyard/state.db`. Large blobs (raw tool outputs, diffs) live in
`.switchyard/blobs/`, addressed by content hash.

## §4 Compatibility layer: every open model, every API format

Goal: any open-weight model that a supported engine can serve, and any endpoint that speaks a
supported API format, works as a Switchyard worker. Nothing in the core talks to a model except
through this layer.

### §4.1 API formats (inbound proxy and outbound clients)
Implement a codec per format with a shared internal representation (messages, tool calls, tool
results, reasoning, usage, streaming deltas, errors):
- chat-completions format (the de facto standard for open-model servers and most gateways)
- responses format
- messages format
- generate-content format
- engine-native formats: Ollama native API, llama.cpp server native endpoints (completion, slots)
- raw text-completion endpoints (no chat template applied server-side)
- Long tail: an optional bridge to a multi-provider gateway library (study LiteLLM), pinned to an
  exact verified version and isolated behind the codec interface.

Every codec supports streaming and non-streaming, tool calls (single and parallel), structured
output where the endpoint offers it, usage reporting, and error mapping with retries and backoff.
Conversion between any two formats is lossless for messages and tool calls; anything that can't
be represented is reported, never silently dropped.

### §4.2 Engines (local model plane)
Engine adapters share one interface (§5.3). Supported targets, each verified in Phase 0:
llama.cpp server (GGUF; widest architecture coverage, CPU-first), Ollama, vLLM, SGLang, MLX-LM
(macOS on ARM), a plain transformers fallback (any architecture the library supports, slow but
universal), AirLLM and KTransformers (slow lane, §5). Optional: ExLlamaV3, TensorRT-LLM (verify).

### §4.3 Model onboarding: `sy models add <hub-id | local path | endpoint URL>`
1. Read metadata: architecture, parameter count (total and active), context length, license,
   chat template, tokenizer, available quantizations.
2. Pick an engine and quant that fit the detected hardware (§5.1), or register the endpoint as-is.
3. Select the tool-call parser from the chat template and probe results (§4.4).
4. Run `sy probe` (§4.6) and store the capability profile.
5. Write a registry entry. No hardcoded model list is needed for a model to work.

### §4.4 Tool-calling compatibility
- Native: per-family parsers for the tool-call formats in use today (for example the harmony
  format used by gpt-oss, XML-tag formats, JSON-in-tags formats, plus whatever Phase 0 finds).
  When an engine already parses tool calls server-side, use its output; otherwise parse raw text.
- Fallback text protocol: for models with no native tool calling, describe tools in the prompt
  and force a JSON action object with grammar or JSON-Schema constrained decoding (llama.cpp
  grammars, engine guided decoding) or validate-and-repair where constraints are unavailable.
  This is what makes "any text-generating model can drive the agent" true.

### §4.5 Reasoning normalization
Map reasoning output (dedicated fields, think tags, harmony channels) into one internal field.
Map effort controls to `effort: low | medium | high | max` per endpoint. Reasoning text is never
passed to a different model during a switch; only HANDOFF content is (§9).

### §4.6 Capability probe: `sy probe <endpoint>`
A short battery, under 3 minutes on a T0 model:
basic chat · system prompt adherence · native tool call · parallel tool calls · fallback tool
protocol · JSON Schema adherence · streaming · usage reporting · reasoning field detection ·
context length check (needle test at configured lengths) · prefix cache behavior (repeat-prefix
latency drop) · prefill and decode throughput.
Output: a capability profile (JSON) stored in SQLite and used by the router.

### §4.7 Compatibility matrix
- `docs/COMPATIBILITY.md` is generated from probe results, never hand-written.
- CI runs probes on a small set of tiny models from different families on every release and on
  a weekly schedule, plus mock-server conformance tests for every API format on every commit.
- Users can submit probe results for their own models via pull request (`sy probe --export`
  produces a result file with model hash, engine version, hardware and date).

### §4.8 Claim scope
The public compatibility claim is worded as: "Works with any open-weight model your installed
engines can serve, and any endpoint in a supported API format." The matrix shows exactly what has
been verified. The claims system (§18) enforces this wording.

## §5 Hardware tiers and default models

Registry entries are data (`registry/*.toml`): hub ID, quant, file size, RAM/VRAM need, context
limit, tool-calling mode, roles, license, engine recipes, date verified. All model IDs below are
candidates (verify) as of October 2026; any other model can be added via §4.3.

| Tier | Hardware | Candidate defaults | Typical roles |
|---|---|---|---|
| T0 | CPU only, 8–16 GB RAM | Qwen3-4B-Instruct-2507, Qwen3.5 4B / 2B / 0.8B, Gemma 4 E2B / E4B, Phi-4-mini, Qwen2.5-Coder-1.5B (FIM), Qwen3-Embedding-0.6B (embeddings) | router/classifier, summarizer, log triage, commit messages, search ranking, handoff writer, embeddings |
| T1 | one GPU, 12–24 GB | Qwen3.8-27B (4-bit), Devstral Small 2 (24B), small-active MoE models with expert offload | editor for small repos, reviewer, budget planner |
| T2 | workstation, 96–192 GB | Qwen3.8-Flash-Next (~111 GB Q4 GGUF), gpt-oss-120b, GLM-5.3-Flash (tight at 4-bit) | editor, planner, reviewer |
| T3 | multi-GPU nodes | GLM-5.3, Kimi K3, MiMo-V2.6-Pro, DeepSeek V4.1 Flash | hardest edits, planning, greenfield builds |
| SL | slow lane, any GPU ≥ 4 GB | T3 models via layer streaming (AirLLM) or CPU-GPU hybrid MoE (KTransformers) | asynchronous second opinions, overnight review, best-of-N candidates |
| R | remote endpoints | any endpoint in a supported API format | opt-in escalation; disabled in zero-egress mode |

Defaults by role (overridable in policy):
- Classify, route, summarize tool output, triage logs, write commit messages: T0.
- Search ranking: T0 embeddings + ripgrep.
- Plan: best available endpoint.
- Edit an existing large repo: GLM-class model when available.
- Greenfield and frontend builds: K3-class model when available.
- Review: a model from a different family than the author of the patch.
- Science-track edits: largest available endpoint. T0 never edits code in the science track.

### §5.1 `sy doctor`
Detects OS, CPU (cores, AVX2/AVX-512/AMX, ARM features), RAM, GPUs (vendor, VRAM, compute
capability including FP8/FP4 support), disk space, installed engines. Prints a recommended
profile and writes it to `.switchyard/local.toml`. `--json` for machine-readable output.

### §5.2 `sy models pull --tier cpu`
Installs the default T0 set: one router model, one summarizer, one embedding model, optionally a
FIM code model. Uses llama.cpp server (preferred) or Ollama if present. Respects a footprint cap
(default 8 GB, `--max-gb`), a configurable model directory, and asks before downloading. Never
redistributes weights; downloads from the original source per license.

### §5.3 Server manager and engine interface
`start(model) / stop / health / metrics / prefix_cache_stats / slot_save / slot_restore`
(the last two only where the engine supports them; verify llama.cpp slot save/restore).
Lazy start on first request, keep-alive TTL, LRU eviction when memory is tight, health checks,
hot-swap (study llama-swap).

## §6 Tracks

### §6.1 General track
Web apps, APIs, CLIs, scripts. Gates: format → lint → typecheck → impact tests → full tests.
Frontend tasks optionally add a build check and screenshot diff with Playwright. API tasks add
contract tests when an OpenAPI schema exists.

### §6.2 Science track
For research code: data pipelines, numerical analysis, fitting, simulation. Zero-egress on by
default.
- **Golden fixtures**: `tests/golden/<case>/` holds input data and expected outputs
  (`.npz`, `.csv`, `.json`). Tolerances per quantity live in `science.toml` (`rtol`, `atol`,
  comparison mode: elementwise, integral, peak positions, spectral overlap).
- **Numerical diff report**: run reference datasets before and after each patch; produce a table
  of max abs/rel deviation per quantity plus overlay plots (PNG), attached to the verdict.
- **Invariant tests**: property-based tests with Hypothesis (normalization conserved, physical
  bounds, monotonicity, symmetry) from templates the user fills in.
- **Protected zones**: physical constants, calibration values and fitting defaults declared in
  `science.toml`. Changes fail the gate unless the task explicitly allows them.
- **Reproducibility**: record seeds, lockfile hash and environment snapshot per run.
- **Notebooks**: pair `.ipynb` with py:percent via jupytext; agents edit the paired script.
- **Glossaries**: `science/glossaries/*.md` (crystallography, spectroscopy, scattering, signal
  processing), injected only when the classifier detects the domain.
- **Bundled examples**: synthetic data only. Example tasks: XRD peak fitting, pair distribution
  function transform, spectrum baseline correction, unit conversion bug, vectorization refactor
  that must keep outputs within tolerance.

## §7 Router

### §7.1 Pipeline
1. **Rules** (zero tokens): file types, explicit flags (`--track`, `--worker`), protected paths,
   offline mode, budgets, capability requirements (e.g. a step needing parallel tool calls only
   goes to endpoints whose probe passed that check).
2. **Classifier** (T0 model, JSON-Schema constrained):
   `{task_type, step_type, difficulty: 1-5, domain, risk: low|med|high, est_context_tokens,
   needs_new_code: bool, frontend: bool, confidence: 0-1}`.
3. **Policy**: maps features to candidate workers with prior success estimates (registry priors,
   probe results, then the learned bandit, §10).
4. **Cost model**: estimates time and tokens per candidate including switch cost (§7.2), picks
   the best expected value within budget.

Every decision is logged with inputs and reasons and shown in the dashboard.

### §7.2 Cache-aware switching
A model cannot reuse another model's prefix cache, so switching means the new model re-reads
uncached context.

```
switch_cost(m)   = uncached_tokens(m) / prefill_tps(m) + handoff_cost
expected_gain(m) = (p_success(m) - p_success(current)) * value_of_success_seconds
switch if expected_gain(m) - switch_cost(m) > margin
```

- `prefill_tps` and `decode_tps` come from the probe or `sy calibrate`.
- `uncached_tokens(m)` comes from a per-server prefix registry (hash of the prefix last sent to
  that server) and engine prefix-cache metrics where available.
- Above a threshold (default 24k uncached tokens), send a HANDOFF brief (§9) instead of the
  transcript.
- Switching back to a model that saw an earlier prefix reuses its warm cache for that prefix.

### §7.3 Escalation ladder
T0 → T1/T2 → T3 → remote (opt-in). Triggers: gate failure after K attempts (default 2),
disagreement (§8.4), classifier confidence below threshold, high risk. Every escalation is a
ledger event with its reason.

### §7.4 Budgets
Per task: max tokens, wall time, GPU-seconds, escalations. Hitting a budget stops the run with a
clear status, never silently.

## §8 Verification

### §8.1 Gate order (cheapest first)
format → lint → typecheck → impacted tests → golden/numerical (science) → full test suite →
review. Stop at the first hard failure and feed a distilled failure report back to the worker.

### §8.2 Test impact analysis
Run tests affected by changed files first (pytest-testmon or a coverage map, verify). The full
suite runs only at the final gate.

### §8.3 Best-of-N across families
N by difficulty: d ≤ 2 → 1, d = 3 → 2, d ≥ 4 → 3. Candidates come from different model families
where possible, each in its own worktree from a pre-created pool. Selection: passes gates →
smaller diff → higher cross-review score.

### §8.4 Disagreement signal
If two or more candidates pass but behave differently (extra generated tests disagree, or science
outputs differ beyond tolerance), mark the task uncertain, then generate targeted tests,
escalate, or ask the human, depending on policy.

### §8.5 Cross-family review
A reviewer from a different model family checks the winning diff against the instruction and
acceptance criteria. Output: `{approve, issues:[{severity, file, line, note}]}`.

## §9 HANDOFF v0.1 (open spec)

Publish as `spec/handoff-v0.1.md` with a JSON Schema at `spec/handoff-v0.1.schema.json`.
Target ≤ 1,500 tokens, measured with the receiving model's tokenizer. Written by the T0
summarizer, validated against the schema, renderable as Markdown.

```json
{
  "version": "0.1",
  "task_id": "string",
  "goal": "string",
  "track": "general|science",
  "constraints": ["string"],
  "acceptance": {"commands": ["string"], "golden": ["string"]},
  "state": {
    "done": ["string"],
    "decisions": [{"what": "string", "why": "string"}],
    "open_questions": ["string"]
  },
  "files": [{"path": "string", "symbols": ["string"], "why": "string"}],
  "failing": [{"check": "string", "excerpt": "string"}],
  "budget_left": {"tokens": 0, "seconds": 0},
  "provenance": {"from_worker": "string", "step": 0}
}
```

## §10 Learning router

- Contextual Thompson sampling. Arms are workers; context is classifier features plus repo
  language, track, context size and capability-profile flags.
- Reward = success − λ·normalized_time − μ·normalized_tokens (λ, μ configurable).
- Cold start from registry priors, probe results and bench results.
- Data stays local. Optional opt-in export of aggregate, anonymized stats; off by default.
- A simulation harness with synthetic workers tests convergence.

## §11 Trajectories: record, fork, replay

- Every step is a JSONL event: prompt hash, worker, tokens in/out, cache hits, tool calls, output
  blob refs, diffs, gate results.
- `sy replay <run> --from-step k --worker X` forks a new run from step k with a different worker.
- Deterministic replay where possible (temperature 0, fixed seeds, recorded tool outputs).
- Same-model resume uses engine KV slot save/restore when supported (verify).

## §12 Workers and harness adapters

Interface: `start(task, worktree, endpoint, settings) -> RunHandle`, `events()`, `cancel()`,
`result() -> RunResult`.

1. **Built-in lean agent** (`sy agent`), the default worker. Tools: `read(path, range)`, `grep`,
   `repo_map`, `lsp(defs|refs|hover|diagnostics)`, `edit(hash-anchored)`, `run(cmd)`,
   `test(selector)`, `search(semantic)`. Step-level routing lives here.
2. **Proxy** (`sy proxy`): a local endpoint that accepts every supported API format (§4.1) and
   forwards to any endpoint in any format. External harnesses point at it, and the router picks
   a model per request. This is how mid-session switching works inside third-party harnesses.
3. **Claude Code adapter**: headless runs pointed at the proxy, model slots mapped by the router.
4. **Oh My Pi / Pi adapter**: TypeScript extension registering Switchyard as a provider and a
   model-role resolver.
5. **OpenCode and Kimi Code adapters**: subprocess adapters with config generation.
6. **Agent Client Protocol client**: generic adapter for any ACP-capable agent (verify spec).

## §13 Token optimization

| ID | Technique | Detail |
|---|---|---|
| TK1 | Stable prefix layout | `[system][tools][repo map][glossary]` byte-identical across steps; volatile content last. |
| TK2 | Repo map | tree-sitter symbol graph ranked by references, token-budgeted (default 2k), cached per commit. |
| TK3 | Read dedup | content-hash cache; re-reading an unchanged file returns a short stub. |
| TK4 | Tool-output distillation | outputs > 2k tokens summarized by T0, keeping error lines, file:line refs and numbers verbatim; raw output stored and retrievable. |
| TK5 | Diff-only edits | hash-anchored or unified-diff edits; never whole-file rewrites. |
| TK6 | Context pruning | drop superseded tool results; rolling structured summary at 60% of the budget. |
| TK7 | Handoff briefs | HANDOFF v0.1 on model switches (§9). |
| TK8 | Constrained decoding | JSON Schema or grammar constraints for classifier, router, reviewer, handoff, fallback tool protocol. |
| TK9 | Hybrid search | T0 embeddings + ripgrep to read fewer files. |
| TK10 | Per-role budgets | max tokens and reasoning effort per role (low for navigation, high for planning). |
| TK11 | Prompt compression | optional, docs only, never code (study LLMLingua). |
| TK12 | Token ledger | per task, step, model and day, in SQLite and the dashboard. |

## §14 Run optimization

| ID | Technique |
|---|---|
| RN1 | Server manager with lazy start, TTL, LRU eviction, hot-swap (§5.3). |
| RN2 | Continuous batching for parallel agents on vLLM/SGLang; concurrency per server from free KV memory. |
| RN3 | Speculative decoding with a T0 draft model where supported (verify per engine). |
| RN4 | KV cache quantization where supported. |
| RN5 | MoE expert offload to system RAM (llama.cpp `--n-cpu-moe`, KTransformers) for T1/T2 boxes (verify flags). |
| RN6 | Test impact analysis first, full suite last (§8.2). |
| RN7 | Cheapest gates first (§8.1). |
| RN8 | Pre-created worktree pool; shared dependency caches (uv cache, pnpm store). |
| RN9 | Slow-lane queue for asynchronous jobs; results arrive as review comments. |
| RN10 | Timeouts, budgets, kill switches; stuck-loop detection (no progress for N steps). |
| RN11 | `sy calibrate`: measure throughput per endpoint for the cost model. |

## §15 Privacy and zero-egress

- `--offline` or `privacy.mode = "local"` blocks every non-local connection at `switchyard.net`.
- Egress audit log: every connection with host, bytes, purpose and task ID; visible in the
  dashboard and via `sy audit`.
- Secret scanner and redactor before any remote call. Per-path deny lists (e.g. `data/**`).
- No telemetry, ever, by default.

## §16 Benchmark and leaderboard

- Suites in `bench/suites/*.yaml`. Each task: repo (bundled toy repo, local path, or URL +
  commit), instruction, acceptance commands, golden checks, track, tags.
- Matrix runner over workers × tasks × seeds, parallel where hardware allows.
- Metrics: pass rate with bootstrap confidence intervals, median wall time, tokens, GPU-seconds,
  escalation rate, cache hit rate, and a reference-cost-equivalent at a user-set price per
  million tokens.
- Outputs: `results.jsonl`, `leaderboard.md`, `leaderboard.json`, charts, dashboard view.
- Built-in suites: `mini-general` (10–20 small tasks on bundled toy repos), `mini-science`
  (synthetic numerical tasks), `own` (user-defined, the main selling point).
- Deterministic fake workers for CI, so the bench machinery is testable without models.
- Every published number links to its results file, command, hardware and date.

## §17 Dashboard and task API

Local web UI, default `http://127.0.0.1:7777`, bound to localhost with a local token.

Views: task board (queued → routing → running → verifying → done / escalated / failed /
needs-human) · live agents (worker, step type, tokens/s, context used, cache hit %) · router
decisions (features, candidate scores, cost estimates, switch events with cost) · ledger (tokens,
time, GPU-seconds by task, model and day; reference-cost-avoided counter) · leaderboard ·
trajectory viewer with fork and replay · endpoints (status, probe profiles, start/stop) ·
compatibility matrix · egress audit log · science view (numerical diff reports with plots) ·
claims view (latest `sy prove` report).

Task API: `POST /api/tasks`, `GET /api/tasks/{id}`, `POST /api/tasks/{id}/cancel`,
`GET /api/workers`, `GET /api/endpoints`, `GET /api/leaderboard`, `GET /api/ledger`,
`GET /api/claims`, `GET /api/events` (SSE).

## §18 Claims verification: `sy prove`

Every public statement about Switchyard is a claim with an automated test. The README shows
only claims whose tests currently pass.

### §18.1 Claims registry: `claims/claims.yaml`
Each entry: `id`, `text` (exact public wording), `scope`, `kind`
(`unit | integration | e2e | bench | probe | platform`), `tests` (pytest node IDs),
`thresholds`, `evidence` (artifact paths), `phase`, `status` (`pending | passing | failing`).

### §18.2 Claim tests
- Live in `tests/claims/`, marked `@pytest.mark.claim("C-0xx")`.
- Each test writes an evidence artifact (JSON) to `claims/evidence/<id>.json` with inputs,
  measurements, thresholds, environment and date.

### §18.3 Integrity meta-tests (`tests/claims/test_integrity.py`)
1. Every claim has at least one test, and every claim test maps to a registered claim.
2. README and docs lint: text inside `<!-- claim:C-0xx -->` markers must match `claims.yaml`
   exactly. Unmarked superlatives or comparatives ("fastest", "best", "beats", "any model",
   "all models", "zero", "never") fail the lint.
3. Every number in the README comes from an evidence file via templating, never typed by hand.
4. The README claims section is generated by `sy prove --render-readme` and includes only
   claims whose latest status is `passing`.

### §18.4 `sy prove`
- `sy prove --quick`: CPU-only, fake workers and T0 models, under 10 minutes on a laptop.
- `sy prove --full`: includes bench-based and probe-matrix claims.
- `sy prove --claim C-0xx`: one claim.
- Prints a pass/fail table and writes `prove-report.json` and `prove-report.md`.
- CI runs `--quick` on Linux, macOS and Windows for every commit and `--full` weekly. A README
  badge shows "claims verified: N/N" from the latest CI report.

### §18.5 Initial claims (scope wording is part of the claim)

| ID | Claim text (public wording) | How it's proven | Phase |
|---|---|---|---|
| C-001 | Runs end to end on a CPU-only machine with 8 GB RAM. | `sy run` completes a toy task in a container capped at 8 GB RAM with no GPU visible. | P2 |
| C-002 | Switches models between steps inside a single task. | One task's trajectory contains ≥ 2 distinct endpoints across typed steps; every switch has a logged reason and cost estimate. | P2 |
| C-003 | Only switches models when the expected gain outweighs the cost of re-reading context. | Property test over randomized scenarios: switch iff gain − cost > margin; no switch otherwise. | P5 |
| C-004 | Speaks the chat-completions, responses, messages and generate-content API formats, plus engine-native formats. | Conformance tests against mock servers per format (streaming, tool calls, parallel tool calls, errors, usage); round-trip translation tests preserve messages and tool calls. | P1 |
| C-005 | Works with any open-weight model your installed engines can serve, including models without native tool calling. | A model with no native tool calling completes a tool-using task through the fallback protocol; probe suite passes on the CI model set (≥ 4 families); compatibility matrix generated from results. | P2–P3 |
| C-006 | In offline mode, makes no network connections except to local endpoints. | Full flow under a socket-level guard plus OS-level network isolation in CI; zero non-local connections observed; audit log matches observed connections exactly. | P9 |
| C-007 | Sends no telemetry. | Default-config flow with no remote endpoints configured opens zero outbound connections. | P1 |
| C-008 | The science gate catches numerical regressions beyond your tolerances. | Mutation test: injected perturbations above tolerance are all caught; perturbations below tolerance are not flagged. | P2 |
| C-009 | Changes to protected constants fail verification unless explicitly allowed. | Gate test with a modified protected value, with and without the allow flag. | P2 |
| C-010 | Handoff briefs validate against the HANDOFF v0.1 schema and stay under 1,500 tokens. | Schema validation and tokenizer count on every handoff produced during the bench suites. | P5 |
| C-011 | Uses fewer tokens than the unoptimized baseline at the same pass rate. | Bench evidence comparing P2 baseline vs optimized; claim text uses the measured percentage. | P5 |
| C-012 | Best-of-N across model families passes at least as many tasks as the best single worker. | Bench evidence on the mini suites with confidence intervals. | P6 |
| C-013 | Flags tasks where passing candidates disagree. | Seeded ambiguous tasks are flagged; unambiguous ones are not. | P6 |
| C-014 | Replays recorded runs deterministically. | Replay of a recorded fake-worker run reproduces identical diffs and gate results. | P7 |
| C-015 | The router learns which worker suits which task. | Simulation: cumulative regret below threshold and better than random and fixed policies. | P7 |
| C-016 | Runs on macOS, Linux and Windows. | `sy prove --quick` passes in CI on all three. | P1+ |
| C-017 | Goes from install to first completed task in under 5 minutes on a CPU-only machine. | Timed scripted quickstart in a clean container (model download time reported separately). | P10 |
| C-018 | Every number in this README is reproducible. | Integrity lint (§18.3) plus evidence files with commands. | P3+ |
| C-019 | Token accounting matches what model servers report. | Ledger totals equal server-reported usage within 1%. | P3 |
| C-020 | Stops at the budget you set. | Runs with tiny budgets stop with the correct status and no overrun beyond one step. | P2 |

## §19 CLI

```
sy init                       # create switchyard.toml, detect hardware, pick profile
sy doctor [--json]            # hardware + engines report
sy models list|add|pull|serve|stop [--tier cpu|t1|t2|t3] [--max-gb N]
sy probe <endpoint> [--export]
sy calibrate                  # measure throughput per endpoint
sy run "<task>" [--track general|science] [--best-of N] [--offline] [--budget-tokens N]
sy agent                      # interactive built-in agent with step-level routing
sy proxy [--port 4000]        # all-format proxy for external harnesses
sy bench run --suite <name> [--workers a,b] [--seeds N] ; sy bench report
sy dash                       # start API + dashboard
sy replay <run> --from-step K --worker X
sy prove [--quick|--full] [--claim ID] [--render-readme]
sy policy show|edit ; sy audit
```

## §20 Config files

- `switchyard.toml` (committed): tracks, policies, acceptance commands, budgets, science
  tolerances and protected zones.
- `.switchyard/local.toml` (gitignored): endpoints, keys, hardware overrides, model directory.
- `registry/*.toml` (in package, overridable per user): model and worker definitions.
- `claims/claims.yaml` (committed): claims registry.

## §21 Repo layout

```
src/switchyard/
  cli.py
  core/        task, step, track, policy, budget
  compat/      codecs/ (chat, responses, messages, generate_content, ollama, llamacpp, text),
               toolcall/ (parsers, fallback protocol), reasoning, probe, onboarding, gateway_bridge
  router/      rules, classifier, bandit, cost
  workers/     builtin_agent, claude_code, omp, opencode, kimi_code, acp
  serve/       manager, llamacpp, ollama, vllm, sglang, mlx, transformers, airllm, ktransformers
  proxy/       server, routing_hooks
  verify/      gates, impact, best_of_n, disagreement, review, science
  context/     repomap, search, distill, dedup, handoff
  trajectory/  record, replay
  bench/       runner, metrics, report
  prove/       runner, render_readme
  api/         app, routes, sse
  net.py       egress control + audit
  ledger.py
dashboard/              React + Vite
adapters/omp/           TypeScript extension
registry/               model and worker TOML
claims/                 claims.yaml, evidence/
bench/suites/ bench/repos/ bench/results/
science/glossaries/ science/templates/
spec/                   HANDOFF spec + schema
tests/                  unit/, integration/, claims/, conformance/ (mock servers per API format)
docs/ scripts/
install.sh install.ps1
```

## §22 Research list (Phase 0)

For each project record in RESEARCH.md: verified link, license, current version and date, what
we borrow, what we avoid, risks. Don't copy code without checking the license.

| Area | Projects to study |
|---|---|
| Big models on small hardware | AirLLM (layer and expert streaming), KTransformers (CPU-GPU hybrid MoE), llama.cpp (GGUF, MoE CPU offload, slots, grammars, speculative decoding), bitnet.cpp |
| Serving and engines | vLLM, SGLang (prefix caching, batching, tool parsers, metrics), Ollama, MLX-LM, transformers, ExLlamaV3, llama-swap (hot-swapping), LMCache (KV reuse), KV-cache quantization plugins |
| API formats and gateways | specifications of the chat-completions, responses, messages and generate-content formats; LiteLLM; claude-code-router; RouteLLM (learned routers) |
| Tool-call and template formats | chat templates and tool-call formats of every major open model family released to date, including the harmony format; engine-side tool parsers |
| Harnesses | Oh My Pi and Pi (model roles, hash-anchored edits, LSP tools, extensions), OpenCode and oh-my-openagent, Kimi Code, Claude Code (headless mode, `/goal`, hooks), mini-swe-agent and SWE-agent |
| Multi-agent orchestration | open-multi-agent (task DAG), Conductor (deterministic YAML routes), Claude Squad and similar worktree fleet runners, Agent Client Protocol |
| Context and tokens | Aider repo map, tree-sitter, LSP client libraries, LLMLingua, small-model "contract" prompting (e.g. simplicio, which claims large gains for tiny models) |
| Testing and verification | pytest-testmon, Hypothesis, jupytext, Playwright, network isolation options for CI on each OS |

## §23 Phases and acceptance criteria

`scripts/phase_check.py N` implements these checks; `--upto N` runs phases 0..N. A phase is also
only complete when every claim assigned to it (§18.5) is `passing` in `sy prove`.

- **P0 Research and design** (no product code): RESEARCH.md covers every §22 row with
  `verified: yes|no` and a date, including a table of API formats, engines and tool-call formats;
  ARCHITECTURE.md; ADRs for name, stack, compatibility layer, proxy and router; PROGRESS.md;
  `claims/claims.yaml` with C-001..C-020 as `pending`.
- **P1 Foundation and compatibility core**: package, CLI skeleton, config, registry,
  `sy doctor`, codecs for all §4.1 formats with mock-server conformance tests, llama.cpp and
  Ollama engine adapters, `sy models add`, `sy models pull --tier cpu` (≥ 3 T0 models),
  `sy probe`, server manager, `sy calibrate`, claims framework with integrity meta-tests and
  `sy prove` skeleton. Claims: C-004, C-007, C-016.
- **P2 Built-in agent, router v1, gates, tracks**: tools, fallback tool protocol, rules +
  classifier routing, step-level routing, escalation ladder, worktree pool, both tracks with
  gates, ledger, budgets. Claims: C-001, C-002, C-005 (fallback part), C-008, C-009, C-020.
- **P3 Bench, leaderboard, compatibility matrix**: suites `mini-general` and `mini-science`,
  matrix runner, bootstrap CIs, fake workers, probes on the CI model set, generated
  COMPATIBILITY.md, remaining engine adapters (vLLM, SGLang, MLX, transformers). Claims: C-005
  (matrix part), C-018, C-019.
- **P4 Dashboard and task API**: all §17 views and endpoints, SSE, Playwright smoke test,
  README claims section generated by `sy prove --render-readme`.
- **→ v0.1 launch candidate.** `sy prove --quick` passes on all three OSes.
- **P5 Token and cache optimization**: TK1–TK12, HANDOFF v0.1, cache-aware switching with
  calibration. Claims: C-003, C-010, C-011.
- **P6 Best-of-N, disagreement, cross-family review.** Claims: C-012, C-013.
- **P7 Learning router, fork/replay.** Claims: C-014, C-015.
- **P8 Proxy and external harness adapters**: contract tests with recorded fixtures per adapter;
  at least one live end-to-end run when the harness is installed.
- **P9 Slow lane and zero-egress**: AirLLM and KTransformers backends for asynchronous jobs;
  offline mode, audit, redaction. Claims: C-006.
- **P10 Launch polish**: README, docs site, demo GIF, examples, CONTRIBUTING, release workflow.
  Claims: C-017.

## §24 Launch and growth plan

- README, top to bottom: one-line pitch → demo GIF of a task where step badges visibly switch
  models (tiny → large → reviewer) with a live token counter → "claims verified: N/N" badge and
  "Don't trust us, run `sy prove`" → 60-second CPU-only quickstart → leaderboard image →
  "run it on your own repo" → compatibility matrix link → how it works → feature comparison
  table (features only) → roadmap.
- Quickstart must work CPU-only: `uv tool install switchyard && sy init && sy run "..."`.
- Contributor magnets: add a model with `sy models add` and submit its probe result; add a bench
  task in YAML; add an API codec or engine adapter against a small interface; labeled
  good-first issues; public roadmap issue for P5–P10.
- Releases: v0.1 after P4, then frequent minor releases while momentum lasts. Changelog kept.
- Launch posts: a "show" post on a major developer forum, local-LLM communities, a short video,
  and a separate post about the science track in research-software communities.
- Docs site (mkdocs-material). MIT license, code of conduct, security policy, issue templates.
- Packaging: uv/pipx install, `install.sh` / `install.ps1`, container images for model servers.

## §25 Non-goals and risks

- No claim of beating frontier models on the hardest tasks; claims are scoped to measured suites.
- "Every model" means every model a supported engine can serve. Brand-new architectures work as
  soon as an engine supports them; the matrix shows what's verified.
- The slow lane is slow. Asynchronous jobs only, never interactive.
- Model churn: the registry is data, updated via pull requests and `sy models add`.
- Licenses: respect model and code licenses; never redistribute weights.
- Scope risk: ship v0.1 after P4 even if later phases slip.
