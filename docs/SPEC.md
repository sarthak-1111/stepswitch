# Stepswitch: Product Spec (v0.3 draft)

Status: v0.3, 2026-10-16. Phase 0 research has been applied: items that were marked (verify)
are now verified and recorded in docs/RESEARCH.md, or replaced. Every change is listed below and
backed by an ADR in docs/decisions/ or by a dated research entry.

### Changes in v0.3 (Phase 0 research)

| Section | Change | Source |
|---|---|---|
| §0, all | Name was taken; renamed to Stepswitch (CLI `ssw`) at the start of Phase 1 | ADR 0001 |
| §21 | Build backend hatchling instead of `uv_build` (packages `registry/` and the dashboard) | ADR 0012 |
| §1 | Positioning corrected: per-turn routing and cache-aware switching already exist; novelty reworded | ADR 0010 |
| §3 | SQLite version and volume checks for WAL | ADR 0002 |
| §4.1 | Responses codec follows the open responses specification; generate-content marked legacy; interactions format is a watch item; lossless scope spelled out; gateway bridge isolated out of process | ADR 0003 |
| §4.2, §5.3 | Engine targets and platforms; TensorRT-LLM dropped; metrics optional; llama.cpp slots verified | ADR 0008 |
| §4.3-§4.6 | Template source recorded; parser chosen by hub ID and template hash; probe before trusting; seven effort levels plus token budgets; reasoning origin and retention | ADR 0003 |
| §5, §5.1, §5.2 | Model candidates verified and updated; commercial-use field; RAM budget for T0; doctor checks driver and SQLite | ADR 0009, ADR 0002 |
| §6, §8.2 | Smoke test instead of screenshot diff; notebook sync command; test-impact details | RESEARCH.md |
| §7.2 | Handoff-aware switch gain; no switch inside an open tool loop | ADR 0005 |
| §9 | Tokenizer resolution order; provenance records tokenizer and exactness | RESEARCH.md |
| §11 | Resume via the RAM prompt cache; slot files across restarts | ADR 0008 |
| §12 | Adapter details per harness; Pi and Oh My Pi split; ACP verified; harness egress switches | ADR 0004, RESEARCH.md |
| §13-§15 | Repo-map caching, compression scope, engine flags, offline-mode gaps | ADR 0008, ADR 0011, RESEARCH.md |
| §18 | Claims contracts; platform and isolation scopes | ADR 0006, ADR 0011 |
| §19-§22, §24, §25 | Proxy port, YAML library, dashboard packaging, stack pins, research notes, quickstart and docs tool, risks | ADR 0001, ADR 0002, ADR 0004 |

### Changes during Phase 2

| Section | Change | Source |
|---|---|---|
| §7.2, §7.3 | The built-in agent may switch inside its own tool loop; the escalation rung is a ceiling for all steps and a floor for heavy steps | ADR 0015 |
| §4.4 | Fallback protocol details: final-answer action, `tool_choice` mapping, history rewrite, default constraints, repairs, offline schema validation | ADR 0016 |
| §12 | `lsp` is a small built-in client instead of a pygls-based one | ADR 0017 |
| §13 (TK5) | Whole-file text only to create a file or replace one of at most 100 lines | ADR 0018 |

---

## §0 Name and pitch

- Name: **Stepswitch**. CLI: `ssw` (long form `stepswitch`). The kickoff's working name,
  Switchyard (CLI `sy`), was taken on the package index, used by an active same-domain project,
  and its CLI collided with a same-domain tool, so ADR 0001 renamed the project at the start of
  Phase 1.
- One-line pitch: "Routes every task, and every step inside it, to the best model and harness for
  the job, works with any open model or API, and proves every claim it makes." (Internal pitch;
  public wording must pass the claims lint, ADR 0006.)
- Headline feature: **step-level model switching inside a single task.** Planning, navigation,
  editing, test triage and review can each run on a different model, chosen at runtime, with the
  cost of switching taken into account.
- Second headline: **`ssw prove`.** Every public claim maps to an automated test that anyone can
  run on their own machine.

## §1 Positioning: what exists vs what is new

Already exists elsewhere (we integrate rather than compete): role-based model assignment in
harnesses, manual mid-session model switching, request-type routing proxies for coding agents,
multi-provider gateways, parallel agent fleets in git worktrees, deterministic YAML agent workflows,
per-turn routing inside agent sessions (including cache-aware switching that prices a cold prefix
cache and refuses to switch inside a tool loop), per-request router hooks inside harnesses,
per-request class hints from harnesses to gateways, category-based routing for delegated
subtasks, rule-based per-task routing with fallback chains, and per-task (harness, model)
selection. Prior art per item: docs/RESEARCH.md, "Positioning check" (ADR 0010).

What Stepswitch combines (each must be real, tested and demoable; public text says "combines",
never "first" or "only"):
1. Step-type-aware routing inside one task, driven by a classifier plus a policy that learns from
   the user's own verified outcomes, with every switch logged with its reason and cost.
