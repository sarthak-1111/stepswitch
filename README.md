# Stepswitch

An open-source orchestrator for coding agents on models you run yourself. It sends each step of
a task (plan, read, edit, test, review) to the model that suits it, checks the change with your
tests and a reviewer from another model family, and counts every token. External harnesses run
on the same models through `ssw proxy`.

![A task in the terminal, played at twice the speed: each step line names the model that ran it, and the task's token count grows step by step](docs/assets/demo.gif)

Each claim in the [Claims](#claims) section has a test you can run. The section lists only the
claims whose tests pass, and names the ones we withdrew when a run proved them wrong. Don't
trust us, run `ssw prove`.

## Quickstart

<!-- quickstart:start -->
A CPU is enough. The tiny tier runs on llama.cpp, which `ssw models pull` installs for you.

Install with the installer attached to each release. On macOS or Linux:

```sh
sh install.sh
```

On Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Each installer downloads uv if it is missing and checks the download's checksum before running
it. It then installs Stepswitch with `uv tool install`. If you already have uv,
`uv tool install stepswitch` does the same.

Then, in a git repository:

```sh
ssw init                     # writes stepswitch.toml, detects the hardware, picks a profile
ssw models pull --tier cpu   # the tiny tier's models and the llama.cpp build they run on
ssw run "Fix the bug in add() in calc.py so that the tests pass."
```

`ssw run` works in a git worktree of its own. It runs your project's tests with your project's
`.venv` (or the interpreter you configure) and leaves the change on the branch
`stepswitch/<task>`; `--apply` applies it to your working tree. `ssw dash` opens the dashboard.
<!-- quickstart:end -->

## Results

### SWE-bench Verified Mini

Real issues from django and sphinx, from the published Mini subset of SWE-bench Verified, graded
by the official SWE-bench harness, which runs tests that are hidden from the agents. Stepswitch and
mini-SWE-agent ran on the same open-weight model, `gpt-oss:120b`, on one workstation, and each
saw only the issue text. Each contestant's patches are committed beside the results under
[bench/results](bench/results), in the harness's own format, so you can grade them again
yourself. `ssw prove --render-readme` writes this table from the evidence of passing claims.

<!-- results:swebench:start -->
| | Stepswitch, `gpt-oss:120b` | mini-SWE-agent, `gpt-oss:120b` |
|---|---|---|
| Instances compared | <!-- num:C-026:sections.swebench.results.stepswitch.instances -->49<!-- /num --> | <!-- num:C-026:sections.swebench.results.mini_swe_agent.instances -->49<!-- /num --> |
| Instances resolved | <!-- num:C-026:sections.swebench.results.stepswitch.resolved_percent -->49.0<!-- /num --> % | <!-- num:C-026:sections.swebench.results.mini_swe_agent.resolved_percent -->14.3<!-- /num --> % |
| Runs that a model server error ended | <!-- num:C-026:sections.swebench.results.stepswitch.server_errors -->1<!-- /num --> | <!-- num:C-026:sections.swebench.results.mini_swe_agent.server_errors -->24<!-- /num --> |
| Resolved where no server error ended either run | <!-- num:C-026:sections.swebench.paired.no_server_error.resolved_percent.stepswitch -->29.2<!-- /num --> % | <!-- num:C-026:sections.swebench.paired.no_server_error.resolved_percent.mini_swe_agent -->25.0<!-- /num --> % |
| Tokens per instance, cached included | <!-- num:C-026:sections.swebench.results.stepswitch.per_instance.tokens -->1,606,949<!-- /num --> | <!-- num:C-026:sections.swebench.results.mini_swe_agent.per_instance.tokens -->2,372,256<!-- /num --> |
| Uncached tokens per instance | <!-- num:C-026:sections.swebench.results.stepswitch.per_instance.uncached_tokens -->142,135<!-- /num --> | <!-- num:C-026:sections.swebench.results.mini_swe_agent.per_instance.uncached_tokens -->46,220<!-- /num --> |
| Tokens per instance that both resolved, cached included | <!-- num:C-026:sections.swebench.paired.both_resolved_tokens.stepswitch -->1,529,179<!-- /num --> | <!-- num:C-026:sections.swebench.paired.both_resolved_tokens.mini_swe_agent -->2,738,146<!-- /num --> |
| Model calls per instance | <!-- num:C-026:sections.swebench.results.stepswitch.per_instance.steps -->86.7<!-- /num --> | <!-- num:C-026:sections.swebench.results.mini_swe_agent.per_instance.steps -->93.2<!-- /num --> |
| Minutes per instance | <!-- num:C-026:sections.swebench.results.stepswitch.per_instance.minutes -->15.5<!-- /num --> | <!-- num:C-026:sections.swebench.results.mini_swe_agent.per_instance.minutes -->53.5<!-- /num --> |

Evidence: [C-026](claims/evidence/C-026.json).
<!-- results:swebench:end -->

mini-SWE-agent ran with its default SWE-bench configuration, which acts through tool calls, and
its own step limit; Stepswitch ran with the limits in the command below. When the model server
could not parse a tool call the model wrote, it answered with an error. After its own retries,
that ended mini-SWE-agent's run without a submission; Stepswitch asked for the same step again
through its fallback tool protocol. The table counts the runs that a server error ended, and
compares the two on the instances where none did. Stepswitch is graded on its task branch
however its run ended, mini-SWE-agent on its submission, and an instance where either run hit an
infrastructure error is left out for both. The evidence lists how each contestant's runs ended
and which instances were left out (see
[the benchmark's decision record](docs/decisions/0042-swebench-verified-mini.md)).

To run it yourself you need Docker and a model server:

```sh
ssw bench run --suite swebench-verified-mini --router-pool <endpoint>/<model> \
  --workers "router,mini-swe-agent:<endpoint>/<model>" --max-steps 100 --budget-seconds 3600
```

A full run takes days on one workstation; `--resume --out <dir>` continues an interrupted one.

### Mini bench suites

Task runs on the mini bench suites: small bug fixes, features, refactors and numerical tasks in
Python, not the hardest tasks. They were measured with `ssw bench` on one workstation, where the
local models were `gpt-oss:120b` and `qwen2.5:32b` under Ollama. `ssw prove --render-readme`
writes this table from the evidence of passing claims, and the [Claims](#claims) section gives
each claim's exact wording.

<!-- results:start -->
| | Stepswitch, local models | Claude Code through `ssw proxy`, local models | Claude Code, `claude-sonnet-5-5` | Claude Code, `claude-opus-5-5` |
|---|---|---|---|---|
| Task runs passed | <!-- num:C-022:sections.comparison.passes.stepswitch.pass_rate_percent -->97.6<!-- /num --> % | <!-- num:C-023:sections.comparison.passes.stepswitch.pass_rate_percent -->100.0<!-- /num --> % | <!-- num:C-022:sections.comparison.passes.sonnet.pass_rate_percent -->100.0<!-- /num --> % | <!-- num:C-022:sections.comparison.passes.opus.pass_rate_percent -->100.0<!-- /num --> % |
| Tokens per task run, cached included | <!-- num:C-022:sections.comparison.per_task_run.stepswitch.tokens -->24,793<!-- /num --> | <!-- num:C-023:sections.comparison.per_task_run.stepswitch.tokens -->173,130<!-- /num --> | <!-- num:C-022:sections.comparison.per_task_run.sonnet.tokens -->73,390<!-- /num --> | <!-- num:C-022:sections.comparison.per_task_run.opus.tokens -->93,920<!-- /num --> |
| Uncached tokens per task run | <!-- num:C-022:sections.comparison.per_task_run.stepswitch.uncached_tokens -->7,049<!-- /num --> | <!-- num:C-023:sections.comparison.per_task_run.stepswitch.uncached_tokens -->48,200<!-- /num --> | <!-- num:C-022:sections.comparison.per_task_run.sonnet.uncached_tokens -->7,798<!-- /num --> | <!-- num:C-022:sections.comparison.per_task_run.opus.uncached_tokens -->9,386<!-- /num --> |
| Tokens sent to hosted models per task run | <!-- num:C-022:sections.comparison.per_task_run.stepswitch.hosted_tokens -->0<!-- /num --> | <!-- num:C-023:sections.comparison.per_task_run.stepswitch.hosted_tokens -->0<!-- /num --> | <!-- num:C-022:sections.comparison.per_task_run.sonnet.hosted_tokens -->73,390<!-- /num --> | <!-- num:C-022:sections.comparison.per_task_run.opus.hosted_tokens -->93,920<!-- /num --> |
| Minutes per task run | <!-- num:C-022:sections.comparison.per_task_run.stepswitch.minutes -->0.7<!-- /num --> | <!-- num:C-023:sections.comparison.per_task_run.stepswitch.minutes -->2.8<!-- /num --> | <!-- num:C-022:sections.comparison.per_task_run.sonnet.minutes -->0.3<!-- /num --> | <!-- num:C-022:sections.comparison.per_task_run.opus.minutes -->0.4<!-- /num --> |

Evidence: [C-022](claims/evidence/C-022.json), [C-023](claims/evidence/C-023.json).
<!-- results:end -->

## Run it on your own repository

Stepswitch talks to model servers over HTTP. `ssw models serve` starts a local engine for a
model from the registry. Servers you run yourself, and hosted endpoints, go in
`.stepswitch/local.toml`, which is gitignored; `stepswitch.toml` holds the project's policy and
is committed. [examples/](examples/) has both files for common setups. `ssw doctor` checks the
setup, and `ssw probe` tests how a model handles tool calls before you rely on it.

| Command | What it does |
|---|---|
| `ssw run "<task>"` | Runs a task: routing, the agent loop, the gates and a review |
| `ssw run --best-of <n>` | Runs candidates on several model families and compares the passing ones |
| `ssw run --track science` | Adds golden numerical cases with tolerances and protected constants |
| `ssw dash` | Opens the dashboard and the local task API |
| `ssw replay <task>` | Replays a recorded run, or forks it at a step onto another worker |
| `ssw proxy` | Serves the models to external harnesses through one local endpoint |
| `ssw jobs` | Queues slow-lane reviews for models too large for interactive use |
| `ssw audit` | Shows each network connection and child process of a task |
| `ssw bench run` | Runs a bench suite and writes a leaderboard |
| `ssw prove` | Runs the claim tests; `--render-readme` writes this README's claims and results |

## Compatibility

The [compatibility matrix](docs/COMPATIBILITY.md) lists what has been verified, model by model
and engine by engine, from the probe results in [probes/](probes/).

## How it works

<!-- how-it-works:start -->
- **Workers and the ladder.** A worker is a model behind a harness. Workers sit on tiers, from
  tiny local models up to large local or remote ones.
- **Routing per step.** Each step of a task (plan, navigate, read, edit, run, triage, review,
  summarize) goes to the worker with the highest expected value for the task's features, given
  cost, latency and what the router has learned from earlier tasks. Failing gates move the
  task up the ladder.
- **One compatibility layer.** Requests go through one internal representation that speaks
  the chat-completions, responses, messages and generate-content formats and the engines'
  native formats. Models without native tool calls use a fallback tool protocol.
- **Verification.** Changes are made in a git worktree and must pass the track's gates:
  format, lint, type check, impacted tests and the full suite. A reviewer from another model
  family checks the change when one is available.
- **Accounting and egress.** Each model call goes into a ledger of tokens, time and cost. Each
  network connection goes through one egress layer, which `ssw audit` reports and offline mode
  closes to remote hosts.

The [architecture](docs/ARCHITECTURE.md) and the [design decisions](docs/decisions/) have the
details.
<!-- how-it-works:end -->

## Features

| Feature | Where |
|---|---|
| A model and a harness per task and per step, with an escalation ladder | `ssw run`, `[[policy.rules]]` |
| Local engines, and hosted endpoints in the common API formats | `ssw models serve`, `.stepswitch/local.toml` |
| Tool calls for models without native support | the fallback tool protocol |
| Gates for format, lint, types and tests; numerical gates for science code | `ssw run --track science` |
| Candidates across model families, with disagreement review | `ssw run --best-of <n>` |
| Token and cache optimization, with handoffs between models | `[context]` |
| A router that learns which worker suits which task | `ssw router stats` |
| Replay and fork of recorded runs | `ssw replay` |
| External harnesses through a local proxy | `ssw proxy`, `ssw run --harness` |
| A slow lane for very large models, as asynchronous jobs | `ssw jobs` |
| Offline mode, egress audit and redaction | `ssw run --offline`, `ssw audit` |
| A dashboard and a local task API | `ssw dash`, [the task API](docs/API.md) |
| Public claims with tests anyone can run | `ssw prove` |

## Roadmap

Stepswitch is built in phases (SPEC §23), and [the progress log](docs/PROGRESS.md) records the
results and open issues of each one. Next:

- the first release on the package index, with the installers attached;
- CI that runs `ssw prove --quick` on macOS, Linux and Windows (C-016);
- more harness adapters and engine adapters, and more models in the compatibility matrix.

## Claims

Every public claim about this project has an automated test that anyone can run. This section is
generated by `ssw prove --render-readme` and lists only claims whose tests currently pass.

<!-- claims:start -->
- **C-001** <!-- claim:C-001 -->Runs end to end on a CPU-only machine with 8 GB RAM.<!-- /claim --> ([evidence](claims/evidence/C-001.json); peak memory <!-- num:C-001:sections.container.peak_memory_gb -->8.00<!-- /num --> GiB, steps <!-- num:C-001:sections.container.steps -->16<!-- /num -->, wall time <!-- num:C-001:sections.container.budget.wall_seconds -->137<!-- /num --> s)
- **C-002** <!-- claim:C-002 -->Switches models between steps inside a single task.<!-- /claim --> ([evidence](claims/evidence/C-002.json))
- **C-003** <!-- claim:C-003 -->Only switches models when the expected gain outweighs the cost of re-reading context.<!-- /claim --> ([evidence](claims/evidence/C-003.json))
- **C-004** <!-- claim:C-004 -->Speaks the chat-completions, responses, messages and generate-content API formats, plus engine-native formats.<!-- /claim --> ([evidence](claims/evidence/C-004.json))
- **C-005** <!-- claim:C-005 -->Works with any open-weight model your installed engines can serve, including models without native tool calling.<!-- /claim --> ([evidence](claims/evidence/C-005.json); model families passing the probe suite <!-- num:C-005:sections.matrix.families_passing_count -->4<!-- /num -->)
- **C-006** <!-- claim:C-006 -->In offline mode, makes no network connections except to local endpoints.<!-- /claim --> ([evidence](claims/evidence/C-006.json))
- **C-007** <!-- claim:C-007 -->Sends no telemetry.<!-- /claim --> ([evidence](claims/evidence/C-007.json))
- **C-008** <!-- claim:C-008 -->The science gate catches numerical regressions beyond your tolerances.<!-- /claim --> ([evidence](claims/evidence/C-008.json))
- **C-009** <!-- claim:C-009 -->Changes to protected constants fail verification unless explicitly allowed.<!-- /claim --> ([evidence](claims/evidence/C-009.json))
- **C-010** <!-- claim:C-010 -->Handoff briefs validate against the HANDOFF v0.1 schema and stay under 1,500 tokens.<!-- /claim --> ([evidence](claims/evidence/C-010.json))
- **C-011** <!-- claim:C-011 -->Uses fewer uncached tokens than the unoptimized baseline at the same pass rate.<!-- /claim --> ([evidence](claims/evidence/C-011.json); fewer uncached tokens than the unoptimized baseline on the mini suites <!-- num:C-011:sections.comparison.claimed.percent -->17.8<!-- /num --> %)
- **C-012** <!-- claim:C-012 -->Best-of-N across model families passes at least as many tasks as the best single worker.<!-- /claim --> ([evidence](claims/evidence/C-012.json))
- **C-013** <!-- claim:C-013 -->Flags tasks where passing candidates disagree.<!-- /claim --> ([evidence](claims/evidence/C-013.json))
- **C-014** <!-- claim:C-014 -->Replays recorded runs deterministically.<!-- /claim --> ([evidence](claims/evidence/C-014.json))
- **C-015** <!-- claim:C-015 -->The router learns which worker suits which task.<!-- /claim --> ([evidence](claims/evidence/C-015.json))
- **C-017** <!-- claim:C-017 -->Goes from install to first completed task in under 5 minutes on a CPU-only machine.<!-- /claim --> ([evidence](claims/evidence/C-017.json))
- **C-018** <!-- claim:C-018 -->Every number in this README is reproducible.<!-- /claim --> ([evidence](claims/evidence/C-018.json))
- **C-019** <!-- claim:C-019 -->Token accounting matches what model servers report.<!-- /claim --> ([evidence](claims/evidence/C-019.json); largest difference from the server counters <!-- num:C-019:sections.accounting.max_relative_error_percent -->0.00<!-- /num --> %)
- **C-020** <!-- claim:C-020 -->Stops at the budget you set.<!-- /claim --> ([evidence](claims/evidence/C-020.json))
- **C-022** <!-- claim:C-022 -->On the mini suites, Stepswitch processes fewer tokens per task than Claude Code on Sonnet 5.5 and on Opus 5.5, counting cached tokens, and sends none to a hosted model.<!-- /claim --> ([evidence](claims/evidence/C-022.json); fewer tokens than Claude Code on Sonnet <!-- num:C-022:sections.comparison.sonnet.claimed.percent -->66.2<!-- /num --> %, fewer tokens than Claude Code on Opus <!-- num:C-022:sections.comparison.opus.claimed.percent -->73.6<!-- /num --> %)
- **C-023** <!-- claim:C-023 -->On the mini suites, Claude Code through `ssw proxy` on local models passes as many task runs as Claude Code on Sonnet 5.5 and on Opus 5.5.<!-- /claim --> ([evidence](claims/evidence/C-023.json); Claude Code on local models passed <!-- num:C-023:sections.comparison.stepswitch.pass_rate_percent -->100.0<!-- /num --> %, Claude Code on Sonnet <!-- num:C-023:sections.comparison.sonnet.pass_rate_percent -->100.0<!-- /num --> %, Claude Code on Opus <!-- num:C-023:sections.comparison.opus.pass_rate_percent -->100.0<!-- /num --> %)
- **C-026** <!-- claim:C-026 -->On SWE-bench Verified Mini, Stepswitch and mini-SWE-agent each ran once on the same local open-weight model, graded by the official SWE-bench harness, and sent no token to a hosted model.<!-- /claim --> ([evidence](claims/evidence/C-026.json); Stepswitch resolved <!-- num:C-026:sections.swebench.results.stepswitch.resolved_percent -->49.0<!-- /num --> %, mini-SWE-agent resolved <!-- num:C-026:sections.swebench.results.mini_swe_agent.resolved_percent -->14.3<!-- /num --> %)
- **C-027** <!-- claim:C-027 -->On SWE-bench Verified Mini, Stepswitch resolves at least as many instances as mini-SWE-agent with the same local model.<!-- /claim --> ([evidence](claims/evidence/C-027.json); Stepswitch resolved <!-- num:C-027:sections.swebench.results.stepswitch.resolved -->24<!-- /num --> instances, mini-SWE-agent resolved <!-- num:C-027:sections.swebench.results.mini_swe_agent.resolved -->7<!-- /num --> instances)
- **C-028** <!-- claim:C-028 -->On SWE-bench Verified Mini, Stepswitch uses fewer tokens per instance than mini-SWE-agent with the same local model, counting cached tokens.<!-- /claim --> ([evidence](claims/evidence/C-028.json); fewer tokens than mini-SWE-agent <!-- num:C-028:sections.swebench.tokens.tokens.percent -->32.3<!-- /num --> %)

Withdrawn: **C-021** ([why](docs/decisions/0041-claims-withdrawn-after-phase-12.md)), **C-024** ([why](docs/decisions/0041-claims-withdrawn-after-phase-12.md)), **C-025** ([why](docs/decisions/0041-claims-withdrawn-after-phase-12.md))
<!-- claims:end -->

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) explains how to set up, test and send a change: adding a
model, a bench task, an API codec or an engine adapter. Report vulnerabilities privately as
[SECURITY.md](SECURITY.md) describes. Everyone taking part follows the
[code of conduct](CODE_OF_CONDUCT.md). The docs site's pages are in [docs/site/](docs/site/).

Stepswitch is released under the [MIT license](LICENSE).
