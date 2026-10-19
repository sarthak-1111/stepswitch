# ADR 0027: Replay and fork of recorded runs

- Status: accepted
- Date: 2026-10-19
- Phase: 7

## Context

SPEC §11 records every step as a JSONL event. It asks for `ssw replay <run> --from-step k
--worker X` to fork a new run from step k with another worker, and for deterministic replay
where possible (temperature 0, fixed seeds, recorded tool outputs). C-014: replaying a recorded
fake-worker run reproduces identical diffs and gate results.

Before this phase a trajectory was a log, not a recording:

- step events kept the first 400 characters of a model answer;
- tool events kept 2,000 characters of a result and none of the raw text that distillation
  reads;
- a failed summarizer call left no event at all;
- several inputs of a run were not recorded: the worker catalog, the context settings, the
  candidates' plans and compressions.

Even with every answer recorded, a run depends on more than its answers: routing decisions read
calibration data from the database, the ladder depends on the configured workers, and time
budgets on the wall clock.

## Decision

1. **Record what a replay needs.**
   - `step` and `aux` events keep the whole answer as a blob (`response_blob`, the IR response
     with usage and finish reason).
   - Tool events keep the raw text of a cut output (`raw_blob`).
   - A failed summarizer call is a `model_error` with its `purpose`.
   - An unusable brief-writer answer is accounted for and recorded like a usable one. Before
     this, it was neither.
   - A `catalog` event lists every worker's full spec, the summarizer and the context settings.
     A `candidate_start` event holds each best-of-N candidate's plan, and `compress` events hold
     each compression.
2. **Replay follows the recording, not the configuration.**
   - The replay is a new run whose worktrees start at the recorded base commits.
   - It uses the recorded workers, settings, features, learning samples, plans and ladders.
   - Each routing decision is the recorded one, rebuilt from its `decision` event. Calibration
     and learned data change between runs and must not change a replay.
   - Model calls are answered in recorded order per candidate, a failure by raising it again,
     so failovers repeat.
   - The replay classifies nothing and samples nothing, and it teaches the learning router and
     the handoff-recovery estimate nothing.
   - The result is never applied to the working tree.
3. **Tools: effects again, results from the recording.**
   - `edit`, `run` and `test` run again for their effect on the files. Tests write files too,
     and gates read the working tree.
   - Every result the transcript gets is the recorded one, so the transcript is the recorded
     one. The only exception is the task id that a HANDOFF brief names.
   - A result that differs from the recording (another edit result, exit code or test outcome)
     is noted as a departure.
   - Other tools (`read`, `grep`, `search`, `repo_map`, `lsp`) are answered from the recording.
   - The gates run for real. Their results are what C-014 compares.
4. **Time as recorded.**
   - A replay's clock is real time plus the recorded time of everything the recording answers:
     each model call's `wall_seconds`, and for a recorded tool result the time between its
     event and the one before.
   - Wall-time budgets therefore run out at the same point. The scenario with a time budget
     stops after the same five calls.
5. **Departures are reported, not hidden.** Every point where the replay leaves the recording
   is listed in the comparison, and a pure replay with departures, other diffs or other gate
   results exits with 1. Examples:
   - a call of another kind than recorded;
   - a tool call with other arguments;
   - a recorded call the replay never used.

   The comparison goes to the replay's `replay.json`. It holds:
   - whether the result's patch and each candidate's patch are identical;
   - the gate results side by side;
   - the model calls from the recording and the live ones;
   - the tools run again;
   - the departures.
6. **A fork is a replay that stops replaying.**
   - With `--from-step k`, everything before the routing decision for model call k is replayed.
     Calls are numbered per candidate, with failed attempts and text steps included, as in the
     `step` events.
   - From that decision on, routing decides, pinned to `--worker X` when given, models answer
     live and tools run.
   - `--worker` alone forks at call 1. A fork's worker may be a configured worker that the
     recording does not have.
   - Forking a best-of-N run is refused. Its candidates interleave, so a single call number does
     not name one point of the run; such a run can still be replayed whole.
7. **Refusals.** A replay is refused, with the reason, for:
   - a run recorded before this phase (no full answers, no catalog);
   - a missing blob or base commit;
   - a `--from-step` outside the run's model calls;
   - an unknown fork worker.

   The CLI, `replay_run` and `POST /api/runs/{id}/replay` refuse them before anything runs.
8. **Fixes found by replaying.**
   - The gates counted untracked bytecode as changed non-Python files and so ran the whole test
     suite where the impacted tests would do. They now leave generated files out, as a
     worktree's diff does.
   - The agent's `test` and `run` tools now drop bytecode older than its source, as the gates
     already did. Without this, a same-size edit within the second of the last compile ran the
     old code and reported stale test results. It showed up as a flaky scripted run.

## Consequences

- C-014 holds on seven recorded runs:
  - a fix with a failing test on the way;
  - a failover;
  - a relay with a brief;
  - a time-budget stop;
  - a best-of-2 run;
  - a science-track run with a golden case;
  - a CLI run whose model servers are shut down before `ssw replay`.

  Every diff and gate result is identical, every model call is answered from the recording,
  and no model is asked.
- A replay needs the project's blobs and the recorded base commits. Pruning either makes the
  runs that need them unreplayable, and the refusal says so.
- A replay runs under the current tools and gate commands. If those changed, the gates may
  differ, and the comparison shows it. Faithful replays need the same environment as the
  recording.
- Recordings are larger: each model answer is kept whole. These blobs are content-addressed and
  local, like tool outputs.
- The dashboard's trajectory page can replay or fork a run. It calls the same API. The
  dashboard could not be type-checked on the development host, whose Node is older than the
  dashboard's toolchain needs. The CI workflow's dashboard job type-checks and builds it.