2. Learned, verification-scored routing over (harness, model) pairs, per task and per step.
3. Universal compatibility: any open-weight model servable by a supported engine and any endpoint
   in a supported API format, including models with no native tool calling (§4).
4. Cache-aware switching (a feature here, not a novelty): a cost model that weighs re-reading
   context on the new model against the expected quality gain, using calibrated prefill
   throughput.
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
14. A claims registry and `ssw prove`: the README shows only claims whose tests currently pass (§18).

## §2 Core concepts

- **Task**: a unit of work with an instruction, a repo or worktree, a track and acceptance checks.
- **Step**: one agent turn within a task. Step types: `plan`, `navigate`, `read`, `edit`, `run`,
  `triage`, `review`, `summarize`, `write-text`.
- **Track**: `general` or `science`. Selects policies, gates, glossaries and defaults.
- **Role**: what a model is used for (router, summarizer, embedder, planner, editor, reviewer...).
- **Endpoint**: a reachable model, either a local engine server or a remote API, with a format.
- **Capability profile**: what an endpoint can actually do, measured by `ssw probe` (§4.6).
- **Worker**: a (harness, endpoint, settings) triple that can execute steps or whole tasks.
- **Tier**: a hardware class that determines which models are practical locally (§5).
- **Policy**: rules plus learned parameters mapping (task features, step type) to a worker.
- **Gate**: an ordered set of verification checks producing a Verdict.
- **Trajectory**: the recorded event stream of a run, which can be replayed or forked.
- **Ledger**: per-step accounting of tokens, wall time, GPU-seconds and cache hits.
- **Claim**: a public statement about Stepswitch with an ID, exact wording, scope and tests (§18).

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
SQLite file per project at `.stepswitch/state.db`. Large blobs (raw tool outputs, diffs) live in
`.stepswitch/blobs/`, addressed by content hash. The database uses WAL mode, which needs SQLite
3.51.3 or later (earlier versions have a WAL-reset corruption bug) and a local volume: `ssw doctor`
checks both, and on network drives the database falls back to `journal_mode=DELETE` (ADR 0002).

## §4 Compatibility layer: every open model, every API format

Goal: any open-weight model that a supported engine can serve, and any endpoint that speaks a
supported API format, works as a Stepswitch worker. Nothing in the core talks to a model except
through this layer.

### §4.1 API formats (inbound proxy and outbound clients)
Implement a codec per format with a shared internal representation (messages, tool calls, tool
results, reasoning, usage, streaming deltas, errors):
- chat-completions format (the de facto standard for open-model servers and most gateways)
- responses format, following the open, vendor-neutral responses specification (its compliance
  tests run against the codec and the proxy); hosted-only items are extensions, and stateful
  features (stored responses, previous-response chaining) are proxy-side state (§12)
- messages format
- generate-content format (its hosted reference now labels it legacy but fully supported)
- engine-native formats: Ollama native API; llama.cpp server native endpoints (`/completion`,
  `/infill`, `/tokenize`, `/detokenize`, `/apply-template`, `/props`, `/slots`)
- raw text-completion endpoints (no chat template applied server-side; Ollama needs `raw: true`)
- Watch item: the newer "interactions" format of the generate-content family (codec deferred
  until its version path settles; outside the C-004 wording until then).
- Long tail: an optional bridge to a multi-provider gateway library (study LiteLLM): base package
  only, own environment, out of process, hash-locked, pinned to a release at least seven days
  old, local cost map, off by default (ADR 0003; the library had compromised releases in 2026).

Local engines now serve several of these formats themselves (llama.cpp: chat-completions,
responses, messages; Ollama, vLLM and SGLang also responses and messages, with gaps).
Conformance tests run against each engine in each format it serves; gaps go into the
compatibility matrix.

Every codec supports streaming and non-streaming, tool calls (single and parallel), structured
output where the endpoint offers it, usage reporting, and error mapping with retries and backoff.
Conversion between any two formats is lossless for messages (text, images and files by
reference), function tool definitions, tool calls and tool results, with tool-call IDs
synthesized where a server omits them. Anything that can't be represented (opaque or signed
reasoning across models, built-in server tools, citations, cache-control hints, n>1 candidates,
logprobs, provider-held state) is reported, never silently dropped.

### §4.2 Engines (local model plane)
Engine adapters share one interface (§5.3). Supported targets, verified in Phase 0 (ADR 0008):
llama.cpp server (GGUF; widest architecture coverage, CPU-first; native on Windows, macOS and
Linux), Ollama (self-managing; `num_ctx` always set), vLLM and SGLang (Linux; on Windows through
WSL2 or a container), MLX-LM (macOS on ARM; no constrained decoding), a `transformers serve`
process as the universal slow fallback (always with the fallback tool protocol), AirLLM (slow
lane, behind an out-of-process HTTP shim) and KTransformers (slow lane, Linux x86-64, as an
SGLang profile). Optional: ExLlamaV3 through its separate server process (pinned by commit, never
vendored). TensorRT-LLM is dropped as an adapter (Linux only; our reference GPUs are not on its
supported list); a user-run server stays reachable through the chat-completions codec.

