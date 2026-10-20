# Progress

Newest phase first. Each phase records what was done, what research changed, open issues and
actions that only the maintainer can take. Phase checks: `uv run python scripts/phase_check.py N`
(one phase) or `--upto N` (phases 0 to N).

## Phase 11: Frontier comparison

Status: done on 2026-10-20. `scripts/phase_check.py 11` passes 6 of 6 checks.
`uv run pytest -q`: 2028 passed, 6 skipped (among them the tests that need a small model on a
local server). `ssw prove --quick --phase 11` (1,038 s): twenty-two claims passing, C-021 to
C-023 among them for the first time, and C-016 pending CI.

SPEC §23 has ten phases. This one was added after them, at the maintainer's request: compare
Stepswitch on local models with Claude Code on Sonnet 5.5 and on Opus 5.5, for pass rate and
tokens (ADR 0034).

### Done

- **Claims first.** C-021 to C-023 were registered, with their tests and thresholds, before
  any comparison run:
  - C-021: the router on local models passes as many task runs as Claude Code on each model;
  - C-022: it uses fewer tokens per task (narrowed after the runs, below);
  - C-023: Claude Code through `ssw proxy` on local models passes as many task runs.

  The claim tests take each contestant's newest run per suite and compare the task runs all
  contestants share. They need every task of both suites with at least two seeds. They leave
  out infrastructure errors (usage limits, API errors, crashes), up to a tenth of the runs.
  Intervals are 95% cluster bootstraps over tasks.
- **Two new bench contestants** (`ssw bench run --workers`):
  - `harness:<name>` runs an external harness through `ssw proxy` on the router's pool.
  - `native:<harness>:<model>` runs the harness alone, on its own login and the named model.
    It gets one session with the first-attempt prompt the built-in agent gets, and no gates or
    retries. The bench's acceptance check scores it, as for every contestant.
- **Native runs of Claude Code** are opt-in by name. They are kept away from the user's
  setup:
  - `--safe-mode`: no CLAUDE.md, skills, plugins, hooks or MCP servers;
  - `--setting-sources project,local` and `--no-session-persistence`;
  - auto memory off, and never a resumed session.

  `start_native` refuses an adapter without native support, and offline settings. Harness
  runs through the proxy are isolated the same way. A run that ends on a usage limit or an
  API error, or with no result line, is an infrastructure error.
- **What every row records now:** tokens per model and hosted tokens, meaning tokens sent to
  a worker that is not local by the catalog's rule (SPEC §15).
  - Stepswitch's counts come from its ledger. A native run's counts are what the harness
    reports per model.
  - A native row also records the harness version, the model it reported, its turns, tool
    calls and reported cost.
  - Leaderboards and the dashboard gain median uncached and median hosted tokens. Run
    manifests record harness versions and which harness runs were isolated.
- **Pilot** (`bench/results/2026-10-19-mini-general-native-pilot`): three general tasks, one
  seed, both models. All 6 runs passed.
  - Median tokens: 92,442 on Sonnet 5.5 and 95,522 on Opus 5.5, of which 8,850 and 10,142
    were uncached.
  - About 91% of their input came from the hosted prompt cache.
  - Every run used the harness's own login, with no API key. None added a project entry or a
    saved session to the user's state; this was checked by counting entries, not by reading
    them.
- **Runs**: both mini suites, 21 tasks, 2 seeds, so 42 task runs per contestant. The local
  pool was gpt-oss:120b and qwen2.5:32b on this workstation's Ollama.

  | Contestant | Passed | All tokens | Uncached | Hosted | Median wall |
  |---|---:|---:|---:|---:|---:|
  | Claude Code, Sonnet 5.5 | 42/42 | 3,082,370 | 327,510 | 3,082,370 | 16.3 s |
  | Claude Code, Opus 5.5 | 42/42 | 3,944,626 | 394,194 | 3,944,626 | 21.0 s |
  | Claude Code through `ssw proxy`, local | 42/42 | 6,175,582 | 1,794,730 | 0 | 67.7 s |
  | Router, local, first run | 36/42 | 1,682,628 | 380,803 | 0 | 93.0 s |
  | Router, local, rerun (ADR 0035) | 42/42 | 1,767,412 | 400,665 | 0 | 86.9 s |

- **Router failures and their fix (ADR 0035):**
  - Five of the first run's six failures were one engine error. Ollama could not parse a
    gpt-oss tool call and returned HTTP 500. The client resent the same request twice, and the
    agent failed over to qwen2.5:32b, which Ollama stopped at its repeat limit. That left no
    worker. The sixth failure ran out of steps.
  - Now the client does not resend such a request. The agent asks the same worker once more
    through the fallback tool protocol.
  - The single rerun the ADR allowed passed 42 of 42, with 8 fallbacks in 7 task runs. Both
    runs are committed.
- **Claims:**
  - C-021 and C-023 pass: every contestant passed every task run.
  - C-022 failed as registered. Stepswitch used 22.3% more uncached tokens than Claude Code on
    Sonnet 5.5 (6.3% to 38.5% more) and 1.6% more than on Opus 5.5 (20.0% fewer to 16.4%
    more). ADR 0036 narrows it to all tokens and adds a check that no token went to a hosted
    model.
  - Narrowed, C-022 passes: 42.7% fewer tokens than on Sonnet 5.5 (28.7% to 55.3%) and 55.2%
    fewer than on Opus 5.5 (42.2% to 66.3%). The README shows the uncached result beside it.
  - C-011 now reads the router rerun. It is the newest optimized run with the same setup as
    C-011's baseline, which is the rule C-011's test follows. C-011's figure moved from 18.7%
    to 17.8% fewer uncached tokens, and it still passes.
  - C-018 failed on the first prove run, as it should have. The new claims' evidence recorded
    the bench command where C-018 needs the prove command that recomputes every number. Their
    evidence now records `ssw prove --claim`, with the bench command beside it, as C-011's
    does.

### What changed against the spec

- SPEC §25 rules out claims of beating frontier models on the hardest tasks. The three claims
  are scoped to the mini suites.
- C-022 counts all tokens, not uncached tokens (ADR 0036).

### Research

- Claude Code 2.1.296 (RESEARCH, "Verified during Phase 11"):
  - `--bare` needs an API key and skips the login.
  - `--safe-mode` turns off CLAUDE.md, skills, plugins, hooks and MCP servers, and keeps the
    login.
  - CLAUDE.md is read from the working directory and every parent. Without safe mode, a bench
    worktree inside a repository would read that repository's CLAUDE.md.
  - The result line's `modelUsage` gives tokens per model.
- Ollama 0.34.4 returns a tool call it cannot parse as HTTP 500, "error parsing tool call".

### Open issues

- **The mini suites are easy.** Every contestant passed every run, so C-021 and C-023 show
  parity on easy tasks only. Next is a harder suite: a SWE-bench Verified subset, after its
  license, format and harness needs are checked.
- Native runs get one session and no gates or retries. Each tool runs as it ships, as ADR
  0034 says, but a native run with the gates' retries would be a closer match.
- The results hold for Claude Code 2.1.296, these model IDs, Ollama 0.34.4 and this
  workstation.
- **Uncached tokens.** The local server's cache served 78% to 84% of the router's input. The
  hosted cache served about 91% of Claude Code's. Making the router's prompts more cacheable on
  local servers is open work.
- Claude Code through the proxy processed twice the tokens of native Claude Code, and its
  local cache hit rate was 68% to 73%.
- The local runs' median wall time was four to five times the native runs' on this machine.
- qwen2.5:32b can still stop on Ollama's repeat limit.
- Only the built-in agent falls back on an engine parse error; the proxy does not yet.
- The dashboard's new columns are not built locally (Node 20.12).
- The README's figure for uncached tokens against Opus 5.5 is a point estimate whose
  interval includes zero.

