# ADR 0034: Comparing Stepswitch with a frontier harness on its own login

- Status: accepted
- Date: 2026-10-19
- Phase: 11

## Context

The maintainer asked for a comparison with frontier models. The question is whether Stepswitch
on local models gets the same results as Claude Code on Sonnet 5.5 or Opus 5.5, and with how
many tokens. The comparison has to run without an API key: the only frontier access is a
subscription login to Claude Code.

What constrains the design:

- **SPEC §25.** No claim of beating frontier models on the hardest tasks; claims are scoped to
  measured suites. **SPEC §24.** The README's comparison table lists features only, and
  performance comparisons go through claims and the leaderboard.
- **ADR 0029.** Every harness run authenticates to `ssw proxy` with a token of its own, and the
  user's credentials never reach a harness.
- **A subscription login is for the official client.** It cannot serve as a model endpoint
  behind the router, so the frontier side has to run as Claude Code itself, on its own login.
- **Claude Code 2.1.296** (docs/RESEARCH.md, "Verified during Phase 11"):
  - `--bare` accepts only an API key or a key helper, never the subscription login.
  - `--safe-mode` turns off every customization (CLAUDE.md, skills, installed plugins, hooks,
    MCP servers, custom commands and agents, output styles, workflows), while auth, model
    selection and permissions work normally.
  - `--setting-sources` limits which settings files load. `--no-session-persistence` keeps a
    print-mode session off the disk. `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` turns auto memory
    off.
  - The final `result` line reports `usage` and a `modelUsage` table per model, with
    uncached input, cache writes, cache reads and output.
- **Parent CLAUDE.md files.** Claude Code reads CLAUDE.md files in every directory from the
  working directory up. Bench worktrees live in the project's state directory, inside this
  repository, so a bench harness run would read the repository's own working rules.

## Decision

1. **Two new contestant kinds** in `ssw bench run --workers`:
   - `harness:<name>` runs each task in an external harness through `ssw proxy`
     (`ssw run --harness`). The proxy routes among the `--router-pool` workers, as for
     `router`.
   - `native:<harness>:<model>` runs the harness alone, on its own login and the named model.
     It is one session, given the prompt a harness run's first attempt gets: the
     instruction, the acceptance commands, the test command, the track notes and the
     working-directory rule. There is no proxy, routing, gate, retry or review. The harness's
     own loop decides when it is done. The bench scores the run like every other contestant,
     on a fresh copy with the diff applied and the protected paths restored.
2. **Native runs are bench-only and opt-in by name.** `ssw run` never starts one. Only
   adapters that declare it support native runs; Claude Code is the first. Before the first
   native run, the bench prints that the harness runs on its own login and that its usage
   counts against that account.
3. **How a native Claude Code run is isolated:**
   - **Flags.** `--safe-mode --setting-sources project,local --no-session-persistence
     --strict-mcp-config` and `--model <model>`. The rest matches a proxy run: the
     permission mode with the test command allowed, `--permission-prompts none`, and the web
     tools disallowed.
   - **Environment.** `minimal_env`, an allowlist, so the run gets no keys and none of the
     harness's credential, endpoint or model variables. `CLAUDE_CONFIG_DIR` is not set
     either. Add the adapter's egress switches and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.
   - **Login.** Claude Code reads its login from its default config directory itself.
     Stepswitch never reads, copies or forwards it.
4. **Bench harness runs are isolated too** (`TaskSpec.isolated`). Through the proxy, Claude
   Code also gets `--safe-mode`. Neither kind of run reads the repository's CLAUDE.md, the
   user's skills, plugins or hooks. The two kinds differ only in where model requests go and
   in session persistence: proxy runs keep their session so a gate retry can resume it.
5. **Token accounting** uses one definition for every contestant: input counts cached tokens,
   and uncached tokens are input minus cached plus output (ADR 0028).
   - Every row gains `models`, per model: input, cached input, cache writes, output, calls,
     and whether the model is hosted. Every row also gains `hosted_tokens`.
   - **Routed runs** take these from the task's ledger. A model counts as hosted when its
     endpoint is not local or forwards to a hosted service (SPEC §15, the catalog's rule).
   - **Native runs** take them from the `result` line's `modelUsage`:
     - input = `inputTokens + cacheCreationInputTokens + cacheReadInputTokens`;
     - cached input = `cacheReadInputTokens`;
     - cache writes = `cacheCreationInputTokens`.

     Every model of a native run is hosted. The harness's own cost estimate is kept in the
     row as reported, and no claim uses it.
   - The leaderboard gains two columns: median uncached tokens and median hosted tokens.
6. **Claims, registered before the comparison runs** (phase 11):
   - **C-021**: "On the mini suites, Stepswitch on local models passes as many task runs as
     Claude Code on Sonnet 5.5 and on Opus 5.5."
     - Compares the `router` contestant with `native:claude-code:claude-sonnet-5-5` and
       `native:claude-code:claude-opus-5-5`.
     - Counts passes over the task runs all three share, across both mini suites together.
       The evidence also shows each suite on its own.
     - The router sends no token to a hosted model.
   - **C-022**: "On the mini suites, Stepswitch uses fewer uncached tokens per task than
     Claude Code on Sonnet 5.5 and on Opus 5.5."
     - Takes the ratio of the router's uncached tokens to each native contestant's.
     - The 95% interval of that ratio (cluster bootstrap over tasks) must lie below one.
     - The evidence also reports all tokens and hosted tokens.
   - **C-023**: "On the mini suites, Claude Code through `ssw proxy` on local models passes as
     many task runs as Claude Code on Sonnet 5.5 and on Opus 5.5." This is C-021's
     comparison, made for `harness:claude-code`.
   - Each claim takes each contestant's rows from the newest bench run that has that
     contestant on the suite, so local and native contestants can run separately.
   - The native contestants name full model IDs. The tests check that the rows' recorded
     models are those.
   - A claim that fails is narrowed in a new ADR, with the measured numbers. Its test is never
     weakened (CLAUDE.md).
7. **The order of the runs.** A pilot comes first: three tasks, one seed, both native models.
   It measures the usage and confirms the isolation, and the maintainer approves the full
   runs. The full runs cover both mini suites with two seeds. Then a harder suite (a subset
   of SWE-bench Verified), after its dataset, license and format are checked.

## Consequences

- The comparison measures products as people run them. A frontier harness with its own loop
  is compared with Stepswitch's routed agent, and with the same harness on local models
  through the proxy. A native run gets no retry from Stepswitch's gates. It does run the
  tests itself, since the prompt names the test command.
- Results hold for the versions recorded in each run: Claude Code's version, the frontier
  model IDs, the local models and the machine. Rerunning the bench is the way to check them.
- A native run can add an entry for its working directory to the harness's global state file
  (`~/.claude.json`). The pilot measures this, and PROGRESS records it.
- The bench now has a contestant that sends task text to a hosted service. That happens only
  when it is named, and never for `ssw run`.
