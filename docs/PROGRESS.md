# Progress

Newest phase first. Each phase records what was done, what research changed, open issues and
actions that only the maintainer can take. Phase checks: `uv run python scripts/phase_check.py N`
(one phase) or `--upto N` (phases 0 to N).

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

## Next: Phase 2

The built-in agent with step-level routing (rules, classifier, priors, cost model, escalation
ladder, budgets), worktrees, the agent's tools and context services, verification gates and
the science track. Claims due: C-001, C-002, C-005 (fallback part), C-008, C-009, C-020.
