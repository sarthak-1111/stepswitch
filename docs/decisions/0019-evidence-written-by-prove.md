# ADR 0019: Committed evidence is written only by `ssw prove`

- Status: accepted
- Date: 2026-10-16
- Phase: 3

## Context

SPEC §18.2 says each claim test writes an evidence file to `claims/evidence/<id>.json`. Until
now every run of a claim test did so, including a plain `uv run pytest -q`. A plain test run
therefore changed committed files (new timestamps, new measurements), and after it the
evidence no longer matched the prove report and README that `ssw prove` had produced from the
previous evidence. Phase 3 renders README numbers from evidence (C-018), so this mismatch would
become a failing integrity test after any plain test run whose measurements differ, such as a
wall time.

## Decision

1. Claim tests write to `claims/evidence/` only when `ssw prove` runs them. The prove runner
   sets `STEPSWITCH_PROVE_MODE`; the evidence writer checks it.
2. A plain test run writes the same evidence files to a scratch directory under the system
   temporary directory, so the writing code is still exercised.
3. Reading committed evidence is unchanged: C-016 and the README number lint read
   `claims/evidence/`.

## Consequences

- Committed evidence, the prove report and the README always come from the same prove run.
- A plain test run leaves the working tree clean.
- To refresh committed evidence, run `ssw prove`, as the README already says.
