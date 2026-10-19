# ADR 0035: Asking again through the fallback protocol when an engine cannot parse a tool call

- Status: accepted
- Date: 2026-10-20
- Phase: 11

## Context

The first Phase 11 comparison run (docs/RESEARCH.md, "Engine tool-call parse failures on
Ollama") failed 5 of the router's 42 task runs on one pattern:

1. Ollama could not parse a tool call that gpt-oss:120b generated, and answered with HTTP 500.
2. The client sent the same request twice more, and it failed the same way.
3. The agent took the worker out for the rest of the task and failed over to qwen2.5:32b.
4. Ollama stopped qwen2.5:32b with its repeat limit, which left no worker, so the task failed.

Claude Code through `ssw proxy` hit the same server and passed every run, because it retries
API errors itself.

The model is not at fault in such a failure: it answered, and the engine's parser rejected
the answer. Stepswitch already has a protocol that does not depend on the engine's parser.
That is the fallback tool protocol (SPEC §4.4, ADR 0003): the tools are described in the
prompt, the reply is constrained to a JSON action where the endpoint can, and Stepswitch
parses it.

## Decision

1. **Recognize the failure.** `compat.errors.tool_call_unparsed` matches an engine's report
   that it could not parse a model's tool call. The only report verified so far is Ollama's
   `error parsing tool call` (0.34.4). Other engines' wording is added when it is observed.
2. **Don't resend it unchanged.** The client does not retry such an error. Resending the
   same request tends to fail the same way, and it costs a full generation each time.
3. **Ask again through the fallback protocol.** When a native-protocol worker's step fails
   this way, the built-in agent asks the same worker for the same step once more, through
   `FallbackModel`. The step and its worker stay the same. The trajectory records a
   `tool_fallback` event. If that attempt fails too, the step fails over as any model error
   does.
4. **Measure again.** The router's comparison runs are run once more with this change. The
   first run stays in `bench/results/`, and PROGRESS reports both. The claims read the newest
   run of each contestant (ADR 0034), so the second run is the one they compare. There will
   be no further reruns in pursuit of a result.

## Consequences

- A malformed tool call costs one fallback request instead of two futile retries and a
  failover.
- Harness runs through the proxy no longer get those client retries either. The error goes
  back to the harness at once, and the harness retries as it does for any API error.
- Replays answer from the recording, so a recorded fallback answer replays as the step's
  answer.
