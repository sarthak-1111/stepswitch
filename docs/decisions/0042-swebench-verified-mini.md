# ADR 0042: SWE-bench Verified Mini as the public benchmark

- Status: accepted
- Date: 2026-10-20
- Phase: 13

## Context

Phases 11 and 12 measured Stepswitch on its own mini suites (ADR 0034, ADR 0037, ADR 0041).
Those suites are small and made for this project, so a reader cannot compare them with any
published result. SWE-bench Verified is the benchmark coding agents report on: real issues from
real repositories, graded by an official harness that runs tests the agent never sees. Its 500
instances, each in an image of several gigabytes, take too long on one workstation with a local
model. SWE-bench Verified Mini is a published subset of 50 of them (25 from django, 25 from
sphinx), chosen by its authors to match the full set's spread of difficulty and resolve rates
(docs/RESEARCH.md, Phase 13).

A result is only worth showing beside a baseline that differs in one thing. mini-SWE-agent is a
widely used minimal agent with a SWE-bench mode. Run on the same open-weight model, the same
server and the same instances, the difference between the two is the orchestrator.

## Decision

1. **Suites.** `bench/suites/swebench-verified-mini.yaml` is the test set: the 50 Mini ids.
   `bench/suites/swebench-dev.yaml` is the dev set: six Verified instances that are not in
   Mini, three from each repository. Agent changes are tuned on the dev set only. The test set
   runs once, after its claims are registered, and is never rerun to improve a number.
2. **Contestants.** `router` is Stepswitch with a pool of one model (`--router-pool
   <endpoint>/<model>`), so no other model helps it. `mini-swe-agent:<endpoint>/<model>` is
   mini-SWE-agent 2.4.6 with its default SWE-bench configuration (`swebench.yaml`, tool calls
   through its `litellm` client), on the same endpoint and model through the chat-completions
   format. Both of its SWE-bench configurations were tried on the dev set:
   - With tool calls, three of six gpt-oss:120b runs ended when the model server answered
     HTTP 500 for a tool call it could not parse, after mini-SWE-agent's own ten retries. In
     one of them the call's patch held the fix, and its JSON had a stray bracket.
   - With text actions (`swebench_backticks.yaml` and the `litellm_textbased` client), gpt-oss
     wrote several commands in one reply, and both dev runs ended on `RepeatedFormatError`,
     one of them on an instance that the default resolved.

   The default is the configuration its authors ship and gpt-oss's own way to act, so it is
   the baseline. Stepswitch meets the same parse failure with its fallback tool protocol
   (ADR 0035): that is a difference between the agents, and each one's stop reasons in the
   evidence show it. A SWE-bench suite refuses every other kind of contestant.
3. **Where a task runs.** Each task run happens in a container of the instance's own image.
   Stepswitch runs from a read-only volume with its locked dependencies and Python 3.12, with
   the package source and its data copied in as a wheel lays them out. mini-SWE-agent runs from
   a pinned harness volume and starts the instance container itself, as it does on its own.
   Only the problem statement reaches either agent: hints, test names, the test patch and the
   gold patch never do. A container gets only endpoints that the host's configuration marks
   local and that need no credentials, so no key enters a container. Dataset rows are cached
   in the state directory and never committed.
4. **What is graded.** For Stepswitch, the diff of its task branch against the testbed's
   `HEAD`, whatever the run's status: the branch is what a run leaves its user, and agents
   that stop at a limit commonly submit their changes so far. For mini-SWE-agent, its own
   submission, which is empty when a limit or an error stops it. The evidence also records how
   many of Stepswitch's resolved runs ended with status `done`.
5. **Grading.** The official harness (swebench 5.0.2) grades each prediction in a Linux
   container, one instance at a time; on a Windows host it never runs under Windows Python
   (CRLF test patches). A run passes when the harness resolves the instance. An empty patch is
   not resolved and is not sent to the harness. Grading does not depend on the agent, so a
   grade that failed for infrastructure may be repeated; a task run may not.