### §4.3 Model onboarding: `ssw models add <hub-id | local path | endpoint URL>`
1. Read metadata: architecture, parameter count (total and active), context length, license and
   commercial-use conditions, chat template and its source (Jinja template, reference encoder
   script, or an engine tokenizer mode; some families ship no Jinja template), tokenizer,
   available quantizations.
2. Pick an engine and quant that fit the detected hardware (§5.1), or register the endpoint as-is.
3. Select the tool-call parser by hub ID plus chat-template hash and confirm it with probe
   results (§4.4). Engine recipes record parser flags, plugins, forks and the minimum engine
   version.
4. Run `ssw probe` (§4.6) and store the capability profile.
5. Write a registry entry. No hardcoded model list is needed for a model to work.

### §4.4 Tool-calling compatibility
- Native: parsers from a catalog of the tool-call syntaxes in use today (harmony and
  harmony-like recipient formats, JSON-in-tags, XML-style function/parameter tags including
  namespaced variants, key/value-tag pairs, special-token sections and control-token markup,
  bracketed name/arguments markers, pythonic call lists and non-JSON argument syntaxes;
  docs/RESEARCH.md, "Tool-call formats"). Syntax can change between
  versions of one family, so the parser is chosen per model version; templates not in the
  catalog fall back to detection by template markers, confirmed by the probe. Server-side
  parsing is used only after `ssw probe` passes single, parallel, streamed and schema-valid calls
  for that (engine, parser, model); otherwise parse raw text.
- Fallback text protocol: for models with no native tool calling, describe tools in the prompt
  and force a JSON action object with grammar or JSON-Schema constrained decoding (llama.cpp
  `json_schema`/grammars, structured `response_format` on GPU servers, Ollama `format`) or
  validate-and-repair where constraints are unavailable. For reasoning models the constraint
  starts after the reasoning block. Never rely on `tool_choice` forcing or assistant prefill
  unless the probe confirmed them. This is what makes "any text-generating model can drive the
  agent" true.

### §4.5 Reasoning normalization
Map reasoning output (dedicated fields under any of their current names, think tags, harmony
channels) into one internal field that also keeps opaque or signed reasoning payloads with their
origin; those are replayed only to the model that produced them. Map effort controls to
`effort: none | minimal | low | medium | high | xhigh | max` per endpoint, plus an optional
`budget_tokens`: each profile stores its supported subset, its mechanism (request field, thinking
budget, template argument, numeric scale, mode switch), its default and whether thinking can be
switched off at all; unsupported levels are clamped and reported. A per-model retention policy
(`drop | keep-since-last-user | keep-for-tool-turns | keep-all`) governs same-model
continuations, and template retention switches are passed explicitly. Reasoning text is never
passed to a different model during a switch; only HANDOFF content is (§9).

### §4.6 Capability probe: `ssw probe <endpoint>`
A short battery, under 3 minutes on a T0 model:
basic chat · system prompt adherence · native tool call · parallel tool calls · fallback tool
protocol · JSON Schema adherence · streaming · usage reporting · reasoning field detection ·
context length check (needle test at configured lengths) · prefix cache behavior (repeat-prefix
latency drop) · prefill and decode throughput · tool-call IDs · forced tool choice · assistant
prefill · streamed tool arguments · structured output under reasoning · reasoning retention ·
usage semantics (whether cached tokens are included) · formats served by the endpoint.
Output: a capability profile (JSON) stored in SQLite and used by the router.

### §4.7 Compatibility matrix
- `docs/COMPATIBILITY.md` is generated from probe results, never hand-written.
- CI runs probes on a small set of tiny models from different families on every release and on
  a weekly schedule, plus mock-server conformance tests for every API format on every commit.
- Users can submit probe results for their own models via pull request (`ssw probe --export`
  produces a result file with model hash, engine version, hardware and date).

### §4.8 Claim scope
The public compatibility claim is worded as: "Works with any open-weight model your installed
engines can serve, and any endpoint in a supported API format." The matrix shows exactly what has
been verified. The claims system (§18) enforces this wording.

## §5 Hardware tiers and default models

Registry entries are data (`registry/*.toml`): hub ID, quant, file size, RAM/VRAM need, context
limit, tool-calling mode, roles, license, `commercial_use` (`permitted | conditional |
prohibited`), template source, engine recipes, date verified. The model IDs below were checked
in Phase 0 (ADR 0009; docs/RESEARCH.md, "Default model candidates") and are re-verified whenever
the registry changes; any other model can be added via §4.3. Models whose licenses carry
commercial, revenue or user-count conditions are flagged and never pulled without an explicit
confirmation.

