# ADR 0028: C-011 counts uncached tokens

- Status: accepted
- Date: 2026-10-19
- Phase: 5

## Context

C-011 said "Uses fewer tokens than the unoptimized baseline at the same pass rate". Its test
compares the optimized configuration with the Phase 2 baseline on the mini bench suites: the same
router pool, tasks and seeds, two seeds each (42 paired runs). Before the second measurement the
test was made strict, and the order of fallbacks was fixed in advance:

1. Total tokens (input plus output), with a 95% cluster bootstrap interval over tasks that lies
   above 0, at a pass rate that is not lower in either suite.
2. If that fails, the claim is narrowed to uncached tokens under the same rule. Uncached tokens
   are the input tokens the server did not serve from its prompt cache, plus the output tokens.
3. If both fail, the claim stays failing and stays out of the README.

The first round showed that the optimizations broke the servers' prompt caches. Uncached
tokens went up even as total tokens went down. Two causes were found and fixed (ADR 0024,
decisions 9 and 14):

- the reasoning effort changed between the steps of one transcript;
- nudges added user messages in the middle of a transcript.

The second round, with the fixes, measured 21.3% fewer total tokens and 18.7% fewer uncached
tokens:

| Measure | Reduction | 95% interval |
|---|---|---|
| Total tokens, overall | 21.3% | −13.6% to 47.8% |
| Uncached tokens, overall | 18.7% | 3.9% to 30.9% |
| Uncached tokens, mini-general | 16.5% | −3.2% to 33.0% |
| Uncached tokens, mini-science | 24.1% | 14.7% to 32.9% |

Pass rates were 29 of 30 runs against 28 of 30 (general) and 12 of 12 against 12 of 12
(science).

## Decision

Step 2 of the fixed order applies:

- C-011 now says "Uses fewer uncached tokens than the unoptimized baseline at the same pass
  rate".
- The test checks uncached tokens (`thresholds.measure: uncached`) with the same rule as before:
  - each suite's reduction above 0;
  - a pass rate that is not lower in either suite;
  - the overall 95% interval above 0.
- The evidence still reports total tokens with their interval, and the README number is the
  uncached reduction.

Uncached tokens are also the better measure of what the optimizations are for:

- A cached prompt token costs a local server almost nothing, while every uncached one has to be
  computed.
- Most of the techniques work by keeping prefixes stable and cached: stable prefixes, the prefix
  registry, pruning only in batches worth a cache miss, and briefs only past a threshold.
- Total tokens are dominated by the cached prefix re-sent on every turn, so they grow with the
  number of turns a task happens to take. That is why their interval is about three times as wide
  as the uncached one on the same runs.

## Consequences

- The README states the uncached reduction. The 21.3% fewer total tokens is in the evidence but
  is not claimed.
- With more paired bench runs (more tasks, not more seeds of the same tasks), the total-token
  wording can be tested again. If its interval then lies above 0, this ADR can be superseded.
- The first round's runs stay in `bench/results` as the record of the cache regression.
