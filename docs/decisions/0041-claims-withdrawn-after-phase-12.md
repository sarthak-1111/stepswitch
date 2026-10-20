# ADR 0041: Three claims withdrawn after the Phase 12 confirmatory run

- Status: accepted
- Date: 2026-10-20
- Phase: 12

## Context

ADR 0037 allowed one confirmatory run of each local contestant on both mini suites, with the
claims registered first and no reruns. The run (`bench/results/2026-10-20-mini-*-confirmatory`)
compared the router and Claude Code through `ssw proxy`, on the same local models, with the
native runs of Claude Code on Sonnet 5.5 and on Opus 5.5 from Phase 11. Over the 42 task runs
every contestant shares:

| Contestant | Passed | Tokens per task run | Uncached per task run | Seconds per task run |
|---|---:|---:|---:|---:|
| router, local models | 41/42 | 24,793 | 7,049 | 41.3 |
| Claude Code through `ssw proxy`, local models | 42/42 | 173,130 | 48,200 | 165.5 |
| Claude Code, Sonnet 5.5 | 42/42 | 73,390 | 7,798 | 16.8 |
| Claude Code, Opus 5.5 | 42/42 | 93,920 | 9,386 | 21.1 |

The router's uncached tokens were 9.6% fewer than Claude Code's on Sonnet 5.5, with a 95%
interval from 11.8% more to 27.1% fewer, and 24.9% fewer than on Opus 5.5 (4.1% to 42.4%). Its
tokens were 66.2% and 73.6% fewer, and 85.7% fewer than through `ssw proxy`.

The failed task run (`mini-science/vectorize-gaussian-sum`, seed 1) was not an infrastructure
error. The model's first edit replaced the whole function with a begin-patch patch whose
removed lines dropped a trailing comma from one parameter line. The tool refused the patch,
and its message listed the current lines without saying which one differed. The model repeated
the same copy nine times, later with the read view's hash prefixes pasted into the patch, and
the agent stopped as stuck with no higher rung in the pool.

So:

- C-021 (the router passes as many task runs as Claude Code on Sonnet 5.5 and on Opus 5.5)
  fails: 41 against 42.
- C-024 (fewer uncached tokens than Claude Code through `ssw proxy`, and as many passes) fails
  on its second condition, by the same task run.
- C-025 (fewer uncached tokens than Claude Code on Sonnet 5.5 and on Opus 5.5) fails for
  Sonnet 5.5: the interval does not lie below one.
- C-022 and C-023 pass.

A failing claim fails `ssw prove` and CI. CLAUDE.md asks for narrowing a claim that cannot be
made true. Here each narrowing would keep the half of a claim that happened to pass, chosen
after seeing the result: Opus only for C-025, the token half for C-024. That is the selection
the confirmatory run exists to prevent.

## Decision

1. **Withdrawn claims.** A claim can be withdrawn by an ADR (`withdrawn_by` in claims.yaml, status
   `withdrawn`). It keeps its wording, scope, thresholds, tests and evidence in the repository.
   `ssw prove` never selects it and reports it as withdrawn, pytest skips its tests with the
   ADR as the reason, and the README claims section lists it by id with a link to the ADR,
   without its wording. A claim that is withdrawn is never reinstated: a new claim gets a new
   id and its own confirmatory run.
2. **C-021, C-024 and C-025 are withdrawn**, neither narrowed nor rerun.
3. **The README results table shows what was measured.** Its cells come from the evidence of
   C-022 (Stepswitch, Sonnet 5.5, Opus 5.5) and C-023 (Claude Code through `ssw proxy`), which
   pass on the same task runs and record every contestant's passes, tokens and time. So the
   table shows the router's 41 of 42 next to the baselines' 42 of 42.
4. **C-022 no longer repeats the uncached comparison beside its line**: the table shows
   uncached tokens per task run for every contestant. Its scope says so.
5. **Next.** The refusal message names the closest match and the line that differs, a unique
   near-match that differs only in removed lines is accepted with a note, and valid hash
   prefixes are stripped from patch lines. These are agent changes for a later phase, tuned on
   the dev suite.

## Consequences

- The registry, the prove report and the README all show which claims were withdrawn and why.
- Phase 12 ends with no passing claim of its own. Its work stands: the integrity fixes, edit
  format version 2, effort by difficulty, the patch bookkeeping fix, the dev suite and the
  results table.
- SPEC §18.1 gains the `withdrawn` status.