| Tier | Hardware | Candidate defaults | Typical roles |
|---|---|---|---|
| T0 | CPU only, 8–16 GB RAM | Qwen3-4B-Instruct-2507 (router, summarizer), Qwen3-Embedding-0.6B (embeddings), Qwen2.5-Coder-1.5B (FIM, optional); alternates Qwen3.5 4B / 2B / 0.8B, Gemma 4 E2B / E4B | router/classifier, summarizer, log triage, commit messages, search ranking, handoff writer, embeddings |
| T1 | one GPU, 12–24 GB | Qwen3.8-27B (4-bit needs 24 GB; smaller cards need 3-bit or offload), Devstral-Small-2-24B-Instruct-2512, Qwen3.6-35B-A3B with expert offload, gpt-oss-20b, Gemma 4 26B-A4B | editor for small repos, reviewer, budget planner |
| T2 | workstation, 96–192 GB | Qwen3.8-Flash-Next (~111 GB Q4 GGUF; community license), gpt-oss-120b, GLM-5.3-Flash (192 GB at IQ4_XS only), MiMo-V2.6-Flash-MOPD, Mistral-Small-4-119B | editor, planner, reviewer |
| T3 | multi-GPU nodes | GLM-5.3, Kimi K3, MiMo-V2.6-Pro-MOPD, DeepSeek V4.1 Flash (vLLM and SGLang only for now) | hardest edits, planning, greenfield builds |
| SL | slow lane; requirements per engine (ADR 0008) | T3 MoE models via CPU-GPU hybrid MoE (KTransformers, llama.cpp expert offload) or layer streaming (AirLLM) | asynchronous second opinions, overnight review, best-of-N candidates |
| R | remote endpoints | any endpoint in a supported API format | opt-in escalation; disabled in zero-egress mode |

Defaults by role (overridable in policy):
- Classify, route, summarize tool output, triage logs, write commit messages: T0.
- Search ranking: T0 embeddings + ripgrep.
- Plan: best available endpoint.
- Edit an existing large repo: GLM-class model when available.
- Greenfield and frontend builds: K3-class model when available.
- Review: a model from a different family than the author of the patch.
- Science-track edits: largest available endpoint. T0 never edits code in the science track.

### §5.1 `ssw doctor`
Detects OS, CPU (cores, AVX2/AVX-512/AMX, ARM features), RAM, GPUs (vendor, VRAM, compute
capability including FP8/FP4 support, driver version), disk space, installed engines and their
versions, and the SQLite library version (WAL needs 3.51.3 or later, ADR 0002). Prints a
recommended profile and writes it to `.stepswitch/local.toml`. `--json` for machine-readable
output.

### §5.2 `ssw models pull --tier cpu`
Installs the default T0 set (ADR 0009): one router and summarizer model (one server, two slots),
one embedding model, optionally a FIM code model. Uses llama.cpp server (preferred) or Ollama if
present (pulling `hf.co/...` names when the original source is required). Respects a disk
footprint cap (default 8 GB, `--max-gb`) and a RAM budget (per-role context sizes, lazy start,
LRU eviction; the default set needs about 5.7 GB resident), a configurable model directory, and
asks before downloading. Never redistributes weights; downloads from the original source per
license.

### §5.3 Server manager and engine interface
`start(model) / stop / health`, plus optional `metrics / prefix_cache_stats` (where an engine has
no metric, cache statistics come from per-response usage and timing fields) and
`slot_save / slot_restore` (llama.cpp server only, verified; other engines may offer an optional
`kv_offload` capability). Lazy start on first request, keep-alive TTL, LRU eviction when memory
is tight, health checks, hot-swap (llama.cpp router mode first, then per-process swaps; llama-swap
studied, not required). The manager owns memory accounting and does not double-manage engines
that load and unload models themselves (Ollama). Engine recipes carry exact flags, parser names,
plugins, forks and the minimum engine version per model (ADR 0008).

## §6 Tracks

### §6.1 General track
Web apps, APIs, CLIs, scripts. Gates: format → lint → typecheck → impact tests → full tests.
Frontend tasks optionally add a build check and a headless Playwright smoke test that compares
accessibility (ARIA) snapshots, with screenshots captured on failure; a pixel diff, if wanted, is
a separate step. API tasks add contract tests when an OpenAPI schema exists.

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
- **Notebooks**: pair `.ipynb` with py:percent via jupytext (global `jupytext.toml` with
  `formats = "ipynb,py:percent"`); agents edit the paired script, and after each edit the gate
  runs `jupytext --to ipynb --update <script>.py`, which keeps stored outputs. `--sync` is not
  used, since it picks the source by timestamp.
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

- `prefill_tps` and `decode_tps` come from the probe or `ssw calibrate`.
- `uncached_tokens(m)` comes from a per-server prefix registry (hash of the prefix last sent to
  that server) and engine prefix-cache metrics where available.
- Above a threshold (default 24k uncached tokens), send a HANDOFF brief (§9) instead of the
  transcript.
- Switching back to a model that saw an earlier prefix reuses its warm cache for that prefix.
- For a mid-trajectory switch, `p_success(m)` is estimated given the handoff (a learned handoff
  penalty on top of the fresh-start rate), since published results show that handing a cheaper
  model's trajectory to a stronger one recovers less than half of the quality gap (ADR 0005).
