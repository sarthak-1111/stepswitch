# ADR 0004: Proxy design (`sy proxy`)

- Status: accepted
- Date: 2026-10-16
- Phase: 0 (implemented in Phase 8; the per-request routing core is shared with Phase 2)

## Context

SPEC §12 item 2: external harnesses point at a local endpoint that accepts every supported API
format and forwards to any endpoint in any format; the router picks a model per request. Phase 0
research (docs/RESEARCH.md: "Harnesses", "API formats", "Positioning check") found:

- One major harness speaks only the messages format over SSE. It supports a base-URL variable
  and per-slot model variables, and with a documented opt-in it sends a request-class header
  (main, subagent, workflow, compaction, auxiliary). It assumes a 200K context window for model
  IDs it does not recognise unless a context-size variable is set, and its documentation says
  routing it to other models through a gateway is not supported upstream.
- Pi can route every request itself through a virtual-model `route()` hook, so it needs no
  proxy. OpenCode and Kimi Code are Agent Client Protocol agents. Oh My Pi uses port 4000 for a
  gateway fallback.
- The responses format has stateful features (stored responses, chaining by previous response
  ID, a WebSocket mode), while local engines serve it statelessly. Signed reasoning must not
  reach a different model.
- Mid-request model switches inside an open tool loop break continuity; switching is safe at
  user-turn boundaries.

## Decision

1. **Process and binding.** The proxy is a FastAPI router that runs alone (`sy proxy`) or
   mounted beside the Task API. It binds to 127.0.0.1 only. Default port **7778** (7777 is the
   dashboard; 4000 is avoided because Oh My Pi uses it). It requires a local token, generated
   on first start and stored under the gitignored state directory, and accepts that token
   through each format's usual auth header.
2. **Inbound routes**: `/v1/chat/completions`, `/v1/completions`, `/v1/responses` (HTTP SSE;
   WebSocket mode later), `/v1/messages` and `/v1/messages/count_tokens` (answered by the
   tokenizer service and marked as an estimate), `/v1beta/models/{model}:generateContent` and
   `:streamGenerateContent`, Ollama-native `/api/chat`, `/api/generate`, `/api/tags`,
   `/api/show`, and `/v1/models`.
3. **Pipeline per request**: decode to IR → session lookup → router (ADR 0005) → encode for
   the chosen upstream → stream back, translated into the caller's format. Errors are mapped
   into the caller's error shape. Retries happen only before the first byte reaches the caller.
4. **Model names are slot hints.** `sy:auto`, `sy:plan`, `sy:edit`, `sy:fast`, `sy:review`,
   registry model names (pass-through), and per-harness aliases from config. A harness's
   request-class header, when present, refines the step type.
5. **Sessions and step boundaries.** A session key comes from `x-switchyard-session` or a hash
   of the system prompt and first user turn. Per-session state holds the current worker, the
   step counter, whether a tool loop is open, the prefix registry used for cache-aware
   switching, and a bounded response store for stateful responses requests. **The proxy
   switches models only at a new user turn, never while a tool loop is open.**
6. **Reasoning.** Opaque or signed reasoning is stripped from history whenever the upstream
   differs from its origin; same-origin continuations keep it (ADR 0003).
7. **Accounting.** Every proxied request becomes a step record with decision, switch event,
   server-reported usage and timing, so external harness runs appear in the ledger, the
   trajectory viewer and the dashboard exactly like built-in agent runs.
8. **Egress.** Upstream calls go through `switchyard.net`; offline mode refuses remote upstreams
   and the audit log records every connection.
9. **Harness specifics live in adapters, not in the proxy.** For example, the messages-format
   harness adapter sets the base URL, slot model names, the context-size variable from the
   target model's profile and the hint-header opt-in. The Pi adapter uses virtual-model routing
   directly. ACP agents are driven by the generic ACP client, with model selection through the
   protocol where offered and the proxy as their model endpoint otherwise.

## Consequences

- Public claims about running a third-party harness on other models are scoped to what our
  contract tests prove; they never imply upstream support.
- The proxy keeps conversation state locally (bounded by TTL and size, inside the project state
  directory), never remotely.
- Responses-format WebSocket mode and the interactions format are deferred until there is demand.
- Because switching waits for user-turn boundaries, a long tool loop runs on one model; the
  built-in agent, which knows its own step types, can switch at finer boundaries.
