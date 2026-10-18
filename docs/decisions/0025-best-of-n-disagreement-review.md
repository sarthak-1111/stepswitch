# ADR 0025: Best-of-N, the disagreement signal and cross-family review

- Status: accepted
- Date: 2026-10-19
- Phase: 6

## Context

SPEC §8.3 asks for N candidates by difficulty (d ≤ 2 → 1, d = 3 → 2, d ≥ 4 → 3), each from a
different model family where possible and in its own worktree. Selection is: passes the gates,
then the smaller diff, then the higher cross-review score. §8.4 marks a task uncertain when
passing candidates behave differently, and then generates targeted tests, escalates or asks the
human, depending on policy. §8.5 has a reviewer from another family answer
`{approve, issues:[{severity, file, line, note}]}`.

Constraints from earlier phases:

- One model server usually serves every worker. On the reference workstation the local server
  runs one large model at a time well, and an 8 GB laptop may have a single tiny model.
- The per-task budgets (tokens, wall time, GPU time, escalations) bound a whole task (ADR 0015).
- Before this phase the in-loop review used whichever worker the router scored best, and that
  was usually the author.

## Decision

1. **Opt-in, with the SPEC's table as `auto`.** `TaskSpec.best_of` is 1 by default (one
   candidate, the Phase 5 path unchanged). N ≥ 2 runs N candidates (at most 5). 0, or
   `ssw run --best-of auto`, takes N from the classifier's difficulty with the SPEC §8.3 table
   (`verify.best_of_n.candidate_count`). A relay task always runs one candidate. Making `auto`
   the default would run every task of difficulty 3 or more two or three times. That is a poor
   default on a laptop with one tiny model, where the extra candidates would all be the same
   model with other seeds.
2. **Sequential candidates, one budget.** Candidates run one after another in their own
   worktrees and share the task's budget. A candidate after the first is skipped
   (`candidate_skipped`) once the budget is exceeded. Running candidates in parallel would make
   them compete for the same server.
3. **One family per candidate.**
   - `plan_candidates` ranks the model families of the eligible workers by their best
     worker's prior for edit steps, and the top N families get one candidate each.
   - With fewer families than candidates, the remaining candidates reuse the ranked families
     in order with another seed.
   - A worker's family is `WorkerSpec.family`, else its model, else its id (`family_key`).
   - A candidate's steps route only among its family's workers (`RouterState.families`, a hard
     exclusion). Reviews and test-writing steps are the exception.
4. **Cross-family review routing.**
   - Review steps avoid the families of the workers that did the task's work steps
     (`RouterState.avoid_families`).
   - When the current rung has no worker of another family, the review moves to the nearest
     rung that has one, upwards first, and the move is recorded as relaxed.
   - With no other family anywhere, the author's family reviews, with a note.
   - Worker pins and pinning rules take precedence.
   - This applies to every task, so the single-candidate review is cross-family too.
5. **The review schema.**
   - `verify.review.REVIEW_SCHEMA` is the §8.5 shape with `additionalProperties: false`,
     severities `low | medium | high`, at most 8 issues, and `line` as an integer ≥ 0.
   - 0 means "no single line". Every field then has one type, rather than a union with null.
   - Reading is lenient. A schema answer bare, fenced or after other text is accepted, and so
     are common aliases (`critical` → high, `nit` → low) and string issues (medium).
   - A plain-text `APPROVE` or `ISSUES` answer (the Phase 2 format) is also read, and is marked
     `constrained: false`.
   - With optimization off (the C-011 baseline), the agent keeps the Phase 2 text prompt.
     Routing the review to another family is not a token technique, so it applies in both
     settings.
6. **Review score.** Approval is worth 0.5. The other 0.5 shrinks with each issue by its
   severity weight (high 0.5, medium 0.2, low 0.05). A review blocks when it does not approve
   or reports a high-severity issue.