- No switch while a tool-call loop is open, or while the next turn depends on state held by the
  current endpoint (signed or encrypted reasoning, stored responses).

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
Run tests affected by changed files first: `pytest --testmon` (verified with pytest 9) with one
data file per worktree (`TESTMON_DATAFILE`, seeded from a baseline when the pool is created).
testmon does not track non-Python files, so any change to data, configs, `science.toml`,
`tests/golden/**` or the lockfile forces the full run. Its cloud storage mode (`--tmnet`) is
never used (§15). The full suite runs only at the final gate.

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

Tokenizer resolution order (`context/tokens`): the model's local `tokenizer.json` from the
registry, then the serving llama.cpp server's `/tokenize`, then a mapped BPE encoding from a
cache prefetched at install time, then a flagged estimate. Some servers (Ollama) offer no
tokenize route. The brief is counted as rendered and sent, and `provenance` records which
tokenizer was used and whether the count is exact. Nothing is downloaded at runtime unless
configured.

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
  "provenance": {"from_worker": "string", "step": 0, "tokenizer": "string", "exact": true}
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
- `ssw replay <run> --from-step k --worker X` forks a new run from step k with a different worker.
- Deterministic replay where possible (temperature 0, fixed seeds, recorded tool outputs).
- Same-model resume: the llama.cpp server keeps a host-RAM prompt cache (`--cache-ram`) that
  restores matching prefixes automatically; slot files (`/slots/{id}?action=save|restore`, under
  `--slot-save-path`, verified) are used only across server restarts or model reloads. Other
  engines resume from the prefix cache where they have one.

## §12 Workers and harness adapters

Interface: `start(task, worktree, endpoint, settings) -> RunHandle`, `events()`, `cancel()`,
`result() -> RunResult`.

1. **Built-in lean agent** (`ssw agent`), the default worker. Tools: `read(path, range)`, `grep`,
   `repo_map`, `lsp(defs|refs|hover|diagnostics)`, `edit(hash-anchored)`, `run(cmd)`,
   `test(selector)`, `search(semantic)`. Step-level routing lives here. `lsp` is a small built-in
   client (ADR 0017) for user-installed language servers whose launch commands live in
   registry TOML; servers are never auto-downloaded. The edit format is Stepswitch's own,
   specified under `spec/` (prior art: the hash-anchored line edits of Oh My Pi, MIT).
2. **Proxy** (`ssw proxy`): a local endpoint that accepts every supported API format (§4.1) and
   forwards to any endpoint in any format. External harnesses point at it, and the router picks
   a model per request. This is how mid-session switching works inside third-party harnesses.
   Switches happen at a new user turn, never inside an open tool-call loop (§7.2, ADR 0004).
3. **Claude Code adapter**: headless runs pointed at the proxy through the harness's base-URL
   and token variables, model slots mapped by the router. The harness speaks only the messages
   format and needs exact streaming behavior (full event sequence, keep-alive pings). With
   `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` it tells the proxy each request's class (main, subagent,
   compaction, ...) for step-level routing; `CLAUDE_CODE_MAX_CONTEXT_TOKENS` is set from the
   routed model's context. Its documentation says routing it to other models through a gateway
   is not supported, so the adapter is documented as "works through `ssw proxy`; not supported
   upstream".
4. **Pi adapter**: TypeScript extension registering Stepswitch as a provider plus a virtual
   model whose `route()` calls the router (in-process step-level routing).
5. **Oh My Pi adapter**: provider plus generated `modelRoles` and a `before_subagent_spawn` hook;
   mid-turn switching goes through `ssw proxy`. Pi and Oh My Pi have diverged (separate packages,
   runtimes and config files), so they are two thin adapters on a shared core.
6. **OpenCode and Kimi Code adapters**: both are ACP agents and are driven through the ACP client
   (item 7) where available. Generated config only points their model endpoints at `ssw proxy`
   and is passed through environment variables or an isolated config directory, never by
   editing user files. Kimi Code means the current TypeScript CLI; the older Python packages are
   archived.
7. **Agent Client Protocol client**: generic adapter for any ACP-capable agent. ACP v1 (schema
   1.25.x) over stdio via the `agent-client-protocol` SDK, stable methods only (`initialize`,
   `session/new`, `session/prompt`, `session/update`, `session/cancel`, client-side
   `session/request_permission`, optional `fs/*` and `terminal/*`), feature-detected at
   `initialize`. Model selection uses `session/set_config_option` when the agent offers a model
   option, otherwise `ssw proxy`. v2 (alpha) is tracked.

In offline mode every adapter sets its harness's egress switches (auto-update, model-list
fetch, telemetry, version checks), and the audit log lists any residual calls (§15).
oh-my-openagent is studied for ideas only (source-available license with use limits, telemetry
on by default): no code reuse and no adapter.

## §13 Token optimization

