# ADR 0005: Router design

- Status: accepted
- Date: 2026-10-16
- Phase: 0 (implemented in Phase 2; learning in Phase 7; cache-aware cost model in Phase 5)

## Context

Step-level model switching inside one task is the headline feature (SPEC §0, §7, §12.1). The
router must choose a worker, a (harness, endpoint, settings) triple, for every step. It must
work CPU-only, including with no model installed at all. Every decision has to be explainable,
since decisions are shown in the dashboard and asserted by claim tests (C-002, C-003, C-015).
It must also be deterministic for replay (C-014), testable with fake workers, and able to learn
from the user's own outcomes later (SPEC §10).

Prior art (docs/RESEARCH.md, "Positioning check" and "Multi-agent orchestration"; ADR 0010):
first-match rule routing per task or per request type already exists in task-DAG runtimes and
in request-routing proxies for coding agents. Learned per-query routers choose between two models
from preference data. A semantic router routes per turn inside agent sessions and already prices
handoff and prefix-cache cost, refusing to switch during an active tool loop. Per-task selection
of a (harness, model) pair also exists. What none of them does is route by step type with a
policy that learns from the user's own verified outcomes. That combination is what this router
adds; cache-aware switching is a feature here, not a novelty.

Research on mid-trajectory handoffs also reports that handing a cheap model's trajectory to a
stronger model recovers less than half of the quality gap.

## Decision

1. **Pure core, edges for I/O.** `route(features, step_type, state, candidates) -> Decision` is a
   pure function. The classifier call, database reads and probe lookups happen before it; storing
   the decision happens after. `Decision` holds the inputs, every candidate with its prior,
   estimated time and tokens, switch cost, score and exclusion reason, the chosen worker, the
   RNG seed, and a one-line human-readable reason.
2. **Four stages, as in SPEC §7.1**, each recorded in the decision:
   - *Rules* are hard filters and pins: explicit flags, track constraints (T0 never edits
     science code), offline mode, budgets, protected paths, and capability requirements from
     probe profiles. They are written as data in `stepswitch.toml` (`[[policy.rules]]` with
     `when` conditions over feature fields and `to`/`exclude` actions, first match wins),
     evaluated by a small safe matcher, never `eval`. This borrows the deterministic
     `{to, when}` route shape from existing workflow tools without depending on them.
   - *Classifier*: a T0 model with JSON-Schema-constrained output and the SPEC §7.1 schema,
     cached by input hash, and called only when the step type or difficulty is not already
     known from the agent's state machine. Without a usable T0 model, a deterministic heuristic
     classifier (file types, keywords, diff size, test output) answers with low confidence.
   - *Policy*: success priors per (worker, step type, difficulty, track, domain) from registry
     roles, probe profiles and bench results (Phase 2). From Phase 7 this becomes contextual
     Thompson sampling: Beta posteriors per (worker, context bucket), backing off to coarser
     buckets when data is sparse, with reward `success − λ·time − μ·tokens` (SPEC §10).
   - *Cost model*: expected value = p_success · value_of_success − time cost − token cost −
     switch cost. The switch rule of SPEC §7.2 (switch only if gain − cost > margin) is a
     separate pure function, so C-003 can property-test it directly. For a mid-trajectory
     switch, `p_success` of the new worker is estimated *given the handoff* (a learned handoff
     penalty on top of its fresh-start rate), not as if it started fresh. Phase 2 uses default
     throughput figures; Phase 5 adds `ssw calibrate` data and the per-server prefix registry.
3. **Step boundaries only.** A step runs to completion on one worker. No switch happens while a
   tool loop is open or while the next turn depends on state held by the current endpoint
   (signed reasoning, stored responses). Switching back to a model whose server still holds the
   shared prefix is priced as cheap, using the prefix registry.
4. **Escalation is its own component.** It consumes gate results, disagreement signals,
   classifier confidence and risk, moves along T0 → T1/T2 → T3 → remote (opt-in), and emits a
   ledger event with the reason for each move (SPEC §7.3).
5. **Determinism.** Sampling uses a seeded RNG whose seed is stored in the decision. Replay
   reuses recorded decisions unless `--worker` overrides them.
6. **Alternatives rejected.** A learned per-query two-model router trained on preference data
   needs large external datasets, chooses per request rather than per step, and cannot price a
   switch; we borrow its cost-quality evaluation method only. Linear or neural contextual
   bandits were deferred: Beta-Thompson sampling with bucketed contexts is simpler, explainable
   and enough for the simulation claim (C-015); the arm/context interface allows a swap later.

## Consequences

- The router can be developed and tested entirely with fake workers and synthetic features.
  C-015 uses a simulation harness with synthetic workers.
- The classifier costs T0 tokens per uncertain step; caching and the state machine's own step
  knowledge keep that small, and the ledger reports it like any other step.
- Rules as data make routing user-editable (`ssw policy show|edit`) and diffable in reviews.
- Learned parameters stay in the local database; nothing leaves the machine (SPEC §10).
