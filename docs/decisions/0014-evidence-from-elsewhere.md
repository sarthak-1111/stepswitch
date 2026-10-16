# ADR 0014: Claims proven by evidence produced elsewhere

- Status: accepted
- Date: 2026-10-16
- Phase: 1

## Context

Most claim tests produce their evidence themselves. C-016 cannot: its proof is that
`ssw prove --quick` passed on hosted Linux, macOS and Windows runners, so its evidence is
written by the CI workflow's aggregation job, and the claim test only validates it. The evidence
is bound to the source digest it was produced from (ADR 0006).

If the test failed whenever no evidence for the current sources existed, every local
`pytest -q` would fail after any source change until CI had run on that exact commit, and a
claim that has not been evaluated would be reported as false.

## Decision

A claim test that validates evidence produced elsewhere distinguishes three cases:

- no evidence, or evidence for other sources: the test skips, so the claim is `pending`
  ("not evaluated for this code");
- evidence for the current sources that shows a failure or a missing platform: the test fails,
  so the claim is `failing`;
- complete evidence for the current sources: the test checks every threshold and passes.

The claim can therefore only pass with evidence for exactly the current sources. Phase checks
still require it to pass, so a phase that includes such a claim is complete only after CI ran.

## Consequences

- Local test runs stay green while CI evidence is pending, and the prove report shows the claim
  as pending with the reason.
- C-016 stays pending until the repository has a hosted remote with CI (a maintainer action).
