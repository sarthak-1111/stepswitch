# ADR 0029: External harness adapters and the per-run proxy

- Status: accepted
- Date: 2026-10-19
- Phase: 8

## Context

SPEC §12 items 3 to 7 ask for adapters that run third-party coding agents (*harnesses*) on a
task, every model request going through `ssw proxy` (ADR 0004), with one interface:
`start(task, worktree, endpoint, settings) -> RunHandle`, `events()`, `cancel()`, `result()`.
Research checked on 2026-10-19 (docs/RESEARCH.md, "Phase 8: harnesses") found:

- Claude Code takes the prompt as a positional argument after `-p`. A prompt on stdin alone is
  documented only for `--input-format stream-json`. Its variadic tool-list flags would swallow a
  trailing positional. `CLAUDE_CODE_SKIP_PROMPT_HISTORY` breaks `--resume`. A `-p` run against a
  third-party endpoint starts in `auto` permission mode unless a mode is given.
- Pi's extensions can add a virtual model whose `route(request, ctx)` runs before every request.
  Its ID may not equal a physical model's ID of the same provider. Providers can also come from
  the agent directory's `models.json`.
- Oh My Pi has no virtual models. It has `before_subagent_spawn`, which picks each child's model,
  and `--trusted-extension`, an exact allowlist that cannot be combined with `-e`. Without a UI,
  approvals fail closed.
- OpenCode (1.19.x) and Kimi Code are ACP agents. OpenCode ignores unknown config keys and
  writes `$schema` back into on-disk config files. Kimi Code's provider types other than `kimi`
  are named after vendors. ACP's schema is at 1.26.0, with `protocolVersion` 1.
- Of the five harnesses, only Claude Code is installed on the development machine.

## Decision

1. **A proxy of its own for every harness run.** `HarnessLoop` serves a `ProxyService` bound to
   the task (`attach`) on a free loopback port with a fresh token (`ssw-…`), and stops it when
   the run ends.
   - Every request is a step of the caller's task: a decision and a step event in its
     trajectory, a ledger row, its budget.
   - Requests are routed with the task's features, rung, pin, families and learned priors.
   - Once the task's budget trips, the proxy refuses with 429 (`quota`), so the harness's own
     retries cannot overspend. The loop's watchdog also cancels the harness.
   - The harness only ever holds this run's token.
2. **Isolation.**
   - **Environment.** The harness gets `minimal_env` (no keys, tokens or proxy settings of the
     user), the adapter's variables and the adapter's egress switches. The switches are set on
     every run, not only offline, since none of them is needed to work. Configured extras that
     look like credentials are refused.
   - **State.** Generated config and harness state live in a per-run state directory, removed
     afterwards unless kept. Examples: `CLAUDE_CONFIG_DIR`, `PI_CODING_AGENT_DIR`,
     `OPENCODE_CONFIG_CONTENT`, `KIMI_CODE_HOME`. User files are never edited or read.
   - **Credentials.** The proxy never logs or forwards inbound credential headers.
3. **Permissions: `edits` by default, `all` for sandboxed runs.**
   - Claude Code: `acceptEdits`, with the test command as the only allowed Bash prefix
     (`Bash(<command> *)`). It also runs with `--permission-prompts none`, `--strict-mcp-config`
     and the web tools disallowed.
   - Oh My Pi: approval mode `write`. Commands that need approval fail closed, so in `edits`
     mode it cannot run the tests itself; the gates run them after it.
   - ACP agents: `session/request_permission` is answered by the run's policy. File access is
     limited to the worktree, and only the test command may run.
4. **Claude Code.**
   - The prompt goes positionally right after `-p`; a prompt starting with `-` is prefixed with
     `Task: `.
   - Prompt history stays on, because turning it off breaks `--resume`.
   - `CLAUDE_CODE_MAX_CONTEXT_TOKENS` is the largest window among the routable workers. A
     transcript that outgrows a smaller worker's window moves to one it fits.
   - Request-class hint headers are on.
   - It is documented as "works through `ssw proxy`; not supported upstream".