### Waiting on the maintainer

- As in Phase 10: the public repository and CI for C-016, the package index and the `v0.1.0`
  tag.

## Phase 10: Launch polish

Status: done on 2026-10-19. `scripts/phase_check.py 10` passes 8 of 8 checks.
`uv run pytest -q` with the CPU models present: 2001 passed, 7 skipped (among them the tests
that need a configured local model server). `ssw prove --quick --phase 10` (887 s): nineteen
claims passing, C-017 among them for the first time, and C-016 pending CI.

### Done

- **README** follows the outline of SPEC §24:
  - the pitch and the demo;
  - "Don't trust us, run `ssw prove`";
  - the quickstart with the installers;
  - running on your own repository, with a command table;
  - compatibility, how it works, features and the roadmap;
  - the generated claims;
  - how to contribute.

  Its only numbers are the generated ones (C-018). It names no ADR or version numbers in
  prose, since the integrity lint counts those digits too.
- **Docs site** (ADR 0032): Sphinx, MyST-Parser and Furo, in `docs/site`, which ADR 0006 set
  aside as public text.
  - Pages splice in the files they show when Sphinx reads them: the README's quickstart,
    how-it-works and claims sections; the task API; the science.toml reference; the
    compatibility matrix; and the examples with their files. Relative links are rewritten.
  - Those files joined `lint.public_docs`, and their unmarked absolutes were reworded.
  - The build makes no network requests. The Phase 10 check builds the site with `-W` and
    fails on any remote script, stylesheet, image, frame or font. The `docs` workflow builds
    it in CI and uploads the HTML.