| ID | Technique | Detail |
|---|---|---|
| TK1 | Stable prefix layout | `[system][tools][repo map][glossary]` byte-identical across steps; volatile content last. |
| TK2 | Repo map | tree-sitter def/ref graph ranked by personalized PageRank, token-budgeted (default 2k); tags cached per file content hash (worktrees hold uncommitted edits); core grammars from pinned per-language wheels (offline), the language pack optional and prefetched. |
| TK3 | Read dedup | content-hash cache; re-reading an unchanged file returns a short stub. |
| TK4 | Tool-output distillation | outputs > 2k tokens summarized by T0, keeping error lines, file:line refs and numbers verbatim; raw output stored and retrievable. |
| TK5 | Diff-only edits | hash-anchored or unified-diff edits; whole-file text only to create a file or replace one of at most 100 lines (ADR 0018). |
| TK6 | Context pruning | drop superseded tool results; rolling structured summary at 60% of the budget. |
| TK7 | Handoff briefs | HANDOFF v0.1 on model switches (§9). |
| TK8 | Constrained decoding | JSON Schema or grammar constraints for classifier, router, reviewer, handoff, fallback tool protocol. |
| TK9 | Hybrid search | T0 embeddings + ripgrep to read fewer files. |
| TK10 | Per-role budgets | max tokens and reasoning effort per role (low for navigation, high for planning). |
| TK11 | Prompt compression | optional extra, docs only, never code: the LLMLingua-2 small checkpoint only (the default LLMLingua model is a 7B GPU model), local model path, off by default, enabled only if the bench shows a gain. |
| TK12 | Token ledger | per task, step, model and day, in SQLite and the dashboard. |

## §14 Run optimization

| ID | Technique |
|---|---|
| RN1 | Server manager with lazy start, TTL, LRU eviction, hot-swap (§5.3). |
| RN2 | Continuous batching for parallel agents on vLLM/SGLang; concurrency per server from free KV memory. |
| RN3 | Speculative decoding per engine: n-gram methods (no extra model memory; the CPU-only option), model-shipped MTP heads, or a T0 draft model (llama.cpp `--spec-type`, `--spec-draft-model`; vLLM `--speculative-config`; SGLang `--speculative-algorithm`). `ssw calibrate` reads acceptance counters. |
| RN4 | KV cache quantization through one registry key, `kv_cache_dtype`, mapped to each engine's flag (llama.cpp `-ctk`/`-ctv`, vLLM and SGLang `--kv-cache-dtype`, Ollama `OLLAMA_KV_CACHE_TYPE`); off by default. FP8 KV on GPUs without native FP8 needs specific attention backends. |
| RN5 | MoE expert offload to system RAM for T1/T2 boxes (verified flags): llama.cpp `--n-cpu-moe N`, `--cpu-moe`, `--override-tensor`, `--moe-cache-mib`; KTransformers through SGLang `--kt-method`, `--kt-num-gpu-experts`, `--kt-cpuinfer`, `--kt-threadpool-count`. |
| RN6 | Test impact analysis first, full suite last (§8.2). |
| RN7 | Cheapest gates first (§8.1). |
| RN8 | Pre-created worktree pool; shared dependency caches (uv cache, pnpm store). |
| RN9 | Slow-lane queue for asynchronous jobs; results arrive as review comments. |
| RN10 | Timeouts, budgets, kill switches; stuck-loop detection (no progress for N steps). |
| RN11 | `ssw calibrate`: measure throughput per endpoint for the cost model. |

## §15 Privacy and zero-egress

- `--offline` or `privacy.mode = "local"` blocks every non-local connection at `stepswitch.net`.
  "Local" means 127.0.0.0/8, ::1 and Unix sockets (ADR 0011).
