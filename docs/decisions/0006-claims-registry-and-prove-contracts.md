# ADR 0006: Claims registry, public-text lint and prove contracts

- Status: accepted
- Date: 2026-10-16
- Phase: 0

## Context

SPEC §18 requires every public statement about Stepswitch to be a registered claim with an
automated test, integrity meta-tests that run from day one, a README that shows only passing
claims, and README numbers that come from evidence files. Several details are left open:

- which documents are "public" and therefore linted;
- the marker syntax for claim text and templated numbers;
- how a claim whose parts land in different phases (C-005 is "P2–P3") is represented;
- how `scripts/phase_check.py` can tell that a `ssw prove` report reflects the current code;
- how `ssw prove --quick` (CPU-only, under 10 minutes) can cover bench-based claims that the
  phase goals require (C-011 in Phase 5, C-012 in Phase 6).

## Decision

1. **Registry format.** `claims/claims.yaml`, `version: 1`. Fields follow SPEC §18.1, plus
   `proof` (the SPEC §18.5 "how it's proven" text, verbatim), optional `parts` (each with
   `name`, `phase`, `tests`) for claims that land in several phases, and optional `narrowed_by`
   (path of the ADR that changed the wording). Without `narrowed_by`, the text must equal the
   SPEC §18.5 wording; a meta-test enforces this.
2. **Public text** is `README.md` and `docs/site/**/*.md` (the future docs site), configured in
   `claims.yaml` under `lint.public_docs`. Internal design documents (SPEC, RESEARCH, PROGRESS,
   ARCHITECTURE, ADRs) discuss hypotheses and other projects, so they are not linted. Generated
   documents (for example `docs/COMPATIBILITY.md`) are checked through their generators.
3. **Markers.**
   - Claim text: `<!-- claim:C-0xx -->exact registered text<!-- /claim -->`.
   - Templated number: `<!-- num:C-0xx:dotted.key -->value<!-- /num -->`, where the value is
     read from `claims/evidence/C-0xx.json` and must match it at the displayed precision.
   - Generated README section: between `<!-- claims:start -->` and `<!-- claims:end -->`,
     written only by `ssw prove --render-readme`.
4. **Lint rules.** The banned unmarked patterns live in `claims.yaml` (`lint.banned_unmarked`):
   the SPEC list, extended with "faster", "better", "cheaper", "cheapest", "outperform",
   "always", "guarantee" and "state of the art", and with "any/all/every … model(s)" allowing
   up to two words in between (so "any open model" is caught). Code spans, fenced code, URLs
   and link targets are not prose. In the README, digits outside markers fail, except claim IDs,
   section references (§) and version tokens such as v0.1.
5. **Status.** `status` is written by `ssw prove`, never by hand. A claim whose parts are not all
   due stays `pending` while the due parts' tests pass.
6. **Prove report contract.** `ssw prove` writes `claims/prove-report.json`:
   `{"schema": 1, "mode": "quick"|"full", "source_digest": "<hex>", "claims": {"C-0xx":
   {"status": ..., "tests": {"<node id>": "passed"|"failed"|"skipped"|"error"}}}}`.
   `source_digest` is the algorithm in `scripts/phase_check.py`: sha256 over the files under
   `src/`, `tests/`, `registry/`, `bench/suites/`, `pyproject.toml`, `uv.lock` and
   `claims/claims.yaml` (with `status:` lines removed), sorted by path, with CRLF normalized to
   LF in text files so that every OS computes the same value. Phase checks accept only a report
   whose digest matches the working tree.
7. **Quick and full modes (provisional; settle in the Phase 1 prove ADR).** `--quick` runs every
   claim test that can run CPU-only. For `bench` and probe-matrix claims it validates the
   committed evidence (schema, thresholds, the digest of the bench configuration that produced
   it, and the recorded command) instead of re-running the bench. `--full` re-runs the bench and
   probe matrix and rewrites that evidence. Evidence never comes from anywhere but a recorded
   `--full` run.
8. **Evidence files are committed** under `claims/evidence/`. Evidence writers scrub host
   names, user names, absolute paths, endpoint URLs and keys; the repository hygiene test fails
   if any of those leak.

## Consequences

- The SPEC §0 pitch cannot appear verbatim in the README ("best", "any open model") until the
  statements in it are registered claims or the pitch is reworded. Tracked in PROGRESS.md.
- Contributors must re-run `ssw prove` after changing sources before a phase check passes.
- Quick-mode coverage of bench claims depends on committed evidence produced by a full run;
  the README links each number to that evidence and its command.
- The meta-tests carry self-tests on synthetic text, so they stay meaningful while the public
  docs are nearly empty.