7. **Selection order (deviation).** The winner is the first passing candidate by:
   1. it passed the gates;
   2. it passed the most valid cross and probe tests;
   3. its review does not block;
   4. its change size band;
   5. its review score;
   6. its plan order.

   The SPEC puts diff size straight after the gates. A smaller change that another
   candidate's test catches, or that the reviewer rejects, is not the better one, so those two
   signals come first.

   Sizes count changed lines outside test files, in bands 25% wide on a log scale of
   (lines + 10). Changes of 2 and 4 lines tie; changes of 40 and 400 lines do not. When at
   least two candidates pass, every passing candidate is reviewed, because the score is a
   selection input. The winner's review is the §8.5 review of the winning diff.
8. **The disagreement signal (§8.4).** It runs only when at least two candidates pass.
   - **Cross tests.** Every test file a candidate added or changed runs against every passing
     candidate's code.
   - **Probe tests.** One test file is written for the task by a model that sees only the
     instruction and an outline of the names the candidates defined, never their code
     (`--no-probe-tests` turns this off).
   - **How tests run.** Test files are copied to a scratch directory `.ssw-probe/<tag>/` in
     each worktree. They run with pytest (`--import-mode=importlib`, a JUnit report, `-B`, no
     cache provider, `addopts` cleared, `--continue-on-collection-errors`), and the scratch
     directory is removed afterwards.
   - **Which tests count.** A candidate's test counts only when it passes on its author's
     code. A probe test counts when it passes on at least one candidate. A collection error is
     an error outcome for every test in that module.
   - **Science outputs.** In the science track every golden case runs once per passing
     candidate, and every pair is compared with the case's mode and tolerances
     (`science.compare`).
   - **Clusters.** Candidates agree when every counted test has the same outcome on both and
     no output pair differs. Agreeing candidates form clusters, and the task is flagged when
     there is more than one cluster.
9. **Policies (`--on-disagreement`).**
   - `flag` (the default): the task is uncertain. The selected candidate is kept, but not
     applied.
   - `tests`: a model writes targeted tests for the disputed tests, given their sources. The
     one cluster that passes every targeted test that passes anywhere is kept. If no single
     cluster passes, the task stays uncertain.
   - `escalate`: one more candidate is planned and run, and the comparison is repeated. A
     strictly largest cluster is kept.
   - `human`: the task ends as `needs-human`.

   Selection then chooses within a kept cluster.
10. **Uncertain means not applied.** With `--apply`, an uncertain task records `not_applied`
    and leaves the working tree alone.
11. **Artifacts.**
    - Every candidate with changes is committed. The winner goes to `stepswitch/<task_id>` and
      the others to `stepswitch/<task_id>-c<i>`.
    - The run directory holds `candidate-<i>.patch` for each candidate and `diff.patch` for
      the winner.
    - Each candidate's agent writes `handoffs-c<i>.jsonl`, and `handoffs.jsonl` merges them.
    - Candidate events carry `candidate: <i>` and share the task's sequence numbers
      (`Trajectory.tagged`).
    - `TaskResult` gains `best_of`, `candidates`, `winner`, `disagreement`, `uncertain` and
      `review`.
12. **Bench contestant.** `ssw bench run --workers best-of-n --best-of N` runs N candidates
    from `--router-pool` on every task. Running the pool's workers as single contestants in
    the same run gives the C-012 comparison.

## Consequences

- A best-of-N task costs roughly N single runs, plus the cross tests, one probe-test step and
  one review per passing candidate.
- Probe tests are model output and can be wrong. They count only when they pass on some
  candidate, and they can only split candidates, never fail a task on their own.
- Two candidates of the same family with different seeds give weaker evidence of agreement
  than two families. The plan notes record when a family was reused.
- C-013 is proven on seeded tasks (`tests/claims/disagreement_fixture.py`) with scripted
  candidates. Detection on real model output is measured by the C-012 bench runs, which
  record whether each task was flagged.