- Egress can also happen behind a localhost URL or outside `stepswitch.net`, so local mode also:
  rejects engine models that forward to a hosted service (Ollama cloud-backed model names; or
  require the engine's no-cloud switch) and llama-swap configs with remote `peers`; forbids lazy
  downloads by libraries used in context services (parser bundles, BPE files, language servers,
  hub tokenizers and models), which are fetched by an install-time prefetch step into caches
  under `.stepswitch/`; sets each harness's egress switches (§12); and disables the gateway
  bridge (§4.1) unless it targets a local endpoint. Context services are tested with networking
  disabled.
- Egress audit log: every connection with host, bytes, purpose and task ID; visible in the
  dashboard and via `ssw audit`. Spawned processes are logged too, since in-process observation
  cannot see their connections; OS-level isolation is the enforcement layer on Linux.
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
claims view (latest `ssw prove` report).

Task API: `POST /api/tasks`, `GET /api/tasks/{id}`, `POST /api/tasks/{id}/cancel`,
`GET /api/workers`, `GET /api/endpoints`, `GET /api/leaderboard`, `GET /api/ledger`,
`GET /api/claims`, `GET /api/events` (SSE).

## §18 Claims verification: `ssw prove`

Every public statement about Stepswitch is a claim with an automated test. The README shows
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
4. The README claims section is generated by `ssw prove --render-readme` and includes only
   claims whose latest status is `passing`.

### §18.4 `ssw prove`
- `ssw prove --quick`: CPU-only, fake workers and T0 models, under 10 minutes on a laptop. For
  bench and probe-matrix claims it validates the committed evidence (schema, thresholds, the
  digest of the configuration that produced it, the recorded command) instead of re-running the
  bench (ADR 0006).
- `ssw prove --full`: includes bench-based and probe-matrix claims and rewrites their evidence.
- `ssw prove --claim C-0xx`: one claim.
- Prints a pass/fail table and writes `prove-report.json` and `prove-report.md`. The JSON report
  carries a digest of the sources it ran against; phase checks accept only a report whose digest
  matches the working tree.
- CI runs `--quick` on pinned runner labels for Linux x86-64, macOS arm64 and Windows x86-64 for
  every commit and `--full` weekly; the report records the runner label and image version
  (ADR 0011). A README badge shows "claims verified: N/N" from the latest CI report.

### §18.5 Initial claims (scope wording is part of the claim)

| ID | Claim text (public wording) | How it's proven | Phase |
|---|---|---|---|
| C-001 | Runs end to end on a CPU-only machine with 8 GB RAM. | `ssw run` completes a toy task in a container capped at 8 GB RAM with no GPU visible. | P2 |
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
| C-016 | Runs on macOS, Linux and Windows. | `ssw prove --quick` passes in CI on all three. | P1+ |
| C-017 | Goes from install to first completed task in under 5 minutes on a CPU-only machine. | Timed scripted quickstart in a clean container (model download time reported separately). | P10 |
| C-018 | Every number in this README is reproducible. | Integrity lint (§18.3) plus evidence files with commands. | P3+ |
| C-019 | Token accounting matches what model servers report. | Ledger totals equal server-reported usage within 1%. | P3 |
| C-020 | Stops at the budget you set. | Runs with tiny budgets stop with the correct status and no overrun beyond one step. | P2 |

## §19 CLI

The CLI is `ssw`, with `stepswitch` as a long alias (ADR 0001). The proxy's default port is
7778, next to the dashboard's 7777, since 4000 is a common default of other local gateways
(ADR 0004).

```
ssw init                       # create stepswitch.toml, detect hardware, pick profile
ssw doctor [--json]            # hardware + engines report
ssw models list|add|pull|serve|stop [--tier cpu|t1|t2|t3] [--max-gb N]
ssw probe <endpoint> [--export]
ssw calibrate                  # measure throughput per endpoint
ssw run "<task>" [--track general|science] [--best-of N] [--offline] [--budget-tokens N]
ssw agent                      # interactive built-in agent with step-level routing
ssw proxy [--port 7778]        # all-format proxy for external harnesses
ssw bench run --suite <name> [--workers a,b] [--seeds N] ; ssw bench report
ssw dash                       # start API + dashboard
ssw replay <run> --from-step K --worker X
ssw prove [--quick|--full] [--claim ID] [--render-readme]
ssw policy show|edit ; ssw audit
```

## §20 Config files

- `stepswitch.toml` (committed): tracks, policies, acceptance commands, budgets, science
  tolerances and protected zones.
- `.stepswitch/local.toml` (gitignored): endpoints, keys, hardware overrides, model directory.
  The rest of `.stepswitch/` (state database, blobs, prefetched caches) is gitignored as well.
- `registry/*.toml` (in package, overridable per user): model and worker definitions.
- `claims/claims.yaml` (committed): claims registry. Tools read and rewrite it with ruamel.yaml
  in round-trip mode, which keeps comments (PyYAML drops them).
- Precedence: CLI flags > environment > `.stepswitch/local.toml` > `stepswitch.toml` > defaults.

## §21 Repo layout

```
src/stepswitch/
  cli.py
  core/        task, step, track, policy, budget
  compat/      codecs/ (chat, responses, messages, generate_content, ollama, llamacpp, text),
               toolcall/ (parsers, fallback protocol), reasoning, probe, onboarding, gateway_bridge
  router/      rules, classifier, bandit, cost
  workers/     builtin_agent, claude_code, pi, omp, opencode, kimi_code, acp
  serve/       manager, llamacpp, ollama, vllm, sglang, mlx, transformers, airllm, ktransformers
  proxy/       server, routing_hooks
  verify/      gates, impact, best_of_n, disagreement, review, science
  context/     repomap, search, distill, dedup, handoff, tokens
  trajectory/  record, replay
  bench/       runner, metrics, report
  prove/       runner, render_readme
  api/         app, routes, sse, static/ (built dashboard)
  net.py       egress control + audit
  ledger.py
dashboard/              React + Vite (Node >= 22.12), built into src/stepswitch/api/static/
adapters/pi/ adapters/omp/   TypeScript extensions on a shared core
registry/               model and worker TOML
claims/                 claims.yaml, evidence/
bench/suites/ bench/repos/ bench/results/
science/glossaries/ science/templates/
spec/                   HANDOFF spec + schema, edit format
tests/                  unit/, integration/, claims/, conformance/ (mock servers per API format;
                        acp/ holds a mock ACP agent)
docs/ scripts/
install.sh install.ps1
```

The dashboard is built in CI before `uv build`, since no Python build backend runs Node; the
hatchling backend maps `dashboard/dist/` and `registry/` into the package (ADR 0012). TypeScript
code that imports the compiler API pins TypeScript 6, because TypeScript 7 has no compiler API
yet (ADR 0002).

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

Phase 0 results are in docs/RESEARCH.md. Notes on the rows above: model roles, hash-anchored
edits and the LSP tool are Oh My Pi features (Pi ships a smaller core and has moved to a new
repository and package name); open-multi-agent has a new canonical repository and package;
"Conductor" names several projects, and the one studied is the YAML workflow CLI; simplicio's
earlier tiny-model gains were regex-scored and not reproduced; oh-my-openagent is ideas-only
(license with use limits). Default model candidates (§5) and the name check (§0) were added as
extra rows.

## §23 Phases and acceptance criteria

`scripts/phase_check.py N` implements these checks; `--upto N` runs phases 0..N. A phase is also
only complete when every claim assigned to it (§18.5) is `passing` in `ssw prove`.

- **P0 Research and design** (no product code): RESEARCH.md covers every §22 row with
  `verified: yes|no` and a date, including a table of API formats, engines and tool-call formats;
  ARCHITECTURE.md; ADRs for name, stack, compatibility layer, proxy and router; PROGRESS.md;
  `claims/claims.yaml` with C-001..C-020 as `pending`.
- **P1 Foundation and compatibility core**: package, CLI skeleton, config, registry,
  `ssw doctor`, codecs for all §4.1 formats with mock-server conformance tests, llama.cpp and
  Ollama engine adapters, `ssw models add`, `ssw models pull --tier cpu` (≥ 3 T0 models),
  `ssw probe`, server manager, `ssw calibrate`, claims framework with integrity meta-tests and
  `ssw prove` skeleton. Claims: C-004, C-007, C-016.
- **P2 Built-in agent, router v1, gates, tracks**: tools, fallback tool protocol, rules +
  classifier routing, step-level routing, escalation ladder, worktree pool, both tracks with
  gates, ledger, budgets. Claims: C-001, C-002, C-005 (fallback part), C-008, C-009, C-020.
- **P3 Bench, leaderboard, compatibility matrix**: suites `mini-general` and `mini-science`,
  matrix runner, bootstrap CIs, fake workers, probes on the CI model set, generated
  COMPATIBILITY.md, remaining engine adapters (vLLM, SGLang, MLX, transformers). Claims: C-005
  (matrix part), C-018, C-019.
- **P4 Dashboard and task API**: all §17 views and endpoints, SSE, Playwright smoke test,
  README claims section generated by `ssw prove --render-readme`.
- **→ v0.1 launch candidate.** `ssw prove --quick` passes on all three OSes.
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
  "Don't trust us, run `ssw prove`" → 60-second CPU-only quickstart → leaderboard image →
  "run it on your own repo" → compatibility matrix link → how it works → feature comparison
  table (features only) → roadmap.
- Quickstart must work CPU-only: `uv tool install stepswitch && ssw init && ssw run "..."`
  (the kickoff's command used the working name, whose package-index entry belongs to an
  unrelated project; ADR 0001). Current uv downloads Python 3.15 by
  default, so every runtime dependency must install on 3.15 (pure-Python or cp315 wheels), and
  CI tests 3.15.
- Contributor magnets: add a model with `ssw models add` and submit its probe result; add a bench
  task in YAML; add an API codec or engine adapter against a small interface; labeled
  good-first issues; public roadmap issue for P5–P10.
- Releases: v0.1 after P4, then frequent minor releases while momentum lasts. Changelog kept.
- Launch posts: a "show" post on a major developer forum, local-LLM communities, a short video,
  and a separate post about the science track in research-software communities.
- Docs site: tool chosen by an ADR in Phase 10 (mkdocs-material is in maintenance mode, MkDocs
  2.0 is incompatible with it, and its successor is in alpha). MIT license, code of conduct,
  security policy, issue templates.
- Packaging: uv/pipx install, `install.sh` / `install.ps1`, container images for model servers.

## §25 Non-goals and risks

- No claim of beating frontier models on the hardest tasks; claims are scoped to measured suites.
- "Every model" means every model a supported engine can serve. Brand-new architectures work as
  soon as an engine supports them; the matrix shows what's verified.
- The slow lane is slow. Asynchronous jobs only, never interactive.
- Model churn: the registry is data, updated via pull requests and `ssw models add`.
- Licenses: respect model and code licenses; never redistribute weights. Model licenses with
  commercial conditions are flagged in the registry (§5).
- Supply chain: dependencies are pinned and hash-locked in `uv.lock`; optional bridges with a
  history of compromised releases run out of process with a release cooldown (ADR 0003).
- Scope risk: ship v0.1 after P4 even if later phases slip.
