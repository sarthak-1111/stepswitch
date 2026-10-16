# ADR 0021: The CI model set, probe results and what "passes the probe suite" means

- Status: accepted
- Date: 2026-10-16
- Phase: 3

## Context

Claim C-005's matrix part is proven by "probe suite passes on the CI model set (≥ 4 families);
compatibility matrix generated from results" (SPEC §18.5). SPEC §4.6 defines a battery of 20
checks, but most of them detect capabilities (parallel tool calls, a reasoning field, forced tool
choice, assistant prefill) that a model may lack while still working with Stepswitch: the router
reads them and adapts. "Passes" therefore needs a definition. The SPEC also leaves open where
probe results live, which models form the CI set, and what the committed results may contain.

## Decision

1. **Verdict.** A probe result passes the probe suite when it is a full probe (not `--quick`)
   and the checks the built-in agent depends on pass: `basic_chat`, `system_prompt` (agent
   instructions live there), `usage` (the ledger and budgets need it) and `formats_served`,
   plus one way to call tools: `native_tool_call` or `fallback_tool_protocol`. All other checks
   are shown in the matrix and do not decide the verdict. Streaming is not required, because
   the agent does not stream.
2. **Results live in `probes/`** at the repository root, one `ssw probe --export` file per
   model and engine (`<model>--<engine>.json`), plus `probes/ci-set.yaml`. The directory is not
   part of the source digest: results are outputs, like evidence. Anyone can add a result by
   pull request.
3. **CI model set:** `qwen3-4b-instruct-2507`, `phi-3.5-mini-instruct`,
   `smollm2-1.7b-instruct` and `granite-3.3-2b-instruct`: four families, all on the llama.cpp
   server on CPU, all under the Apache-2.0 or MIT license, with and without native tool
   calling. The last two have registry entries in no default tier; they are pulled by id.
4. **Commands.** `ssw probe --ci-set` starts a managed server per model, runs the full battery,
   writes the result and stops the server. `ssw probe --render-matrix` renders
   `docs/COMPATIBILITY.md` from every result in `probes/`; `--check` fails when the committed
   file differs.
5. **Quick and full mode** (ADR 0006 item 7). In quick mode the C-005 matrix test validates the
   committed results: each CI model needs a passing full probe of exactly the registry's model
   file (the result's model hash equals the registry sha256). In full mode it probes the set
   again first.
6. **No product names in results.** Exports keep only counts and sizes of the hardware (OS,
   architecture, CPU threads, RAM, GPU count, memory and backend); processor and GPU product
   names carry manufacturer names and are left out (ADR 0007).

## Consequences

- The matrix shows exactly the engine and model pairs that a probe ran on; nothing is
  extrapolated to other quantizations, engines or hardware.
- A model that fails a capability check can still pass, and the matrix says which capabilities
  it lacks.
- Results produced on a busy machine carry lower throughput figures; the verdict does not use
  them.
