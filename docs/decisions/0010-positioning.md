# ADR 0010: Positioning against existing projects (SPEC §1)

- Status: accepted
- Date: 2026-10-16
- Phase: 0

## Context

SPEC §1 lists fourteen items as "New in Switchyard". The claims system depends on honest
positioning, so Phase 0 searched for prior art for each item (docs/RESEARCH.md, "Positioning
check"). Findings:

- Per-turn or per-request routing inside agent sessions already exists in a semantic router
  project, in an agent SDK's router, and in request-routing proxies for coding agents. One
  harness sends per-request class hints to gateways, and another lets an extension route every
  request (virtual models).
- Cache-aware switching, meaning a router that prices the cost of moving a session to another
  model's cold prefix cache, already exists in that semantic router project and in a hosted
  router.
- Choosing a (harness CLI, model, effort) triple per task exists in a small recent project.
- Rule-based per-task model routing with fallback chains exists in orchestration frameworks.
- For items 5 to 14 (best-of-N by tests, disagreement signal, learned router, fork and replay,
  handoff format, own-task benchmark, science track, zero-egress audit, slow lane, claims
  registry), prior art exists for parts of each. The combination does not appear in one tool.

## Decision

1. Move "cache-aware switching" to the "already exists elsewhere" list. Switchyard keeps it as a
   feature (time-based, measured prefill cost model), with no novelty claim.
2. Reword the novelty of item 1 to: **step-type-aware routing inside one task, with a policy that
   learns from the user's own verified outcomes, and every switch logged with its reason and
   cost**. Step-level switching remains the headline feature, but nothing claims it is new on its
   own.
3. Reword item 2 to: **learned, verification-scored routing over (harness, model) pairs, per task
   and per step**.
4. Items 5 to 14 are described as what Switchyard *combines* or *integrates*, each with its
   closest prior art. Public text never says "first" or "only"; the claims lint already rejects
   such words.
5. Add to the "already exists" list: per-turn routing inside agent sessions, per-request router
   hooks inside harnesses, per-request class hints from harnesses to gateways, category-based
   model routing for delegated subtasks, rule-based per-task routing with fallback chains, and
   per-task harness-and-model selection.

## Consequences

- SPEC §1 is updated accordingly (SPEC v0.3).
- The README and launch posts lead with verifiable outcomes (claims C-002, C-005, C-012, C-015)
  rather than novelty statements.
- The positioning table is refreshed before launch (Phase 10), because this space moves monthly.
