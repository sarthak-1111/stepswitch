# ADR 0003: Compatibility layer

- Status: accepted
- Date: 2026-10-16
- Phase: 0 (codecs and probe in Phase 1; fallback protocol in Phase 2; matrix in Phase 3)

## Context

SPEC §4 promises that any model a supported engine can serve, and any endpoint in a supported
API format, works as a worker, and that nothing outside `compat` talks to a model directly.
Phase 0 research (docs/RESEARCH.md: "API formats", "Format conversion notes", "Engines",
"Tool-call formats", "Engine-side tool parsers", "Gateways and routers") found:

- **More formats, more servers.** Besides chat-completions, responses, messages and
  generate-content, there is an open, vendor-neutral specification of the responses format with
  compliance tests. A newer "interactions" format is generally available, and the
  generate-content guides are now labelled legacy. The llama.cpp server, Ollama, vLLM and SGLang
  serve chat-completions plus stateless responses and messages routes, with gaps.
- **Reasoning differs everywhere.** Field names are `reasoning_content` (llama.cpp, SGLang),
  `reasoning` (vLLM; also Ollama's chat-completions route) and `thinking` (Ollama native), plus
  think tags and harmony channels. Three hosted formats return signed or encrypted reasoning
  that must go back unchanged, and only to the model that produced it.
- **Usage semantics differ.** For example, messages-format `input_tokens` excludes cached
  tokens, while chat-completions reports cached tokens as a subset of prompt tokens.
- **Tool calling is uneven.** Some servers omit tool-call IDs. `tool_choice` and assistant
  prefill are unsupported on some routes and rejected by some models. Server-side parser lists
  are long (dozens of names per engine), named explicitly per model, and documented behind the
  code. Two engines offer no constrained decoding.
- **Templates and syntaxes vary per model version.** The tool-call syntax can change between
  releases of one family. One major family's published chat template re-renders past tool calls
  differently from what the model generated and stamps the current date, so re-rendered history
  no longer matches the server's prefix cache. Reasoning-retention rules also differ per model:
  some need their own prior reasoning back during tool loops, others strip it.
- **Supply-chain risk.** The multi-provider gateway library named in SPEC §4.1 shipped two
  compromised releases in 2026 (a credential stealer that runs at interpreter start). Its proxy
  extra pulls a proprietary package, and by default it fetches a remote cost map at startup.

## Decision

1. **Internal representation (IR)** as in docs/ARCHITECTURE.md §5, with these rules:
   - Reasoning is stored as a part with optional plain text, an optional opaque payload
     (signature or encrypted content) and its **origin** (format, endpoint fingerprint, model).
     Opaque payloads are replayed only to the same origin. Otherwise they are dropped and
     reported. Plain reasoning text is never sent to a different model (SPEC §4.5).
   - Usage is normalized so that `input_tokens` always includes cached tokens. Separate fields
     hold cached input, cache writes, output and reasoning tokens. The raw usage object is kept
     for audit, so C-019 can compare exactly what the server said.
   - Missing tool-call IDs get deterministic synthetic IDs (turn index and call index); the
     mapping is kept so results can be matched back in every format.
2. **Codecs**: chat-completions, responses, messages, generate-content, Ollama native,
   llama.cpp native, raw text completion (Ollama's `raw: true`; llama.cpp `/completion` with a
   template rendered by the server's `/apply-template` or by us). The responses codec follows
   the open specification and treats stateful features (stored responses, previous-response
   chaining) as proxy-side state (ADR 0004). The interactions format is a watch item, outside
   the C-004 wording until a codec ships.
3. **Lossless scope.** Conversion is lossless for messages (text, images and files by
   reference), function tool definitions, tool calls and tool results. Everything else, such as
   opaque reasoning across origins, server-side built-in tools, citations, cache-control hints,
   n>1 candidates, logprobs and audio, goes into a `ConversionReport`: reported, approximated
   or, in strict mode, rejected. This is exactly the SPEC §4.1 rule with the scope spelled out.
4. **Probe before trusting.** Every capability that varies by server is decided per endpoint by
   `sy probe` and stored in the capability profile, never inferred from the format name. That
   covers native tool calls, parallel calls, streamed tool arguments, tool-call IDs,
   `tool_choice` forcing, assistant prefill, the reasoning field, usage semantics, structured
   output, context length, prefix-cache behaviour and throughput.
5. **Tool-call parsing.** Server-side parsing is used only when the probe passed for that
   endpoint and model. Registry engine recipes carry the per-engine parser name. Otherwise the
   output is parsed by our incremental parsers, which also work while streaming. The parsers come
   from a catalog of syntaxes: harmony and harmony-like recipient formats, JSON-in-tags,
   XML-style function/parameter tags (including namespaced, nested-element variants),
   key/value-tag pairs, special-token sections and control-token markup, bracketed
   name/arguments markers, pythonic call lists, and non-JSON argument syntaxes. Each syntax
   has a conformance fixture. The parser is selected by **hub ID plus chat-template hash** (or
   the encoder script's hash when a family ships no Jinja template), not by family name.
   Templates not in the catalog fall back to detection by template markers (the approach the
   llama.cpp server uses), confirmed by the probe.
6. **Fallback tool protocol.** For models without native tools, the tools are described in the
   prompt and the model must emit one JSON action object (`{"tool": ..., "arguments": ...}` or
   `{"final": ...}`). Output is constrained by grammar or JSON Schema where the endpoint offers
   it (llama.cpp `json_schema`/`grammar`, structured `response_format` on the GPU servers,
   Ollama `format`), and otherwise validated and repaired with at most two retries. For
   reasoning models, the constraint starts only after the reasoning block (trigger-based grammars
   or reasoning-aware structured output); a probe case checks this. Removed legacy request fields
   are never sent.
7. **Effort mapping.** The IR uses `effort: none|minimal|low|medium|high|xhigh|max`, a superset
   of the four SPEC levels, plus an optional `budget_tokens`. Each endpoint profile stores its
   supported subset, the mechanism (request field, thinking budget, template argument, numeric
   scale, mode switch or system-prompt line), its default level, and whether thinking can be
   switched off (several current models cannot). Unsupported levels are clamped to the nearest
   supported one and the clamp is reported. An unset effort means the endpoint's default, which
   for some models is an adaptive mode.
8. **Reasoning retention.** Registry entries and probes record a per-model policy:
   `drop | keep-since-last-user | keep-for-tool-turns | keep-all`. Same-model continuations
   follow it. Where a template exposes a retention switch (a preserve- or clear-thinking
   argument), the codec passes it explicitly instead of relying on the template default.
   Switching models always drops reasoning (rule 1).
9. **Rendering fidelity.** When Switchyard renders a prompt itself (raw completion endpoints,
   harmony models), history is rendered byte-identically to what the model generated, using the
   family's reference renderer or an equivalent, with any template date pinned per session.
   This keeps server prefix caches valid. Registry entries record the template source (Jinja
   template, reference encoder script, or engine tokenizer mode), because some families ship
   only a Python encoder; those families get native renderers for the raw completion path.
   Channel and recipient metadata are IR fields; losing them in an outbound format is reported.
10. **Gateway bridge (optional, off by default).** Base package only, never extras, installed in
   its own virtual environment, run out of process behind the chat-completions codec, locked by
   hash, pinned to a release that has been out for at least seven days, using a local cost map,
   and reporting every degradation into the `ConversionReport`. The default install never
   contains it.

## Consequences

- The probe grows by about six checks; profiles become the single place where per-server quirks
  live, and the compatibility matrix is generated from them.
- Fixture maintenance: conformance tests replay recorded server traffic plus specification
  examples; recordings are refreshed when engines are upgraded.
- Signed or encrypted reasoning makes some same-model continuations stateful; the IR carries it,
  and switching models discards it by design.
- The long tail of hosted providers is reachable only through the optional bridge, with an
  explicit supply-chain posture.
