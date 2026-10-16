# ADR 0015: Built-in agent and router v1

- Status: accepted
- Date: 2026-10-16
- Phase: 2

## Context

Phase 2 implements the built-in agent with step-level routing (SPEC §7, §12), the escalation
ladder and budgets. ADR 0005 fixed the router's shape. Building it settled questions that
ADR 0005 and the SPEC leave open, and showed that one of ADR 0005's rules is stricter than the
built-in agent needs.

## Decision

1. **One step is one model turn.** The agent predicts the type of the next turn from the last
   tool it ran (search tools lead to `read`, `read` to `edit`, `edit` to `run`, a failing test
   to `triage`) and routes that turn. Text steps (`plan`, `review`, `summarize`) get a
   purpose-built prompt without tools that names the step on a `Step: <type>` line. Work steps
   continue one shared transcript with the tools; their system prompt does not change between
   steps, so the transcript only grows and each server's prefix cache stays valid.
2. **Switching inside the agent's tool loop is allowed.** ADR 0005 item 3 forbids a switch while a
   tool loop is open. In the built-in agent the agent, not the model server, owns the loop:
   every turn is a complete request with the full IR transcript, so no server holds open loop
   state at a step boundary, and the next worker continues from the same history. Reasoning is
   replayed only to its own origin (SPEC §4.5) and dropped elsewhere with a conversion-report
   entry; a worker without native tool calls sees the history through the fallback protocol.
   The rule of ADR 0005 still applies where a server holds state the transcript cannot carry
   (stored responses), and to the proxy (Phase 8), where the harness owns the loop.
   `RouterState.pinned` keeps a task on its endpoint in those cases.
3. **The escalation rung is a band.** A task starts on the lowest rung that has a worker (one
   rung higher for high risk or low classifier confidence, and above T0 in the science track,
   where T0 never edits code). Without a pinning rule, a step may use workers up to the current
   rung, and heavy steps (all except navigate, summarize and write-text) use workers on the rung
   itself, so after an escalation the rung that failed stops doing the work while light steps
   can still use cheaper models. When the band is empty, the nearest rung with an eligible
   worker is used and the decision records it. The rung is soft: a matching `to` rule or a
   `--worker` pin overrides it, but never the hard rules (failed workers, offline mode, remote
   opt-in, context size, T0 in the science track).
4. **Gates run when the worker calls `finish`** (or answers twice without a tool call). A failed
   verdict goes back as the result of the `finish` call. After `gate_attempts` failures (K = 2)
   on one rung the ladder moves up. If there is no higher rung the task fails with
   `gates_failed`; if there is one but the escalation budget is spent, the task stops with
   `budget_escalations`.
5. **Review sends the work back at most once.** After the gates pass, a review step answers
   APPROVE or ISSUES; ISSUES returns the work to the worker once, and the gates run again.
   An answer that is neither counts as approval and is recorded as unparsed.
6. **Worker facts come from data, in a fixed order**: the project's `[models."<name>"]` table,
   the latest probe profile and calibration, the registry entry, then a size written in the
   model name. The tier follows from the parameter count (≤ 4.5B cpu, ≤ 16B t1, ≤ 130B t2,
   else t3); a non-local endpoint is the remote rung; an unknown size is treated as t1. A
   worker whose probe failed native tool calls, or whose registry entry has none, uses the
   fallback protocol.
7. **Other limits.** A run stops when it reaches `max_steps` model turns (status `stopped`), and
   fails as `stuck` when eight work steps in a row bring no new file read, search, edit or test
   outcome and the ladder cannot move up. The classifier's call is ledger step 0 with step
   type `classify`.

## Consequences

- C-002 can be shown with two endpoints and two rules, and every switch event carries the
  reason and the estimated re-reading cost.
- On a machine with a GPU model and a CPU tiny model, a task starts on the tiny model and moves
  up after two failed verifications. Projects that want to start higher write a rule, for
  example `to = { tier = ["t2", "t3"] }` for heavy steps.
- Phase 5 adds HANDOFF briefs above the uncached-token threshold and the per-server prefix
  registry; Phase 2 approximates the prefix registry from the transcript each worker last saw.
