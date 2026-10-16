# ADR 0022: Tool calls parsed from the reply text on chat-completions endpoints

- Status: accepted
- Date: 2026-10-16
- Phase: 3

## Context

SPEC §4.4: "Server-side parsing is used only after `ssw probe` passes single, parallel, streamed
and schema-valid calls for that (engine, parser, model); otherwise parse raw text." Until now
only the prompt-based formats (text-completion, llama.cpp native) parsed raw text. A
chat-completions endpoint whose server did not parse a model's calls left that model with the
fallback protocol alone.

The CI model set exposed the gap. The llama.cpp server (b11669) leaves Granite-3.3's
`<|tool_call|>` special token out of the reply text and returns the call list as plain content,
even with `--special` (docs/RESEARCH.md, "Granite 3.3 tool calls on the llama.cpp server").
Under the fallback protocol, Granite-3.3-2B answered directly instead of calling the tool. The
model therefore failed both tool paths of the probe verdict (ADR 0021), although it does write
well-formed calls.

## Decision

1. **Parsing on chat-completions.** `CodecOptions.tool_parser`, a syntax from the parser catalog,
   now applies to the chat-completions codec too. When a request offers tools and the reply has
   no `tool_calls`, the codec parses the calls out of the reply text with that syntax and gives
   them synthetic IDs. Calls the server parsed always win. A stream passes text through until a
   call section could start, then buffers it and parses it when the stream ends.
2. **History in the model's syntax.** With the option set, the codec sends earlier assistant
   calls back written in the model's own syntax inside `content`, not as `tool_calls`. Templates
   such as Granite-3.3's render only `role` and `content`, so a `tool_calls` field would never
   reach the model. Every catalog syntax has a renderer for this.
3. **Where it is set.**
   - `tool_parser` on an `[[endpoints]]` entry, for a configured server.
   - `tool_parser` in a `[models."<name>"]` table, for one model; this wins.
   - For managed models, the registry recipe of the engine that serves them:
     `[engines.llamacpp] tool_parsing = "client"` parses the entry's `tool_syntax`.

   The recipe's existing `tool_parser` keeps its meaning: the engine's own parser flag
   (vLLM, SGLang).
4. **The granite syntax.** The catalog gains `granite`: `<|tool_call|>` (or Granite 3.1's plain
   `<tool_call>`) followed by a JSON array of `{"name", "arguments"}` objects.
   - Servers may drop the special-token marker, so a reply that is nothing but such an array
     also counts. The checks are strict: the whole reply is the array, and every item has exactly
     `name` (a non-empty string) and `arguments` (an object).
   - The bare-array rule applies only when the syntax is configured. Parsing with no known
     syntax (`parse(text, None)`) still needs a marker.
   - vLLM matches the marker by token ID instead and never reads bare arrays. Over HTTP,
     Stepswitch sees only text, so this rule is its own.
5. **Probe and matrix.**
   - The probe runs its tool checks with the endpoint's parser and records it in the profile
     and the export (`tool_parser`).
   - `ssw probe --ci-set` uses the recipe, as the managed worker does.
   - The matrix lists such models as "native, parsed from text (`<syntax>`)".
   - The verdict of ADR 0021 is unchanged: these are native calls in the model's own syntax.

## Consequences

- Granite-3.3 on the llama.cpp server is probed and runs with native calls that Stepswitch
  parses. Its tool-call IDs are synthesized, and the `tool_call_ids` check reports that.
- When the granite syntax is configured, an answer that is exactly a JSON array of
  name/arguments objects is read as calls. The strict shape makes this unlikely, and the option
  is per model.
- Adding a syntax means adding a parser and a renderer to the catalog.
