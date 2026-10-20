# ADR 0040: C-025, fewer uncached tokens than Claude Code on Sonnet 5.5 and on Opus 5.5

- Status: accepted
- Date: 2026-10-20
- Phase: 12

## Context

C-022 was registered on uncached tokens and failed there: on the Phase 11 runs the router used
more uncached tokens than Claude Code on Sonnet 5.5 (ADR 0036). ADR 0037 allows one claim about
uncached tokens against a frontier model, registered before the confirmatory run, and only if
the dev runs support it.

The rule was fixed before any native run on the dev suite: register the claim if the router's
uncached tokens per task run on the dev suite are at least 15% below each baseline's.

The dev runs (6 tasks, 2 seeds, tuning only):

| Contestant | Passed | Uncached per task run | Tokens per task run |
|---|---:|---:|---:|
| router (after the patch-edit fix) | 12/12 | 5,759 | 19,678 |
| Claude Code, Sonnet 5.5 | 12/12 | 7,786 | 68,870 |
| Claude Code, Opus 5.5 | 12/12 | 11,563 | 113,560 |

The router used 26.0% fewer uncached tokens than Claude Code on Sonnet 5.5 (95% interval 8.7%
to 40.0%) and 50.2% fewer than on Opus 5.5 (24.9% to 68.5%), so the rule is met. The runs are
committed under `bench/results/2026-10-20-dev-local` and `bench/results/2026-10-20-dev-native`;
`2026-10-20-dev-router` is the router before the patch-edit fix (8,224 uncached tokens per task
run).

## Decision

Register C-025 before the router's confirmatory run: "On the mini suites, Stepswitch on local
models uses fewer uncached tokens per task than Claude Code on Sonnet 5.5 and on Opus 5.5."

- The task runs and baselines are those of C-021: the native runs of Phase 11 stay the
  baselines, as ADR 0037 requires.
- The ratio's 95% cluster-bootstrap interval over tasks must lie below one for each model, the
  router sends no token to a hosted model, and every compared run of the router passed the
  canary check.
- There is no rerun in pursuit of the result. If the claim fails, it stays registered and
  failing, or is narrowed by an ADR.

## Consequences

- If C-025 passes, the README reports fewer tokens on both measures, each from a passing claim.
- C-022 keeps its narrowed wording and history.