- **Demo** (`docs/demo/demo.tape`, `scripts/record_demo.py`):
  - VHS v0.12.1 in its container image, pinned by digest.
  - Stepswitch is installed from a wheel with install.sh, and the toy project has its own
    `.venv`.
  - The demo policy pins planning and summaries to the tiny tier, the work to a large model and
    the review to a model of another family. Its only endpoint is the host's Ollama.
  - Each step line now ends with the task's running token count.
  - The recording: the tiny tier planned and summarized, gpt-oss:120b did the work and
    qwen2.5:32b reviewed. The task ended `done` after 13 steps and 3 switches, and the GIF is
    275 KB. A first recording with the two large models' roles swapped stalled on a model
    error (the server's repeat limit), and the tiny model did the work instead. So the policy
    gives the work to gpt-oss:120b.
- **Installers** (ADR 0033):
  - `install.sh` (POSIX sh) and `install.ps1` (PowerShell 5.1) pin uv 0.13.1 and check what
    they download: uv's installer script, or for Windows uv's release archive.
  - They run `uv tool install stepswitch==0.1.0` (`STEPSWITCH_PACKAGE` overrides it) and
    `ssw prefetch`, and print the bin directory if it is not on PATH.
  - Neither edits a shell profile or the user's environment. A real `install.ps1` install
    succeeded on this Windows machine.
- **`ssw models pull` installs the engine** its GGUF files need: the pinned llama.cpp build,
  with consent. It reports the install's seconds in its JSON.
- **Release workflow**: a `vX.Y.Z` tag runs three jobs.
  - build: check the tag against the package and installer versions, build the dashboard,
    then the sdist and the wheel built from it, and write the release notes with git-cliff;
  - publish: trusted publishing, with attestations;
  - release: `gh release create` with the packages and the installers.
  - Every action is pinned to a commit, and gh's telemetry is off.
- **Test matrix**: the `test` workflow runs the unit tests on Python 3.11 to 3.15, as ADR 0002
  promised. On this machine the unit tests pass on Python 3.15.0rc3 (1699 passed, 6
  skipped).
- **Examples**: five configurations (CPU laptop, Ollama workstation, remote endpoint, science
  project, slow lane). The real configuration loader validates each one in a test.
- **Community files**: MIT license, contributing guide, security policy (private
  vulnerability reporting), Contributor Covenant 3.0, issue forms and a pull request template.
- **C-017** (ADR 0033): the scripted quickstart in `python:3.12-slim` with 8 CPUs, 8 GB of
  memory and no GPU.
  - The run took 3.12 of the 5 minutes allowed. Installing took 28 s, `ssw init` 0.4 s,
    the llama.cpp install 2.9 s and the first task 156 s.
  - The task ended `done` after 9 steps, with the impacted and full test gates passing.
  - The model files are mounted read-only from the host, so their download is excluded,
    as the claim's scope says.
- **Fixes the C-017 measurement led to.** The first three timed runs took 5.28, 5.40 and 5.83
  minutes, for reasons other than the CPU:
  - llama.cpp counts the host's physical cores and ignores the container's CPU limit. The
    adapter now passes `--threads` for the cores the process may use.
  - A task's worktree has no `.venv`, so the gates and the agent's `test` tool ran the
    project's tests with Stepswitch's own interpreter. Under `uv tool install` that
    interpreter has no pytest, so the tests were skipped while the model kept retrying them.
    Worktrees now use the environment of their checkout, with the worktree's `src/` first on
    PYTHONPATH, and the agent's commands get the gates' environment.
  - The model named `test_calc.py` for `tests/test_calc.py` and repeated the call four times.
    The `test` tool now resolves a test file named without its directory, as `read` and
    `edit` already did.

### Research

- MkDocs 2.0 is a pre-release rewrite with no plugin API, and Material for MkDocs is in
  maintenance until 2027-05-05. Zensical is still 0.0.x, and its default theme loads fonts
  from a CDN. Sphinx 9.1 needs Python 3.12, so the lock forks on 3.11.
- uv's `install.ps1` checks no hash, and its `install.sh` skips the check without
  `sha256sum`. Both installers therefore check what they download themselves.
- The GitHub CLI sends telemetry by default from 2.91.0, CI included.
- llama.cpp b11669 counts physical cores from sysfs for the whole machine.

### Open issues

- **C-016** needs the repository on a public host with CI. The prove workflow's three-OS run
  produces its evidence.
- The installers and the README's install lines point at "the installer attached to each
  release". They get a URL when the repository has its public home, and so does a published
  docs site.
- In the C-017 container the cgroup's memory peak is within 1.2 MB of its 8 GB limit. The
  cgroup also counts page cache, including the 4.8 GB of model files that llama.cpp maps, so
  the peak shows that the limit was reached, not that the processes needed it. Resident memory
  is not measured, so the headroom is unknown.
- The README has no leaderboard image (SPEC §24): an image's numbers are outside the integrity
  lint that C-018 relies on. The bench results and their leaderboards are in `bench/results/`.
- The API tests can show one deprecation warning from the locked Starlette's test client,
  which asks for `httpx2` in place of `httpx`. It affects only the tests.
- The dashboard needs Node 22.12 or later to build. CI uses Node 24; this machine has 20.12, so
  local packaging checks used an earlier build.

### Waiting on the maintainer

- Create the public repository, enable private vulnerability reporting, and push. Then let
  the prove, test and docs workflows run, and record C-016.
- On the package index:
  - claim the `stepswitch` project;
  - add the repository's release workflow as a trusted publisher, with the `pypi`
    environment;
  - give that environment required reviewers.
- Tag `v0.1.0` after setting the package version (`scripts/release_check.py` checks it
  against the tag and the installers).

## Phase 9: Slow lane and zero-egress

Status: done on 2026-10-19. `scripts/phase_check.py 9` passes 10 of 10 checks. `uv run pytest -q`
with the CPU models present: 1962 passed, 9 skipped (among them the tests that need a
configured local model server). `ssw prove --quick --phase 9` (687 s): eighteen claims
passing, C-006 among them for the first time, and C-016 pending CI.

### Done

- **The slow lane** (ADR 0030).
  - The SL tier is pin-only: it never joins the interactive ladder or best-of-N.
  - **AirLLM** runs behind an out-of-process HTTP shim over its transformers wrapper. It loads
    local directories only (no hub IDs), and macOS is refused in favour of MLX.
  - **KTransformers** is an SGLang profile (`sglang-kt`). It checks the CPU features each
    `--kt-method` needs, and its readiness check never runs a generation.
  - **The job queue** lives in the project state: `ssw jobs submit|list|show|run|cancel`, and
    `GET /api/jobs`.
    - A claimed job is requeued when its worker goes stale. A job times out after six hours.
    - Cancelling a running task job stops its task. The job records the task's id before the
      task starts.
  - **Review jobs** add their comments to the original task's events and to the dashboard.
    `ssw run --slow-review` queues one when a task is done.
- **Zero egress** (ADR 0031).
  - Offline mode is enforced per task at the transport: only loopback connections. Child
    processes get the offline switches of each tool and harness.
  - Local endpoints that forward to a hosted service (cloud model tags, proxies) count as
    remote.
  - The audit records each connection with its bytes, and each child process. See
    `ssw audit --processes --task --blocked`.
  - Secrets are redacted in requests to non-local endpoints, by pattern and by entropy.
  - One deny list (`privacy.deny_paths`) applies to the built-in agent and every harness.
  - `ssw prefetch` downloads what the context services use offline, and only when asked.
- **C-006**: the offline flow (`ssw init`, then `ssw run --offline` on the toy repository with a
  local scripted model and a configured hosted endpoint, then `ssw audit`) runs under a
  recorder that wraps sockets, name lookups and process starts and refuses non-local
  traffic.
  - On this machine the task made 8 connections, all to the local model, and none was
    refused. The audit's destinations equal the observed ones.
  - CI repeats the flow under OS-level network isolation on Linux.

### Open issues

- The AirLLM and KTransformers adapters are tested against fakes built from their source and
  documentation. This machine has neither AirLLM nor a Linux host with AMX.
- On this Windows machine C-006 has only the in-process recorder, without OS-level network
  isolation. The isolated run is CI's Linux job, which needs the remote (C-016).

## Phase 8: Proxy and external harness adapters

Status: done on 2026-10-19. `scripts/phase_check.py 8` passes 6 of 6 checks, the live run among
them. `uv run pytest -q` with the CPU models present: 1825 passed, 8 skipped (among them the
tests that need a configured local model server). `ssw prove --quick --phase 8` (702 s):
seventeen claims passing and C-016 pending CI. No claims are assigned to this phase.

### Done

- **`ssw proxy`** (ADR 0004, ADR 0029) answers the chat-completions, responses and messages
  formats on one local port, with a token per run.
  - It routes each request as a step of the caller's task and switches workers only at a new
    user turn.
  - Bound tasks, route-only decisions (`POST /stepswitch/v1/route`) and budget refusals (429)
    keep a harness inside the task's budget.
  - Inbound credential headers are neither logged nor forwarded.
- **Harness adapters**: Claude Code, Pi, Oh My Pi, OpenCode, Kimi Code and generic ACP agents.
  - Each run gets its own proxy, token and state directory, and a minimal environment holding
    none of the user's keys. Each harness's egress switches are set.
  - Pi and Oh My Pi get small extensions (`adapters/pi`, `adapters/omp`) on a shared core.
  - The ACP client is the project's own.
- **`ssw run --harness <name>`** runs a task in an external harness. Its steps, ledger and
  gates appear like a built-in run's.
- **Contract tests** run every adapter against its fixtures. Line-protocol transcripts are
  parsed and also replayed through the subprocess path, and ACP sessions run against a strict
  mock agent.
- **A live run.** `ssw run --harness claude-code` (Claude Code 2.1.296 on Windows, one local
  model behind the proxy) fixed the toy repository's bug and passed the gates. The run is
  recorded as the `recorded-live` contract fixture, with its paths replaced.
- **Fixes the live runs led to.**
  - The harness's Bash tool is a POSIX shell on Windows too, so the model types the
    interpreter path with forward slashes. The allow rule held it with backslashes, so the
    test command was refused. The prompt and the rules now use forward slashes, and the rules
    allow both spellings.
  - With a prompt that only said the tests would be checked afterwards, the model ran a bare
    `pytest`, which the rule refused and which would have run outside the project's
    environment. The prompt now asks for the test command exactly as written, and the next
    live run ran it.

### Open issues

- The other adapters' fixtures come from documentation and source. Pi, Oh My Pi, OpenCode,
  Kimi Code and a generic ACP agent are not installed on this machine, so none has a live
  recording yet.
- The prompt wording that got the test command run was checked in single live runs, not
  measured over many.

## Phase 7: Learning router, fork and replay

Status: done on 2026-10-19. `scripts/phase_check.py 7` passes 5 of 5 checks. `uv run pytest -q`
with the CPU models present: 1769 passed, 7 skipped (among them the tests that need a
configured local model server). `ssw prove --quick --phase 7` (752 s): seventeen claims
passing, C-014 and C-015 among them for the first time, and C-016 pending CI.

### Done

- **The learning router** (ADR 0026): contextual Thompson sampling.
  - It learns per worker a Beta posterior of success and running means of normalized time and
    tokens. It works on five context levels that back off to coarser ones.
  - It draws one sample per worker per task, seeded by the task, and credits workers by their
    share of the work steps.
  - Modes: `on`, `frozen` and `off`. Bench runs default to `off`, and a replay never learns.
  - `ssw router stats` shows the statistics. `--export` writes aggregates without endpoint
    names or single-task contexts.
- **C-015**, scoped to the simulation harness (`router.simulate`): synthetic worlds of four
  workers. The learning router is compared with the static router, the best fixed worker and
  random routing on the same draws.
  - Over 30 worlds of 4,000 tasks, the mean total regret was 118 for the learning router,
    194 for the static router, 167 for the best fixed worker (chosen knowing the world) and
    670 for random routing. The learning router's regret per task fell from 0.045 in the
    first quarter to 0.021 in the last.
  - Per world it had less regret than the static router in 18 of 30 and than the best
    fixed worker in 15 of 30, so the claim rests on the mean, not on every world.
- **Replay and fork** (ADR 0027).
  - Runs record whole model answers and tool outputs.
  - `ssw replay <task>` reruns a recording. It applies edits, commands and tests again for
    their effect on the files, answers from the recording, runs the gates for real, and
    reports each departure from the recording.
  - `--from-step k` and `--worker` fork a run: the recording plays up to call k, and live
    models take over from there.
  - The API and the dashboard can start replays and forks.
- **C-014** replays seven recorded fake-worker runs and compares their diffs and gate results
  with the recordings. All 7 reproduced them, a failover, a handoff brief, a stop at the time
  budget and a best-of-N run among them, with every model call answered from the recording.
- **Fixes found along the way.**
  - The `test` and `run` tools drop stale bytecode first. An edit of the same size within the
    same second as the last compile would otherwise run the old code. The gates also leave
    generated files out of a task's change.
  - Handoff records calibrate the handoff recovery, so they now carry the router's estimates
    of success (the posterior mean, with the step shift) rather than the learning router's
    samples. A sample can rank a weaker worker first, which dropped the handoff from
    calibration at random and made one unit test flaky (ADR 0026).

## Phase 6: Best-of-N, disagreement and cross-family review

Status: done on 2026-10-19. `scripts/phase_check.py 6` passes 7 of 7 checks. `uv run pytest -q`
with the CPU models present and the dashboard built: 1742 passed, 6 skipped.
`ssw prove --quick --phase 6` (615 s): fifteen claims passing, C-012 and C-013 among them for the
first time, and C-016 pending CI.

### Done

- **Cross-family review** (SPEC §8.5, ADR 0025).
  - Review steps avoid the model families that did the task's work. When the current rung
    has no other family, the review moves to the nearest rung that has one and records the
    move. With no other family anywhere, the author's family reviews, with a note.
  - The reviewer answers the §8.5 schema as a strict response format. Reading is lenient:
    fenced or prefixed answers, common severity aliases and the Phase 2 plain-text answer are
    all accepted. A live check on both local model families conformed on correct and wrong
    diffs (docs/RESEARCH.md).
  - This applies to every task, so a single-candidate task's review is cross-family too.
- **Best-of-N** (SPEC §8.3). `ssw run --best-of N` (or `auto`, the SPEC's table by difficulty)
  runs N candidates one after another, each in its own worktree, on one model family each,
  within the task's one budget.
  - Selection: the gates, then the cross and probe tests passed, then a non-blocking review,
    then the change size band, the review score and the plan order. The SPEC puts diff size
    straight after the gates; ADR 0025 explains the change.
  - Every candidate with changes is committed to its own branch, and the winner's diff is the
    task's diff.
- **The disagreement signal** (SPEC §8.4). When at least two candidates pass, the tests each
  one added run against the others' code, and a probe test written from the instruction alone
  runs against all of them. In the science track every golden case is compared across the
  candidates. Candidates that behave differently form separate clusters, and the task is
  flagged.
  - Policies (`--on-disagreement`): `flag` (the default; the change is kept but not
    applied), `tests` (targeted tests decide between the clusters), `escalate` (one more
    candidate) and `human`.
- **The `best-of-n` bench contestant** draws its candidates from the run's worker pool, so a
  bench run with the pool's workers as single contestants gives the C-012 comparison.
- **C-012**: on both mini suites, best-of-N across the two local model families against each
  of them alone, two seeds per task (`bench/results/2026-10-19-mini-*-best-of-n`).
  - mini-science: best-of-N passed 12 of 12 task runs, the best single worker 12 of 12 and the
    other 7 of 12.
  - mini-general: best-of-N passed 29 of 30, the best single worker 29 of 30 and the other
    16 of 30.
  - Both candidates passed in 15 of the 30 mini-general runs and 8 of the 12 mini-science
    runs. The disagreement signal flagged 2 of those 15 and none of those 8.
  - Best-of-N's mean wall time was 1.4 times the best single worker's on mini-general and 2.0
    times on mini-science.
- **C-013**: seeded tasks, each solved by two scripted candidates from different families
  that both pass the gates (`tests/claims/disagreement_fixture.py`).
  - In an ambiguous task each candidate takes one reading of the instruction, or, in the
    science track, the candidates' outputs differ from each other by more than the tolerance.
  - In an unambiguous task the code differs but the behaviour does not.
  - All 4 ambiguous tasks (3 general, 1 science) were flagged, and none of the 4 unambiguous
    ones. Every task had two passing candidates.

### What changed against the spec

- ADR 0025: best-of-N is opt-in (`--best-of`), because the SPEC's table as the default would
  run every task of difficulty 3 or more two or three times, even on a laptop with one tiny
  model. Candidates run one after another, since one server usually serves every worker.
- The selection order puts the tests and the review before the diff size (ADR 0025, item 7).

### Open issues

- Probe tests are model output and can be wrong. They count only when they pass on some
  candidate, so they can split candidates but never fail a task on their own.
- With fewer families than candidates, candidates reuse a family with another seed, which is
  weaker evidence of agreement. The plan records each reuse.
- **C-012 has no margin on mini-general.** Best-of-N tied the best single worker, so one more
  failure would break the claim. Its one failure was a model error in the gpt-oss candidate,
  on a task run that gpt-oss alone passed. A model error takes a worker out for the rest of
  the task, and a candidate limited to one family has no other worker. The bench removes run
  directories unless `--keep` is given, so the error itself was not kept.
- The two flags on real model output were not checked by hand. The bench records whether a
  task was flagged, not the clusters, so false flags are not measured yet.

### Waiting on the maintainer

1. C-016 (Phase 1, unchanged): add a remote and let the `prove` workflow run on the three
   hosted platforms.

## Phase 5: Token and cache optimization

Status: done on 2026-10-19. `scripts/phase_check.py 5` passes 9 of 9 checks (`--upto 5`: 43 of 44, the one failure
being C-016). `uv run pytest -q` with the CPU models present and the dashboard built:
1693 passed, 6 skipped. `ssw prove --quick --phase 5` (595 s): thirteen claims passing,
C-003, C-010 and C-011 among them for the first time, and C-016 pending CI.

### Done

- **Cache-aware switching** (SPEC §7.2, ADR 0024).
  - A prefix registry per server predicts how much of the next prompt that server has cached.
    The cached tokens each server reports calibrate its prediction, and
    `ssw calibrate --cache` seeds it.
  - A switch costs the uncached context the new server has to read. Staying costs something
    too: the current server has to read the tool results it has not seen yet.
  - Above 24,000 uncached tokens a switch sends a HANDOFF brief instead of the transcript. The
    new worker's chance of success after a brief uses the learned share of the gap that a brief
    recovers.
  - C-003: a property test over 4,000 random scenarios. Of these, 2,597 were cost-model
    decisions: 455 switches (51 of them with a brief) and 2,142 stays. Each one switched
    exactly when the expected gain exceeded the cost plus the margin. The other scenarios were
    forced switches (1,127) or held steps (276), and they followed their own rules.
- **HANDOFF v0.1 briefs** (SPEC §9; `spec/handoff-v0.1.md` and its JSON Schema).
  - The agent collects the facts from its work log: task, files, commands, the failing test and
    the plan. A local level-0 model adds what was done, the decisions and the open questions,
    under a JSON schema. Without that model the facts alone make the brief.
  - Each brief is validated against the schema and trimmed to at most 1,500 tokens, counted
    with the receiving model's tokenizer. Local paths are rewritten, and every brief is
    recorded in the run's `handoffs.jsonl`.
  - Briefs also replace the older turns when a transcript reaches 60% of its budget
    (compaction).
  - C-010: 28 briefs from the bench relay runs (17 general, 11 science). All are valid; the
    largest has 1,213 tokens, counted with the receiving model's tokenizer file.
- **Token techniques in the agent loop** (SPEC §13): TK1 to TK4, TK6 to TK8, TK10 and TK11.
  Phase 2 already shipped TK5, TK9 and TK12.
  - Stable prefix; tree-sitter repository map; read dedup; distillation of long tool outputs,
    with the whole output still readable as `@output/<id>`.
  - Pruning of superseded results in batches, and compaction; briefs on switches and failovers;
    a review answer under a JSON schema; output caps and reasoning effort per role.
  - Prose compression is optional and off by default. `extras/llmlingua-server/` is a
    reference compression server that runs on the CPU.
  - One switch turns all of them off: `ssw run --no-optimize`, `[context] optimize` or
    `ssw bench run --baseline`. With it off, the agent behaves like the Phase 2 agent.
- **Bench and calibration commands.**
  - `ssw bench run --baseline` runs the Phase 2 configuration. `--relay FIRST,SECOND` adds a
    contestant that hands every task from one worker to the other, with a brief, at a fixed
    step.
  - `ssw bench calibrate` learns per-model success rates and the handoff recovery from bench
    results. For this machine it stored a recovery of 0.53 from the 28 relay handoffs.
  - `ssw models pull --tokenizers` fetches tokenizer files, pinned by revision and sha256.
- **C-011, two bench rounds.** Both suites (mini-general with 15 tasks, mini-science with 6),
  two seeds, and the same router pool (the local reasoning model and the 32B model) as the
  baseline.
  - Round 1 (commit 7cb9ba4) used fewer tokens than the baseline but more uncached ones: on
    mini-general 1,616,419 tokens against 1,831,708, but 557,962 uncached against 347,371.
  - The cause was the reasoning model's chat template (docs/RESEARCH.md). The template writes
    the reasoning effort into the first system block, and it renders reasoning only after the
    last user message. Changing the effort between steps, and nudges in the middle of a
    transcript, both broke the server's prompt cache. ADR 0024 decisions 9 and 14 fix this.
  - Round 2 (commit bc39881):

    | Suite | Passed (baseline, optimized) | Fewer tokens (95% interval) | Fewer uncached tokens (95% interval) |
    |---|---|---|---|
    | mini-general | 28/30, 29/30 | 20.4% (−27.3% to 52.0%) | 16.5% (−3.2% to 33.0%) |
    | mini-science | 12/12, 12/12 | 24.9% (14.0% to 34.0%) | 24.1% (14.7% to 32.9%) |
    | Both | 40/42, 41/42 | 21.3% (−13.6% to 47.8%) | 18.7% (3.9% to 30.9%) |

  - Only the uncached reduction's interval over both suites lies above 0, so C-011 now counts
    uncached tokens, the second step of an order fixed before the round (ADR 0028).
- **A missing path stands for the only file of its name.** The prove run found the CPU model
  without native tool calls (C-005) asking for `src/calc.py` eight times. Each answer said
  "Did you mean: calc.py?", and the run stopped as stuck. A `read`, or an anchored `edit`, of a
  path that does not exist now goes to the one file in the repository with that file name, if
  there is exactly one, and the answer says so. Whole-file writes still create the path that
  was asked for. On the rerun the model asked for `src/calc.py` again, read and edited
  `calc.py` through it, and finished in six steps with the tests passing.

### What changed against the spec

- ADR 0024 records the Phase 5 decisions:
  - one switch for every technique;
  - a cost for staying as well as for switching;
  - the brief threshold of 24,000 uncached tokens;
  - one reasoning effort for all work steps of a transcript;
  - a text answer after a tested change finishes the work, with no nudge;
  - prose compression off by default.
- ADR 0028: C-011 counts uncached tokens. Its text and README number changed with it.
- Research corrected three SPEC assumptions (docs/RESEARCH.md):
  - Ollama has no tokenize route, so counts use tokenizer files fetched in advance.
  - tiktoken and the tree-sitter language pack download files on first use, so pinned
    tokenizer files and grammar wheels replace them.
  - The default LLMLingua model is a 7B GPU model; the reference server uses the small
    LLMLingua-2 checkpoint on the CPU, out of process.
- The router never switches by choice on the mini suites, so the briefs for C-010 come from
  the relay contestant.

### Open issues

- **C-011 per suite.** The uncached reduction is significant over both suites and on
  mini-science, but mini-general's own interval still includes 0 (−3.2% to 33.0%). The
  reduction in all tokens is not significant (−13.6% to 47.8%). More seeds would narrow both.
- **Prose compression** has no bench evidence: the mini suites read almost no prose. It stays
  off until a suite with documentation reads exists.
- **The date in the first system block.** The reasoning model's template also writes the
  current date into the first block, so a transcript that runs past midnight loses its cached
  prefix once.
- **C-005 is one run of a tiny model.** The CPU model without native tool calls passes 3 of 30
  mini-general runs in the bench. The claim test is one run on the toy task, so it can still
  fail by chance.
- **Node on this machine** (unchanged): building the dashboard needs Node 22.12 or later.

### Waiting on the maintainer

1. C-016 (Phase 1, unchanged): add a remote and let the `prove` workflow run on the three
   hosted platforms.

## Phase 4: Dashboard and task API

Status: done on 2026-10-16. `scripts/phase_check.py 4` passes 5 of 5 checks (`--upto 4`:
34 of 35, the one failure being C-016). `uv run pytest -q` with the CPU models present and the
dashboard built: 1565 passed, 6 skipped (the same skips as in Phase 3). `ssw prove --quick --phase 4` (617 s): the ten claims of Phases 1 to 3 passing, C-016 pending CI. No
claims are assigned to Phase 4. With this phase, Stepswitch reaches the v0.1 launch candidate of
SPEC §23. The launch candidate also needs `ssw prove --quick` to pass on all three hosted
platforms (C-016).

### Done

- **Task API** (docs/API.md, ADR 0023).
  - `ssw dash` serves the SPEC §17 routes and a read model for every view on 127.0.0.1 (port
    7777 by default).
  - **Tasks:** list, detail, trajectory events, create and cancel.
  - **Work and routing:** workers, live agents, and router decisions with classifier features.
  - **Ledger:** grouped by model, task or day, with a reference-cost counter.
  - **Models:** endpoints with probe profiles and on-request reachability checks; starting and
    stopping managed models.
  - **Results and records:** bench leaderboards with charts, claims, the compatibility matrix,
    the egress audit log, and science reports with plots.
  - **Live feed:** a Server-Sent Events stream.
- **Access control.**
  - A per-session token from the printed link moves into a strict `HttpOnly` cookie, one per
    port. Scripts can send a Bearer header instead.
  - Other `Host` headers and cross-site posts are refused before routing.
  - Every answer carries a Content-Security-Policy and related headers.
  - Answers never contain keys, tokens, server log paths or remote endpoint URLs.
  - Files from a run directory are served only from that directory. Images display inline;
    everything else is sent as text or as a download.
- **Running and cancelling tasks.**
  - Submitted tasks run on a thread pool through `run_task`, one at a time by default
    (`--max-tasks`). A task always ends with a final status.
  - A cancel request is a row in the state database (state migration 3). The budget tracker
    reads it at every step boundary, so the task stops whichever process runs it. This covers
    tasks started with `ssw run` in a terminal too.
  - Ctrl+C on `ssw dash` lets its tasks stop at their next step. A second Ctrl+C, or SIGTERM,
    records them as stopped and exits.
- **Live updates.** The stream polls the state database twice a second, so the dashboard
  follows tasks from any process. A reconnecting client resumes exactly after
  `Last-Event-ID`.
- **Dashboard** (`dashboard/`; React 19.3, Vite 8.3, TypeScript 7.0).
  - Eleven views: a task board with a new-task form and cancel buttons, task detail, agents,
    router, ledger, leaderboard, trajectories, endpoints with start and stop, compatibility,
    audit, science with plots, and claims.
  - Updates arrive live. There are light and dark themes, and the layout works at phone width
    (320 px) without horizontal scrolling.
  - A mock API server (`npm run mock`) lets the dashboard run without the backend.
- **Browser smoke test** (`tests/e2e`; Playwright 1.64 with the installed Chrome).
  - It covers signing in through the link, all eleven views with no script errors, and
    starting a task from the page and cancelling it there.
  - CI builds the dashboard with Node 24 and runs the test. In CI a missing prerequisite is a
    failure, not a skip.
- **Packaging.** A wheel build hook ships the built dashboard as `stepswitch/_dashboard`.
  `ssw dash` serves it from there, or from `dashboard/dist` in a source checkout.
- **Checked on real data.** A copy of this project's state database held 102 tasks, 4,308
  trajectory events and 1,199 ledger rows. Every route answered in 2 to 140 ms. The agents
  view showed a bench task that was running in another process. `ssw dash` was also run end to
  end as a separate process: the printed link signed in, the API refused requests without the
  token, and Ctrl+Break stopped it cleanly.

### What changed against the spec

- ADR 0023 decides several details:
  - The token cookie is named per port.
  - The live feed polls the state database, so tasks from other processes appear.
  - Cancel requests live in the state database.
  - The server writes the event stream itself rather than using the web framework's SSE
    support.
- The dashboard build is not copied into `src/stepswitch/api/static/` (ARCHITECTURE §12,
  updated). A source checkout serves `dashboard/dist`, and wheels carry the build through the
  build hook.
- The trajectory viewer's fork and replay arrive with Phase 7. The dashboard shows them as not
  yet available.

### Open issues

- **Tasks orphaned by a dead process.** If the process running a task dies, the task stays
  `running`. Cancelling it records the request, but nothing acts on it. Recognising dead tasks
  needs a process heartbeat.
- **Large router answers.** The default router answer (200 decisions with their candidates) is
  about 675 KB on this project's data. That is fine locally; it needs paging if projects grow.
- **npm pins.** Newer vite, react and plugin-react releases become eligible between 2026-10-19
  and 2026-10-28 under the 14-day rule (docs/RESEARCH.md).
- **Node on this machine.** The host's Node 20.12 is too old for Vite 8. The dashboard was built
  with a portable Node 24.21 in the session's scratch space. Building the dashboard needs
  Node 22.12 or later.

### Waiting on the maintainer

1. C-016 (Phase 1, unchanged): add a remote and let the `prove` workflow run on the three
   hosted platforms. Its new `dashboard` job builds the dashboard and runs the browser smoke
   test there as well.

## Phase 3: Bench, leaderboard, compatibility matrix

Status: done on 2026-10-16, except C-016, which still needs hosted CI (Phase 1, maintainer
action 1). `scripts/phase_check.py 3` passes 6 of 6 checks (`--upto 3`:
29 of 30, the one failure being C-016). `uv run pytest -q` with the CPU models present: 1520 passed,
6 skipped (C-016 pending, and tests that need an Ollama server with a small model, ripgrep, POSIX
file modes or `STEPSWITCH_LIVE_HUB=1`).
`ssw prove --quick --phase 3` (639 s): C-001, C-002, C-004, C-005, C-007, C-008, C-009,
C-018, C-019 and C-020 passing; C-016 pending CI.

### Done

- **Bench.**
  - **Suites.** `mini-general` has 15 tasks: 14 bug fixes and small features, plus `calc-bug`,
    the claims' toy task. `mini-science` has 6 numerical tasks on synthetic spectroscopy and
    signal code. Each task has golden patches and protected paths.
  - **Runner.** Each run gets an independent acceptance check on a fresh copy of the task.
  - **Metrics.** Leaderboards in Markdown, JSON and SVG carry cluster-bootstrap confidence
    intervals over tasks.
  - **Merging and rescoring.** `ssw bench report` merges runs and rescores rows written under
    an earlier rule. A run passes when every acceptance command exits with 0 on a fresh copy
    with the run's diff applied and the suite's protected paths restored. Changed tests are
    counted separately.
  - **Router pool.** A task can route within a named pool (`--router-pool`).
- **Leaderboards.** These are the first real numbers, measured on the development workstation
  (32 CPU threads, four 48 to 51 GB GPUs). Every contestant ran each task with two seeds; the
  intervals are 95%.

  `bench/results/2026-10-16-mini-general` (15 tasks):

  | Contestant | Passed | Pass rate (95% interval) | Median time | Median tokens |
  |---|---|---|---|---|
  | gpt-oss:120b (GPU) | 29 of 30 | 97% (90% to 100%) | 57.8 s | 36,202 |
  | router over gpt-oss:120b and qwen2.5:32b | 28 of 30 | 93% (83% to 100%) | 49.2 s | 35,392 |
  | qwen2.5:32b (GPU) | 17 of 30 | 57% (37% to 77%) | 58.6 s | 11,760 |
  | Qwen3-4B-Instruct-2507 (CPU) | 9 of 30 | 30% (10% to 50%) | 69.6 s | 18,563 |
  | Phi-3.5-mini-instruct (CPU, fallback protocol) | 3 of 30 | 10% (0% to 27%) | 451.2 s | 42,674 |

  `bench/results/2026-10-16-mini-science` (6 tasks):

  | Contestant | Passed | Pass rate (95% interval) | Median time |
  |---|---|---|---|
  | gpt-oss:120b | 12 of 12 | 100% | 36.0 s |
  | router over the same two models | 11 of 12 | 92% (75% to 100%) | 45.9 s |
  | qwen2.5:32b | 6 of 12 | 50% (17% to 83%) | 88.0 s |

  Prompt-cache hits were 76% to 89% of input tokens for every contestant.
- **Engine adapters** (ADR 0020).
  - vLLM, SGLang, MLX-LM and a small transformers wrapper start from the machine's
    configuration.
  - Every child process runs with telemetry and downloads off.
  - No key ever appears on a command line.
  - `ssw models serve` names every engine.
- **Compatibility matrix** (ADR 0021).
  - `ssw probe --ci-set` probes the CI model set on the llama.cpp server, and
    `ssw probe --render-matrix` writes `docs/COMPATIBILITY.md`.
  - All four CI models pass the probe suite:
    - Qwen3-4B calls tools natively.
    - Phi-3.5-mini and SmolLM2-1.7B call tools through the fallback protocol.
    - Granite-3.3-2B calls tools natively, with Stepswitch parsing the calls (next item).
- **Tool calls parsed from reply text** (ADR 0022). Chat-completions endpoints can parse
  native calls from the reply text in the model's own syntax. This applies when the server
  leaves them there, as the llama.cpp server does for Granite 3.3. It can be set per endpoint,
  per model or per registry recipe, and the probe, the catalog and the matrix all record it.
- **Token accounting** (C-019). The toy task ran on both CPU models, 28 model calls in all.
  The ledger matched the llama.cpp server's own counters exactly for input, cached input and
  output tokens.
- **README numbers** (C-018). README claim lines render their numbers from evidence files that
  record the command, and only `ssw prove` writes committed evidence (ADR 0019).

### What changed against the spec

- ADR 0019: claim tests write committed evidence only under `ssw prove`. A plain test run
  writes the same files to a scratch directory.
- ADR 0020: transformers runs through a small wrapper instead of its own serve command. vLLM
  and SGLang are Linux-only, and MLX-LM is arm64 macOS only. Elsewhere the adapters say why and
  suggest an endpoint (WSL2 or a container).
- ADR 0021: the probe verdict. A probe passes on basic chat, system prompt, usage, formats
  served and one tool path; all other checks are informational. The CI model set has four
  families on CPU.
- ADR 0022: SPEC §4.4's "parse raw text" now covers chat-completions endpoints whose server
  leaves calls in the text.

### Open issues

- **The router did not switch models within a task.**
  - Its priors favour gpt-oss:120b for every step type. It chose that model for 95% of steps,
    and the switch cost (re-reading the context) never paid off.
  - The other 5% of steps went to qwen2.5:32b, always right after the inference server failed
    to parse a gpt-oss:120b tool call. These failovers saved those tasks, but they are not
    counted as switches.
  - The bench therefore shows routing as failover, not yet as step-level model choice. Mixed
    CPU and GPU pools, calibrated per-step priors and cache-aware switch costs are Phase 5
    work, and a learned policy is Phase 7.
- **Qwen3-4B's 30% understates the model.** 20 of its 30 runs ended with `no_worker`. The
  model's managed server is shared with its router role and started with a 4,096-token
  context, so agent turns overflowed it. Context sizes per role (or one server per role) come
  with Phase 5. The runs were not repeated.
- **Some measurements are rough.**
  - The CPU runs shared the machine with the GPU runs, so their times are upper bounds.
  - Phi-3.5-mini needs 451 s per task (median) on this CPU, and 3 of its runs hit the
    900-second budget.
  - The mini-general single-model runs were made at commit `cd9ffff`, before the pool and
    scoring changes, and rescored under the current rule (4 qwen2.5:32b verdicts changed).
    The router runs are from commit `4766b79`.
- **Python engines are untested live.** Their adapters are tested with fakes; no model was
  served through vLLM, SGLang, MLX-LM or transformers on this Windows machine.
- **MLX-LM image URLs.** MLX-LM's server fetches image URLs itself. Keeping image URLs away
  from it in the compatibility layer is still open (ADR 0020).
- **Forced tool choice.** It is "no" for every CI model on the llama.cpp server (b11669).
- **Long Windows paths.** On Windows, bench workspaces under a long project path can exceed the
  260-character path limit. The benches ran from a short worktree path; long-path support
  needs to be on, or the project should sit near the drive root.

### Waiting on the maintainer

1. C-016 (Phase 1, unchanged): add a remote and let the `prove` workflow run on the three
   hosted platforms.

## Phase 2: Built-in agent, router v1, gates, tracks

Status: done on 2026-10-16, except C-016, which still needs hosted CI (Phase 1, maintainer
action 1). `scripts/phase_check.py 2` passes 5 of 5 checks. `uv run pytest -q`: 1273 passed,
6 skipped (C-016 pending, and live or platform tests that need a local server with a small
model, a local server with a probe model, ripgrep, POSIX file modes or `STEPSWITCH_LIVE_HUB=1`).
`ssw prove --quick --phase 2` (364 s): C-001, C-002, C-004, C-007, C-008, C-009 and C-020
passing; C-005 pending until its compatibility-matrix part (Phase 3), with its fallback part
passing; C-016 pending CI.

### Done

- **Built-in agent and router v1** (ADR 0015). One step is one model turn, with typed steps,
  routing by rules, the T0 classifier, priors and a cost model, and every switch logged with
  its reason and cost estimate. The escalation ladder moves up after two gate failures on one
  rung. Per-task budgets (tokens, wall time, GPU-seconds, escalations) are checked at step
  boundaries. `ssw run` and `ssw agent` drive it, with a worktree pool that keeps generated
  files out of diffs, a token ledger and run trajectories.
- **Fallback tool protocol** (ADR 0016). Models without native tool calls get the tools in
  the prompt, one JSON action per reply under a JSON-schema constraint, with validation and
  repair. Tool arguments are spelled out field by field, and tool examples are shown.
- **The agent's tools and context services.** Repo map, search, hash-anchored edits
  (`spec/edit-format.md`, ADR 0018), a built-in LSP client (ADR 0017), test, run and finish.
- **Verification gates.** Lint, types, impacted tests, full tests with acceptance commands,
  protected paths, and science checks with numerical tolerances and plot checks. There are
  general and science tracks; T0 never edits code in the science track.
- **Live results on the development machine.** C-001: the toy task finished inside a Linux
  container limited to 8 GB with no swap and no GPU devices: 15 steps, 29,840 tokens, 113 s,
  peak memory 6.43 GiB. C-005: Phi-3.5-mini-instruct, which has no tool calling, finished the
  toy task through the fallback protocol in 12 model turns.

### What changed against the spec

- ADR 0015: the agent, not the model server, owns the tool loop, so a step may switch workers
  inside it (ADR 0005 item 3 still holds for stored responses and for the proxy). The
  escalation rung is a band, not a single level.
- ADR 0016: the fallback protocol's reply format, constraints, history rendering and repair.
- ADR 0017: a small built-in LSP client instead of pygls and lsprotocol.
- ADR 0018: TK5 narrowed. A whole-file `text` edit replaces files of up to 100 lines. Files
  of up to 60 lines are always read whole. A stale anchor in a file of up to 100 lines shows
  the whole file again.
- The C-005 fallback model is Phi-3.5-mini-instruct, not the 1.5B coder base model. The base
  model kept repeating tool calls and never edited correctly (ADR 0016 item 10).

### Open issues

- Small models vary from run to run. Phi-3.5-mini-instruct failed its first live run
  (`gates_failed`) before the short-file rules, and the claim test records one run. The
  Phase 3 bench measures pass rates over several seeds.
- No LSP server launch commands ship yet; the `lsp` tool works only with a server the user
  configures.
- Assumptions isolated behind interfaces (docs/RESEARCH.md, "Verified during Phase 2"):
  that one engine's batch embeddings follow the input order, and the package installer's
  offline variable.
- Probe exports still carry hardware product names; they must be generic before the Phase 3
  compatibility matrix commits them (Phase 1 issue).

### Waiting on the maintainer

Nothing new. Phase 1 actions 1 to 3 still apply.

## Phase 1: Foundation and compatibility core

Status: done on 2026-10-16, except C-016, which needs hosted CI (maintainer action 1 below).
`scripts/phase_check.py 1` passes 11 of 12 checks; the twelfth fails only because C-016 is
pending. `uv run pytest -q`: 796 passed, 4 skipped (C-016 pending, and three live tests that
need a local server, a small local model or `STEPSWITCH_LIVE_HUB=1`). `ssw prove --quick
--phase 1`: C-004 and C-007 passing, C-016 pending.

### Done

- **Rename** to Stepswitch, CLI `ssw` (ADR 0001), and the package skeleton with hatchling as
  the build backend, so the registry ships inside the wheel (ADR 0012).
- **Configuration, egress and state.** Layered TOML configuration (`stepswitch.toml` committed,
  `.stepswitch/local.toml` gitignored), `ssw init`; every HTTP client comes from
  `stepswitch.net` with a purpose string, an audit row and offline enforcement, and takes no
  proxy settings or `.netrc` credentials from the environment; SQLite state with migrations and
  the WAL safety check (ADR 0002).
- **Compatibility core.** The IR, a codec per SPEC §4.1 format (chat-completions, responses,
  messages, generate-content, Ollama native, llama.cpp native, text completion), streaming
  assembly, tool-call parsers, error mapping with retries, and `ModelClient`. Reasoning keeps its
  full origin (format, endpoint, model) and is replayed only there; refusals are their own part.
  Mock-server conformance tests cover every format (240 tests).
- **Registry and models.** `registry/` with the verified CPU tier; `ssw models pull --tier
  cpu` and `ssw models add <hub id | path | URL>` with verified downloads, GGUF metadata reading
  and chat-template hashes. Three T0 models are installed on the development machine.
- **Model plane.** Server manager with llama.cpp and Ollama adapters (start, health, stop,
  reuse); `ssw doctor` detects OS, CPU, RAM, GPUs, disk and engines and writes a hardware
  profile.
- **Probe and calibration.** `ssw probe` runs a 20-check capability battery (tool calls,
  fallback protocol, JSON schema, streaming, usage semantics, reasoning field and retention,
  context length, prefix cache, throughput and more) and stores the profile; `ssw calibrate`
  measures prefill and decode throughput and time to first token, preferring server timings.
  A full probe of a 4B model on CPU took 60 s (SPEC §4.6 asks for under 3 minutes).
- **Claims framework.** `ssw prove` (quick and full modes, phase and claim selection, fresh
  source digest), the README claims section renderer with its check mode, evidence files with
  machine-specific values scrubbed, and the connection recorder for the network claims
  (ADR 0013). C-016 is proven by evidence that CI writes (ADR 0014).

### What changed against the spec

- ADR 0012: hatchling instead of `uv_build`.
- ADR 0013: the connection recorder falls back from audit hooks to patched socket and process
  functions, because endpoint-security software on the development machine breaks interpreter
  audit hooks.
- ADR 0014: claims whose evidence comes from other machines (C-016) stay pending without it,
  and phase checks keep requiring them.
- Prove reports keep skip reasons without checkout paths, so a committed report holds nothing
  machine-specific.

### Open issues

- IR additions requested by the codec work and not made yet: an image reference by file ID,
  an in-band response error, a description on `ResponseFormat`, and a way for
  `decode_response` to record losses in the conversion report.
- Probe exports carry hardware product names from `ssw doctor`; before exports are committed
  (Phase 3 compatibility matrix) they must be reduced to generic descriptions so that the
  hygiene test passes (ADR 0007 item 3).
- No engine tested enforces a named `tool_choice`, so the router never relies on forced tool
  choice; the fallback protocol uses JSON-schema constrained decoding instead.
- Probe profiles are stored under the public model label; a lookup by a configured file-path
  model ID would miss. The Phase 2 catalog looks profiles up by both.

### Waiting on the maintainer

1. **Remote repository and CI.** C-016 (Linux, macOS and Windows) needs the CI workflow
   (`.github/workflows/prove.yml`) to run on a hosted remote and commit its evidence. Until
   then `phase_check.py 1` and `--upto N` report C-016 as not passing.
2. **Name registration.** Register the package-index and npm names before any public
   announcement.
3. **Development tools.** Node 22.12 or later before Phase 4.

## Phase 0: Research and design

Status: done on 2026-10-16. `scripts/phase_check.py 0` passes all seven checks. Phases 1 to 4
fail, as they should before any product code exists.

### Done

- **Spec.** `docs/SPEC.md` v0.3: the kickoff spec with Phase 0 research applied in place. A
  change table at the top maps every changed section to its ADR or research entry. The claims
  table (SPEC §18.5) is unchanged, because the integrity tests check claim wording against it.
- **Research.** `docs/RESEARCH.md`, produced by thirteen parallel research tasks (one per SPEC §22
  row, plus API formats, open-weight model families, the SPEC §5 model candidates, and the name
  and stack check). It holds an index of 63 projects and items, each marked verified yes or no
  with a date, and tables for API formats, format conversion limits, engines, tool-call formats,
  engine-side tool parsers, open-weight model families, default model candidates, harnesses,
  orchestration, positioning, token counting, network isolation, the name check and the stack.
  It ends with a table of SPEC corrections and how each was resolved.
- **Architecture.** `docs/ARCHITECTURE.md`: components, processes, interfaces and invariants.
- **Decisions.** `docs/decisions/`:
  - 0001 Project name and CLI name: rename to Stepswitch (CLI `ssw`) at the start of Phase 1
  - 0002 Stack and minimum versions
  - 0003 Compatibility layer
  - 0004 Proxy design
  - 0005 Router design
  - 0006 Claims registry, public-text lint and prove contracts
  - 0007 Scope and enforcement of the no-company-names rule
  - 0008 Engine targets and platform scope
  - 0009 Default models per tier
  - 0010 Positioning against existing projects
  - 0011 Scopes of the platform and isolation claims
- **Claims.** `claims/claims.yaml` registers C-001 to C-020 as `pending`, with the public-text
  lint configuration. Claim texts match SPEC §18.5. Four scopes were made precise (ADR 0011).
- **Tests.** `tests/claims/test_integrity.py` implements the SPEC §18.3 meta-tests, with
  self-tests on synthetic text. `tests/unit/test_repo_hygiene.py` enforces working rules 4 and 5:
  no company names (hashed denylist) and nothing machine-specific in committable files.
- **Phase checks.** `scripts/phase_check.py` covers phases 0 to 4 (`N`, `--upto N`, `--json`).
  From Phase 1 on, a phase also requires a fresh `sy prove` report whose source digest matches
  the working tree.
- **Development environment.** uv with a uv-managed Python 3.12 interpreter, because
  interpreters that bundle SQLite older than 3.51.3 have a WAL corruption bug.

### What research changed

Details are in the SPEC change table and the ADRs. The main points:

- **Name.** The package-index name is taken, a same-domain project uses the name, and `sy`
  collides with a same-domain tool. The project becomes Stepswitch (`ssw`) as the first change
  of Phase 1 (ADR 0001).
- **Positioning.** Per-turn routing inside agent sessions, cache-aware switching and per-task
  (harness, model) selection already exist elsewhere. Our novelty is the combination: step-type
  routing with a policy that learns from the user's own verified outcomes, plus verification,
  claims and the science track. Public text says "combines", never "first" (ADR 0010).
- **Compatibility layer.** The responses codec follows the open, vendor-neutral specification.
  The generate-content reference is labelled legacy, and the newer interactions format is a
  watch item. Reasoning keeps its origin, because signed reasoning may only be replayed to the
  model that produced it. Effort has seven levels. Every server-dependent capability is probed
  before it is trusted. Tool-call parsers are chosen per model version (ADR 0003).
- **Engines.** TensorRT-LLM is dropped as an adapter. vLLM, SGLang and KTransformers are
  Linux-only, so on Windows they run under WSL2 or in a container. The llama.cpp server's router
  mode and slot files cover hot-swap and resume (ADR 0008).
- **Models.** The default CPU set takes 4.78 GB on disk and about 5.7 GB of RAM. One candidate
  ID was renamed and several sizes and fits were corrected. Licenses with commercial conditions
  are flagged in the registry (ADR 0009).
- **Supply chain.** The gateway library named in the spec shipped compromised releases in 2026.
  The bridge to it is optional, off by default and isolated out of process (ADR 0003).
- **Claim scopes.** Hosted CI offers containers and OS-level network isolation only on Linux.
  The container and isolation claims are scoped to Linux, and the platform claim uses pinned
  runner labels (ADR 0011).
- **Company-name rule.** Identifiers that other software requires verbatim (protocol headers,
  environment variables, parser and package names) are allowlisted explicitly. Prose never names
  companies (ADR 0007).

### Open issues

- Items marked `verified: no` in RESEARCH.md:
  - the interactions format, whose version path is not settled;
  - embeddings, rerank and realtime endpoints, which are outside the C-004 wording;
  - CI network isolation, which can only be confirmed by running hosted CI;
  - one model family (LongCat).
- The SPEC §0 pitch contains unregistered superlatives, so the README must use claim-backed
  wording (ADR 0006).
- Spikes still to run:
  - whether testmon data copied between worktrees at the same base commit stays valid (Phase 2);
  - harmony rendering fidelity against the server's prefix cache (Phase 1 codecs).
- The Linux-only claim parts (C-001, and the OS-isolated part of C-006) will report "not
  applicable here" on Windows and macOS development machines.

### Waiting on the maintainer

1. **Working rules file.** The kickoff package's working-rules file (`CLAUDE.md`) is not in the
   repository yet. Copying it in from the kickoff archive was held back for the maintainer's
   approval, because that file gives standing instructions to every future agent session.
   Add it yourself or approve the copy, then commit it.
2. **Name.** Confirm Stepswitch (`ssw`), or pick the runner-up Shuntyard, which keeps `sy`
   (ADR 0001). The rename is the first change of Phase 1, and from then on the phase goal
   commands use the new CLI name.
3. **Name registration.** Register the package-index and npm names before any public
   announcement.
4. **Remote repository and CI.** C-016 (macOS, Linux and Windows) needs hosted CI, so a remote
   repository has to be created.
5. **Development tools.** Install uv 0.13 or later (it matches CI's Python 3.15 default) and
   Node 22.12 or later (the current LTS line is recommended) before Phase 4. The Ollama release
   that was researched is 0.40.5; older installs may lack routes the research relies on.

## Next

All ten phases of SPEC §23 are done. What is left needs the maintainer (Phase 10, Waiting on
the maintainer): a public repository with CI, whose prove workflow records C-016; the package
index's trusted publisher; and the first tag.
