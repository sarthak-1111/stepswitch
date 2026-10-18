# ADR 0024: Token and cache optimization in the built-in agent

- Status: accepted
- Date: 2026-10-19
- Phase: 5

## Context

SPEC §13 lists twelve token techniques (TK1 to TK12), §7.2 asks for cache-aware switching and
§9 for HANDOFF briefs counted with the receiving model's tokenizer. Phase 2 already shipped
TK5 (anchored edits), TK9 (hybrid search) and TK12 (the ledger). The Phase 3 bench supplied
the baseline and three findings:

- The router moved a step to another model only after a server error, never by choice.
  Natural switch handoffs therefore do not happen on the mini suites, and C-010 needs at least
  20 briefs from bench runs.
- Work steps averaged about 1.9K prompt tokens, and the largest transcripts reached about 10K.
  Each step re-sends the whole transcript, so an extra step costs more than most stubs save.
- One server shared by a model's router and worker roles capped the context of agent turns.

Research corrected three SPEC assumptions (docs/RESEARCH.md, "SPEC corrections"):

- Ollama has no tokenize route.
- tiktoken and the tree-sitter language pack download files on first use.
- The default LLMLingua model is a 7B GPU model.

## Decision

1. **One switch for everything.** A task's `optimize` flag (`ssw run --no-optimize`,
   `[context] optimize`, `ssw bench run --baseline`) turns every Phase 5 technique on or off.
   With it off, the same code behaves like the Phase 2 agent: same prompts, output caps and
   prompt order, no effort hints, no briefs. C-011 compares exactly these two settings.
2. **Stable prefix (TK1).** System text, then tool specs, repository map and glossary, the same
   bytes on every step. Volatile content is appended as messages. The message list only grows,
   except at the batched pruning and compaction points.
3. **Prefix registry (§7.2).**
   - One hash chain per server: block 0 is the stable prefix, then one block per message.
   - The predicted cached share is the common prefix of the new prompt and the last prompt
     sent to that server.
   - Each server's prediction is calibrated by an EWMA of the reported cached tokens (Ollama
     `prompt_eval_cached_count`, llama.cpp `cached_tokens`). `ssw calibrate --cache` seeds
     it: two requests share a prompt of about 1.5K tokens behind a fresh nonce.
   - An error forgets that server's chain.
4. **Cost model (§7.2).**
   - The SPEC formula is extended so that staying also costs something: the current worker
     must read the tool results its server has not seen.
   - Above 24,000 uncached tokens a switch sends a brief instead of the transcript.
   - The new worker's chance of success after a handoff is `handoff_success(p_new,
     p_current, r)`, where r is the share of the gap it recovers.
   - r is a posterior on a grid, with its prior centred below one half (ADR 0005). Only tasks
     that handed over update it, and only when the brief went to a stronger model.
   - `ssw bench calibrate` turns bench rows into per-model success rates. Each rate is shrunk
     towards the tier rate by 4 pseudo-runs.
5. **Briefs (TK7, §9).**
   - The agent builds the facts deterministically: task, files, commands, the failing test,
     the plan as the first decision.
   - A local level-0 model with the `handoff_writer` (or `summarizer`) role adds done, decisions
     and open questions. Its answer is constrained by a JSON schema, and it is accounted as a
     `summarize` step. If no writer is available or the writer fails, the facts alone make the
     brief.
   - Every brief is validated against the schema and trimmed until the receiver's tokenizer
     counts at most 1,500 tokens.
   - Tokenizer resolution: the registry's `tokenizer.json`, then the llama.cpp `/tokenize`
     route, then a tokenizer file mapped from the model name, then a flagged estimate. Mapped
     files come from `ssw models pull --tokenizers`, pinned by revision and sha256.
   - Absolute paths are rewritten before anything leaves the agent: the worktree becomes ".",
     the project root `<project>` and the home directory `~`.
6. **Compaction (TK6).**
   - At 60% of the transcript budget, the older turns are replaced by a brief. The budget is
     min(32,768, the worker's context), and the latest exchange is kept verbatim.
   - Compaction briefs are recorded like switch briefs (reason `compact`), so C-010 covers
     them as well.
   - Superseded results become one-line stubs, applied in batches of at least 800 tokens so
     that server caches are invalidated rarely. The two newest results of each kind are kept.
7. **Read dedup (TK3).**
   - A read of an unchanged range is answered with a stub once per range and content, and
     only when the read is at least 500 tokens. The next identical read is shown in full, and
     the older copy is pruned as superseded.
   - Reason: in a Phase 5 smoke run, a 26-line file was re-read five times against stubs
     after a failed edit. The task took 17 steps, each re-sending the transcript.
8. **Distillation (TK4).** A tool output over 2,000 tokens becomes its error lines, file:line
   references and tail, plus an optional T0 summary. The raw output stays readable as
   `@output/<n>`.
9. **Role budgets (TK10).**
   - Output caps and effort: plan 1,200 tokens at high effort; navigate, read and run 2,048 at
     low; edit and triage 2,048 at medium; review 600 at medium; summaries 300 at low; text
     600 at low.
   - Work-step caps stay at Phase 2's 2,048, because a step type is a prediction. A "read"
     step that turns into an edit must not be cut off.
   - Effort hints go only to models marked `reasoning`.
   - Optimized plans ask for one line per step and are cut at 1,200 characters, since every
     work step re-sends the plan.
10. **Constrained review (TK8).** The reviewer answers `{approve, issues}` under a strict JSON
    schema. Unparseable answers fall back to the Phase 2 text reading. Both local test models
    honoured the schema (docs/RESEARCH.md, Phase 5).
11. **Relay contestant (bench only).** `ssw bench run --relay A,B --relay-step N` pins worker A
    until work step N (default 6), then pins B with a handoff threshold of 0, so the switch
    always sends a brief. Relay runs produce the switch briefs that C-010 checks and the
    handoff outcomes that `ssw bench calibrate` feeds to r. They are not compared in C-011.
12. **Tree-sitter repository map (TK2).** Pinned per-language grammar wheels and Stepswitch's
    own queries replace the language pack, which downloads parsers at runtime. Python keeps
    `ast`.
13. **Prompt compression (TK11) stays off.**
    - The reference server `extras/llmlingua-server` runs the LLMLingua-2 small checkpoint on
      the CPU, from a local path, out of process, behind an offline guard.
    - A live check halved 6,000 characters of Markdown in about 0.5 s with 1.2 GB of RAM. The
      output also broke paths and dates apart (`docs / SPEC. md`).
    - The mini suites read almost no prose, so no bench can show a gain yet. The setting stays
      off until a suite with documentation reads exists.

## Consequences

- Every optimized task writes its briefs to `handoffs.jsonl` in its run directory. Bench runs
  collect them per suite.
- Tasks that a stronger model completes from a brief update r. Over time the router learns
  how much a brief actually recovers.
- The baseline remains runnable forever. Any later change to the agent can be measured
  against Phase 2 behaviour with one flag.
- Briefs and prefixes depend on tokenizer files. Without them, counts are flagged estimates,
  and C-010 rejects estimated counts.
