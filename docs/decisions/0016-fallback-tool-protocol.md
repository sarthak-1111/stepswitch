# ADR 0016: Fallback tool protocol

- Status: accepted
- Date: 2026-10-16
- Phase: 2

## Context

SPEC §4.4 asks for a fallback text protocol: describe the tools in the prompt and force one
JSON action object with grammar or JSON-Schema constrained decoding where the server has it,
and validate and repair where it does not. The SPEC leaves open how a model ends a step without
a tool, how `tool_choice` maps onto the protocol, how tool history is shown, which constraint
each format gets, and how failures are retried. Building `FallbackModel`
(`stepswitch.compat.toolcall.fallback`) and running it live on small CPU models settled those
questions (docs/RESEARCH.md, "Verified during Phase 2").

## Decision

1. **Reply format.** Each reply is one JSON object: `{"tool": <name>, "arguments": {...}}`, or
   `{"final": "<answer>"}` to answer without a tool. The constraint schema has one closed
   branch per tool plus the final branch, with keys in that order (grammars keep insertion
   order, so the model writes the tool name first). A final answer comes back as plain text
   with finish reason `stop`; a tool action becomes one synthetic `ToolCallPart` with finish
   reason `tool_calls`. One action per reply.
2. **`tool_choice`.** `required` removes the final branch; a named tool offers only that tool,
   without the final branch, and an unknown name is an error; `none` offers no tools while the
   tool history is still shown as text. A request with no tools and no tool history passes
   straight through.
3. **History.** Earlier calls are shown as the JSON action lines the model would have written.
   Results become a user message starting "Result of <tool> (call <n>):" (marked `[error]` for
   errors), numbered in call order, with images kept. User text right after results joins that
   message, so roles keep alternating. Earlier plain assistant answers are wrapped as
   `{"final": ...}`. `tools`, `tool_choice`, `parallel_tool_calls` and any pre-rendered prompt
   are dropped; dropping a pre-rendered prompt is reported as a loss.
4. **Protocol text.** One line per tool: name, arguments with types (objects inside arguments
   are spelled out field by field, for example `edits?: {op: "replace"|..., start: string}[]`),
   and the first line of the description. A tool may carry example arguments in the IR
   (`ToolSpec.examples`, which no wire format sends); the first one is shown under the tool's
   line and replaces the placeholders in the worked examples. Live runs showed why: with an
   argument rendered as a bare `object[]`, a small model never attempted the edit.
5. **Constraints.** The default constraint is `json_schema` for every format except
   text-completion, which gets none (validate and repair only); a probe profile can override it
   (`constraint_for`). If the endpoint rejects the constraint (`bad_request` or `unsupported`),
   the call is retried once without it, and that retry is not counted as a repair. If a
   constrained reply has reasoning but no answer, later attempts run unconstrained. Only the
   answer text is parsed, never the reasoning.
6. **Validate and repair.** Parsing is tolerant: code fences, prose around the object, reasoning
   blocks, Python-style dicts, trailing commas, common key variants (`name`, `parameters`,
   `args`, `input`, `action_input`, wrapper objects), arguments sent as a string or at the top
   level, case and `-`/`_` differences in tool names, null optional arguments, and numbers or
   booleans sent as strings where the schema says so. The arguments are then validated against
   the tool's full schema. An invalid reply gets a repair message naming the problem, at most
   `max_repairs` (2) times; after that the last reply's text is returned with the failure in the
   conversion report.
7. **No network during validation.** Schemas are validated with an offline registry
   (`jsonschema_specifications.REGISTRY`), because the default registry of the validation
   library fetches remote `$ref`s itself, which would be egress outside `stepswitch.net`.
8. **Accounting.** Usage is summed over all attempts of one turn (each attempt's raw usage is
   kept), and every turn through the protocol adds an "approximated" `tools` loss to the
   conversion report naming the constraint and the number of repairs. The ledger therefore shows
   which turns went through the fallback.
9. **Repeated calls.** The built-in agent adds a short hint to a tool result that repeats the
   previous call's result for the same call ("Repeating it changes nothing: act on the
   result..."). It is advice only: the stuck rule of ADR 0015 still ends a run without progress.
10. **The C-005 proof model.** The proof uses `phi-3.5-mini-instruct`, an instruction-tuned chat
    model whose template has no tool calling, served on CPU by the managed llama.cpp server. The
    registry's base model for fill-in-the-middle (Qwen2.5-Coder-1.5B) was tried first: the
    protocol carried every one of its actions, but it did not solve the toy task. The new entry
    is in no default tier, because its KV cache (no grouped-query attention, 384 KiB per token)
    does not fit the CPU tier's RAM budget; it is pulled by id.

## Consequences

- Any model that writes text can drive the agent's tools; how well it does depends on the model,
  and the compatibility matrix (Phase 3) records that per model.
- Not covered yet: streaming through the protocol (the whole reply is needed to check it), lazy
  grammars on `/completion` for reasoning models, hosted strict-schema limits (covered only by
  the unconstrained retry), and engines other than llama.cpp and Ollama.
- The capability probe (`stepswitch.compat.probe`) has its own copy of the action schema and
  parser from Phase 1; it should move onto this module when the probe is next changed.
