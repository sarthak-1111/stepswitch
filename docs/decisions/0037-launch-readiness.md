# ADR 0037: Launch readiness: integrity fixes, fewer failed calls, and one confirmatory run

- Status: accepted
- Date: 2026-10-20
- Phase: 12

## Context

Phase 11 compared Stepswitch on local models with Claude Code on Sonnet 5.5 and on Opus 5.5
(ADR 0034). Every contestant passed every task run. Stepswitch processed fewer tokens, but it
used more uncached tokens than Claude Code on Sonnet 5.5 (ADR 0036), and its local runs took
four to five times as long.

The step logs of the router's 42 task runs show where its uncached tokens and time went:

- **Not the cache.** Its fresh input (294k tokens) was within 5% of Claude Code's on Sonnet 5.5
  (281k). Its output was more than twice as large (107k against 47k), over 12 model calls per
  task against 5 turns.
- **Failed edits.** 77 of its 151 edit calls were refused, almost all for their shape:
  - a patch in the `*** Begin Patch` format;
  - old and new text instead of anchors;
  - an anchor without its line number or its hash;
  - an empty `text` beside `edits`, or no `op`.

  Each refusal cost a model call.
- **Planning.** The plan step runs at high effort. It produced 35% of the output tokens and took
  about 20 of the 87 seconds of a median task.
- **Model time.** 89% of a task's wall time was spent in model calls.

The logs also showed three integrity problems:

1. **A dead model.** From 2026-10-19 on, qwen2.5:32b on the workstation's Ollama answered every
   request with nothing: `done: false` and no content under a JSON format, or HTTP 500 at the
   repeat limit without one. It does so at `num_ctx` 32768, its default there, and answers
   normally at 16384 (RESEARCH, Phase 12).
2. **Empty reviews counted as approvals.** All 42 cross-family reviews of the router's Phase 11
   runs went to that model. The agent read each empty answer as an approval. The gates passed
   every run, so the pass rates stand, but no review took place.
3. **No `num_ctx`.** The agent's Ollama requests carried no `num_ctx`, although SPEC §4.2 and
   ADR 0008 say every request sets it. The server's default decided the context, and the
   server's default was the one that broke the model.

## Decision

1. **Integrity first.**
   - An Ollama reply that is not complete (`done` false, or no final chunk) is a model error.
   - A review that yields nothing usable is recorded as no review. It is never an approval,
     and the task result says that no review took place.
   - `ssw bench run` sends every pool worker a canary request before the first task, and
     stops if one gives a wrong answer.
   - Ollama native requests always carry the worker's context as `num_ctx`.
2. **Fewer failed calls.** The edit tool accepts the shapes models send (ADR 0038).
3. **Less generation.** The plan step's effort follows the task's difficulty. So does the work
   steps' effort, which stays the same within a task, as ADR 0024 requires. A retry after a
   failed gate may raise it.
4. **Method.**
   - Changes are tuned on a new `dev` suite only. Its tasks share no repository with the mini
     suites.
   - Claims are registered before one confirmatory run of each local contestant on both mini
     suites (router, and Claude Code through `ssw proxy`).
   - The native runs of Phase 11 stay the baselines: same tasks, seeds and Claude Code version.
   - There will be no reruns in pursuit of a result. If a registered claim fails, it is
     narrowed by an ADR or recorded as failing.
5. **Claims.**
   - C-024 compares like with like: on the same local models, Stepswitch's agent against Claude
     Code through `ssw proxy`.
   - A claim about uncached tokens against a frontier model is registered only if the dev runs
     support it, and only before the confirmatory run.
6. **Front page.** The README shows a results table near the top. It is generated from passing
   claims, so C-018 checks its numbers (ADR 0039).
7. **Next.** A public benchmark, a SWE-bench Verified subset, follows in Phase 13.

## Consequences

- Phase 11's local runs had a working gpt-oss:120b and a dead second model. PROGRESS records
  that. The confirmatory run replaces them as the newest runs, so C-021 to C-023 are checked
  against runs with a working reviewer.
- The canary and the `num_ctx` rule turn a silent failure into a stop before the first task.
- The edit tool becomes more forgiving. Its anchored edits keep their checks, and the new
  shapes must match the file exactly once.