6. **Gold check.** Before the confirmatory run, the gold patches of all 56 instances are graded
   on the same machine. An instance whose gold patch is not resolved there is excluded for both
   contestants, and the exclusion is recorded before the run.
7. **General fixes from the dev set.** Each is a general agent or verification change with its
   own tests, not a rule for SWE-bench:
   - A worktree of a flat-layout project installed in editable mode imports the worktree
     (`PYTHONPATH`), and a conda environment's directories go on `PATH`.
   - Test summaries read unittest output (django's own runner).
   - The search index reads the repository map once per refresh (on django: 627 s to 9.6 s),
     skips translation catalogs and generated files, and takes a path scope. A query written as
     code gets exact matches first: definitions for `def name` or `class Name`, the text as
     written for a fragment, every line naming a name otherwise. They list source files first,
     then tests, then everything else: in file order, sphinx's change logs filled all twenty
     lines for a name the issue mentioned.
   - Failures that already happen at the base commit: after a failed run, the `test` tool and
     the test gates run the same command in a checkout of the base commit and list the failures
     found there apart. They never pass because of them: a task is often to fix a test that
     fails at the base commit, and a first version that passed such runs let an unfixed bug
     through (`tests/unit/test_verify_baseline.py`). A run without named failures, or longer
     than ten minutes, is not compared. While no file differs from the base commit, the `test`
     tool says that every failure happens before any change, with no second run, and the work
     log that briefs are written from says so too: a dev run that saw 126 failures before its
     first edit (two of them unnamed, so not compared) spent its steps on the test setup
     instead of the issue. A failure that a run cannot name no longer keeps the others from
     being compared: the named ones are, a failure counts as old only when the base commit's
     run named it too, and the report says how many were left out. pytest ids whose
     parameters hold spaces are now named.
   - `test` without a selector runs only the test files that import the change when the suite
     has more than 200 test files, and asks for a selector when none does.
   - Patches (ADR 0041, item 5): the read view's `<line>:<hash>|` prefixes are dropped when
     every line a hunk expects in the file has one; a hunk whose old lines are not found goes to
     the one place where only removed lines differ, by at most two lines, each nearly the
     file's line, with a note naming them; a refusal names the closest place and the lines that
     differ there, or says that a hunk came before the previous one.
   - The repository hygiene scan removes URLs before allowed identifiers, and allows the
     provider prefix that mini-SWE-agent's model client needs for a chat-completions server.
   - The transcript budget (amends ADR 0024, item 6). When `window_tokens` is not configured,
     a worker with more than 65,536 tokens of context gets half of it, at most 65,536; every
     other worker keeps min(32,768, its context). On the dev set, a 131,072-token model
     compacted at about 20,000 tokens, every 12 to 15 steps on django and sphinx files, and read
     the same ranges again after each brief.
   - Stuck detection. A compaction or a handoff takes the earlier reads out of the transcript,
     and the brief tells the worker to read a file again before it edits it, but a read with the
     same arguments never counted as progress again: a dev run that went over the same file
     ranges after its third compaction stopped as stuck. Such a read or search now counts once
     more after a compaction or handoff; a third pass is still stuck, and edits, test results
     and command outputs count only once.
8. **Containers and volumes.** The bench names what it creates `ssw-swe-*`, and the harness
   names its grading containers `sweb.eval.<id>.<run id>`. The bench removes only those.
   Instance images stay, because pulling them again is slow.
9. **Interruptions.** A run of both contestants takes days, so `ssw bench run --resume --out
   <dir>` continues an interrupted one. Every option that shapes a result must be the same
   (the manifest's suite, contestants, tasks, seeds, limits, pool, source hash, dataset,
   harness, runtime and images), and the first run's manifest is kept, with each resumption
   recorded in it. A task run that was graded is never run again. One that was cut short
   before its grade has no result, so it starts again, and its saved prediction is dropped.
   The containers of a run carry a label with its directory's name; a resumption removes the
   ones still there, whose agent would otherwise keep calling the model server.
10. **The confirmatory run, registered before it starts.** One seed of each contestant on the
    50 Mini instances, on gpt-oss:120b on this workstation's model server.
    - Stepswitch: `router` with that one model, `--max-steps 100 --budget-seconds 3600`, review
      on. These are the settings of the dev runs. A higher step limit mostly adds tokens to
      runs that have lost their way, and the dev runs did not show what it would win.
    - mini-SWE-agent: item 2's configuration with its own limits: 250 steps, 60 s per command,
      and an instance container that lives two hours.
    - Claims C-026 to C-028, as registered. The evidence also gives each contestant's stop
      reasons, and the tokens per instance on the instances that both resolved, which no step
      limit shapes. The README table shows both.
    - Dev runs, for tuning only: `bench/results/2026-10-20-swebench-dev` (both contestants,
      mini-SWE-agent with its default configuration, the router before the fixes of item 7:
      router 2 of 6 resolved, mini-SWE-agent 1 of 6), `...-swebench-dev-router` (the router
      after them: 2 of 6, with 10 compactions instead of 28 and further on three of the four
      it missed), and `2026-10-21-swebench-dev-check` (the router with these settings, 2 of 2;
      mini-SWE-agent with its text-action configuration, 0 of 2, both on
      `RepeatedFormatError`).
11. **Found during the confirmatory run.** Written while it ran, after part of its results
    were in. None of these changes a claim's rule or any code the run used.
    - **The bench's time cap.** The bench stops a mini-SWE-agent run that has not ended after
      four hours and records an infrastructure error, so C-026 leaves the instance out for both
      contestants. The cap was in the code the run started from, but this record did not name
      it. mini-SWE-agent's instance container lives two hours, so a run past the cap can no
      longer submit a patch: leaving the instance out, instead of counting it as unresolved for
      mini-SWE-agent, takes nothing from mini-SWE-agent. The phase check first asked for a grade
      for every run. It now asks what C-026 asks: a run of each contestant on every instance,
      and the instances without a grade within the share the claim allows.
    - **Server errors, counted.** Many of mini-SWE-agent's runs ended on the model server's
      error answer to a tool call it could not parse, after mini-SWE-agent's own retries. The
      evidence now also counts, for each contestant, the runs that a model server error ended
      (Stepswitch's `model_error`; for mini-SWE-agent, the exit statuses that name its model
      client's server-error exceptions), and what each resolved on the instances where no such
      error ended either run. The README table shows both. They were added after part of the
      results were seen and are descriptive: no claim reads them.
    - **Committed predictions.** One of Stepswitch's task branches held a docs build's output,
      which a command the agent ran wrote into the repository: sphinx-doc__sphinx-9698, whose
      prediction has 75 files and was resolved. The harness grades the whole branch, so the
      committed prediction keeps that output byte for byte, and the output includes a vendored
      script whose comments name a company. The repository hygiene scan (working rule 4, ADR
      0007) therefore reads every field of a committed prediction except its patch: the patch
      is a third-party repository's text with the agent's changes, kept so that the harness can
      grade it again. A task branch should not take in files that commands wrote; that is an
      open issue (PROGRESS.md, Phase 13).

## Consequences

- `ssw bench run --suite swebench-verified-mini --workers "router,mini-swe-agent:<endpoint>/
  <model>" --router-pool <endpoint>/<model>` reproduces a result on any machine with Docker and
  a model server; the manifest records the dataset revision, a hash of the rows, the harness
  and runtime versions and the images.
- A full run of both contestants takes most of a day on one workstation, because the model
  server answers one request at a time.
- The fixes in item 7 apply to every task, not only to SWE-bench.