5. **Pi: generated provider plus a routed virtual model.**
   - The adapter writes `models.json`. The `stepswitch` provider lists the slots, at the largest
     window, and every routable worker, at its own window.
   - The `adapters/pi` extension adds only the virtual model `stepswitch/routed`, which the run
     selects. This narrows SPEC §12 item 4's "registering Stepswitch as a provider": the
     provider has one source and works without the extension.
   - `route()` calls `POST /stepswitch/v1/route`. The proxy makes the routing decision there and
     reserves it for the worker it names, so the request that follows is that step, not a
     second decision.
6. **Oh My Pi.**
   - The adapter writes `models.yml` and `config.yml` (`modelRoles`).
   - The `adapters/omp` extension's `before_subagent_spawn` hook gives every child the slot of
     its role. The role map arrives in `STEPSWITCH_ROLE_MODELS`, so the Python adapter stays its
     only source.
   - It is loaded with `--trusted-extension`, so nothing else loads.
   - Mid-turn switching happens in the proxy.
7. **Slot names.** The canonical names are `ssw:*`. The dash forms `ssw-*` are accepted for
   harnesses whose model selectors read `:` as a thinking level. `ssw:summarize` is added for
   compaction requests.
8. **OpenCode and Kimi Code.**
   - OpenCode's config is inline JSON (`OPENCODE_CONFIG_CONTENT`), so its `$schema` write-back
     never touches a file.
   - Kimi Code is configured through `KIMI_MODEL_*` and a generated `config.toml` in its own
     home. Its default provider type is `kimi` (chat-completions with extra fields). Any other
     type is a vendor name, so it is set in local config only (rule 4).
9. **An in-house ACP client** (`stepswitch.workers.acp`), not the SDK, whose current release
   requires an older Python. The stable methods are few, and owning them keeps permission
   answers, file access, cancellation and transcript recording under our control.
   - **Initialize.** `initialize` feature-detects. Another protocol version is refused (fail
     closed).
   - **Model.** `session/set_config_option` selects the model when the agent offers one.
     Otherwise the agent's model config points at the proxy.
   - **Generic agents.** They get `STEPSWITCH_PROXY_URL`, `_TOKEN`, `_SESSION`, `_MODEL` and
     `_CONTEXT_TOKENS`.
10. **The orchestrator's view.**
    - A harness run replaces the built-in agent's loop. The candidate's built-in agent remains
      as a helper for the gates, the cross-family review, escalation and best-of-N's text steps.
    - Step numbers are shared between the proxy's steps and the helper's.
    - `max_steps` does not apply to harness runs: the harness has its own turn limit and the
      budget is enforced by the proxy.
    - Harness runs are not replayable, because their tool calls run inside the harness.
      `ssw replay` says so.
11. **The extensions are plain ES modules with JSDoc types** (`// @ts-check`) under
    `adapters/{core,pi,omp}` (SPEC §21). Both hosts load them without a build step, and the
    repository's tests run them under any Node ≥ 18: against a fake host, and against a real
    proxy. The packages import the shared core relatively, so they load from a source checkout
    and are not published separately yet.

## Consequences

- **Contract tests.** Every adapter has them, using fixtures under `tests/fixtures/harness/`.
  Claude Code's are recorded from a live run. The others are built from the harnesses'
  documented stream and protocol shapes (`source: "documented"`) until a live run can be
  recorded on a machine that has them.
- **Scope of claims.** Public claims about third-party harnesses stay within what these tests
  prove and never imply upstream support.
- **Latency.** Pi makes one extra loopback call per request (`route`). Oh My Pi and the ACP
  agents switch models only through the proxy, at user turns (ADR 0004).
- **Oh My Pi in `edits` mode.** It cannot run tests on its own initiative.
