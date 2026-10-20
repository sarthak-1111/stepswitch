# Research notes

Phase 0 research for Switchyard, checked on 2026-10-16. Every fact in this file was read from a
primary source on that date: project repositories and release pages, package indexes, the model
hub, and official references. Sources are listed per project under "Details". Anything that could
not be confirmed is marked `verified: no` with the reason. Facts age quickly: re-check anything
older than a few weeks before code depends on it (working rule 1), and update the date here.
The project has since been renamed Stepswitch (ADR 0001); the Phase 0 notes keep the working
name. Facts checked while building later phases are in "Verified during Phase 1" at the end, and
entries they supersede are marked where they stand.

The work was split into thirteen parallel research tasks: one per SPEC §22 row, plus the two
tables that the compatibility claim depends on (API formats, open-weight model families), the
SPEC §5 model candidates, and the name and stack check. Prose follows ADR 0007 (no company names;
URLs are citations). Spot checks of the most surprising findings were repeated independently
before this file was assembled.

Contents: Index · API formats · Format conversion notes · Engines · Tool-call formats ·
Engine-side tool parsers · Open-weight model families · Default model candidates (SPEC §5) ·
Harnesses · Multi-agent orchestration · Positioning check (SPEC §1) · Token counting ·
Network isolation · Name check · Stack · Details · SPEC corrections · Verified during Phase 1.

## Index

One row per project in SPEC §22, plus the name check and the stack. `Verified` is yes when the facts were confirmed from primary sources on the date given; no means they could not be confirmed (see the project's details).

| Area | Project | Verified | Checked | Version | License |
|---|---|---|---|---|---|
| Big models on small hardware | AirLLM | yes | 2026-10-16 | 4.0.0 (2026-09-05) | Apache-2.0 |
| Big models on small hardware | KTransformers | yes | 2026-10-16 | 0.7.2 (2026-10-15) | Apache-2.0 |
| Big models on small hardware | llama.cpp | yes | 2026-10-16 | v0.7.0 = build b11669 (2026-10-15) | MIT |
| Big models on small hardware | bitnet.cpp | yes | 2026-10-16 | none tagged (main last commit 2026-07-27) | MIT |
| Serving and engines | vLLM | yes | 2026-10-16 | 0.31.0 (2026-10-05) | Apache-2.0 |
| Serving and engines | SGLang | yes | 2026-10-16 | 0.5.22 (2026-10-16) | Apache-2.0 |
| Serving and engines | LMCache | yes | 2026-10-16 | 0.5.6 (2026-10-15) | Apache-2.0 |
| Serving and engines | KV-cache quantization plugins | yes | 2026-10-16 | engine flags in vLLM 0.31.0, SGLang 0.5.22, llama.cpp b11677, Ollama 0.40.5; libraries: kvpress 0.5.5 (2026-09-17), transformers 5.20.0 (2026-10-15), optimum-quanto 0.2.7 (2025-03-06), hqq 0.2.8.post1 (2025-10-20), KIVI (research repo, last commit 2025-11-20) | engines: see their rows; kvpress Apache-2.0, transformers Apache-2.0, optimum-quanto Apache-2.0, hqq "Apache 2" (PyPI field), KIVI MIT |
| Tool-call and template formats | engine-side tool parsers | yes | 2026-10-16 | vLLM 0.31.0 (2026-10-05), SGLang 0.5.22 (2026-10-16), llama.cpp b11677 (2026-10-16), Ollama 0.40.5 (2026-10-15) | vLLM and SGLang Apache-2.0; llama.cpp and Ollama MIT |
| Serving and engines | Ollama | yes | 2026-10-16 | 0.40.5 (2026-10-14) | MIT |
| Serving and engines | MLX-LM | yes | 2026-10-16 | 0.32.2 (2026-10-16) | MIT |
| Serving and engines | transformers | yes | 2026-10-16 | 5.20.0 (2026-10-15) | Apache-2.0 |
| Serving and engines | ExLlamaV3 | yes | 2026-10-16 | 1.6.2 (2026-10-14); server TabbyAPI: rolling, no tagged releases | MIT (TabbyAPI: AGPL-3.0) |
| Serving and engines | llama-swap | yes | 2026-10-16 | v265 (2026-10-14) | MIT |
| Serving and engines | TensorRT-LLM | yes | 2026-10-16 | 1.2.1 stable (2026-04-20); 1.4.0rc0 pre-release (2026-10-13) | Apache-2.0 (LICENSE notes third-party portions) |
| API formats and gateways | chat-completions format | yes | 2026-10-16 | REST v1, living reference (response header `openai-version: 2020-10-01`) | n/a (hosted API reference) |
| API formats and gateways | responses format | yes | 2026-10-16 | REST v1, living reference | n/a (hosted API reference) |
| API formats and gateways | messages format | yes | 2026-10-16 | version header value `2023-06-01`, living reference | n/a (hosted API reference) |
| API formats and gateways | generate-content format | yes | 2026-10-16 | v1beta, living reference (guides now titled "Legacy") | n/a (hosted API reference) |
| API formats and gateways | Ollama native API | yes | 2026-10-16 | living docs (not version-pinned; reference hardware runs 0.34.4) | n/a (project docs) |
| API formats and gateways | llama.cpp native API | yes | 2026-10-16 | server README on master (living; no API version) | n/a (project docs) |
| API formats and gateways | raw text-completion endpoints | yes | 2026-10-16 | legacy `/v1/completions` shape, living | n/a |
| API formats and gateways | Open Responses (additional) | yes | 2026-10-16 | 2026-04-24 (previous 2026-01-15) | Apache-2.0 |
| API formats and gateways | interactions format (additional) | no | 2026-10-16 | GA June 2026; path shown as v1beta and as v1beta2 on different pages | n/a (hosted API reference) |
| API formats and gateways | embeddings endpoints (additional) | no | 2026-10-16 | living | n/a |
| API formats and gateways | rerank endpoints (additional) | no | 2026-10-16 | living | n/a |
| API formats and gateways | batch endpoints (additional) | yes | 2026-10-16 | living | n/a |
| API formats and gateways | responses WebSocket mode (additional) | yes | 2026-10-16 | living | n/a |
| API formats and gateways | realtime endpoints (additional) | no | 2026-10-16 | not verified | n/a |
| API formats and gateways | LiteLLM | yes | 2026-10-16 | 1.105.1 (2026-10-14) | MIT for the core; `enterprise/` directory and the `litellm-enterprise` package are proprietary |
| API formats and gateways | claude-code-router | yes | 2026-10-16 | 3.1.3 (2026-10-09) | MIT |
| API formats and gateways | RouteLLM | yes | 2026-10-16 | 0.2.0 (2024-07-08) | Apache-2.0 |
| Tool-call and template formats | open-model chat templates and tool-call formats | yes | 2026-10-16 | part 1: 14 families | per model, see table |
| Tool-call and template formats | harmony format | yes | 2026-10-16 | `openai-harmony` 0.0.8 (PyPI, 2025-11-05; no GitHub releases published) | `Apache-2.0` |
| Harnesses | Oh My Pi | yes | 2026-10-16 | 18.11.1 (2026-10-15) | MIT |
| Harnesses | Pi | yes | 2026-10-16 | 1.3.0 (2026-10-15) | MIT |
| Harnesses | OpenCode | yes | 2026-10-16 | 1.19.2 (2026-10-16) | MIT |
| Harnesses | oh-my-openagent | yes | 2026-10-16 | 5.1.33 (2026-10-13) | SUL-1.0 (Sustainable Use License 1.0, source-available) |
| Harnesses | Kimi Code | yes | 2026-10-16 | 2.2.0 (2026-10-14) | MIT |
| Harnesses | Claude Code | yes | 2026-10-16 | 2.1.301 (2026-10-15); npm `stable` tag 2.1.292 | Proprietary (all rights reserved; commercial terms) |
| Harnesses | mini-swe-agent | yes | 2026-10-16 | 2.4.6 (2026-07-23) | MIT |
| Harnesses | SWE-agent | yes | 2026-10-16 | 1.1.0 (2025-05-22) | MIT |
| Multi-agent orchestration | open-multi-agent | yes | 2026-10-16 | 1.21.2 (2026-10-09) | MIT |
| Multi-agent orchestration | Conductor | yes | 2026-10-16 | 0.1.42 (2026-10-06) | MIT |
| Multi-agent orchestration | Claude Squad | yes | 2026-10-16 | 1.0.20 (2026-08-20) | AGPL-3.0-only |
| Multi-agent orchestration | Agent Client Protocol | yes | 2026-10-16 | schema 1.25.0 (2026-10-09); wire `protocolVersion` 1 | Apache-2.0 |
| Context and tokens | Aider repo map | yes | 2026-10-16 | aider-chat 0.86.2 (2026-02-12) | Apache-2.0; tag queries adapted from grammar repos (mostly MIT) |
| Context and tokens | tree-sitter | yes | 2026-10-16 | py-tree-sitter 0.26.0 (2026-06-30); tree-sitter-language-pack 1.22.1 (2026-10-12) | MIT; MIT |
| Context and tokens | LSP client libraries | yes | 2026-10-16 | pygls 2.1.1 (2026-03-25); lsprotocol 2025.0.0 (2025-06-17); multilspy 0.1.1 (2026-10-13); SolidLSP in serena-agent 2.0.0 (2026-10-13) | Apache-2.0; MIT; MIT; SolidLSP files MIT, serena-agent package GPL-3.0-or-later |
| Context and tokens | LLMLingua | yes | 2026-10-16 | llmlingua 0.2.2 (2024-04-09) | MIT (code); LLMLingua-2 checkpoints MIT (large) / Apache-2.0 (small) |
| Context and tokens | simplicio | yes | 2026-10-16 | simplicio-cli 0.18.16 (2026-09-28) | MIT |
| Testing and verification | pytest-testmon | yes | 2026-10-16 | 2.2.0 (2025-12-01) | MIT |
| Testing and verification | Hypothesis | yes | 2026-10-16 | 6.170.0 (2026-10-15) | MPL-2.0 |
| Testing and verification | jupytext | yes | 2026-10-16 | 1.19.6 (2026-10-04) | MIT |
| Testing and verification | Playwright | yes | 2026-10-16 | Python 1.64.0 (2026-10-13); npm 1.64.1 (2026-10-14) | Apache-2.0 |
| Testing and verification | CI network isolation | no | 2026-10-16 | n/a (pytest-socket 0.8.1 (2026-08-19); bubblewrap 0.13.1 (2026-10-14)) | n/a (pytest-socket MIT; bubblewrap LGPL-2.1-or-later) |
| Name and stack | name: Switchyard | yes | 2026-10-16 | n/a (name taken; see Name check) | n/a |
| Name and stack | uv | yes | 2026-10-16 | 0.13.1 (2026-10-14) | MIT OR Apache-2.0 |
| Name and stack | Typer | yes | 2026-10-16 | 0.27.3 (2026-10-06) | MIT |
| Name and stack | FastAPI | yes | 2026-10-16 | 0.143.2 (2026-10-15) | MIT |
| Name and stack | Pydantic | yes | 2026-10-16 | 2.14.1 (2026-10-11) | MIT |
| Name and stack | httpx | yes | 2026-10-16 | 0.28.1 (2024-12-06); pre-release 1.0.dev6 (2026-08-31) | BSD-3-Clause |
| Name and stack | SQLite (WAL) | yes | 2026-10-16 | 3.54.0 (2026-10-09) | Public domain |
| Name and stack | React | yes | 2026-10-16 | 19.3.1 (2026-10-14) | MIT |
| Name and stack | Vite | yes | 2026-10-16 | 8.3.4 (2026-10-08) | MIT |
| Name and stack | TypeScript | yes | 2026-10-16 | 7.0.2 (2026-07-08) | Apache-2.0 |
| Name and stack | ruff | yes | 2026-10-16 | 0.17.1 (2026-10-15) | MIT |
| Name and stack | mypy | yes | 2026-10-16 | 2.4.0 (2026-10-01) | MIT |
| Name and stack | pytest | yes | 2026-10-16 | 9.1.1 (2026-06-19) | MIT |
| Name and stack | mkdocs-material | yes | 2026-10-16 | 9.7.7 (2026-07-17) | MIT |

## API formats

Model API formats in use today (research task A04). Field and event names were taken from the linked references on the checked date.

| Format | Endpoint(s) | Request shape | Response shape | Streaming | Tool calls | Reasoning fields | Structured output | Usage fields | Errors | Spec link (checked 2026-10-16) |
|---|---|---|---|---|---|---|---|---|---|---|
| chat-completions | `POST /v1/chat/completions`; auth `Authorization: Bearer`; optional `OpenAI-Organization`, `OpenAI-Project`; response `x-request-id`; optional request `X-Client-Request-Id` | `messages[]` roles `developer`, `system`, `user`, `assistant`, `tool` (`function` deprecated); user parts `text`, `image_url{url,detail}`, `input_audio{data,format}`, `file{file_data,file_id,filename}`; system prompt = a message in the list; `max_completion_tokens` (`max_tokens` deprecated); `stop` (up to 4); `n`; `temperature`, `top_p`, `seed`, `presence_penalty`, `frequency_penalty`, `logit_bias`, `logprobs`, `top_logprobs`; `verbosity`; `prompt_cache_key`, `prompt_cache_options`; `stream`, `stream_options` | `object: "chat.completion"`; `choices[]{index, message{role, content, refusal, tool_calls, annotations, audio}, finish_reason, logprobs{content, refusal}}`; `finish_reason`: `stop`, `length`, `tool_calls`, `content_filter`, `function_call` (deprecated) | SSE `data:` chunks, `object: "chat.completion.chunk"`, `choices[].delta{role, content, refusal, tool_calls, audio}`; tool args = string fragments in `delta.tool_calls[].function.arguments` keyed by `index`; `usage` only in last chunk with `stream_options.include_usage`; `data: [DONE]` terminator (see details) | `tools[]`: `{type: "function", function{name, description, parameters, strict}}` or `{type: "custom", custom{name, description, format}}`; `tool_choice`: `none`, `auto`, `required`, named function, named custom, `allowed_tools{mode, tools}`; `parallel_tool_calls`; call `{id, type, function{name, arguments}}` (arguments = JSON string); result `{role: "tool", tool_call_id, content}` | request `reasoning_effort`: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`; hosted message has no reasoning text field; open servers add `reasoning_content` (llama.cpp) or `reasoning` (vLLM) | `response_format`: `{type: "json_schema", json_schema{name, description, schema, strict}}`, `{type: "json_object"}`, `{type: "text"}` | `prompt_tokens`, `completion_tokens`, `total_tokens`, `prompt_tokens_details{cached_tokens, cache_write_tokens, audio_tokens, image_tokens, text_tokens}`, `completion_tokens_details{reasoning_tokens, audio_tokens, accepted_prediction_tokens, rejected_prediction_tokens, text_tokens}` | body `error{message, type, param, code}`; 401, 403, 429 (`rate_limit_error` code `slow_down`; quota codes), 500, 503 (`service_unavailable_error` code `server_is_overloaded`); `Retry-After`; `x-ratelimit-*` | https://developers.openai.com/api/reference/resources/chat |
| responses | `POST /v1/responses`; `POST /v1/responses/{response_id}/cancel` (background only); WebSocket mode (`response.create`); auth as chat-completions | `input` (string or items: `message` roles `user`, `assistant`, `system`, `developer`; `function_call`, `function_call_output`, `reasoning`, `item_reference`, `compaction_trigger`, built-in tool items); parts `input_text`, `input_image{detail}`, `input_file`; `instructions`; `max_output_tokens`; `temperature`, `top_p`, `top_logprobs`; no `stop`, `n`, `seed`, penalties, `logit_bias`; `previous_response_id`, `conversation`, `store`, `background`, `include[]`, `truncation`, `context_management`, `max_tool_calls` | `object: "response"`; `status`: `completed`, `failed`, `in_progress`, `cancelled`, `queued`, `incomplete`; `output[]` items (`message` with `output_text` or `refusal` parts, `function_call`, `reasoning`, built-in calls); `incomplete_details.reason`: `max_output_tokens`, `max_messages`, `content_filter`, `steered`; `error` | SSE, `event:` line equals `type`, every event has `sequence_number`; `response.created`, `response.in_progress`, `response.output_item.added`/`.done`, `response.content_part.added`/`.done`, `response.output_text.delta`/`.done`, `response.function_call_arguments.delta`/`.done`, `response.reasoning_text.delta`, `response.reasoning_summary_text.delta`, `response.completed`, `response.failed`, `response.incomplete`, `error` | flat `tools[]`: `{type: "function", name, description, parameters, strict}` plus built-in types and `custom`; item `function_call{id, call_id, name, arguments, status}`; result item `function_call_output{call_id, output}` (string or part list); `parallel_tool_calls`; `max_tool_calls` | `reasoning{effort, summary, context, mode}` (effort enum as chat-completions); item `reasoning{id, summary[summary_text], content[reasoning_text], encrypted_content, status}`; replay items when managing context client-side | `text.format{type: "json_schema", name, schema, description, strict}`; `text.verbosity` | `input_tokens`, `input_tokens_details{cached_tokens, cache_write_tokens}`, `output_tokens`, `output_tokens_details{reasoning_tokens}`, `total_tokens` | HTTP as chat-completions; `Response.error{code, message, param, type}` with `code` enum (`server_error`, `rate_limit_exceeded`, `invalid_prompt`, image codes, ...); stream `error` event | https://developers.openai.com/api/reference/resources/responses |
| messages | `POST /v1/messages`; `POST /v1/messages/count_tokens`; `/v1/messages/batches`; headers `x-api-key`, `anthropic-version: 2023-06-01`, `anthropic-beta`; response `request-id` | `model`; `max_tokens` (required; `0` only fills the prompt cache); `messages[]` roles `user`, `assistant` (no system role); top-level `system` (string or text blocks); blocks `text`, `image`, `document`, `search_result`, `tool_use`, `tool_result`, `thinking`, `redacted_thinking`, server-tool blocks; `stop_sequences`; `temperature`, `top_p`, `top_k` (see details); `cache_control` (top-level and per block); `metadata.user_id`; `service_tier`; `thinking`; `output_config` | `type: "message"`, `role: "assistant"`, `content[]`, `stop_reason`: `end_turn`, `max_tokens`, `stop_sequence`, `tool_use`, `pause_turn`, `refusal`, `model_context_window_exceeded`; `stop_sequence`, `stop_details`, `usage`, `container`, `diagnostics` | SSE named events `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`, `ping`, `error`; deltas `text_delta`, `input_json_delta{partial_json}`, `thinking_delta`, `signature_delta`, `citations_delta`; `message_delta.usage` is cumulative | `tools[]{name, description, input_schema, strict, cache_control, eager_input_streaming, defer_loading, allowed_callers, input_examples}`; `tool_choice{type: auto, any, tool, none; disable_parallel_tool_use}`; block `tool_use{id, name, input}` (input = object); `tool_result{tool_use_id, content, is_error}` in the next user turn | `thinking{type: adaptive, enabled (budget_tokens), disabled, between_tools; display}`; `output_config.effort`: `low`, `medium`, `high`, `xhigh`, `max`; blocks `thinking{thinking, signature}`, `redacted_thinking{data}`; pass back unchanged in tool-use turns | `output_config.format{type: "json_schema", schema}`; `strict` tools | `input_tokens` (after last cache breakpoint), `cache_creation_input_tokens`, `cache_read_input_tokens`, `cache_creation`, `output_tokens` (inclusive), `output_tokens_details`, `server_tool_use`, `service_tier`, `inference_geo`, `speed` | `{"type": "error", "error": {type, message}, "request_id"}`; 400 `invalid_request_error`, 401 `authentication_error`, 402 `billing_error`, 403 `permission_error`, 404 `not_found_error`, 409 `conflict_error`, 413 `request_too_large`, 429 `rate_limit_error`, 500 `api_error`, 504 `timeout_error`, 529 `overloaded_error`; `retry-after`; `anthropic-ratelimit-*` | https://platform.claude.com/docs/en/api/messages |
| generate-content | `POST /v1beta/{model=models/*}:generateContent`; `POST /v1beta/{model=models/*}:streamGenerateContent?alt=sse`; auth `key` query parameter (reference examples) or `x-goog-api-key` header (same host, interactions guide) | `contents[]{role: "user" or "model", parts[]}`; parts `text`, `inline_data{mime_type, data}`, `file_data{mime_type, file_uri}`, `functionCall`, `functionResponse`, `thought`, `thoughtSignature`; `systemInstruction` (Content); `tools`, `toolConfig`, `safetySettings`, `generationConfig{maxOutputTokens, stopSequences, candidateCount, temperature, topP, topK, thinkingConfig, ...}`, `cachedContent`, `continuationToken`, `serviceTier`, `store`, `labels` | `candidates[]{content, finishReason, safetyRatings, citationMetadata, tokenCount, groundingAttributions, groundingMetadata, avgLogprobs, logprobsResult, urlContextMetadata, continuationToken, index, finishMessage}`, `promptFeedback{blockReason, safetyRatings}`, `usageMetadata`, `modelVersion`, `responseId`, `modelStatus`; `finishReason` has 23 values (details) and no tool-call value | SSE with `alt=sse`; "a stream of GenerateContentResponse instances" (no named events) | `tools[{functionDeclarations[{name, description, parameters}]}]` plus built-in tools; `toolConfig.functionCallingConfig{mode: MODE_UNSPECIFIED, AUTO, ANY, NONE, VALIDATED; allowedFunctionNames}`; part `functionCall{id, name, args}` (args = object); result part `functionResponse{id, name, response, parts}` in a `user` content; several calls per turn | `generationConfig.thinkingConfig{thinkingLevel: minimal, low, medium, high; thinkingBudget; includeThoughts}`; parts with `thought: true` are summaries; `thoughtSignature` on parts, return as received (mandatory for function calling) | `responseMimeType` + `responseSchema` (verified); `responseJsonSchema` (not verified today) | `promptTokenCount`, `cachedContentTokenCount`, `candidatesTokenCount`, `toolUsePromptTokenCount`, `thoughtsTokenCount`, `totalTokenCount`, `promptTokensDetails`, `cacheTokensDetails`, `candidatesTokensDetails`, `toolUsePromptTokensDetails` (`{modality, tokenCount}`), `serviceTier` | 429 `RESOURCE_EXHAUSTED` and 503 `UNAVAILABLE` retryable; 400, 402, 403 not retryable; 408 and 5xx retryable; body shape not verified (details) | https://ai.google.dev/api/generate-content |
| Ollama native API | `POST /api/chat`, `POST /api/generate`, `POST /api/embed`, `GET /api/tags`, `POST /api/show`, `GET /api/ps`, `GET /api/version`; local server validates no API key | chat: `model`, `messages[]{role: system, user, assistant, tool; content; images (base64); tool_calls; tool_name; thinking}`, `tools`, `think`, `format`, `options{num_ctx, num_predict, temperature, top_k, top_p, min_p, stop, seed, ...}`, `stream` (default true), `keep_alive`, `logprobs`, `top_logprobs`; generate: `prompt`, `suffix`, `images`, `system`, `template`, `raw`, `context` (deprecated) | `model`, `created_at`, `message{role, content, thinking, tool_calls, images}` (chat) or `response` (generate), `done`, `done_reason` (examples: `stop`, `load`, `unload`), timing fields, `logprobs` | NDJSON (`application/x-ndjson`); final object has `done: true`, `done_reason`, metrics; `stream: false` returns one JSON object | `tools[{type: "function", function{name, description, parameters}}]`; `message.tool_calls[{function{name, description, arguments}}]` (arguments = object, no call id documented); tool result = message with role `tool` and `tool_name`; no `tool_choice` | request `think`: bool, model-defined string (api.md lists `low`, `medium`, `high`, `max`) or null; output `message.thinking` | `format`: `"json"` or a JSON Schema object | `prompt_eval_count`, `prompt_eval_cached_count`, `eval_count`, `total_duration`, `load_duration`, `prompt_eval_duration`, `eval_duration` (ns) | `{"error": "<message>"}`; 400, 404, 429, 500, 502; mid-stream NDJSON `{"error": ...}` with unchanged status | https://docs.ollama.com/api/chat |
| llama.cpp native API | `POST /completion`, `/infill`, `/tokenize`, `/detokenize`, `/apply-template`, `/embedding`, `/reranking`; `GET /slots`; `POST /slots/{id_slot}?action=save`, `restore`, `erase`; `GET`/`POST /props`; `GET /metrics`; `/lora-adapters`; `/health`; `POST /v1/chat/completions/control`; plus chat-completions, responses, messages, embeddings, rerank, `/v1/models` | `/completion`: `prompt`, `n_predict`, `temperature`, `top_k`, `top_p`, `min_p`, `stop`, `stream`, `grammar`, `json_schema`, `cache_prompt`, `id_slot`, `n_probs`, `samplers`, `return_tokens`, `response_fields`, `lora`, ...; `/infill`: `input_prefix`, `input_suffix`, `input_extra[{filename, text}]`, `prompt` | `content`, `tokens`, `stop`, `stop_type`: `none`, `eos`, `limit`, `word`; `stopping_word`, `generation_settings`, `model`, `prompt`, `timings`, `tokens_cached`, `tokens_evaluated`, `truncated`, `completion_probabilities` | SSE; `/completion` streams only `content`, `tokens`, `stop` until completion | `/completion`: none (constrain with `grammar` or `json_schema`); chat-completions endpoint needs `--jinja`, supports `parallel_tool_calls`; `/v1/messages` supports `tools`, `tool_choice` | server `--reasoning-format`: `none`, `deepseek`, `deepseek-legacy`, `auto`; field `reasoning_content`; `--reasoning-budget`; request `reasoning_format`, `reasoning_effort`, `reasoning_control`, `chat_template_kwargs`; control `action: reasoning_end` | `grammar` (GBNF), `json_schema`; chat endpoint `response_format` `json_object` or `json_schema` | `tokens_evaluated`, `tokens_cached`, `timings{cache_n, prompt_n, prompt_ms, prompt_per_token_ms, prompt_per_second, predicted_n, predicted_ms, predicted_per_token_ms, predicted_per_second}` | `{"error": {code, message, type}}`; 400 `invalid_request_error`, 401 `authentication_error`, 501 `not_supported_error`, 503 `unavailable_error` ("Loading model") | https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md |
| raw text-completion endpoints | `POST /v1/completions` (hosted legacy, llama.cpp, vLLM, Ollama); llama.cpp `POST /completion`; Ollama `POST /api/generate` with `raw: true` | `model`, `prompt` (string, string list, token ids, token-id lists), `max_tokens`, `stop` (up to 4), `suffix`, `echo`, `best_of`, `n`, `logprobs` (int, max 5), `logit_bias`, `seed`, `presence_penalty`, `frequency_penalty`, `temperature`, `top_p`, `stream`, `stream_options` | `choices[]{text, index, logprobs{tokens, token_logprobs, top_logprobs, text_offset}, finish_reason}`; `finish_reason`: `stop`, `length`, `content_filter` | SSE data-only events, terminated by `data: [DONE]` | none (tools rendered into the prompt by the caller) | none (caller parses think tags or channels from text) | hosted: none; llama.cpp `grammar`, `json_schema`; Ollama `format` | `usage{prompt_tokens, completion_tokens, total_tokens}` | per host format | https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/completion_create_params.py |
| Open Responses (additional) | `/v1/responses` (HTTP and WebSocket); headers `Authorization`, `Content-Type: application/json` | items `message` (roles `user`, `system`, `developer`, `assistant`), `function_call{name, call_id, arguments}`, `function_call_output`, `reasoning`, `item_reference`, `compaction`; parts `input_text`, `output_text`, `text`, `summary_text`, `reasoning_text`, `refusal`, `input_image`, `input_file`, `input_video` (tool output only); `truncation`: `auto`, `disabled` | response `status` (typed string); item status `in_progress`, `completed`, `incomplete` | SSE, `event:` equals `type`; events as responses format but reasoning text is `response.reasoning.delta`/`.done`; one of `response.completed`, `response.failed`, `response.incomplete` is mandatory; `[DONE]` optional | as responses format (function tools only in the core) | `reasoning{content, summary, encrypted_content}` | not covered in the fetched pages | `input_tokens`, `output_tokens`, `total_tokens`, `input_tokens_details.cached_tokens`, `output_tokens_details.reasoning_tokens` | `{"error": {message, type, param, code}}`; types `invalid_request` 400, `not_found` 404, `too_many_requests` 429, `server_error` 500, `model_error` 500; codes `model_not_found`, `previous_response_not_found`, `websocket_connection_limit_reached` | https://www.openresponses.org/specification |
| interactions (additional) | `POST /v1beta/interactions` (overview) vs `/v1beta2/interactions` (migration guide); `x-goog-api-key` | `model`, `input`, `system_instruction`, `tools`, `generation_config{thinking_level, temperature}`, `previous_interaction_id`, `store`, `background`, `stream`, `response_format{mime_type, schema}` | `id`, `status` (`created`, `in_progress`, `requires_action`, `completed`), `steps[]` (`user_input`, `model_output`, `function_call`, `function_result`, thoughts) | SSE; `interaction.created`, `interaction.in_progress`, `step.start`, `step.delta`, `step.stop`, `interaction.completed` | `function_call` step, `function_result` input | thought steps; signature handling not verified | `response_format{mime_type, schema}` | `prompt_tokens`, `completion_tokens`, `total_tokens` (stream example only) | snake_case `code` table on the API errors page; scope unclear (details) | https://ai.google.dev/gemini-api/docs/interactions-overview |
| embeddings (additional) | `/v1/embeddings` (Ollama, llama.cpp, vLLM); Ollama `/api/embed`; llama.cpp `/embedding`, `/embeddings`; vLLM `/v2/embed` | `/v1/embeddings`: `model`, `input`, `encoding_format`, `dimensions`; `/api/embed`: `model`, `input`, `truncate`, `options`, `keep_alive`, `dimensions` | `/api/embed`: `model`, `embeddings`, `total_duration`, `load_duration`, `prompt_eval_count` | none | n/a | n/a | n/a | `prompt_eval_count` (Ollama native) | per host format | https://docs.ollama.com/api/openai-compatibility |
| rerank (additional) | llama.cpp `/reranking` (aliases `/rerank`, `/v1/rerank`, `/v1/reranking`); vLLM `/rerank`, `/v1/rerank`, `/v2/rerank` | `query`, `documents` (strings), `top_n`, `model` | not verified today | none | n/a | n/a | n/a | not verified | per host format | https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md |
| batch (additional) | `/v1/batches` (hosted chat-completions/responses family); `/v1/messages/batches`; vLLM `/v1/chat/completions/batch` | `/v1/batches`: `input_file_id`, `endpoint` (`/v1/responses`, `/v1/chat/completions`, `/v1/embeddings`, `/v1/completions`, `/v1/moderations`, `/v1/images/generations`, `/v1/images/edits`, `/v1/videos`), `completion_window: "24h"`, `metadata`, `output_expires_after`; JSONL input, up to 50,000 requests, 200 MB | not verified today | none (poll) | per embedded format | per embedded format | per embedded format | per embedded format | messages batches: 256 MB request limit | https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/batch_create_params.py |
| responses WebSocket mode (additional) | persistent socket on the `/v1/responses` resource | client event `response.create` with the HTTP body fields (`stream`, `background` not used) plus `stream_id` (1 to 256 chars) | as responses format | server events = responses streaming events; up to 16 in-flight responses, up to 32 named `stream_id` lanes, 60-minute connections | as responses format | as responses format | as responses format | as responses format | `previous_response_not_found`, `invalid_stream_id`, `websocket_stream_limit_reached`, `websocket_connection_limit_reached` | https://developers.openai.com/api/docs/guides/websocket-mode |
| realtime (additional) | vLLM `/v1/realtime` (speech-to-text); hosted realtime not fetched | not verified | not verified | WebSocket (not verified) | not verified | not verified | not verified | not verified | not verified | https://docs.vllm.ai/en/latest/serving/online_serving/ |

## Format conversion notes

What cannot be carried across formats; the codec layer reports these instead of dropping them (SPEC §4.1).

| From -> To | Lossy or unrepresentable features | Suggested handling (report / approximate / reject) |
|---|---|---|
| chat-completions -> responses | `n` > 1; `stop`; `seed`; `presence_penalty`, `frequency_penalty`, `logit_bias`; message `name`; `prediction`; audio output (`modalities`, `audio`); `prompt_cache_breakpoint` per part; invalid-JSON tool `arguments` | `n`: reject (or fan out N requests, reported); `stop`: approximate by client-side truncation, report; sampling extras: report; `name`: report; audio: reject; breakpoints: approximate with `prompt_cache_options`, report; invalid args: report |
| responses -> chat-completions | `reasoning` items (`encrypted_content`, summaries); built-in tool definitions and their call items; `previous_response_id`, `conversation`, `store`, `background`; `include`, `truncation`, `context_management`, `max_tool_calls`; `phase`; item `status`; `function_call_output` with image or file parts; `namespace`, `caller`; `incomplete_details.reason` `max_messages`, `steered` | reasoning: drop for hosted target, map text only to `reasoning`/`reasoning_content` on open servers, report; built-in tools: reject when requested, history items rendered as text and reported; state: proxy expands to full history or rejects `background`; tool-output media: approximate (follow-up user message), report; others: report |
| chat-completions -> messages | `system`/`developer` messages interleaved mid-conversation (only top-level `system` exists); `n`; `seed`, penalties, `logit_bias`, `logprobs`; custom (grammar) tools; `tool_choice` `allowed_tools`; `tool_choice` `required` or named on models that reject `any`/`tool`; `json_object` without schema; `image_url.detail`; `input_audio`; `file_id`; `refusal`; `annotations`; `max_completion_tokens` absent (target requires `max_tokens`); usage semantics (`prompt_tokens` includes cached) | hoist system text, report when interleaved; `n`: reject; sampling extras: report; custom tools: reject (or wrap as single-string `input_schema`, reported); `allowed_tools`: approximate by filtering `tools` (cache impact), report; forced choice: probe, else approximate with `auto` + strict tools, report; json_object: approximate with `{"type": "object"}` schema; audio: reject; `file_id`: reject unless resolvable; default `max_tokens` from profile; recompute usage |
| messages -> chat-completions | `thinking.signature`, `redacted_thinking`; server tools and `server_tool_use`/`*_tool_result` blocks; `citations`, `search_result` and URL `document` blocks; `cache_control` TTLs (`5m`, `1h`) vs `ttl: "30m"`; `top_k` (hosted); `tool_result` with images or documents; `is_error`; `stop_reason` `pause_turn`, `refusal`, `model_context_window_exceeded`; `container`, `context_management`, `metadata.user_id`; `eager_input_streaming`, `defer_loading`, `input_examples`, `allowed_callers` | signed reasoning: drop, report (text only to open servers); server tools: reject, history as text, report; citations: approximate as `url_citation` where a URL exists, else report; cache: approximate (`prompt_cache_breakpoint` on hosted, nothing on engines), report TTL change; `top_k`: pass to engines that accept it, else report; tool-result media: approximate (user message), report; `is_error`: approximate (text prefix), report; stop reasons: map `refusal` to `content_filter`, others report |
| chat-completions -> generate-content | interleaved `system`/`developer`; `tool` message string content (target `response` is an object); custom tools; `allowed_tools` with mode `auto` (`allowedFunctionNames` only with `ANY`/`VALIDATED`); `parallel_tool_calls: false`; `logit_bias`; `refusal`, `annotations`; `image_url` with http URL; `name`; `reasoning_effort` `none` on models that cannot disable thinking, `xhigh`, `max` | hoist to `systemInstruction`, report when interleaved; wrap string as `{"result": ...}` or parse JSON (approximate); custom tools: reject; allowed tools: approximate with `ANY` or `VALIDATED`, report; parallel flag: report; `logit_bias`: report; remote images: approximate by inlining base64, report; effort: approximate to nearest `thinkingLevel`, reject `none` where unsupported |
| generate-content -> chat-completions | `thoughtSignature`; `thought` parts; `safetyRatings`, `promptFeedback`, `citationMetadata`, `groundingMetadata`, `urlContextMetadata`; built-in tools and their parts; `functionResponse.parts`; `cachedContent`; `continuationToken`/`CONTINUATION`; finish reasons other than `STOP`, `MAX_TOKENS`, safety-family; `toolUsePromptTokenCount`; `functionCall` without `id` | signatures: drop, report (keep in session store for same-endpoint replay); thoughts: open-server reasoning field or drop, report; safety/grounding: report (citations to `url_citation` where possible); built-ins: reject, report; `cachedContent`: reject; continuation: report; finish reasons: `STOP` -> `stop` or `tool_calls`, `MAX_TOKENS` -> `length`, safety family -> `content_filter`, others report; ids: synthesize |
| responses -> messages | `encrypted_content` and reasoning summaries; built-in tools (names and semantics differ from server tools); stateful fields; custom tools; `allowed_tools`; `phase`; `max_tool_calls`, `truncation`, `context_management` (both formats have one, semantics differ); `effort` `none`, `minimal` | reasoning: drop, report; built-ins: reject, report; state: expand or reject; custom tools: reject; allowed tools: approximate by filtering; `phase`: drop, report; effort: approximate (`none` -> `thinking.type` `disabled` or `between_tools` where accepted) |
| messages -> responses | signed thinking and `redacted_thinking`; server tools; `cache_control`; `citations`, `search_result`; `top_k`, `stop_sequences` (target has no stop or top-k); `is_error`; `pause_turn`, `model_context_window_exceeded`; `container`, `mcp_servers` | signed reasoning: drop, report; server tools: reject, report; cache: approximate with `prompt_cache_key`/`prompt_cache_options`; citations: approximate as annotations, report; `top_k`, stop: report (stop approximated client-side); `is_error`: approximate (text), report; stop reasons: report |
| responses -> generate-content | `encrypted_content`; built-in tools; stateful fields; custom tools; `allowed_tools`; `parallel_tool_calls: false`; `function_call_output` parts (target supports `functionResponse.parts` only on newer models); `include` logprobs | drop/report; reject built-ins and custom tools; expand state or reject; allowed tools: approximate (`ANY`/`VALIDATED`); parallel flag: report; tool-output parts: approximate, report; logprobs: report (request flags unverified) |
| generate-content -> responses | `thoughtSignature`; `thought` parts; `candidateCount` > 1; `stopSequences`, `topK`, seed and penalties; safety and grounding metadata; `cachedContent`; `continuationToken` | signature: drop, report; thoughts: approximate as `reasoning` item `summary` without `encrypted_content`, report; `candidateCount`: reject or fan out; stop: approximate client-side; `topK`, seed, penalties: report; metadata: report; `cachedContent`: reject; continuation: report |
| messages -> generate-content | `thinking.signature`, `redacted_thinking` (not interchangeable with `thoughtSignature`); server tools; per-block `cache_control`; `tool_result` string and `is_error` (target needs an object `response`); `disable_parallel_tool_use`; URL `document` sources; `citations`; effort `xhigh`, `max` | signatures: drop, report; server tools: reject, report; cache: report (target caching is a separate `cachedContent` resource); wrap result as `{"result": ...}` or `{"error": ...}` (approximate); parallel flag: report; URL docs: approximate by inlining, report; citations: report; effort: approximate to `high` |
| generate-content -> messages | `thoughtSignature`; `thought` parts (target thinking blocks need target signatures); `candidateCount` > 1; seed, penalties, logprobs; safety and grounding metadata; built-in tools and their parts; `functionResponse.response` object (target `content` is string or blocks); `functionCall` without `id`; `cachedContent`; finish reasons | signatures and thoughts: drop, report; `candidateCount`: reject or fan out; extras: report; built-ins: reject, report; serialize `response` as JSON text (approximate); synthesize `tool_use` ids; `cachedContent`: reject; finish: `STOP` -> `end_turn` or `tool_use`, `MAX_TOKENS` -> `max_tokens`, safety family -> `refusal`, others report |
| any -> Ollama native API | tool-call ids (absent from the documented schema and from older servers; current servers send them, see "Verified during Phase 1"); `arguments` string vs object; `tool_choice`; image URLs (base64 only); documents; signed reasoning; `n`; logit bias; per-request cache markers | keep the server's ids, synthesize missing ones, and send `tool_call_id` next to `tool_name` on results; parse/serialize arguments, report invalid JSON; `tool_choice`: approximate with `format` (JSON Schema) for forced calls, else report; fetch and inline images; documents: reject; reasoning: report; `n`: reject |
| any -> llama.cpp `/completion` or raw text-completion | everything structured (roles, tools, images, reasoning fields) | render with the model's chat template (`/apply-template` on llama.cpp, or local Jinja); tools go into the fallback text protocol; constrain with `grammar`/`json_schema`; parse think tags and tool syntax from text (approximate, report parser used) |
| responses <-> Open Responses | hosted-only items, tools and events (built-ins, compaction details, `phase`, moderation, prompt-cache options); reasoning event name (`response.reasoning_text.delta` vs `response.reasoning.delta`); stateful features absent in stateless implementations | pass unknown items through as `slug:type` extensions or report; translate the reasoning event name both ways; state: expand or reject |
| usage, all pairs | cached-token semantics (`prompt_tokens` and `input_tokens` in chat-completions/responses include cached tokens; messages-format `input_tokens` excludes cache reads and writes); reasoning tokens separate (`reasoning_tokens`, `thoughtsTokenCount`) vs folded (`output_tokens` inclusive); engine metrics in time units (Ollama ns, llama.cpp ms) | normalize to internal `{input_total, input_cached_read, input_cache_write, output_total, output_reasoning}` plus raw provider usage; mark derived values |

## Engines

Engine capabilities relevant to the model plane (tasks A01-A03).

| Engine | Version (date) | License | Windows | macOS | Linux | API formats served | Server-side tool parsing | Reasoning separation | Constrained decoding | Prefix cache + metrics | KV slot save/restore | Speculative decoding | KV-cache quant | MoE CPU offload | Ampere sm_86 notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| llama.cpp server | v0.7.0 = b11669 (2026-10-15) | MIT | native, prebuilt (x64: CPU, CUDA 12.4/13.4, Vulkan, SYCL, ROCm, OpenVINO; arm64) | native, prebuilt (arm64 Metal; x64 CPU) | native, prebuilt (x64/arm64; CUDA 12.8/13.4, Vulkan, ROCm, SYCL) | chat-completions, completions, responses (converted to chat internally), messages, embeddings, rerank; native `/completion`, `/infill`, `/tokenize`, `/apply-template`, `/slots`; router `/models`; no generate-content | yes, Jinja on by default: ~20 dedicated parsers (gpt-oss harmony, Qwen3-Coder XML, Kimi K2/K3, DeepSeek V3.2/V4, Gemma 4, MiniMax-M3, Ministral 3, ...) + template autoparser | `reasoning_content`; `--reasoning-format` none / deepseek / deepseek-legacy (default auto); `--reasoning-budget`; `--reasoning-effort`; `reasoning_end` control | GBNF `grammar`, `json_schema`, `response_format` json_object / json_schema | `cache_prompt` on, `--cache-reuse`, `--cache-ram` 8192 MiB; hits only per response (`timings.cache_n`, `cached_tokens`); `/metrics` has throughput counters, no cache counter | yes: `POST /slots/{id}?action=save` / `restore` / `erase` + `--slot-save-path` | yes: `--spec-type` draft-simple/eagle3/mtp/dflash/dspark + n-gram modes; `-md`, `--spec-draft-n-max`/`-n-min` | `-ctk`/`-ctv`: q8_0, q4_0, q4_1, iq4_nl, q5_0, q5_1, bf16, f16 | `--cpu-moe`, `--n-cpu-moe N`, `-ot`, `--moe-cache-mib N` | release CUDA builds include `86-real`; MXFP4 via int8 MMQ path; FP4 MMA only on Blackwell |
| KTransformers | 0.7.2 (2026-10-15) | Apache-2.0 | no native wheel; WSL2 undocumented (verify) | no | x86-64 only (manylinux_2_35 wheels, CPython 3.11/3.12) | chat-completions via `sglang-kt` (`python -m sglang.launch_server`, `/v1`); other formats per fork (verify) | via SGLang `--tool-call-parser` | via SGLang `--reasoning-parser` | inherits SGLang fork (verify) | inherits SGLang fork (verify) | not documented | not documented (verify) | not documented (verify) | core feature: `--kt-method` (AMXINT4/AMXINT8/LLAMAFILE/FP8/BF16/RAWINT4), `--kt-num-gpu-experts`, `--kt-cpuinfer` | wheel built for SM 80/86/89/90; native precision needs >= 24 GB VRAM GPUs |
| AirLLM | 4.0.0 (2026-09-05) | Apache-2.0 | not documented (pure-Python wheel; verify) | ARM only, via mlx | yes (CUDA) | none (Python API); needs a chat-completions shim | none | none | none | none; `profiling_mode` timings only | no | no | no (weight-only `compression` 4bit/8bit) | layer streaming from disk; per-expert streaming for Kimi K3 | README measurement on RTX 3090 (sm_86); Kimi K3 path needs CUDA 12 torch + `flash-attn` |
| bitnet.cpp | no tagged release; main 2026-07-27 | MIT | native, build from source (VS2022 + clang) | build from source (ARM) | build from source | `run_inference_server.py` starts vendored fork `llama-server` (chat-completions + native endpoints of that revision; verify) | inherits fork (verify) | n/a for listed models (verify) | inherits fork GBNF (verify) | inherits fork (verify) | inherits fork (verify) | no | inherits fork (verify) | n/a (dense ternary models) | CPU kernels I2_S/TL1/TL2; separate GPU kernel in `gpu/` (arch support not checked) |
| vLLM | 0.31.0 (2026-10-05) | Apache-2.0 | no native; WSL2/Docker (docs suggest WSL) | no CUDA path; ARM via separate vLLM-Metal plugin (macOS 14+) | yes: x86_64, aarch64 wheels; CUDA 13.0 wheel (R580+ driver for CUDA 13 images), CUDA 12.9 image | chat-completions, completions, responses, messages (+ count_tokens), embeddings, rerank, score, pooling, tokenize | yes: `--enable-auto-tool-choice --tool-call-parser` (50 names), `--tool-parser-plugin`, `--tool-strict-level`; required and named via structured outputs | yes: `--reasoning-parser` (35 names); field `reasoning` (renamed from `reasoning_content`) | backends auto, xgrammar, guidance, outlines, lm-format-enforcer; `structured_outputs` (json, regex, choice, grammar, structural_tag) or `response_format` json_schema; `guided_*` removed in 0.12.0 | on by default when the model supports it (`--no-enable-prefix-caching`); `vllm:prefix_cache_hits`, `vllm:prefix_cache_queries`, `vllm:prompt_tokens`, `vllm:generation_tokens` (rate() for throughput), `vllm:kv_cache_usage_perc` | no per-slot API; CPU KV offload `--kv-offloading-size`, backend native or lmcache; `--kv-transfer-config` connectors | `--speculative-config` method ngram, ngram_gpu, suffix, draft_model, medusa, mlp_speculator, eagle, eagle3, mtp, dflash, dspark | `--kv-cache-dtype` fp8, fp8_e4m3, fp8_e5m2, int8/int4/fp8 per-token-head, turboquant_*, nvfp4 | generic weight offload only (`--cpu-offload-gb`, `--cpu-offload-params`, `--offload-*`); no MoE-specific flag found | FP8 KV only via TRITON_ATTN or FLASHINFER (FlashAttention backend: sm90/sm100 only); gpt-oss MXFP4 min capability 80, A100 recipe uses TRITON_ATTN + Marlin MoE, sm_86 not documented, `--moe-backend marlin` pins Marlin; `--tensor-parallel-size 4` |
| SGLang | 0.5.22 (2026-10-16) | Apache-2.0 | no native; WSL2/Docker not documented | ARM via MLX runtime (`SGLANG_USE_MLX=1`, source install, macOS 14+) | yes: x86_64, aarch64 wheels (manylinux_2_34); CUDA 13 required since 0.5.20 | chat-completions, completions, responses, messages (+ count_tokens), embeddings, rerank, score; native `/generate` | yes: `--tool-call-parser auto` or 42 names; required and named via EBNF (xgrammar); `/parse_function_call` | yes: `--reasoning-parser auto` or 34 names; field `reasoning_content`; `separate_reasoning`, `/separate_reasoning` | `--grammar-backend` xgrammar (default), outlines, llguidance, none; `response_format` json_schema, `regex`, `ebnf` | RadixAttention on by default (`--disable-radix-cache`); metrics need `--enable-metrics` (default off): `sglang:cache_hit_rate`, `sglang:prompt_tokens_total`, `sglang:generation_tokens_total`, `sglang:gen_throughput`; `--enable-cache-report` | no per-slot API; HiCache host tier (`--enable-hierarchical-cache`) and storage backends file, mooncake, hf3fs, nixl, aibrix, dynamic, eic, simm | `--speculative-algorithm` EAGLE, EAGLE3, NEXTN, STANDALONE, NGRAM; `--speculative-draft-model-path` | `--kv-cache-dtype` fp8_e4m3, fp8_e5m2, bf16, nvfp4, fp4_mx_block16 (FP4 experimental) | KTransformers flags `--kt-method` (default AMXINT4), `--kt-weight-path`, `--kt-cpuinfer`, `--kt-num-gpu-experts`, `--kt-max-deferred-experts-per-token`; `--cpu-offload-gb` | Ampere default attention FlashInfer, fallback Triton, both support FP8 KV; native NVFP4 KV targets SM100; gpt-oss cookbook covers H100, H200, B200 only (sm_86 not documented); `--tp-size 4`; memory saver `--enable-memory-saver` |
| LMCache | 0.5.6 (2026-10-15) | Apache-2.0 | no | no | yes: x86_64, ARM64 wheels; CUDA 13.0 default, CUDA 12.9 wheel on a separate index | none (engine extension) | n/a | n/a | n/a | extends the engine prefix cache to CPU, disk and remote tiers; non-prefix reuse (CacheBlend); shows in vLLM as `vllm:external_prefix_cache_hits` and `vllm:external_prefix_cache_queries` | persistent tiered KV (CPU RAM, local disk, Redis/Valkey, Mooncake, InfiniStore, S3-compatible, NIXL, GDS); no per-slot API | n/a | KV compression (CacheGen) documented, not evaluated (verified: no) | n/a | docs prerequisite compute capability 7.0+ and CUDA 12.1+, so sm_86 is in range; no Ampere-specific notes found |
| Ollama | 0.40.5 (2026-10-14) | MIT | native | native | native | Ollama native (`/api/*`, NDJSON); chat-completions, completions, embeddings, models (`/v1`, SSE); responses (stateless, since 0.13.3); messages (since 0.14.0) | yes (`tools`; parallel calls; arguments as object; `tool_choice` unsupported) | yes: `message.thinking` (native), `message.reasoning` (chat-completions), thinking blocks (messages), reasoning summaries (responses); `think` bool or level | `format`: `"json"` or JSON Schema; `response_format` on `/v1` | automatic; per response `prompt_eval_cached_count` / `usage.prompt_tokens_details.cached_tokens`; no metrics endpoint in OpenAPI spec | no | yes: separate draft models or embedded MTP (`draft_num_predict`, `/api/create` `draft_files`) | `OLLAMA_KV_CACHE_TYPE` f16 / q8_0 / q4_0 (global, needs flash attention) | automatic CPU/GPU layer split (`ollama ps` PROCESSOR); no documented expert-only control | CC 8.6 listed incl. RTX A6000; driver 550+ (551.61+ on Windows) |
| MLX-LM (mlx_lm.server) | 0.32.2 (2026-10-16) | MIT | no | native (ARM) | declared (extras cuda12 / cuda13 / cpu); untested | chat-completions, completions, `/v1/models`, `/health` (SSE) | yes (tokenizer tool parser) | yes: `reasoning` field | no (`response_format` not implemented) | LRU prompt cache (`--prompt-cache-size`, `--prompt-cache-bytes`); `cached_tokens` in usage; no metrics endpoint | server no; CLI `mlx_lm.cache_prompt` writes a .safetensors prefix file | yes: `--draft-model`, `--num-draft-tokens` (disables batching) | `--kv-bits`, `--kv-group-size`, `--quantized-kv-start` (disables batching) | not documented | n/a on macOS; Linux CUDA not verified |
| transformers (transformers serve) | 5.20.0 (2026-10-15) | Apache-2.0 | native | native | native | chat-completions, completions, responses, `/v1/audio/transcriptions`, `/v1/models`, `/load_model` (SSE) | yes, for tokenizers declaring tool-call tokens | yes: `reasoning_content` (chat-completions), `reasoning` item (responses); needs response template | not documented for serve | with `--continuous-batching`: shared-prefix KV block reuse (default on); no metrics endpoint documented | no | not documented for serve | not documented for serve | not documented for serve | PyTorch CUDA build; no specific notes found |
| ExLlamaV3 (via TabbyAPI) | 1.6.2 (2026-10-14); TabbyAPI rolling | MIT; TabbyAPI AGPL-3.0 | native (win_amd64 wheels + triton-windows) | no | native (CUDA; ROCm) | chat-completions, completions, embeddings, models, model load/unload API, Kobold-style API | yes | yes: `reasoning_content` (reasoning parser, default on) | JSON Schema, regex, EBNF | paged attention + continuous batching; prefix reuse not documented in fetched pages; no metrics route | no | yes: draft models | 2 to 8 bit (`cache_mode`, default FP16) | yes (CPU offload for MoE, AVX2/AVX-512) | wheels built for arch 8.6; paged batching needs Ampere or newer |
| llama-swap (proxy) | v265 (2026-10-14) | MIT | native (windows_amd64.zip; WinGet unofficial) | native (amd64/arm64; Homebrew, MacPorts) | native (amd64/arm64; container images) | proxies chat-completions, completions, responses, embeddings, models, audio, images, messages (+ count_tokens), llama-server `/completion`, `/infill`, `/rerank`, `/props` | n/a (upstream) | n/a (`sendLoadingState` injects loading text into the reasoning field) | n/a | `/metrics` (system and GPU, Prometheus); no cache stats | n/a | n/a (upstream flags) | n/a | n/a | n/a |
| TensorRT-LLM | 1.2.1 (2026-04-20); 1.4.0rc0 (2026-10-13) | Apache-2.0 | no (deprecated since 0.18.0) | no | native (pip on Ubuntu 24.04; containers) | chat-completions, completions, responses; main-branch source adds messages (+ count_tokens, batches), embeddings | yes (`--tool_parser`) | yes (`--reasoning_parser`) | guided decoding (`guided_decoding_backend`, xgrammar) | KV block reuse; `/metrics` (beta), `/kv_cache_events` | no | yes (one-model EAGLE3 / MTP paths) | `--kv_cache_dtype` | not documented in fetched pages (expert parallel `--ep_size`) | supported hardware lists A100 only for Ampere; sm_86 not listed |

## Tool-call formats

Tool-call syntaxes used by open-weight model families (tasks A06-A07).

| Format | Used by | Syntax sketch | Parsers (llama.cpp / vLLM / SGLang / Ollama) | Notes |
|---|---|---|---|---|
| harmony | gpt-oss, gpt-oss-safeguard | `<\|start\|>assistant<\|channel\|>commentary to=functions.NAME <\|constrain\|>json<\|message\|>{"k":"v"}<\|call\|>`; result `<\|start\|>functions.NAME to=assistant<\|channel\|>commentary<\|message\|>{...}<\|end\|>` | `gpt-oss.cpp` / `--tool-call-parser openai` (+ reasoning `openai_gptoss`) / `gpt-oss` (+ reasoning `gpt-oss`) / built-in Go parser `harmony/harmonyparser.go` | recipient may sit in the role or the channel header; `<\|constrain\|>` may be missing in re-rendered history (llama.cpp treats it as optional); built-in tools use `browser.*` / `python` recipients on `analysis` |
| harmony dialect: recipient + XML invoke | Muse Glimmer, Muse Spark Open | `<\|start\|>assistant to=NAME<\|message\|><atem:function_calls><atem:invoke name="NAME"><atem:parameter name="k">v</atem:parameter></atem:invoke></atem:function_calls>`; result `<\|start\|>tool NAME<\|message\|><tool_output name="NAME">...</tool_output><\|eot\|>` | `muse-glimmer.cpp`, `muse-spark.cpp` / `muse_glimmer` / `muse` / `glimmer` | reasoning `to=self`, reply `to=user`; no channels; not parseable by harmony parsers |
| harmony dialect (LLM-jp) | LLM-jp-4.1 | ? | `llm-jp-harmony.cpp` / ? / ? / ? | listed by llama.cpp docs as "Harmony dialect"; not researched |
| Gemma 4 native | Gemma 4 | `<\|tool_call>call:NAME{k:<\|"\|>text<\|"\|>,n:1}<tool_call\|>`; result `<\|tool_response>response:NAME{...}<tool_response\|>` | `gemma4.cpp` / `gemma4` / `gemma4` / `gemma4` | not JSON; keys unquoted, strings wrapped in `<\|"\|>`; declarations in `<\|tool>declaration:NAME{...}<tool\|>` |
| FunctionGemma | `google/functiongemma-270m-it` | `<start_function_call>call:NAME{k:<escape>v<escape>}<end_function_call>` | ? / `functiongemma` / none / `functiongemma` | needs a developer-role system instruction; meant for fine-tuning |
| Mistral `[TOOL_CALLS]` | Mistral Small 4, Medium 3.5, Large 3, Devstral 2 / Small 2, Ministral 3 | `[TOOL_CALLS]NAME[ARGS]{"k":"v"}` (repeated per call); result `[TOOL_RESULTS]...[/TOOL_RESULTS]` | `ministral3.cpp` / `mistral` / `mistral` / `ministral` | tools in `[AVAILABLE_TOOLS]...[/AVAILABLE_TOOLS]` before the first user turn; no id/name in rendered results |
| Llama 3.1 JSON | Llama 3.1 / 3.3, 3.2 | `{"name": "f", "parameters": {"k": "v"}}` (alt. `<function=f>{...}</function>`) | native per docs / `llama3_json` / `llama3` / ? | no parallel calls in vLLM; tool output role `ipython` |
| Pythonic list | Llama 3.2 / 3.3 / 4 | `[f(a=1), g(b="x")]` | ? / `pythonic`, `llama4_pythonic` / `pythonic` / ? | model must not mix text and calls (vLLM) |
| LFM2 tokens + pythonic | LFM2, LFM2.5 | `<\|tool_call_start\|>[f(a=1)]<\|tool_call_end\|>` | `lfm2.cpp` / `lfm2` / `lfm2` / `lfm2` | JSON output can be requested in the system prompt |
| OLMo 3 `function_calls` | Olmo 3 / 3.1 | `<function_calls>` + one pythonic call per line + `</function_calls>` | ? / `olmo3` / none / `olmo3` | accepts JSON and Python literals |
| JSON in `<tool_call>` tags (Hermes style) | Granite 4.1, SmolLM3 (`xml_tools`), Falcon H1R, Trinity Mini | `<tool_call>{"name": "f", "arguments": {"k": "v"}}</tool_call>` | Hermes 2/3 native per docs / `hermes` (Granite 4.x: `granite4`) / `hermes` / ? | Granite 4.0 not re-verified |
| Granite 3.x special-token list | Granite 3.0-3.3 | `<\|tool_call\|>[{"name": "f", "arguments": {"k": "v"}}]` (Granite 3.1 also writes the marker as plain `<tool_call>`) | not parsed by b11669 (live, 2026-10-16) / `granite` (marker matched by token id) / ? / ? | the marker is a special token and there is no closing marker; Granite-3.3-2B writes `arguments` before `name`; catalog syntax `granite` (ADR 0022) |
| Qwen3-Coder-style XML | Granite 4.2, Nemotron 3 / 3.5, Trinity Large Thinking | `<tool_call><function=f><parameter=k>v</parameter></function></tool_call>` | `qwen3-coder.cpp` / `qwen3_coder` / `qwen3_coder` / ? (Ollama has `qwen3coder`; per-model use ?) | cross-family reuse; Nemotron and Trinity syntax inferred from parser names |
| Command (melody) | Command A Reasoning, Command A+, North-Mini-Code | ? (not on cards) | `cohere2moe.cpp` / `cohere_command3`, `cohere_command4` / `cohere_command4` / built-in (publisher-named) | vLLM needs the `cohere_melody` package |
| Apertus | Apertus v1 / v1.5 | `[{"f": {"k": "v"}}]` between tool prefix and suffix tokens | ? / `apertus` / `apertus2509` / ? | token names ?; no tool calls in thinking mode |
| Phi-4-mini | Phi-4-mini-instruct | declarations `<\|tool\|>[{...}]<\|/tool\|>` in the system turn; output ? | ? / `phi4_mini_json` / none / ? | output syntax not on card |
| Laguna | Laguna S / XS 2.1 | ? (not on card) | fork / `poolside_v1` / `poolside_v1` / `laguna` | |
| Switchyard fallback JSON action | any text-generating model | `{"tool": "NAME", "arguments": {...}}` under grammar or JSON Schema | grammars (not re-verified today) / structured outputs (`tool_choice="required"` uses them) / ? / ? | for reasoning models the constraint must start after the reasoning block |
| F1 JSON in `<tool_call>` tags | Qwen2.5, Qwen3, Qwen3-2507, Qwen3-Next, ERNIE-4.5-Thinking, Intern-S1-Pro, Ling 2.x / Ring <=2.5 | `<tool_call>\n{"name": "f", "arguments": {"k": "v"}}\n</tool_call>` | template autoparser / `hermes` (`qwen3_json` alias on main only), `ernie45` / `qwen25`, `qwen`, `hermes` / `qwen3`, `qwen3-thinking` | Arguments are JSON, so JSON-Schema constrained decoding applies directly |
| F2 JSON with family tags | LongCat-Flash-Chat; Hunyuan-A13B; Intern-S1 | `<longcat_tool_call>{json}</longcat_tool_call>`; `<tool_calls>[{"name":…,"arguments":…}]</tool_calls>`; `<\|action_start\|><\|plugin\|>\n{"name":…,"parameters":…}<\|action_end\|>` | autoparser / `longcat`, `hunyuan_a13b`, `internlm` / `hunyuan`, `interns1` / none | Intern-S1 uses `parameters`, not `arguments` |
| F3 XML function/parameter, newline layout | Qwen3-Coder, Qwen3-Coder-Next, Qwen3.5, 3.6, 3.8, Intern-S2, Step-3.5/3.7/5 | `<tool_call>\n<function=f>\n<parameter=k>\nv\n</parameter>\n</function>\n</tool_call>` | dedicated Qwen3-Coder handler / `qwen3_coder` (= `qwen3_xml`), `step3p5`, `step5` (image only) / `qwen3_coder`, `step3p5` / `qwen3-coder`, `qwen3.5` | Values are raw text; non-string values are JSON literals; parser needs the tool schema to coerce types |
| F4 XML function/parameter, compact | MiMo-V2.x | `<tool_call><function=f><parameter=k>v</parameter></function></tool_call>` | autoparser (excluded from the F3 handler) / `mimo` / `mimo` / none | Same tags as F3, no newlines |
| F5 XML function/parameter in `seed:` wrapper | Seed-OSS | `<seed:tool_call>\n<function=f>\n<parameter=k>v</parameter>\n</function>\n</seed:tool_call>` | autoparser / `seed_oss` / none / none | |
| F6 `arg_key` / `arg_value` pairs | GLM-4.5/4.6 (newline), GLM-4.7/5.x (compact), Atria-Dawn-Preview, Ling 3.x, Ring-2.6, LongCat 2601/Lite/2.0 (`longcat_` prefixed tags), Hy3/Hy4 (`:opensource` suffixed tags inside `<tool_calls:opensource>`; Hy3 adds `<tool_sep:opensource>`) | `<tool_call>f<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>` | autoparser, dedicated Ling 3 handler / `glm45`, `glm47`, `ling3`, `hy_v3`, `hy_v4` / `glm45`, `glm47`, `ling3`, `hunyuan` / `glm-4.7` | Five tag dialects; separator layout differs by version |
| F7 `invoke` + `parameter name=` XML | MiniMax-M2, M2.1, M2.5, M2.7 | `<minimax:tool_call>\n<invoke name="f">\n<parameter name="k">v</parameter>\n</invoke>\n</minimax:tool_call>` | autoparser / `minimax_m2` / `minimax-m2` / none | |
| F8 namespaced nested XML | MiniMax-M3 | `]<]minimax[>[<tool_call>\n]<]minimax[>[<invoke name="f">]<]minimax[>[<k>v]<]minimax[>[</k>]<]minimax[>[</invoke>\n]<]minimax[>[</tool_call>` | dedicated M3 handler / `minimax_m3` / `minimax-m3` / none | Objects and arrays expand into nested elements, not JSON |
| F9 DSML | DeepSeek V3.2 (`function_calls`), V4 (`tool_calls`), V4.1 (` calls`, leading-space tag names) | `<｜DSML｜tool_calls>\n<｜DSML｜invoke name="f">\n<｜DSML｜parameter name="k" string="true">v</｜DSML｜parameter>\n</｜DSML｜invoke>\n</｜DSML｜tool_calls>` | dedicated DSML handler (V3.2/V4) / `deepseek_v32`, `deepseek_v4`, `deepseek_v41` with matching `--tokenizer-mode` / `deepseekv32`, `deepseekv4`, `deepseekv41` / none | `string="false"` values are JSON literals; three block-name variants |
| F10 special-token sections | DeepSeek V3, R1 (fenced JSON), V3.1 (raw JSON); Kimi K2.x | `<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>f<｜tool▁sep｜>{json}<｜tool▁call▁end｜><｜tool▁calls▁end｜>`; `<\|tool_calls_section_begin\|><\|tool_call_begin\|>functions.f:0<\|tool_call_argument_begin\|>{json}<\|tool_call_end\|><\|tool_calls_section_end\|>` | autoparser (DeepSeek), dedicated (Kimi K2) / `deepseek_v3`, `deepseek_v31`, `kimi_k2` / `deepseekv3`, `deepseekv31`, `kimi_k2` / `deepseek3` | V3/R1 prefix `function<｜tool▁sep｜>f` + fenced JSON; Kimi K2 needs `functions.NAME:IDX` IDs |
| F11 XTML control-token markup | Kimi K3 | `<\|open\|>tools<\|sep\|><\|open\|>call tool="f" index="1"<\|sep\|><\|open\|>argument key="k" type="string"<\|sep\|>v<\|close\|>argument<\|sep\|><\|close\|>call<\|sep\|><\|close\|>tools<\|sep\|>` | dedicated K3 handler / `kimi_k3` / `kimi_k3` / none | Typed arguments; optional `<\|open\|>json type="object"<\|sep\|>` block |
| F12 `function name=` / `param name=` XML | MiniCPM5 | `<function name="f"><param name="k">v</param></function>` | dedicated MiniCPM5 handler / `minicpm5` / `minicpm5` / none | |

## Engine-side tool parsers

| Tool-call format | vLLM parser name | SGLang parser name | llama.cpp support | Ollama support | Notes |
|---|---|---|---|---|---|
| Harmony channels (gpt-oss) | `--tool-call-parser openai` (reasoning `openai_gptoss`) | `gpt-oss` (tool and reasoning) | dedicated handler (also the LLM-jp-4.1 dialect) | built-in `harmony` | vLLM chat-completions supports only `tool_choice="auto"`; vLLM responses: no streaming with function calls, usage reported as zeros; SGLang drops analysis-channel text, content may be empty |
| JSON in `<tool_call>` tags (Hermes 2/3, Qwen2.5, QwQ-32B; SGLang `qwen` covers the Qwen series except Qwen3-Coder) | `hermes` | `hermes`, `qwen`, `qwen25` | auto-parser JSON_NATIVE (Hermes 2 Pro tested) | built-in `qwen3`, `qwen3-thinking`, `qwen3.5`; others template-derived | vLLM docs name Qwen2.5 and QwQ-32B for `hermes`; Qwen3-4B-Instruct-2507 (T0) maps to SGLang `qwen` by the docs wording |
| XML `<function=…><parameter=…>` tags (Qwen3-Coder, Step-3.5-Flash, Nanbeige) | `qwen3_coder`, `qwen3_xml`, `step3p5` | `qwen3_coder` (also behind `step3p5`, `nanbeige`) | dedicated `qwen3_coder` init; TAG_WITH_TAGGED (Qwen3-Coder and Step-3.5-Flash tested) | built-in `qwen3-coder` | vLLM docs map Qwen3-Coder to `qwen3_xml`; structured outputs can switch off if reasoning is not parsed (`enable_in_reasoning`) |
| Llama 3.x JSON | `llama3_json` | `llama3` | auto-parser JSON_NATIVE (Llama 3.1/3.2/3.3 tested) | template-derived | vLLM docs: no parallel calls; arrays may arrive serialized as strings |
| Pythonic call list (Llama 3.2 1B/3B, Llama 4, ToolACE-8B) | `pythonic`, `llama4_pythonic`, `llama4_json` | `pythonic` | not listed | template-derived | vLLM: model must not mix text and calls in one generation; SGLang docs: pythonic "still under development on Blackwell" |
| `[TOOL_CALLS]` JSON (Mistral, Mistral Small 3.2, Devstral, Ministral 3) | `mistral` | `mistral` | auto-parser JSON_NATIVE (Mistral Nemo, Mistral Small 3.2, Devstral tested); dedicated Ministral 3 handler | built-in `ministral` | vLLM: 7B weak at parallel calls; 9-digit tool-call IDs with the transformers tokenizer backend; Devstral Small 2 (T1) not re-tested |
| DeepSeek V3, V3.1, V3.2, V4, V4.1 | `deepseek_v3`, `deepseek_v31`, `deepseek_v32`, `deepseek_v4`, `deepseek_v41` | `deepseekv3`, `deepseekv31`, `deepseekv32`, `deepseekv4`, `deepseekv41` | DeepSeek V3.1 JSON_NATIVE tested; dedicated `deepseek_v3_2` init | built-in `deepseek3` | SGLang docs recommend specific chat templates for v3 and v31; DeepSeek V4.1 Flash (T3) maps by name only (not verified) |
| GLM `<arg_key>`/`<arg_value>` tags (GLM-4.5, 4.6, 4.7) | `glm45`, `glm47` | `glm`, `glm45`, `glm47` | TAG_WITH_TAGGED (GLM-4.6, GLM-4.7-Flash tested) | built-in `glm-4.7` | GLM-5.x parser mapping not verified |
| Kimi K2 / K3 | `kimi_k2`, `kimi_k3`, `k2_horizon` | `kimi_k2`, `kimi_k3`, `k2_horizon` | dedicated `kimi_k2`, `kimi_k3`, `k2_horizon` inits; Kimi-K2 JSON_NATIVE tested | no built-in parser | Kimi K3 (T3) has named parsers in vLLM, SGLang and llama.cpp |
| MiniMax M2 / M3 (XML invoke, JSON args) | `minimax_m2`, `minimax_m3` | `minimax-m2`, `minimax-m3` | MiniMax-M2 TAG_WITH_JSON tested; MiniMax M3 has its own PEG format | no built-in parser | underscore (vLLM) vs hyphen (SGLang) names |
| Gemma 4 / FunctionGemma | `gemma4`, `functiongemma` | `gemma4` | dedicated `gemma4` init (PEG_GEMMA4) | built-in `gemma4`, `gemma4-no-thinking`, `functiongemma` | Gemma 4 E2B/E4B are T0 candidates |
| Phi-4-mini JSON | `phi4_mini_json` | none | not listed | template-derived | T0 candidate; only vLLM names a parser, so plan client-side parsing on the other engines |
| Granite 3.x / 4.x | `granite`, `granite4` | none (reasoning `granite_thinking_parser` only) | JSON_NATIVE (Granite 3.3 and 4.0 tested) | template-derived | none |
| Command models (R7B, A) | `cohere_command3`, `cohere_command4` | `cohere_command4` | Command R7B JSON_NATIVE tested; dedicated `cohere2moe` init | built-in (publisher-named) | vLLM needs the separate `cohere_melody` package |
| Seed-OSS `<seed:tool_call>` | `seed_oss` | none | TAG_WITH_TAGGED tested | no built-in parser | none |
| LFM2 | `lfm2` | `lfm2` | dedicated `lfm2`, `lfm2_5` inits | built-in `lfm2`, `lfm2-thinking` | none |
| Olmo 3 | `olmo3` | none | not listed | built-in `olmo3`, `olmo3-think` | none |
| Nemotron 3 | none (reasoning `nemotron_v3` only) | none (reasoning `nemotron_3` only) | TAG_WITH_TAGGED (Nemotron-3-Nano tested) | built-in `nemotron-3-nano`, `nemotron-3.5-nano` | parse client-side on vLLM and SGLang unless a probe finds a working parser |
| Apertus | `apertus` | `apertus2509` | JSON_NATIVE (Apertus-8B tested) | no built-in parser | none |
| Other registered names | `dots`, `ernie45`, `gigachat3`, `hunyuan_a13b`, `hy_v3`, `hy_v4`, `inkling`, `internlm`, `jamba`, `ling3`, `longcat`, `mimo`, `minicpm5`, `muse_glimmer`, `poolside_v1`, `step3`, `xlam` | `dots`, `gigachat3`, `gigachat35`, `hunyuan`, `inkling`, `interns1`, `iquest_q1`, `ling3`, `mimo`, `minicpm5`, `muse`, `poolside_v1`, `spark25`, `step3`, `trinity` | dedicated inits `functionary_v3_2`, `gigachat_v3`, `ling3`, `minicpm5`, `muse_glimmer`, `muse_spark`; tested Functionary v3.1/v3.2, Firefunction v2, Apriel 1.5 | built-in `cogito`, `glimmer`, `glm-ocr`, `laguna`, `ornith`, `passthrough`, `poolside-v1`, `qwen3-vl-instruct`, `qwen3-vl-thinking` | MiMo-V2.6-Pro (T3) maps to `mimo` by name only (not verified) |
| No native format (any text model) | none; force with `tool_choice` required or named (structured outputs) or `structured_outputs.json` | none; `tool_choice` required or named (EBNF), or `response_format` json_schema / `ebnf` | undetected markers: auto-parser logs an error and disables tool parsing; `function-calling.md` still describes a "Generic" handler | `response_format` JSON mode (compatibility API) | Switchyard JSON fallback protocol (§4.4) |

## Open-weight model families

Families released to date, latest generations first (tasks A06-A07).

| Family | Latest open-weight releases (hub IDs) | Architecture (total/active) | Context | License | Engines (llama.cpp / vLLM / SGLang / Ollama / MLX / transformers) | Chat template | Tool-call format | Reasoning format | Quirks |
|---|---|---|---|---|---|---|---|---|---|
| gpt-oss | `openai/gpt-oss-120b`, `openai/gpt-oss-20b` (2025-08); `openai/gpt-oss-safeguard-120b`, `openai/gpt-oss-safeguard-20b` (safety-classifier variants). The hub org listing shows no newer gpt-oss model | MoE. 120b: 117B / 5.1B, 36 layers, 128 experts top-4, alternating sliding-window (128) and full attention, MXFP4 MoE weights. 20b: 21B / 3.6B | 131,072 (YaRN x32 from 4,096) | `Apache-2.0` | llama.cpp: dedicated handler `gpt-oss.cpp` / vLLM: `--tool-call-parser openai`, reasoning `openai_gptoss` / SGLang: `gpt-oss`, `gpt-oss` / Ollama: built-in harmony parser; `gpt-oss:20b` 14GB, `gpt-oss:120b` 65GB / MLX: `gpt_oss` / transformers: yes | harmony | `<\|start\|>assistant<\|channel\|>commentary to=functions.NAME <\|constrain\|>json<\|message\|>{...}<\|call\|>` | `analysis` channel; `Reasoning: low\|medium\|high` in the system message (default medium); no off level | hub template re-renders past calls as `commentary json` (no `<\|constrain\|>`) and stamps today's date; store `<\|return\|>` as `<\|end\|>`; keep CoT only on tool-call turns; vLLM Chat Completions: function tools only, `tool_choice="auto"` only |
| Llama | `meta-llama/Llama-4-Scout-17B-16E-Instruct`, `meta-llama/Llama-4-Maverick-17B-128E-Instruct` (+`-FP8`) (2025-04); `meta-llama/Llama-3.3-70B-Instruct`; `meta-llama/Llama-3.2-1B-Instruct`, `meta-llama/Llama-3.2-3B-Instruct`. No newer LLM in that org (see Muse row) | Llama 4: MoE, 17B active, 16 / 128 experts (per ID), totals ?; Llama 3.x: dense | Scout 10M, Maverick 1M; 3.x ? | Llama community licenses (not re-verified today) | llama.cpp: Llama 3.x native (docs), others via autoparser / vLLM: `llama3_json`, `llama4_json`, `llama4_pythonic`, `pythonic` / SGLang: `llama3`, `pythonic` / Ollama: ? / MLX: `llama`, `llama4` / transformers: yes | 3.x: `<\|start_header_id\|>role<\|end_header_id\|>...<\|eot_id\|>`; 4: `<\|header_start\|>role<\|header_end\|>...<\|eot\|>`; tool output role `ipython` | 3.1: `{"name": "f", "parameters": {...}}` (alt. `<function=f>{...}</function>`); 4: `[f(a=1), g(b="x")]` or a JSON array | none | vLLM: no parallel calls with `llama3_json`, arrays may arrive as strings; `pythonic` parser fails when text and calls are mixed; 3.x built-in tools need `Environment: ipython` |
| Muse | `meta-models/Muse-Glimmer-30B` (+`-GGUF`, `-assistant`) (2026-08); `meta-models/Muse-Spark-Open-816B-A42B` (+`-NVFP4`) (card: 2026-10-15) | Glimmer: dense 29.6B incl. ~1.8B vision encoder, 52 layers, local (2048) : global = 3:1, gated attention, GQA 32/2. Spark Open: MoE 816B / 42B, 256 experts top-8, 62 layers | Glimmer 131,072+; Spark Open 1M | Glimmer `Apache-2.0`; Spark Open "Muse Model License" | llama.cpp: handlers `muse-glimmer.cpp`, `muse-spark.cpp` / vLLM: `muse_glimmer`, `muse_glimmer` / SGLang: `muse`, `muse` / Ollama: parser `glimmer` / MLX: `muse_glimmer`, `muse_spark` / transformers: yes | harmony-like: `<\|start\|>role[ to=recipient]<\|message\|>...<\|eot\|>` or `<\|eom\|>`; `# Valid recipients:` list | `<\|start\|>assistant to=NAME<\|message\|><atem:function_calls><atem:invoke name="NAME"><atem:parameter name="k">v</atem:parameter></atem:invoke></atem:function_calls>` | `to=self` messages; `Reasoning strength: low\|medium\|high\|xhigh` (Glimmer template default high; Spark default xhigh, other values rejected) | shares tokens with harmony but has no channels, no `functions.` prefix, XML arguments: needs its own parser; Glimmer template replays all prior `reasoning_content`; cards give no parser flags |
| Gemma | Gemma 4: `google/gemma-4-E2B-it`, `google/gemma-4-E4B-it`, `google/gemma-4-26B-A4B-it`, `google/gemma-4-31B-it` (2026-03), `google/gemma-4-12B-it` (2026-05), QAT GGUF e.g. `google/gemma-4-E4B-it-qat-q4_0-gguf`; Gemma 3n: `google/gemma-3n-E4B-it`; `google/functiongemma-270m-it` | E2B dense 5.1B with embeddings (2.3B effective, per-layer embeddings); E4B 8B (4.5B effective); 12B dense 11.95B; 26B-A4B MoE 25.2B / 3.8B (128 experts top-8 + 1 shared); 31B dense 30.7B; sliding-window + global layers | E2B/E4B 128K; 12B/26B/31B 256K; FunctionGemma 32K | Gemma 4: `Apache-2.0`; Gemma 3n and FunctionGemma: hub tag `gemma` (gated) | llama.cpp: dedicated `gemma4.cpp` / vLLM: `gemma4`, `gemma4`; `functiongemma` / SGLang: `gemma4`, `gemma4` / Ollama: parsers `gemma4`, `functiongemma` / MLX: `gemma4`, `gemma3n` / transformers: latest | `<\|turn>role\n...<turn\|>`; assistant role `model`; system and developer merged into one system turn | `<\|tool_call>call:NAME{k:<\|"\|>text<\|"\|>,n:1}<tool_call\|>`; result `<\|tool_response>response:NAME{...}<tool_response\|>` | `<\|think\|>` at start of system turn; output `<\|channel>thought\n...<channel\|>`; vLLM: off by default | not JSON (strings delimited by `<\|"\|>`); arguments must be an object; 12B/26B/31B emit an empty thought block when off; size RAM by total, not "E" params |
| Phi | `microsoft/Phi-4-reasoning-vision-15B` (2026-01); `microsoft/Phi-4-mini-flash-reasoning`, `microsoft/Phi-mini-MoE-instruct`, `microsoft/Phi-tiny-MoE-instruct` (2025-06); `microsoft/Phi-4-mini-reasoning`, `microsoft/Phi-4-reasoning`, `microsoft/Phi-4-reasoning-plus`, `microsoft/Phi-4-mini-instruct`, `microsoft/phi-4` | Phi-4-mini-instruct: dense 3.8B, GQA, 200K vocab; others ? | Phi-4-mini 128K; others ? | Phi-4-mini-instruct `MIT`; others ? | llama.cpp: ? / vLLM: tool `phi4_mini_json`, no Phi reasoning parser / SGLang: none / Ollama: ? / MLX: ? / transformers: yes | `<\|system\|>...<\|end\|><\|user\|>...<\|end\|><\|assistant\|>` | tools declared as JSON inside `<\|tool\|>[...]<\|/tool\|>` in the system turn; output syntax not on card | reasoning variants exist; format ? | partially verified; no text-only Phi newer than 2025-06 |
| Mistral | `mistralai/Mistral-Small-4-119B-2603`; `mistralai/Mistral-Medium-3.5-128B`; `mistralai/Mistral-Large-3-675B-Instruct-2512`; `mistralai/Devstral-2-123B-Instruct-2512`; `mistralai/Devstral-Small-2-24B-Instruct-2512`; `mistralai/Ministral-3-{3B,8B,14B}-{Instruct,Reasoning}-2512`; `mistralai/Magistral-Small-2509`; `mistralai/Leanstral-1.5-119B-A6B` | Small 4: MoE 119B / 6.5B (128 experts, 4 active); Medium 3.5: dense 128B; Large 3: 675B (arch ?); Devstral 2: 123B (hub arch tag `ministral3`); Devstral Small 2: 24B, Ministral 3 arch; Ministral 3 8B: 8.4B LM + 0.4B vision | 256K (Small 4, Medium 3.5, Devstral Small 2, Ministral 3) | `Apache-2.0`: Small 4, Large 3, Devstral Small 2, Ministral 3. Modified `MIT` with revenue cap: Devstral 2 (no rights above $20M monthly revenue), Medium 3.5 (large-revenue exception) | llama.cpp: handler `ministral3.cpp` (Devstral Small 2: PR #17945) / vLLM: `mistral`, `mistral` / SGLang: `mistral`, `mistral` / Ollama: parser `ministral`, model `devstral-small-2` / MLX: `mistral3`, `ministral3`, `mistral4` / transformers: v5 (`Mistral3ForConditionalGeneration`) | `[SYSTEM_PROMPT]...[/SYSTEM_PROMPT][AVAILABLE_TOOLS][...][/AVAILABLE_TOOLS][INST]...[/INST]` | `[TOOL_CALLS]NAME[ARGS]{...}` per call; results `[TOOL_RESULTS]...[/TOOL_RESULTS]` | `[THINK]...[/THINK]`; `reasoning_effort` `none` or `high` (Small 4, Medium 3.5) | vLLM has two modes, `--tokenizer_mode mistral` (`mistral_common`) or `hf`; Ministral 3 card still needs `--tokenizer_mode/--config_format/--load_format mistral`; cards ship required system prompts; keep reasoning in context (Ministral 3 Reasoning) |
| Granite | `ibm-granite/granite-4.2-3b`, `ibm-granite/granite-4.2-8b`, `ibm-granite/granite-4.2-30b` (2026-08-25; `-GGUF`, `-fp8`, MLX repos); `ibm-granite/granite-4.1-{3b,8b,30b}` (2026-04-29); `ibm-granite/granite-switch-4.1-{8b,30b}` (2026-10-14, not researched) | 4.2: dense `GraniteForCausalLM` (3B 40 layers, 8B 40, 30B 64); 4.1-8b dense (hub 9B); 4.0 hybrid ? | 4.2: 128K native (card also claims a 512K extension); 4.1: 131,072 | `Apache-2.0` | llama.cpp: GGUF, autoparser / vLLM: 4.2 `qwen3_coder` + `granite_thinking_parser` (or `nemotron_v3`); 4.0/4.1 `granite4` / SGLang: `auto` (resolves to `qwen3_coder`, `nemotron_3`), >=0.5.18 / Ollama: ? / MLX: `granite`, `granitemoe`, `granitemoehybrid` / transformers: yes | ? | 4.2: `<tool_call><function=NAME><parameter=k>v</parameter></function></tool_call>`; 4.1: `<tool_call>{"name": ..., "arguments": {...}}</tool_call>` | 4.2: `<think>...</think>`, on by default; `enable_thinking=False`; `low_effort=True`; older thinking stripped | tool format changed between 4.1 and 4.2; 4.1 lists FIM (tokens not on card) |
| OLMo | `allenai/Olmo-3.1-32B-Instruct`, `allenai/Olmo-3.1-32B-Think` (2025-12); `allenai/Olmo-3-7B-Instruct`, `allenai/Olmo-3-7B-Think`, `allenai/Olmo-3-32B-Think` (2025-11); `allenai/Olmo-Hybrid-Instruct-DPO-7B`, `allenai/Olmo-Hybrid-Think-SFT-7B` (2026-02) | dense 7B, 32B; Hybrid 7B "hybrid RNN" (SSM cache in vLLM; layer layout not on card) | ? (not on cards) | `Apache-2.0` (plus responsible-use guidelines) | llama.cpp: ? / vLLM: `olmo3`, `olmo3` / SGLang: none / Ollama: parsers `olmo3`, `olmo3_think` / MLX: `olmo3`, `olmo_hybrid` / transformers: >=4.57 (Olmo 3), >=5.3 (Hybrid) | ChatML `<\|im_start\|>...<\|im_end\|>`; default system message with empty `<functions></functions>` | pythonic calls inside `<function_calls>...</function_calls>`, one per line | Think variants (tag ?) | vLLM `olmo3` parser accepts JSON and Python literals |
| Nemotron | `nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-BF16` (+`-24GB`, `-24GB-GGUF`) (card: 2026-10-15); `nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16` (2026-10); `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16` (2026-08-11); `nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16` (2026-06); `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16` (2026-03); `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` (+`-GGUF`) | hybrid Mamba-2 + attention + MoE: Lightning 30B / 3B; Bolt 74B / 9B (LatentMoE + vision encoder); Super 120B / 12B (hub 123.6B, `nemotron_h`) | Lightning up to 1M; Bolt 256K | 3.5 (Lightning, Bolt): OpenMDW-1.1; 3 (Super): `nvidia-nemotron-open-model-license` | llama.cpp: GGUF repos, autoparser / vLLM: `qwen3_coder` + `nemotron_v3` (Bolt: plugin `ultra_v3`) / SGLang: `qwen3_coder` + `nemotron_3` / Ollama: parser `nemotron3nano` / MLX: `nemotron_h` / transformers: `custom_code` | ? | Qwen3-Coder-style XML (from parser name) | `<think>...</think>`; `enable_thinking` (default on); Bolt `medium_effort` | license changed between 3 and 3.5; Bolt 24GB uses native 2-bit weights; Bolt reasoning parser is out-of-tree |
| Falcon | `tiiuae/Falcon-H1R-7B` (+`-GGUF`, `-FP8`); `tiiuae/Falcon-H1-Tiny-90M-Instruct`, `tiiuae/Falcon-H1-Tiny-R-0.6B`, `tiiuae/Falcon-H1-Tiny-Tool-Calling-90M`, `tiiuae/Falcon-H1-Tiny-Coder-90M` (2026-01) | hybrid attention + Mamba-2; H1R hub 8B | H1R: vLLM default `--max-model-len` 262144 (no spec stated) | Falcon-LLM License | llama.cpp: ? / vLLM: `hermes`, `deepseek_r1` (>=0.11.0) / SGLang: ?, `deepseek-r1` / Ollama: ? / MLX: `falcon_h1` / transformers: needs `mamba-ssm[causal-conv1d]` | ? | Hermes-style (parser `hermes`) | `<think>...</think>` | card suggests repetition/presence penalties against endless loops; up to 65,536 new tokens |
| SmolLM3 | `HuggingFaceTB/SmolLM3-3B`, `HuggingFaceTB/SmolLM3-3B-Base` (2025-07); no newer SmolLM on the hub | dense 3B, GQA, NoPE (3:1 ratio) | 64K trained (65,536 default); 128K with YaRN x2 | `Apache-2.0` | llama.cpp: listed on card / vLLM: `hermes` / SGLang: yes / Ollama: ? / MLX: listed on card / transformers: >=4.53 | ? | `xml_tools`: `<tool_call>{"name": ..., "arguments": {...}}</tool_call>`; `python_tools`: `<code>f(a="x")</code>` | thinking on by default; `/think`, `/no_think` in the system prompt override `enable_thinking` | system-prompt flag beats the template kwarg |
| LFM2 / LFM2.5 | `LiquidAI/LFM2.5-24B-A2B` (+`-GGUF`, `-FP8`, MLX) (2026-10); `LiquidAI/LFM2.5-2.6B`; `LiquidAI/LFM2.5-VL-3B`; drafter repos `LiquidAI/LFM2.5-8B-A1B-DSpark`, `LiquidAI/LFM2.5-1.2B-Instruct-DSpark` | 24B-A2B: 40 layers (30 gated short-conv + 10 GQA), MoE 64 experts top-4, 24.0B / ~2.45B | 131,072 (24B-A2B) | hub tag `lfm1.0` (full name not on card) | llama.cpp: dedicated `lfm2.cpp` / vLLM: tool `lfm2`, no `lfm2` reasoning parser / SGLang: `lfm2` / Ollama: parser `lfm2` / MLX: `lfm2`, `lfm2_moe` / transformers: >=5.0 | ChatML `<\|im_start\|>...<\|im_end\|>` | `<\|tool_call_start\|>[f(a=1)]<\|tool_call_end\|>` (pythonic; JSON optional); tools as JSON in the system turn | 24B-A2B always thinks (`<think>` opened by the template); no toggle | reasoning cannot be disabled; constraints must start after the think block |
| Command A | `CohereLabs/command-a-plus-05-2026-bf16` (+`-fp8`, `-w4a4`) (2026-05); `CohereLabs/North-Mini-Code-1.0` (2026-06); `CohereLabs/command-a-reasoning-08-2025`; `CohereLabs/c4ai-command-a-03-2025` | A+: MoE 218B / 25B, 128 experts top-8 + 1 shared, sliding+RoPE / global NoPE 3:1; North-Mini-Code: MoE 30B / 3B, same layout; A Reasoning: 111B (hub) | A+: 128K in / 64K out; North-Mini-Code: 256K in / 64K out | A+ and North-Mini-Code: `Apache-2.0`; A Reasoning: `CC-BY-NC-4.0` (non-commercial) | llama.cpp: handler `cohere2moe.cpp` / vLLM: `cohere_command4` x2 (A+ >=0.21.0, North on main), `cohere_command3` (A Reasoning) / SGLang: `cohere_command4` x2 / Ollama: built-in parser (publisher-named) / MLX: model modules for both generations / transformers: yes | ? (not on cards) | not on cards; vLLM parsing needs `cohere_melody>=0.9.0` | `<\|START_THINKING\|>...<\|END_THINKING\|>`; interleaved thinking (North) | license depends on release; send reasoning and tool calls back (North) |
| Apertus | `swiss-ai/Apertus-v1.5-8B`, `swiss-ai/Apertus-v1.5-70B` (2026-07); `swiss-ai/Apertus-v1.1-0.5B-Instruct`, `swiss-ai/Apertus-v1.1-1.5B-Instruct`, `swiss-ai/Apertus-v1.1-4B-Instruct` (2026-05); v1: `swiss-ai/Apertus-8B-Instruct-2509`, `swiss-ai/Apertus-70B-Instruct-2509` | dense decoder, xIELU; v1.5-8B hub 9B; v1.5 adds image and audio input | 262,144 (v1.5) | `Apache-2.0` + acceptable-use policy | llama.cpp: ? / vLLM: tool `apertus` (main); reasoning `apertus` only in the fork image / SGLang: `apertus2509`, `apertus2509` / Ollama: ? / MLX: `apertus` / transformers: fork branch (v1.5) | ? | JSON list of single-key objects between tool prefix/suffix tokens (SGLang docs) | `<\|inner_prefix\|>...<\|inner_suffix\|>`; off by default; `enable_thinking=True` | no tool calls in thinking mode; v1.5 needs forked vLLM and transformers |
| Trinity / AFM | `arcee-ai/Trinity-Large-Thinking` (+`-GGUF`, `-FP8-Block`, `-W4A16`, `-NVFP4`) (2026-04); `arcee-ai/Trinity-Large-Preview`, `arcee-ai/Trinity-Large-Base`, `arcee-ai/Trinity-Large-TrueBase` (2026-01); `arcee-ai/Trinity-Mini`; `arcee-ai/Trinity-Nano-Preview` | `AfmoeForCausalLM` MoE. Large: ~398B / ~13B, 256 experts + 1 shared, top-4, 6 dense layers. Mini: 26B / 3B. Nano: ? | Large 512K (8,192 pretraining); Mini 128K | OpenMDW-1.1 | llama.cpp: GGUF, autoparser / vLLM: Large `qwen3_coder` + `deepseek_r1` (>=0.11.1), Mini `hermes` + `deepseek_r1` / SGLang: `trinity` (registry) / Ollama: ? / MLX: `afmoe` / transformers: yes | template re-wraps `reasoning_content` in `<think>` | Large: Qwen3-Coder-style XML; Mini: Hermes JSON (both from parser names) | `<think>...</think>`, must stay in context | two tool formats inside one family |
| Laguna | `poolside/Laguna-S-2.1` (+`-GGUF`, `-FP8`, `-INT4`, `-NVFP4`, `-DFlash`) (2026-07); `poolside/Laguna-XS-2.1-GGUF` (+`-INT4`, `-DFlash`) | S 2.1: MoE 118B / 8B, 256 experts top-10 + 1 shared, 48 layers (12 global + 36 sliding-512); XS 2.1: 33B / 3B | 1,048,576 | OpenMDW-1.1 | llama.cpp: fork branch `laguna` (upstream PR #25165 in review) / vLLM: `poolside_v1`, `poolside_v1` / SGLang: `poolside_v1`, `poolside_v1` / Ollama: `laguna-s-2.1`, parser `laguna` / MLX: `laguna` / transformers: ? | ? | not on card (`poolside_v1`) | interleaved, preserved thinking (keep `reasoning_content`); `enable_thinking`; SGLang detector uses `<think>` | TRT-LLM reasoning parser is named `laguna`; upstream llama.cpp support pending |
| Qwen (2.5, 3, 3-2507, 3-Next, 3-Coder, 3.5, 3.6, 3.8) | `Qwen/Qwen3.8-27B` (2026-08-05), `Qwen/Qwen3.8-Flash-Next` (2026-08-24), `Qwen/Qwen3.8-2.4T-A95B` (2026-08-08, text-only); `Qwen/Qwen3.6-27B`, `Qwen/Qwen3.6-35B-A3B` (2026-04); `Qwen/Qwen3.5-397B-A17B`, `-122B-A10B`, `-35B-A3B`, `-27B`, `-9B`, `-4B`, `-2B`, `-0.8B` (2026-02); `Qwen/Qwen3-Coder-Next` (2026-01-30); `Qwen/Qwen3-Next-80B-A3B-Instruct` / `-Thinking`; `Qwen/Qwen3-4B-Instruct-2507`; `Qwen/Qwen3-Coder-30B-A3B-Instruct`; `-FP8` twins. No 3.7 open weights in the org listing | 3.5/3.6/3.8: hybrid Gated DeltaNet + gated attention 3:1, 1 MTP layer, vision-language checkpoints. 3.8-27B dense 27B; 3.6-35B-A3B 35B/3B (256 experts, 8+1); 3.5-397B-A17B 397B/17B; 3.8-Flash-Next 125B/6B + 51B n-gram embedding + 4B MTP, sparse attention (arch `qwen4_exp`, ~180B stored); 3.8-2.4T 2.4T/95B; Next and Coder-Next 80B/3B | 262,144 native; YaRN to 1,010,000 (3.5, 3.6, 3.8-2.4T) or 1,000,000 (3.8-27B, 3.8-Flash-Next) | Apache-2.0 (2.5, 3, 2507, Next, Coder, 3.5, 3.6, 3.8-27B); `qwen-community-1.0` (3.8-Flash-Next); `qwen3.8-max` (3.8-2.4T) | llama.cpp: archs `qwen2`, `qwen3`, `qwen3moe`, `qwen3next`, `qwen35`, `qwen35moe`, `qwen4exp`; official `ggml-org/Qwen3.8-27B-GGUF`. vLLM: `--reasoning-parser qwen3 --tool-call-parser qwen3_coder` (3.5-3.8, Coder), `hermes` (2.5/3/2507/Next); >=0.19.0 for 3.6. SGLang: same names; >=0.5.10 for 3.6. Ollama: parsers `qwen3`, `qwen3-thinking`, `qwen3-coder`, `qwen3.5`; renderer `qwen3.8`. MLX-LM: `qwen3_5`, `qwen3_5_moe`, `qwen3_next` (no `qwen4_exp`). transformers: `qwen3_5`, `qwen3_5_moe`, `qwen3_next`, `qwen4_exp` | ChatML `<\|im_start\|>role\n…<\|im_end\|>`; tool results as `<tool_response>…</tool_response>` inside a user turn | 2.5/3/2507/Next: JSON in tags `<tool_call>\n{"name":…,"arguments":{…}}\n</tool_call>`. Coder, 3.5-3.8: XML `<tool_call>\n<function=f>\n<parameter=k>\nv\n</parameter>\n</function>\n</tool_call>` | `<think>…</think>`. 3.5-3.8 prompt ends with `<think>\n` (output lacks the opening tag); `enable_thinking=false` inserts an empty think block. 3.8: `reasoning_effort` xhigh (default) / medium / low, rendered as system text; `preserve_thinking` default true. 3.6: `preserve_thinking` opt-in. 3.5: past reasoning kept only after the last user query. 3.8-2.4T: thinking cannot be disabled. Qwen3 hybrid: `enable_thinking`, `/think`, `/no_think`. 2507-Instruct, Next-Instruct, Coder: non-thinking only | 3.8 thinking: T 1.0, top_p 0.95, top_k 20, presence 0.0; non-thinking: T 0.7, top_p 0.8, presence 1.5. 3.5-0.8B defaults to non-thinking (4B defaults to thinking). Template raises if no user message. Text-only serving of VL checkpoints: vLLM `--language-model-only`. Coder-Next: T 1.0, top_p 0.95, top_k 40 |
| DeepSeek (V3, R1, V3.1, V3.2, V4, V4.1) | `deepseek-ai/DeepSeek-V4.1-Flash` (2026-09-10, multimodal); `deepseek-ai/DeepSeek-V4-Pro-0813` (2026-08-13); `deepseek-ai/DeepSeek-V4-Flash-0731` (2026-07-31); `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp` (2026-08-31); `deepseek-ai/DeepSeek-V4-Pro`, `deepseek-ai/DeepSeek-V4-Flash` (preview, 2026-04-22); `deepseek-ai/DeepSeek-V3.2`, `-V3.2-Speciale`; `-V3.1-Terminus`, `-V3.1`; `-R1-0528`; `-V3-0324` | All MoE. V3.x/R1 671B/37B, MLA, 1 MTP; V3.2 adds sparse attention (index_topk 2048). V4-Pro 1.6T/49B, V4-Flash 284B/13B: compressed sparse + heavily compressed attention, SWA 128, 1 MTP, FP4 experts + FP8. V4.1-Flash: causal encoder-decoder (20+20 layers), CSA2, 552B backbone + 196B memory table, 8B (prefill) / 16B (decode) active, 384 experts top-6 | V3.x/R1 128K (config 163,840); V4, V4.1 1,048,576 | MIT | llama.cpp: archs `deepseek2`, `deepseek32`, `deepseek4`; no V4.1 arch; dedicated DSML chat handler. vLLM: V3/R1 `deepseek_v3` + `deepseek_r1`; V3.1 `deepseek_v31`; V3.2 `--tokenizer-mode deepseek_v32 --tool-call-parser deepseek_v32 --reasoning-parser deepseek_v3`; V4 `--tokenizer-mode deepseek_v4` + `deepseek_v4`; V4.1 `--tokenizer-mode deepseek_v41` + `deepseek_v41`. SGLang: tool `deepseekv3`/`v31`/`v32`/`v4`/`v41`, reasoning `deepseek-r1`/`-v3`/`-v4`/`-v41`. Ollama: parser `deepseek3`, renderer `deepseek3.1`. MLX-LM: `deepseek_v3`, `deepseek_v32`, `deepseek_v41` (no `deepseek_v4`). transformers: `deepseek_v3`, `deepseek_v32`, `deepseek_v4` (no V4.1) | Special tokens `<｜begin▁of▁sentence｜>`, `<｜User｜>`, `<｜Assistant｜>`, `<｜end▁of▁sentence｜>`. V3.2, V4, V4-0731, V4-Pro-0813 ship no Jinja template (Python `encoding/encoding_dsv32.py`, `encoding_dsv4.py`); V4.1 ships `chat_template.jinja` ported from `encoding/encoding.py` | V3/R1: `<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>f` + fenced JSON + `<｜tool▁call▁end｜><｜tool▁calls▁end｜>`; V3.1: same tokens, raw JSON. V3.2: DSML `<｜DSML｜function_calls><｜DSML｜invoke name="f"><｜DSML｜parameter name="k" string="true">v</｜DSML｜parameter>…`; V4: block `<｜DSML｜tool_calls>`; V4.1: leading-space tags `<｜DSML｜ calls>`, `<｜DSML｜ invoke …>`. Results: V3.2 `<function_results>`, V4/V4.1 `<tool_result>` inside user turn | `<think>…</think>`. V3.1: `thinking` kwarg (default false). V3.2: `thinking_mode` + `drop_thinking`. V4 preview: Non-think / Think High / Think Max. V4-0731/0813: `reasoning_effort` low (default, no prompt) / high / max (system prefix). V4.1: effort integer 1-100 or low/medium/high/max = 50/62/75/100 (default high), rendered `Reasoning Effort: N`; `drop_thinking` default true but disabled when tools are present | Sampling T 1.0, top_p 0.95 (agentic) or 1.0. High/max effort: max output >=384K (V4 cards). V3.2-Speciale has no tool calling. llama.cpp DSML detection looks for a `dsml_token` variable; the V4.1 Jinja names it `DSML` (not verified end to end) |
| Kimi (K2, K2 Thinking, K2.5, K2.6, K2.7-Code, K3, Kimi-Linear) | `moonshotai/Kimi-K3` (2026-06-13); `moonshotai/Kimi-K2.7-Code` (2026-06-11); `moonshotai/Kimi-K2.6` (2026-04-14); `moonshotai/Kimi-K2.5` (2026-01-01); `moonshotai/Kimi-K2-Thinking` (2025-11-04); `moonshotai/Kimi-Linear-48B-A3B-Instruct` (2025-10-30); `moonshotai/Kimi-K2-Instruct-0905` (2025-09-03) | K2.x: MLA MoE 1T/32B, 384 experts top-8, no MTP; native INT4 QAT from K2-Thinking on; K2.5+ multimodal. K3: 2.8T/104B, 93 layers = 69 KDA linear + 24 gated MLA, 896 experts top-16 + 2 shared, MXFP4 weights / MXFP8 activations QAT, multimodal. Kimi-Linear 48B/3B KDA hybrid | K2.x 262,144; K3 1,048,576; Kimi-Linear 1,048,576 | K2.x `modified-mit`; K3 `kimi-k3` (custom); Kimi-Linear MIT | llama.cpp: dedicated K2 and K3 chat handlers; archs `kimi-linear`, `kimi-k3` (K2 GGUF arch not checked). vLLM: `kimi_k2`, `kimi_k3` (tool + reasoning). SGLang: tool `kimi_k2`/`kimi_k3`, reasoning `kimi_k2`/`kimi_k3`/`kimi`. Ollama: no dedicated parser. MLX-LM: `kimi_k2` mapped to `deepseek_v3`, `kimi_k25`, `kimi_k3`, `kimi_linear`. transformers: `kimi_k25`, `kimi_linear` (K3 via remote code) | K2.x: `<\|im_system\|>`/`<\|im_user\|>`/`<\|im_assistant\|>` name `<\|im_middle\|>` … `<\|im_end\|>`; tools in a `tool_declare` system message (JSON or TypeScript `tools_ts_str`); results as `## Return of {id}`. K3: XTML markup on control tokens `<\|open\|>`, `<\|sep\|>`, `<\|close\|>`, `<\|end_of_msg\|>`; no Jinja; rendered by `encoding_k3.py` through the remote-code tokenizer | K2.x: `<\|tool_calls_section_begin\|><\|tool_call_begin\|>functions.f:0<\|tool_call_argument_begin\|>{json}<\|tool_call_end\|><\|tool_calls_section_end\|>`. K3: `<\|open\|>tools<\|sep\|><\|open\|>call tool="f" index="1"<\|sep\|><\|open\|>argument key="k" type="string"<\|sep\|>v<\|close\|>argument<\|sep\|>…<\|close\|>call<\|sep\|><\|close\|>tools<\|sep\|>` | `<think>…</think>` (K2.x); K3 `<\|open\|>think<\|sep\|>…<\|close\|>think<\|sep\|>` then a `response` block. K2.5/K2.6: `thinking` kwarg; K2.6 `preserve_thinking` default false (keeps reasoning only after the last non-tool-call assistant turn). K2.7-Code forces thinking and preserved thinking. K3: always thinking, `reasoning_effort` low/high/max (default max); trained with preserved thinking and requires the full assistant message (`reasoning_content` + `tool_calls`) to be sent back | K2 parsers expect tool-call IDs `functions.NAME:IDX`; K3 drops that requirement. K2.6/K2.7: T 1.0, top_p 0.95 (K2.6 Instant mode T 0.6); K3 evaluation: T 1.0, top_p 0.95, or 1.0 for agentic tasks. K3 license: separate agreement for model-as-a-service operators above $20M revenue per 12 months; attribution above 100M MAU or $20M monthly revenue |
| GLM (4.5, 4.6, 4.7, 5, 5.1, 5.2, 5.3) | `zai-org/GLM-5.3` (2026-08-25, FP8; BF16 in `zai-org/GLM-5.3-BF16`); `zai-org/GLM-5.3-Flash` (2026-08-25); `zai-org/GLM-5.2` (2026-06-16); `zai-org/GLM-5.1` (2026-04-03); `zai-org/GLM-5` (2026-02-11); `zai-org/GLM-4.7-Flash` (2026-01-19); `zai-org/GLM-4.7` (2025-12-22); `zai-org/GLM-4.6` (2025-09-29); `zai-org/GLM-4.5`, `zai-org/GLM-4.5-Air` (2025-07-20); `-FP8` twins | GLM-5.x: MoE + sparse attention (`glm_moe_dsa`), 744B/40B (GLM-5 card; 5.1-5.3 configs have the same shape), 256 experts top-8, 1 MTP; 5.2 adds shared indexer. 5.3-Flash: new base, multimodal, 320B/18B, KDA linear + sparse attention 3:1 (`glm5_next`). 4.6/4.7: 355B/32B (`glm4_moe`). 4.7-Flash: 30B-A3B (`glm4_moe_lite`) | 5.2, 5.3, 5.3-Flash 1,048,576; 5, 5.1, 4.6, 4.7, 4.7-Flash 202,752; 4.5-Air 131,072 | MIT (4.5 to 5.2, 5.3-Flash); `glm-5.3` custom (5.3: MIT-style plus a security review for model-as-a-service operators above $10B revenue per 12 months) | llama.cpp: archs `glm4moe`, `glm-dsa`, `glm5-next`; chat via the template autoparser. vLLM: `--tool-call-parser glm47 --reasoning-parser glm47` (5.3 / 5.3-Flash recipes); `glm45` and `glm47` map to one implementation. SGLang: tool `glm45`/`glm` (4.5/4.6), `glm47` (4.7+); reasoning `glm45`. Ollama: parser `glm-4.7` (plus a GLM-4.6 parser file). MLX-LM: `glm4_moe`, `glm4_moe_lite`, `glm_moe_dsa` (no `glm5_next`). transformers: `glm4_moe`, `glm4_moe_lite`, `glm_moe_dsa`, `glm5_next`. KTransformers listed on 5.3 cards | `[gMASK]<sop>` + `<\|system\|>`, `<\|user\|>`, `<\|assistant\|>`, `<\|observation\|>`; results as `<tool_response>` after `<\|observation\|>` | `<tool_call>f<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>` (4.7+, no separators); 4.5/4.6: newline after the name and after each pair | `<think>…</think>`. 4.5/4.6: `enable_thinking`, `/nothink`. 4.7: `clear_thinking` unset strips reasoning before the last user turn; `false` keeps it. 5.2: effort high/max (default max) as `<\|system\|>Reasoning Effort: Max`, `enable_thinking` toggle. 5.3 / 5.3-Flash: effort low/high/max (default max), no thinking toggle in template (prompt always opens `<think>`), `clear_thinking` default false (all past reasoning kept); cards advise `clear_thinking=true` for chat | Eval sampling T 1.0, top_p 0.95-1.0. 5.3-Flash 4-bit GGUF: 156.8 GB (UD-IQ4_XS) to 199.7 GB (UD-Q4_K_XL). Same tag names, different layouts across 4.5/4.6 and 4.7+ |
| MiniMax (M1, M2, M2.1, M2.5, M2.7, M3) | `MiniMaxAI/MiniMax-M3` (2026-06-02, + `-MXFP8`); `MiniMaxAI/MiniMax-M2.7` (2026-04-09); `MiniMaxAI/MiniMax-M2.5` (2026-02-12); `MiniMaxAI/MiniMax-M2.1` (2025-12-20); `MiniMaxAI/MiniMax-M2` (2025-10-22); `MiniMaxAI/MiniMax-M1-80k`, `-M1-40k` (2025-06) | M2.x: MoE 230B/10B (M2 card; M2.1-M2.7 configs identical), full attention, 62 layers, 256 experts top-8, FP8. M3: multimodal MoE ~428B/~23B, sparse attention (MSA), 128 experts top-4 + 1 shared, 1 MTP. M1: 456B hybrid linear/full attention 7:1, 32 experts top-2 | M2-M2.5 196,608; M2.7 204,800; M3 1,048,576; M1 config 10,240,000 | M2, M2.1, M2.5 `modified-mit`; M2.7 non-commercial (commercial use needs written authorization); M3 `minimax-community` (non-commercial grant; commercial use needs attribution plus a notice, or written authorization above $20M yearly revenue); M1 Apache-2.0 | llama.cpp: archs `minimax-01`, `minimax-m2`, `minimax-m3`; dedicated M3 chat handler, M2 via autoparser. vLLM: `minimax_m2` (tool + reasoning; also reasoning `minimax_m2_append_think`), `minimax_m3`. SGLang: tool `minimax-m2`, `minimax-m3`; reasoning `minimax`, `minimax-append-think`, `minimax-m3`. Ollama: no dedicated parser. MLX-LM: `minimax` (M2 via remap), `minimax_m3_vl`. transformers: `minimax`, `minimax_m2`, `minimax_m3_vl` | Custom role markers `]~!b[`, `]~b]system`, `]~b]user`, `]~b]ai`, `]~b]tool`, end `[e~[`; M3 adds namespace token `]<]minimax[>[` | M2.x: `<minimax:tool_call>\n<invoke name="f">\n<parameter name="k">v</parameter>\n</invoke>\n</minimax:tool_call>`. M3: `]<]minimax[>[<tool_call>` + `]<]minimax[>[<invoke name="f">` + `]<]minimax[>[<k>v]<]minimax[>[</k>` (objects and arrays become nested elements) | M2.x: `<think>…</think>`, always on (prompt opens `<think>\n`); past reasoning kept only after the last user message. M3: `<mm:think>…</mm:think>`; `thinking_mode` enabled / adaptive / disabled (template default adaptive); reasoning rendered for every past assistant turn | M2.7: T 1.0, top_p 0.95, top_k 40; M3: T 1.0, top_p 0.95. No M1-specific tool parser in vLLM today. M2.7 license change makes it unusable for commercial defaults without authorization |
| MiMo (V2-Flash, V2.5, V2.6) | `XiaomiMiMo/MiMo-V2.6-Pro-MOPD`, `XiaomiMiMo/MiMo-V2.6-Flash-MOPD` (2026-09-27); `XiaomiMiMo/MiMo-V2.6-Pro-RL`, `XiaomiMiMo/MiMo-V2.6-Flash-RL`, `XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B` (2026-09-21); `XiaomiMiMo/MiMo-V2.5-Pro`, `XiaomiMiMo/MiMo-V2.5` (2026-04-27); `XiaomiMiMo/MiMo-V2-Flash` (2025-12-16) | MoE with interleaved sliding-window (128) and global attention plus sink bias, 3 MTP modules, no shared experts. Flash 309B/15B (48 layers: 39 SWA / 9 GA; 256 experts top-8); Pro 1.02T/42B (384 experts top-8); V2.6 weights FP4-packed + FP8 | V2.5, V2.6 1,048,576; V2-Flash 262,144 | MIT | llama.cpp: arch `mimo2`; chat via autoparser (compact XML is excluded from the Qwen3-Coder handler). vLLM: `--reasoning-parser mimo --tool-call-parser mimo`. SGLang: `mimo` (tool + reasoning). Ollama: no dedicated parser. MLX-LM: `mimo_v2_flash` only (V2.5/V2.6 `mimo_v2` not present). transformers: `mimo_v2_flash` only | ChatML; in V2.6 `<\|im_end\|>` has no trailing newline; tool results as role `tool` | `<tool_call><function=f><parameter=k>v</parameter></function></tool_call>` (no newlines) | `<think>…</think>`; thinking on unless `enable_thinking=false` (then `<think></think>`); V2.6 renders `reasoning_content` for every past assistant turn | T 1.0, top_p 0.95; vLLM `--generation-config vllm`. MOPD checkpoints are the later release that reduces repeated identical tool calls |
| Hunyuan / Hy (A13B, 0.5-7B, Hy3, Hy4) | `tencent/Hy4-preview` (2026-08-27, + `-FP8`); `tencent/Hy3` (2026-07-02, + `-FP8`); `tencent/Hy3-preview` (2026-04-13); `tencent/Hunyuan-A13B-Instruct` (2025-06-25); `tencent/Hunyuan-7B-Instruct`, `-4B-`, `-1.8B-`, `-0.5B-Instruct` (2025-07-30) | Hy4-preview MoE 770B/49B, sparse attention, 78 layers, 256 experts top-8 + 1 shared, 1 MTP. Hy3 MoE 295B/21B + 3.8B MTP, 192 experts top-8 + 1 shared. A13B MoE 80B/13B. Dense 0.5-7B | Hy4 1,048,576; Hy3 262,144; A13B 262,144 per card (config ships 32,768) | Hy3, Hy4-preview Apache-2.0; Hy3-preview `other`; A13B custom (`other`); dense 0.5-7B: no license tag in metadata | llama.cpp: archs `hunyuan-moe`, `hunyuan-dense`, `hy_v3`, `hy_v4`. vLLM: `hy_v3`, `hy_v4`, `hunyuan_a13b` (tool + reasoning). SGLang: `hunyuan` (tool + reasoning); Hy4 card uses `auto`. Ollama: no dedicated parser. MLX-LM: `hunyuan`, `hunyuan_v1_dense` (no Hy3/Hy4). transformers: `hunyuan_v1_dense`, `hunyuan_v1_moe`, `hy_v3` (card: >=5.6.0), `hy_v4` | All special tokens carry a `:opensource` suffix. Hy3: `<｜hy_User:opensource｜>`, `<｜hy_Assistant:opensource｜>`. Hy4: `<｜hy_start:opensource｜>role<｜hy_middle:opensource｜>…<｜hy_end:opensource｜>` | Hy3: `<tool_calls:opensource>\n<tool_call:opensource>f<tool_sep:opensource>\n<arg_key:opensource>k</arg_key:opensource>\n<arg_value:opensource>v</arg_value:opensource>\n</tool_call:opensource>\n</tool_calls:opensource>`; Hy4: same without `tool_sep` and newlines. A13B: `<tool_calls>[{"name":…,"arguments":…}]</tool_calls>` | Hy3/Hy4: `<think:opensource>…</think:opensource>`; `reasoning_effort` Hy3 no_think (default) / low / high, Hy4 high (default) / no_think, emitted as `<｜reasoning_mode:opensource｜>reasoning_effort:X`; `preserved_thinking` default false, or true when tools are passed. A13B: `<think>…</think><answer>…</answer>`, `enable_thinking`, `/think`, `/no_think` | Hy3/Hy4: T 0.9, top_p 1.0. Hy3 card: build vLLM/SGLang from source at release; Hy4: dedicated container images |
| ERNIE (4.5) | `baidu/ERNIE-4.5-VL-28B-A3B-Thinking` (2025-11-07); `baidu/ERNIE-4.5-21B-A3B-Thinking` (2025-09-08); `baidu/ERNIE-4.5-300B-A47B-PT`, `baidu/ERNIE-4.5-21B-A3B-PT`, `baidu/ERNIE-4.5-0.3B-PT` (2025-06-28; `-Paddle` and `-Base` variants). No newer open-weight ERNIE LLM on the hub today | MoE 300B/47B (64 experts top-8) and 21B/3B (64 experts top-6), 1 MTP (PT); dense 0.3B | 131,072 | Apache-2.0 | llama.cpp: archs `ernie4_5`, `ernie4_5-moe`. vLLM: `ernie45` (tool + reasoning); card: >=0.10.2, not 0.11.0. SGLang: no ERNIE parser. FastDeploy 2.2: `ernie_x1` parsers. Ollama: no dedicated parser. MLX-LM: `ernie4_5`, `ernie4_5_moe`. transformers >=4.54.0: `ernie4_5`, `ernie4_5_moe` | Thinking: ChatML-like `<\|im_start\|>` with a `think_mode=True` system setting. PT instruct: `<\|begin_of_sentence\|>` plain User/Assistant template without tools | JSON in tags `<tool_call>\n{"name":…,"arguments":…}\n</tool_call>` (Thinking); vLLM parser also strips a `<response>` wrapper | `<think>…</think>` (Thinking models only); past reasoning kept only after the last user query | `-PT` = transformers weights, `-Paddle` = PaddlePaddle weights |
| Seed-OSS | `ByteDance-Seed/Seed-OSS-36B-Instruct`, `-36B-Base`, `-36B-Base-woSyn` (2025-08-20) | Dense 36B, GQA | 524,288 | Apache-2.0 | llama.cpp: arch `seed_oss`. vLLM >=0.10.0: `seed_oss` (tool + reasoning). SGLang: no Seed-OSS parser. Ollama: no dedicated parser. MLX-LM: `seed_oss`. transformers: `seed_oss` | `<seed:bos>role\n…<seed:eos>` | `<seed:tool_call>\n<function=f>\n<parameter=k>v</parameter>\n</function>\n</seed:tool_call>` | `<seed:think>…</seed:think>` with `<seed:cot_budget_reflect>` progress notes; `thinking_budget` -1 (default, unlimited), 0 (no thinking), else tokens (multiples of 512 advised) | T 1.1, top_p 0.95 |
| Ling / Ring (1T, 2.5, 2.6, 3.0, 3.1) | `inclusionAI/Ling-3.1-flash` (2026-10-13); `inclusionAI/Ling-3.0-flash-VL` (2026-09-04); `inclusionAI/Ling-3.0-tiny` (2026-08-10); `inclusionAI/Ling-3.0-flash` (2026-08-02); `inclusionAI/Ring-2.6-1T` (2026-05-14); `inclusionAI/Ling-2.6-1T` (2026-04-29); `inclusionAI/Ling-2.6-flash` (2026-04-28); `inclusionAI/Ling-2.5-1T`, `inclusionAI/Ring-2.5-1T` (2026-02); `inclusionAI/Ring-1T`, `inclusionAI/Ling-1T` (2025-10) | Ling 3.x: KDA linear + MLA hybrid (3.0-flash 5:1, 3.0-tiny 3:1), MoE + 1 shared; 3.1-flash 560B/25B (512 experts), 3.0-flash 124B/5.1B, 3.0-tiny 7.9B/1.3B (128 experts top-8). 2.5/2.6: 1T hybrid (`bailing_hybrid`). Ring = reasoning line | 3.1-flash 1M per card (config 262,144); 3.0-flash 262,144; 3.0-tiny 131,072 (262,144 with YaRN in recipe); Ling-2.6-1T 262,144; Ring-2.6-1T 131,072 | MIT | llama.cpp: archs `bailingmoe`, `bailingmoe2`, `bailingmoe3`; dedicated Ling 3 chat handler. vLLM: `ling3` (tool + reasoning; 3.1 card uses a vLLM branch build). SGLang: `ling3` (dev images per release). Ollama: no dedicated parser. MLX-LM: `bailing_moe`, `bailing_moe_linear`, `bailing_moe_v3`, `bailing_hybrid`. transformers: `bailing_hybrid` | `<role>SYSTEM\|HUMAN\|ASSISTANT\|OBSERVATION</role>…<\|role_end\|>`; `detailed thinking on/off` appended to the system turn | Ling 3.x, Ring-2.6: `<tool_call>f\n<arg_key>k</arg_key>\n<arg_value>v</arg_value>\n</tool_call>`. Ling 2.x, Ring <=2.5: JSON in `<tool_call>` (template markers only) | `<think>…</think>`; `enable_thinking` (default on in 3.x); 3.1 template hard-sets preserved thinking; Ring-2.6 `reasoning_effort` high (default) / xhigh as `Reasoning: high` | 3.1: T 1.0, top_p 0.95, top_k 20; 3.0-flash: T 0.6 |
| InternLM / Intern-S (S1, S1-Pro, S2) | `internlm/Intern-S2-397B` (2026-09-13); `internlm/Atria-Dawn-Preview` (2026-09-11, GLM-5.2 based); `internlm/Intern-S2-Mobius` (2026-07-29); `internlm/Intern-S2-Preview` (2026-05-15); `internlm/Intern-S1-Pro` (2026-02-02); `internlm/Intern-S1-mini` (2025-08-18); `internlm/Intern-S1` (2025-07-24); `internlm/internlm3-8b-instruct` (2025-01) | S2-397B: Qwen3.5-397B layout (hybrid Gated DeltaNet, 512 experts top-10) + vision. S2-Preview: Qwen3.5-35B layout. S2-Mobius: 36B new arch (2,560 experts). S1-Pro 1T/22B (512 experts top-8). S1: Qwen3-235B-A22B text + vision (241B stored). S1-mini 8.5B | S2 262,144; S1-Pro 262,144; S1 65,536 (config) | Apache-2.0 (S1, S2); MIT (Atria-Dawn-Preview) | LMDeploy: `--reasoning-parser intern-s1 --tool-call-parser intern-s1` (S1), `interns2-preview` (S2). vLLM: `qwen3` + `qwen3_coder` (S2, recipe and Mobius card), `internlm` (S1). SGLang: `interns1` (tool + reasoning). llama.cpp: arch `internlm2`; S1/S2 GGUF support not checked. Ollama: no dedicated parser. transformers: remote code for `interns1*` / `interns2*` | ChatML `<\|im_start\|>…<\|im_end\|>` | S2: Qwen3-Coder XML. S1-Pro: JSON in `<tool_call>`. S1, S1-mini: `<\|action_start\|><\|plugin\|>\n{"name":…,"parameters":…}<\|action_end\|>` | `<think>…</think>`; `enable_thinking` (default on); S2-397B adds `clear_thinking` | S1-Pro/S2: T 0.8, top_p 0.95, top_k 50; S2-Mobius: T 0.8, top_p 1.0 |
| Step (step3, 3.5-Flash, 3.7-Flash, 5) | `stepfun-ai/Step-5-Preview` (2026-09-20; `-BF16`, `-NVFP4`); `stepfun-ai/Step-3.7-Flash` (2026-05-23; `-FP8`, `-NVFP4`, `-GGUF`); `stepfun-ai/Step-3.5-Flash` (2026-02-01; `-FP8`, `-GGUF-Q8_0`, `-GGUF-Q4_K_S`); `stepfun-ai/step3` (2025-07-28) | Step-5-Preview MoE 600B/27B, multimodal, 92 layers, 352 experts top-8, SWA 512 hybrid, 3 MTP. Step-3.7-Flash 198B (196B LM + 1.8B vision), ~11B active, 288 experts, 3 MTP. Step-3.5-Flash ~196B/11B text. step3 321B VL MoE | Step-5 1,048,576; 3.5/3.7 262,144 | 3.5, 3.7, step3 Apache-2.0; Step-5-Preview custom `LICENSE.txt` v1.0 (attribution above 100M MAU or $20M monthly revenue; separate commercial license for device makers) | llama.cpp: arch `step35` (3.5/3.7 text), Qwen3-Coder chat handler; official `stepfun-ai/Step-3.7-Flash-GGUF`. vLLM: `step3p5` (3.5/3.7), `step3`; `step5` only in the `vllm/vllm-openai:step-5-preview` image, absent from vLLM main and 0.31.0. SGLang: `step3p5` (tool parser = Qwen3-Coder detector), `step3`; no `step5`. Ollama: no dedicated parser. MLX-LM: `step3p5`. transformers: `step3p7` | ChatML | Qwen3-Coder XML `<tool_call>\n<function=f>\n<parameter=k>\nv\n</parameter>\n</function>\n</tool_call>` | `<think>…</think>`; prompt opens `<think>\n`; 3.7 and 5: `reasoning_effort` free string rendered `Reasoning: <value>` (allowed values not documented); Step-5 `preserve_thinking` default true | Step-5: T 1.0, top_p 0.95, top_k 0 |
| LongCat (Flash, Flash-Thinking, Flash-Lite, 2.0) | `meituan-longcat/LongCat-Flash-Lite-Sparse` (2026-07-31); `meituan-longcat/LongCat-2.0` (2026-07-05; `-FP8`, `-INT8`); `meituan-longcat/LongCat-Flash-Lite` (2026-01-27); `meituan-longcat/LongCat-Flash-Thinking-2601` (2026-01-14); `meituan-longcat/LongCat-Flash-Thinking` (2025-09-21); `meituan-longcat/LongCat-Flash-Chat` (2025-08-29) | LongCat-2.0 MoE 1.6T/~48B, sparse attention, 768 experts. Flash-Chat / Thinking 562B stored (512 experts). Flash-Lite 69B with n-gram embedding | 2.0: 1M per card (config 262,144); Thinking-2601 config 983,040; Flash-Lite 327,680; Flash-Chat 131,072 | MIT | llama.cpp: no LongCat arch. vLLM: `longcat` parser is a JSON-in-tags (Hermes) subclass: matches Flash-Chat only, not the arg_key format of 2601/Lite/2.0. SGLang: cookbook for 2.0; no `longcat` parser name in the registry. Ollama: none. MLX-LM: `longcat_flash`, `longcat_flash_ngram`. transformers: `longcat_flash` | `<longcat_system>`, `<longcat_user>`, `<longcat_assistant>`; tools in `<longcat_tool_declare>` | Flash-Chat: `<longcat_tool_call>\n{json}\n</longcat_tool_call>`. 2601, Lite, 2.0: `<longcat_tool_call>f\n<longcat_arg_key>k</longcat_arg_key>\n<longcat_arg_value>v</longcat_arg_value>\n</longcat_tool_call>` | `<longcat_think>…</longcat_think>`; `enable_thinking` (default true) appends `/think_on` or `/think_off` to the last user turn; `thinking_budget` (floored to 1024); `save_history_reasoning_content` default true | Format changed between Flash-Chat and later releases under the same tag name |
| MiniCPM (MiniCPM5) | `openbmb/MiniCPM5-2B` (2026-09-06; `-GGUF`, `-MLX`, `-GPTQ`); `openbmb/MiniCPM5-1B` (2026-05-21); `openbmb/MiniCPM-V-4.7-35B-A3B`, `openbmb/MiniCPM-V-4.7-1B` (2026-10) | MiniCPM5-2B: dense 2.52B (`LlamaForCausalLM`) | 131,072 | Apache-2.0 | llama.cpp: dedicated MiniCPM5 chat handler (llama arch); official GGUF. vLLM >=0.21: `minicpm5` tool parser. SGLang >=0.5.16: `minicpm5`. Ollama: via GGUF (card); no dedicated parser. MLX: official `-MLX` repo. transformers: llama | ChatML | `<function name="f"><param name="k">v</param></function>` | `<think>…</think>`; `enable_thinking` unset: model decides; false: empty think block; true: prompt opens `<think>` | T 1.0, top_p 0.95 |

## Default model candidates (SPEC §5)

### Candidates by tier

| Tier | Role | SPEC candidate | Status (ok / renamed / replaced / not found) | Recommended hub ID | GGUF repo + file | Quant | Size GB | Min RAM/VRAM GB | Context | Tool calls | License | Ollama tag | Checked |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T0 | router, summarizer | Qwen3-4B-Instruct-2507 | ok | `Qwen/Qwen3-4B-Instruct-2507` | `unsloth/Qwen3-4B-Instruct-2507-GGUF` `Qwen3-4B-Instruct-2507-Q4_K_M.gguf`; Q8_0: `ggml-org/Qwen3-4B-Instruct-2507-Q8_0-GGUF` `qwen3-4b-instruct-2507-q8_0.gguf` | Q4_K_M / Q8_0 | 2.50 / 4.28 | 3.7 at 8K ctx (Q4_K_M + f16 KV) | 262,144 | yes | Apache-2.0 | `qwen3:4b-instruct-2507-q4_K_M` (2.5GB), `qwen3:4b-instruct-2507-q8_0` (4.3GB) | 2026-10-16 |
| T0 | router/summarizer alt | Qwen3.5 4B | ok | `Qwen/Qwen3.5-4B` | `unsloth/Qwen3.5-4B-GGUF` `Qwen3.5-4B-Q4_K_M.gguf` / `Qwen3.5-4B-Q8_0.gguf` (vision only with `mmproj-BF16.gguf`, 0.68) | Q4_K_M / Q8_0 | 2.74 / 4.48 | 2.74 + KV | 262,144 (ext. 1,010,000) | yes | Apache-2.0 | `qwen3.5:4b-q4_K_M` (3.3GB), `qwen3.5:4b-q8_0` (5.2GB) | 2026-10-16 |
| T0 | classifier alt | Qwen3.5 2B | ok | `Qwen/Qwen3.5-2B` | `unsloth/Qwen3.5-2B-GGUF` `Qwen3.5-2B-Q4_K_M.gguf` / `Qwen3.5-2B-Q8_0.gguf` | Q4_K_M / Q8_0 | 1.28 / 2.01 | 1.28 + KV | 262,144 | yes | Apache-2.0 | `qwen3.5:2b-q4_K_M` (1.9GB), `qwen3.5:2b-q8_0` (2.7GB) | 2026-10-16 |
| T0 | classifier alt | Qwen3.5 0.8B | ok | `Qwen/Qwen3.5-0.8B` | `unsloth/Qwen3.5-0.8B-GGUF` `Qwen3.5-0.8B-Q4_K_M.gguf` / `Qwen3.5-0.8B-Q8_0.gguf`; `ggml-org/Qwen3.5-0.8B-GGUF` `Qwen3.5-0.8B-Q8_0.gguf` (0.83) | Q4_K_M / Q8_0 | 0.53 / 0.81 | 0.53 + KV | 262,144 | yes | Apache-2.0 | `qwen3.5:0.8b-q8_0` (1.0GB); no q4_K_M tag | 2026-10-16 |
| T0 | alt family (cross-check) | Gemma 4 E2B | ok | `google/gemma-4-E2B-it` | `unsloth/gemma-4-E2B-it-GGUF` `gemma-4-E2B-it-Q4_K_M.gguf` / `gemma-4-E2B-it-Q8_0.gguf`; QAT: `google/gemma-4-E2B-it-qat-q4_0-gguf` `gemma-4-E2B_q4_0-it.gguf` (3.35) | Q4_K_M / Q8_0 | 3.11 / 5.05 | 3.11 + KV | 128K | yes (native function calling) | Apache-2.0 | `gemma4:e2b-it-q4_K_M` (4.6GB), `gemma4:e2b-it-qat` (4.3GB) | 2026-10-16 |
| T0 | alt family (cross-check) | Gemma 4 E4B | ok | `google/gemma-4-E4B-it` | `unsloth/gemma-4-E4B-it-GGUF` `gemma-4-E4B-it-Q4_K_M.gguf` / `gemma-4-E4B-it-Q8_0.gguf`; QAT: `google/gemma-4-E4B-it-qat-q4_0-gguf` `gemma-4-E4B_q4_0-it.gguf` (5.15) | Q4_K_M / Q8_0 | 4.98 / 8.19 | 4.98 + KV | 128K | yes (native function calling) | Apache-2.0 | `gemma4:e4b-it-q4_K_M` (6.6GB), `gemma4:e4b-it-qat` (6.1GB) | 2026-10-16 |
| T0 | alt family | Phi-4-mini | replaced (exists as `microsoft/Phi-4-mini-instruct`, 2025-02; outdated) | drop; alt family covered by `google/gemma-4-E4B-it` | (Phi, for user-added registry entry) `unsloth/Phi-4-mini-instruct-GGUF` `Phi-4-mini-instruct-Q4_K_M.gguf` / `Phi-4-mini-instruct.Q8_0.gguf` | Q4_K_M / Q8_0 | 2.49 / 4.08 | 2.49 + KV | 128K | yes (`<\|tool\|>` format) | MIT | `phi4-mini:3.8b-q4_K_M` (2.5GB) | 2026-10-16 |
| T0 | FIM code completion | Qwen2.5-Coder-1.5B (FIM) | ok | `Qwen/Qwen2.5-Coder-1.5B` (base) | `ggml-org/Qwen2.5-Coder-1.5B-Q8_0-GGUF` `qwen2.5-coder-1.5b-q8_0.gguf` (no base-model Q4_K_M GGUF among the first 8-12 hub search results) | Q8_0 | 1.65 | 1.9 at 8K ctx | 32,768 | n/a (base model, FIM) | Apache-2.0 | `qwen2.5-coder:1.5b-base-q8_0` (1.6GB), `qwen2.5-coder:1.5b-base-q4_K_M` (986MB) | 2026-10-16 |
| T0 | embeddings | Qwen3-Embedding-0.6B | ok | `Qwen/Qwen3-Embedding-0.6B` | `Qwen/Qwen3-Embedding-0.6B-GGUF` `Qwen3-Embedding-0.6B-Q8_0.gguf` (repo has only Q8_0 and f16 1.20) | Q8_0 | 0.64 | 1.6 at 8K ctx | 32,768 | n/a | Apache-2.0 | `qwen3-embedding:0.6b` (639MB, same digest as `0.6b-q8_0`) | 2026-10-16 |
| T1 | editor, reviewer | Qwen3.8-27B (4-bit) | ok (fits 24 GB only at 4-bit) | `Qwen/Qwen3.8-27B` | `ggml-org/Qwen3.8-27B-GGUF` `Qwen3.8-27B-Q4_K_M.gguf` / `Qwen3.8-27B-Q8_0.gguf`; `unsloth/Qwen3.8-27B-GGUF` `Qwen3.8-27B-UD-Q4_K_XL.gguf` (17.56) | Q4_K_M / Q8_0 | 18.97 / 28.60 | 19.0 + KV | 262,144 (ext. 1,000,000) | yes (chat template) | Apache-2.0 | `qwen3.8:27b-q4_K_M` (18GB), `qwen3.8:27b-q8_0` (30GB) | 2026-10-16 |
| T1 | reviewer (other family) | Devstral Small 2 (24B) | ok (use full ID) | `mistralai/Devstral-Small-2-24B-Instruct-2512` | `unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF` `Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf` / `...-Q8_0.gguf` | Q4_K_M / Q8_0 | 14.33 / 25.06 | 14.3 + KV | 256k (card); 393,216 (config) | yes | Apache-2.0 | `devstral-small-2:24b-instruct-2512-q4_K_M` (15GB), `...-q8_0` (26GB) | 2026-10-16 |
| T1 | budget editor, MoE offload | small-active MoE models with expert offload | replaced (named) | `Qwen/Qwen3.6-35B-A3B` | `unsloth/Qwen3.6-35B-A3B-GGUF` `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` / `Qwen3.6-35B-A3B-Q8_0.gguf` | UD-Q4_K_M / Q8_0 | 22.13 / 36.90 | 22.1 split GPU/RAM with `--n-cpu-moe` | 262,144 (ext. 1,010,000) | yes | Apache-2.0 | `qwen3.6:35b-a3b-q4_K_M` (24GB), `qwen3.6:35b-a3b-q8_0` (39GB) | 2026-10-16 |
| T1 | MoE, other family | (added) | added | `openai/gpt-oss-20b` | `ggml-org/gpt-oss-20b-GGUF` `gpt-oss-20b-MXFP4.gguf` | MXFP4 | 12.11 | 12.1 + KV | 128K (Ollama page) | yes | Apache-2.0 | `gpt-oss:20b` | 2026-10-16 |
| T1 | MoE, other family | (added) | added | `google/gemma-4-26B-A4B-it` | `unsloth/gemma-4-26B-A4B-it-GGUF` `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` / `gemma-4-26B-A4B-it-Q8_0.gguf` | UD-Q4_K_M / Q8_0 | 16.95 / 26.86 | 17.0 + KV | 256K | yes | Apache-2.0 | `gemma4:26b-a4b-it-q4_K_M` (18GB) | 2026-10-16 |
| T2 | editor | Qwen3.8-Flash-Next (~111 GB Q4 GGUF) | ok (license flag) | `Qwen/Qwen3.8-Flash-Next` | `unsloth/Qwen3.8-Flash-Next-GGUF` `UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf` (4 files); Q8_0: `Q8_0/Qwen3.8-Flash-Next-Q8_0-00001-of-00006.gguf` | UD-Q4_K_XL / Q8_0 | 111.33 / 188.23 | 111.3 + KV | 262,144 (ext. 1,000,000) | yes (chat template) | Qwen Community License 1.0 (other) | none (`qwen3.8` page lists 27b tags only) | 2026-10-16 |
| T2 | reviewer | gpt-oss-120b | ok | `openai/gpt-oss-120b` | `ggml-org/gpt-oss-120b-GGUF` `gpt-oss-120b-MXFP4.gguf` | MXFP4 (native) | 63.39 | 63.4 + KV, or experts in RAM (`--cpu-moe`) | 131,072 | yes | Apache-2.0 | `gpt-oss:120b` (65GB) | 2026-10-16 |
| T2 | planner | GLM-5.3-Flash (tight at 4-bit) | ok (size note) | `zai-org/GLM-5.3-Flash` | `unsloth/GLM-5.3-Flash-GGUF` `UD-IQ4_XS/GLM-5.3-Flash-UD-IQ4_XS-00001-of-00005.gguf`; `UD-Q4_K_XL/GLM-5.3-Flash-UD-Q4_K_XL-00001-of-00006.gguf`; 2-bit `UD-Q2_K_XL/GLM-5.3-Flash-UD-Q2_K_XL-00001-of-00004.gguf` (108.72) | UD-IQ4_XS / UD-Q4_K_XL | 156.82 / 199.71 | 156.8 + KV; Q4_K_XL > 192 needs RAM offload | 1,048,576 (config) | yes (chat template) | MIT | `glm-5.3-flash:cloud` only (no local tag) | 2026-10-16 |
| T2 | reviewer, other family | (added) | added | `XiaomiMiMo/MiMo-V2.6-Flash-MOPD` | `ggml-org/MiMo-V2.6-Flash-MOPD-GGUF` `MiMo-V2.6-Flash-MOPD-MXFP4-00001-of-00002.gguf` | MXFP4 | 167.37 | 167.4 + KV, or experts in RAM | 1M (card) | yes | MIT | not checked | 2026-10-16 |
| T2 | reviewer, other family | (added) | added | `mistralai/Mistral-Small-4-119B-2603` | `unsloth/Mistral-Small-4-119B-2603-GGUF` `UD-Q4_K_XL/Mistral-Small-4-119B-2603-UD-Q4_K_XL-00001-of-00003.gguf` | UD-Q4_K_XL | 74.16 | 74.2 + KV | 256k (card) | yes (card tool example) | Apache-2.0 | not checked | 2026-10-16 |
| T3 | hardest edits | GLM-5.3 | ok | `zai-org/GLM-5.3` | `unsloth/GLM-5.3-GGUF` `UD-Q4_K_XL/GLM-5.3-UD-Q4_K_XL-00001-of-00011.gguf`; UD-Q2_K_XL 253.88 (7 files); Q8_0 801.36 | UD-Q4_K_XL | 467.29 | 467.3 + KV | 1,048,576 (config) | yes (chat template) | GLM-5.3 License (custom) | `glm-5.3:cloud` only | 2026-10-16 |
| T3 | greenfield, frontend | Kimi K3 | ok | `moonshotai/Kimi-K3` | `unsloth/Kimi-K3-GGUF` `UD-Q2_K_XL/Kimi-K3-UD-Q2_K_XL-00001-of-00019.gguf`; UD-Q4_K_XL 1,508.67 (32 files); UD-IQ1_S 594.04 (14 files) | UD-Q2_K_XL | 861.28 | 861.3 + KV | 1,048,576 | yes | Kimi K3 License (custom) | `kimi-k3:cloud` only | 2026-10-16 |
| T3 | planning, edits | MiMo-V2.6-Pro | renamed | `XiaomiMiMo/MiMo-V2.6-Pro-MOPD` (upgrade of `XiaomiMiMo/MiMo-V2.6-Pro-RL`) | `ggml-org/MiMo-V2.6-Pro-MOPD-GGUF` `MiMo-V2.6-Pro-MOPD-MXFP4-00001-of-00007.gguf`; also `ggml-org/MiMo-V2.6-Pro-RL-GGUF` (554.21) | MXFP4 | 554.21 | 554.2 + KV | 1M (card) | yes | MIT | none (library page 404) | 2026-10-16 |
| T3 | planning, edits | DeepSeek V4.1 Flash | ok on hub; no llama.cpp/Ollama-local path | `deepseek-ai/DeepSeek-V4.1-Flash` | none for llama.cpp (repos `unsloth/DeepSeek-V4.1-Flash-GGUF`, `ggml-org/DeepSeek-V4.1-Flash-GGUF` not found); `antirez/deepseek-v4.1-flash-gguf` `DeepSeek-V4.1-Flash-Q2.gguf` targets the DwarfStar engine only | n/a | 365.71 (that Q2 file) | n/a | up to 1M (card) | yes (Python encoder; no Jinja template shipped) | MIT | `deepseek-v4.1-flash:cloud` only | 2026-10-16 |

### Proposed T0 CPU set

| Role | Hub ID | GGUF repo + file | Quant | Size GB | RAM GB | Ollama tag | Why |
|---|---|---|---|---|---|---|---|
| Router / classifier (JSON-schema output) | `Qwen/Qwen3-4B-Instruct-2507` | `unsloth/Qwen3-4B-Instruct-2507-GGUF` / `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` | Q4_K_M | 2.50 | 3.71 at 8K ctx; 3.10 at 4K | `qwen3:4b-instruct-2507-q4_K_M` (2.5GB) | Non-thinking only (no think tokens). Card: MMLU-Pro 69.6, IFEval 83.4, BFCL-v3 61.9. Qwen3.5-2B card (non-thinking): MMLU-Pro 55.3, IFEval 61.2 vs 69.6 / 83.4 for this model. Schema-constrained JSON: llama.cpp `json_schema` (`/completion`) or `response_format` with schema (`/v1/chat/completions`), compiled to a grammar; Ollama `format` takes a JSON schema. |
| Summarizer | same as router (one llama.cpp process, separate slot) | same file | Q4_K_M | 0 (shared) | KV per extra slot only (1.21 per 8K) | same tag | 262,144 native ctx; no second model on disk. |
| Embeddings | `Qwen/Qwen3-Embedding-0.6B` | `Qwen/Qwen3-Embedding-0.6B-GGUF` / `Qwen3-Embedding-0.6B-Q8_0.gguf` | Q8_0 | 0.64 | 1.58 at 8K ctx; 0.87 at 2K | `qwen3-embedding:0.6b` (639MB) | GGUF from the model authors; 32K ctx; 1024 dims (MRL 32-1024). Cards: MTEB multilingual 64.33, MTEB eng v2 70.70, MTEB-Code 75.41. Served with llama.cpp `--embedding`. |
| FIM code model (optional) | `Qwen/Qwen2.5-Coder-1.5B` | `ggml-org/Qwen2.5-Coder-1.5B-Q8_0-GGUF` / `qwen2.5-coder-1.5b-q8_0.gguf` | Q8_0 | 1.65 | 1.88 at 8K ctx; 1.76 at 4K | `qwen2.5-coder:1.5b-base-q8_0` (1.6GB) | The exact repo/file behind llama.cpp `--fim-qwen-1.5b-default`; `/infill` endpoint; Ollama `suffix` parameter. |
| Reranker (optional, off by default) | `Qwen/Qwen3-Reranker-0.6B` | `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` / `qwen3-reranker-0.6b-q8_0.gguf` | Q8_0 | 0.64 | 0.64 + KV | none (Ollama API has no rerank endpoint) | Card: MTEB-R 65.80 vs 61.82 embedding-only, but MTEB-Code 73.42 vs 75.41 (no gain on code). llama.cpp `--rerank` (`/v1/rerank`). |

Total footprint (default set: router+summarizer, embedder, FIM): 2.50 + 0.64 + 1.65 = 4.78 GB on disk (8 GB cap, 3.22 GB spare); with optional reranker 5.42 GB; Ollama equivalent 2.5 + 0.639 + 1.6 = 4.74 GB. Resident RAM if all three run at 8K ctx: 3.71 + 1.58 + 1.88 = 7.17 GB (weights + f16 KV, excludes compute buffers and OS); at router 4K / embedder 2K / FIM 4K: 5.74 GB. On 8 GB machines start FIM lazily or use q8_0 KV.

### Workstation fits

| Role | Model | Quant/format | Engine | VRAM/RAM split | Family | Notes |
|---|---|---|---|---|---|---|
| Editor (default) | `Qwen/Qwen3.8-27B` | Q8_0 GGUF 28.60 GB (`ggml-org/Qwen3.8-27B-GGUF`) | Ollama `qwen3.8:27b-q8_0` (30GB) or llama.cpp `-sm none` | GPU0 only: 28.6 GB weights, ~19 GB left for KV; RAM 0 | Qwen (dense 27B; 16 x (3 Gated DeltaNet + 1 Gated Attention) layers) | Apache-2.0. Card: SWE-bench Pro 61.7, Terminal Bench 2.1 73.0 (Qwen3.6-27B: 53.5, 63.4). Same-family successor to the Qwen2.5-32B build now served. |
| Editor (large, optional) | `Qwen/Qwen3.8-Flash-Next` | UD-Q4_K_XL GGUF 111.33 GB (4 files) | llama.cpp server (GGUF README: "use llama.cpp"); no Ollama tag | GPU1-3 (144 GB): 111.3 GB weights, ~32.7 GB left for KV; RAM 0 | Qwen (MoE 125B, 6B active, + 51B n-gram embedding + 4B MTP) | Qwen Community License 1.0 (see corrections). Card: SWE-bench Pro 62.5 vs 61.7 for Qwen3.8-27B; DeepSWE 1.1 58.7 vs 42.2. Card says n-gram embeddings are "more amenable to offloading"; no llama.cpp flag for that verified. |
| Planner | `zai-org/GLM-5.3-Flash` | UD-Q4_K_XL GGUF 199.71 GB (6 files) or UD-IQ4_XS 156.82 GB (5 files) | llama.cpp (support merged 2026-09-30, PR #27773); Ollama: `:cloud` only | Q4_K_XL: GPU1-3 + `--n-cpu-moe N`, at least 55.7 GB of expert weights in RAM (199.71 - 144); IQ4_XS: GPU0-3, ~35 GB left for KV | GLM (MoE 320B, 18B active; sparse + linear attention) | MIT; 1,048,576 ctx (config). Open issue #29950: higher VRAM after a commit in #27773. |
| Reviewer | `openai/gpt-oss-120b` | MXFP4 GGUF 63.39 GB (`ggml-org/gpt-oss-120b-GGUF`) | Ollama `gpt-oss:120b` (already served, 65GB) or llama.cpp `--cpu-moe` | Ollama: tag size 65GB, held in VRAM. llama.cpp `--cpu-moe`: all expert weights in RAM, attention + KV on one GPU (frees VRAM for the planner) | gpt-oss (MoE 117B, 5.1B active) | Apache-2.0; 131,072 ctx; family differs from editor and planner. |
| Reviewer (alt family) | `XiaomiMiMo/MiMo-V2.6-Flash-MOPD` | MXFP4 GGUF 167.37 GB (2 files) | llama.cpp (MiMo-V2.6 support merged 2026-09-22, PR #29257) | `--cpu-moe`: experts in RAM, rest on GPU | MiMo (MoE 309B, 15B active) | MIT; 1M ctx. Card: DeepSWE v1.1 67.9, Terminal Bench 2.1 87.6. |
| Reviewer (alt family, Apache) | `mistralai/Mistral-Small-4-119B-2603` | UD-Q4_K_XL GGUF 74.16 GB (3 files) | llama.cpp | 2 GPUs (96 GB), or 1 GPU + `--n-cpu-moe` | Mistral (MoE 119B, 6.5B active) | Apache-2.0; 256k ctx; no coding benchmark verified today. |
| Slow lane (T3 on this box) | `XiaomiMiMo/MiMo-V2.6-Pro-MOPD` | MXFP4 GGUF 554.21 GB (7 files) | llama.cpp | `--cpu-moe`: experts in 1 TB RAM, attention/shared on GPUs | MiMo (MoE 1.02T, 42B active) | MIT. Card: DeepSWE v1.1 71.9, Terminal Bench 2.1 89.9. |
| Slow lane | `zai-org/GLM-5.3` | UD-Q4_K_XL GGUF 467.29 GB (11 files) | llama.cpp | experts in RAM, rest on GPUs | GLM (MoE; 753B stored params) | GLM-5.3 License. Card: DeepSWE v1.1 66.9, Terminal Bench 2.1 88.2. |
| Slow lane | `moonshotai/Kimi-K3` | UD-Q2_K_XL GGUF 861.28 GB (19 files) | llama.cpp (Kimi-K3 text support merged 2026-08-15, PR #26185) | ~860 GB in RAM + GPUs; UD-Q4_K_XL (1,508.67 GB) exceeds 1 TB RAM + 192 GB VRAM | Kimi (MoE 2.8T, 104B active) | Kimi K3 License. GGUF README: lossless = UD-Q8_K_XL (1,561.16 GB); 2-bit is below native precision. |
| Not deployable here | `deepseek-ai/DeepSeek-V4.1-Flash` | none for llama.cpp | llama.cpp PR #28696 open; Ollama `:cloud` only | n/a | DeepSeek | Only GGUF targets the DwarfStar engine. |

### Embedding and reranker candidates

| Model | Hub ID | Params | Size GB | Dims | Context | License | Notes |
|---|---|---|---|---|---|---|---|
| Qwen3-Embedding-0.6B (default) | `Qwen/Qwen3-Embedding-0.6B` | 595.8M | 0.64 (Q8_0, `Qwen/Qwen3-Embedding-0.6B-GGUF`) | 1024 (MRL 32-1024) | 32,768 | Apache-2.0 | Cards: MTEB multilingual 64.33, MTEB eng v2 70.70, MTEB-Code 75.41. Instruction-aware queries. Ollama `qwen3-embedding:0.6b`. |
| EmbeddingGemma 2 | `google/embeddinggemma-2` | 740M total (270M text backbone; vision 170M, audio 300M) | 0.31 (Q8_0, `unsloth/embeddinggemma-2-GGUF` `embeddinggemma-2-Q8_0.gguf`; mmproj separate) | 768 (MRL 512/256/128) | 8,192 | Apache-2.0 | Repo created 2026-09-14, not gated. Card: MTEB multilingual v2 61.36, MTEB code v1 78.68 (predecessor 61.15 / 68.76). No Ollama tag (404). llama.cpp embedding support not verified today. |
| embeddinggemma-300m | `google/embeddinggemma-300m` | 302.9M | 0.33 (Q8_0, `ggml-org/embeddinggemma-300M-GGUF`) | not verified | 2K (Ollama) | Gemma license (gated: manual) | Superseded by EmbeddingGemma 2 per its card. Ollama `embeddinggemma:300m-qat-q8_0` (338MB). |
| `nomic-embed-text` v1.5 | `nomic-ai/nomic-embed-text-v1.5` | 136.7M | 0.15 (Q8_0) / 0.08 (Q4_K_M), `nomic-ai/nomic-embed-text-v1.5-GGUF` | 768 (hidden_size) | 2,048 (config; `n_positions` 8,192) | Apache-2.0 | Ollama `nomic-embed-text` (274MB, 2K ctx). |
| bge-m3 | `BAAI/bge-m3` | ~567M (Ollama tag `567m`; hub count n/a) | 1.2 (Ollama F16) | 1024 (hidden_size) | 8,192 (config 8,194 positions) | MIT | Ollama `bge-m3` (1.2GB). |
| Arctic Embed L v2.0 | `Snowflake/snowflake-arctic-embed-l-v2.0` | 567.8M | 1.2 (Ollama `snowflake-arctic-embed2`) | 1024 (hidden_size) | 8,192 (config 8,194 positions) | Apache-2.0 | |
| Granite Embedding English R2 | `ibm-granite/granite-embedding-english-r2` | 149.0M | GGUF not checked | 768 (hidden_size) | 8,192 | Apache-2.0 | Ollama `granite-embedding` tags are older (30m/278m, 512 ctx). |
| Qwen3-Reranker-0.6B (reranker) | `Qwen/Qwen3-Reranker-0.6B` | 595.8M | 0.64 (Q8_0, `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF`) | n/a | 32K (card) | Apache-2.0 | Card: MTEB-R 65.80, MTEB-Code 73.42. llama.cpp `--rerank`. |
| bge-reranker-v2-m3 (reranker) | `BAAI/bge-reranker-v2-m3` | 567.8M | 0.64 (Q8_0) / 0.44 (Q4_K_M), `gpustack/bge-reranker-v2-m3-GGUF` | n/a | not verified | Apache-2.0 | Named in llama.cpp server README as reranker example. MTEB-Code 41.38 (Qwen3-Reranker card). |
| Granite Embedding Reranker English R2 (reranker) | `ibm-granite/granite-embedding-reranker-english-r2` | 149.6M | GGUF not checked | n/a | not verified | Apache-2.0 | |
| `mxbai-rerank-base-v2` (reranker) | `mixedbread-ai/mxbai-rerank-base-v2` | 494.0M | GGUF not checked | n/a | not verified | Apache-2.0 | |
| `jina-reranker-v2-base-multilingual` (reranker) | `jinaai/jina-reranker-v2-base-multilingual` | 278.4M | n/a | n/a | not verified | CC-BY-NC-4.0 | Non-commercial: exclude from defaults. MTEB-Code 58.98 (Qwen3-Reranker card). |

## Harnesses

| Harness | Version (date) | License | Runtime | Headless invocation | API formats spoken | Custom endpoint config | Model roles/slots | Extension API | ACP | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| Oh My Pi | 18.11.1 (2026-10-15) | MIT | TypeScript on Bun >= 1.3.14 plus Rust natives; prebuilt binary option | `omp -p "<task>"`; `omp -p --mode json` (session header + NDJSON events); `omp --mode rpc` (NDJSON commands incl. `set_model`) | chat-completions, responses, messages, generate-content (+ others) | `~/.omp/agent/models.yml`: `providers.<id>.baseUrl`, `api`, `apiKey`, `models[]`; per-run `--profile`, `--config <yaml>` | `modelRoles`: `default`, `smol`, `slow`, `vision`, `plan`, `commit`, `tiny`, `memory`, `task`, `advisor` (+ `image`, `web`, `speech`, `dictation`, `judge`); `--smol/--slow/--plan` | TS extension: `registerProvider`, `on("before_subagent_spawn")`, `on("before_provider_request")`, `setModel`, `ctx.models.resolve` | native (`omp acp`) | hashline edits are the default edit mode; `lsp` tool; `omp worktree`; fork of Pi |
| Pi | 1.3.0 (2026-10-15) | MIT | TypeScript on Node >= 22.19.0 | `pi -p "<task>"`; `pi --mode json` (session header + JSONL events); `pi --mode rpc` | chat-completions, responses, messages, generate-content (+ others) | `~/.pi/agent/models.json`: `providers.<id>.baseUrl`, `api`, `apiKey`, `models[]`; `PI_CODING_AGENT_DIR` | none built in; `--model provider/id[:thinking]`, `--models` cycle list; virtual models | TS extension: `registerProvider`, `registerVirtualModel({ route })`, `on(...)`, `registerTool` | third-party adapter only (`pi-acp`) | no LSP, subagents, plan mode or hash-anchored edits by default; repo and npm scope moved in 2026 |
| OpenCode | 1.19.2 (2026-10-16) | MIT | TypeScript; prebuilt executable via npm `opencode-ai` | `opencode run "<task>" --format json [-m provider/model] [--agent X] [--auto] [--dir D]`; `opencode serve` + `run --attach <url>` | chat-completions, responses, messages (generate-content not verified) | `provider.<id>.npm`, `options.baseURL`, `options.apiKey`, `options.headers`, `models`; inline `OPENCODE_CONFIG_CONTENT` | `model`, `small_model`, `agent.<name>.model` | JS/TS plugins: `tool.execute.before/after`, `shell.env`, `event`, `experimental.session.compacting` | native (`opencode acp`) | docs recommend WSL on Windows |
| oh-my-openagent | 5.1.33 (2026-10-13) | SUL-1.0 | TypeScript; OpenCode plugin edition or standalone `omo` (npm `omo-ai`, engines node >= 24) | `bunx oh-my-openagent run <message> --json [-a agent] [-m provider/model] [-d dir]` | plugin edition: whatever OpenCode speaks; Native custom providers: chat-completions, messages, responses | plugin: OpenCode `provider.*`; Native: `~/.omo/agent/models.json` | curated agents and task categories, each with an ordered model chain (`~/.omo/omo.jsonc`) | is itself an OpenCode plugin / an extension on its own engine | not documented | telemetry on by default; source-available license |
| Kimi Code | 2.2.0 (2026-10-14) | MIT | TypeScript; single-binary installer or npm (Node >= 22.19.0) | `kimi -p "<task>" --output-format stream-json [-m alias] [--agent X]` | chat-completions, responses, messages, generate-content (+ `kimi` type) | env `KIMI_MODEL_*`, or `KIMI_CODE_HOME` + `config.toml` `[providers.<n>] type`, `base_url` | `default_model` (main); `[secondary_model]` pool for subagents (`coder`, `explore`, `plan`) | command hooks (20 events), plugins, custom agents (Markdown) | native (`kimi acp`) | Python `kimi-cli` archived |
| Claude Code | 2.1.301 (2026-10-15) | Proprietary | prebuilt executable via npm (engines node >= 22.0.0) | `claude -p "<task>" --output-format stream-json --verbose [--input-format stream-json] [--model] [--allowedTools] [--permission-mode] [--max-turns]` | messages only (+ two cloud-provider variants of it) | `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY`; `ANTHROPIC_CUSTOM_HEADERS` | main; `opus`/`sonnet`/`haiku`/`fable` alias pins; small-fast = haiku pin; `CLAUDE_CODE_SUBAGENT_MODEL` | hooks (33 events, 5 handler types), plugins, skills, subagents, MCP | third-party adapter only | `/goal`, `--worktree`, routing hint headers |
| mini-swe-agent | 2.4.6 (2026-07-23) | MIT | Python >= 3.10 | `mini -t "<task>" -y --exit-immediately -o traj.json` | via litellm: chat-completions; responses via `litellm_response_toolcall` class | YAML `model.model_kwargs.api_base`; `MSWEA_MODEL_NAME` | single model | swap Python classes (`--agent-class`, `--model-class`, `--environment-class`); YAML config | no | bash is the only tool; native tool calling is the v2 default, text parsing optional |
| SWE-agent | 1.1.0 (2025-05-22) | MIT | Python >= 3.11 + Docker sandbox | `sweagent run --agent.model.name=... --env.repo.path=... --problem_statement.text=...` | via litellm | `agent.model.api_base` | single model | YAML configs, parse functions, history processors | no | maintainers recommend mini-swe-agent going forward |

## Multi-agent orchestration

"Model" means execution model.

| Project | URL | Version (date) | License | Model | Worktrees | Deterministic routes | Borrow | Avoid |
|---|---|---|---|---|---|---|---|---|
| open-multi-agent | https://github.com/open-multi-agent/open-multi-agent | 1.21.2 (2026-10-09) | MIT | TypeScript library. One coordinator LLM call turns a goal into a task DAG (`dependsOn`), a scheduler runs it, and a second call writes the answer (`runTeam`). `runTasks` takes an explicit DAG. | Not mentioned in README | Yes, after planning: "The coordinator is never consulted mid-run". `modelRouting.rules`: first match wins. | Task spec fields, readiness-driven dispatch, failure cascade limited to dependents, opt-in `maxRetries`, pre-dispatch gate, token budget checks, rule-based model routing with error-driven `fallback` | A TypeScript runtime dependency (Switchyard is Python), LLM-made plans that skip Switchyard gates, legacy `onApproval` batch semantics |
| Conductor | https://github.com/microsoft/conductor | 0.1.42 (2026-10-06) | MIT | Python CLI. A YAML workflow is a graph of agent, script, human-gate and sub-workflow steps. | Not mentioned in README | Yes: `routes: [{to, when}]`, Jinja2 or simpleeval expressions, first match wins, `$end`, no LLM in the routing loop | Route syntax, `workflow.limits`, script steps that route on `exit_code`, `for_each` with `max_concurrent`, human gates, run bundles | Taking it as a dependency (Python >=3.12, 21 runtime deps including a Copilot SDK), 0.1.x churn |
| Claude Squad | https://github.com/smtg-ai/claude-squad | 1.0.20 (2026-08-20) | AGPL-3.0-only | Go TUI. One tmux session plus one git worktree and branch per agent instance. A human dispatches the work. | Yes, created per session on demand | No (manual) | Worktree path and branch naming, base-commit record, pause/resume lifecycle, tmux session recovery, program profiles | Copying code (AGPL into MIT), tmux as a hard dependency, prompt auto-accept (`-y`, experimental) |
| Agent Client Protocol | https://agentclientprotocol.com | schema 1.25.0 (2026-10-09); `protocolVersion` 1 | Apache-2.0 | JSON-RPC 2.0 between client and agent over stdio. The client owns permissions, files and terminals. | The client sets an absolute `cwd` per session | N/A (the agent drives) | The stable method set, as the `workers/acp` adapter | The draft HTTP transport, v2 alpha features, SDK-only extras |

## Positioning check (SPEC §1)

| # | SPEC §1 item | Prior art found (project + URL) | How Switchyard differs | Verdict (new / partly new / exists) |
|---|---|---|---|---|
| 1 | Automatic step-level routing inside one task (classifier + learned policy) | vLLM Semantic Router: selects models "for each call" for agent harnesses; v0.3 (2026-06) adds session-aware per-turn routing (https://github.com/vllm-project/semantic-router, https://vllm-sr.ai/blog/session-aware-agentic-routing/). OpenHands SDK: rule-based router per LLM request, "under active development" (https://docs.openhands.dev/sdk/guides/llm-routing). claude-code-router 2.x: automatic per-request scenarios (background, think, longContext, webSearch, image) (https://raw.githubusercontent.com/musistudio/claude-code-router/v2.0.0/README.md) | Explicit step types inside our own agent; policy learned from the user's local test outcomes; every decision gated by verification | partly new |
| 2 | Routing over (harness, model) pairs | agent-router: CLI that "picks an AI coding agent, model, and reasoning effort" per task, with rule filters plus an external ranking API (https://github.com/nidhi-singh02/agent-router). Claw-Kanban: role-based auto-assignment of tasks to agent CLIs (https://github.com/GreenSheep01201/claw-kanban). Harbor evaluates arbitrary agents with any model offline (https://github.com/harbor-framework/harbor) | Learned from local pass/fail; can act per step through the proxy; no external ranking service | partly new |
| 3 | Universal compatibility, including models without native tool calling | LiteLLM: many providers plus prompt-injected function calling (https://docs.litellm.ai/docs/completion/function_call). llama.cpp server: "Generic" tool-call handler for unrecognized templates (https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md). any-llm: one interface over many providers (https://github.com/mozilla-ai/any-llm) | Capability profiles measured by probes, grammar-constrained JSON fallback, cross-codec loss reports | partly new |
| 4 | Cache-aware switching cost model | vLLM Semantic Router SAAR (2026-06-02): "Prices handoff cost, switch history, remaining-turn priors, and prefix-cache checkout" per turn, with hard locks during tool loops (https://vllm-sr.ai/blog/session-aware-agentic-routing/). A hosted, closed-source inference router applies a cache-aware switching budget (https://www.digitalocean.com/blog/inference-router-cache-aware) | Time-based cost from measured prefill throughput on local engines, a per-server prefix registry and a HANDOFF threshold; these are implementation details, not a new idea | exists |
| 5 | Best-of-N across model families, chosen by tests | Every Code: `/code` uses "Claude, Gemini and GPT-5 consensus" and "creates multiple worktrees then implements the optimal solution" (https://github.com/just-every/code). Agentless: re-ranks candidate patches with regression and reproduction tests, single model (https://github.com/OpenAutoCoder/Agentless) | Selection by the repo's own gates (tests, numerical checks) across families, accounted in the ledger | partly new |
| 6 | Cross-model disagreement as an escalation signal | Semantic-agreement cascades: ensemble agreement as the deferral signal (https://arxiv.org/abs/2509.21837). Agreement-gated cascade (https://arxiv.org/abs/2607.10139). Claude Octopus: cross-provider peer check (https://github.com/Alarukai/Agent_octopus) | Disagreement over code edits and test outcomes triggers escalation inside the router | partly new |
| 7 | Router that learns from the user's own pass/fail history | TensorZero: bandit-based adaptive A/B tests on user feedback metrics, repository archived 2026-06-12 (https://www.tensorzero.com/docs/experimentation/run-adaptive-ab-tests, https://github.com/tensorzero/tensorzero). Agent-as-a-Router (ACRouter): learns from execution feedback on coding tasks; paper only, code link returned 404 today (https://arxiv.org/abs/2606.22902). LLMRouter: 16+ trainable routers including personalized ones (https://github.com/ulab-uiuc/LLMRouter) | Online contextual Thompson sampling over workers at step level; reward from local gates; data stays local | partly new |
| 8 | Fork and replay of trajectories with a different worker | LangGraph time travel: "Replay" and "Fork" from prior checkpoints (https://docs.langchain.com/oss/python/langgraph/use-time-travel). "The Handoff Tax" (2026-08-25) studies continuing one model's trajectory with another (https://arxiv.org/abs/2608.24358) | First-class CLI fork of coding-agent runs across (harness, model) with recorded tool outputs | partly new |
| 9 | Open handoff format between models | ESAA-Conversational: `handoff.md`, `state.md`, `decisions.md` and `tasks.json` derived from an event log; paper plus CLI, no public spec link found (https://arxiv.org/abs/2606.23752). A2A: agent-to-agent task and message protocol, not a compact context brief (https://github.com/a2aproject/A2A) | Versioned, model-agnostic spec for compact context transfer, with conformance tests | partly new (no open spec of this kind found) |
| 10 | Built-in benchmark on the user's own tasks, with a leaderboard | Harbor: "Evaluate arbitrary agents… Build and share your own benchmarks" (https://github.com/harbor-framework/harbor). promptfoo: compares models side by side on user test cases (https://github.com/promptfoo/promptfoo) | Runs inside the orchestrator on the user's repo tasks; results seed router priors | partly new |
| 11 | Science track with tolerance-aware numerical verification | pytest-regressions: `num_regression` with tolerances (https://pytest-regressions.readthedocs.io/en/latest/api.html). SciCode: research-coding benchmark focused on numerical methods (https://github.com/scicode-bench/SciCode) | Numerical gates (golden data, tolerances) inside the agent's verification pipeline | partly new |
| 12 | Verifiable zero-egress mode with an audit log | AgentWall: local proxy around agents with an allowlist-only `strict` mode, audit trail, "Offline by default" (https://github.com/balyakin/agentwall). ccproxy: network interceptor for agent harnesses with TLS inspection (https://github.com/starbased-co/ccproxy) | Built into the orchestrator, audit tied to the ledger, proven by a claim test | partly new |
| 13 | CPU laptop to GPU cluster, plus a slow lane for very large models | AirLLM: "70B large language models run on a single 4GB GPU card" (https://github.com/lyogavin/airllm). llama.cpp: CPU-first serving | Tier-aware routing that includes an asynchronous slow lane | partly new (integration) |
| 14 | Claims registry and `sy prove` | Claimline: Markdown claims bound to code anchors and test-evidence globs, SARIF for CI, v0.1.0 (https://github.com/Alpoflex/claimline) | README shows only claims whose tests pass now; anyone can run the claims locally | partly new |

### Other routers and gateways

| Project | URL | License | One line |
|---|---|---|---|
| Bifrost | https://github.com/maximhq/bifrost | Apache-2.0 | Go gateway: one chat-completions-compatible API over 23+ providers, with failover and load balancing; HTTP v2.3.0 (2026-10-13). |
| AI Gateway | https://github.com/Portkey-AI/gateway | MIT | "Route to 250+ LLMs with 1 fast & friendly API"; last release v1.15.2 (2026-01-12), last commit 2026-05-25. |
| Agent Router (formerly Envoy AI Gateway) | https://github.com/envoyproxy/ai-gateway | Apache-2.0 | Envoy-based chat-completions-compatible API for models and MCP servers; v1.2.0 (2026-10-07). |
| agentgateway | https://github.com/agentgateway/agentgateway | Apache-2.0 | Proxy built on MCP and A2A, with an LLM gateway; v1.6.0 (2026-10-02). |
| any-llm | https://github.com/mozilla-ai/any-llm | Apache-2.0 | Python library with a single interface over many providers; 1.36.0 (2026-10-14). |
| Plano | https://github.com/katanemo/plano | Apache-2.0 | Proxy and data plane for agentic apps; routes "by model name, alias (semantic names) or automatically via preferences"; 0.4.37 (2026-09-28). |
| vLLM Semantic Router | https://github.com/vllm-project/semantic-router | Apache-2.0 | Per-call model selection for agent harnesses, session-aware routing, open "Decision" routing models; v0.4.0 (2026-09-27). |
| LLMRouter | https://github.com/ulab-uiuc/LLMRouter | MIT | Library and CLI for 16+ trainable routers (single-round, multi-round, multimodal, agentic, personalized); no tagged releases, last commit 2026-10-09. |
| TensorZero | https://github.com/tensorzero/tensorzero | Apache-2.0 | Gateway with feedback-driven adaptive A/B testing; repository archived 2026-06-12 (last release 2026.6.0). |
| agent-router | https://github.com/nidhi-singh02/agent-router | MIT | Picks the agent CLI, model and effort per task; needs an external ranking API; v0.1.0 (2026-09-17). |
| claude-code-proxy | https://github.com/1rgs/claude-code-proxy | none found (no LICENSE at repo root; verify) | Messages-format proxy that translates to chat-completions through LiteLLM; last commit 2026-06-23. |
| ccproxy | https://github.com/starbased-co/ccproxy | AGPL-3.0 or commercial | Network interceptor for LLM harnesses (mitmproxy plus WireGuard) that can re-route traffic between providers; v2.0.0-rc1 (2026-04-16). |
| llms | https://www.npmjs.com/package/@musistudio/llms | MIT | "A universal LLM API transformation server"; the transformer engine behind claude-code-router 1.x; 1.0.53 (2026-01-07). |

## Token counting

| Method | Package/endpoint | Version | Offline? | CPU cost | Notes |
|---|---|---|---|---|---|
| Local `tokenizer.json` | tokenizers `Tokenizer.from_file` | 0.23.3 (2026-10-09); 1.0.0rc2 pre-release (2026-09-21) | yes with `from_file`; `from_pretrained` fetches from the model hub | Rust; README: "less than 20 seconds to tokenize a GB of text on a server's CPU"; wheels 2.6-10.3 MB | exact for models that ship `tokenizer.json`; copy it into the registry at `sy models add`; abi3 wheels incl. win_arm64 and musllinux; Apache-2.0; depends on the `huggingface-hub` package |
| tiktoken encoding | tiktoken `get_encoding("o200k_harmony")`, `encoding_for_model("gpt-oss-...")` | 0.14.0 (2026-08-17) | only after the BPE file is cached: first use downloads `o200k_base.tiktoken` (sha256-checked) into `TIKTOKEN_CACHE_DIR`, else `DATA_GYM_CACHE_DIR`, else `<tmp>/data-gym-cache` | README: "3-6x faster than a comparable open source tokeniser"; wheels 0.9-1.3 MB | `o200k_harmony` = `o200k_base` ranks + harmony special tokens; 0.14.0 `model.py` maps prefix `gpt-oss-` to it; no win_arm64 wheel; MIT |
| GGUF vocab via server | llama.cpp server `POST /tokenize` (`add_special`, `parse_special`, `with_pieces`), `/detokenize`, `/apply-template` | server README on master (no release pinned) | yes (local HTTP) | one local HTTP call; reuses the loaded model's vocab | exact for that GGUF; needs the receiving model's server running; render the chat template via `/apply-template` before counting a full prompt |
| GGUF vocab in-process | llama-cpp-python `Llama(model_path, vocab_only=True).tokenize()` | 0.3.36 (2026-10-01) | yes | loads vocabulary only ("Only load the vocabulary no weights") | PyPI ships an sdist only (C compiler needed; prebuilt CPU wheels on a separate index); heavy for counting alone |
| GGUF metadata | gguf (gguf-py) | 0.19.0 (2026-05-06) | yes | metadata read only | exposes `tokenizer.ggml.*` keys but no tokenizer runtime; would need our own BPE/SentencePiece code; not recommended |
| Ollama | Ollama REST API; ollama client | client 0.6.3 (2026-09-29) | local server | only `prompt_eval_count` after a generate/chat call (runs prefill) | API docs list no tokenize endpoint; Messages-format layer lists `/v1/messages/count_tokens` as "Not supported"; `/api/show` `verbose=true` returns `tokenizer.ggml.tokens`/`merges`; resolve the model to a `tokenizer.json` or its GGUF instead |
| Flagged estimate | tiktoken `o200k_base` + margin | 0.14.0 | yes if cache pre-seeded | as tiktoken | only when no exact tokenizer exists (e.g. remote endpoints); mark `exact=false`; margin set from bench data |
| Recommended order (HANDOFF, TK12) | 1) registry `tokenizer.json` 2) llama.cpp `/tokenize` 3) mapped tiktoken encoding with `TIKTOKEN_CACHE_DIR` under `.switchyard/` 4) flagged estimate | - | yes after install-time prefetch | per call small next to any LLM call (see tokenizers and tiktoken README claims above); one-time tokenizer load; not measured by us | count the rendered brief as sent; record method and `exact` in the ledger; never download at runtime unless configured |

## Network isolation

Canary used in "Verifies (how)": (1) TCP connect to a public IP literal must fail, (2) lookup of a public hostname must fail, (3) connect to a listener on 127.0.0.1 must succeed. "Inferred" means it follows from verified facts but was not run or documented for that runner.

| OS | Mechanism | Works on hosted CI runner? | Privileges needed | Blocks DNS? | Verifies (how) | Notes |
|---|---|---|---|---|---|---|
| Linux | Network namespace as root: `sudo unshare --net -- <cmd>` (util-linux `-n, --net`), then drop back to the job user | Yes on `ubuntu-24.04` / `ubuntu-26.04` VMs (inferred: passwordless sudo is documented). No on `ubuntu-slim` (runs in an unprivileged container) | root via sudo | Yes (inferred: the namespace has no route to any resolver) | Canary inside the namespace | Loopback state inside a fresh namespace not verified; bring `lo` up and check before the canary |
| Linux | Unprivileged namespace: `unshare -r -n` (`--map-root-user`, `--net`) | No by default on `ubuntu-24.04`: hosted-runner issue reports `unshare(...): Operation not permitted`. Yes after `echo 0 \| sudo tee /proc/sys/kernel/apparmor_restrict_unprivileged_userns` (third-party report on image 20260920.314.1, for bubblewrap) | sudo once for the sysctl, then none | Yes (inferred) | Canary | The sysctl turns off the AppArmor restriction on unprivileged user namespaces for the whole VM. The Ubuntu 24.04 release notes document it and warn that turning it off loses the mitigation against kernel exploits that abuse unprivileged user namespaces |
| Linux | Docker `--network none` | Yes on `ubuntu-24.04` (Docker Server 28.0.4) and `ubuntu-26.04` (Docker Server 29.4.2). No on `ubuntu-slim` (no Docker server listed; Docker-in-Docker not supported) | Docker daemon access (not verified for the job user; sudo is available) | Yes: "only the loopback device is created" | `docker run --rm --network none alpine:latest ip link show` lists only `lo`; then the canary | No IPv6 loopback in `none` containers, so local endpoints must use 127.0.0.1, not `::1`. The same container can carry the C-001 memory cap |
| Linux | bubblewrap `--unshare-net` | Only after the sysctl above: no bwrap AppArmor profile on the `ubuntu-24.04` image (third-party report). Not listed in the image readme, so it must be installed (Ubuntu package 0.9.0-1ubuntu0.3 was reported) | none after the sysctl | Yes (inferred) | Canary | Upstream 0.13.1. Setuid builds were removed in 0.12.0 |
| All | Python audit-hook recorder (`sys.addaudithook`) on the `socket.*` events | Yes on all runners (pure Python) | none | Partly: it sees `socket.getaddrinfo`, `socket.gethostbyname`, `socket.gethostbyaddr` and `socket.getnameinfo` calls made from Python. It does not see the resolver's own packets or lookups from native code | Hook log of `socket.connect` / `socket.sendto` / `socket.sendmsg` addresses compared with the `sy audit` log | The Python docs say hooks "are not suitable for implementing a 'sandbox'". They do not cover child processes or native code that bypasses the socket module. A new hook is not added if an existing hook raises `RuntimeError` |
| All | pytest-socket 0.8.1: `--disable-socket`, `--allow-hosts`, `--allow-unix-socket` | Yes on all runners | none | Yes for Python's socket interface ("including DNS resolution") | Blocked calls raise `SocketBlockedError` and emit `SocketBlockedWarning` | In-process only (inferred). FAQ: sockets that a higher-scoped fixture creates before function-scoped blocking stay unblocked |
| macOS | `sandbox-exec` (Seatbelt) profile that denies `network-outbound` except loopback | Not verified on hosted images (macOS 15.7.9, 26.6.2) | none | Not verified (name resolution goes through a system daemon) | Canary under the profile | The man page (macOS mirror) marks it "DEPRECATED" and points to App Sandbox |
| macOS | pf rule scoped to a dedicated test user (`user` option, `set skip on lo0`, own anchor), loaded with `pfctl` | Not verified on hosted VMs; passwordless sudo is documented | sudo, plus a separate user account | Not verified. Lookups made by the system resolver under its own user would not match a user-scoped rule | Canary run as the test user | Per pf.conf (macOS 26.6), `user` applies to TCP and UDP only. Blocking the job user would also cut the runner's own connection |
| macOS | Containers / VMs | No: there is no Docker in the macOS images, and arm64 runners do not support nested virtualization | n/a | n/a | n/a | So C-001 cannot use containers on macOS |
| Windows | Per-program outbound block: `New-NetFirewallRule -Direction Outbound -Program <exe> -Action Block -RemoteAddress 0.0.0.0-126.255.255.255,128.0.0.0-255.255.255.255,<IPv6 ranges excluding ::1>` | Likely (inferred): the job runs as administrator with UAC disabled, and the image build scripts checked contain no firewall changes. Not run | Administrator | Not verified. Lookups are expected to come from the system DNS client service, not the blocked program | Canary from the blocked executable | A rule matches one executable path, so every child executable (engine servers, worker CLIs, the interpreter behind a venv launcher) needs its own rule. Check the firewall profile state in the job before relying on it |
| Windows | Windows Sandbox `.wsb` with `<Networking>Disable</Networking>` | No on `windows-2022` / `windows-2025` (inferred: these are Windows Server, and the docs list Windows 10 1903+ / Windows 11). `windows-11-arm` needs nested virtualization, not verified | Administrator, to enable `Containers-DisposableClientVM` | Yes (no network in the sandbox) | `<LogonCommand>` runs the flow and writes evidence to a `<MappedFolder>` | `<MemoryInMB>` could cap memory on client machines |
| Windows | Windows containers with `--network none` | Docker 29.7.2 is on `windows-2022` / `windows-2025` (all images cached on `windows-2022` are ltsc2022 Server Core / Nano Server based). `windows-11-arm` lists no Docker. The `none` driver is not in the Windows container network-driver docs, so not verified | Administrator | Not verified | Canary | Linux containers are not available on hosted Windows runners: Docker container actions, job containers and service containers need a Linux runner |
| Windows | AppContainer with no network capabilities | Not verified (no stock CLI launcher identified) | none to run; needs launcher code | Not verified | Canary | Docs: network access is granted for Internet, Intranet and acting as a server. Loopback behaviour not verified, so local endpoints may break |

## Name check

| Name | Package index | npm | Code host (notable repos) | CLI collision | Verdict |
|---|---|---|---|---|---|
| Switchyard (CLI `sy`) | Taken: `switchyard` 1.0.1 (2020-07-03), "framework for creating networked systems", GPLv3 classifier. `Switchyard` resolves to the same project. `switch-yard`, `switch_yard`, `switchyard-ai`: free. | Taken: `switchyard` 0.1.0 (2016-03-26, Express routing library). `switchyard-ai` 1.0.0 (2026-09-25): "Routing, Cost & Safety Layer for AI Coding Agents", installs bins `switchyard` and `sy`, 204 downloads in the last month. | 397 repos with the name. Same problem domain: [LLM model-routing library, Rust, Apache-2.0, 3,386 stars, created 2026-05-19, pushed 2026-10-16](https://github.com/NVIDIA-NeMo/Switchyard), plus at least 6 small 2026 repos with the name that do LLM or coding-agent model routing. Others: [browser gateway, 114 stars, active](https://github.com/alyraffauf/switchyard); [Rust async executor, 74 stars](https://github.com/BVE-Reborn/switchyard) (also crates.io `switchyard` 0.3.1); [SwitchYard Java integration framework, archived](https://github.com/jboss-switchyard/core); [the networking framework that owns the PyPI name, 41 stars, last push 2024-02-20](https://github.com/jsommers/switchyard). | `sy`: no executable in Debian stable (trixie) or Ubuntu noble, where only keyboard-layout data files are named `sy`. No Homebrew formula or cask. Not in the Windows command reference and not on PATH on this Windows 11 machine. Binaries named `sy` come from crates.io `sy` 0.4.0 (file sync tool, 2,794 downloads) and npm `switchyard-ai`. macOS built-ins: not verified. | **Unsafe.** The PyPI name is unavailable, and an active, well-known LLM routing library uses the same name. |
| Stepswitch (CLI `ssw`, long form `stepswitch`) | Free: `stepswitch`, `step-switch` | Free: `stepswitch`. npm `ssw` 0.0.3 (2019) is an Angular library with no bin. | 4 repos, all 0 stars: a telephony-platform module named stepswitch ([example](https://github.com/2600hz/kazoo-stepswitch)) and one personal repo. A web search found no software product with this name. | `ssw` and `stepswitch`: no file in Debian stable, no Homebrew formula or cask, not in the Windows command reference, not on the local PATH. crates.io `ssw` 0.1.1 has no binaries. PyPI `ssw` exists; its console scripts were not checked. | **Safe. Recommended.** The name describes the headline feature (step-level switching). |
| Shuntyard (CLI `sy` unchanged) | Free: `shuntyard`, `shunt-yard` | Free | 11 repos, all 0 stars. The 4 inspected are shunting-yard algorithm exercises ([example](https://github.com/gearsix/shuntyard)) or have no description. A web search found no product. | `shuntyard`: none (Debian, Homebrew, crates.io). `sy`: same findings as the Switchyard row. | **Safe on the registries. Runner-up.** Keeps every `sy` command in the SPEC. Risks: search noise from the algorithm name, and `sy` stays visually tied to the Switchyard brand. |
| Signalyard (CLI `sy` unchanged) | Free: `signalyard`, `signal-yard` | Free | 5 repos, 0 to 1 stars. The 4 inspected were created in 2026: IoT monitoring, security-operations tooling, and log aggregation with an MCP endpoint. A web search found no product. | `signalyard`: none (Debian, Homebrew, crates.io). `sy`: as above. | **Usable.** Weaker than the other two because the name suggests observability tooling, a space where 2026 repos already use it. |

Also checked and rejected: `stepyard` (PyPI, taken 2026-06-14), `yardmaster` (PyPI, taken 2026-05-21), `railyard` (PyPI and npm, taken), `turnout` (npm, taken), `modelyard` (code-host repos for an AI gateway and an AI-usage app). `sortyard` and `routeyard` are free on both registries, but each has one unexplained 2026 repo.

Recommendation: rename to **Stepswitch** (distribution and import name `stepswitch`, CLI `ssw` with `stepswitch` as an alias). If keeping `sy` matters more, use **Shuntyard**. Record the decision in the §0 name ADR and register the PyPI and npm names before announcing.

## Stack

| Component | Version (date) | License | Min runtime | Recommendation | Notes |
|---|---|---|---|---|---|
| Python | 3.15.0 (2026-10-09) newest; 3.14.8 (2026-09-30); 3.11.17 (2026-10-01) | not checked | n/a | `requires-python = ">=3.11"`; CI on 3.11 through 3.15; point users to uv-managed Python | 3.10 reached EOL on 2026-10-01. 3.11 is security-only until 2027-10. Security branches (3.11 to 3.13) get source-only releases. uv 0.13 downloads 3.15 by default. |
| uv | 0.13.1 (2026-10-14) | MIT OR Apache-2.0 | wheel declares Python >=3.8 | Contributors use uv >=0.13; users install with `uv tool install` | This workstation has 0.12.23. 0.13.0 (2026-10-09) changed the default downloaded Python to 3.15. |
| uv_build (backend) | 0.13.1 (2026-10-14) | MIT OR Apache-2.0 | >=3.8 | `uv_build>=0.13.1,<0.14`, provided the dashboard is prebuilt into the package directory | Pure Python only and no build scripts. Wheel data must sit under the module root or in data directories. |
| hatchling (backend) | 1.32.4 (2026-09-20) | MIT | >=3.10 (build only) | Use only if `uv build` must run the Vite build itself | The uv docs point to hatchling when build scripts are needed. |
| Typer | 0.27.3 (2026-10-06) | MIT | >=3.10 | `typer>=0.26` | Click has been vendored since 0.26.0 (2026-05-26), and Click-specific features are unsupported. `typer-slim` is discontinued. Beta classifier. |
| FastAPI | 0.143.2 (2026-10-15) | MIT | >=3.10 | `fastapi>=0.135` | Built-in SSE since 0.135.0 (2026-03-01). Pulls Starlette, Pydantic and opentelemetry-api. |
| Starlette (transitive) | 1.8.0 (2026-10-13) | BSD-3-Clause | >=3.11 | no direct pin | 1.x needs 3.11 or newer, which matches the floor. |
| uvicorn | 0.54.0 (2026-09-25) | BSD-3-Clause | >=3.10 | `uvicorn>=0.54` without `[standard]` | `[standard]` pulls PyYAML (no 3.15 wheels), httptools, uvloop, watchfiles and websockets. |
| SSE | FastAPI `fastapi.sse` (built in); sse-starlette 3.5.0 (2026-09-28) | sse-starlette: BSD-3-Clause | >=3.10 | Use the built-in `EventSourceResponse` and drop sse-starlette | Built-in support sends a keep-alive ping every 15 s, sets `Cache-Control: no-cache` and `X-Accel-Buffering: no`, and supports `Last-Event-ID`. |
| Pydantic | 2.14.1 (2026-10-11) | MIT | >=3.10 | `pydantic>=2.14,<3` | pydantic-core 2.50.1 ships cp315 wheels, Windows included. There is no 3.x release. |
| httpx | 0.28.1 (2024-12-06); 1.0.dev6 (2026-08-31) | BSD-3-Clause | >=3.8 (1.0.dev: >=3.10) | `httpx>=0.28.1,<1`, called only through one internal client module | No stable release in 22 months. 1.0.dev is a redesigned client and server toolkit. Only collaborators can open issues. Alternatives: aiohttp 3.14.5 (2026-10-11), niquests 3.21.2 (2026-09-23). |
| SQLite (WAL) | 3.54.0 (2026-10-09) | Public domain | ships inside Python's `sqlite3` | WAL on local disks only; require `sqlite3.sqlite_version` >=3.51.3 (or the 3.50.7 / 3.44.6 backports) | The WAL-reset corruption bug affects 3.7.0 through 3.51.2. This workstation's Pythons: 3.11.1 has 3.39.4 and 3.12.1 has 3.43.1, both affected. The uv-managed Python build source pins 3.53.1. |
| Node.js | 24 LTS (maintenance from 2026-10-20, EOL 2028-04-30); 22 (EOL 2027-04-30); 20 (EOL 2026-04-30) | not checked | n/a | `"engines": {"node": ">=22.12"}`; CI and dev on Node 24 | This workstation's Node 20.12.1 is EOL and below Vite's range. Node 26 becomes LTS on 2026-10-28. |
| React | 19.3.1 (2026-10-14) | MIT | engines: node >=0.10.0 | `react@^19.3` | 19.3.0 was released 2026-09-09. |
| Vite | 8.3.4 (2026-10-08) | MIT | Node ^20.19.0 or >=22.12.0 | `vite@^8.3`, `@vitejs/plugin-react@^6.1` (6.1.2, 2026-10-05) | 8.0.0 was released 2026-03-12. plugin-react 6 requires Vite ^8.0.0. |
| TypeScript | 7.0.2 (2026-07-08); 6.0.3 (2026-04-16) | Apache-2.0 | Node >=16.20.0 | `typescript@^7.0` for `tsc --noEmit`; add `@typescript/typescript6` (6.0.2) only for tools that need the compiler API | 7.0 is the native compiler port written in Go, shipped as per-platform binaries. It has no programmatic API; one is planned for 7.1. |
| ruff | 0.17.1 (2026-10-15) | MIT | >=3.7 | `ruff>=0.17` (dev group) | Production/Stable classifier, 0.x versions |
| mypy | 2.4.0 (2026-10-01) | MIT | >=3.10 | `mypy>=2.4` as the blocking type checker | 2.0.0 was released 2026-05-06. |
| ty | 0.0.88 (2026-10-15) | MIT | >=3.8 | Optional, non-blocking CI job only | The README says beta: 0.0.x versions, no stable API, and breaking changes possible between any two versions. Not production-ready as the only gate. |
| pytest | 9.1.1 (2026-06-19) | MIT | >=3.10 | `pytest>=9.1` | Classifier: 6 - Mature |
| PyYAML | 6.0.3 (2025-09-25) | MIT | >=3.8 | Not for claims.yaml | A local round trip dropped all comments. No cp315 wheels, so it builds from the sdist on 3.15. |
| ruamel.yaml | 0.19.1 (2026-01-02) | MIT | >=3.9 | `ruamel.yaml>=0.19.1` for claims.yaml | Preserves comments in round-trip mode. Pure-Python wheel. Beta classifier. The maintainer warns that future PyPI uploads may become impossible (PEP 625 naming). |
| jsonschema | 4.26.0 (2026-01-07) | MIT | >=3.10 | `jsonschema>=4.26` | Full Draft 2020-12 support. Depends on rpds-py, whose 2026.9.1 release has cp315 wheels. |
| mkdocs-material | 9.7.7 (2026-07-17) | MIT | >=3.8 | `mkdocs-material>=9.7.5` (which caps mkdocs below 2) for now; decide Zensical migration in an ADR | Maintenance mode since 2025-11-11, with critical fixes promised for at least 12 months. Incompatible with MkDocs 2.0. |
| Zensical | 0.0.70 (2026-10-12) | MIT | >=3.11 | Candidate successor docs tool | Alpha classifier. Reads `mkdocs.yml`; plugin coverage is partial. |

## Details

### Big models on small hardware

#### AirLLM
- verified: yes (2026-10-16), basis: PyPI JSON for `airllm` and `transformers`, project README and LICENSE, release and commit feeds on the code host. Throughput: verified: no (the project publishes no tokens/s figures). Windows: verified: no (not documented).
- link: https://github.com/lyogavin/airllm
- license: Apache-2.0 (LICENSE file; PyPI metadata has no license field)
- version: 4.0.0 (released 2026-09-05). Earlier: 3.0.0 (2026-06-30), 3.1.0 (2026-07-29), 3.2.0 (2026-08-18), 3.3.0 (2026-08-28). Commits after 2026-09-05 (daily, latest 2026-10-15) are automated "chore: refresh star history chart". Status: maintained, single-project cadence of roughly one release per month.
- platforms: Windows not documented (pure-Python wheel `airllm-4.0.0-py3-none-any.whl` on top of torch; untested), macOS ARM only (needs `mlx` + torch), Linux yes (CUDA).
- what we use:
  - Python API only, no HTTP server: `from airllm import AutoModel`; `AutoModel.from_pretrained(<hub id or local path>, compression='4bit'|'8bit', layer_shards_saving_path=..., delete_original=..., prefetching=..., profiling_mode=True, hf_token=...)`; `model.tokenizer`; `model.generate(input_ids, max_new_tokens=..., use_cache=True, return_dict_in_generate=True)`.
  - Dependencies (requires_dist): `torch>=2.4`, `transformers<6,>=4.49`, `accelerate>=1.0`, `safetensors`, the model-hub client package, `scipy`, `sentencepiece`; `compression` needs `bitsandbytes`. Current `transformers` is 5.20.0 (2026-10-15), inside the pin.
  - Per-model pins from the README: Kimi K3 needs `transformers` 4.56.x (its remote code does not load on 5.x), `compressed-tensors`, `flash-attn` and a CUDA 12 torch build; Qwen3.8-27B needs `transformers` 5.8+; Qwen3.8-Flash-Next needs a source build of `transformers` with in-tree `qwen4_exp` (README note dated 2026/08) and about 360 GB of checkpoint disk.
  - Supported architectures (README): Llama 2/3/3.1/3.3/4; Qwen 1/2/2.5/3/3.5/3.8 incl. MoE, Flash-Next, FP8, native VL; DeepSeek V2/V3/R1; Mistral/Mixtral; Phi; Gemma; ChatGLM; Baichuan; InternLM; Yi; Kimi K3.
  - README VRAM figures: Qwen3-235B ~3 GB, Llama 3.x 70B ~4 GB, Llama 3.1 405B ~8 GB, DeepSeek-V3 671B ~12 GB, Kimi K3 3.72 GB (measured), Qwen3.8-27B 3.33 GB (measured on RTX 3090, sm_86), Qwen3.8-Flash-Next 5.95 GB (plus host RAM for a file-mapped n-gram table; "a 64GB machine is enough").
  - Wrapper we need (core is HTTP-only): a separate process in its own venv (optional extra, never imported by the core) exposing `POST /v1/chat/completions`, `GET /v1/models`, `GET /health`; applies the tokenizer chat template; single-flight queue (one generation at a time); reports `usage` from token counts; no native tool parsing (use the §4.4 fallback protocol with validate-and-repair); streaming optional.
- what we borrow: layer sharding to disk with prefetch overlap; per-expert streaming that loads only routed experts; weight-only block-wise compression ("the bottleneck is mainly at the disk loading", README).
- what we avoid: in-process import (pulls torch into the core); defaulting `delete_original=True` (deletes the original download, breaks hash-pinned registry entries); sharing one venv across model families with conflicting `transformers` pins.
- risks: throughput unpublished; derived estimate (not measured): every forward pass streams all layer weights (or routed experts) from disk, so per-token decode time is roughly streamed bytes divided by disk read bandwidth; suitable only for asynchronous jobs, measure with `sy calibrate`. Layer splitting roughly doubles disk use unless originals are deleted. Per-model dependency pins. No server, tool calling, constrained decoding or prefix cache.
- sources: https://pypi.org/pypi/airllm/json, https://pypi.org/pypi/airllm/4.0.0/json, https://pypi.org/pypi/transformers/json, https://raw.githubusercontent.com/lyogavin/airllm/main/README.md, https://raw.githubusercontent.com/lyogavin/airllm/main/LICENSE, https://github.com/lyogavin/airllm/releases.atom, https://github.com/lyogavin/airllm/commits/main.atom

#### KTransformers
- verified: yes (2026-10-16), basis: PyPI JSON for `ktransformers`, `kt-kernel`, `sglang-kt`; project README, kt-kernel README, Native-Precision tutorial, KT-CLI doc; v0.7.2 release page. WSL2 support and AMX visibility inside WSL2: verified: no (not documented).
- link: https://github.com/kvcache-ai/ktransformers
- license: Apache-2.0 (`ktransformers` and `kt-kernel` license_expression; `sglang-kt`: "Apache License")
- version: 0.7.2 (released 2026-10-15: PyPI upload 04:13Z, code-host release 07:09Z). Components: `kt-kernel` 0.7.2 (2026-10-14), `sglang-kt` 0.7.2 (2026-10-15). 0.7.2 headline is LoRA fine-tuning for DeepSeek-V4-Flash with MXFP4 routed experts on CPU.
- platforms: Windows no native wheel (all 0.7.2 wheels are `manylinux_2_35_x86_64`; PyPI classifier "Operating System :: POSIX :: Linux"; WSL2 undocumented), macOS no, Linux x86-64 yes (`kt-kernel` wheels for CPython 3.11/3.12; `ktransformers` requires Python >= 3.11). The kt-kernel README still says "manylinux_2_17 compatible", which does not match the 0.7.2 wheel tags (glibc >= 2.35 implied).
- what we use:
  - Install: `pip install kt-kernel sglang-kt` (the project's SGLang fork; README: uninstall upstream `sglang` first).
  - Serve: `python -m sglang.launch_server --model <path> --kt-method <AMXINT4|AMXINT8|LLAMAFILE|FP8|FP8_PERCHANNEL|BF16|RAWINT4> --kt-weight-path <cpu weights> --kt-cpuinfer <physical cores> --kt-threadpool-count <NUMA nodes> --kt-num-gpu-experts <N> --kt-max-deferred-experts-per-token <N> --kt-gpu-prefill-token-threshold <N> --tool-call-parser <name> --reasoning-parser <name> --served-model-name <name> --port 30000`; API: chat-completions at `http://localhost:30000/v1` (tutorial example, e.g. `--tool-call-parser glm47 --reasoning-parser glm45`).
  - CLI: `kt run <model>`, `kt chat`, `kt doctor`, `kt version` (KT-CLI doc: "under active development").
  - Weights: AMX backends need a conversion step (FP8/FP16/BF16 -> INT4/INT8); LLAMAFILE backend reads GGUF (e.g. Q4_K_M); native-precision backends read FP8/BF16/RAWINT4 checkpoints directly.
  - Supported MoE families (docs and tutorials): DeepSeek-V3/R1/V3.2, DeepSeek-V4-Flash; Kimi-K2, K2-Thinking, K2.5 (K2.6 for LoRA); GLM-4.7, GLM-5, GLM-5.1, GLM-5.2, GLM-5.3-Flash; MiniMax-M2, M2.1, M2.5, M3; Qwen3-30B-A3B, Qwen3-235B-A22B, Qwen3-Next-80B-A3B, Qwen3-Coder-Next.
  - Hardware: CPU AVX2 minimum (LLAMAFILE); AVX512 for FP8/BF16/RAWINT4 native precision; AMX (Sapphire Rapids and later) for AMXINT4/AMXINT8, and BF16 "additionally benefits from AMX"; runtime dispatch across 6 CPU variants. GPU: wheel built for SM 80/86/89/90 (Turing/Volta unsupported), CUDA driver 11.8+/12.x. Native precision minimum: 1-2 GPUs with >= 24 GB VRAM; RAM at least model size (e.g. 256 GB+ for MiniMax-M2.1); Linux (Ubuntu 20.04+ recommended).
  - Our workstation: w5-3435X is AMX-class per our hardware notes (`sy doctor` must confirm the AMX CPU flags); 4x A6000 (sm_86, 48 GB) meets the >= 24 GB rule; 1 TB RAM fits T3 expert weights. Blocker: Windows 11 host, Linux-only wheels.
  - Fit for SL (§5): good on a Linux host (T3 MoE with experts in RAM, attention and hot experts on GPU, served over chat-completions); on the Windows workstation it needs WSL2 or a Linux boot (both unverified for AMX/GPU passthrough performance). Published example: DeepSeek-R1-0528 (FP8) on 8x L20 + Xeon Gold 6454S, 227.85 tokens/s total, 87.58 tokens/s output at 8-way concurrency (not comparable to our box).
- what we borrow: hot/cold expert placement (`--kt-num-gpu-experts`), deferred-expert pipelining, NUMA-aware thread pools (`--kt-threadpool-count`), runtime CPU-variant dispatch and `kt doctor` checks as input to `sy doctor`.
- what we avoid: installing `sglang-kt` next to upstream `sglang` (separate venv or container); AMXINT4 for accuracy-sensitive roles without evaluation (README: "May cause huge accuracy drop for some models, e.g., Qwen3-30B-A3B"); the legacy pre-kt-kernel server path.
- risks: fork drift from upstream SGLang; Linux x86-64 only; fast release cadence (0.7.0 2026-08-18, four post releases, 0.7.2 2026-10-15); install docs lag wheel tags; constrained decoding, prefix-cache metrics, speculative decoding and KV quantization in the fork: verified: no.
- sources: https://pypi.org/pypi/ktransformers/json, https://pypi.org/pypi/kt-kernel/json, https://pypi.org/pypi/sglang-kt/json, https://raw.githubusercontent.com/kvcache-ai/ktransformers/main/README.md, https://raw.githubusercontent.com/kvcache-ai/ktransformers/main/kt-kernel/README.md, https://raw.githubusercontent.com/kvcache-ai/ktransformers/main/doc/en/kt-kernel/Native-Precision-Tutorial.md, https://raw.githubusercontent.com/kvcache-ai/ktransformers/main/doc/en/kt-kernel/kt-cli.md, https://github.com/kvcache-ai/ktransformers/tree/main/doc/en/kt-kernel, https://github.com/kvcache-ai/ktransformers/releases/tag/v0.7.2

#### llama.cpp
- verified: yes (2026-10-16), basis: releases API and release pages, tag-pinned `tools/server/README.md`, `tools/server/server.cpp`, `common/chat.cpp`, `common/chat.h`, `common/chat-auto-parser.h`, `common/parsers/`, CUDA sources and CMake, release workflow, LICENSE, security advisories.
- link: https://github.com/ggml-org/llama.cpp
- license: MIT
- version: v0.7.0 (released 2026-10-15); its `nightly-tag.txt` asset maps it to build tag b11669 (2026-10-15). Binaries ship on the bNNNNN build tags; newest build b11675 (prerelease, 2026-10-16). The project now has semantic versions (v0.x) next to daily build tags; record both in probe exports.
- platforms: Windows native (prebuilt x64: CPU, CUDA 12.4, CUDA 13.4, Vulkan, SYCL, ROCm 10.0, OpenVINO; arm64: CPU, CUDA 13.4, Vulkan, OpenCL-Adreno), macOS native (arm64 with embedded Metal library; x64 CPU-only; deployment target 13.3), Linux native (Ubuntu x64: CPU, Vulkan, CUDA 12.8, CUDA 13.4, ROCm 10.0, SYCL fp16/fp32, OpenVINO; arm64: CPU, Vulkan, CUDA 13.4; s390x).
- what we use (v0.7.0, `llama-server`):
  - Breaking defaults: `--port` default 9931 (was 8080; container images keep 8080 inside); `--host` default 127.0.0.1; `--jinja` default enabled. Always pass `--host`/`--port` explicitly.
  - chat-completions: `POST /v1/chat/completions` (alias `/chat/completions`), `POST /v1/chat/completions/input_tokens`, `POST /v1/chat/completions/control` (`action: reasoning_end`, needs `reasoning_control: true`).
  - completions: `POST /v1/completions`. responses: `POST /v1/responses` ("works by converting Responses request into Chat Completions request"), `POST /v1/responses/input_tokens`. messages: `POST /v1/messages`, `POST /v1/messages/count_tokens`. Not served: generate-content format; Ollama-native routes (none in server.cpp).
  - Embeddings/rerank: `POST /v1/embeddings` (pooling must not be `none`), native `POST /embedding` and `/embeddings`; `POST /reranking` (aliases `/rerank`, `/v1/rerank`, `/v1/reranking`; needs `--embedding --pooling rank`).
  - Native: `POST /completion`, `POST /infill`, `POST /tokenize`, `POST /detokenize`, `POST /apply-template`, `GET /props` (`POST /props` needs `--props`), `GET /health` and `/v1/health` (503 while loading, public), `GET /metrics` (needs `--metrics`), `GET /slots` (`--slots` default on), `POST /slots/{id_slot}?action=save|restore|erase` (body `filename`, files in `--slot-save-path`), `GET|POST /lora-adapters`, `GET /v1/models` (one entry; `id` = model path or `--alias`). Also routed: `/v1/audio/transcriptions`, `/v1/systemone` (decision-model API, `questions` of type `choice`/`score`/`noul`), `/v1/stream`, `/v1/streams/lookup` (last two undocumented).
  - Router mode (start without `-m`): sources `LLAMA_CACHE` (cache dir), `--models-dir`, `--models-preset <ini>` (keys = flag names; preset-only keys `load-on-startup`, `stop-timeout`, `dedup-cache-models`); `--models-max N` (default 4), `--models-autoload/--no-models-autoload`, per-request `?autoload=`; `GET /models` (`?reload=1`; status `unloaded/loading/loaded/sleeping/downloading`), `POST /models/load`, `POST /models/unload`, `GET /models/sse`, `POST /models` (download), `DELETE /models?model=`; routing by body `model` (POST) or `?model=` (GET; `/metrics` requires it). `--sleep-idle-seconds N` unloads model and KV after idleness.
  - Flags: `--chat-template JINJA_TEMPLATE`, `--chat-template-file JINJA_TEMPLATE_FILE`, `--chat-template-kwargs`, `--skip-chat-parsing` (raw text, for our own parsers); `--reasoning-format none|deepseek|deepseek-legacy` (default auto), `-rea, --reasoning on|off|auto`, `--reasoning-effort LEVEL` (minimal/low/medium/high/xhigh/max), `--reasoning-budget N` (-1 unrestricted, 0 immediate end), `--reasoning-budget-message`; `-ngl, --gpu-layers, --n-gpu-layers N|auto|all` (default auto), `-fit, --fit on|off` (default on), `-fitt, --fit-target`; `-cmoe, --cpu-moe`, `-ncmoe, --n-cpu-moe N`, `--moe-cache-mib N` (new in v0.7.0: GPU cache for CPU-resident experts), `-ncffn, --n-cpu-ffn N`, `-ot, --override-tensor <pattern>=<buffer type>,...`; `-c, --ctx-size N` (0 = from model); `-np, --parallel N` (-1 = auto), `-kvu, --kv-unified`; `-fa, --flash-attn on|off|auto` (default auto); `-ctk, --cache-type-k` / `-ctv, --cache-type-v` (f32, f16, bf16, q8_0, q4_0, q4_1, iq4_nl, q5_0, q5_1; default f16); `--cache-prompt` (default on), `--cache-reuse N`, `-cram, --cache-ram N` (MiB, default 8192), `--cache-idle-slots` (default on), `-ctxcp, --ctx-checkpoints N` (default 32); `--slot-save-path PATH` (env `LLAMA_ARG_SLOT_SAVE_PATH`, new); `--metrics`; `--api-key KEY` (env `LLAMA_API_KEY`), `--api-key-file`; `--offline` (cache only, no network); `--embedding, --embeddings`, `--pooling none|mean|cls|last|rank`, `--rerank, --reranking`; `-hf, -hfr, --hf-repo <user>/<model>[:quant]` (default quant Q4_K_M), `-hff`, `-hft` (`HF_TOKEN`), `-hfd` (draft).
  - Speculative decoding: `--spec-type none,draft-simple,draft-eagle3,draft-mtp,draft-dflash,draft-dspark,ngram-simple,ngram-map-k,ngram-map-k4v,ngram-mod,ngram-cache`; `-md, --model-draft, --spec-draft-model FNAME`; `--spec-draft-n-max N` (default 3), `--spec-draft-n-min N`, `--spec-draft-n-ubatch N` (new), `-ngld`, `-devd`, `--spec-draft-p-min`, `--spec-draft-sampling greedy|probabilistic`, `--spec-default`. Removed: `--draft, --draft-n, --draft-max` and `--draft-min, --draft-n-min` ("the argument has been removed").
  - Request fields: `grammar` (GBNF), `json_schema`, `response_format` (`{"type":"json_object"}` with optional `schema`, or `{"type":"json_schema",...}`), `cache_prompt` (default true), `id_slot` (-1 = idle slot), `n_probs`, `post_sampling_probs`, `n_cache_reuse`, `timings_per_token`, `return_progress`, `parallel_tool_calls`, `chat_template_kwargs`, `reasoning_effort` (`none` disables), `reasoning_format`, `reasoning_control`. Responses carry `timings` (`cache_n`, `prompt_n`, `prompt_ms`, `prompt_per_second`, `predicted_n`, `predicted_per_second`) and `usage.prompt_tokens_details.cached_tokens`; `/completion` also `tokens_cached`.
  - `/metrics` names: `llamacpp:prompt_tokens_total`, `llamacpp:prompt_seconds_total`, `llamacpp:prompt_tokens_seconds` (prefill tok/s), `llamacpp:tokens_predicted_total`, `llamacpp:tokens_predicted_seconds_total`, `llamacpp:predicted_tokens_seconds` (decode tok/s), `llamacpp:requests_processing`, `llamacpp:requests_deferred`, `llamacpp:n_tokens_max`, `llamacpp:n_decode_total`, `llamacpp:n_busy_slots_per_decode`, `llamacpp:spec_decode_num_draft_tokens_total`, `llamacpp:spec_decode_num_accepted_tokens_total`, `llamacpp:spec_decode_num_drafts_total`, `llamacpp:spec_decode_num_accepted_tokens_per_pos_total`. No prefix-cache counter.
  - Tool-call parsing (from `common_chat_template_detect_params_init` in `common/chat.cpp` and `common/parsers/*.cpp`): dedicated handlers for Ministral 3 / Magistral Large 3 (`[TOOL_CALLS]...[ARGS]`), LLM-jp Harmony v1, gpt-oss harmony (`<|channel|>`), Muse Glimmer, Functionary v3.2 (`>>>recipient`), Kimi K2 Thinking (`<|tool_calls_section_begin|>`), Kimi K3, K2 Horizon, Ling 3.0 / Bailing V3 (`<arg_key>`/`<arg_value>`), Command-family `<|START_ACTION|>` JSON, LFM2, LFM2.5, GigaChat V3, MiniMax-M3 (`<invoke name=`), DeepSeek V3.2/V4 (DSML), Gemma 4 (`<|tool_call>call:`, with a legacy-template workaround), MiniCPM5 (`<function name=...><param name=...>`), TranslateGemma, Qwen3-Coder XML (`<tool_call><function=...><parameter=...>`, also used by Qwen3.5 and Nemotron Nano 3). Every other template goes through the "autoparser", which derives the format by rendering and diffing the template: `tool_format` JSON_NATIVE (`{"name":...,"arguments":...}`), TAG_WITH_JSON (`<function=X>{...}</function>`), TAG_WITH_TAGGED (`<param=key>value</param>`), with call-ID position detection. Output format enum: `CONTENT_ONLY`, `PEG_SIMPLE`, `PEG_NATIVE`, `PEG_GEMMA4`, `PEG_MINIMAX_M3`. `docs/function-calling.md` still documents an older list and a "Generic" fallback; treat source as authoritative.
  - Reasoning extraction: autoparser `reasoning_mode` NONE / TAG_BASED (`<think>...</think>`, start tag may be empty) / TOOLS_ONLY; harmony channels via the gpt-oss handler; result in `message.reasoning_content` (and `reasoning_content` deltas). `deepseek-legacy` keeps `<think>` in content and also fills `reasoning_content`; `none` leaves thoughts in content. `--reasoning-preserve` (default on) keeps reasoning in history for templates that support it.
  - MXFP4 on CUDA sm_86: supported. Release CUDA builds use the default arch list, which always includes `86-real`. On non-Blackwell GPUs MXFP4 runs through the MMQ path (`ggml_cuda_mmq_load_tiles_mxfp4` with q8_1 activations); FP4xFP4 tensor-core MMA is compiled only when `BLACKWELL_MMA_AVAILABLE` (compute capability >= 12.0).
- what we borrow: router presets INI and `/models/sse` events for the server manager and dashboard; `--fit` memory fitting as an input to `sy doctor`; `/apply-template` to hash exact prompt bytes for the §7.2 prefix registry; per-response `timings` and `cached_tokens` for the token ledger; template-diff autoparser idea for §4.3 step 3 (MIT, attribution required if code is copied); `n_probs` for classifier confidence; evaluate `/v1/systemone` decision models for the T0 router role.
- what we avoid: `--tools`, `--agent`, `--mcp-servers-config`, `--mcp-servers-json`, `--ui-mcp-proxy` (server-side tool execution; our agent runs tools in its own sandbox) and the internal `/tools` endpoint ("do NOT use this endpoint in a downstream application"); exposing `--rpc` backends; default CORS (reflects any origin with credentials) on non-loopback hosts; `--*-default` presets and `-hf` in zero-egress mode (they download; use `--offline` and local paths); hardcoding port 8080; the removed `--draft-max`/`--draft-min`.
- risks: fast churn (daily builds; v0.7.0 changed the default port, refactored the chat layer around `common_chat_session` and removed flags) so pin an exact build per release of Switchyard and re-run probes; GGUF and RPC security advisories in 2026: GHSA-j8rj-fmpv-wcxw (Critical, unauthenticated RCE in the RPC backend, 2026-03-26), GHSA-96jg-mvhq-q7q7 (High, GGUF tensor parsing heap overflow, 2026-03-18), GHSA-3p4r-fq3f-q74v (High, `mem_size` integer overflow bypassing the CVE-2025-53630 fix, 2026-03-12), GHSA-8947-pfff-2f3c (Low, out-of-bounds write in llama-server, 2026-01-05): load only hash-verified GGUF files, never expose RPC, set `--api-key`, bind 127.0.0.1. Router-mode routing of `/slots` save/restore requests: verified: no.
- sources: https://api.github.com/repos/ggml-org/llama.cpp/releases/latest, https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=6, https://github.com/ggml-org/llama.cpp/releases/tag/v0.7.0, https://github.com/ggml-org/llama.cpp/releases/download/v0.7.0/nightly-tag.txt, https://github.com/ggml-org/llama.cpp/releases/tag/b11669, https://github.com/ggml-org/llama.cpp/releases/expanded_assets/b11669, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/tools/server/README.md, https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/tools/server/server.cpp, https://github.com/ggml-org/llama.cpp/tree/v0.7.0/tools/server, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/docs/function-calling.md, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/common/chat.h, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/common/chat.cpp, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/common/chat-auto-parser.h, https://github.com/ggml-org/llama.cpp/tree/v0.7.0/common, https://github.com/ggml-org/llama.cpp/tree/v0.7.0/common/parsers, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/ggml/src/ggml-cuda/mmq.cuh, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/ggml/src/ggml-cuda/common.cuh, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/ggml/src/ggml-cuda/CMakeLists.txt, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/.github/workflows/release.yml, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/LICENSE, https://github.com/ggml-org/llama.cpp/security/advisories

#### bitnet.cpp
- verified: yes (2026-10-16), basis: project README, `run_inference_server.py`, `.gitmodules`, release/tag/commit feeds, model hub API and model config, embeddings guide. Feature set of the vendored server fork: verified: no.
- link: https://github.com/microsoft/BitNet
- license: MIT (README badge; model cards on the hub also MIT)
- version: no tagged releases (release and tag feeds empty); README news "bitnet.cpp 1.0 released" (2024-10-17); last commit 2026-07-27 (README news), last code change 2026-07-16/17 (embedding I2_S conversion). Status: maintained at low cadence.
- platforms: Windows native build from source only (Visual Studio 2022 with clang toolset, Developer Command Prompt), macOS build from source (ARM; README demo on an M2 Mac), Linux build from source (python >= 3.10, cmake >= 3.22, clang >= 18). No prebuilt binaries.
- what we use:
  - Models (exact hub IDs): https://huggingface.co/microsoft/bitnet-b1.58-2B-4T-gguf (`ggml-model-i2_s.gguf`, 1.188 GB; `BitNetForCausalLM`, 2.4B params, `max_position_embeddings` 4096), https://huggingface.co/microsoft/bitnet-b1.58-2B-4T, https://huggingface.co/microsoft/bitnet-b1.58-2B-4T-bf16, https://huggingface.co/microsoft/bitnet-embedding-0.6b (`bitnet-embeddings-0.6b-bf16-i2_s.gguf`, 0.428 GB), https://huggingface.co/microsoft/bitnet-embedding-270m (`bitnet-embeddings-270m-bf16-i2_s.gguf`, 0.367 GB). `setup_env.py --hf-repo` also lists third-party 1.58-bit models (bitnet_b1_58-large, bitnet_b1_58-3B, Llama3-8B-1.58-100B-tokens, Falcon3-1B/3B/7B/10B-Instruct-1.58bit; not checked on the hub).
  - Build/run: `python setup_env.py -md <dir> -q i2_s|tl1 [--quant-embd] [--use-pretuned]`; `python run_inference.py -m <gguf> -p <prompt> -cnv -t -c -n -temp`. Kernels: I2_S, TL1, TL2 (2B-4T: x86 I2_S + TL2, ARM I2_S + TL1). Embedding guide uses the `llama-embedding` CLI.
  - Server: `python run_inference_server.py` launches the vendored fork's `llama-server` with `-m -c -t -n -ngl 0 --temp --host --port -cb` (defaults: host 127.0.0.1, port 8080, threads 2, ctx 2048). Fork: submodule https://github.com/isHuangXin/llama.cpp.git, branch `release-bitnet-embedding-0.6b-270m`. API = chat-completions plus llama.cpp native endpoints of that fork revision.
  - Mainline llama.cpp alternative: v0.7.0 has the `bitnet` architecture, its converter registers `BitNetForCausalLM`, and has ternary types TQ1_0/TQ2_0 but no I2_S type. The hub's i2_s GGUF needs bitnet.cpp; a TQ1_0/TQ2_0 conversion of the bf16 checkpoint could run in mainline `llama-server` (quality and speed: verified: no).
  - T0 relevance: footprint fits the 8 GB T0 budget easily (1.19 GB LLM, 0.37-0.43 GB embedders), but the 4096-token context limits TK4 summarization inputs, tool-calling/JSON reliability is unknown, and it adds a second, source-built engine. Treat as an optional T0 candidate gated on `sy probe`.
- what we borrow: ternary models as optional T0 entries in the registry; per-CPU kernel choice (I2_S vs TL1/TL2) mirrors our `sy doctor` feature detection.
- what we avoid: bundling or building the fork inside `sy models pull` (toolchain requirements); exposing the fork server beyond loopback.
- risks: no versioning; vendored llama.cpp fork lags upstream fixes, including the 2026 GGUF parser advisories listed under llama.cpp (derived risk, fork revision not audited); few official models.
- sources: https://raw.githubusercontent.com/microsoft/BitNet/main/README.md, https://raw.githubusercontent.com/microsoft/BitNet/main/run_inference_server.py, https://raw.githubusercontent.com/microsoft/BitNet/main/.gitmodules, https://github.com/microsoft/BitNet/tree/main, https://github.com/microsoft/BitNet/releases.atom, https://github.com/microsoft/BitNet/tags.atom, https://github.com/microsoft/BitNet/commits/main.atom, https://raw.githubusercontent.com/microsoft/BitNet/main/docs/bitnet-embeddings-i2s-guide.md, https://huggingface.co/api/models?author=microsoft&search=bitnet&limit=50, https://huggingface.co/api/models/microsoft/bitnet-b1.58-2B-4T-gguf?blobs=true, https://huggingface.co/api/models/microsoft/bitnet-embedding-0.6b?blobs=true, https://huggingface.co/api/models/microsoft/bitnet-embedding-270m?blobs=true, https://huggingface.co/microsoft/bitnet-b1.58-2B-4T/resolve/main/config.json, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/src/llama-arch.cpp, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/ggml/include/ggml.h, https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/conversion/bitnet.py

### Serving and engines: GPU servers, KV cache, engine-side tool parsers

#### vLLM
- verified: yes (2026-10-16), basis: PyPI JSON API; v0.31.0 release page; stable docs (tool calling, reasoning outputs, structured outputs, quantized KV cache, GPU install, online serving, sleep mode, metrics); gpt-oss recipe; source files at tag v0.31.0 (tool and reasoning parser registries, `config/cache.py`, `config/speculative.py`, `config/structured_outputs.py`, `engine/arg_utils.py`, attention backends, MXFP4 MoE backend selection, `v1/metrics/loggers.py`)
- link: https://github.com/vllm-project/vllm
- license: Apache-2.0
- version: 0.31.0 (released 2026-10-05); previous 0.30.0 (2026-09-22), 0.29.0 (2026-09-09), 0.28.0 (2026-08-26): about one release every two weeks
- platforms: Windows no native ("vLLM does not support Windows natively"; docs suggest WSL) -> WSL2/Docker; macOS no CUDA path, ARM macOS only via the separate vLLM-Metal plugin (macOS 14+); Linux yes (PyPI wheels only manylinux_2_28 x86_64 and aarch64). The 0.31.0 PyPI wheel is built for CUDA 13.0 (release notes; a CUDA 12.9 image is offered; the install page dated 2026-05-11 still says 12.9); CUDA 13 images need an R580-series or newer host driver; compute capability 7.5+; Python >=3.10,<3.15
- what we use: launch `vllm serve <model> --tensor-parallel-size 4 --enable-auto-tool-choice --tool-call-parser <name> --reasoning-parser <name>`; tool options `--tool-parser-plugin`, `--tool-strict-level` (auto, function, parameter), `--exclude-tools-when-tool-choice-none`, `--chat-template`, env `VLLM_ENFORCE_STRICT_TOOL_CALLING` (default true; false disables structural-tag constraints); `tool_choice` auto, none, named, required (named and required run through structured outputs); tool parsers registered at v0.31.0 (50): `apertus`, `cohere_command3`, `cohere_command4`, `deepseek_v3`, `deepseek_v31`, `deepseek_v32`, `deepseek_v4`, `deepseek_v41`, `dots`, `ernie45`, `functiongemma`, `gemma4`, `gigachat3`, `glm45`, `glm47`, `granite`, `granite4`, `hermes`, `hunyuan_a13b`, `hy_v3`, `hy_v4`, `inkling`, `internlm`, `jamba`, `k2_horizon`, `kimi_k2`, `kimi_k3`, `lfm2`, `ling3`, `llama3_json`, `llama4_json`, `llama4_pythonic`, `longcat`, `mimo`, `minicpm5`, `minimax_m2`, `minimax_m3`, `mistral`, `muse_glimmer`, `olmo3`, `--tool-call-parser openai` (gpt-oss), `phi4_mini_json`, `poolside_v1`, `pythonic`, `qwen3_coder`, `qwen3_xml`, `seed_oss`, `step3`, `step3p5`, `xlam`; reasoning parsers (35): `cohere_command3`, `cohere_command4`, `deepseek_r1`, `deepseek_v3`, `deepseek_v4`, `deepseek_v41`, `ernie45`, `gemma4`, `glm45`, `glm47`, `granite`, `granite_thinking_parser`, `holo2`, `hunyuan_a13b`, `hy_v3`, `hy_v4`, `inkling`, `k2_horizon`, `kimi_k2`, `kimi_k3`, `ling3`, `mimo`, `minimax_m2`, `minimax_m2_append_think`, `minimax_m3`, `mistral`, `muse_glimmer`, `nemotron_v3`, `olmo3`, `openai_gptoss`, `poolside_v1`, `qwen3`, `seed_oss`, `step3`, `step3p5`; reasoning returned as `reasoning` on `/v1/chat/completions`, `/v1/messages`, `/v1/responses`; `--reasoning-config`, `--default-chat-template-kwargs '{"enable_thinking": false}'`; structured outputs `--structured-outputs-config.backend` auto, xgrammar, guidance, outlines, lm-format-enforcer, request `structured_outputs` with `json`, `regex`, `choice`, `grammar` or `structural_tag` (via extra body) or `response_format` `{"type": "json_schema"}`, `--structured-outputs-config.enable_in_reasoning=True`; endpoints `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, `/v1/messages`, `/v1/messages/count_tokens`, `/v1/embeddings`, `/tokenize`, `/detokenize`, `/v1/models`, `/health`, `/version`, `/load`, `/metrics`; prefix caching `enable_prefix_caching` default True when the model supports it, `--no-enable-prefix-caching` to disable, hash `sha256` default; metrics `vllm:prefix_cache_hits`, `vllm:prefix_cache_queries`, `vllm:prompt_tokens_cached`, `vllm:prompt_tokens`, `vllm:generation_tokens` (counters; docs list no throughput gauge, use rate()), `vllm:kv_cache_usage_perc`, `vllm:num_requests_running`, `vllm:num_requests_waiting`, `vllm:time_to_first_token_seconds`, `vllm:inter_token_latency_seconds`, `vllm:engine_sleep_state`, `vllm:external_prefix_cache_hits`, `vllm:external_prefix_cache_queries` (docs do not state the `_total` exposition suffix; read names from `/metrics` at probe time); speculative decoding `--speculative-config` (`-sc`) JSON with `method` ngram, ngram_gpu, suffix, draft_model, medusa, mlp_speculator, eagle, eagle3, extract_hidden_states, mtp and model-specific `*_mtp`, dflash, dspark, custom_class; KV cache `--kv-cache-dtype` (see KV-cache quantization plugins), `--kv-offloading-size <GiB>`, `--kv-offloading-backend` native or lmcache, `--kv-transfer-config`; sleep mode `--enable-sleep-mode` plus `VLLM_SERVER_DEV_MODE=1`: `POST /sleep?level=1` (weights to CPU, KV discarded) or `level=2` (both discarded), `POST /wake_up` (optional `tags=weights` or `tags=kv_cache`), `GET /is_sleeping`, `POST /release_kv_cache_memory`; MoE kernel pin `--moe-backend` (for example `marlin`)
- what we borrow: lazy name-to-(module, class) parser registry, as data; the per-reasoning-parser capability columns (structured output, tool calling) as registry fields; structural-tag strict tool calling as the model for the JSON fallback protocol
- what we avoid: importing vllm in core; dev-mode endpoints (sleep, wake, prefix-cache reset) on non-loopback interfaces; hardcoding parser lists from the docs page (it lists 26 names and `granite-20b-fc`, which is not registered at v0.31.0); the removed `guided_*` request fields
- risks: breaking changes every release (0.31.0: `tokenizer_mode="slow"` removed, `json_object` rejected with the outlines backend, mamba prefix flag renamed, `quantization="fp8"` redirected to `fp8_per_tensor`); the `reasoning_content` to `reasoning` rename makes old clients read an empty field; CUDA 13 default wheel needs an R580+ driver; Linux-only; gpt-oss on the responses endpoint reports usage as zeros and does not stream function calls (recipe)
- sources: https://pypi.org/pypi/vllm/json ; https://github.com/vllm-project/vllm/releases/tag/v0.31.0 ; https://docs.vllm.ai/en/stable/features/tool_calling/ ; https://docs.vllm.ai/en/stable/features/reasoning_outputs/ ; https://docs.vllm.ai/en/stable/features/structured_outputs/ ; https://docs.vllm.ai/en/stable/features/quantization/quantized_kvcache/ ; https://docs.vllm.ai/en/stable/getting_started/installation/gpu/ ; https://docs.vllm.ai/en/stable/serving/online_serving/ ; https://docs.vllm.ai/en/stable/features/sleep_mode/ ; https://docs.vllm.ai/en/stable/usage/metrics/ ; https://docs.vllm.ai/projects/recipes/en/latest/OpenAI/GPT-OSS.html ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/tool_parsers/__init__.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/reasoning/__init__.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/config/cache.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/config/speculative.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/config/structured_outputs.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/engine/arg_utils.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/v1/metrics/loggers.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/v1/attention/backends/flash_attn.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/v1/attention/backends/fa_utils.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/v1/attention/backends/triton_attn.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/v1/attention/backends/flashinfer.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/model_executor/layers/quantization/mxfp4.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/model_executor/layers/fused_moe/oracle/mxfp4.py

#### SGLang
- verified: yes (2026-10-16), basis: PyPI JSON API; release tag page v0.5.22 (no notes); source at tag v0.5.22 (`function_call/function_call_parser.py`, `parser/reasoning_parser.py`, `server_args.py`, `arg_groups/choices.py`, `entrypoints/http_server.py`); docs (server arguments, production metrics, install, tool parser, reasoning parser, structured outputs, quantized KV cache, attention backends, ARM macOS page, gpt-oss cookbook)
- link: https://github.com/sgl-project/sglang
- license: Apache-2.0
- version: 0.5.22 (released 2026-10-16 on PyPI; tag dated 2026-10-15); previous 0.5.21 (2026-10-01), 0.5.20 (2026-09-18), 0.5.19 (2026-09-04): about one release every two weeks
- platforms: Windows no native (PyPI wheels are manylinux_2_34 only; install docs target Linux; WSL2/Docker not documented, verified: no); macOS ARM via the MLX runtime (`SGLANG_USE_MLX=1`, source install, macOS 14+); Linux yes (x86_64, aarch64, cp311 to cp313). "SGLang requires CUDA 13"; 0.5.19 is the last release with a CUDA 12 build (image tag `v0.5.19-cu129`); driver: "a CUDA 13-compatible driver" (no number given); default attention backend FlashInfer needs sm75+
- what we use: launch `python3 -m sglang.launch_server --model-path <id> --tp-size 4 --tool-call-parser auto --reasoning-parser auto --enable-metrics --enable-cache-report`; `--tool-call-parser` accepts `auto` ("detect from chat template") or 42 names: `apertus2509`, `cohere_command4`, `deepseekv3`, `deepseekv31`, `deepseekv32`, `deepseekv4`, `deepseekv41`, `dots`, `gemma4`, `gigachat3`, `gigachat35`, `glm`, `glm45`, `glm47`, `gpt-oss`, `hermes`, `hunyuan`, `inkling`, `interns1`, `iquest_q1`, `k2_horizon`, `kimi_k2`, `kimi_k3`, `lfm2`, `ling3`, `llama3`, `mimo`, `minicpm5`, `minimax-m2`, `minimax-m3`, `mistral`, `muse`, `nanbeige`, `poolside_v1`, `pythonic`, `qwen`, `qwen25`, `qwen3_coder`, `spark25`, `step3`, `step3p5`, `trinity`; `--reasoning-parser` accepts `auto` or 34 names: `apertus2509`, `cohere_command4`, `deepseek-r1`, `deepseek-v3`, `deepseek-v4`, `deepseek-v41`, `dots`, `gemma4`, `gigachat35`, `glm45`, `gpt-oss`, `granite_thinking_parser`, `hunyuan`, `inkling`, `interns1`, `iquest_q1`, `k2_horizon`, `kimi`, `kimi_k2`, `kimi_k3`, `ling3`, `mimo`, `minimax`, `minimax-append-think`, `minimax-m3`, `mistral`, `muse`, `nanbeige`, `nemotron_3`, `poolside_v1`, `qwen3`, `qwen3-thinking`, `step3`, `step3p5`; reasoning returned as `reasoning_content` (request `separate_reasoning`, `stream_reasoning`); `tool_choice` required and named enforced with EBNF ("full support" with xgrammar); post-hoc parsing endpoints `POST /parse_function_call`, `POST /separate_reasoning`; constrained decoding `--grammar-backend` xgrammar (default), outlines, llguidance, none, request `response_format` json_schema, `regex`, `ebnf` (one constraint per request); endpoints `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, `/v1/messages`, `/v1/messages/count_tokens`, `/v1/embeddings`, `/v1/rerank`, `/v1/score`, `/v1/tokenize`, `/generate`, `/health`, `/v1/models`, `/flush_cache`; prefix cache RadixAttention on unless `--disable-radix-cache`, `--radix-eviction-policy`; metrics only with `--enable-metrics` (default off): `sglang:cache_hit_rate`, `sglang:prompt_tokens_total`, `sglang:generation_tokens_total`, `sglang:gen_throughput`, `sglang:token_usage`, `sglang:num_used_tokens`, `sglang:num_running_reqs`, `sglang:num_queue_reqs`, `sglang:time_to_first_token_seconds`, `sglang:inter_token_latency_seconds`, `sglang:e2e_request_latency_seconds`; `--enable-cache-report` returns cached tokens in `usage.prompt_tokens_details`; HiCache `--enable-hierarchical-cache`, `--hicache-ratio` (default 2.0), `--hicache-size`, `--hicache-storage-backend` file, mooncake, hf3fs, nixl, aibrix, dynamic, eic, simm; speculative `--speculative-algorithm` EAGLE, EAGLE3, NEXTN, STANDALONE, NGRAM with `--speculative-draft-model-path`; KV `--kv-cache-dtype` auto, fp8_e5m2, fp8_e4m3, bf16, bfloat16, nvfp4, fp4_mx_block16, `--quantization-param-path` (FP8 scales); memory release `--enable-memory-saver` with `POST /release_memory_occupation`, `POST /resume_memory_occupation`, `--enable-weights-cpu-backup`, weight reload `POST /update_weights_from_disk`; MoE CPU offload through KTransformers flags `--kt-method` (default AMXINT4), `--kt-weight-path`, `--kt-cpuinfer`, `--kt-threadpool-count`, `--kt-num-gpu-experts`, `--kt-max-deferred-experts-per-token`; generic `--cpu-offload-gb`
- what we borrow: `auto` parser detection from the chat template; post-hoc `/parse_function_call` and `/separate_reasoning` as the shape of Switchyard's own parser service; EBNF-forced `tool_choice` for the fallback protocol
- what we avoid: relying on metrics without `--enable-metrics`; `tool_choice` required or named with non-xgrammar backends; trusting the docs parser table (it lists `llama4`, absent from the v0.5.22 map, and omits 29 registered names)
- risks: CUDA 13 floor since 0.5.20 (R580-class driver needed; older drivers pin 0.5.19); Linux-only wheels need glibc 2.34+ (manylinux_2_34), which matters for the WSL2 distro choice; release notes for 0.5.22 are empty on the release page; gpt-oss cookbook lists H100, H200, B200 (plus ROCm and Xeon) only, so Ampere is undocumented; `gpt-oss` parser drops analysis-channel text
- sources: https://pypi.org/pypi/sglang/json ; https://github.com/sgl-project/sglang/releases/tag/v0.5.22 ; https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/srt/function_call/function_call_parser.py ; https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/srt/parser/reasoning_parser.py ; https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/srt/server_args.py ; https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/srt/arg_groups/choices.py ; https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/srt/entrypoints/http_server.py ; https://docs.sglang.io/advanced_features/server_arguments.html ; https://docs.sglang.io/references/production_metrics.html ; https://docs.sglang.io/get_started/install.html ; https://docs.sglang.io/advanced_features/tool_parser.html ; https://docs.sglang.io/docs/advanced_features/separate_reasoning.md ; https://docs.sglang.io/docs/advanced_features/structured_outputs.md ; https://docs.sglang.io/docs/advanced_features/quantized_kv_cache.md ; https://docs.sglang.io/docs/advanced_features/attention_backend.md ; https://docs.sglang.io/docs/hardware-platforms/apple_metal.md ; https://docs.sglang.io/cookbook/autoregressive/OpenAI/GPT-OSS.md ; https://docs.sglang.io/llms.txt

#### LMCache
- verified: yes (2026-10-16), basis: PyPI JSON API; README and LICENSE at tag v0.5.6; docs (installation, compatibility, integration, dynamic connector, index); vLLM v0.31.0 `config/cache.py` (`lmcache` offloading backend)
- link: https://github.com/LMCache/LMCache
- license: Apache-2.0
- version: 0.5.6 (released 2026-10-15); preceded by 0.5.6rc1 to rc5 (2026-09-26 to 2026-10-12) and 0.5.5 (2026-09-12)
- platforms: Windows no; macOS no; Linux yes (PyPI classifier "Operating System :: POSIX :: Linux"; wheels x86_64 and ARM64; default wheel CUDA 13.0, CUDA 12.9 wheel on a separate index; ROCm and XPU builds installed inside matching vLLM containers). Docs prerequisites: Linux, CUDA GPU compute capability 7.0+, CUDA 12.1+; Python: docs say 3.9 to 3.13, PyPI says >=3.10,<3.14
- what we use: optional, vLLM-only add-on for long shared prefixes across restarts or instances: `vllm serve ... --kv-offloading-backend lmcache --kv-offloading-size <GiB>`, or `--kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both"}'`, or the multiprocess connector `{"kv_connector":"LMCacheMPConnector","kv_connector_module_path":"lmcache.integration.vllm.lmcache_mp_connector","kv_role":"kv_both"}` (external module path for vLLM >= 0.20.0); hit accounting through `vllm:external_prefix_cache_hits` and `vllm:external_prefix_cache_queries`. Function: tiered KV offload and reuse (CPU RAM, local disk, Redis/Valkey, Mooncake, InfiniStore, S3-compatible object storage, NIXL, GDS), non-prefix reuse (CacheBlend), prefill-to-decode KV transfer
- what we borrow: chunk-hash KV reuse as the reason to keep Switchyard's prompt prefix byte-stable (TK1); per-request prefix-hit observability for the dashboard
- what we avoid: making it a dependency of any default tier; SGLang integration (listed under "Supported Engines" in the docs, but SGLang v0.5.22 server args contain no LMCache option and LMCache is not among its built-in HiCache storage backends, although a `dynamic` backend option exists; verified: no for a working SGLang path)
- risks: "Development Status :: 3 - Alpha" classifier; tight vLLM version coupling (compatibility page: connector loading changed at vLLM 0.20.0, per-model validated pairs); release candidates roughly weekly; Linux-only; KV compression features (CacheGen) not evaluated (verified: no)
- sources: https://pypi.org/pypi/lmcache/json ; https://raw.githubusercontent.com/LMCache/LMCache/v0.5.6/README.md ; https://raw.githubusercontent.com/LMCache/LMCache/v0.5.6/LICENSE ; https://docs.lmcache.ai/getting_started/installation.html ; https://docs.lmcache.ai/getting_started/compatibility.html ; https://docs.lmcache.ai/developer_guide/integration.html ; https://docs.lmcache.ai/api_reference/dynamic_connector.html ; https://docs.lmcache.ai/index.html ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/config/cache.py

#### KV-cache quantization plugins
- verified: yes (2026-10-16), basis: vLLM v0.31.0 source (`CacheDType` literal, attention backend `supported_kv_cache_dtypes`, `fa_utils.py`) and quantized KV cache docs; SGLang server-args, quantized KV cache and attention backend docs; llama.cpp server README and `src/llama-context.cpp` (master, build b11677); Ollama FAQ at v0.40.5; transformers KV cache docs; PyPI JSON (kvpress, transformers, optimum-quanto, hqq); kvpress README and LICENSE; KIVI LICENSE and commits feed. Not verified: hardware and backend support for vLLM `turboquant_*` dtypes; LMCache KV compression
- link: https://docs.vllm.ai/en/stable/features/quantization/quantized_kvcache/ ; https://docs.sglang.io/docs/advanced_features/quantized_kv_cache.md ; https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md ; https://github.com/NVIDIA/kvpress ; https://github.com/jy-yuan/KIVI
- license: vLLM, SGLang Apache-2.0; llama.cpp, Ollama MIT; kvpress Apache-2.0; transformers Apache-2.0; optimum-quanto Apache-2.0; hqq "Apache 2" (PyPI license field); KIVI MIT
- version: no single project; engine-native: vLLM 0.31.0 (2026-10-05), SGLang 0.5.22 (2026-10-16), llama.cpp b11677 (2026-10-16), Ollama 0.40.5 (2026-10-15); libraries: kvpress 0.5.5 (2026-09-17), transformers 5.20.0 (2026-10-15), optimum-quanto 0.2.7 (2025-03-06, "Development Status :: 2 - Pre-Alpha"), hqq 0.2.8.post1 (2025-10-20), KIVI last commit 2025-11-20
- platforms: engine-native options follow the engine (vLLM, SGLang: Linux; llama.cpp, Ollama: see their own research rows); kvpress ships a py3-none-any wheel on top of transformers
- what we use: recommendation: expose engine flags only, through one registry key `kv_cache_dtype` mapped per engine, default off (auto or f16), opt-in per model after a probe accuracy check. Mapping: vLLM `--kv-cache-dtype` auto, float16, bfloat16, fp8, fp8_e4m3, fp8_e5m2, fp8_inc, fp8_ds_mla, nvfp4_ds_mla, turboquant_k8v4, turboquant_4bit_nc, turboquant_k3v4_nc, turboquant_3bit_nc, int4_per_token_head, int8_per_token_head, fp8_per_token_head, nvfp4, nvfp4_4over6, plus `--kv-cache-dtype-skip-layers` (layer indices or types such as `sliding_window`); SGLang `--kv-cache-dtype` auto, fp8_e5m2, fp8_e4m3, bf16, bfloat16, nvfp4, fp4_mx_block16 (FP4 "currently experimental", needs CUDA 12.8+ and PyTorch 2.8.0+) plus `--quantization-param-path`; llama.cpp `-ctk, --cache-type-k` and `-ctv, --cache-type-v` with f32, f16, bf16, q8_0, q4_0, q4_1, iq4_nl, q5_0, q5_1 (default f16; draft model `-ctkd`, `-ctvd`); Ollama env `OLLAMA_KV_CACHE_TYPE` f16 (default), q8_0, q4_0 (applies when Flash Attention is enabled); transformers fallback `cache_implementation="quantized"` with `cache_config={"backend": "hqq" or "quanto", "nbits": ..., "axis_key": ..., "axis_value": ...}` (hqq int1, int2, int3, int4, int8; quanto int2, int4, default backend). Ampere sm_86: vLLM FlashAttention backend accepts FP8 KV only on sm90 (FA3) or sm100 (FA4), so on sm_86 FP8 KV runs on the TRITON_ATTN or FLASHINFER backend; the Triton backend also lists int8, int4 and fp8 per-token-head and accepts any compute capability (code only, not on the docs page); FlashInfer `nvfp4` KV requires the sm100 family; SGLang FP8 KV works with FlashInfer (Ampere default) and Triton, native NVFP4 recipes target SM100
- what we borrow: per-layer skip list for KV quantization (vLLM `--kv-cache-dtype-skip-layers`; docs: sliding-window layers are "more sensitive") as a registry field
- what we avoid: kvpress (transformers-only research toolkit of compression "presses" applied at prefill, decoding compression "experimental"; no engine integration in the README); KIVI (MIT research code; last commit 2025-11-20); optimum-quanto as a default (Pre-Alpha, last release 2025-03-06); FP4 KV on the Ampere workstation
- risks: vLLM FP8 without calibration uses scales of 1.0 (llm-compressor calibration recommended); SGLang warns quantized KV "can be extremely slow if dequantization is not fused with the attention kernel"; llama.cpp quantized V cache requires Flash Attention (auto-enabled when `-fa auto`, error when off); transformers QuantizedCache has no sliding-layer, offloading or torch.compile support and "can harm latency" at short context
- sources: https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/config/cache.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/v1/attention/backends/flash_attn.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/v1/attention/backends/fa_utils.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/v1/attention/backends/triton_attn.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/v1/attention/backends/flashinfer.py ; https://docs.vllm.ai/en/stable/features/quantization/quantized_kvcache/ ; https://docs.sglang.io/advanced_features/server_arguments.html ; https://docs.sglang.io/docs/advanced_features/quantized_kv_cache.md ; https://docs.sglang.io/docs/advanced_features/attention_backend.md ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/src/llama-context.cpp ; https://raw.githubusercontent.com/ollama/ollama/v0.40.5/docs/faq.mdx ; https://huggingface.co/docs/transformers/main/en/kv_cache ; https://pypi.org/pypi/kvpress/json ; https://pypi.org/pypi/transformers/json ; https://pypi.org/pypi/optimum-quanto/json ; https://pypi.org/pypi/hqq/json ; https://raw.githubusercontent.com/NVIDIA/kvpress/main/README.md ; https://raw.githubusercontent.com/NVIDIA/kvpress/main/LICENSE ; https://raw.githubusercontent.com/jy-yuan/KIVI/main/LICENSE ; https://github.com/jy-yuan/KIVI/commits/main.atom

#### engine-side tool parsers
- verified: yes (2026-10-16), basis: vLLM v0.31.0 parser registry, tool-calling docs and gpt-oss recipe; SGLang v0.5.22 parser map, `server_args.py` and tool-parser docs; llama.cpp master at build b11677 (`docs/autoparser.md`, `docs/function-calling.md`, `common/chat.h`, `common/chat.cpp`, server README); Ollama v0.40.5 (`model/parsers/parsers.go`, `model/renderers/renderer.go`, `tools/tools.go`, `server/routes.go`) and its tool-calling and compatibility docs. Not verified: behaviour of the workstation's Ollama 0.34.4
- link: https://docs.vllm.ai/en/stable/features/tool_calling/ ; https://docs.sglang.io/advanced_features/tool_parser.html ; https://github.com/ggml-org/llama.cpp/blob/master/docs/autoparser.md ; https://docs.ollama.com/capabilities/tool-calling
- license: vLLM Apache-2.0; SGLang Apache-2.0; llama.cpp MIT; Ollama MIT
- version: vLLM 0.31.0 (2026-10-05); SGLang 0.5.22 (2026-10-16); llama.cpp b11677 (2026-10-16; the release feed also shows a `v0.7.0` release on 2026-10-15); Ollama 0.40.5 (2026-10-15)
- platforms: parsers run inside each engine; vLLM and SGLang Linux (see rows above); llama.cpp and Ollama platforms are covered by their own research rows
- what we use: mechanisms: vLLM: one named parser per model family (`--enable-auto-tool-choice --tool-call-parser <name>`), streamed deltas, `strict: true` per tool or `--tool-strict-level` to constrain auto calls (docs: without it "auto tool calls are extracted from raw text, so arguments may be malformed"). SGLang: `--tool-call-parser auto` or a name; required and named `tool_choice` via EBNF; streamed argument fragments the client must concatenate; `/parse_function_call` for text the server did not parse. llama.cpp: `--jinja` (default enabled); the auto-parser renders the chat template differentially to extract reasoning, content and tool markers, then builds a PEG parser and optional GBNF grammar; tool modes JSON_NATIVE, TAG_WITH_JSON, TAG_WITH_TAGGED; dedicated handlers before the auto-parser for gpt-oss, Functionary v3.2 and Ministral 3; `common_chat_format` is now CONTENT_ONLY plus PEG_SIMPLE, PEG_NATIVE, PEG_GEMMA4, PEG_MINIMAX_M3; parallel calls only with `"parallel_tool_calls": true`. Ollama: 27 built-in parser names (`cogito`, a publisher-named one, `deepseek3`, `functiongemma`, `gemma4`, `gemma4-no-thinking`, `glimmer`, `glm-4.7`, `glm-ocr`, `harmony`, `laguna`, `lfm2`, `lfm2-thinking`, `ministral`, `nemotron-3-nano`, `nemotron-3.5-nano`, `olmo3`, `olmo3-think`, `ornith`, `passthrough`, `poolside-v1`, `qwen3`, `qwen3-coder`, `qwen3-thinking`, `qwen3-vl-instruct`, `qwen3-vl-thinking`, `qwen3.5`), plus built-in prompt renderers for many of the same families (`model/renderers/renderer.go`); otherwise a parser built "from a model's chat template and a list of provided tools" that matches tool names and JSON objects and emits a call only when complete; tools documented on `/api/chat` and `/v1/chat/completions` (`/v1/responses` and `/v1/messages` routes also exist; tool handling there not checked); `tool_choice` "Not supported" in the chat-completions compatibility API. Trust policy: use server-side parsing for an (engine, parser, model) triple only after `sy probe` passes (1) one call, (2) two parallel calls, (3) a streamed call reassembled into valid JSON, (4) arguments valid against the tool schema, (5) prose and reasoning preserved next to calls; otherwise omit `tools`, render tools into the prompt with the fallback protocol (§4.4) and parse raw text client-side
- what we borrow: llama.cpp's differential template analysis (idea only) for parser selection in `sy models add`; SGLang's post-hoc parse endpoint shape; vLLM's structural-tag strict mode for constrained tool calls
- what we avoid: hardcoded parser names (they differ per engine and change per release); assuming parallel calls; `tool_choice` on Ollama's compatibility API; harmony tool calls on vLLM chat-completions with any `tool_choice` other than `auto`
- risks: docs lag code (vLLM docs list `granite-20b-fc`, missing from the v0.31.0 registry; SGLang docs list `llama4`, missing from the v0.5.22 map; llama.cpp `function-calling.md` still describes a "Generic" handler that `chat.h` no longer enumerates); naming drift across engines (`minimax_m2` vs `minimax-m2`, `deepseek_v31` vs `deepseekv31`, `--tool-call-parser openai` vs `gpt-oss` vs `harmony`); llama.cpp undetected tool formats log an error and fall back to no tool parsing; Ollama's template-derived parser cannot parse a call without an `arguments` object and only treats `{`/`[` as start tags when output begins with them; the workstation's Ollama 0.34.4 is six minor versions behind 0.40.5
- sources: https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/tool_parsers/__init__.py ; https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/reasoning/__init__.py ; https://docs.vllm.ai/en/stable/features/tool_calling/ ; https://docs.vllm.ai/projects/recipes/en/latest/OpenAI/GPT-OSS.html ; https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/srt/function_call/function_call_parser.py ; https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/srt/server_args.py ; https://docs.sglang.io/advanced_features/tool_parser.html ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/autoparser.md ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/function-calling.md ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/chat.h ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/chat.cpp ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/LICENSE ; https://github.com/ggml-org/llama.cpp/releases.atom ; https://raw.githubusercontent.com/ollama/ollama/v0.40.5/model/parsers/parsers.go ; https://raw.githubusercontent.com/ollama/ollama/v0.40.5/model/renderers/renderer.go ; https://raw.githubusercontent.com/ollama/ollama/v0.40.5/tools/tools.go ; https://raw.githubusercontent.com/ollama/ollama/v0.40.5/server/routes.go ; https://raw.githubusercontent.com/ollama/ollama/v0.40.5/LICENSE ; https://github.com/ollama/ollama/releases.atom ; https://docs.ollama.com/capabilities/tool-calling ; https://docs.ollama.com/api/openai-compatibility

### Serving and engines: local engines and hot-swap

#### Ollama
- verified: yes (2026-10-16), basis: release list and repo metadata (code host API), release pages, repo docs sources (`docs/openapi.yaml`, `docs/api/*.mdx`, `docs/capabilities/*.mdx`, `docs/faq.mdx`, `docs/context-length.mdx`, `docs/modelfile.mdx`, `docs/import.mdx`, `docs/windows.mdx`, `docs/gpu.mdx`), Go source of the chat-completions compatibility layer and its streaming middleware (see sources), model hub docs page for hub pulls.
- link: https://github.com/ollama/ollama
- license: MIT
- version: 0.40.5 (released 2026-10-14). The workstation's 0.34.4 (released 2026-09-23) is not current: 8 releases behind (0.35.0, 0.35.1, 0.40.0 to 0.40.5). Notable since 0.34.4: 0.35.0 decision models (`POST /v1/systemone`); 0.40.0 runs MLX-supported architectures on the MLX runner by default on ARM macOS; 0.40.4 multimodal embeddings through llama-server, MLX speculative-decoding fixes; 0.40.5 fixes continuing a responses-format conversation with custom tool calls, updates llama.cpp and MLX.
- platforms: Windows native (Windows 10 22H2+, per-user install without admin, GPU driver 551.61+), macOS native, Linux native. CUDA compute capability 5.0+ with driver 550+; CC 8.6 table lists RTX A6000.
- what we use:
  - Native API (default `127.0.0.1:11434`, NDJSON streaming `application/x-ndjson`, `stream` defaults to true, usage in the final `done: true` chunk):
    - `POST /api/chat`: `model`, `messages[]` (`role` system/user/assistant/tool, `content`, `images` base64, `tool_calls`; tool results as `{"role":"tool","tool_name":...,"content":...}`), `tools[]` (`type: function`, `function{name, description, parameters}`), `format`, `options`, `stream`, `think`, `keep_alive`, `logprobs`, `top_logprobs`. Response: `message.content`, `message.thinking`, `message.tool_calls[].function{index, name, arguments}` (arguments is a JSON object, not a string; no call id), `done`, `done_reason`, timing fields. Parallel tool calls documented.
    - `POST /api/generate`: `prompt`, `suffix` (FIM), `images`, `format`, `system`, `stream`, `think`, `raw` (no prompt template; covers SPEC §4.1 raw text completion), `keep_alive`, `options`, `logprobs`, `top_logprobs`. Response: `response`, `thinking`, timing fields.
    - `POST /api/embed`: `model`, `input` (string or array), `truncate` (default true), `dimensions`, `keep_alive`, `options`. Response: `embeddings`, `total_duration`, `load_duration`, `prompt_eval_count`.
    - `POST /api/show`: `model`, `verbose`. Response: `capabilities`, `thinking{values, default}`, `template`, `parameters`, `model_info`, `details`, `license`, `modified_at`.
    - `GET /api/ps`: `models[]` with `name`, `model`, `size`, `size_vram`, `context_length`, `expires_at`, `digest`, `details`.
    - `GET /api/tags`: `models[]` with `name`, `model`, `size`, `digest`, `modified_at`, `details{format, family, families, parameter_size, quantization_level}`, `remote_model`, `remote_host`.
    - `POST /api/pull`: `model`, `insecure`, `stream`; progress events `{status, digest, total, completed}`.
    - `GET /api/version`: `{version}`.
    - `POST /api/create`: `model`, `from`, `files` (filename to SHA-256 digest; split GGUF shards under original names), `template`, `renderer`, `parser`, `system`, `parameters`, `messages`, `quantize`, `draft_files`, `draft_quantize`, `requires`, `capabilities`, `license`, `stream`; blobs via `HEAD`/`POST /api/blobs/{digest}`. Also `/api/copy`, `/api/delete`, `/api/push`.
  - Usage/timing (nanoseconds): `total_duration`, `load_duration`, `prompt_eval_count`, `prompt_eval_cached_count`, `prompt_eval_duration` (uncached prompt tokens only), `eval_count`, `eval_duration`.
  - Compatibility endpoints (SSE `data: {...}` frames ending `data: [DONE]`):
    - `/v1/chat/completions`: supported `model`, `messages` (text, base64 image, content-part arrays; image URLs not supported), `frequency_penalty`, `presence_penalty`, `response_format`, `seed`, `stop`, `stream`, `stream_options.include_usage`, `temperature`, `top_p`, `max_tokens`, `tools`, `reasoning_effort`, `reasoning.effort`. Not supported: `tool_choice`, `logit_bias`, `user`, `n`; logprobs listed as unsupported. Response carries reasoning in `message.reasoning` / `delta.reasoning` (mapped from native `thinking`) and `usage.prompt_tokens_details.cached_tokens` (mapped from `prompt_eval_cached_count`); both confirmed in source.
    - `/v1/completions` (`prompt` string only, `suffix`), `/v1/embeddings` (`input` string or string array, `encoding_format`, `dimensions`), `/v1/models`, `/v1/models/{model}`.
    - Responses format `POST /v1/responses`: added in v0.13.3 (2025-12-09); stateless only (no `previous_response_id`, `conversation`, `truncation`); fields `model`, `input`, `instructions`, `tools`, `stream`, `temperature`, `top_p`, `max_output_tokens`, `reasoning.effort`, `think` (extension); reasoning summaries for thinking models.
    - Messages format `POST /v1/messages`: added in v0.14.0 (2026-01-10); text, content-block arrays, `tool_use`, `tool_result`, `thinking` blocks, `system`, `stream`, `temperature`, `top_p`, `top_k`, `stop_sequences`, `tools`, `thinking`, `output_config.effort`; streaming events `message_start` ... `message_stop`, `ping`, `error`. Not supported: `tool_choice`, `metadata`, `/v1/messages/count_tokens`, `cache_control` prompt caching, batches, citations, PDF `document` blocks; `budget_tokens` accepted but not enforced; local server does not validate API keys; token counts approximate.
  - `think`: `true`, `false`, `null` (model default) or a model-defined string from `/api/show` `thinking.values`; numbers rejected. gpt-oss reports `values: ["low","medium","high"]`, `default: "medium"`. On `/v1` for gpt-oss: `"minimal"` maps to `"low"`, `"xhigh"`/`"ultra"` map to `"high"`, `"none"` requests no thinking output.
  - `format`: `"json"` or a JSON Schema object (structured outputs; not available on the hosted cloud).
  - `keep_alive`: duration string (`"10m"`), seconds number, negative keeps loaded, `0` unloads after the response; default 5 minutes; overrides `OLLAMA_KEEP_ALIVE`.
  - `options.num_ctx`: always set explicitly and read back `context_length` from `/api/ps`; docs disagree on the default (Modelfile table: 2048; FAQ: 4096; context-length page: VRAM-based, under 24 GiB 4k, 24 to 48 GiB 32k, 48 GiB or more 256k). `/v1` cannot set context size (needs a Modelfile `PARAMETER num_ctx`).
  - Env vars: `OLLAMA_HOST` (default bind `127.0.0.1:11434`), `OLLAMA_MODELS` (model dir), `OLLAMA_KEEP_ALIVE`, `OLLAMA_CONTEXT_LENGTH`, `OLLAMA_NUM_PARALLEL` (default 1; memory scales with `OLLAMA_NUM_PARALLEL` x `OLLAMA_CONTEXT_LENGTH`), `OLLAMA_MAX_LOADED_MODELS` (default 3 x GPU count, or 3 on CPU), `OLLAMA_MAX_QUEUE` (default 512; HTTP 503 beyond), `OLLAMA_FLASH_ATTENTION` (automatic when supported; `1` forces on, `0` off), `OLLAMA_KV_CACHE_TYPE` (`f16` default, `q8_0`, `q4_0`; global; needs flash attention), `OLLAMA_ORIGINS`, `OLLAMA_NO_CLOUD=1`.
  - GGUF import: Modelfile `FROM /path/file.gguf` (absolute or relative to the Modelfile), split GGUF `FROM ./model-*.gguf`; GGUF is not re-quantized on import (quantize beforehand with `llama-quantize`); Safetensors via `FROM <dir>` with optional `quantize`. Other instructions: `PARAMETER`, `TEMPLATE`, `SYSTEM`, `LICENSE`, `MESSAGE`, `REQUIRES`, `CAPABILITY`; parameter `draft_num_predict` (speculative draft tokens; default 4 for separate draft models; `0` disables).
  - Hub pulls from the original repo: `ollama pull hf.co/{username}/{repository}:{quantization}` (default `Q4_K_M` when present; full filename also accepted as tag).
  - Auth: none on the local API; hosted cloud uses `Authorization: Bearer`.
- what we borrow: capability discovery via `/api/show` (`capabilities`, `thinking.values/default`) for `sy probe`; `/api/ps` (`size_vram`, `expires_at`, `context_length`) as the residency signal for the server manager; `keep_alive` value semantics; pull progress event shape; `REQUIRES`-style minimum-engine-version field for registry recipes.
- what we avoid: `/v1` for anything needing `num_ctx` or `think` beyond effort names (use native `/api/chat`); assuming a default context; `tool_choice` (unsupported on chat-completions and messages endpoints); expecting messages-format prompt caching or `budget_tokens` enforcement; stateful responses; library tags and `:cloud` models in zero-egress mode; per-model KV-quant assumptions (`OLLAMA_KV_CACHE_TYPE` is global).
- risks: release cadence (8 releases from 2026-09-23 to 2026-10-14), so pin the tested version in the compatibility matrix and re-probe after upgrades; a signed-in local server routes `:cloud` model names to the hosted service, so egress can happen behind a localhost URL (detect via `remote_host`/`remote_model` in `/api/tags`, or require `OLLAMA_NO_CLOUD=1` or `"disable_ollama_cloud": true` in `~/.ollama/server.json`); no auth on the local API, so `OLLAMA_HOST=0.0.0.0` exposes it; docs say `/v1` logprobs unsupported while the Go request struct has a `logprobs` field (treat as unverified); the repo security page lists no published advisories (2026-10-16).
- sources: https://api.github.com/repos/ollama/ollama/releases?per_page=15 ; https://api.github.com/repos/ollama/ollama ; https://github.com/ollama/ollama/releases/tag/v0.14.0 ; https://github.com/ollama/ollama/releases/tag/v0.13.3 ; https://github.com/ollama/ollama/releases/tag/v0.34.4 ; https://github.com/ollama/ollama/releases/tag/v0.40.0 ; https://raw.githubusercontent.com/ollama/ollama/main/docs/openapi.yaml ; https://raw.githubusercontent.com/ollama/ollama/main/docs/api/openai-compatibility.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/api/anthropic-compatibility.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/api/usage.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/api/streaming.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/api/authentication.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/capabilities/thinking.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/capabilities/structured-outputs.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/capabilities/tool-calling.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/faq.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/context-length.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/modelfile.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/import.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/windows.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/gpu.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/openai/openai.go ; https://raw.githubusercontent.com/ollama/ollama/main/middleware/openai.go ; https://github.com/ollama/ollama/security ; https://huggingface.co/docs/hub/ollama

#### MLX-LM
- verified: yes (2026-10-16), basis: PyPI JSON (version, license, classifiers, extras, upload dates), release list, repo `README.md`, `mlx_lm/SERVER.md`, `mlx_lm/server.py`, `mlx_lm/LEARNED_QUANTS.md`.
- link: https://github.com/ml-explore/mlx-lm
- license: MIT
- version: 0.32.2 (released 2026-10-16; 0.32.0 on 2026-10-01, 0.32.1 on 2026-10-15; previous 0.31.3 on 2026-04-22). Requires Python >= 3.11, `transformers>=5.7.0`.
- platforms: Windows no (not declared); macOS native (ARM; the `mlx` dependency is Darwin-only by default); Linux declared (classifier POSIX Linux; extras `cuda12`, `cuda13`, `cpu`), not tested by us.
- what we use:
  - `mlx_lm.server --model <path or hub id>`; defaults `--host 127.0.0.1`, `--port 8080`.
  - Endpoints: `POST /v1/chat/completions`, `POST /v1/completions`, `GET /v1/models`, `GET /health`. SSE streaming ending `data: [DONE]`.
  - Request fields: `messages`, `role_mapping`, `stop`, `max_tokens` (default 512), `stream` (default false), `temperature` (default 0.0), `top_p`, `top_k`, `min_p`, `repetition_penalty`, `presence_penalty`, `frequency_penalty` (each with a `*_context_size`), `logit_bias`, `logprobs` (1 to 10), `model`, `adapters`, `draft_model`, `num_draft_tokens` (default 3).
  - Response: `tool_calls` parsed server-side by the tokenizer's tool parser (`finish_reason: "tool_calls"`); `reasoning` field on message/delta for tokenizers with thinking tokens; `usage.prompt_tokens`, `completion_tokens`, `total_tokens`, `prompt_tokens_details.cached_tokens`.
  - Flags: `--draft-model`, `--num-draft-tokens`, `--chat-template`, `--use-default-chat-template`, `--chat-template-args`, `--prompt-cache-size` (default 10 caches), `--prompt-cache-bytes`, `--decode-concurrency` (default 32), `--prompt-concurrency` (default 8), `--prefill-step-size` (default 2048), `--kv-bits`, `--kv-group-size`, `--quantized-kv-start` (default 5000), `--max-tokens`, `--temp`, `--top-p`, `--top-k`, `--min-p`, `--adapter-path`, `--trust-remote-code`, `--allowed-origins`, `--pipeline`, `--log-level`.
  - Quantization: `mlx_lm.convert --model <id> -q` (README example yields 4-bit); learned quants `mlx_lm.dwq`, `mlx_lm.dynamic_quant`, `mlx_lm.awq`. Offline KV prefix file: `mlx_lm.cache_prompt --prompt-cache-file <f>.safetensors`, reused by `mlx_lm.generate --prompt-cache-file`.
  - Coverage: architectures implemented under `mlx_lm/models`; MLX-format weights from the model hub or converted locally.
- what we borrow: nearest-prefix LRU prompt cache with a byte cap (`LRUPromptCache.fetch_nearest_cache`); explicit batchability rule (draft model or quantized KV disables batching) as an engine capability flag.
- what we avoid: constrained decoding on this engine (no `response_format` handling in `server.py`; use the JSON fallback with validate-and-retry); `--kv-bits` when concurrency matters (server then handles one request at a time); binding beyond localhost (SERVER.md: "not recommended for production as it only implements basic security checks").
- risks: release gap April to October 2026 followed by three releases in two weeks; API is "intended to be similar" to chat-completions (no `tool_choice`, no `response_format`), so re-probe per release; pulls in transformers 5.x as a dependency.
- sources: https://pypi.org/pypi/mlx-lm/json ; https://pypi.org/pypi/mlx-lm/0.32.2/json ; https://api.github.com/repos/ml-explore/mlx-lm/releases?per_page=10 ; https://raw.githubusercontent.com/ml-explore/mlx-lm/main/README.md ; https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/SERVER.md ; https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/server.py ; https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/LEARNED_QUANTS.md

#### transformers
- verified: yes (2026-10-16), basis: PyPI JSON, repo docs sources (`docs/source/en/serve-cli/serving.md`, `serve-cli/serving_optims.md`, `continuous_batching.md`, `chat_templating.md`, `chat_extras.md`, `chat_response_parsing.md`, `quantization/gguf.md`), CLI source `src/transformers/cli/serve.py`.
- link: https://github.com/huggingface/transformers
- license: Apache-2.0
- version: 5.20.0 (released 2026-10-15); major version 5 since 5.0.0 (2026-01-26). Requires Python >= 3.10.
- platforms: Windows native (pip; "OS Independent"), macOS native, Linux native; accelerator support follows the installed PyTorch build.
- what we use:
  - `pip install "transformers[serving]"`; `transformers serve [<model>]`, default `http://localhost:8000`; models load on demand and unload after `--model-timeout` seconds idle (default 300; `-1` disables).
  - Endpoints: `/v1/chat/completions` (text, image, audio, video), `/v1/completions`, `/v1/responses`, `/v1/audio/transcriptions`, `/v1/models`, `/v1/systemone`, `POST /load_model` (SSE events: `status` loading/ready/error, `stage` processor/config/download/weights, `progress{current,total}`, `cached`).
  - Flags: `--continuous-batching` (paged attention; `--cb-block-size`, `--cb-num-blocks`, `--cb-max-batch-tokens`, `--cb-max-memory-percent`, `--cb-use-cuda-graph`), `--attn-implementation`, `--compile`, `--quantization bnb-4bit|bnb-8bit` (runtime; pre-quantized hub checkpoints load as-is), `--reasoning on|off` (sets `enable_thinking`), `--chat-template-kwargs '<json>'`, `--device auto|cpu|cuda:0`, `--dtype auto`, `--trust-remote-code`, `--host`, `--port`, `--enable-cors`, `--default-seed`, `--decision-config`.
  - Tool calling: chat-completions-style `tools`; works for models whose tokenizer declares tool-call tokens (docs: Qwen and Gemma 4 out of the box).
  - Reasoning: chat-completions returns `reasoning_content`; responses returns a `reasoning` output item before `message`; needs a tokenizer `response_template` or a built-in per-architecture fallback, otherwise raw text stays in `content`.
  - Continuous batching reuses KV blocks for shared prefixes by default (requires paged-compatible attention: flash-attention variants, sdpa, eager).
  - Template and parsing helpers (engine-side or offline tooling, not the core): `apply_chat_template(messages, tools=..., add_generation_prompt=True)`, `get_json_schema(fn)`, `parse_response(text, prefix=...)` returning `{role, thinking, content, tool_calls}`, streaming `ResponseParser` events; GGUF loading via `from_pretrained(..., gguf_file=...)` (packed path only for Qwen3.5 / Qwen3.5 MoE on MPS; other architectures are dequantized at load).
- what we borrow: `/load_model` stage/progress events for `start(model)` progress reporting; idle-unload timer semantics; declarative response templates (regions for thinking/content/tool_calls) as the model for parsing output from raw text-completion endpoints.
- what we avoid: importing transformers in the core (run `transformers serve` as a separate process); serving GGUF through it (dequantization multiplies memory); treating it as a throughput engine (docs position it for "evaluation, experimentation, and moderate load" and point large deployments to dedicated engines).
- risks: 8 minor releases between 2026-08-10 and 2026-10-15, so pin; constrained decoding (`response_format`) not documented for `serve`; tool parsing limited to tokenizers with declared tool tokens.
- fallback suitability: suitable as the universal slow fallback for architecture coverage (anything the library loads, including CPU via `--device cpu`) when combined with the JSON fallback tool protocol plus schema validation and retry; not suitable as a default engine.
- sources: https://pypi.org/pypi/transformers/json ; https://raw.githubusercontent.com/huggingface/transformers/main/docs/source/en/serve-cli/serving.md ; https://raw.githubusercontent.com/huggingface/transformers/main/docs/source/en/serve-cli/serving_optims.md ; https://raw.githubusercontent.com/huggingface/transformers/main/src/transformers/cli/serve.py ; https://raw.githubusercontent.com/huggingface/transformers/main/docs/source/en/continuous_batching.md ; https://raw.githubusercontent.com/huggingface/transformers/main/docs/source/en/chat_templating.md ; https://raw.githubusercontent.com/huggingface/transformers/main/docs/source/en/chat_extras.md ; https://raw.githubusercontent.com/huggingface/transformers/main/docs/source/en/chat_response_parsing.md ; https://raw.githubusercontent.com/huggingface/transformers/main/docs/source/en/quantization/gguf.md ; https://raw.githubusercontent.com/huggingface/transformers/main/docs/source/en/_toctree.yml

#### ExLlamaV3
- verified: yes (2026-10-16), basis: PyPI JSON, release v1.6.2 and its assets (code host API), repo `README.md`, `pyproject.toml`, CI build workflow; TabbyAPI repo metadata, `README.md`, `pyproject.toml`, the two endpoint router modules, `config_sample.yml`.
- link: https://github.com/turboderp-org/exllamav3 (server: https://github.com/theroyallab/tabbyAPI)
- license: MIT (ExLlamaV3); AGPL-3.0 (TabbyAPI)
- version: 1.6.2 (released 2026-10-14). TabbyAPI has no tagged releases ("rolling release"; `pyproject.toml` version 0.0.1); its main branch pins exllamav3 1.6.2 wheels.
- platforms: Windows native (prebuilt `win_amd64` wheels; requires `triton-windows`); macOS no (CUDA and ROCm only); Linux native (CUDA wheels; ROCm 10.0 wheels for Python 3.12 to 3.14). TabbyAPI: Windows and Linux via pinned wheels; container images `latest` (CUDA 12.8), `cu13`, `latest-rocm`.
- what we use:
  - Engine only through TabbyAPI over HTTP: `POST /v1/chat/completions`, `POST /v1/completions`, `POST /v1/embeddings`, `GET /v1/models`, `GET /v1/model`, `POST /v1/model/load`, `POST /v1/model/unload`, `GET /v1/model/draft/list`, `GET /health`, `POST /v1/token/encode`, `POST /v1/token/decode`, template and sampling-override switch endpoints; a Kobold-style router also exists.
  - TabbyAPI config keys: `host: 127.0.0.1`, `port: 5000`, `disable_auth: false` (API keys on by default), `model_dir`, `inline_model_loading: false`, `max_seq_len`, `cache_mode: FP16` (quantized modes such as Q4), `tensor_parallel`, draft-model settings, reasoning parser (on by default; splits `reasoning_content` from `content`), chat-template kwargs (`enable_thinking`, `reasoning_effort`).
  - TabbyAPI features: chat-completions-style tool calling, JSON Schema + regex + EBNF constraints, speculative decoding via draft models, continuous batching with paged attention (Ampere or newer), embeddings, Jinja2 chat templates.
  - ExLlamaV3: EXL3 format (QTIP-based weight quantization), 2 to 8 bit KV cache quantization, tensor- and expert-parallel inference, CPU offloading for MoE models (AVX2/AVX-512), continuous dynamic batching, speculative decoding (1.6.2: DFlash2 drafters with TP), multimodal.
  - Wheels: `+cu128` (torch 2.8 to 2.11) and `+cu132` (torch 2.11 to 2.13), Python 3.10 to 3.14; built for CUDA arch `7.5 8.0 8.6 8.9 9.0 10.0 12.0+PTX` (includes sm_86). The PyPI package has no prebuilt extension (needs CUDA toolkit and build tools).
- what we borrow: explicit load/unload endpoints and an inline-loading toggle as a pattern for the engine interface; constraint-type capability flags (JSON Schema, regex, EBNF) in registry entries.
- what we avoid: bundling, vendoring or patching TabbyAPI (AGPL-3.0); exposing TabbyAPI beyond localhost (README: "not meant to run on production servers"); EXL3 as a default format (GPU-only, unusable on T0).
- risks: TabbyAPI has no versions (pin by commit); wheel matrix ties Python, torch and CUDA versions; TabbyAPI README calls it a "hobby project made for a small amount of users".
- sources: https://pypi.org/pypi/exllamav3/json ; https://api.github.com/repos/turboderp-org/exllamav3/releases?per_page=10 ; https://api.github.com/repos/turboderp-org/exllamav3/releases/tags/v1.6.2 ; https://api.github.com/repos/turboderp-org/exllamav3 ; https://raw.githubusercontent.com/turboderp-org/exllamav3/master/README.md ; https://raw.githubusercontent.com/turboderp-org/exllamav3/master/pyproject.toml ; https://raw.githubusercontent.com/turboderp-org/exllamav3/master/.github/workflows/build.yml ; https://api.github.com/repos/theroyallab/tabbyAPI ; https://raw.githubusercontent.com/theroyallab/tabbyAPI/main/README.md ; https://raw.githubusercontent.com/theroyallab/tabbyAPI/main/pyproject.toml ; https://raw.githubusercontent.com/theroyallab/tabbyAPI/main/endpoints/OAI/router.py ; https://raw.githubusercontent.com/theroyallab/tabbyAPI/main/endpoints/core/router.py ; https://raw.githubusercontent.com/theroyallab/tabbyAPI/main/config_sample.yml

#### llama-swap
- verified: yes (2026-10-16), basis: release list and v265 assets, repo metadata, `README.md`, `docs/README.md`, `config-schema.json`.
- link: https://github.com/mostlygeek/llama-swap
- license: MIT
- version: v265 (released 2026-10-14); build-number versions, 10 releases from v256 (2026-09-17) to v265.
- platforms: Windows native (`llama-swap_265_windows_amd64.zip`; WinGet package maintained by a community contributor, not official); macOS native (darwin amd64/arm64; Homebrew, MacPorts); Linux native (amd64/arm64; container images with llama-server builds for CUDA 12, CUDA 13, Vulkan); FreeBSD amd64. Single Go binary.
- what we use (only when a user already runs it; then it is an upstream endpoint):
  - Proxied formats: chat-completions (`v1/chat/completions`), `v1/completions`, responses (`v1/responses`), `v1/embeddings`, `v1/models`, audio and image endpoints; messages (`v1/messages`, `v1/messages/count_tokens`); llama-server native `/completion`, `/infill`, `/rerank`, `/props?model=`.
  - Management: `GET /running`, `POST /api/models/unload`, `POST /api/models/unload/:model_id`, `/upstream/:model_id`, `GET /health` ("OK"), `GET /metrics` (system and GPU metrics for Prometheus), `/logs`, `/logs/stream/{model_id}`, `/ui`, `/api/profiles`, `/api/mcp`. CLI: `llama-swap -config config.yaml -listen localhost:8080`.
  - Config (YAML; JSON schema in repo). Top level: `models`, `macros`, `groups`, `matrix`, `routing` (`router.use: group|matrix`, `scheduler.use: fifo`; must not be mixed with legacy `groups`/`matrix`), `hooks.on_startup`, `profiles`, `selectors`, `peers`, `apiKeys` (empty means no auth), `healthCheckTimeout` (default 120 s), `globalTTL` (default 0 = never unload), `unloadTimeout`, `globalConcurrencyLimit`, `startPort` (default 5800), `sendLoadingState`, `includeAliasesInList`, `logLevel`, `metricsMaxInMemory`.
  - Per model: `cmd` (with `${PORT}`), `cmdStop` (`${PID}`), `proxy` (default `http://localhost:${PORT}`), `ttl` (default -1 = use `globalTTL`; 0 disables), `checkEndpoint` (default `/health`; `none` skips; a JSON body `{"progress":..,"message":..}` reports load progress and restarts the timeout), `aliases`, `env`, `useModelName`, `filters` (`stripParams`, `setParams`, `setParamsByID`), `concurrencyLimit` (0 = internal default 10; excess gets HTTP 429), `unloadTimeout`, `unlisted`, `capabilities`, `metadata`, `macros`, `name`, `description`.
  - `groups.<name>`: `members`, `swap` (default true: one model at a time), `exclusive` (default true: unload other groups), `persistent` (default false). `matrix`: `vars`, `sets` (DSL with `&`, `|`, `()`, `+ref`, `+auto:orphans`), `evict_costs`.
  - Swap mechanics: the proxy reads the request's `model`, finds its config, stops a conflicting upstream if one runs, starts `cmd`, polls `checkEndpoint` until ready or `healthCheckTimeout`, then forwards; TTL unloads idle models.
- what we borrow (comparison with SPEC §5.3):
  - lazy start on first request: present (by model ID).
  - keep-alive TTL: present (`ttl`, `globalTTL`; default never unload).
  - LRU eviction when memory is tight: absent (no memory accounting; eviction by group rules or matrix `evict_costs`).
  - health checks: present (`checkEndpoint`, progress JSON, `healthCheckTimeout`).
  - hot-swap: present (process stop/start per model).
  - metrics / prefix_cache_stats / slot save-restore: system and GPU `/metrics` only; none of the others.
  - borrow: config shape (`cmd` + `${PORT}` + `proxy` + `checkEndpoint` + `ttl` + `aliases` + `cmdStop`), progress-reporting readiness probe, `evict_costs` as an eviction tie-breaker, `/running` and per-model unload endpoints, per-model concurrency limit with 429.
- what we avoid: `sendLoadingState` (injects loading text into the reasoning field, which would corrupt reasoning normalization); `peers` in zero-egress mode (forwards requests to remote servers); managing an engine that llama-swap already manages.
- risks: high release cadence (pin a version); docs replaced by a knowledge base read by a built-in agent (`docs/kb/`), `config.example.yaml` moved to `docs/config.example.yaml`; WinGet package unofficial.
- sources: https://api.github.com/repos/mostlygeek/llama-swap/releases?per_page=10 ; https://api.github.com/repos/mostlygeek/llama-swap ; https://github.com/mostlygeek/llama-swap/releases/expanded_assets/v265 ; https://raw.githubusercontent.com/mostlygeek/llama-swap/main/README.md ; https://raw.githubusercontent.com/mostlygeek/llama-swap/main/docs/README.md ; https://raw.githubusercontent.com/mostlygeek/llama-swap/main/config-schema.json ; https://raw.githubusercontent.com/mostlygeek/llama-swap/main/config.example.yaml

#### TensorRT-LLM
- verified: yes (2026-10-16), basis: PyPI JSON, release list (code host API), repo `LICENSE`, docs sources (`docs/source/commands/trtllm-serve/trtllm-serve.rst`, `docs/source/installation/installation-guide.md`, `docs/source/installation/index.rst`, `docs/source/supported-hardware.md`, `docs/source/overview.md`, `docs/source/release-notes.md`), server and CLI source on main (see sources).
- link: https://github.com/NVIDIA/TensorRT-LLM
- license: Apache-2.0 (LICENSE states portions derive from other projects under their own licenses; repo metadata reports NOASSERTION)
- version: 1.2.1 (released 2026-04-20) is the latest stable; 1.3.0 is still at release candidates (rc29, 2026-09-29); 1.4.0rc0 on PyPI 2026-10-13.
- platforms: Windows no ("Windows platform support is deprecated as of v0.18.0. All Windows-related code and functionality will be completely removed in future releases"; current install docs cover prebuilt release containers, "Install on Linux via pip" and source builds); macOS no; Linux yes (pip path tested on Ubuntu 24.04 with CUDA Toolkit 13.4 and torch 2.14.0 cu132).
- what we use: nothing in v1. A user-run `trtllm-serve` is reachable through generic codecs: documented `/v1/models`, `/v1/completions`, `/v1/chat/completions`, responses format, `/health`, `/metrics` (beta), `/version`; main-branch source also routes `/v1/messages` (+ `count_tokens`, batches), `/v1/embeddings`, `/kv_cache_events`. Flags include `--tool_parser`, `--reasoning_parser`, `--kv_cache_dtype`, `--tp_size`, `--pp_size`, `--ep_size`, `--config <yaml>`, `--set PATH=VALUE` (precedence: defaults < `--config` < CLI flags < `--set`).
- what we borrow: `/kv_cache_events` and iteration statistics in `/metrics` as references for `prefix_cache_stats`; the layered config precedence.
- what we avoid: a dedicated adapter or any import of its Python package.
- risks: supported-hardware page lists A100 as the only Ampere part (RTX A6000, sm_86, is not listed); FP8 paths need Hopper or later and FP4 Blackwell; main-branch release notes list breaking changes under Release 1.2 (TensorRT backend removed, PyTorch sole backend; two-model speculative decoding removed); slow stable cadence.
- recommendation: drop from the SPEC §4.2 optional engines; cover user-run servers via the chat-completions, responses and messages codecs.
- sources: https://pypi.org/pypi/tensorrt-llm/json ; https://api.github.com/repos/NVIDIA/TensorRT-LLM/releases?per_page=100&page=1 ; https://api.github.com/repos/NVIDIA/TensorRT-LLM ; https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/LICENSE ; https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/commands/trtllm-serve/trtllm-serve.rst ; https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/installation/installation-guide.md ; https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/installation/index.rst ; https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/supported-hardware.md ; https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/overview.md ; https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/release-notes.md ; https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/tensorrt_llm/serve/openai_server.py ; https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/tensorrt_llm/commands/serve.py

### API formats

#### chat-completions format
- verified: yes (2026-10-16), basis: the developers reference page for the chat resource (endpoint, parts, tools, tool_choice, response_format, stream_options, chunk and usage shapes) and the generated SDK type files (field lists, enums, docstrings). The older platform reference URL returned HTTP 403; the method subpage returned 404 to the fetcher.
- link: https://developers.openai.com/api/reference/resources/chat
- request: `messages` (required), `model` (required), `audio{format: wav, aac, mp3, flac, opus, pcm16; voice}`, `frequency_penalty`, `function_call` and `functions` (deprecated), `logit_bias`, `logprobs`, `max_completion_tokens`, `max_tokens` (deprecated), `metadata`, `modalities` (`text`, `audio`), `moderation{model, policy{input{mode: score, block}, output{mode}}}`, `n`, `parallel_tool_calls`, `prediction`, `presence_penalty`, `prompt_cache_key`, `prompt_cache_options{mode: implicit, explicit; ttl: "30m"}`, `prompt_cache_retention` (deprecated, `in_memory`, `24h`), `reasoning_effort`, `response_format`, `safety_identifier`, `seed`, `service_tier` (`auto`, `default`, `flex`, `scale`, `priority`, `fast`), `stop` (up to 4; not supported with `o3`, `o4-mini`), `store`, `stream`, `stream_options{include_usage, include_obfuscation}`, `temperature`, `tool_choice`, `tools`, `top_logprobs`, `top_p`, `user` (deprecated), `verbosity` (`low`, `medium`, `high`), `web_search_options{search_context_size, user_location}`. Messages: developer `{role, content (text parts only), name}`; system and user `{role, content, name}`; assistant `{role, content (string, text or refusal parts, or null; required unless tool_calls/function_call), refusal, name, audio{id}, tool_calls, function_call}`; tool `{role: "tool", tool_call_id, content (string or text parts)}`; function `{role: "function", name, content}`. Every content part may carry `prompt_cache_breakpoint{mode: "explicit"}` (TTL from `prompt_cache_options.ttl`).
- response: `id`, `object: "chat.completion"`, `created`, `model`, `choices[]{index, message, finish_reason, logprobs{content, refusal}}`, `usage`, `service_tier`, `system_fingerprint`, `moderation`, `metadata`. Message: `role: "assistant"`, `content`, `refusal`, `annotations[{type: "url_citation", url_citation{start_index, end_index, title, url}}]`, `audio`, `function_call` (deprecated), `tool_calls`.
- streaming: SSE; chunk `{id, object: "chat.completion.chunk", created, model, choices[]{index, delta, finish_reason, logprobs}, usage, service_tier, system_fingerprint, moderation, obfuscation}`; delta `{role, content, refusal, tool_calls[]{index, id, type: "function", function{name, arguments}}, function_call, audio{id, data, expires_at, transcript}}`; `usage` is null except in the last chunk when `stream_options.include_usage` is true; `obfuscation` omitted when `include_obfuscation` is false. The `data: [DONE]` terminator is stated in the `/v1/completions` stream docstring; the chat stream docstring was not re-read today.
- tool calls: function tools `{type: "function", function{name, description, parameters, strict}}`; custom tools `{type: "custom", custom{name, description, format: {type: "text"} or {type: "grammar", grammar{definition, syntax: lark, regex}}}}`; `tool_choice`: `none`, `auto`, `required`, `{type: "function", function{name}}`, `{type: "custom", custom{name}}`, `{type: "allowed_tools", allowed_tools{mode: auto, required; tools}}`; calls `{id, type: "function", function{name, arguments}}` or `{id, type: "custom", custom{name, input}}`; arguments are a JSON string that "may be invalid"; parallel calls are several entries in `tool_calls`, controlled by `parallel_tool_calls`.
- reasoning: `reasoning_effort` (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`; not all models support every value). The hosted message schema has no reasoning text field; only `completion_tokens_details.reasoning_tokens`. Open servers add a non-standard field: `reasoning_content` (llama.cpp), `reasoning` (vLLM; formerly `reasoning_content`). Nothing to pass back across turns for the hosted reference.
- usage: see table; `prompt_tokens_details.cache_write_tokens` is new and pairs with `prompt_cache_options`.
- errors: body `{"error": {...}}` with `type`, `code`, `param` (referenced by the error-codes guide; full wrapper shown in the Open Responses spec); 400 (`invalid_request_error`, e.g. invalid `service_tier`), 401, 403 (unsupported region), 429 (`rate_limit_error` + `slow_down`; `credit_balance_exhausted`, `organization_spend_limit_exceeded`, `project_spend_limit_exceeded`, `organization_usage_limit_exceeded`; `insufficient_quota` type), 500, 503 (`service_unavailable_error` + `server_is_overloaded`). Honour `Retry-After`; otherwise exponential backoff with jitter. Headers: `x-request-id`, `openai-processing-ms`, `openai-version`, `openai-organization`, `x-ratelimit-limit-requests`, `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-requests`, `x-ratelimit-remaining-tokens`, `x-ratelimit-reset-requests`, `x-ratelimit-reset-tokens`, plus `x-ratelimit-*-project-tokens` variants.
- risks: `max_tokens` is deprecated in the hosted reference but is the only length field Ollama's compat layer lists; effort enums diverge across servers; new hosted-only fields (`moderation`, `prompt_cache_options`, `prompt_cache_breakpoint`, `obfuscation`, custom tools) are absent from open servers; reasoning text field name differs per server; docs moved from the platform domain to the developers domain.
- sources: https://developers.openai.com/api/reference/resources/chat ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/chat/completion_create_params.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/chat/chat_completion.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/chat/chat_completion_chunk.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/chat/chat_completion_message.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/chat/chat_completion_content_part_param.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/completion_usage.py ; https://developers.openai.com/api/docs/guides/error-codes ; https://developers.openai.com/api/reference/overview ; https://www.openresponses.org/specification ; https://platform.openai.com/docs/api-reference/chat/create (HTTP 403)

#### responses format
- verified: yes (2026-10-16), basis: generated SDK type files (request, response, items, events, usage, errors, reasoning), the developers reference pages for the responses resource and its streaming events (first 100k characters of 5 MB and 2 MB pages), the streaming and WebSocket guides.
- link: https://developers.openai.com/api/reference/resources/responses
- request: `access_programs`, `background`, `context_management[{type: "compaction", compact_threshold}]`, `conversation`, `include` (`web_search_call.action.sources`, `code_interpreter_call.outputs`, `computer_call_output.output.image_url`, `file_search_call.results`, `message.input_image.image_url`, `message.output_text.logprobs`, `reasoning.encrypted_content`), `input`, `instructions` (not carried over with `previous_response_id`), `max_output_tokens`, `max_tool_calls` (across all built-in tool calls), `metadata`, `model`, `moderation`, `parallel_tool_calls`, `previous_response_id`, `prompt`, `prompt_cache_key`, `prompt_cache_options{comparison_response_id, mode, prewarm, ttl: "30m"}`, `prompt_cache_retention` (deprecated), `reasoning`, `safety_identifier`, `service_tier`, `store`, `stream`, `stream_options{include_obfuscation}`, `temperature`, `text`, `tool_choice`, `tools`, `top_logprobs` (0 to 20), `top_p`, `truncation` (`auto`, `disabled`), `user` (deprecated). Input message roles `user`, `assistant`, `system`, `developer`; assistant input messages carry `phase`: `commentary` or `final_answer`. Parts `input_text`, `input_image{detail: low, high, auto, original}`, `input_file{detail: auto, low, high}`. Input item union has 33 members, including `function_call`, `function_call_output{call_id, output, id, status}`, `reasoning`, `compaction`, `compaction_trigger`, `item_reference{id}`, `custom_tool_call`, `custom_tool_call_output`, shell, apply-patch, MCP, tool-search and program items.
- response: `id`, `object: "response"`, `created_at`, `completed_at`, `status`, `output[]` (28 item types: `message`, `function_call`, `function_call_output`, `reasoning`, `compaction`, `program`, `program_output`, `additional_tools`, `image_generation_call`, `local_shell_call`, `mcp_call`, `mcp_list_tools`, `mcp_approval_request`, file-search, web-search, computer, code-interpreter, shell, apply-patch, custom-tool and tool-search items), `incomplete_details.reason`, `error`, `usage`, `prompt_cache_diagnostics`, echo of request settings. SDK property `output_text` concatenates `output_text` parts.
- streaming: SSE; 59 event types in the SDK union. Lifecycle `response.created`, `response.queued`, `response.in_progress`, `response.completed`, `response.failed`, `response.incomplete`; items `response.output_item.added`/`.done`, `response.content_part.added`/`.done`; text `response.output_text.delta`/`.done`, `response.output_text.annotation.added`, `response.refusal.delta`/`.done`; tools `response.function_call_arguments.delta` then `.done{item_id, output_index, arguments, sequence_number}`, custom-tool input delta/done events (SDK classes `ResponseCustomToolCallInputDeltaEvent`, `ResponseCustomToolCallInputDoneEvent`; wire names not fetched), MCP and built-in tool progress events; reasoning `response.reasoning_text.delta{item_id, output_index, content_index, delta, sequence_number}`, `response.reasoning_summary_part.added`/`.done`, `response.reasoning_summary_text.delta`/`.done`; `response.compaction.compacting`; `error`. Wire names `response.queued`, `response.output_text.annotation.added`, `response.refusal.*` and `response.reasoning_summary_*` were read from the Open Responses reference and match the SDK class names; the other names above were read from hosted pages or SDK literals. Usage arrives inside the `response` object of lifecycle events. Use the item from `response.output_item.done`: `encrypted_content` in `.added` "may be incomplete".
- tool calls: function tool `{type: "function", name, parameters, strict, description, defer_loading, allowed_callers: direct, programmatic; output_schema}`; built-in tool types `file_search`, `computer`, `computer_use_preview`, `web_search`, `web_search_preview`, `mcp`, `code_interpreter`, `image_generation`, `local_shell`, `shell`, `custom`, `apply_patch`, `tool_search`, `programmatic_tool_calling`, `namespace`; `tool_choice` union of options, allowed-tools, built-in types, function, MCP, custom, programmatic, apply-patch, shell. Call item `{type: "function_call", id, call_id, name, arguments, status, namespace, caller{type: direct or program}}`; result item `{type: "function_call_output", call_id, output}` where `output` is a string or a list of parts.
- reasoning: `reasoning{effort, summary: auto, concise, detailed; generate_summary (deprecated); context: auto, current_turn, all_turns; mode: standard, pro}`. Item `{type: "reasoning", id, summary[{type: "summary_text", text}], content[{type: "reasoning_text", text}], encrypted_content, status}`; docstring: include reasoning items in `input` for later turns when managing context manually; `encrypted_content` is populated by default and "especially important when `store` is `false`".
- usage: `input_tokens`, `input_tokens_details{cached_tokens, cache_write_tokens}`, `output_tokens`, `output_tokens_details{reasoning_tokens}`, `total_tokens`.
- errors: HTTP codes and headers as chat-completions. `Response.error{code, message, param, type, misalignment}`; `code`: `server_error`, `rate_limit_exceeded`, `invalid_prompt`, `data_residency_mismatch`, `bio_policy`, `misalignment_policy_violation`, `vector_store_timeout`, `invalid_image`, `invalid_image_format`, `invalid_base64_image`, `invalid_image_url`, `image_too_large`, `image_too_small`, `image_parse_error`, `image_content_policy_violation`, `invalid_image_mode`, `image_file_too_large`, `unsupported_image_media_type`, `empty_image_file`, `failed_to_download_image`, `image_file_not_found`.
- risks: fastest-moving format (new item and event types appear often; unknown types must be tolerated); stateful features (`previous_response_id`, `conversation`, `background`, `store`) are server-side; local engines implement it statelessly (Ollama: `previous_response_id`, `conversation`, `truncation` unsupported; llama.cpp converts requests to chat completions); reasoning event names differ from Open Responses (`response.reasoning_text.delta` vs `response.reasoning.delta`).
- sources: https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_create_params.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_usage.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_stream_event.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_reasoning_text_delta_event.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_function_call_arguments_done_event.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_reasoning_item.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_output_item.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_input_item_param.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_function_tool_call.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/function_tool_param.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_text_config_param.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_format_text_json_schema_config_param.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_error.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/shared/reasoning.py ; https://developers.openai.com/api/reference/resources/responses ; https://developers.openai.com/api/reference/resources/responses/streaming-events ; https://developers.openai.com/api/docs/guides/streaming-responses ; https://developers.openai.com/api/docs/guides/websocket-mode

#### messages format
- verified: yes (2026-10-16), basis: messages API reference (first 100k characters of 1.18 MB), streaming guide (full), errors page (full), rate-limits page (full), generated SDK type files. `temperature`, `top_p`, `top_k` were not in the fetched reference excerpt; they are confirmed only by the engine compat pages (Ollama, llama.cpp).
- link: https://platform.claude.com/docs/en/api/messages
- request: `model`; `max_tokens` (min 0; `0` populates the prompt cache without generating); `messages` (max 100,000; roles `user`, `assistant`); `system` (string or text blocks); `metadata{user_id}`; `stop_sequences`; `stream`; `tools`; `tool_choice`; `thinking`; `output_config{effort, format}`; `service_tier` (`auto`, `standard_only`); `speed` (`standard`, `fast`); `container`; top-level `cache_control{type: "ephemeral", ttl: 5m or 1h}`; `inference_geo`; `diagnostics{previous_message_id}` (returns `diagnostics.cache_miss_reason`). Request block types: `text`, `image` (source base64, url, file), `document` (base64 PDF, text, content, url, file), `search_result`, `thinking`, `redacted_thinking`, `tool_use`, `tool_result`, `server_tool_use`, `web_search_tool_result`, `web_fetch_tool_result`, `code_execution_tool_result`, `bash_code_execution_tool_result`, `text_editor_code_execution_tool_result`, `tool_search_tool_result`, `container_upload`. Headers: `x-api-key`, `anthropic-version: 2023-06-01`, `content-type: application/json`, `anthropic-beta` for betas.
- response: `id`, `type: "message"`, `role: "assistant"`, `content`, `model`, `stop_reason` (null only in streamed `message_start`), `stop_sequence`, `stop_details`, `usage`, `container`, `diagnostics`.
- streaming: SSE; each event has an `event:` name equal to the JSON `type`. Order: `message_start` (Message with empty `content`), per block `content_block_start`, `content_block_delta`*, `content_block_stop` (with `index`), one or more `message_delta` (`delta{stop_reason, stop_sequence}`, cumulative `usage`), `message_stop`; `ping` anywhere; `event: error` mid-stream (e.g. `overloaded_error`). Delta union: `text_delta`, `input_json_delta{partial_json}` (accumulate and parse at `content_block_stop`), `thinking_delta`, `signature_delta` (sent just before `content_block_stop`), `citations_delta`. `eager_input_streaming` per tool enables fine-grained input streaming. New event types may be added; ignore unknown ones. During server-side fallback a `fallback` block arrives as a start/stop pair.
- tool calls: `tools[]{name, input_schema{type: "object", properties, required}, description, strict, cache_control, eager_input_streaming, defer_loading, allowed_callers, input_examples, type: "custom"}` plus server tools; `tool_choice{type: auto, any, tool (+name), none; disable_parallel_tool_use}`; `tool_use{id, name, input}`; `tool_result{tool_use_id, content (string or text, image, search_result, document, tool_reference, browser_state blocks), is_error, cache_control, toolset_name}`. Parallel calls = several `tool_use` blocks in one assistant turn. Several current models reject `tool_choice` `any` and `tool` with 400.
- reasoning: `thinking.type` `adaptive` (`display`), `enabled` (`budget_tokens` >= 1024 and < `max_tokens`, `display`), `disabled`, `between_tools`; `display`: `summarized`, `omitted` (beta `updates`); `output_config.effort`: `low`, `medium`, `high`, `xhigh`, `max`. Blocks `thinking{thinking, signature}` and `redacted_thinking{data}` (opaque). With tool use, every thinking block of the assistant turn must be passed back unchanged, or the request fails with 400. On the newest models replayed blocks are bound to an unchanged prefix (`system`, `tools`, earlier messages); beta `thinking.block_binding.prefix_mismatch_behavior` (`error`, `drop_block`) with header value `thinking-binding-controls-2026-08-01`. `thinking.type: enabled` returns 400 on current models (use `adaptive` + effort); some models reject `disabled`.
- usage: `input_tokens` (tokens after the last cache breakpoint only), `cache_creation_input_tokens`, `cache_read_input_tokens`, `cache_creation` (by TTL), `output_tokens` (inclusive billing total), `output_tokens_details`, `server_tool_use`, `service_tier` (`standard`, `priority`, `batch`), `inference_geo`, `speed`. Total input = `cache_read_input_tokens + cache_creation_input_tokens + input_tokens`.
- errors: see table. Spend-cap 429 has `error.details.error_code: "enforced_spend_limit_reached"` and no `retry-after`. Request size limits: 32 MB (messages, token counting), 256 MB (batch), 500 MB (files); 413 `request_too_large`. Response header `request-id`; body `request_id`. Rate headers: `retry-after`, `anthropic-ratelimit-{requests,tokens,input-tokens,output-tokens}-{limit,remaining,reset}`, `anthropic-priority-{input,output}-tokens-{limit,remaining,reset}`.
- risks: assistant prefill returns 400 on newer models; forced tool choice unsupported on several models; thinking type rules differ per model; beta headers gate new fields; `input_tokens` semantics differ from the other formats (excludes cache).
- sources: https://platform.claude.com/docs/en/api/messages ; https://platform.claude.com/docs/en/build-with-claude/streaming ; https://platform.claude.com/docs/en/api/errors ; https://platform.claude.com/docs/en/api/rate-limits ; https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/stop_reason.py ; https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/usage.py ; https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/message.py ; https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/raw_content_block_delta.py ; https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/tool_param.py ; https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/tool_result_block_param.py ; https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/redacted_thinking_block.py

#### generate-content format
- verified: yes (2026-10-16), basis: generate-content API reference (all 174,589 characters read in three passes), caching reference (ToolConfig), legacy thinking and function-calling guides, troubleshooting and API-errors pages. Not verified today: the GenerationConfig definition (not on the reference page; linked elsewhere), so `responseJsonSchema`, `seed`, `presencePenalty`, `frequencyPenalty`, `responseLogprobs`, `logprobs`, `responseModalities` are unconfirmed; the error body shape for this endpoint.
- link: https://ai.google.dev/api/generate-content
- request: `contents[]`, `tools[]`, `toolConfig{functionCallingConfig, retrievalConfig, includeServerSideToolInvocations}`, `safetySettings[]`, `systemInstruction` (Content; text only), `generationConfig`, `cachedContent`, `continuationToken` (resume after `finishReason: CONTINUATION`; `contents` must replicate the original; `maxOutputTokens` is cumulative across hops), `serviceTier`, `store`, `labels`. Reference shell examples use snake_case keys (`inline_data`, `mime_type`, `file_data`, `file_uri`, `system_instruction`, `function_declarations`, `tool_config`, `function_calling_config`); definitions and JS use camelCase. `generationConfig` keys seen in examples: `candidateCount`, `stopSequences`, `maxOutputTokens`, `temperature`, `topP`, `topK`, `thinkingConfig`.
- response: `candidates[]`, `promptFeedback{blockReason: BLOCK_REASON_UNSPECIFIED, SAFETY, OTHER, BLOCKLIST, PROHIBITED_CONTENT, IMAGE_SAFETY; safetyRatings}`, `usageMetadata`, `modelVersion`, `responseId`, `modelStatus`. `finishReason`: `FINISH_REASON_UNSPECIFIED`, `STOP`, `MAX_TOKENS`, `SAFETY`, `RECITATION`, `LANGUAGE`, `OTHER`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`, `MALFORMED_FUNCTION_CALL`, `IMAGE_SAFETY`, `IMAGE_PROHIBITED_CONTENT`, `IMAGE_OTHER`, `NO_IMAGE`, `IMAGE_RECITATION`, `UNEXPECTED_TOOL_CALL`, `TOO_MANY_TOOL_CALLS`, `MISSING_THOUGHT_SIGNATURE`, `MALFORMED_RESPONSE`, `ESCALATION`, `PUP_LIMITED_DISABLED`, `CONTINUATION`.
- streaming: `:streamGenerateContent` with `alt=sse`; body is "a stream of GenerateContentResponse instances" (each chunk is a partial response with candidates; usage on chunks). Whether a function call can be split across chunks was not stated on the fetched pages.
- tool calls: declarations `{name, description, parameters{type, properties, required}}` inside `tools[].functionDeclarations`; built-in tools named on fetched pages: code execution, search grounding, URL context, file search. `functionCallingConfig.mode`: `MODE_UNSPECIFIED`, `AUTO`, `ANY`, `NONE`, `VALIDATED` (`VALIDATED` is the default when built-in tools or structured output are also enabled); `allowedFunctionNames` only with `ANY` or `VALIDATED`. Call part `functionCall{id, name, args}`; result part `functionResponse{id, name, response, parts}` (`parts` for multimodal results on newer models) sent in a content with role `user`. Results are matched by `id` (the guide also says to keep request order; it contradicts itself). No parallel-calls flag.
- reasoning: `thinkingConfig{thinkingLevel (minimal, low, medium, high; required on the newest family, which cannot disable thinking), thinkingBudget (deprecated on the newest family; -1 dynamic, 0 off where allowed; sending both returns 400), includeThoughts}`. Parts with `thought: true` are thought summaries. `thoughtSignature` (on any part on newer models) must be returned in its original part, unmerged; "mandatory for function calling"; `MISSING_THOUGHT_SIGNATURE` is a finish reason.
- usage: see table; thinking tokens in `thoughtsTokenCount` count against `maxOutputTokens`.
- errors: troubleshooting page: retry 429 `RESOURCE_EXHAUSTED`, 503 `UNAVAILABLE`, 408 and 5xx with exponential backoff and jitter; do not retry 400, 402, 403. The API-errors page documents `{"error": {"code": "<snake_case>", "message"}}` with codes `invalid_request` 400, `failed_precondition` 400, `out_of_range` 416, `parameter_unknown` 400, `authentication` 401, `payment_required` 402, `permission_denied` 403, `not_found` 404, `model_not_found` 404, `already_exists` 409, `aborted` 409, `rate_limit_exceeded` 429, `quota_exceeded` 429, `too_many_requests` 429, `cancelled` 499, `api_error` 500, `unimplemented` 501, `service_unavailable` 503, `deadline_exceeded` 504, and stream errors with `"event_type": "error"`; that page sits beside the interactions docs and its applicability to generate-content was not confirmed.
- risks: guides for this format are now titled "Gemini Generate Content API (Legacy)"; the successor (interactions format) is GA; structured-output naming is in flux (the legacy structured-output guide fetched today shows `responseFormat{mimeType, schema}` in REST/JS and `ResponseMIMEType`/`ResponseJsonSchema` in Go); camelCase and snake_case keys both appear; finish-reason enum grows over time.
- sources: https://ai.google.dev/api/generate-content ; https://ai.google.dev/api/caching ; https://ai.google.dev/gemini-api/docs/generate-content/thinking ; https://ai.google.dev/gemini-api/docs/generate-content/function-calling ; https://ai.google.dev/gemini-api/docs/generate-content/structured-output ; https://ai.google.dev/gemini-api/docs/troubleshooting ; https://ai.google.dev/gemini-api/docs/api-errors ; https://ai.google.dev/gemini-api/docs/thought-signatures (moved notice) ; https://ai.google.dev/gemini-api/docs/interactions-overview

#### Ollama native API
- verified: yes (2026-10-16), basis: api.md in the repo (says docs are moving to docs.ollama.com/api) and the docs.ollama.com pages for chat, streaming, errors, chat-completions compat and messages compat. The docs are not version-pinned; behaviour of 0.34.4 specifically was not checked.
- link: https://docs.ollama.com/api/chat
- request: `/api/chat`: `model`, `messages`, `tools`, `format`, `options`, `stream`, `think`, `keep_alive` (string such as `5m` or number, `0` unloads), `logprobs`, `top_logprobs`. `/api/generate`: `model`, `prompt`, `suffix`, `images`, `think`, `format`, `options`, `system`, `template`, `stream`, `raw`, `keep_alive`, `context` (deprecated). Options include `num_ctx`, `num_predict`, `temperature`, `top_k`, `top_p`, `min_p`, `stop`, `seed`, `repeat_penalty`, `num_keep`, `num_batch`, `num_gpu`, `num_thread`, `presence_penalty`, `frequency_penalty` (`typical_p` deprecated). Images are base64 strings inside messages.
- response: chat `{model, created_at, message{role: "assistant", content, thinking, tool_calls, images}, done, done_reason, total_duration, load_duration, prompt_eval_count, prompt_eval_cached_count, prompt_eval_duration, eval_count, eval_duration, logprobs}`; generate returns `response` instead of `message`. Throughput = `eval_count / eval_duration * 10^9`.
- streaming: NDJSON, content type `application/x-ndjson`; every chunk has `model`, `created_at`, `done`; final chunk adds `done_reason` and metrics. Whether tool calls arrive whole per chunk is not stated (on 0.34.4 they do, one call per line; see "Verified during Phase 1").
- tool calls: see table. No call `id` and no `index` in the documented schema (superseded: current servers send both, see "Verified during Phase 1"); `arguments` is an object; the result is a `tool` role message with `tool_name`.
- reasoning: `think` true, false, null (model default) or a model-defined string matching `/api/show`; output in `thinking`. Compat-layer effort mapping: models without metadata map `minimal` to `low` and `xhigh` or `ultra` to `max`; gpt-oss maps `minimal` to `low`, `xhigh` or `ultra` to `high`, `none` to no thinking output.
- usage: counts and durations (ns) listed above; no reasoning-token count.
- errors: `{"error": "..."}`; 400, 404, 429, 500, 502; mid-stream errors are an NDJSON object with `error` and the original status.
- compat layers: chat-completions at `/v1/chat/completions` (supports `model`, `messages`, `frequency_penalty`, `presence_penalty`, `response_format`, `seed`, `stop`, `stream`, `stream_options.include_usage`, `temperature`, `top_p`, `max_tokens`, `tools`, `reasoning_effort`, `reasoning.effort`; not `tool_choice`, `logit_bias`, `user`, `n`, logprobs; image URLs not supported), `/v1/completions` (prompt string only, `suffix`; not `best_of`, `echo`, `logit_bias`, `user`, `n`), `/v1/embeddings`, `/v1/models`, `/v1/models/{model}`; responses at `/v1/responses` (stateless: `previous_response_id`, `conversation`, `truncation` unsupported; extension `think`); messages at `/v1/messages` (not `tool_choice`, `metadata`, `cache_control`, `count_tokens`, batches, citations, PDF documents; `budget_tokens` accepted but not enforced; token counts approximate; the local server validates no key).
- risks: tool-call ids undocumented (current servers send them, older ones do not); `tool_choice` unsupported in every compat layer; effort names are per model; docs migrating from the repo to docs.ollama.com.
- sources: https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md ; https://docs.ollama.com/api/chat ; https://docs.ollama.com/api/streaming ; https://docs.ollama.com/api/errors ; https://docs.ollama.com/api/openai-compatibility ; https://docs.ollama.com/api/anthropic-compatibility

#### llama.cpp native API
- verified: yes (2026-10-16), basis: tools/server/README.md on master (read in two passes).
- link: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- request: `/completion` options listed in the table plus `dynatemp_range`, `dry_multiplier`, `mirostat`, `logit_bias`, `seed`, `ignore_eos`, `n_keep`, `n_cmpl`, `typical_p`, `repeat_penalty`, `xtc_probability`, `t_max_predict_ms`, `timings_per_token`, `return_progress`, `sse_ping_interval`, `post_sampling_probs`. `/infill`: `input_prefix`, `input_suffix`, `input_extra[{filename, text}]`, `prompt` (after the FIM_MID token) plus all `/completion` options. `/tokenize`: `content`, `add_special` (false), `parse_special` (true), `with_pieces` (false) -> `tokens` (ids or `{id, piece}`). `/detokenize`: `tokens`. `/apply-template`: `messages` (chat-completions shape) -> `{prompt}`. `/v1/chat/completions/control`: `id`, `action: reasoning_end`, `model` (router mode) -> `success`, `message`.
- response: see table.
- streaming: SSE when `stream: true`.
- tool calls: chat-completions tool calling needs `--jinja` (default enabled; `--chat-template-file` or `--chat-template chatml` as fallback); `parallel_tool_calls` depends on the template; `/v1/messages` accepts `model`, `messages`, `max_tokens` (default 4096), `system`, `temperature`, `top_p`, `top_k`, `stop_sequences`, `stream`, `tools`, `tool_choice` (`auto`, `any`, `tool`).
- reasoning: `--reasoning-format` (`none` outputs raw text), `reasoning_content` field, `--reasoning-budget N` (-1 unrestricted), `--reasoning-budget-message`, `--reasoning-effort`, `--reasoning`, `--reasoning-preserve`; request `reasoning_effort` (`none` disables; other values go to the template), `chat_template_kwargs` (e.g. `{"enable_thinking": false}`), `reasoning_control` (enables `/v1/chat/completions/control`).
- usage: `tokens_evaluated`, `tokens_cached`, `timings{...}`; slot info `{id, id_task, n_ctx, speculative, is_processing, params, next_token{has_next_token, has_new_line, n_remain, n_decoded}}`; slot save/restore/erase return `n_saved`/`n_written`, `n_restored`/`n_read`, `n_erased` (files under `--slot-save-path`).
- errors: chat-completions-style `{"error": {code, message, type}}`; `/slots?fail_on_no_slot=1` returns 503.
- other endpoints: `/v1/chat/completions/input_tokens`, `/v1/responses/input_tokens`, `/v1/messages/count_tokens`, `/embeddings` (non-standard), `/v1/systemone` (not researched).
- risks: README is the only reference and changes on master without versioning; `/v1/responses` is a conversion to chat completions (no server state).
- sources: https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md

#### raw text-completion endpoints
- verified: yes (2026-10-16), basis: SDK request and choice types for the legacy completions shape; llama.cpp README; Ollama api.md and compat page; vLLM serving index. The canonical reference page (developers domain, completions method) was found by search but not fetched.
- link: https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/completion_create_params.py
- request: see table; hosted model literals are `gpt-3.5-turbo-instruct`, `davinci-002`, `babbage-002`; `best_of` cannot be streamed; `suffix` only on one hosted model; Ollama `/v1/completions` takes a string prompt only.
- response: `choices[]{text, index, logprobs, finish_reason: stop, length, content_filter}`, `usage`.
- streaming: "data-only server-sent events ... terminated by a `data: [DONE]` message".
- tool calls: none; this is the transport for the fallback text protocol and for native parsers that read raw text.
- reasoning: none; think tags or channels stay in `text` (or in `content` for llama.cpp `/completion` with `--reasoning-format none`).
- usage: `prompt_tokens`, `completion_tokens`, `total_tokens`.
- errors: as the host.
- risks: Ollama `/api/generate` applies the model template unless `raw: true`; hosted endpoint is legacy; vLLM also lists render endpoints (`/v1/completions/render`, `/v1/chat/completions/render`) in scale-out mode.
- sources: https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/completion_create_params.py ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/completion_choice.py ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md ; https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md ; https://docs.ollama.com/api/openai-compatibility ; https://docs.vllm.ai/en/latest/serving/online_serving/

#### Open Responses (additional)
- verified: yes (2026-10-16), basis: specification page, OpenAPI reference page, project home, repository page.
- link: https://www.openresponses.org/specification
- request: JSON body; `model`, `input`, `tools`, `tool_choice` (incl. allowed tools), `store`, `previous_response_id`, `truncation` (`auto`, `disabled`), `service_tier`; WebSocket requests must not include `stream`, `stream_options`, `background`. Stateless by default.
- response: items need `id`, `type`, `status`; item statuses `in_progress`, `completed`, `incomplete`; response statuses include `queued`, `failed`.
- streaming: SSE with `Content-Type: text/event-stream`; events `response.created`, `response.queued`, `response.in_progress`, `response.completed`, `response.failed`, `response.incomplete`, `response.output_item.added`/`.done`, `response.content_part.added`/`.done`, `response.output_text.delta`/`.done`, `response.output_text.annotation.added`, `response.refusal.delta`/`.done`, `response.reasoning.delta`/`.done`, `response.reasoning_summary_part.added`/`.done`, `response.reasoning_summary_text.delta`/`.done`, `response.function_call_arguments.delta`/`.done`, `error`. The first event for an item must be `response.output_item.added`.
- tool calls: `function_call{name, call_id, arguments}`, `function_call_output`.
- reasoning: `reasoning{type, id, content, summary (required), encrypted_content}`.
- usage: see table.
- errors: see table; stream `error` payload `{type, code, message, param, headers}`; WebSocket `{type: "error", status, error}`.
- extensions: provider items and events are prefixed with an implementor slug (`slug:type`); extension fields must be optional; clients must ignore unknown types.
- risks: young spec (two dated versions in 2026); reasoning event naming differs from the hosted reference; compliance tests exist in the repo (`bin/compliance-test.ts`), usable as conformance tests for `sy proxy`.
- sources: https://www.openresponses.org/specification ; https://www.openresponses.org/reference ; https://www.openresponses.org/ ; https://github.com/openresponses/openresponses

#### interactions format (additional)
- verified: no (2026-10-16), basis: overview and migration guides only; the formal reference was not fetched; the two pages disagree on the version path (`v1beta` vs `v1beta2`); field lists are incomplete.
- link: https://ai.google.dev/gemini-api/docs/interactions-overview
- request/response/streaming: see table. Background execution and server-side storage (`store`, `previous_interaction_id`) mirror the responses format's state model.
- tool calls: `function_call` steps, `function_result` inputs; field names not verified.
- reasoning: thought steps; signature handling not verified.
- usage: `prompt_tokens`, `completion_tokens`, `total_tokens` (one streaming example).
- errors: the API-errors page (snake_case codes, `event_type: "error"` in streams) appears to belong to this surface; not confirmed.
- risks: GA since June 2026 and "recommended for all new projects"; generateContent "remains fully supported"; re-verify before building a codec.
- sources: https://ai.google.dev/gemini-api/docs/interactions-overview ; https://ai.google.dev/gemini-api/docs/migrate-to-interactions ; https://ai.google.dev/gemini-api/docs/api-errors

#### embeddings endpoints (additional)
- verified: no (2026-10-16), basis: engine docs verified; the hosted embeddings reference was not fetched.
- link: https://docs.ollama.com/api/openai-compatibility
- request: `/v1/embeddings` on Ollama: `model`, `input` (string or string list), `encoding_format`, `dimensions` (token arrays and `user` unsupported). Ollama `/api/embed`: `model`, `input` (string, list, or objects with `text`, `image`, `audio`), `truncate` (default true), `options`, `keep_alive`, `dimensions`. llama.cpp: `/v1/embeddings`, `/embedding`, `/embeddings` (different format). vLLM: `/v1/embeddings`, `/v2/embed`, `/pooling`.
- response: `/api/embed` returns `model`, `embeddings`, `total_duration`, `load_duration`, `prompt_eval_count`.
- streaming / tool calls / reasoning: n/a.
- usage: see response.
- errors: per host.
- risks: needed for `search(semantic)` (§12); keep it out of the chat codec layer.
- sources: https://docs.ollama.com/api/openai-compatibility ; https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md ; https://docs.vllm.ai/en/latest/serving/online_serving/

#### rerank endpoints (additional)
- verified: no (2026-10-16), basis: request fields and paths verified; response shape not found on fetched pages.
- link: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- request: llama.cpp `/reranking` (aliases `/rerank`, `/v1/rerank`, `/v1/reranking`): `query`, `documents`, `top_n`, `model`; needs a reranker model and `--embedding --pooling rank`. vLLM: `/rerank`, `/v1/rerank`, `/v2/rerank`, plus `/score`, `/v1/score`.
- response, usage, errors: not verified.
- risks: two rerank request/response shapes are served (`/v1/rerank` and `/v2/rerank`); verify before use.
- sources: https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md ; https://docs.vllm.ai/en/latest/serving/online_serving/

#### batch endpoints (additional)
- verified: yes (2026-10-16), basis: SDK batch params; messages-format docs; Ollama messages compat page; vLLM serving index.
- link: https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/batch_create_params.py
- request: see table; `/v1/embeddings` batches limited to 50,000 inputs; messages-format batches have their own rate limits and a 256 MB request limit; Ollama does not support `/v1/messages/batches`; vLLM has `/v1/chat/completions/batch`.
- response/streaming: asynchronous; poll.
- risks: useful for `sy prove` and benchmark runs against hosted endpoints; not a proxy path.
- sources: https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/batch_create_params.py ; https://platform.claude.com/docs/en/api/rate-limits ; https://platform.claude.com/docs/en/api/errors ; https://docs.ollama.com/api/anthropic-compatibility ; https://docs.vllm.ai/en/latest/serving/online_serving/

#### responses WebSocket mode (additional)
- verified: yes (2026-10-16), basis: WebSocket mode guide, error-codes guide, Open Responses spec, reasoning item docstring.
- link: https://developers.openai.com/api/docs/guides/websocket-mode
- request: `response.create` events (HTTP body fields; `stream`, `background` not used); `stream_id` lanes run FIFO per lane; continuation by `previous_response_id`; a separate mid-turn steering guide exists (not fetched; its event names are unverified).
- streaming: server events follow the responses streaming model; named lanes echo `stream_id`.
- errors: on `previous_response_not_found` retry with full input and `previous_response_id: null`; on `websocket_connection_limit_reached` open a new connection.
- risks: the raw `wss://` URL is not given in the guide (SDK helpers only); a proxy must terminate WebSockets to support harnesses that use this mode.
- sources: https://developers.openai.com/api/docs/guides/websocket-mode ; https://developers.openai.com/api/docs/guides/error-codes ; https://www.openresponses.org/specification

#### realtime endpoints (additional)
- verified: no (2026-10-16), basis: only vLLM's endpoint list (`/v1/realtime`, speech-to-text) was fetched; the hosted realtime reference was not fetched.
- link: https://docs.vllm.ai/en/latest/serving/online_serving/
- risks: audio-centric; recommend out of scope for v1 and report as unsupported.
- sources: https://docs.vllm.ai/en/latest/serving/online_serving/

### Gateways and routers

#### LiteLLM
- verified: yes (2026-10-16), basis: package-index JSON (litellm, litellm-enterprise, litellm-proxy-extras), package-index integrity (provenance) API, OSV API, the repository's advisory pages, the project's March 2026 incident post, LICENSE files, release feed and release page, official docs (responses, messages, function calling, cost map, config settings), one closed issue.
- link: https://github.com/BerriAI/litellm
- license: MIT for everything outside `enterprise/`. `enterprise/LICENSE.md` is a proprietary license: production use needs a paid subscription, while development and testing use is allowed. On the package index, `litellm` is `MIT` and `litellm-proxy-extras` is `MIT`, but `litellm-enterprise` is `LicenseRef-Proprietary`. The `[proxy]` extra of 1.105.1 hard-pins `litellm-enterprise==0.1.73`.
- version: 1.105.1 (released 2026-10-14; marked "Latest" on the release page). Other recent releases: 1.105.0 (2026-10-11), 1.104.3 (2026-10-11) and 1.103.4 (2026-10-07). Between 2026-09-23 and 2026-10-14, eight release lines (1.98.x to 1.105.x) got patch releases, and rc/dev builds ship almost daily. The index lists 936 versions in total.
- platforms: Windows native, macOS native and Linux native. 1.105.1 ships `cp310-abi3` wheels for win_amd64, macOS x86_64 and arm64, and manylinux_2_28 and musllinux_1_2 on x86_64 and aarch64. Requires Python >=3.10,<3.15. The proxy also ships as cosign-signed container images.
- what we use: only an optional long-tail bridge (SPEC §4.1), off by default, for providers that have no native Switchyard codec. Recommendation: include it as an extra that installs the SDK only (base package, no `[proxy]` or other extras). Install it in its own environment and call it out of process through a thin codec adapter, with hashes locked.
  - Exact pin: `litellm==1.105.1`, adopted only after a 7-day cooldown, so from 2026-10-21. If a pin is needed before then, use `litellm==1.103.4` (uploaded 2026-10-07), which falls outside every published advisory range checked today. Never use 1.82.7 or 1.82.8.
  - Set `LITELLM_LOCAL_MODEL_COST_MAP=True`. By default the library fetches the model cost map from a remote URL at startup, which breaks zero-egress (SPEC §15).
  - SDK vs proxy: the SDK is a Python library with call entry points for chat-completions, responses and messages formats. The proxy (`litellm[proxy]`) is a FastAPI server with keys, SSO, an admin UI, MCP, guardrails and spend logs. It adds 34 requirement lines, including the proprietary enterprise package. By title, every 2026 advisory except the malware one targets proxy-server endpoints, auth, SSO, MCP or guardrail features. SDK-only exposure was not assessed advisory by advisory (verify in Phase 1).
  - Dependency weight (1.105.1 base install): 18 direct dependencies, among them the reference chat-completions client SDK (>=2.20), `boto3>=1.43.1`, `tokenizers`, a model-hub client, `tiktoken`, `aiohttp`, `jinja2`, `jsonschema`, `pydantic`, `pydantic-settings`, `httpx[http2]` and `fastuuid`. The package has 15 extras. Wheels are 36.4 to 39.8 MB and the sdist is 20.3 MB, against a 15.6 MB pure-Python wheel at 1.83.0. Wheels have been platform-specific since about 1.95.
  - Translation fidelity is path-dependent and best-effort, so it is not lossless in the SPEC §4.1 sense:
    - responses to chat-completions: providers without a native responses endpoint are served through "a bridge to LiteLLM's `/chat/completions` endpoint". The docs say only that "the model's default `mode` determines how bridging works".
    - chat-completions to responses: requests "may be bridged here automatically when the provider lacks support for that endpoint". A per-model flag forces the chat-completions path.
    - Reasoning: on the degraded path "each reasoning item keeps its summary text and loses only its encrypted payload and id". Items with no readable text are dropped. Cross-deployment encrypted reasoning is "served in degraded form rather than failed".
    - Stored conversation state for the responses format is a proxy feature that needs spend-log storage plus object storage. Guardrail text rewrites apply to non-streaming responses only.
    - messages to the other formats: one messages-format entry point covers all providers. Thinking needs `budget_tokens` of at least 1024 and below `max_tokens`. A separate messages-to-responses parameter mapping exists.
    - Issue #20433 (opened 2026-02, closed as completed) showed that a messages-format call to a reasoning model returned only reasoning token counts, not reasoning text, because it went through chat-completions instead of responses.
- what we borrow:
  - The MIT-licensed model capability and cost map JSON, as seed priors for `sy models add` (attribution required).
  - The prompt-injected function-calling fallback (`add_function_to_prompt`, documented as "Function calling for Models w/out function-calling support"), as a baseline to beat with our grammar-constrained JSON fallback (SPEC §4.4).
  - The documented degradation cases above, turned into codec conformance fixtures.
  - The release-signing pattern: cosign key pinned by commit hash, with tag-based verification as a convenience.
- what we avoid:
  - The `[proxy]` extra and every server feature: proprietary dependency and nearly all of the CVE surface.
  - Installing it into Switchyard's own environment.
  - Unpinned or fresh releases.
  - Using it for the four main formats, which we implement natively.
  - Relying on it for lossless conversion: our adapter must diff the internal representation before and after and emit loss reports.
- risks:
  - Supply chain:
    - On 2026-03-24, versions 1.82.7 (payload in `proxy_server.py`) and 1.82.8 (adds `litellm_init.pth` plus the same payload) were uploaded to the index. They contained a credential stealer that sent data to a non-project domain.
    - They were live from 10:39 UTC for about 40 minutes before quarantine. The project's post gives 10:39 to 16:00 UTC as the exposure window for pip installs and image builds.
    - The suspected origin is a compromised Trivy dependency in the CI security-scan workflow and/or a maintainer's index account.
    - Advisories: GHSA-5mg7-485q-xm76 (Critical), PYSEC-2026-2, MAL-2026-2144.
    - Both versions are now gone from the index. They were removed, not yanked; the index shows zero yanked releases since 2023.
    - Releases paused from 1.82.6 (2026-03-22) until 1.83.0 (uploaded 2026-03-31, the first release from the new pipeline). v1.78.0 to v1.82.6 were audited as clean.
    - A `.pth` file runs at every interpreter start in that environment, even without an import. This is why the bridge must live in a separate environment.
  - Provenance: package-index provenance attestations exist for the 1.83.0, 1.83.7 and 1.83.14 wheels. They are missing (HTTP 404) for 1.84.0, 1.88.6, 1.90.0, 1.95.0, 1.104.3 and 1.105.1. Hash-locking plus a cooldown is the only integrity control available for current releases.
  - Advisories (OSV plus the repository's advisory page):
    - 2025: 6 GHSA entries, published 2025-03-20, for older CVEs, all fixed by 1.61.15 or earlier.
    - 2026: 30 GHSA entries (6 Critical, 9 High, 6 Moderate, 9 Low). Three of them (GHSA-7hp6-4w63-5g45 Critical, GHSA-g5ff-637f-6q2m High, GHSA-hhww-mrg2-969h Moderate) were on the repository page but not yet in OSV today.
    - Critical, with fixes:
      - GHSA-jjhc-v7c2-5hh6 (OIDC auth bypass): fixed in 1.83.0.
      - GHSA-r75f-5x8p-qvmc (SQL injection in proxy key check): affects 1.81.16 up to 1.83.7.
      - GHSA-6wvf-77m9-58rm (template injection): fixed in 1.83.7.
      - GHSA-4xpc-pv4p-pm3w (host-header auth bypass): fixed in 1.84.0.
      - GHSA-7hp6-4w63-5g45 (privilege escalation to proxy admin): affects 1.91.0 up to 1.100.4 plus early 1.101 to 1.104 builds; fixed in 1.100.4, 1.101.3, 1.102.2, 1.103.1 and 1.104.0rc2.
    - Newest: GHSA-3cv6-jpf6-8222 (Moderate; SSRF plus provider-credential exfiltration; fixed in 1.96.2 and backports).
  - Very high release cadence; heavy dependencies; license split; the default network fetch at startup.
- sources: https://pypi.org/pypi/litellm/json ; https://pypi.org/pypi/litellm-enterprise/json ; https://pypi.org/pypi/litellm-proxy-extras/json ; https://pypi.org/integrity/litellm/1.83.0/litellm-1.83.0-py3-none-any.whl/provenance (same endpoint checked for 1.83.7, 1.83.14, 1.84.0, 1.88.6, 1.90.0, 1.95.0, 1.104.3, 1.105.1) ; https://api.osv.dev/v1/query (package litellm, PyPI) ; https://github.com/BerriAI/litellm/security/advisories ; https://github.com/BerriAI/litellm/security/advisories/GHSA-7hp6-4w63-5g45 ; https://github.com/BerriAI/litellm/security/advisories/GHSA-g5ff-637f-6q2m ; https://github.com/BerriAI/litellm/security/advisories/GHSA-hhww-mrg2-969h ; https://docs.litellm.ai/blog/security-update-march-2026 ; https://raw.githubusercontent.com/BerriAI/litellm/main/LICENSE ; https://raw.githubusercontent.com/BerriAI/litellm/main/enterprise/LICENSE.md ; https://github.com/BerriAI/litellm/releases.atom ; https://github.com/BerriAI/litellm/releases/tag/v1.105.1 ; https://github.com/BerriAI/litellm/commits/main.atom ; https://docs.litellm.ai/docs/response_api ; https://docs.litellm.ai/docs/anthropic_unified ; https://docs.litellm.ai/docs/completion/function_call ; https://docs.litellm.ai/docs/completion/token_usage ; https://docs.litellm.ai/docs/proxy/config_settings ; https://github.com/BerriAI/litellm/issues/20433

#### claude-code-router
- verified: yes (2026-10-16), basis: npm registry metadata (the package and its gateway dependencies), README at `main` and at tag `v2.0.0`, LICENSE, git tags, official docs site (routing, providers, Claude Code profile, configuration overview), OSV. One gap: the exact 3.x config storage path was not confirmed.
- link: https://github.com/musistudio/claude-code-router
- license: MIT (LICENSE file and npm metadata). Its 3.x gateway dependencies are MIT too, but the gateway package has no repository link in its npm metadata.
- version: 3.1.3 (released 2026-10-09). Line history: 1.0.0 (2025-06-10) to 1.0.73 (2025-12-18); 2.0.0 (2026-01-04); 3.0.0 (2026-07-06) to 3.1.3. The last commit on `main` is 2026-10-10.
- platforms: Windows native, macOS native and Linux native. Desktop builds: Windows installer, macOS arm64 and x64, Linux AppImage. The npm CLI needs Node.js 22 or newer. A container compose setup also exists.
- what we use: no runtime use. It serves as a reference for which harnesses a proxy must configure. 3.x ships "Agent Config" profiles for Claude Code, Claude Design, Codex, Grok CLI, Kimi CLI, Kilo Code, OpenCode, Pi, ZCode and WorkBuddy.
  - Formats: the 3.x README says it supports chat-completions, responses, messages and generate-content, including that family's newer "interactions" API. Provider docs list three upstream protocol values (messages, responses, chat-completions), and connection checks report "which protocol matched" (protocol probing). In 2.x, harnesses sent messages format and transformers adapted outbound requests to each provider.
  - Routing in 3.x (docs):
    - A built-in route sends Claude Code main requests to the profile model "when the client has not selected a recognized model".
    - Ordered custom rules match on `request.header` or `request.body` with `==`, `starts with`, `contains` or `contains deep`. The first enabled match rewrites the request: Set, Delete, or Append/Prepend/Remove/Replace in an array. Usually it sets `request.body.model`.
    - Failure handling is Off, Retry (with a count) or Fallback targets (a model chain), set globally or per rule.
    - Node.js script rules run in a Worker. They are fail-open and inherit the process's access.
    - Rules can read `input.tokenCount`, an estimate.
    - A `<CCR-SUBAGENT-MODEL>` tag routes subagent and task requests.
    - The Codex patch tool is rewritten to a function tool for other models and converted back, which the docs call a "tool protocol bridge".
    - The 3.x routing docs do not describe the 2.x scenario keys.
  - Routing and config in 2.0.0:
    - Config lives in `~/.claude-code-router/config.json` with `Providers` (each with `transformer`), `APIKEY`, `HOST` (forced to 127.0.0.1 when no key is set), `NON_INTERACTIVE_MODE` and env-var interpolation.
    - `Router` keys: `default`, `background`, `think`, `longContext`, `longContextThreshold` (default 60000 tokens), `webSearch` and `image` (beta).
    - `CUSTOM_ROUTER_PATH` loads a JS router. `/model provider,model` switches models inside a session.
  - Transformers (1.x and 2.x): per-provider or per-model request/response adapters (`"transformer": {"use": [...]}`, with options). There are 16 built-ins, mostly provider-specific adapters plus generic ones (`maxtoken`, `tooluse`, `reasoning`, `sampling`, `enhancetool`, `cleancache`). Custom JS transformers load through a `transformers` array. 1.0.34 to 1.0.73 depend on the `llms` npm package ("A universal LLM API transformation server"). In 3.x the term does not appear in the routing or provider docs; protocol work moved to a separate gateway package.
  - How harnesses use it:
    - 2.x: `ccr code`, or `eval "$(ccr activate)"`, which exports the harness's base-URL variable pointing at http://127.0.0.1:3456.
    - 3.x: a profile launched with `ccr "<profile>"` or from the desktop app. The Claude Code profile has a default model plus per-tier overrides (Fable, Opus, Sonnet, Haiku; Haiku is the small/fast tier). `/model` lists router models. "CCR managed compact" is optional. The management UI is at 127.0.0.1:3458 and the gateway at 127.0.0.1:3456.
- what we borrow:
  - Ordered header/body match, rewrite and fallback-chain rules, for the SPEC §7.1 rules stage.
  - An explicit subagent pin tag.
  - The tool-protocol-bridge idea for patch-style tools.
  - Protocol probing when a provider is added (compare `sy probe`).
  - Binding to localhost unless a key is set.
  - Per-tier slot mapping for the Claude Code adapter (SPEC §12 item 3).
- what we avoid:
  - Fail-open scripts with full process access.
  - A token-count threshold as the only long-context signal.
  - Routing logic tied to one desktop app.
- risks:
  - Churn: three major lines in 13 months, and the 2.x scenario keys and transformers are no longer documented in 3.x.
  - The core gateway package (`@the-next-ai/ai-gateway` 1.0.21, 2026-09-08) has no source repository in npm metadata.
  - GHSA-8hmm-4crw-vm2c (High; a CORS misconfiguration could expose API keys; fixed in 1.0.34; published 2025-08-21).
  - The README contains sponsor and referral links.
- sources: https://registry.npmjs.org/@musistudio%2Fclaude-code-router ; https://raw.githubusercontent.com/musistudio/claude-code-router/main/README.md ; https://raw.githubusercontent.com/musistudio/claude-code-router/main/LICENSE ; https://raw.githubusercontent.com/musistudio/claude-code-router/v2.0.0/README.md ; https://github.com/musistudio/claude-code-router/commits/main.atom ; https://ccrdesk.top/en/routing/ ; https://ccrdesk.top/en/configuration/providers/ ; https://ccrdesk.top/en/configuration/agents/claude-code/ ; https://ccrdesk.top/en/configuration/overview/ ; https://registry.npmjs.org/@the-next-ai%2Fai-gateway ; https://registry.npmjs.org/@musistudio%2Fllms ; https://api.osv.dev/v1/vulns/GHSA-8hmm-4crw-vm2c

#### RouteLLM
- verified: yes (2026-10-16), basis: repository README and LICENSE, commit feed and latest commit patch, package-index JSON, OSV (0 advisories).
- link: https://github.com/lm-sys/RouteLLM
- license: Apache-2.0 (LICENSE file; the package-index metadata has no license field).
- version: 0.2.0 (released 2024-07-08). The last commit on `main` was 2024-08-10 ("create /health endpoint"). The repository is not archived but has had no commits for about 26 months, so it is unmaintained in practice.
- platforms: Windows, macOS and Linux via a pure-Python wheel (`py3-none-any`). Practical support follows its heavy dependencies (torch, transformers, datasets, scikit-learn, pandas), which were not tested here.
- what we use: nothing at runtime.
  - How it routes: binary routing between one strong and one weak model with a per-request cost threshold. Routers:
    - `mf`: matrix factorization on preference data (recommended).
    - `sw_ranking`: similarity-weighted Elo.
    - `bert`: BERT classifier.
    - `causal_llm`: LLM-based classifier.
    - `random`.
  - Thresholds are calibrated to a target share of strong-model calls. It offers a chat-completions-compatible server where `model="router-<name>-<threshold>"` picks the router, plus offline evals on `mmlu`, `gsm8k` and `mt-bench` with cached results.
- what we borrow (for SPEC §10):
  - The single-method interface (`calculate_strong_win_rate(prompt)`), generalized to per-arm success posteriors.
  - Threshold calibration to a target escalation share on the user's own history, as a budget knob.
  - Virtual model IDs for `sy proxy`.
  - Baseline arms to benchmark the bandit against: `random`; similarity-weighted Elo over local history with local embeddings; matrix factorization over the (task features × worker) outcome matrix as a cold-start prior.
  - A cached offline-eval pattern for the §10 simulation harness.
- what we avoid:
  - Strong/weak-only routing.
  - Training on chat preference data instead of test outcomes.
  - `mf` and `sw_ranking`, which need a remote embeddings API key (conflicts with zero-egress).
  - Its dependency set.
- risks: unmaintained. `requires_dist` lists `litellm` with no version bound, so an install during the 2026-03-24 window could have resolved to a compromised LiteLLM release.
- sources: https://github.com/lm-sys/RouteLLM ; https://raw.githubusercontent.com/lm-sys/RouteLLM/main/README.md ; https://raw.githubusercontent.com/lm-sys/RouteLLM/main/LICENSE ; https://github.com/lm-sys/RouteLLM/commits/main.atom ; https://github.com/lm-sys/RouteLLM/commit/0b64fdafe049e596a3f5657c219329f24af24198.patch ; https://pypi.org/pypi/routellm/json ; https://api.osv.dev/v1/query (package routellm, PyPI)

### Open-weight model families, part 1

#### Engines (shared sources for all parser names)
- verified: yes (2026-10-16), basis: engine source files on the default branch today and the latest release tags.
- vLLM tool parsers (main): `deepseek_v3`, `deepseek_v31`, `deepseek_v32`, `deepseek_v4`, `deepseek_v41`, `ernie45`, `glm45`, `glm47` (both map to `glm47_moe_tool_parser`), `ling3`, `hermes`, `hf` (main only), `hunyuan_a13b`, `hy_v3`, `hy_v4`, `internlm`, `kimi_k2`, `kimi_k3`, `longcat`, `mimo`, `minimax_m2`, `minimax_m3`, `minicpm5`, `qwen3_coder`, `qwen3_xml` (same file as `qwen3_coder`), `qwen3_json` (main only, = hermes), `seed_oss`, `step3`, `step3p5`, plus families covered by part 2. Reasoning parsers: `deepseek_r1`, `deepseek_v3`, `deepseek_v4`, `deepseek_v41`, `ernie45`, `glm45`, `glm47`, `ling3`, `hunyuan_a13b`, `hy_v3`, `hy_v4`, `kimi_k2`, `kimi_k3`, `mimo`, `minimax_m2`, `minimax_m2_append_think`, `minimax_m3`, `qwen3`, `seed_oss`, `step3`, `step3p5`. Tokenizer modes `deepseek_v32`, `deepseek_v4`, `deepseek_v41` replace a Jinja template for DeepSeek.
- SGLang tool parsers: `deepseekv3`, `deepseekv31`, `deepseekv32`, `deepseekv4`, `deepseekv41`, `glm`, `glm45`, `glm47`, `hermes`, `hunyuan`, `interns1`, `kimi_k2`, `kimi_k3`, `ling3`, `mimo`, `minicpm5`, `minimax-m2`, `minimax-m3`, `qwen`, `qwen25`, `qwen3_coder`, `step3`, `step3p5` (+ others). Reasoning: `deepseek-r1`, `deepseek-v3`, `deepseek-v4`, `deepseek-v41`, `glm45`, `hunyuan`, `interns1`, `kimi`, `kimi_k2`, `kimi_k3`, `ling3`, `mimo`, `minimax`, `minimax-append-think`, `minimax-m3`, `qwen3`, `qwen3-thinking`, `step3`, `step3p5`. Both flags also accept `auto`. No ERNIE, Seed-OSS, LongCat or Step-5 parser.
- llama.cpp: chat formats are now `CONTENT_ONLY`, `PEG_SIMPLE`, `PEG_NATIVE`, `PEG_GEMMA4`, `PEG_MINIMAX_M3`; a template autoparser handles most templates, with dedicated handlers selected by template markers for Kimi K2, Kimi K3, Ling 3, MiniMax-M3, DeepSeek V3.2/V4 DSML, MiniCPM5 and the Qwen3-Coder XML layout (comment: also used by Qwen3.5 and Step-3.5-Flash). `use_jinja` defaults to true for llama-server (false for llama-completion and mtmd). Reasoning flags: `--reasoning on|off|auto`, `--reasoning-format none|auto|deepseek|deepseek-legacy` (default auto), `--reasoning-budget N`, `--reasoning-preserve`; setting `enable_thinking` through `--chat-template-kwargs` is deprecated. `docs/function-calling.md` still lists the older handler set and is stale.
- Ollama: built-in parsers `qwen3`, `qwen3-thinking`, `qwen3.5`, `qwen3-coder`, `qwen3-vl-instruct`, `qwen3-vl-thinking`, `deepseek3`, `glm-4.7` (+ `glm46.go`), `harmony`, `cogito`, `olmo3`, `nemotron-3-nano`, `gemma4`, `lfm2`, `ministral`, and others; renderers include `qwen3.8` and `deepseek3.1`. No Kimi, MiniMax, MiMo, Hy, Ling, Seed-OSS, ERNIE, LongCat, Step or MiniCPM parser. Which architectures Ollama can load was not checked (verified: no).
- MLX-LM model modules relevant here: `qwen3_5`, `qwen3_5_moe`, `qwen3_next`, `deepseek_v3`, `deepseek_v32`, `deepseek_v41`, `glm4_moe`, `glm4_moe_lite`, `glm_moe_dsa`, `kimi_k25`, `kimi_k3`, `kimi_linear`, `minimax`, `minimax_m3_vl`, `mimo_v2_flash`, `hunyuan`, `hunyuan_v1_dense`, `ernie4_5`, `ernie4_5_moe`, `seed_oss`, `bailing_moe`, `bailing_moe_linear`, `bailing_moe_v3`, `bailing_hybrid`, `step3p5`, `longcat_flash`, `longcat_flash_ngram`; remaps `kimi_k2`->`deepseek_v3`, `minimax_m2`->`minimax`, `deepseek_v41_text`->`deepseek_v41`. Absent: `qwen4_exp`, `deepseek_v4`, `glm5_next`, `hy_v3`, `hy_v4`, `mimo_v2`.
- sources: https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/tool_parsers/__init__.py, https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/reasoning/__init__.py, https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/tool_parsers/__init__.py, https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/reasoning/__init__.py, https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/function_call/parser_names.py, https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/function_call/function_call_parser.py, https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/parser/reasoning_parser.py, https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/parser/reasoning_parser_names.py, https://raw.githubusercontent.com/sgl-project/sglang/v0.5.21/python/sglang/srt/function_call/function_call_parser.py, https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/server_args.py, https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/chat.cpp, https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/chat.h, https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/arg.cpp, https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/common.h, https://raw.githubusercontent.com/ggml-org/llama.cpp/master/src/llama-arch.cpp, https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/function-calling.md, https://raw.githubusercontent.com/ollama/ollama/main/model/parsers/parsers.go, https://raw.githubusercontent.com/ollama/ollama/main/model/renderers/renderer.go, https://github.com/ollama/ollama/tree/main/model/parsers, https://github.com/ml-explore/mlx-lm/tree/main/mlx_lm/models, https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/utils.py, https://github.com/huggingface/transformers/tree/main/src/transformers/models, https://github.com/vllm-project/vllm/releases/latest, https://github.com/sgl-project/sglang/releases/latest, https://github.com/ggml-org/llama.cpp/releases/latest, https://github.com/ollama/ollama/releases/latest, https://github.com/ml-explore/mlx-lm/releases/latest, https://pypi.org/pypi/vllm/json, https://pypi.org/pypi/sglang/json, https://pypi.org/pypi/transformers/json, https://pypi.org/pypi/mlx-lm/json, https://recipes.vllm.ai/Qwen/Qwen3.8-27B (recipe bundle containing all model recipes)

#### Qwen
- verified: yes (2026-10-16), basis: org listing, model cards, `config.json`, `chat_template.jinja` / `tokenizer_config.json`, LICENSE files, GGUF file trees, vLLM recipe bundle.
- releases: `Qwen/Qwen3.8-Flash-Next` + `-FP8` (2026-08-24), `Qwen/Qwen3.8-27B` (2026-08-05) + `-FP8` (2026-08-13), `Qwen/Qwen3.8-2.4T-A95B` + `-FP8` (2026-08-08), `Qwen/Qwen3.6-27B` (2026-04-21), `Qwen/Qwen3.6-35B-A3B` (2026-04-15), `Qwen/Qwen3.5-397B-A17B` (2026-02-16), `Qwen/Qwen3.5-122B-A10B`, `-35B-A3B`, `-27B` (2026-02-24), `Qwen/Qwen3.5-9B`, `-4B` (2026-02-27), `-2B`, `-0.8B` (2026-02-28), GPTQ-Int4 variants (2026-03-03), `Qwen/Qwen3-Coder-Next` (2026-01-30; official `Qwen/Qwen3-Coder-Next-GGUF`), `Qwen/Qwen3-Next-80B-A3B-Instruct` / `-Thinking` (2025-09-09; official GGUF 2025-12-03), `Qwen/Qwen3-4B-Instruct-2507` (2025-08-05), `Qwen/Qwen3-Coder-30B-A3B-Instruct` (2025-07-31), `Qwen/Qwen3-Coder-480B-A35B-Instruct` (2025-07-22). Also `Qwen/Qwen-AgentWorld-35B-A3B` (2026-06-22, not examined).
- template/tool/reasoning notes: Qwen3.8 templates for 27B and Flash-Next are byte-identical; 2.4T differs (text-only, raises on `enable_thinking=false`). `reasoning_effort` maps to fixed system sentences (xhigh and low have text, medium has none). `preserve_thinking` default true in 3.8 renders `<think>\n{reasoning}\n</think>` for every past assistant turn, even when empty. Qwen3.5 template keeps past reasoning only after the last real user query; Qwen3.6 adds opt-in `preserve_thinking`; 3.6 and 3.8 changed argument rendering (strings raw, everything else `tojson`). Tool results from consecutive `tool` messages are merged into one user turn of `<tool_response>` blocks. Qwen3-Coder repos ship their own `qwen3coder_tool_parser*.py`. GGUF: `ggml-org/Qwen3.8-27B-GGUF` Q4_K_M 19.0 GB, Q8_0 28.6 GB, separate `mtp-*` and `dflash-*` draft files; `unsloth/Qwen3.8-Flash-Next-GGUF` UD-Q4_K_XL 111.3 GB, UD-IQ4_XS 93.7 GB, UD-IQ3_XXS 82.0 GB. Qwen3.8-Flash-Next license: attribution above 100M MAU or $20M monthly revenue; model-as-a-service or AI work-assistant businesses need a separate license for commercial use (internal use excepted). Qwen3.8-2.4T license: same attribution rule; separate license above $50M revenue per 12 months for those businesses.
- sources: https://huggingface.co/api/models?author=Qwen&sort=createdAt&direction=-1&limit=150, https://huggingface.co/api/models/Qwen/Qwen3.8-27B, https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/chat_template.jinja, https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json, https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/README.md, https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/config.json, https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/LICENSE, https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B/raw/main/chat_template.jinja, https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B/raw/main/LICENSE, https://huggingface.co/Qwen/Qwen3.6-35B-A3B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3.6-27B/raw/main/chat_template.jinja, https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/chat_template.jinja, https://huggingface.co/Qwen/Qwen3.5-397B-A17B/raw/main/config.json, https://huggingface.co/Qwen/Qwen3.5-0.8B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3-Coder-Next/raw/main/README.md, https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct/raw/main/README.md, https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/raw/main/README.md, https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/raw/main/tokenizer_config.json, https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct/raw/main/chat_template.jinja, https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B/raw/main/tokenizer_config.json, https://huggingface.co/api/models/Qwen/Qwen3-Embedding-0.6B, https://huggingface.co/api/models/ggml-org/Qwen3.8-27B-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/unsloth/Qwen3.8-Flash-Next-GGUF/tree/main?recursive=true

#### DeepSeek
- verified: yes (2026-10-16), basis: org listing, model cards, configs, `encoding/*.py` reference encoders, V3/V3.1 `tokenizer_config.json` templates, V4.1 `chat_template.jinja`, vLLM recipe bundle. Not verified: how GGUF builds of V3.2/V4 embed a template (official repos ship none).
- releases: `deepseek-ai/DeepSeek-V4.1-Flash` (2026-09-10), `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp` (2026-08-31), `deepseek-ai/DeepSeek-V4-Pro-0813` (2026-08-13), `deepseek-ai/DeepSeek-V4-Flash-0731` (2026-07-31), `deepseek-ai/DeepSeek-V4-Pro-DSpark`, `deepseek-ai/DeepSeek-V4-Flash-DSpark` (2026-06-27), `deepseek-ai/DeepSeek-V4-Pro`, `deepseek-ai/DeepSeek-V4-Flash` (+ `-Base`, 2026-04-22), `deepseek-ai/DeepSeek-V3.2` (2025-12-01), `deepseek-ai/DeepSeek-V3.2-Speciale` (2025-11-28), `deepseek-ai/DeepSeek-V3.2-Exp` (2025-09-29), `deepseek-ai/DeepSeek-V3.1-Terminus` (2025-09-22), `deepseek-ai/DeepSeek-V3.1` (2025-08-21), `deepseek-ai/DeepSeek-R1-0528` (2025-05-28), `deepseek-ai/DeepSeek-V3-0324` (2025-03-24).
- template/tool/reasoning notes: V4-0731 and V4-Pro-0813 are the non-preview releases of V4-Flash and V4-Pro. V4 encoder: `reasoning_effort` low (default, adds nothing), high and max add long system prefixes; tool results become `<tool_result>` blocks in user turns, reordered to the call order. V4.1 Jinja: kwargs `thinking_mode` (`thinking` default, `chat`; `enable_thinking` accepted as alias), `drop_thinking` (default true; forced off when any message carries tools), `reasoning_effort`; reads `reasoning_content` or `thinking`; mid-conversation system messages count as user turns. V4.1 card also points to a maintained prompt toolkit (`deepseek-recipe`, Rust with Python bindings) that converts chat-completions, messages and responses requests. V3.2 places `<think>` after the last user turn only in thinking mode and has a `developer` role reserved for search agents. V3.1 thinking toggle is the `thinking` template kwarg (default false). V4.1 vLLM recipe uses DSpark speculative decoding and `--language-model-only` for text-only serving.
- sources: https://huggingface.co/api/models?author=deepseek-ai&sort=createdAt&direction=-1&limit=150, https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md, https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/chat_template.jinja, https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/encoding/encoding.py, https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/config.json, https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/raw/main/README.md, https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/raw/main/README.md, https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/raw/main/encoding/encoding_dsv4.py, https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813/raw/main/README.md, https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/raw/main/config.json, https://huggingface.co/deepseek-ai/DeepSeek-V3.2/raw/main/README.md, https://huggingface.co/deepseek-ai/DeepSeek-V3.2/raw/main/encoding/encoding_dsv32.py, https://huggingface.co/deepseek-ai/DeepSeek-V3.1/raw/main/README.md, https://huggingface.co/deepseek-ai/DeepSeek-V3.1/raw/main/tokenizer_config.json, https://huggingface.co/deepseek-ai/DeepSeek-V3-0324/raw/main/tokenizer_config.json, https://huggingface.co/deepseek-ai/DeepSeek-R1-0528/raw/main/tokenizer_config.json, https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash, https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash

#### Kimi
- verified: yes (2026-10-16), basis: org listing, cards, configs, K2.x Jinja templates, K3 `encoding_k3.py` and `tokenization_kimi.py`, LICENSE files, vLLM recipe bundle. Not verified: GGUF architecture used for K2.x.
- releases: `moonshotai/Kimi-K3` (2026-06-13), `moonshotai/Kimi-K2.7-Code` (2026-06-11), `moonshotai/Kimi-K2.6` (2026-04-14), `moonshotai/Kimi-K2.5` (2026-01-01), `moonshotai/Kimi-K2-Thinking` (2025-11-04), `moonshotai/Kimi-Linear-48B-A3B-Instruct` / `-Base` (2025-10-30), `moonshotai/Kimi-K2-Instruct-0905` (2025-09-03), `moonshotai/Kimi-K2-Instruct` (2025-07-11).
- template/tool/reasoning notes: K2.6 template splits history at the last assistant message without tool calls; earlier assistant turns get `<think></think>`; `preserve_thinking=true` keeps everything. `thinking=false` puts `<think></think>` in the prompt. K3 tokenizer `apply_chat_template` calls `encoding_k3.build_chat_segments`; kwarg `thinking_effort` (low/high/max, default max); every assistant turn carries a `think` channel (empty allowed) followed by `response` and optional `tools`; tool results are re-sorted into call order by `tool_call_id`. K3 card: always thinking, returns `reasoning_content`, MXFP4 weights; deploy on vLLM, SGLang or TokenSpeed.
- sources: https://huggingface.co/api/models?author=moonshotai&sort=createdAt&direction=-1&limit=150, https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md, https://huggingface.co/moonshotai/Kimi-K3/raw/main/config.json, https://huggingface.co/moonshotai/Kimi-K3/raw/main/encoding_k3.py, https://huggingface.co/moonshotai/Kimi-K3/raw/main/tokenization_kimi.py, https://huggingface.co/moonshotai/Kimi-K3/raw/main/tokenizer_config.json, https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE, https://huggingface.co/moonshotai/Kimi-K2.7-Code/raw/main/README.md, https://huggingface.co/moonshotai/Kimi-K2.7-Code/raw/main/chat_template.jinja, https://huggingface.co/moonshotai/Kimi-K2.6/raw/main/README.md, https://huggingface.co/moonshotai/Kimi-K2.6/raw/main/chat_template.jinja, https://huggingface.co/moonshotai/Kimi-K2.6/raw/main/LICENSE, https://huggingface.co/moonshotai/Kimi-K2.5/raw/main/chat_template.jinja, https://huggingface.co/moonshotai/Kimi-K2-Thinking/raw/main/chat_template.jinja, https://huggingface.co/moonshotai/Kimi-K2-Instruct-0905/raw/main/chat_template.jinja, https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct/raw/main/config.json, https://recipes.vllm.ai/moonshotai/Kimi-K3, https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/tool_parsers/kimi_k3_tool_parser.py

#### GLM
- verified: yes (2026-10-16), basis: org listing, cards, configs, templates for 4.6, 4.7, 5, 5.1, 5.2, 5.3, 5.3-Flash, GLM-5.3 LICENSE, GGUF tree, vLLM recipe bundle. Active parameters for 5.1-5.3 inferred from the GLM-5 card plus identical config shape (verified: no for 5.3 directly).
- releases: `zai-org/GLM-5.3`, `zai-org/GLM-5.3-BF16`, `zai-org/GLM-5.3-Flash`, `zai-org/GLM-5.3-Flash-BF16` (2026-08-25), `zai-org/GLM-5.2` + `-FP8` (2026-06-16), `zai-org/GLM-5.1` + `-FP8` (2026-04-03), `zai-org/GLM-5` + `-FP8` (2026-02-11), `zai-org/GLM-4.7-Flash` (2026-01-19), `zai-org/GLM-4.7` + `-FP8` (2025-12-22), `zai-org/GLM-4.6V`, `-4.6V-Flash` (2025-12-07), `zai-org/GLM-4.6` + `-FP8` (2025-09-29), `zai-org/GLM-4.5`, `zai-org/GLM-4.5-Air` (+ FP8, Base; 2025-07-20).
- template/tool/reasoning notes: GLM-5.3 template line 2 forces effort to `max` unless `low` or `high`; line 3 always emits `<|system|>Reasoning Effort: …`; no `enable_thinking`; reasoning is also recovered from `<think>` inside `content`. 5.3-Flash template differs only in media handling (image/video/audio tokens). GLM-5 card serve flags: vLLM and SGLang `--tool-call-parser glm47 --reasoning-parser glm45`; 5.3 recipes use `glm47` for both. 5.2 card: 1M context via a shared indexer across every four sparse-attention layers. Atria-Dawn-Preview (InternLM row) reuses this template family.
- sources: https://huggingface.co/api/models?author=zai-org&sort=createdAt&direction=-1&limit=150, https://huggingface.co/zai-org/GLM-5.3/raw/main/README.md, https://huggingface.co/zai-org/GLM-5.3/raw/main/chat_template.jinja, https://huggingface.co/zai-org/GLM-5.3/raw/main/config.json, https://huggingface.co/zai-org/GLM-5.3/raw/main/LICENSE, https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/README.md, https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/chat_template.jinja, https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/config.json, https://huggingface.co/zai-org/GLM-5.2/raw/main/README.md, https://huggingface.co/zai-org/GLM-5.2/raw/main/chat_template.jinja, https://huggingface.co/zai-org/GLM-5/raw/main/README.md, https://huggingface.co/zai-org/GLM-4.7/raw/main/chat_template.jinja, https://huggingface.co/zai-org/GLM-4.7-Flash/raw/main/README.md, https://huggingface.co/zai-org/GLM-4.6/raw/main/chat_template.jinja, https://huggingface.co/zai-org/GLM-4.5-Air/raw/main/config.json, https://huggingface.co/api/models/unsloth/GLM-5.3-Flash-GGUF/tree/main?recursive=true, https://recipes.vllm.ai/zai-org/GLM-5.3, https://recipes.vllm.ai/zai-org/GLM-5.3-Flash

#### MiniMax
- verified: yes (2026-10-16), basis: org listing, cards, configs, M2/M2.5/M2.7/M3 templates, M2.7 and M3 LICENSE files, vLLM recipe bundle. Not verified: M2/M2.1/M2.5 license text (only `license_name: modified-mit` metadata; text hosted off-hub), exact M1 tool syntax.
- releases: `MiniMaxAI/MiniMax-M3` + `-MXFP8` (2026-06-02), `MiniMaxAI/MiniMax-M2.7` (2026-04-09), `MiniMaxAI/MiniMax-M2.5` (2026-02-12), `MiniMaxAI/MiniMax-M2.1` (2025-12-20), `MiniMaxAI/MiniMax-M2` (2025-10-22), `MiniMaxAI/MiniMax-M1-80k`, `-M1-40k` (+ `-hf`, 2025-06/07).
- template/tool/reasoning notes: M2.7 template renders `<think>\n…\n</think>\n\n` only for assistant turns after the last user message; generation prompt `]~b]ai\n<think>\n`. M3 template: `thinking_mode` adds a `<thinking_instructions>` system block; disabled mode pre-fills `</mm:think>`; enabled pre-fills `<mm:think>`; adaptive leaves it open; turns without reasoning are prefixed with `</mm:think>`. M2.7 license: non-commercial grant; commercial use prohibited without prior written authorization; "Built with MiniMax M2.7" attribution for authorized commercial use. M3 license: commercial use needs "Built with MiniMax M3" attribution plus a one-time notice, or written authorization above $20M yearly revenue.
- sources: https://huggingface.co/api/models?author=MiniMaxAI&sort=createdAt&direction=-1&limit=150, https://huggingface.co/MiniMaxAI/MiniMax-M3/raw/main/README.md, https://huggingface.co/MiniMaxAI/MiniMax-M3/raw/main/chat_template.jinja, https://huggingface.co/MiniMaxAI/MiniMax-M3/raw/main/config.json, https://huggingface.co/MiniMaxAI/MiniMax-M3/raw/main/LICENSE, https://huggingface.co/MiniMaxAI/MiniMax-M2.7/raw/main/README.md, https://huggingface.co/MiniMaxAI/MiniMax-M2.7/raw/main/chat_template.jinja, https://huggingface.co/MiniMaxAI/MiniMax-M2.7/raw/main/LICENSE, https://huggingface.co/MiniMaxAI/MiniMax-M2.5/raw/main/chat_template.jinja, https://huggingface.co/MiniMaxAI/MiniMax-M2/raw/main/README.md, https://huggingface.co/MiniMaxAI/MiniMax-M1-80k/raw/main/config.json, https://recipes.vllm.ai/MiniMaxAI/MiniMax-M3

#### MiMo
- verified: yes (2026-10-16), basis: org listing, cards, configs, V2.6 `chat_template.jinja`, V2/V2.5 `tokenizer_config.json` templates. Not verified: MLX-LM and transformers support for `mimo_v2` (V2.5/V2.6).
- releases: `XiaomiMiMo/MiMo-V2.6-Pro-MOPD`, `XiaomiMiMo/MiMo-V2.6-Flash-MOPD` (2026-09-27), `XiaomiMiMo/MiMo-V2.6-Pro-RL`, `XiaomiMiMo/MiMo-V2.6-Flash-RL`, `XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B` (2026-09-21), `XiaomiMiMo/MiMo-V2.5-Pro-FP4-DFlash` (2026-06-08), `XiaomiMiMo/MiMo-V2.5-Pro`, `XiaomiMiMo/MiMo-V2.5` (+ `-Base`, 2026-04-27), `XiaomiMiMo/MiMo-V2-Flash` (+ `-Base`, 2025-12-16).
- template/tool/reasoning notes: V2.6-Pro-MOPD card: "MOPD upgrade of the MiMo-V2.6-Pro-RL checkpoint", targets repeated identical tool calls. V2.6 template accepts `tool_call.custom` and string `arguments` (inserted raw), renders `<think>{reasoning}</think>` for all past assistant turns; tools may also be attached per message (`message.tools`). V2 and V2.5 templates (in `tokenizer_config.json`) use the same `<function=`/`<parameter=` tags. Serve flags on V2.5/V2.6 cards: SGLang and vLLM `--reasoning-parser mimo --tool-call-parser mimo`.
- sources: https://huggingface.co/api/models?author=XiaomiMiMo&sort=createdAt&direction=-1&limit=150, https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Pro-RL/raw/main/README.md, https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Pro-RL/raw/main/chat_template.jinja, https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Pro-RL/raw/main/config.json, https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Pro-MOPD/raw/main/README.md, https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/raw/main/README.md, https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro/raw/main/README.md, https://huggingface.co/XiaomiMiMo/MiMo-V2.5/raw/main/tokenizer_config.json, https://huggingface.co/XiaomiMiMo/MiMo-V2-Flash/raw/main/README.md

#### Hunyuan / Hy
- verified: yes (2026-10-16), basis: org listing, cards, configs, Hy3 and Hy4-preview templates, A13B card, vLLM `hunyuan_a13b` parser source. Not verified: license of the dense 0.5-7B models (no metadata tag), A13B license terms (only `license: other`).
- releases: `tencent/Hy4-preview` + `-FP8` (2026-08-27), `tencent/Hy3-FP8` (2026-07-04), `tencent/Hy3` (2026-07-02), `tencent/Hy3-preview` + `-Base` (2026-04-13), `tencent/Hunyuan-A13B-Instruct` (+ FP8, GPTQ-Int4, GGUF; 2025-06/07), `tencent/Hunyuan-7B-Instruct`, `-4B-Instruct`, `-1.8B-Instruct`, `-0.5B-Instruct` (+ FP8/AWQ/GPTQ, 2025-07-30). Translation models `tencent/Hy-MT2-*` (2026-05) not examined.
- template/tool/reasoning notes: Hy3 template: `reasoning_effort` default `no_think`; invalid values raise; with tools, the system prompt tells the model to print `</tool_calls:opensource>` + reasoning-mode marker after function returns; `preserved_thinking` (default false, or true when tools are passed; same in Hy4) keeps reasoning for all turns, otherwise only after the last user turn. Hy4 template: default `high`, values `high` / `no_think` only; `no_think` overrides preserved thinking; tool responses re-ordered to match call IDs. Serve flags: Hy3 vLLM `--tool-call-parser hy_v3 --reasoning-parser hy_v3`, SGLang `hunyuan`; Hy4 vLLM `hy_v4`, SGLang `auto`. A13B: reasoning in `<think>`, answer in `<answer>`; vLLM parser extracts `<tool_calls>…</tool_calls>` JSON.
- sources: https://huggingface.co/api/models?author=tencent&sort=createdAt&direction=-1&limit=150, https://huggingface.co/tencent/Hy4-preview/raw/main/README.md, https://huggingface.co/tencent/Hy4-preview/raw/main/chat_template.jinja, https://huggingface.co/tencent/Hy4-preview/raw/main/config.json, https://huggingface.co/tencent/Hy3/raw/main/README.md, https://huggingface.co/tencent/Hy3/raw/main/chat_template.jinja, https://huggingface.co/tencent/Hy3/raw/main/config.json, https://huggingface.co/tencent/Hy3-preview/raw/main/chat_template.jinja, https://huggingface.co/tencent/Hunyuan-A13B-Instruct/raw/main/README.md, https://huggingface.co/tencent/Hunyuan-7B-Instruct/raw/main/config.json, https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/tool_parsers/hunyuan_a13b_tool_parser.py, https://recipes.vllm.ai/Qwen/Qwen3.8-27B (bundle incl. Hy3/Hy4 recipes)

#### ERNIE
- verified: yes (2026-10-16), basis: org listing sorted by creation date (newest ERNIE LLM 2025-11-07; later entries are image, video and OCR models), a hub search of the `PaddlePaddle` account for ERNIE (no entries returned), cards, configs, templates, vLLM `ernie45` parser source.
- releases: `baidu/ERNIE-4.5-VL-28B-A3B-Thinking` (2025-11-07), `baidu/ERNIE-4.5-21B-A3B-Thinking` (2025-09-08), `baidu/ERNIE-4.5-300B-A47B-PT`, `baidu/ERNIE-4.5-21B-A3B-PT`, `baidu/ERNIE-4.5-0.3B-PT`, `baidu/ERNIE-4.5-VL-424B-A47B-PT`, `baidu/ERNIE-4.5-VL-28B-A3B-PT` (2025-06-28), plus `-Paddle`, `-Base-*`, FP8/2-bit/W4A8 Paddle variants.
- template/tool/reasoning notes: Thinking template opens with a `<global_setting>` block containing `think_mode=True`; tools listed as a JSON array; assistant tool calls rendered as `<tool_call>\n{"name": …, "arguments": …}\n</tool_call>`; reasoning kept only for turns after the last user query. vLLM parser constants: `<think>`, `<response>`, `<tool_call>`. Card: vLLM parsers need the main branch at the time of release; FastDeploy uses `ernie_x1`.
- sources: https://huggingface.co/api/models?author=baidu&sort=createdAt&direction=-1&limit=150, https://huggingface.co/api/models?author=PaddlePaddle&search=ERNIE&sort=createdAt&direction=-1&limit=30, https://huggingface.co/baidu/ERNIE-4.5-21B-A3B-Thinking/raw/main/README.md, https://huggingface.co/baidu/ERNIE-4.5-21B-A3B-Thinking/raw/main/chat_template.jinja, https://huggingface.co/baidu/ERNIE-4.5-300B-A47B-PT/raw/main/config.json, https://huggingface.co/baidu/ERNIE-4.5-300B-A47B-PT/raw/main/chat_template.jinja, https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/tool_parsers/ernie45_tool_parser.py

#### Seed-OSS
- verified: yes (2026-10-16), basis: org listing (no newer open-weight LLM in that listing; `ByteDance-Seed/Cola-DLM` 2026-05-15 is a diffusion LM, not examined), card, config, template.
- releases: `ByteDance-Seed/Seed-OSS-36B-Instruct`, `ByteDance-Seed/Seed-OSS-36B-Base`, `ByteDance-Seed/Seed-OSS-36B-Base-woSyn` (2025-08-20).
- template/tool/reasoning notes: the template maps budgets to reflection intervals up to 16,384; `thinking_budget=0` pre-fills a closed think block with a budget-reflect note. The tool instruction text inside the template is in Chinese. Card serve flags: `--enable-auto-tool-choice --tool-call-parser seed_oss` and the repo `chat_template.jinja`.
- sources: https://huggingface.co/api/models?author=ByteDance-Seed&sort=createdAt&direction=-1&limit=150, https://huggingface.co/ByteDance-Seed/Seed-OSS-36B-Instruct/raw/main/README.md, https://huggingface.co/ByteDance-Seed/Seed-OSS-36B-Instruct/raw/main/chat_template.jinja, https://huggingface.co/ByteDance-Seed/Seed-OSS-36B-Instruct/raw/main/config.json

#### Ling / Ring
- verified: yes (2026-10-16), basis: org listing, cards, configs, Ling-3.1/3.0/2.6 and Ring-2.6 templates. Not verified: recommended parser for Ling 2.x JSON-in-tags (no serve flag on cards examined).
- releases: `inclusionAI/Ling-3.1-flash` (2026-10-13), `inclusionAI/Ling-3.0-flash-Fin` (2026-09-03), `inclusionAI/Ling-3.0-flash-VL` (2026-09-04), `inclusionAI/Ling-3.0-tiny` (+ fp8/int4/GGUF, 2026-08-10), `inclusionAI/Ling-3.0-flash` (+ fp8/fp4/int4/GGUF, 2026-08-02), `inclusionAI/Ring-2.6-1T` (2026-05-14), `inclusionAI/Ling-2.6-1T` (2026-04-29), `inclusionAI/Ling-2.6-flash` (2026-04-28), `inclusionAI/Ling-2.5-1T` (2026-02-14), `inclusionAI/Ring-2.5-1T` (2026-02-10), `inclusionAI/Ring-flash-linear-2.0-128k` (2025-10-21), `inclusionAI/Ring-1T` (2025-10-10), `inclusionAI/Ling-1T` (2025-10-02). Diffusion LMs `inclusionAI/LLaDA2.x` not examined.
- template/tool/reasoning notes: Ling 3.x template (`Bailing V3`): `enable_thinking` maps to `thinking_option` on/off; system turn always ends with `detailed thinking on|off` unless the user system prompt already contains it; tool results grouped under one `<role>OBSERVATION</role>` turn as `<tool_response>` blocks; argument values on a new line after `<arg_value>`. Ring-2.6: `reasoning_effort` default `high` rendered `Reasoning: high` in system. Serve flags: Ling-3.1 vLLM `--tool-call-parser ling3 --reasoning-parser ling3`; SGLang cards run without explicit parser flags in the sample command.
- sources: https://huggingface.co/api/models?author=inclusionAI&sort=createdAt&direction=-1&limit=150, https://huggingface.co/inclusionAI/Ling-3.1-flash/raw/main/README.md, https://huggingface.co/inclusionAI/Ling-3.1-flash/raw/main/chat_template.jinja, https://huggingface.co/inclusionAI/Ling-3.1-flash/raw/main/config.json, https://huggingface.co/inclusionAI/Ling-3.0-flash/raw/main/README.md, https://huggingface.co/inclusionAI/Ling-3.0-tiny/raw/main/README.md, https://huggingface.co/inclusionAI/Ling-2.6-flash/raw/main/chat_template.jinja, https://huggingface.co/inclusionAI/Ring-2.6-1T/raw/main/README.md, https://huggingface.co/inclusionAI/Ring-2.6-1T/raw/main/chat_template.jinja

#### InternLM / Intern-S
- verified: yes (2026-10-16), basis: org listing (search for `Intern-S`), cards, configs, templates. Not verified: llama.cpp support for Intern-S1/S2 GGUFs, active parameters of Intern-S2-397B (config shape equals Qwen3.5-397B-A17B).
- releases: `internlm/Intern-S2-397B` + `-FP8` (2026-09-13), `internlm/Atria-Dawn-Preview` + `-FP8` (2026-09-11; card: built on GLM-5.2; MIT), `internlm/Intern-S2-Mobius` + `-FP8` (2026-07-29), `internlm/Intern-S2-Preview-397B` (2026-07-16), `internlm/Intern-S2-Preview` (2026-05-15), `internlm/Intern-S1-Pro` (2026-02-02), `internlm/Intern-S1-mini` (+ FP8, GGUF; 2025-08-18), `internlm/Intern-S1` (+ FP8, GGUF; 2025-07-24), `internlm/internlm3-8b-instruct` (2025-01-13).
- template/tool/reasoning notes: Intern-S1 template injects a default thinking system prompt and a tool instruction that wraps JSON calls in `<|action_start|><|plugin|>` … `<|action_end|>` with key `parameters`. Intern-S1-Pro uses Hermes-style JSON in `<tool_call>`; generation prompt `<think>` unless `enable_thinking=false` (then `<think></think>`). Intern-S2 uses the Qwen3.5-style XML template plus `clear_thinking`. LMDeploy flags from cards: S1 `--reasoning-parser intern-s1 --tool-call-parser intern-s1`; S2 `--tool-call-parser interns2-preview`. Atria-Dawn-Preview card shows client configs with efforts low/medium/high (and an example listing low/medium/high/xhigh/max for another client).
- sources: https://huggingface.co/api/models?author=internlm&sort=createdAt&direction=-1&limit=150, https://huggingface.co/api/models?search=Intern-S1&author=internlm&limit=50, https://huggingface.co/internlm/Intern-S2-397B/raw/main/README.md, https://huggingface.co/internlm/Intern-S2-397B/raw/main/chat_template.jinja, https://huggingface.co/internlm/Intern-S2-397B/raw/main/config.json, https://huggingface.co/internlm/Intern-S2-Mobius/raw/main/README.md, https://huggingface.co/internlm/Intern-S2-Preview/raw/main/config.json, https://huggingface.co/internlm/Intern-S1-Pro/raw/main/README.md, https://huggingface.co/internlm/Intern-S1-Pro/raw/main/chat_template.jinja, https://huggingface.co/internlm/Intern-S1/raw/main/README.md, https://huggingface.co/internlm/Intern-S1/raw/main/chat_template.jinja, https://huggingface.co/internlm/Atria-Dawn-Preview/raw/main/README.md, https://huggingface.co/internlm/Atria-Dawn-Preview/raw/main/config.json

#### Step
- verified: yes (2026-10-16), basis: org listing, cards, configs, templates, Step-5 `LICENSE.txt`, vLLM recipe bundle. Not verified: allowed `reasoning_effort` values for Step-3.7 / Step-5.
- releases: `stepfun-ai/Step-5-Preview-NVFP4` (2026-10-14), `stepfun-ai/Step-5-Preview`, `stepfun-ai/Step-5-Preview-BF16` (2026-09-20), `stepfun-ai/Step-3.7-Flash-GGUF` (2026-05-28), `stepfun-ai/Step-3.7-Flash-NVFP4` (2026-05-27), `stepfun-ai/Step-3.7-Flash`, `-FP8` (2026-05-23), `stepfun-ai/Step-3.5-Flash-Base` (2026-03-02), `stepfun-ai/Step-3.5-Flash` + `-FP8` (2026-02-01), `stepfun-ai/Step-3.5-Flash-GGUF-Q8_0`, `-GGUF-Q4_K_S`, `stepfun-ai/Step3-VL-10B` (2026-01-13), `stepfun-ai/step3` (2025-07-28).
- template/tool/reasoning notes: Step-3.7 and Step-5 templates are Qwen3-Coder-style ChatML; Step-5 adds `preserve_thinking` (default true). The Step-5 vLLM recipe requires the `vllm/vllm-openai:step-5-preview` image with `--reasoning-parser step5 --tool-call-parser step5`; NVFP4 checkpoint limited to `--max-model-len 320000` in the recipe. Step-3.7 card lists llama.cpp among supported backends.
- sources: https://huggingface.co/api/models?author=stepfun-ai&sort=createdAt&direction=-1&limit=150, https://huggingface.co/stepfun-ai/Step-5-Preview/raw/main/README.md, https://huggingface.co/stepfun-ai/Step-5-Preview/raw/main/chat_template.jinja, https://huggingface.co/stepfun-ai/Step-5-Preview/raw/main/config.json, https://huggingface.co/stepfun-ai/Step-5-Preview/raw/main/LICENSE.txt, https://huggingface.co/stepfun-ai/Step-3.7-Flash/raw/main/README.md, https://huggingface.co/stepfun-ai/Step-3.7-Flash/raw/main/chat_template.jinja, https://huggingface.co/stepfun-ai/Step-3.5-Flash/raw/main/README.md, https://huggingface.co/stepfun-ai/Step-3.5-Flash/raw/main/chat_template.jinja, https://huggingface.co/stepfun-ai/step3/raw/main/config.json, https://recipes.vllm.ai/Qwen/Qwen3.8-27B (bundle incl. Step-5-Preview recipe)

#### LongCat
- verified: no (2026-10-16), basis: org listing, cards, configs, templates in `tokenizer_config.json`, vLLM `longcat` parser source. Reason for "no": SGLang parser for LongCat-2.0 not identified; LongCat-Flash active-parameter figure not checked.
- releases: `meituan-longcat/LongCat-Flash-Lite-Sparse` (2026-07-31), `meituan-longcat/LongCat-2.0` (2026-07-05), `meituan-longcat/LongCat-2.0-FP8`, `-INT8` (2026-07-03), `meituan-longcat/LongCat-Flash-Prover` (2026-03-20), `meituan-longcat/LongCat-Flash-Lite` + `-FP8` (2026-01-27), `meituan-longcat/LongCat-Flash-Thinking-2601` + `-FP8` (2026-01-14), `meituan-longcat/LongCat-Flash-Thinking` (2025-09-21), `meituan-longcat/LongCat-Flash-Chat` + `-FP8` (2025-08-29).
- template/tool/reasoning notes: LongCat-2.0 template converts content-block messages (`thinking`, `tool_use`, `tool_result` blocks) into chat messages before rendering; card shows three modes (thinking on, thinking on with all reasoning kept, thinking off). Card: GPU deployment via the SGLang cookbook; NPU via a separate SGLang fork. Weights MIT; card states no trademark or patent grant.
- sources: https://huggingface.co/api/models?author=meituan-longcat&sort=createdAt&direction=-1&limit=150, https://huggingface.co/meituan-longcat/LongCat-2.0/raw/main/README.md, https://huggingface.co/meituan-longcat/LongCat-2.0/raw/main/tokenizer_config.json, https://huggingface.co/meituan-longcat/LongCat-2.0/raw/main/config.json, https://huggingface.co/meituan-longcat/LongCat-Flash-Thinking-2601/raw/main/tokenizer_config.json, https://huggingface.co/meituan-longcat/LongCat-Flash-Lite/raw/main/tokenizer_config.json, https://huggingface.co/meituan-longcat/LongCat-Flash-Chat/raw/main/tokenizer_config.json, https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/tool_parsers/longcat_tool_parser.py

#### MiniCPM
- verified: yes (2026-10-16) for MiniCPM5-2B; MiniCPM-V-4.7 checkpoints listed only (not examined).
- releases: `openbmb/MiniCPM-V-4.7-35B-A3B` (2026-10-07), `openbmb/MiniCPM-V-4.7-1B` (2026-10-06), `openbmb/MiniCPM5-2B` (+ `-GGUF`, `-MLX`, `-GPTQ`, `-DSpark`; 2026-09-05/07), `openbmb/MiniCPM5-1B` (+ `-GGUF`, `-MLX`; 2026-05-21/24).
- template/tool/reasoning notes: tool guidelines and `<tools>` list in the system turn; assistant calls rendered as `<function name="…">` with `<param name="…">` children; past reasoning recovered from `<think>` in content. llama.cpp selects its MiniCPM5 handler on the `Tool usage guidelines:` marker.
- sources: https://huggingface.co/api/models?author=openbmb&sort=createdAt&direction=-1&limit=40, https://huggingface.co/openbmb/MiniCPM5-2B/raw/main/README.md, https://huggingface.co/openbmb/MiniCPM5-2B/raw/main/chat_template.jinja, https://huggingface.co/openbmb/MiniCPM5-2B/raw/main/config.json

#### Other same-ecosystem releases seen, not examined
- `Kwaipilot/KAT-Coder-V2.5-Dev` (2026-07-23, Apache-2.0 tag), `baichuan-inc/Baichuan-M3-235B` (2026-01-13, Apache-2.0 tag), `rednote-hilab/dots.llm1.base` (2026-10-06, MIT tag), `Skywork/R1V4` (2025-12-03). Tags only; verified: no.
- sources: https://huggingface.co/api/models?author=Kwaipilot&sort=createdAt&direction=-1&limit=40, https://huggingface.co/api/models?author=baichuan-inc&sort=createdAt&direction=-1&limit=40, https://huggingface.co/api/models?author=rednote-hilab&sort=createdAt&direction=-1&limit=40, https://huggingface.co/api/models?author=Skywork&sort=createdAt&direction=-1&limit=40

### Open-weight model families, part 2 (incl. harmony)

Conventions: `?` = not verified today. "registry" = the parser name is present in the engine's source registry on the default branch fetched 2026-10-16 (may be ahead of the latest release). Hub dates are repo creation dates unless a card states a release date. Engine registries fetched today, used by every family below: vLLM tool parsers https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/tool_parsers/__init__.py, vLLM reasoning parsers https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/reasoning/__init__.py, SGLang tool parsers https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/function_call/function_call_parser.py, SGLang reasoning parsers https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/parser/reasoning_parser.py, llama.cpp dedicated handlers https://github.com/ggml-org/llama.cpp/tree/master/common/parsers, Ollama parsers https://github.com/ollama/ollama/tree/main/model/parsers (listing may be truncated), MLX-LM architectures https://github.com/ml-explore/mlx-lm/tree/main/mlx_lm/models (listing truncated after `openelm.py`, so `phi3`, `smollm3` and later names are `?`).

Engine-wide findings that affect parser selection:
- llama.cpp: `--jinja` is on by default. `common/chat.h` now has only `CONTENT_ONLY`, `PEG_SIMPLE`, `PEG_NATIVE`, `PEG_GEMMA4`, `PEG_MINIMAX_M3` formats. Templates without a dedicated handler go through a "differential autoparser" derived from the template. Dedicated handlers seen today: `cohere2moe`, `deepseek`, `functionary-v3-2`, `gemma4`, `gigachat-v3`, `gpt-oss`, `k2-horizon`, `kimi-k2`, `kimi-k3`, `lfm2`, `ling3`, `llm-jp-harmony`, `minicpm5`, `minimax-m3`, `ministral3`, `muse-glimmer`, `muse-spark`, `qwen3-coder`, `translate-gemma`. Server flags: `--reasoning-format none|deepseek|deepseek-legacy` (default `auto`), `--reasoning on|off|auto`, `--reasoning-budget -1|0|N`, `--reasoning-effort minimal|low|medium|high|xhigh|max|default`. The request field `reasoning_effort: none` disables reasoning. Reasoning is returned in `reasoning_content`. `/v1/responses` works by converting to chat completions; `/v1/messages` exists.
- vLLM: parsers must be named explicitly (`--tool-call-parser`, `--reasoning-parser`). Generic `hf` tool and reasoning parsers exist (`ResponseTemplateToolParser`), details not researched. The response field was renamed `reasoning_content` -> `reasoning`. `reasoning_effort` low/medium/high injects `enable_thinking=true` and `none` injects false. `tool_choice="required"` uses structured outputs.
- SGLang: per its docs, Granite 4.2 can use `--tool-call-parser auto` and `--reasoning-parser auto`. The docs table lists `llama4`, but the source mapping fetched today does not show it (recheck).
- Ollama: built-in Go parsers per family. Thinking is returned in `message.thinking`. `think` accepts a bool or a level string listed in `/api/show` `thinking.values`.

#### harmony format
- verified: yes (2026-10-16), basis: format guide, renderer repo and PyPI page, gpt-oss model card, `config.json`, hub `chat_template.jinja`, engine docs and parser source (vLLM, SGLang, llama.cpp, Ollama).
- Tokens (encoding `o200k_harmony`, tiktoken-based): `<|start|>` 200006, `<|end|>` 200007, `<|message|>` 200008, `<|channel|>` 200005, `<|constrain|>` 200003, `<|return|>` 200002 (= `eos_token_id`), `<|call|>` 200012.
- Message sketch: `<|start|>{role}[ to={recipient}]<|channel|>{channel}[ to={recipient}][ <|constrain|>{type}]<|message|>{content}` + one of `<|end|>`, `<|call|>` (tool call, stop), `<|return|>` (final answer, stop).
- Roles and precedence: system > developer > user > assistant > tool.
- Channels: `analysis` = chain of thought, not user-facing; the guide says it is not held to the same safety standards as `final`. `commentary` = function tool calls and preambles (user-visible text before tool calls). `final` = the answer.
- System message: identity line (template variable `model_identity`), `Knowledge cutoff: 2024-06`, `Current date: YYYY-MM-DD`, `Reasoning: high` (or low/medium), `# Valid channels: analysis, commentary, final. Channel must be included for every message.` When functions exist, add `Calls to these tools must go to the commentary channel: 'functions'.` Built-in tools are declared here under `# Tools`.
- Developer message: instructions, then `# Tools` / `## functions` / `namespace functions { ... }` in TypeScript-like syntax (`//` comments, one argument named `_`, `type f = () => any;` for no-argument functions, return type `any`). Structured output: `# Response Formats` / `## {name}` + JSON Schema. This only steers the model; enforcement needs constrained sampling.
- Tool call: `<|start|>assistant<|channel|>commentary to=functions.get_current_weather <|constrain|>json<|message|>{"location":"San Francisco"}<|call|>`. Tool result: `<|start|>functions.get_current_weather to=assistant<|channel|>commentary<|message|>{...}<|end|>`. Then sample again with the full history, including the analysis message that preceded the call.
- Built-in tools: `browser` (`search`, `open`, `find`; recipients like `browser.search`; `analysis` channel). `python` (stateful notebook, 120 s timeout, `/mnt/data`; recipient `python`; `analysis` channel).
- Effort: `low`, `medium`, `high`; default medium; no documented off level.
- Cross-turn rules: in history, replace the trailing `<|return|>` with `<|end|>`. Drop earlier `analysis` once a turn ended in `final`. Pass the chain of thought back while tool calls are in flight.
- Hub template (`openai/gpt-oss-20b` `chat_template.jinja`):
  - reasoning field is `thinking`, not `reasoning_content`;
  - it renders `thinking` only on tool-call messages with no later final message;
  - it raises if a tool-call message has both `content` and `thinking`, if content contains `<|channel|>analysis<|message|>` or `<|channel|>final<|message|>`, or if a tool message has no preceding tool call;
  - default `reasoning_effort` is `medium`; the date comes from `strftime_now`;
  - it renders past calls as `<|start|>assistant to=functions.NAME<|channel|>commentary json<|message|>{args}<|call|>`, without `<|constrain|>`, so re-rendered history differs from the tokens the model generated;
  - tool results are `|tojson`-encoded.
- Renderer library: repo https://github.com/openai/harmony, `Apache-2.0`.
  - Rust core with Python bindings: PyPI `openai-harmony` 0.0.8 (2025-11-05), abi3 wheels for CPython >=3.8 on Windows, macOS arm64 and Linux glibc/musl. Earlier versions: 0.0.6 (2025-11-05), 0.0.4 (2025-08-09), 0.0.3, 0.0.2, 0.0.1 (2025-08).
  - The repo also has a `javascript/` folder; no npm package confirmed. No GitHub releases are published (tags not checked); Rust users take a git dependency.
  - Python API: `load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)`, `enc.render_conversation_for_completion(convo, Role.ASSISTANT)`, `enc.parse_messages_from_completion_tokens(tokens, role=Role.ASSISTANT)`. Rust: `HarmonyEncodingName::HarmonyGptOss`. A streaming parser class was not confirmed on the fetched pages.
  - The tokenizer vocab is downloaded at runtime. vLLM documents a `HarmonyError` when that download fails; the fix is to pre-download and set `TIKTOKEN_ENCODINGS_BASE`.

Engine handling:

| Engine | Tool calls | Reasoning | Effort control | Known issues |
|---|---|---|---|---|
| vLLM | `--tool-call-parser openai --enable-auto-tool-choice` | `openai_gptoss` is registered, but the recipe omits it; Chat Completions returns reasoning separately; `include_reasoning: false` drops it | not documented in the recipe | function calling only with `tool_choice="auto"`; built-in tools (`--tool-server demo` or MCP `host:port` list) only on `/v1/responses`; Responses API usage is all zeros, streaming "fairly barebone" |
| SGLang | `--tool-call-parser gpt-oss` | `--reasoning-parser gpt-oss` (`GptOssDetector` wraps a `HarmonyParser`, `force_reasoning` true) | `reasoning_effort` low/medium/high (Responses API example) | parser drops `analysis`, so the final text can be empty; return results as `role="tool"`; offline engine needs `no_stop_trim: True`; docs show 0.165 invalid-output rate on GSM8K for 20b vs 0.005 for 120b |
| llama.cpp | dedicated PEG handler `common/parsers/gpt-oss.cpp`; args constrained by the tool JSON Schema; lazy grammar triggers | copies `reasoning_content` to `thinking`; thinking tags `<\|channel\|>analysis<\|message\|>` ... `<\|end\|>` | `--reasoning-effort` / request `reasoning_effort` passed to the template | erases `content` on history messages that carry tool calls (preambles lost); replaces the last `<\|return\|>` with `<\|end\|>` (issue 15417); swallows unsolicited recipient messages, so built-in tools are not executed |
| Ollama | built-in Go parser (`harmony/harmonyparser.go`) | `message.thinking` | `think`: `"low"`, `"medium"`, `"high"` (default medium) | disabling gpt-oss thinking is not documented |
| transformers | hub template | `thinking` field | `reasoning_effort` template variable | template output differs from the renderer (see above) |

- Harmony-like dialects: the Muse family uses `<|start|>`, `<|message|>`, ` to=` recipients and `<|eot|>`/`<|eom|>` without channels. LLM-jp-4.1 is listed as a "Harmony dialect" in llama.cpp docs. Neither is wire-compatible with gpt-oss harmony.
- Secondary sources (search snippets only, not opened, not relied on):
  - vLLM PR #29236 (2025-11) fixes streaming tool calls left in the active buffer;
  - a vendor-forum report says `--reasoning-parser openai_gptoss` returned HTTP 400 for non-streaming requests in one container build;
  - an arXiv paper (2604.00362) argues the Chat Completions conversion of harmony is lossy.
- sources:
  - https://developers.openai.com/cookbook/articles/openai-harmony (redirect target of https://cookbook.openai.com/articles/openai-harmony)
  - https://github.com/openai/harmony
  - https://github.com/openai/harmony/releases
  - https://pypi.org/project/openai-harmony/
  - https://huggingface.co/openai/gpt-oss-120b
  - https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json
  - https://huggingface.co/openai/gpt-oss-20b/raw/main/chat_template.jinja
  - https://docs.vllm.ai/projects/recipes/en/latest/OpenAI/GPT-OSS.html
  - https://docs.vllm.ai/en/latest/features/tool_calling.html
  - https://docs.vllm.ai/en/latest/features/reasoning_outputs.html
  - https://docs.sglang.io/basic_usage/gpt_oss.html
  - https://docs.sglang.io/advanced_features/tool_parser.html
  - https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/parsers/gpt-oss.cpp
  - https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/chat.h
  - https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/function-calling.md
  - https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md
  - https://github.com/ollama/ollama/tree/main/harmony
  - https://docs.ollama.com/capabilities/thinking
  - https://ollama.com/library/gpt-oss
  - plus the engine registries listed under Conventions
  - secondary: https://github.com/vllm-project/vllm/pull/29236, https://forums.developer.nvidia.com/t/support-for-openai-gptoss-reasoning-parser-in-vllm-and-its-impact-on-the-effective-inference-performance-on-spark/358421, https://www.alphaxiv.org/abs/2604.00362

#### gpt-oss
- verified: yes (2026-10-16), basis: model card, `config.json`, hub org model listing, Ollama library page.
- Releases: `openai/gpt-oss-120b`, `openai/gpt-oss-20b` (hub repos 2025-08-04). `openai/gpt-oss-safeguard-120b`, `openai/gpt-oss-safeguard-20b` (repos 2025-09-18) are safety-classification variants. Newer repos in the org (`openai/privacy-filter`, `openai/circuit-sparsity`) are not chat LLMs. One third-party site claims a 2026 gpt-oss release with 8B/20B/120B and 256K context; it is not on the hub, so treat it as false.
- Card: "117B parameters with 5.1B active parameters" (120b), 21B / 3.6B (20b). MXFP4 post-trained MoE weights; 120b fits one 80 GB GPU, 20b fits 16 GB. The card says the models "should only be used with the harmony format as it will not work correctly otherwise". Tools: browsing, python, function calling, structured outputs.
- `config.json` (120b): `GptOssForCausalLM`, 36 layers, 128 local experts, 4 per token, hidden 2880, vocab 201088, `sliding_window` 128 alternating with full attention, `max_position_embeddings` 131072, YaRN factor 32 over 4096.
- FIM: none documented.
- sources: https://huggingface.co/openai/gpt-oss-120b, https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json, https://huggingface.co/api/models?author=openai&sort=createdAt&direction=-1&limit=40, https://ollama.com/library/gpt-oss

#### Llama
- verified: partial (2026-10-16), basis: hub org listing and the official prompt-format pages. Licenses, total parameters and Llama 3.x context were not re-verified.
- Releases: the newest LLM repos in that org are Llama 4 Scout/Maverick (2025-04). Later repos in that org are only guard/classifier models (`meta-llama/Llama-Guard-4-12B`, `meta-llama/Llama-Prompt-Guard-2-86M`). The newer open-weight line from the same publisher lives under `meta-models` (Muse, below).
- Llama 3.1 template: `<|begin_of_text|>`, `<|start_header_id|>` / `<|end_header_id|>`, `<|eot_id|>` (end of turn), `<|eom_id|>` (end of message), `<|python_tag|>`. Custom tools: "Respond in the format {"name": function name, "parameters": dictionary of argument name and its value}", or the user-defined `<function=example_function_name>{"example_name": "example_value"}</function>`. Built-ins via `Tools: brave_search, wolfram_alpha`; `Environment: ipython` enables code. Tool output uses role `ipython`.
- Llama 4 template: `<|header_start|>` / `<|header_end|>`, `<|eot|>`. Zero-shot pythonic calls: "Use exact format: [func_name1(param1=value1, param2=value2), func_name2(...)]". A JSON alternative uses `name`/`parameters` objects. Tool output role is `ipython`. Context: Scout 10M, Maverick 1M.
- Parsers: vLLM `llama3_json` (no parallel), `llama4_json`, `llama4_pythonic` (parallel), `pythonic`. SGLang `llama3`, `pythonic`. llama.cpp docs list Llama 3.x as native. MLX `llama`, `llama4`, `llama4_text`.
- FIM: none.
- sources: https://huggingface.co/api/models?author=meta-llama&sort=createdAt&direction=-1&limit=30, https://dev.meta.ai/llama/docs/model-cards-and-prompt-formats/llama4/, https://dev.meta.ai/llama/docs/model-cards-and-prompt-formats/llama3_1/ (the Llama 4 page was reached via redirects from https://www.llama.com/docs/model-cards-and-prompt-formats/llama4/ and https://developer.meta.com/ai/docs/model-cards-and-prompt-formats/llama4/), https://docs.vllm.ai/en/latest/features/tool_calling.html, https://docs.sglang.io/advanced_features/tool_parser.html

#### Muse
- verified: yes for IDs, sizes, licenses and template; partial for engine flags (cards give none, names come from registries).
- Releases: `meta-models/Muse-Glimmer-30B` (+ `-GGUF`, `-assistant`, `-ExecuTorch-PTE`), card date August 2026. `meta-models/Muse-Spark-Open-816B-A42B` (+ `-NVFP4`), card "October 15th, 2026".
- Glimmer: "29.6B (including vision encoder)", 52 layers, hidden 6656, [Local, Local, Local, Global] with sliding window 2048, gated attention, 32 Q / 2 KV heads, RoPE on local layers only, vocab 202,048; context "131,072+"; `Apache-2.0`; text and image in. Sampling: temperature 1.0, top_p 0.95, top_k 64.
- Spark Open: MoE 816B / 42B, 256 experts top-8, 62 layers, 128 Q / 16 KV heads, gated attention, QK-norm; context "1M at inference"; "Muse Model License"; text, image, video and audio in. `reasoning_effort` low/medium/high/xhigh (default xhigh) via the request or `chat_template_kwargs`; other values error. The card lists llama.cpp, vLLM, SGLang and MLX support without flags.
- Template (Glimmer `chat_template.jinja`):
  - system `<|start|>system<|message|>`; user `<|start|>user<|message|>`;
  - reasoning `<|start|>assistant to=self<|message|>...<|eom|>`;
  - tool call `<|start|>assistant to=NAME<|message|><atem:function_calls>...`;
  - tool result `<|start|>tool NAME<|message|><tool_output name="NAME">...</tool_output><|eot|>`;
  - generation prompt `<|start|>assistant`;
  - `# Valid recipients:` lists `self`, `<namespace>.*`, `user`;
  - `Reasoning strength: high` is injected by default, and "Reasoning effort" in a system prompt is rewritten to "Reasoning strength";
  - tool schemas are listed as JSON under `// Function schemas`;
  - arguments must be a mapping;
  - prior `reasoning_content` is always re-rendered.
- FIM: none documented.
- sources: https://huggingface.co/meta-models/Muse-Glimmer-30B, https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/chat_template.jinja, https://huggingface.co/meta-models/Muse-Spark-Open-816B-A42B, https://huggingface.co/api/models?author=meta-models&sort=createdAt&direction=-1&limit=30

#### Gemma
- verified: yes for Gemma 4 and FunctionGemma; partial for Gemma 3 / 3n (license and IDs only; the Gemma 3 template was not fetched today).
- Releases: Gemma 4 repos `-E2B(-it)`, `-E4B(-it)` (2026-03-02), `-26B-A4B(-it)`, `-31B(-it)` (2026-03-11/12), `-12B(-it)` (2026-05-23). QAT repos (`-it-qat-q4_0-gguf`, `-qat-w4a16-ct`, `-qat-mobile-*`) and `-it-assistant` repos also exist; the purpose of `-assistant` is not stated on the fetched card.
- Card facts:
  - layers: E2B 35, E4B 42, 12B 48, 26B-A4B 30, 31B 60;
  - sliding window 512 (E-sizes) or 1024;
  - global layers use unified keys and values and proportional RoPE;
  - the 12B is encoder-free (image and audio go through linear layers);
  - audio input on E2B, E4B and 12B;
  - sampling: temperature 1.0, top_p 0.95, top_k 64.
- Template (E4B `chat_template.jinja`):
  - turns `<|turn>{role}\n` ... `<turn|>\n`; assistant role is `model`; generation prompt `<|turn>model\n`;
  - tool declarations `<|tool>declaration:NAME{description:<|"|>...<|"|>,parameters:{...type:<|"|>OBJECT<|"|>}}<tool|>`;
  - calls `<|tool_call>call:NAME{key:value}<tool_call|>` with keys sorted (`dictsort`); string arguments as JSON strings raise "tool_calls[].function.arguments must be a JSON object (mapping), not a string.";
  - results `<|tool_response>response:NAME{...}<tool_response|>`, taken from `role: tool` messages or a `tool_responses` field;
  - thinking: `<|think|>\n` in the system turn; rendered as `<|channel>thought\n...\n<channel|>` only after the last user message, or on tool-call turns when `preserve_thinking` is set.
- FunctionGemma: `google/functiongemma-270m-it`, built on Gemma 3 270M, hub license `gemma` (gated), 32K context. Example call: `<start_function_call>call:get_current_temperature{location:<escape>London<escape>}<end_function_call>`. The developer role is "required". The card says it is not intended as a direct dialogue model.
- Gemma 3n: `google/gemma-3n-E4B-it` (2025-06-03), license `gemma`, gated, 7,849,978,192 parameters in safetensors.
- FIM: none documented.
- sources: https://huggingface.co/api/models?author=google&search=gemma&sort=createdAt&direction=-1&limit=40, https://huggingface.co/google/gemma-4-26B-A4B-it, https://huggingface.co/google/gemma-4-E4B-it/raw/main/chat_template.jinja, https://huggingface.co/google/functiongemma-270m-it, https://huggingface.co/api/models/google/gemma-3n-E4B-it

#### Phi
- verified: partial. Phi-4-mini-instruct was verified from its card. Other Phi models were verified from the hub listing only (IDs and dates).
- Phi-4-mini-instruct: 3.8B dense decoder-only (hub shows 4B), 200K vocabulary, GQA, shared input/output embeddings, 128K context, `MIT`. Chat format: `<|system|>Insert System Message<|end|><|user|>Insert User Message<|end|><|assistant|>`. Tools go in the system message "wrapped by <|tool|> and <|/tool|> tokens". The tool-call output format is not documented on the card. vLLM registry: `phi4_mini_json`.
- Newest Phi repos: `microsoft/Phi-4-reasoning-vision-15B` (2026-01-23), `microsoft/Phi-Ground-Any` (2026-05; GUI grounding, not a chat LLM), `microsoft/Phi-4-mini-flash-reasoning` (2025-06-19). Licenses and formats for these: ?
- FIM: none documented.
- sources: https://huggingface.co/api/models?author=microsoft&search=phi&sort=createdAt&direction=-1&limit=30, https://huggingface.co/microsoft/Phi-4-mini-instruct

#### Mistral family
- verified: yes for Small 4, Medium 3.5, Devstral 2 (license), Devstral Small 2, Ministral 3 and Large 3 (license). Not verified today: Magistral details, Codestral (open-weight status, FIM tokens), Leanstral details.
- Releases (hub repo dates): Small 4 119B (2026-01, `-NVFP4` and `-eagle` 2026-03); Medium 3.5 128B (2026-03-31, `-EAGLE` 2026-04); Large 3 675B Instruct/Base (2025-11); Devstral 2 123B and Devstral Small 2 24B (2025-11-28); Ministral 3 3B/8B/14B Base/Instruct/Reasoning + GGUF (2025-10-31); Magistral Small 2509; Leanstral 2603 and 1.5-119B-A6B (Lean prover models).
- Small 4: "128 experts, 4 active", 119B total, 6.5B active, 256K, `Apache-2.0`, text and image in. `reasoning_effort` `'none'` or `'high'` (temperature 0.7 for high). vLLM: `--tool-call-parser mistral --enable-auto-tool-choice --reasoning-parser mistral --attention-backend FLASH_ATTN_MLA`; needs `mistral_common >= 1.11.0`; transformers main.
- Medium 3.5: "Dense 128B parameters", 256K, "Modified MIT License" with "exceptions for companies with large revenue" (threshold not on card). Same vLLM parsers.
- Devstral 2: hub license `other`; `LICENSE` is "Modified MIT", with no rights if "global consolidated monthly revenue ... exceeds $20 million". 125,025,989,840 parameters; hub tag `ministral3`.
- Devstral Small 2: 24B, "same architecture as Ministral 3 using rope-scaling as introduced by Llama 4", 256K, `Apache-2.0`, image input, temperature 0.15. vLLM `--tool-call-parser mistral --enable-auto-tool-choice` (`mistral_common >= 1.8.6`). SGLang `--tool-call-parser mistral` (transformers 5.0.0rc). llama.cpp needs the changes from PR #17945. Ollama `devstral-small-2`. System prompt file `CHAT_SYSTEM_PROMPT.txt`.
- Ministral 3: Instruct is FP8 and Reasoning is BF16. 8B = 8.4B LM + 0.4B vision encoder; 256K; `Apache-2.0`. vLLM >= 0.12.0 with `--tokenizer_mode mistral --config_format mistral --load_format mistral --enable-auto-tool-choice --tool-call-parser mistral --reasoning-parser mistral`. The card says "We highly recommend keeping the reasoning traces in context" and asks you to append the provided system prompt.
- Large 3: hub license `apache-2.0`; architecture not re-verified.
- Template (Devstral Small 2):
  - default `[SYSTEM_PROMPT]...[/SYSTEM_PROMPT]` when none is given; `[AVAILABLE_TOOLS]` + `tools|tojson` + `[/AVAILABLE_TOOLS]` once before the first user turn; `[INST]...[/INST]`;
  - calls `[TOOL_CALLS]` + name + `[ARGS]` + arguments (empty becomes `{}`); assistant turns end with `eos_token`;
  - results `[TOOL_RESULTS]` + content + `[/TOOL_RESULTS]` (no id or name); no `[THINK]` handling in this template;
  - adjacent same-role messages are merged; role transitions are checked (e.g. a conversation must start with user or system).
- vLLM docs: the official format uses `--tokenizer_mode mistral`, the transformers format uses `--tokenizer_mode hf`; a tool-call ID length issue applies to the transformers backend; Mistral 7B struggles with parallel calls. SGLang reasoning detector `mistral` uses `[THINK]` / `[/THINK]`.
- FIM: not verified today.
- sources: https://huggingface.co/api/models?author=mistralai&sort=createdAt&direction=-1&limit=50, https://huggingface.co/mistralai/Mistral-Small-4-119B-2603, https://huggingface.co/mistralai/Mistral-Medium-3.5-128B, https://huggingface.co/api/models/mistralai/Mistral-Large-3-675B-Instruct-2512, https://huggingface.co/api/models/mistralai/Devstral-2-123B-Instruct-2512, https://huggingface.co/mistralai/Devstral-2-123B-Instruct-2512/raw/main/LICENSE, https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512, https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512/raw/main/chat_template.jinja, https://huggingface.co/mistralai/Ministral-3-8B-Reasoning-2512, https://docs.vllm.ai/en/latest/features/tool_calling.html

#### Granite
- verified: yes for 4.2 and 4.1. Granite 4.0 hybrid models and Granite Switch were not researched.
- 4.2 (card release date August 25, 2026): "Decoder-only Dense Transformer (Reasoning)".
  - 30B: 64 layers, 32 heads / 8 KV, MLP 32768, RoPE theta 10,000,000.
  - 128K native, with "Long-context extension to 512K"; `Apache-2.0`.
  - Thinking is the default; `enable_thinking=False` makes the prompt end with `<think></think>`; `enable_thinking=True, low_effort=True` gives low effort; `truncate_history_thinking=True` by default.
  - Tool calls use `<tool_call>` + `<function=...>` + `<parameter=...>`.
  - vLLM: `--tool-call-parser qwen3_coder --enable-auto-tool-choice`, `--reasoning-parser granite_thinking_parser` (the card ships a plugin for v0.20+; vLLM main now registers it built-in), or `nemotron_v3`. SGLang >=0.5.18: `auto` resolves to `qwen3_coder` / `nemotron_3`.
- 4.1-8b (card release date April 29th, 2026): dense, 131,072 context, `Apache-2.0`. Tools: "return a json object with function name and arguments within <tool_call></tool_call> XML tags". FIM is listed as a capability; tokens not given.
- vLLM also registers `granite` (3.x), `granite-20b-fc`, `granite4`, and reasoning `granite` (3.2). The llama.cpp docs use `ibm-granite/granite-4.1-3b-GGUF:Q4_K_M` as an example.
- sources: https://huggingface.co/api/models?author=ibm-granite&search=granite-4&sort=createdAt&direction=-1&limit=50, https://huggingface.co/ibm-granite/granite-4.2-30b, https://huggingface.co/ibm-granite/granite-4.1-8b, https://docs.vllm.ai/en/latest/features/reasoning_outputs.html

#### OLMo
- verified: partial. IDs, license, chat style and parser names are verified. Context length, hybrid layer layout and the Think tag format are not.
- Releases: Olmo 3 (7B, 32B; Instruct, Think, RL-Zero; 2025-11), Olmo 3.1 32B Instruct/Think (2025-12), Olmo Hybrid 7B (base 2026-01; Instruct DPO and Think SFT 2026-02).
- Olmo 3 7B Instruct: `Apache-2.0` ("meant for research and educational use" under responsible-use guidelines). `<|im_start|>`/`<|im_end|>`, with `<|endoftext|>` closing assistant turns. Default system message: "You are a helpful function-calling AI assistant. You do not currently have access to any functions." Recommended sampling: temperature 0.6, top_p 0.95. Transformers >= 4.57.0.
- Olmo Hybrid: "hybrid RNN"; the vLLM example sets `mamba_ssm_cache_dtype="float32"`; transformers >= 5.3.0.
- vLLM `olmo3` tool parser (`Olmo3PythonicToolParser`): newline-delimited calls in `<function_calls>`. vLLM also has an `olmo3` reasoning parser.
- FIM: none documented.
- sources: https://huggingface.co/api/models?author=allenai&search=olmo&sort=createdAt&direction=-1&limit=40, https://huggingface.co/allenai/Olmo-3-7B-Instruct, https://huggingface.co/allenai/Olmo-Hybrid-Instruct-DPO-7B, https://docs.vllm.ai/en/latest/features/tool_calling.html

#### Nemotron
- verified: yes for 3.5 Lightning, 3.5 Bolt and the 3 Super license. Not researched: 3.5 Super VL, 3 Ultra, the Nano 4B card, and Nemotron Nano 2.
- 3.5 Lightning (release August 11, 2026): "Mixture-of-Experts Hybrid (Mamba + Transformer)", 30B / 3B, "Up to 1M tokens", OpenMDW-1.1. `enable_thinking` is on by default. vLLM `--reasoning-parser nemotron_v3 --tool-call-parser qwen3_coder --enable-auto-tool-choice`; SGLang `--reasoning-parser nemotron_3 --tool-call-parser qwen3_coder`.
- 3.5 Bolt (hub release 10/15/2026): "Mamba-2-Transformer hybrid LatentMoE", 74B / 9B, vision encoder, 256K, OpenMDW-1.1. `enable_thinking`; medium effort via `{"enable_thinking": True, "medium_effort": True}`. vLLM `qwen3_coder` + reasoning parser `ultra_v3` from plugin file `ultra_v3_reasoning_parser.py`. The `-24GB` variant "uses a native 2-bit quantization to fit in 24GB"; `-LiLiCorr` repos are speculative-decoding drafters.
- 3 Super: hub `license_name` `nvidia-nemotron-open-model-license`, arch `NemotronHForCausalLM` / `nemotron_h`, 123,611,012,096 parameters, tags `latent-moe`, `mtp`, `custom_code`.
- FIM: none documented.
- sources: https://huggingface.co/api/models?author=nvidia&search=Nemotron-3&sort=createdAt&direction=-1&limit=50, https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16, https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-BF16, https://huggingface.co/api/models/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16

#### Falcon
- verified: partial. Falcon H1R-7B is verified; H1-Tiny is verified by ID only. The 2025 Falcon-H1 sizes were not re-verified.
- H1R-7B: "Hybrid (Transformers + Mamba2)", hub 8B, "Falcon-LLM License", `<think>...</think>`. vLLM >= 0.11.0 `--reasoning-parser deepseek_r1`, tools `--enable-auto-tool-choice --tool-call-parser hermes`. SGLang `--reasoning-parser deepseek-r1`. Sampling: temperature 0.6, top-p 0.95, up to 65,536 new tokens. Paper arXiv 2601.02346.
- H1-Tiny (2026-01): 90M to 0.6B models, including tool-calling and coder variants.
- sources: https://huggingface.co/api/models?author=tiiuae&sort=createdAt&direction=-1&limit=30, https://huggingface.co/tiiuae/Falcon-H1R-7B

#### SmolLM3
- verified: yes. `HuggingFaceTB/SmolLM3-3B` is still the newest SmolLM; the only later repo is a 2026-04 GSM8K SFT checkpoint.
- Card: "GQA and NoPE (with 3:1 ratio)", 11.2T tokens, 64K trained, YaRN `factor` 2.0 over 65,536 for 128K, `Apache-2.0`. Six native languages.
- Thinking is on by default; `/think` and `/no_think` in the system prompt override `enable_thinking`. Tools: `xml_tools` -> `<tool_call>{"name": "get_weather", "arguments": {"city": "Copenhagen"}}</tool_call>`; `python_tools` -> `<code>get_weather(city="Copenhagen")</code>`. vLLM `--enable-auto-tool-choice --tool-call-parser=hermes`.
- sources: https://huggingface.co/HuggingFaceTB/SmolLM3-3B, https://huggingface.co/api/models?author=HuggingFaceTB&search=SmolLM&sort=createdAt&direction=-1&limit=20

#### LFM2 / LFM2.5
- verified: yes for LFM2.5-24B-A2B. Other sizes are verified by hub ID only. The original LFM2 models were not re-verified.
- 24B-A2B: 30 "double-gated short-convolution" + 10 GQA blocks, 64 experts top-4, 24.0B / ~2.45B, vocab 128,000, 131,072 context (up from 32K in LFM2-24B-A2B), license tag `lfm1.0`. ChatML with `<|startoftext|>`. The card calls it "a pure reasoning model that always thinks before it answers". Pythonic calls go between `<|tool_call_start|>` and `<|tool_call_end|>`; JSON is possible via the system prompt.
- Registries: vLLM tool `lfm2` (no reasoning parser), SGLang `lfm2`, llama.cpp `lfm2.cpp`, Ollama `lfm2`, MLX `lfm2`, `lfm2_moe`, `lfm2-vl`.
- sources: https://huggingface.co/api/models?author=LiquidAI&search=LFM&sort=createdAt&direction=-1&limit=40, https://huggingface.co/LiquidAI/LFM2.5-24B-A2B

#### Command A
- verified: yes for Command A+, North-Mini-Code and the Command A Reasoning license. The `c4ai-command-a-03-2025` license and the raw chat-template token syntax were not re-fetched.
- Command A+ (`command-a-plus-05-2026-*`): "decoder-only Sparse Mixture-of-Experts", 218B / 25B, 128 experts, 8 active + 1 shared, sliding RoPE / global NoPE at 3:1, 128K in / 64K out, 48 languages, `Apache-2.0`. Thinking `<|START_THINKING|>`...`<|END_THINKING|>` (the card prose is inconsistent about the pipes). Optional citations `<co>...</co: 0:[0]>`. vLLM >= 0.21.0 + `cohere_melody` >= 0.9.0, `--tool-call-parser cohere_command4 --reasoning-parser cohere_command4 --enable-auto-tool-choice`.
- North-Mini-Code-1.0: "open weights research release of a 30B-A3B parameter model" for agentic coding, 256K in / 64K out, `Apache-2.0`. Interleaved thinking; pass `reasoning` and tool calls back. vLLM main, `--max-model-len 320000`, `cohere_command4` parsers. SWE-bench Verified 67.6 (card).
- Command A Reasoning 08-2025: `cc-by-nc-4.0`, with a gate checkbox "I agree to use this model for non-commercial use ONLY"; 111,057,580,032 parameters.
- FIM: none documented.
- sources: https://huggingface.co/api/models?author=CohereLabs&sort=createdAt&direction=-1&limit=30, https://huggingface.co/CohereLabs/command-a-plus-05-2026-bf16, https://huggingface.co/CohereLabs/North-Mini-Code-1.0, https://huggingface.co/api/models/CohereLabs/command-a-reasoning-08-2025, https://docs.vllm.ai/en/latest/features/reasoning_outputs.html

#### Apertus
- verified: partial. v1.5-8B is verified from its card; other IDs from the hub listing and SGLang docs. Template token names are not verified.
- v1.5-8B: decoder-only, xIELU activation, AdEMAMix optimizer, 19T tokens, context "up to 262,144", `apache-2.0` + acceptable-use policy, image and audio input. Thinking is off by default; enable it with `enable_thinking=True`. Reasoning sits between `<|inner_prefix|>` and `<|inner_suffix|>`. vLLM (modified fork container image for now): `--default-chat-template-kwargs.enable_thinking true --reasoning-parser apertus`, `--enable-auto-tool-choice --tool-call-parser apertus`. Tool calling is not supported in thinking mode. Transformers needs a fork branch.
- vLLM main registers tool `apertus` but no reasoning parser `apertus`. SGLang: `apertus2509` for both tools and reasoning.
- sources: https://huggingface.co/api/models?author=swiss-ai&sort=createdAt&direction=-1&limit=25, https://huggingface.co/swiss-ai/Apertus-v1.5-8B, https://docs.sglang.io/advanced_features/tool_parser.html

#### Trinity / AFM
- verified: yes for Large Thinking and Mini. The Nano size and the Large Preview card were not checked.
- Large Thinking: "398B-parameter sparse Mixture-of-Experts (MoE) model with approximately 13B active", 256 experts, 1 shared, 4 active, 6 dense layers, 8,192 pretraining context, 512K after extension, OpenMDW-1.1. `<think>` traces "must be kept in context". vLLM >= 0.11.1: `--reasoning-parser deepseek_r1 --enable-auto-tool-choice --tool-call-parser qwen3_coder`. Technical report arXiv 2602.17004.
- Mini: "26B MoE model with 3B active parameters", 128K, OpenMDW-1.1, vLLM `--tool-call-parser hermes`, `--reasoning-parser deepseek_r1`.
- Registries: SGLang `trinity` tool detector; MLX `afmoe`, `afm7`. `arcee-ai/AFM-4.5B-Base-KDA-*` (2025-12) are research base models.
- sources: https://huggingface.co/api/models?author=arcee-ai&sort=createdAt&direction=-1&limit=30, https://huggingface.co/arcee-ai/Trinity-Large-Thinking, https://huggingface.co/arcee-ai/Trinity-Mini

#### Laguna (coding-focused MoE)
- verified: yes for S 2.1 (card). XS 2.1 and M.1 sizes come from the S 2.1 card; `poolside/Laguna-XS-2.1` (unsuffixed) was not seen in the listing.
- S 2.1: 118B / 8B, 256 routed experts top-10 + 1 shared, softplus gating, GQA with 8 KV heads, 12 global + 36 sliding-window (512) layers, 1,048,576 context, OpenMDW-1.1. Interleaved thinking with preserved thinking ("Keep `reasoning_content`"). Per-request `chat_template_kwargs: {"enable_thinking": false}`.
- Flags: vLLM `--tool-call-parser poolside_v1 --reasoning-parser poolside_v1 --enable-auto-tool-choice`; SGLang same names; TRT-LLM `--tool_parser poolside_v1 --reasoning_parser laguna`.
- llama.cpp: fork branch `laguna`; upstream PR #25165 is in review. Ollama `laguna-s-2.1`.
- Other sizes: XS 2.1 = 33B-A3B; M.1 = 225B-A23B (hub ID not checked).
- sources: https://huggingface.co/api/models?author=poolside&sort=createdAt&direction=-1&limit=20, https://huggingface.co/poolside/Laguna-S-2.1

#### Other families seen only in engine registries (not researched, verified: no)
- vLLM, SGLang, llama.cpp, Ollama or MLX registries today include parsers or architectures for: LongCat (`longcat`, `longcat_flash`), EXAONE (`exaone4`, `exaone_moe`), Jamba (`jamba`), dots (`dots`), MiniCPM 5 (`minicpm5`), GigaChat 3 / 3.5, PLaMo 3, Holo2, Nanbeige, IQuest, Inkling, Mellum, Helium.
- LongCat and EXAONE are not on either Phase 0 researcher's list. Flag them for a follow-up.

### Default model candidates (SPEC §5)

- Conventions: Size GB = 10^9 bytes, summed from hub file listings (byte counts; split GGUFs summed). Ollama sizes are as shown on library tag pages. RAM GB for T0 = file + f16 KV at the stated ctx, KV = 2 x layers x kv_heads x head_dim x 2 bytes per token from each config.json (Qwen3-4B: 147,456 B/token; Qwen3-Embedding-0.6B: 114,688; Qwen2.5-Coder-1.5B: 28,672, head_dim derived as 1536/12); compute buffers excluded. "Repo created" = hub API `createdAt` (can precede public release; the API exposes no release date). "Checked" for every row = 2026-10-16.
- Engines (verified yes): llama.cpp server README documents `json_schema` and `grammar` (grammar-based sampling), `response_format` with `json_schema`, `--embedding`, `--rerank` (`/rerank`, `/v1/rerank`, needs reranker model with pooling rank), `/infill`, tool calls via `--jinja` (default enabled), and MoE offload flags `-cmoe/--cpu-moe`, `-ncmoe/--n-cpu-moe N`, `--moe-cache-mib N`, `-ot`, `-sm {none,layer,row,tensor}`, `-ts`, `--fit` (default on). `common/arg.cpp`: `--fim-qwen-1.5b-default` = `ggml-org/Qwen2.5-Coder-1.5B-Q8_0-GGUF` / `qwen2.5-coder-1.5b-q8_0.gguf`; `--fim-qwen-30b-default` = `ggml-org/Qwen3-Coder-30B-A3B-Instruct-Q8_0-GGUF`. Ollama API doc: `format` accepts a JSON schema (structured outputs), `tools`, `suffix`, embeddings; no rerank endpoint. Not verified: Ollama 0.34.4 compatibility with Qwen3.8-Flash-Next, GLM-5.3-Flash, MiMo-V2.6 or Kimi K3 GGUFs; llama.cpp Windows/AMX specifics. Sources: https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md, https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/arg.cpp, https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md
- Qwen3-4B-Instruct-2507: verified yes (2026-10-16). Basis: hub info (Apache-2.0, 4,022,468,096 params, repo created 2025-08-05), card (4.0B, 36 layers, GQA 32/8, 262,144 native ctx, non-thinking only; MMLU-Pro 69.6, IFEval 83.4, BFCL-v3 61.9, TAU2-Retail 40.4, LiveCodeBench v6 35.1), config (head_dim 128), file listings, Ollama tags (256K ctx). No GGUF repo from the model authors; the `unsloth/` and `bartowski/` Q4_K_M files differ by 384 bytes (2,497,281,120 / 2,497,280,736 B). Sources: https://huggingface.co/api/models/Qwen/Qwen3-4B-Instruct-2507, https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/raw/main/README.md, https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/raw/main/config.json, https://huggingface.co/api/models/unsloth/Qwen3-4B-Instruct-2507-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/ggml-org/Qwen3-4B-Instruct-2507-Q8_0-GGUF/tree/main?recursive=true, https://ollama.com/library/qwen3/tags
- Qwen3.5 4B / 2B / 0.8B: verified yes. Basis: hub info (Apache-2.0; params 4.66B / 2.27B / 0.87B incl. vision encoder; repos created 2026-02-27 / 02-28 / 02-28; pipeline image-text-to-text), cards (262,144 native ctx, 4B ext. to 1,010,000; 4B "operates in thinking mode by default"; 2B and 0.8B "operate in non-thinking mode by default"; tool calling section present). Card scores, 4B thinking: IFEval 89.8, BFCL-V4 50.3, TAU2 79.9, LiveCodeBench v6 55.8. 2B card non-thinking: Qwen3.5-2B MMLU-Pro 55.3 / IFEval 61.2; Qwen3.5-0.8B 29.7 / 52.1. No GGUF from the model authors; `ggml-org/Qwen3.5-0.8B-GGUF` has Q4_0/Q8_0 only. Router use needs `enable_thinking: false` for 4B. Sources: https://huggingface.co/api/models/Qwen/Qwen3.5-4B, https://huggingface.co/api/models/Qwen/Qwen3.5-2B, https://huggingface.co/api/models/Qwen/Qwen3.5-0.8B, https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3.5-0.8B/raw/main/README.md, https://huggingface.co/api/models/unsloth/Qwen3.5-4B-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/unsloth/Qwen3.5-2B-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/unsloth/Qwen3.5-0.8B-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/ggml-org/Qwen3.5-0.8B-GGUF/tree/main?recursive=true, https://ollama.com/library/qwen3.5/tags
- Gemma 4 E2B / E4B: verified yes. Basis: hub info (license apache-2.0, link to the Gemma 4 license page; not gated; repos created 2026-03-02), card (E2B 2.3B effective, 5.1B with embeddings; E4B 4.5B effective, 8B with embeddings; 128K ctx; native function calling; configurable thinking, E2B/E4B emit no empty thought block when thinking is off). Card scores E2B / E4B: MMLU Pro 60.0% / 69.4%, LiveCodeBench v6 44.0% / 52.0%, Tau2 24.5% / 42.2%. Official QAT Q4_0 GGUFs exist. Sources: https://huggingface.co/api/models/google/gemma-4-E2B-it, https://huggingface.co/api/models/google/gemma-4-E4B-it, https://huggingface.co/google/gemma-4-E2B-it/raw/main/README.md, https://huggingface.co/api/models/unsloth/gemma-4-E2B-it-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/unsloth/gemma-4-E4B-it-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/google/gemma-4-E2B-it-qat-q4_0-gguf/tree/main?recursive=true, https://huggingface.co/api/models/google/gemma-4-E4B-it-qat-q4_0-gguf/tree/main?recursive=true, https://huggingface.co/api/models/ggml-org/gemma-4-E2B-it-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/ggml-org/gemma-4-E4B-it-GGUF/tree/main?recursive=true, https://ollama.com/library/gemma4/tags
- Phi-4-mini: verified yes (exists), replaced. Basis: hub info (`microsoft/Phi-4-mini-instruct`, MIT, 3,836,021,760 params, repo created 2025-02-19), card (3.8B, 128K ctx, release February 2025, `<|tool|>` function-calling format, MMLU-Pro 0-shot CoT 52.8). No newer small Phi instruct model in the hub listing (latest Phi repos: Phi-Ground-Any 2026-05, Phi-4-reasoning-vision-15B 2026-01). Replacement justification: Gemma 4 E4B card MMLU Pro 69.4%, Qwen3-4B-Instruct-2507 card MMLU-Pro 69.6, at 2.50-4.98 GB Q4_K_M vs 2.49 GB. Ollama `phi4-mini:3.8b-q8_0` page lists 4K ctx. Sources: https://huggingface.co/api/models/microsoft/Phi-4-mini-instruct, https://huggingface.co/microsoft/Phi-4-mini-instruct/raw/main/README.md, https://huggingface.co/api/models?author=microsoft&search=Phi&sort=createdAt&direction=-1&limit=15, https://huggingface.co/api/models/unsloth/Phi-4-mini-instruct-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/bartowski/microsoft_Phi-4-mini-instruct-GGUF/tree/main?recursive=true, https://ollama.com/library/phi4-mini/tags
- Qwen2.5-Coder-1.5B (FIM): verified yes. Basis: hub info (Apache-2.0, 1,543,714,304 params, repo created 2024-09-18), card (1.54B, 28 layers, GQA 12/2, 32,768 ctx), config, llama.cpp preset (exact repo/file), Ollama base tags. Card does not mention FIM; FIM use is evidenced by the llama.cpp `--fim-qwen-*` presets and `/infill`. Hub search returned only a Q8_0 GGUF for the base model. Sources: https://huggingface.co/api/models/Qwen/Qwen2.5-Coder-1.5B, https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B/raw/main/README.md, https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B/raw/main/config.json, https://huggingface.co/api/models/ggml-org/Qwen2.5-Coder-1.5B-Q8_0-GGUF/tree/main?recursive=true, https://huggingface.co/api/models?search=Qwen2.5-Coder-1.5B-GGUF&sort=downloads&direction=-1&limit=8, https://ollama.com/library/qwen2.5-coder/tags
- Qwen3-Embedding-0.6B: verified yes. Basis: hub info (Apache-2.0, 595,776,512 params, repo created 2025-06-03), card (28 layers, 32K, dims up to 1024, MRL 32-1024, instruction aware, MTEB multilingual 64.33, MTEB eng v2 70.70), Qwen3-Reranker card (MTEB-Code 75.41 for this embedder), config, official GGUF listing (Q8_0 639,150,592 B; f16). Sources: https://huggingface.co/api/models/Qwen/Qwen3-Embedding-0.6B, https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/raw/main/config.json, https://huggingface.co/api/models/Qwen/Qwen3-Embedding-0.6B-GGUF/tree/main?recursive=true, https://ollama.com/library/qwen3-embedding/tags
- Qwen3.8-27B: verified yes. Basis: author listing for `Qwen/` (Qwen3.8 repos: 27B 2026-08-05, Flash-Next 2026-08-24, 2.4T-A95B 2026-08-08; no small Qwen3.8 models), hub info (Apache-2.0, 27,781,427,952 params), card (64 layers, layout 16 x (3 Gated DeltaNet + 1 Gated Attention), 262,144 native ext. to 1,000,000, thinking on by default; SWE-bench Pro 61.7, Terminal Bench 2.1 73.0, LiveCodeBench v6 90.3), chat template with tool_call handling, file listings (also MTP and DFlash draft GGUFs in `ggml-org/Qwen3.8-27B-GGUF`). 4-bit sizes: UD-IQ4_XS 14.25, UD-Q4_K_M 16.46, UD-Q4_K_XL 17.56, Q4_K_M 18.97; UD-Q3_K_XL 13.15. Sources: https://huggingface.co/api/models?author=Qwen&sort=createdAt&direction=-1&limit=100, https://huggingface.co/api/models/Qwen/Qwen3.8-27B, https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/chat_template.jinja, https://huggingface.co/api/models/ggml-org/Qwen3.8-27B-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF/tree/main?recursive=true, https://ollama.com/library/qwen3.8/tags
- Devstral Small 2: verified yes. Basis: hub info (Apache-2.0, 24.0B, repo created 2025-11-28; stored FP8), card (24B, 256k context, tool calling; SWE-bench Verified 68.0%, SWE-bench Multilingual 55.7%, Terminal Bench 2 22.5%), config (393,216 positions), author listing for that org (no newer Devstral after 2512). Older than Qwen3.8-27B; kept as a different-family reviewer. Sources: https://huggingface.co/api/models/mistralai/Devstral-Small-2-24B-Instruct-2512, https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512/raw/main/README.md, https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512/raw/main/config.json, https://huggingface.co/api/models?author=mistralai&sort=createdAt&direction=-1&limit=25, https://huggingface.co/api/models/unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF/tree/main?recursive=true, https://ollama.com/library/devstral-small-2/tags
- Small-active MoE (T1): verified yes. `Qwen/Qwen3.6-35B-A3B` (repo 2026-04-15; Apache-2.0; 35B total, 3B active, 8 routed + 1 shared experts; 262,144 ext. 1,010,000). Qwen3.6-35B-A3B card table: SWE-bench Verified 73.4 vs Qwen3.5-35B-A3B 70.0 and Gemma4-26B-A4B 17.4; SWE-bench Pro 49.5; Terminal-Bench 2.0 51.5. `openai/gpt-oss-20b`: card 21B total, 3.6B active, Apache-2.0. `google/gemma-4-26B-A4B-it`: card 25.2B total, 3.8B active, 256K. Also on hub: `zai-org/GLM-4.7-Flash` (MIT, 31.2B params, card SWE-bench Verified 59.2; GGUF not checked); `Qwen/Qwen-AgentWorld-35B-A3B` (agent fine-tune of Qwen3.5-35B-A3B-Base, Apache-2.0). Sources: https://huggingface.co/api/models/Qwen/Qwen3.6-35B-A3B, https://huggingface.co/Qwen/Qwen3.6-35B-A3B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3.6-27B/raw/main/README.md, https://huggingface.co/api/models/unsloth/Qwen3.6-35B-A3B-GGUF/tree/main?recursive=true, https://ollama.com/library/qwen3.6/tags, https://huggingface.co/openai/gpt-oss-20b/raw/main/README.md, https://huggingface.co/api/models/ggml-org/gpt-oss-20b-GGUF/tree/main?recursive=true, https://ollama.com/library/gpt-oss/tags, https://huggingface.co/api/models/google/gemma-4-26B-A4B-it, https://huggingface.co/api/models/unsloth/gemma-4-26B-A4B-it-GGUF/tree/main?recursive=true, https://huggingface.co/zai-org/GLM-4.7-Flash/raw/main/README.md, https://huggingface.co/Qwen/Qwen-AgentWorld-35B-A3B/raw/main/README.md
- Qwen3.8-Flash-Next: verified yes. Basis: hub info (license other `qwen-community-1.0`, 179,999,981,459 stored params, repo created 2026-08-24), card (125B with 6B activated, plus 51B n-gram embedding and 4B MTP; 48 layers; 512 experts, 10 routed + 1 shared; 262,144 ext. to 1,000,000; thinking default; SWE-bench Pro 62.5, DeepSWE 1.1 58.7, SWE-bench Multilingual 81.0, LiveCodeBench v6 91.9), LICENSE text (attribution above 100M MAU or US$20M monthly revenue; separate license required before commercial use by licensees running a "Model as a Service" or "AI Work Assistant" business, the latter defined as an independent product primarily for AI-assisted coding or office productivity; internal use exempt), chat template with tool_call, GGUF README ("To run, please use llama.cpp"), listing (UD-Q4_K_XL 111,334,654,784 B). Sources: https://huggingface.co/api/models/Qwen/Qwen3.8-Flash-Next, https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/README.md, https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/LICENSE, https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/chat_template.jinja, https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/raw/main/README.md, https://huggingface.co/api/models/unsloth/Qwen3.8-Flash-Next-GGUF/tree/main?recursive=true, https://ollama.com/library/qwen3.8/tags
- gpt-oss-120b: verified yes. Basis: hub info (Apache-2.0, repo created 2025-08-04), card (117B total, 5.1B active, function calling), config (131,072 positions, 128 experts, 4 per token, 36 layers), listing (single 63,387,346,208 B MXFP4 file), Ollama `gpt-oss:120b` 65GB 128K. Sources: https://huggingface.co/api/models/openai/gpt-oss-120b, https://huggingface.co/openai/gpt-oss-120b/raw/main/README.md, https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json, https://huggingface.co/api/models/ggml-org/gpt-oss-120b-GGUF/tree/main?recursive=true, https://ollama.com/library/gpt-oss/tags
- GLM-5.3-Flash: verified yes. Basis: hub info (MIT, 321.3B stored params mostly FP8, repo created 2026-08-25), card (320B total, 18B active, natively multimodal, hybrid sparse + linear attention; benchmarks shown only as an image, not cited), config (1,048,576 positions, 45 layers, 288 routed experts, 8 per token), chat template with tool_call, GGUF listing (UD-IQ4_XS 156.82, UD-Q4_K_XL 199.71, UD-Q3_K_XL 147.54, UD-IQ3_XXS 120.37, UD-Q2_K_XL 108.72, Q8_0 340.98 GB). llama.cpp: PR #27754 closed unmerged 2026-10-01 (the GGUF README still links it); PR #27773 "add GLM-5.3-Flash (GLM5-Next) support" merged 2026-09-30; issue #29950 open. Ollama: `glm-5.3-flash:cloud` only. Sources: https://huggingface.co/api/models/zai-org/GLM-5.3-Flash, https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/README.md, https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/config.json, https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/chat_template.jinja, https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF/raw/main/README.md, https://huggingface.co/api/models/unsloth/GLM-5.3-Flash-GGUF/tree/main?recursive=true, https://github.com/ggml-org/llama.cpp/pull/27754, https://api.github.com/search/issues?q=repo:ggml-org/llama.cpp+GLM-5-Next+is:pr&per_page=15, https://api.github.com/search/issues?q=repo:ggml-org/llama.cpp+glm5_next&per_page=15, https://ollama.com/library/glm-5.3-flash/tags
- GLM-5.3: verified yes. Basis: hub info (license other `glm-5.3`, 753,329,940,480 stored params, repo created 2026-08-25), card ("same base model as GLM-5.2"; total/active params not stated; Terminal Bench 2.1 88.2, DeepSWE v1.1 66.9), config (1,048,576 positions, 78 layers, 256 routed experts, 8 per token), LICENSE (MIT-style; licensees running a Model-as-a-Service business above US$10B revenue in 12 months must pass the licensor's security review before commercial use), chat template with tool_call, GGUF listing. Ollama `glm-5.3:cloud` only. Sources: https://huggingface.co/api/models/zai-org/GLM-5.3, https://huggingface.co/zai-org/GLM-5.3/raw/main/README.md, https://huggingface.co/zai-org/GLM-5.3/raw/main/config.json, https://huggingface.co/zai-org/GLM-5.3/raw/main/LICENSE, https://huggingface.co/zai-org/GLM-5.3/raw/main/chat_template.jinja, https://huggingface.co/api/models/unsloth/GLM-5.3-GGUF/tree/main?recursive=true, https://ollama.com/library/glm-5.3/tags
- Kimi K3: verified yes. Basis: hub info (license other `kimi-k3`, repo created 2026-06-13, modified 2026-09-02), card (2.8T total, 104B activated, 896 experts, 16 selected + 2 shared, context 1,048,576, MXFP4 weights / MXFP8 activations via QAT, tool calls require passing back reasoning_content; DeepSWE 67.5, Terminal-Bench 2.1 88.3), LICENSE (Model-as-a-Service licensees above US$20M revenue in 12 months need a separate agreement; "Kimi K3" display above 100M MAU or US$20M monthly revenue; internal use exempt), GGUF README (points to a fork PR; lossless = UD-Q8_K_XL), mainline llama.cpp PR #26185 "add Kimi-K3 text model" merged 2026-08-15. Ollama `kimi-k3:cloud` only. Sources: https://huggingface.co/api/models/moonshotai/Kimi-K3, https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md, https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE, https://huggingface.co/unsloth/Kimi-K3-GGUF/raw/main/README.md, https://huggingface.co/api/models/unsloth/Kimi-K3-GGUF/tree/main?recursive=true, https://api.github.com/search/issues?q=repo:ggml-org/llama.cpp+%22Kimi+K3%22+is:pr&per_page=10, https://ollama.com/library/kimi-k3/tags
- MiMo-V2.6-Pro: verified yes (renamed). Basis: author listing has no `MiMo-V2.6-Pro` repo; it has `XiaomiMiMo/MiMo-V2.6-Pro-RL` (2026-09-21) and `XiaomiMiMo/MiMo-V2.6-Pro-MOPD` (2026-09-27; card: "MOPD upgrade of the MiMo-V2.6-Pro-RL checkpoint", targets tool-call repetition). Both MIT; card 1.02T total / 42B activated, 384 routed experts / 8 activated, 1M ctx; MOPD card table: DeepSWE v1.1 71.9 (Pro) / 67.9 (Flash), Terminal Bench 2.1 89.9 / 87.6. llama.cpp PR #29257 "convert: add MiMo-V2.6 support" merged 2026-09-22. Ollama `mimo-v2.6-pro` page: 404. Also `XiaomiMiMo/MiMo-V2.6-Flash-MOPD` (309B / 15B active, 256 routed / 8, 1M ctx, MIT). Sources: https://huggingface.co/api/models?author=XiaomiMiMo&sort=createdAt&direction=-1&limit=40, https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.6-Pro-MOPD, https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Pro-MOPD/raw/main/README.md, https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Pro-RL/raw/main/README.md, https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-MOPD/raw/main/README.md, https://huggingface.co/api/models/ggml-org/MiMo-V2.6-Pro-MOPD-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/ggml-org/MiMo-V2.6-Pro-RL-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/ggml-org/MiMo-V2.6-Flash-MOPD-GGUF/tree/main?recursive=true, https://ollama.com/library/mimo-v2.6-pro/tags
- DeepSeek V4.1 Flash: verified yes (exists); local GGUF path verified absent. Basis: hub info (MIT, repo created 2026-09-10, modified 2026-10-12), card (552B backbone params; Engram conditional memory 196B; activated 8B / 16B; 40-layer causal encoder-decoder; contexts up to one million tokens; no Jinja chat template, Python `encoding` reference instead; DeepSWE v1.1 74.2, Terminal-Bench 2.1 90.6 vs K3 67.5 / 88.3 and GLM-5.3 66.9 / 88.2 in the same table). llama.cpp PR #28696 "add DeepSeek V4.1 (DeepseekV41ForCausalLM)" open. Repos `unsloth/DeepSeek-V4.1-Flash-GGUF` and `ggml-org/DeepSeek-V4.1-Flash-GGUF` return not-found. `antirez/deepseek-v4.1-flash-gguf` README: weights for the DwarfStar Metal engine, "not interchangeable"; Q2 file 340.60 GiB incl. 188.83 GiB FP8 Engram tables. DeepSeek-family alternative with GGUF: `deepseek-ai/DeepSeek-V4-Flash-0731` (MIT; base 284B / 13B activated per the V4.1 card; Terminal Bench 2.1 82.7, DeepSWE 54.4; `unsloth/DeepSeek-V4-Flash-0731-GGUF` UD-Q4_K_XL 155.10 GB); its llama.cpp support is not verified (GGUF README names no engine; related V4 PRs #23122 and #25860 are still open). Sources: https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash, https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md, https://huggingface.co/api/models/unsloth/DeepSeek-V4.1-Flash-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/ggml-org/DeepSeek-V4.1-Flash-GGUF/tree/main?recursive=true, https://huggingface.co/antirez/deepseek-v4.1-flash-gguf/raw/main/README.md, https://huggingface.co/api/models/antirez/deepseek-v4.1-flash-gguf/tree/main?recursive=true, https://api.github.com/search/issues?q=repo:ggml-org/llama.cpp+%22DeepSeek+V4%22+is:pr&per_page=10, https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/raw/main/README.md, https://huggingface.co/unsloth/DeepSeek-V4-Flash-0731-GGUF/raw/main/README.md, https://huggingface.co/api/models/unsloth/DeepSeek-V4-Flash-0731-GGUF/tree/main?recursive=true, https://ollama.com/library/deepseek-v4.1-flash/tags
- Mistral Small 4 (added T2 alt): verified yes. Basis: hub info (Apache-2.0, 119.4B stored, repo created 2026-01-23), card (MoE 128 experts, 4 active; 119B, 6.5B activated; 256k ctx; tool-definition example), listing (UD-Q4_K_XL 74.16 GB, MXFP4_MOE 71.81 GB). Sources: https://huggingface.co/api/models/mistralai/Mistral-Small-4-119B-2603, https://huggingface.co/mistralai/Mistral-Small-4-119B-2603/raw/main/README.md, https://huggingface.co/api/models/unsloth/Mistral-Small-4-119B-2603-GGUF/tree/main?recursive=true
- Embeddings and rerankers: verified yes for IDs, licenses, param counts (hub API), dims/context from configs or cards as marked; "not verified" where no page was fetched. EmbeddingGemma 2 card: 740M total, text backbone 270M (130M transformer + 140M embedder), 8,192 ctx, 768 native dims. Qwen3-Reranker-0.6B card: reranking scored on top-100 candidates from Qwen3-Embedding-0.6B; MTEB-R 65.80 vs 61.82, MTEB-Code 73.42 vs 75.41. Sources: https://huggingface.co/api/models/google/embeddinggemma-2, https://huggingface.co/google/embeddinggemma-2/raw/main/README.md, https://huggingface.co/api/models/unsloth/embeddinggemma-2-GGUF/tree/main?recursive=true, https://ollama.com/library/embeddinggemma2/tags, https://huggingface.co/api/models/google/embeddinggemma-300m, https://huggingface.co/api/models/ggml-org/embeddinggemma-300M-GGUF/tree/main?recursive=true, https://ollama.com/library/embeddinggemma/tags, https://huggingface.co/api/models/nomic-ai/nomic-embed-text-v1.5, https://huggingface.co/nomic-ai/nomic-embed-text-v1.5/raw/main/config.json, https://huggingface.co/api/models/nomic-ai/nomic-embed-text-v1.5-GGUF/tree/main?recursive=true, https://ollama.com/library/nomic-embed-text/tags, https://huggingface.co/api/models/BAAI/bge-m3, https://huggingface.co/BAAI/bge-m3/raw/main/config.json, https://ollama.com/library/bge-m3/tags, https://huggingface.co/api/models/Snowflake/snowflake-arctic-embed-l-v2.0, https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0/raw/main/config.json, https://ollama.com/library/snowflake-arctic-embed2/tags, https://huggingface.co/api/models/ibm-granite/granite-embedding-english-r2, https://huggingface.co/ibm-granite/granite-embedding-english-r2/raw/main/config.json, https://ollama.com/library/granite-embedding/tags, https://huggingface.co/api/models/Qwen/Qwen3-Reranker-0.6B, https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/raw/main/README.md, https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/raw/main/config.json, https://huggingface.co/api/models/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/BAAI/bge-reranker-v2-m3, https://huggingface.co/api/models/gpustack/bge-reranker-v2-m3-GGUF/tree/main?recursive=true, https://huggingface.co/api/models/ibm-granite/granite-embedding-reranker-english-r2, https://huggingface.co/api/models/mixedbread-ai/mxbai-rerank-base-v2, https://huggingface.co/api/models/jinaai/jina-reranker-v2-base-multilingual
- Workstation notes: verified yes for sizes, flags and PR states; verified no for throughput, Ollama 0.34.4 support of the new GGUF architectures, and AMX use. VRAM headroom figures are arithmetic on listed file sizes against 48 GB per GPU and exclude compute buffers. MXFP4 (gpt-oss-120b) is already served by Ollama on this Ampere box, per the assignment.

### Harnesses

#### Oh My Pi
- verified: yes (2026-10-16), basis: npm registry entry, GitHub repo metadata and release list, repo README, docs (extensions, models, cli-reference, settings, environment-variables, hooks), shipped prompts (`src/edit/hashline-compact.md`, `prompts/tools/lsp.md`, `prompts/tools/read.md`).
- link: https://github.com/can1357/oh-my-pi (site https://omp.sh). Identity: the coding-agent CLI `omp`, a fork of Pi (next section); npm https://registry.npmjs.org/@oh-my-pi%2Fpi-coding-agent.
- license: MIT
- version: 18.11.1 (released 2026-10-15)
- platforms: Windows native (same binary, "no WSL bridge"), macOS native, Linux native; npm install needs Bun >= 1.3.14
- what we use: Headless: `omp -p "<task>"` (stdin accepted), `omp -p --mode json "<task>"` (session header then JSON-lines events), `omp --mode rpc --no-session` (NDJSON commands in, e.g. `{"type":"prompt",...}`, `{"type":"set_model","provider":...,"modelId":...}`, `{"type":"abort"}`; each prompt ends with `prompt_result`), `omp acp`; run controls `--max-time <dur>`, `--approval-mode always-ask|write|yolo` / `--yolo`, `--tools a,b`, `--no-lsp`, `--no-session`, `--cwd`, `--profile <name>` (isolated auth, sessions, settings), `--config <file>` (YAML overlay, repeatable). Endpoint: `~/.omp/agent/models.yml` `providers.<id>.baseUrl`, `api` (`openai-completions`, `openai-responses`, `anthropic-messages`, `google-generative-ai`, ...), `apiKey`, `models[].{id,name,contextWindow,maxTokens}`; check with `omp models <provider>`. Roles: `modelRoles` in `~/.omp/agent/config.yml` (chat roles `default`, `smol`, `slow`, `vision`, `plan`, `commit`, `tiny`, `memory`, `task`, `advisor`; kind roles `image`, `web`, `speech`, `dictation`, `judge`; aliases `@role`, thinking suffix `:low` etc.); per run `--model <id|@role>`, `--smol`, `--slow`, `--plan`, env `PI_SMOL_MODEL`, `PI_SLOW_MODEL`, `PI_PLAN_MODEL`; per-role fallbacks `retry.fallbackChains`. Extension (TS module with default-export factory `(pi: ExtensionAPI)`, loaded by `-e <path>` or `--trusted-extension <abs-path>`): `pi.registerProvider(name, { baseUrl, api, apiKey, models, fetchDynamicModels })` (dynamic fetch bounded to 15 s); `pi.on("before_subagent_spawn")` returns `{ model, block, reason, note }` per spawned child (event carries `modelRole`; last returned model wins, remaining entries become the retry chain); `pi.on("before_provider_request")` returns a replacement payload; `pi.on("after_provider_response")`; `pi.setModel()`; `ctx.models.list/current/resolve("@slow")/family()`; `cache_warming_decision` event. No role-resolver or virtual-model registration API is documented. Edit format: `edit.mode` = `hashline` (default) | `apply_patch` | `patch` | `replace` | `sloppy`, per-model `edit.modelVariants`, env `PI_EDIT_VARIANT`, `PI_STRICT_EDIT_MODE`; hashline patch = `*** Begin Patch`, header `[PATH#TAG]` (TAG = required 4-hex file snapshot shown by `read`, e.g. `[foo.ts#1A2B]`), ops `PUT N.=M:` (replace inclusive range), `PUT N*:` (replace syntax block), `PUT <N:` / `PUT >N:` / `PUT >$:` (insert), `CUT N.=M` / `CUT N*` (delete, optional capture `@name`), register paste, `REM` (delete file), `MV DEST`; body rows start with `+`; `*** End Patch`; stale tag means the patch is rejected; `edit.enforceSeenLines` rejects anchors on lines a prior read did not show in full; the separate `readHashLines` setting was removed (hash anchors now follow `edit.mode`). LSP: `lsp` tool ops `diagnostics`, `symbols`, `rename`, `rename_file`, `code_actions`, `reload`, `request` (raw method + payload) and position-based lookups (`file` + `line` + `symbol`); diagnostics after writes; config in docs/lsp-config.md. Worktrees: `omp worktree|wt` subcommand; subagent isolation `task.isolation.enabled` + `isolation.backend: rcopy|overlayfs|projfs`. Hooks: legacy `HookAPI` modules via `--hook <path>`, run through the extension runner (`tool_call` / `tool_result` interception).
- what we borrow: hashline idea (file snapshot tag + line-number ops, reject on stale tag) as prior art for `edit(hash-anchored)` / TK5 (MIT; reimplement, no code copy without a license check); role names as default slot vocabulary; the `before_subagent_spawn` contract; the cache-warming economics fields (`warmCost`, `missCost`, `continuationProbability`) as inputs to §7.2.
- what we avoid: ambient project inputs (opening a repo loads its settings, extensions, hooks, tools by design): run adapters with `--no-extensions` plus `--trusted-extension`; `--external-thinking`; writing to the user's `~/.omp` (use `--profile` / `--config`).
- risks: release cadence (major line 18, releases on consecutive days); settings keys get renamed (migration table in settings docs); Bun-only runtime; Oh My Pi treats `http://localhost:4000/v1` as the LiteLLM fallback (`LITELLM_BASE_URL`), the same port as the planned `sy proxy` default.
- sources: https://registry.npmjs.org/@oh-my-pi%2Fpi-coding-agent ; https://api.github.com/repos/can1357/oh-my-pi ; https://api.github.com/repos/can1357/oh-my-pi/releases?per_page=2 ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/README.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/README.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/extensions.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/models.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/cli-reference.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/settings.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/environment-variables.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/hooks.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/edit/hashline-compact.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/prompts/tools/lsp.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/prompts/tools/read.md

#### Pi
- verified: yes (2026-10-16), basis: npm registry (current and former package), GitHub repo metadata (former URL redirects) and release list, package README, docs (cli, json, models, custom-provider, virtual-models, extensions, environment-variables, windows), pi-ai README.
- link: https://github.com/earendil-works/pi (site https://pi.dev; https://github.com/badlogic/pi-mono now redirects here). Ambiguity: the SPEC's "Pi" is this coding-agent CLI (`pi`), not a consumer chat assistant also named Pi. Current npm: https://registry.npmjs.org/@earendil-works%2Fpi-coding-agent; the former package https://registry.npmjs.org/@mariozechner%2Fpi-coding-agent stopped at 0.73.1 (2026-05-07); an npm package literally named `pi-coding-agent` is a 0.0.1 placeholder with no repository link.
- license: MIT
- version: 1.3.0 (released 2026-10-15)
- platforms: Windows native (Git Bash for the `bash` tool, optional `powershell` tool) or WSL2, macOS native, Linux native; Node >= 22.19.0
- what we use: Headless: `pi -p "<task>"` (final assistant text), `pi --mode json "<task>"` (session header + JSONL events; split records on LF only, not with Node `readline`), `pi --mode rpc` (JSONL commands/events until shutdown); `--model provider/id[:thinking]`, `--provider`, `--api-key`, `--thinking off|minimal|low|medium|high|xhigh|max`, `--models`, `--no-session`, `-t/--tools`, `-e/--extension`, `-ne/--no-extensions`. Endpoint: `~/.pi/agent/models.json` `providers.<id>.baseUrl`, `api`, `apiKey` (`$NAME`, `${NAME}`, or `!command`), `headers`, `models[].id`; `api` values `openai-completions`, `openai-responses`, `anthropic-messages`, `google-generative-ai`, `google-vertex`, `azure-openai-responses`, `mistral-conversations`, `bedrock-converse-stream`, `openai-codex-responses`; `PI_CODING_AGENT_DIR` isolates config. Extension: `pi.registerProvider(name, ProviderConfig)` or a complete `Provider` from Pi's AI package (`pi-ai`, https://raw.githubusercontent.com/earendil-works/pi/main/packages/ai/README.md); `pi.registerVirtualModel({ provider, id, name, thinkingLevels, contextWindow, maxTokens, route(request, ctx) })` where `route` runs before every request and returns `{ model, thinkingLevel }`; `request` has `model`, `thinkingLevel`, `reason`, `previous`, `failed`, `state`, `messages`; look up targets with `ctx.modelRegistry.find(provider, id)`; SDK equivalent `modelRuntime.registerVirtualModel()`; events via `pi.on(...)` (`session_start`, `before_agent_start`, `context`, `tool_call`, `tool_result`, `turn_end`, `agent_end`, ...). No model roles, LSP tools or hash-anchored edit mode are documented (default tools `read`, `bash`, `edit`, `write`). Egress: `PI_OFFLINE`, `PI_SKIP_VERSION_CHECK`, `PI_TELEMETRY=0`. Shell commands see `PI_PROVIDER`, `PI_MODEL`, `PI_REASONING_LEVEL`, `PI_SESSION_ID`.
- what we borrow: the virtual-model split between selection and dispatch (selected virtual pair stored as `model_change`; each assistant message records the physical `provider`, `api`, `model`, `thinkingLevel`; context limits follow the physical model; compaction before sending to a smaller window) for §7.2 and §11 trajectories; strict JSONL framing rules.
- what we avoid: assuming roles, subagents or plan mode exist (Pi "skips features like sub-agents and plan mode").
- risks: repo, organization and npm scope changed in 2026 (pin new names); issues and PRs from new contributors are closed automatically; a virtual model cannot route to another virtual model.
- sources: https://registry.npmjs.org/@earendil-works%2Fpi-coding-agent ; https://registry.npmjs.org/@mariozechner%2Fpi-coding-agent ; https://registry.npmjs.org/-/v1/search?text=pi-coding-agent&size=15 ; https://api.github.com/repos/badlogic/pi-mono ; https://api.github.com/repos/badlogic/pi-mono/releases?per_page=2 ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/package.json ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/README.md ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/cli.md ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/json.md ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/models.md ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/custom-provider.md ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/virtual-models.md ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/extensions.md ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/environment-variables.md ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/windows.md ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/ai/README.md ; https://registry.npmjs.org/pi-acp

#### OpenCode
- verified: yes (2026-10-16), basis: npm registry, GitHub repo metadata (former path redirects) and releases, docs sources in the repo (cli, config, providers, agents, acp, plugins, index).
- link: https://opencode.ai (repo https://github.com/anomalyco/opencode; https://github.com/sst/opencode redirects). Ambiguity: a different Go terminal assistant also named OpenCode (https://github.com/opencode-ai/opencode) was archived on 2025-09-18 and continues as Crush; the SPEC means the TypeScript project.
- license: MIT
- version: 1.19.2 (released 2026-10-16)
- platforms: Windows native (Chocolatey, Scoop, npm, Mise, Docker; docs recommend WSL for full compatibility), macOS native, Linux native
- what we use: Headless: `opencode run "<task>" --format json` (raw JSON events) with `-m/--model provider/model`, `--agent <name>`, `--dir <path>`, `--auto` (auto-approve permissions not explicitly denied), `--variant <effort>`, `-c/--continue`, `-s/--session`, `--fork`, `--title`, `-f/--file`; warm server `opencode serve` (HTTP API; basic auth via `OPENCODE_SERVER_PASSWORD`, `OPENCODE_SERVER_USERNAME`) with `opencode run --attach http://localhost:4096`; `opencode acp` (JSON-RPC over stdio). Endpoint: `provider.<id>.npm` = `@ai-sdk/openai-compatible` (chat-completions) or `@ai-sdk/openai` (responses), `options.baseURL`, `options.apiKey`, `options.headers`, `models.<id>.name`; `options.baseURL` also overrides built-in providers (docs example targets the messages provider). Config without touching user files: `OPENCODE_CONFIG_CONTENT` (inline JSON; loaded after project config and `.opencode` dirs), or `OPENCODE_CONFIG` / `OPENCODE_CONFIG_DIR`. Slots: `model`, `small_model` (title generation and other light tasks), `agent.<name>.model` (built-ins `build`, `plan`, `general`, `explore`, `scout`, `compaction`, `title`, `summary`; subagents inherit the invoking primary agent's model unless set). Plugins: JS/TS modules in `.opencode/plugins/`, `~/.config/opencode/plugins/` or npm (`plugin` config key); context `{ project, client, $, directory, worktree }`; hooks `tool.execute.before`, `tool.execute.after`, `shell.env`, `event` (e.g. `session.idle`, `permission.asked`), `experimental.session.compacting`. Egress switches: `OPENCODE_DISABLE_AUTOUPDATE`, `OPENCODE_DISABLE_MODELS_FETCH`, `OPENCODE_DISABLE_DEFAULT_PLUGINS`, `OPENCODE_DISABLE_LSP_DOWNLOAD`, `OPENCODE_DISABLE_CLAUDE_CODE` (stop reading `.claude`).
- what we borrow: layered config with an inline environment layer (remote `.well-known/opencode` -> global -> `OPENCODE_CONFIG` -> project -> `.opencode` -> `OPENCODE_CONFIG_CONTENT` -> managed); serve/attach split to avoid per-run cold starts.
- what we avoid: remote `.well-known/opencode` config (egress); writing `opencode.json` into user repos.
- risks: two releases within 24 hours (1.19.1, 1.19.2), so pin versions; git worktree management is not documented in the CLI docs (plugins only receive the worktree path); generate-content support was not verified today.
- sources: https://registry.npmjs.org/opencode-ai ; https://api.github.com/repos/sst/opencode ; https://api.github.com/repos/sst/opencode/releases?per_page=2 ; https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/cli.mdx ; https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/config.mdx ; https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/providers.mdx ; https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/agents.mdx ; https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/acp.mdx ; https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/plugins.mdx ; https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/index.mdx ; https://raw.githubusercontent.com/opencode-ai/opencode/main/README.md ; https://github.com/opencode-ai/opencode

#### oh-my-openagent
- verified: yes (2026-10-16), basis: npm registry (`oh-my-openagent`, `oh-my-opencode`, `omo-ai`), GitHub repo metadata and releases, LICENSE.md, README, docs (reference/cli, reference/opencode-config, guide/agent-model-matching, guide/migrating-from-opencode, guide/telemetry).
- link: https://github.com/code-yeongyu/oh-my-openagent (site https://omo.dev). Naming: formerly "oh-my-opencode". npm `oh-my-openagent` (preferred) and `oh-my-opencode` (compatibility) are the OpenCode plugin edition; "OmO Native" is the standalone `omo` command from npm `omo-ai`, which runs a pinned engine named senpi with the OMO extension built in (the engine's lineage is not stated in the fetched docs).
- license: SUL-1.0 (Sustainable Use License 1.0 per LICENSE.md: use or modify only for internal business, non-commercial or personal purposes; distribute only free of charge for non-commercial purposes; third-party components keep their own licenses). GitHub API reports NOASSERTION.
- version: 5.1.33 (released 2026-10-13; same version on all three npm packages)
- platforms: Windows native (`install.ps1` installs the native binary), macOS native, Linux native; the plugin edition follows OpenCode
- what we use: nothing by default (reference only). If benchmarked as a worker: `bunx oh-my-openagent run <message> --json` with `-a/--agent`, `-m/--model provider/model`, `-d/--directory`, `--attach <url>`, `-p/--port`, `--session-id`, `--on-complete <cmd>`, `--verbose`; `run` exits only when the main session is idle, all todos are completed or cancelled, all descendant sessions are idle and no continuation hooks are active. Plugin registration in `opencode.json` prefers `oh-my-openagent`. Config `~/.omo/omo.jsonc` and project `.omo/omo.jsonc` (closest wins): `agents.<name>.model|models|reasoning`, `categories.<name>.model|models`, `[opencode]` and `[native]` blocks. Native custom providers: `~/.omo/agent/models.json` (protocol picked by `@ai-sdk/openai-compatible`, `@ai-sdk/anthropic` or `@ai-sdk/openai`). Telemetry opt-out: `DO_NOT_TRACK=1` or `OMO_DISABLE_POSTHOG=1`. Worktrees: `worktree-sweep` command reports or removes stale linked worktrees.
- what we borrow: ideas only: the category vocabulary (`quick`, `deep-low`, `deep-high`, `ultrabrain`, `visual-engineering`, `writing`, `architect`, `artistry`, `unspecified-low`, `unspecified-high`) as a seed label set for the §7 classifier; ordered per-category fallback chains; the multi-condition completion rule for headless runs.
- what we avoid: any code reuse (SUL-1.0); its hard-wired hosted-model ladders (docs: "A model outside the ladder isn't supported as the main agent"); default-on telemetry.
- risks: license terms; two editions and repeated renames (the `omo` bin moved from the plugin packages to `omo-ai`); ACP support not documented; frequent releases.
- sources: https://registry.npmjs.org/oh-my-openagent ; https://registry.npmjs.org/oh-my-opencode ; https://registry.npmjs.org/omo-ai ; https://api.github.com/repos/code-yeongyu/oh-my-openagent ; https://api.github.com/repos/code-yeongyu/oh-my-openagent/releases?per_page=2 ; https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/LICENSE.md ; https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/README.md ; https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/docs/reference/cli.md ; https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/docs/reference/opencode-config.md ; https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/docs/guide/agent-model-matching.md ; https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/docs/guide/migrating-from-opencode.md ; https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/docs/guide/telemetry.md

#### Kimi Code
- verified: yes (2026-10-16), basis: npm registry, GitHub repo metadata and releases, PyPI JSON for the archived Python packages, README, docs (reference/kimi-command, configuration/providers, configuration/config-files, configuration/env-vars, customization/hooks, customization/agents, guides/migration).
- link: https://github.com/MoonshotAI/kimi-code (docs https://moonshotai.github.io/kimi-code/en/). Naming: "Kimi Code CLI" (command `kimi`; npm https://registry.npmjs.org/@moonshot-ai%2Fkimi-code) replaces the Python `kimi-cli`; PyPI `kimi-cli` 1.52.0 and `kimi-code` 1.52.0 (2026-09-22) are marked archived / legacy.
- license: MIT
- version: 2.2.0 (released 2026-10-14)
- platforms: Windows native (requires Git for Windows; `KIMI_SHELL_PATH` for a non-default `bash.exe`), macOS native, Linux native
- what we use: Headless: `kimi -p "<task>" --output-format stream-json` (stdout JSONL: assistant messages, assistant messages with `tool_calls`, tool messages; thinking not written; progress on stderr); `-p` runs under the `auto` permission policy (static deny rules still apply) and cannot be combined with `--yolo`, `--auto` or `--plan`; also `-m <alias>`, `--agent <name>`, `--agent-file <path>`, `--add-dir`, `--skills-dir`, `-S/--session`, `-c/--continue`; `kimi acp`; `kimi web` (local server). Endpoint without writing files: `KIMI_MODEL_NAME` (enable switch), `KIMI_MODEL_API_KEY`, `KIMI_MODEL_PROVIDER_TYPE` (the kimi, messages-format or chat-completions provider type), `KIMI_MODEL_BASE_URL`, `KIMI_MODEL_MAX_CONTEXT_SIZE`, `KIMI_MODEL_CAPABILITIES`, `KIMI_MODEL_THINKING_EFFORT`; precedence `-m` > `KIMI_MODEL_*` > `default_model`. Endpoint with a file: `KIMI_CODE_HOME=<dir>` + `config.toml`: `[providers.<n>] type` (one value each for the chat-completions, responses, messages and generate-content formats, a hosted-cloud variant, and kimi), `base_url`, `api_key` or `api_key_env`, `custom_headers`; `[models.<alias>] provider`, `model`, `max_context_size`, `capabilities`; top-level `default_model`. Slots: main `default_model`; subagents `[secondary_model]` with `default_model`, `models` (described pool; the main agent picks per spawn through the `model` parameter of the `Agent` / `AgentSwarm` tools), `force`, `default_effort`; built-in subagents `coder`, `explore`, `plan`. Hooks (command hooks in `config.toml`): blockable `UserPromptSubmit`, `PreToolUse`, `Stop`; observe-only `UserPromptQueued`, `TurnStarted`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionResult`, `SessionStart`, `SessionEnd`, `SessionHeartbeat`, `SubagentStart`, `SubagentStop`, `TaskStarted`, `StopFailure`, `Interrupt`, `PreCompact`, `PostCompact`, `Notification`. Egress: `KIMI_DISABLE_TELEMETRY=1`.
- what we borrow: the `secondary_model` pool (router options described to the main agent, chosen per spawn) as a pattern for exposing router choices inside a harness; env-synthesized ephemeral providers for adapters.
- what we avoid: the archived Python packages; the `/provider` import flow (fetches a public model catalog; falls back to a bundled snapshot offline).
- risks: 2.x rewrite and data-dir change (`~/.kimi` -> `~/.kimi-code`, migration via `kimi migrate`); the env-only path supports only the kimi, messages-format and chat-completions provider types, so responses or generate-content need a generated `config.toml`; worktree support not documented.
- sources: https://registry.npmjs.org/@moonshot-ai%2Fkimi-code ; https://api.github.com/repos/MoonshotAI/kimi-code ; https://api.github.com/repos/MoonshotAI/kimi-code/releases?per_page=2 ; https://pypi.org/pypi/kimi-cli/json ; https://pypi.org/pypi/kimi-code/json ; https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/README.md ; https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/docs/en/reference/kimi-command.md ; https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/docs/en/configuration/providers.md ; https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/docs/en/configuration/config-files.md ; https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/docs/en/configuration/env-vars.md ; https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/docs/en/customization/hooks.md ; https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/docs/en/customization/agents.md ; https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/docs/en/guides/migration.md

#### Claude Code
- verified: yes (2026-10-16), basis: npm registry, GitHub repo metadata and releases, README and LICENSE.md, official docs (cli-reference, headless, goal, model-config, llm-gateway, llm-gateway-connect, llm-gateway-protocol, hooks, settings, env-vars, sub-agents, setup).
- link: https://code.claude.com/docs/en/overview (repo https://github.com/anthropics/claude-code; npm https://registry.npmjs.org/@anthropic-ai%2Fclaude-code)
- license: Proprietary (LICENSE.md: all rights reserved; use subject to the vendor's commercial terms)
- version: 2.1.301 (released 2026-10-15); npm `stable` dist-tag 2.1.292
- platforms: Windows native (Windows 10 1809+ or Server 2019+; PowerShell or CMD; WSL also supported), macOS native, Linux native
- what we use: Headless: `claude -p "<task>"`; `--output-format text|json|stream-json` (docs pair `stream-json` with `--verbose`; add `--include-partial-messages` for token deltas; last line is a `result` message with cost and session metadata); `--input-format text|stream-json` (+ `--replay-user-messages`); `--model <alias|id>`; `--fallback-model`; `--allowedTools`, `--disallowedTools`, `--tools`; `--permission-mode default|acceptEdits|plan|auto|dontAsk|bypassPermissions` (`manual` = alias of `default`); `--permission-prompts none` (>= 2.1.259); `--max-turns N`; `--max-budget-usd`; `--json-schema`; `--no-session-persistence`; `--bare` (skip discovery of hooks, skills, plugins, MCP servers, CLAUDE.md); `--settings <file|json>`; `--setting-sources user,project,local`; `--restricted` (for evaluation harnesses, >= 2.1.248); `-w/--worktree <name>` (creates `<repo>/.claude/worktrees/<name>`); `--agents <json>`; `--forward-subagent-text`; `--include-hook-events`; `--session-id`, `--resume`, `--continue`, `--fork-session`; `--effort`. Endpoint (messages format only): `ANTHROPIC_BASE_URL`; credential `ANTHROPIC_AUTH_TOKEN` (sent as `Authorization: Bearer`) or `ANTHROPIC_API_KEY` (`x-api-key`) or `apiKeyHelper` (both headers); `ANTHROPIC_CUSTOM_HEADERS`. Proxy contract: serve `POST /v1/messages?beta=true` as SSE with the full event sequence through `message_delta` and `message_stop`, keep `ping` events (idle watchdog), optional `/v1/messages/count_tokens` (else a character estimate), tolerate `HEAD /api/hello`; forward `anthropic-version` (`2023-06-01`), `anthropic-beta` and unknown body fields as open lists; gateway `/v1/models` discovery only with `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` and only IDs containing the vendor's model-family or company name survive. Slots: main (priority `/model` > `--model` > `ANTHROPIC_MODEL` > `model` setting > `ANTHROPIC_DEFAULT_MODEL`); alias pins `ANTHROPIC_DEFAULT_OPUS_MODEL` (also `opusplan` in plan mode), `ANTHROPIC_DEFAULT_SONNET_MODEL` (`opusplan` otherwise), `ANTHROPIC_DEFAULT_HAIKU_MODEL` (small-fast slot: background work and the `/goal` evaluator; replaces deprecated `ANTHROPIC_SMALL_FAST_MODEL`), `ANTHROPIC_DEFAULT_FABLE_MODEL`; subagents `CLAUDE_CODE_SUBAGENT_MODEL` (+ `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`; per-definition `model: sonnet|opus|haiku|fable|<id>|inherit`); `--advisor <model>`; context window for unrecognized IDs `CLAUDE_CODE_MAX_CONTEXT_TOKENS` (else 200K assumed); capability declarations `ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES` (same pattern per family). Routing hints: `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` (>= 2.1.273; off by default for a custom base URL) adds `x-claude-code-request-class` (`main` | `subagent` | `workflow` | `compaction` | `auxiliary`), `x-claude-code-agent-type`, `x-claude-code-compaction`, `x-claude-code-context-compacted`, `x-claude-code-prev-tool-durations`, `x-claude-code-prompt-id`; always sent: `x-claude-code-session-id`, `x-claude-code-agent-id`, `x-claude-code-parent-agent-id`. Hooks (settings key `hooks`; handler types `command`, `http`, `mcp_tool`, `prompt`, `agent`): `SessionStart`, `Setup`, `InstructionsLoaded`, `UserPromptSubmit`, `UserPromptExpansion`, `MessageDisplay`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `PermissionDenied`, `Notification`, `SubagentStart`, `SubagentStop`, `TaskCreated`, `TaskCompleted`, `Stop`, `StopFailure`, `TeammateIdle`, `ConfigChange`, `CwdChanged`, `DirectoryAdded`, `FileChanged`, `WorktreeCreate`, `WorktreeRemove`, `PreCompact`, `PostCompact`, `PreModelSwitch`, `PostModelSwitch`, `SessionEnd`, `Elicitation`, `ElicitationResult`. `/goal <condition>` (max 4,000 chars): one goal per session, replaces an active one, starts a turn immediately with the condition as the directive; it is a session-scoped prompt-based Stop hook: after each turn the condition plus transcript go to the small-fast model, which returns "not yet met" (keep working, reason fed back), "met" (goal cleared, achieved entry) or "impossible" (goal cleared, failed entry); the evaluator calls no tools and judges only what the transcript shows; `/goal` alone shows condition, elapsed time, evaluated turns, token spend and last reason; `/goal clear` (aliases `stop`, `off`, `reset`, `none`, `cancel`; `/clear` also removes it); cleared on errors the user must fix (auth failure when self-managed, exhausted credit, context overflow that auto-compaction cannot clear, unavailable model); other errors keep it (interactive: retry up to three times, or pause); evaluation is skipped while a subagent or background shell runs, with check-ins after 30 minutes by default (`CLAUDE_CODE_GOAL_CHECKIN_MINUTES`; `0` disables check-ins and automatic retries); the loop stops with a warning after several turns without tool use; restored on resume with counters reset; `claude -p "/goal <condition>"` runs the loop to completion in one process (use `--output-format stream-json --verbose` to see progress); unavailable when `disableAllHooks` is true or managed `allowManagedHooksOnly` is set; does not change the permission mode. Settings: precedence managed (`managed-settings.json`, MDM, server-managed) > `--settings` > `.claude/settings.local.json` > `.claude/settings.json` > `~/.claude/settings.json`; `env` block inside settings; `CLAUDE_CONFIG_DIR` relocates `~/.claude`. Egress: `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` (also disables auto-update); the fast-mode availability check and the WebFetch domain safety check still call the vendor API directly.
- what we borrow: the request-class vocabulary (`main`, `subagent`, `workflow`, `compaction`, `auxiliary`) as step classes for §7.1; the `/goal` loop shape (condition, separate evaluator, no-progress guard, background-work deferral), with test-based verification (§8) in place of a model verdict; a per-run inline settings layer.
- what we avoid: gateway model discovery (drops non-Claude IDs); `--dangerously-skip-permissions` outside a sandbox; treating `/goal` verdicts as proof.
- risks: proprietary license; the gateway docs state the vendor does not support routing Claude Code to non-Claude models through any gateway; new `anthropic-beta` values and body fields arrive with releases (near-daily); the haiku pin also drives background summarization, so mapping it to a weak model affects more than `/goal`.
- sources: https://registry.npmjs.org/@anthropic-ai%2Fclaude-code ; https://api.github.com/repos/anthropics/claude-code ; https://api.github.com/repos/anthropics/claude-code/releases?per_page=2 ; https://raw.githubusercontent.com/anthropics/claude-code/main/README.md ; https://raw.githubusercontent.com/anthropics/claude-code/main/LICENSE.md ; https://code.claude.com/docs/en/cli-reference.md ; https://code.claude.com/docs/en/headless.md ; https://code.claude.com/docs/en/goal.md ; https://code.claude.com/docs/en/model-config.md ; https://code.claude.com/docs/en/llm-gateway.md ; https://code.claude.com/docs/en/llm-gateway-connect.md ; https://code.claude.com/docs/en/llm-gateway-protocol.md ; https://code.claude.com/docs/en/hooks.md ; https://code.claude.com/docs/en/settings.md ; https://code.claude.com/docs/en/env-vars.md ; https://code.claude.com/docs/en/sub-agents.md ; https://code.claude.com/docs/en/setup.md ; https://registry.npmjs.org/@agentclientprotocol%2Fclaude-agent-acp ; https://registry.npmjs.org/@zed-industries%2Fclaude-code-acp

#### mini-swe-agent
- verified: yes (2026-10-16), basis: PyPI JSON, GitHub repo metadata and releases, README, docs (usage/mini, models/quickstart, models/local_models, advanced/global_configuration, advanced/v2_migration), source (`run/mini.py`, `environments/local.py`).
- link: https://mini-swe-agent.com/latest/ (repo https://github.com/SWE-agent/mini-swe-agent)
- license: MIT
- version: 2.4.6 (released 2026-07-23)
- platforms: Windows not documented (the local environment has a non-POSIX code path, but the agent issues bash commands, so WSL2 is the safe choice), macOS native, Linux native; Python >= 3.10
- what we use: run in the repo directory: `mini -t "<task>" -y --exit-immediately -m <model> -o <traj.json> -l 0`; flags `-t/--task`, `-y/--yolo`, `-m/--model`, `-c/--config` (repeatable, accepts `key=value` overrides such as `-c agent.step_limit=100`), `-o/--output` (trajectory file), `-l/--cost-limit` (0 disables), `--exit-immediately`, `--model-class`, `--agent-class`, `--environment-class`. Model config (YAML): `model.model_name`, `model.model_class` (`litellm` = native tool calling, default in v2; `litellm_textbased`; `litellm_response_toolcall` for responses; `openrouter`, `openrouter_textbased`, ...), `model.model_kwargs.api_base`, `custom_llm_provider`, `cost_tracking: "ignore_errors"`. Env: `MSWEA_MODEL_NAME`, `MSWEA_COST_TRACKING`, `MSWEA_CONFIG_DIR`, `MSWEA_MINI_CONFIG_PATH`, `MSWEA_GLOBAL_CALL_LIMIT`, `MSWEA_GLOBAL_COST_LIMIT`, `MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT`. Benchmarks: `mini-extra swebench`.
- what we borrow: a bash-only baseline worker for the §16 leaderboard; the v2 split where the model class owns action parsing (tool calls vs text), matching the §4.4 fallback for models without native tool calling; one independent subprocess per action.
- what we avoid: litellm cost-registry errors for local models (set `cost_tracking` to `ignore_errors` or supply a registry).
- risks: depends on litellm (pins out 1.82.7 and 1.82.8); one model per run (no slots); no ACP; Windows behavior undocumented.
- sources: https://pypi.org/pypi/mini-swe-agent/json ; https://api.github.com/repos/SWE-agent/mini-swe-agent ; https://api.github.com/repos/SWE-agent/mini-swe-agent/releases?per_page=2 ; https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/README.md ; https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/docs/usage/mini.md ; https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/docs/models/quickstart.md ; https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/docs/models/local_models.md ; https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/docs/advanced/global_configuration.md ; https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/docs/advanced/v2_migration.md ; https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/run/mini.py ; https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/environments/local.py

#### SWE-agent
- verified: yes (2026-10-16), basis: GitHub repo metadata and releases, `sweagent/__init__.py` and `pyproject.toml` on main, README, docs (usage/hello_world, usage/cl_tutorial, installation/source, installation/keys, config/models), PyPI JSON.
- link: https://swe-agent.com (repo https://github.com/SWE-agent/SWE-agent)
- license: MIT
- version: 1.1.0 (released 2025-05-22; still `__version__` on main today)
- platforms: Windows not documented; macOS and Linux with Docker (default sandbox); Python >= 3.11
- what we use: install from source (`pip install --editable .`; the PyPI name `sweagent` is a 0.0.1 placeholder from 2024); local repo run: `sweagent run --agent.model.name=<litellm id> --agent.model.per_instance_cost_limit=0 --env.repo.path=<repo> --problem_statement.text="..."` (or `--problem_statement.path=<file.md>`); `--config <yaml>` (repeatable; default `config/default.yaml`); model YAML `agent.model.api_base`, `total_cost_limit`, `max_input_tokens`, `litellm_model_registry`; `agent.tools.parse_function.type: thought_action` for models without function calling; `--env.deployment.type=modal` for remote execution; `sweagent run-batch` for suites; key rotation by joining keys with `:::` in `--agent.model.api_key`.
- what we borrow: parse-function choice and history processors as reference designs; nothing at runtime.
- what we avoid: building an adapter (README: "Our general recommendation is to use mini-SWE-agent instead of SWE-agent going forward").
- risks: no release since 2025-05-22; Docker requirement; litellm dependency.
- sources: https://pypi.org/pypi/sweagent/json ; https://pypi.org/pypi/swe-agent/json ; https://api.github.com/repos/SWE-agent/SWE-agent ; https://api.github.com/repos/SWE-agent/SWE-agent/releases?per_page=2 ; https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/README.md ; https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/__init__.py ; https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/pyproject.toml ; https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/docs/usage/hello_world.md ; https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/docs/usage/cl_tutorial.md ; https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/docs/installation/source.md ; https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/docs/installation/keys.md ; https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/docs/config/models.md

### Multi-agent orchestration

#### open-multi-agent
- verified: yes (2026-10-16). Basis: GitHub repo page and releases API, npm registry (current and deprecated package), and two docs files fetched raw from the repo.
- link: https://github.com/open-multi-agent/open-multi-agent. Fetching the old URL https://github.com/JackChen-me/open-multi-agent served this repo.
- license: MIT
- version: 1.21.2 (released 2026-10-09). npm `@open-multi-agent/core` 1.21.2 was published 2026-10-09; the package was created 2026-05-09.
- platforms: Windows native, macOS and Linux, all unverified. It is a Node.js library (`engines: node >=20.0.0`) and the README states no OS restriction. The README says Node.js 20 support goes in the next major release, no earlier than 2026-10-31.
- name ambiguity (SPEC: "open-multi-agent (task DAG)"):

| Candidate | URL | What it is | Match |
|---|---|---|---|
| open-multi-agent | https://github.com/open-multi-agent/open-multi-agent | TypeScript agent runtime with `runTeam()`/`runTasks()` task DAG; 7.0k stars | **Pick.** Only candidate with a task DAG scheduler. The npm package moved from `@jackchen_me/open-multi-agent`, which is now deprecated with the message "Package moved to @open-multi-agent/core". |
| Open Multi-Agent Canvas | https://github.com/CopilotKit/open-multi-agent-canvas | MIT chat UI for several LangGraph agents with MCP servers; the README says it was consolidated into a monorepo | No DAG scheduler mentioned |

- what it does: The README calls it a "Self-hosted TypeScript agent runtime with durable approvals and verifiable run records". Its API includes `runAgent()`, `runTasks()`, `runTeam()`, `createTeam`, `FileStore`, `verifyRun()` and `defineTool`. It works with many hosted providers and with local servers (Ollama, vLLM, llama-server) through `baseURL`.
- task DAG model (docs/task-scheduling.md):

| Aspect | Behavior |
|---|---|
| Task spec | `title`, `description`, `assignee`, `role`, `metadata` (max 16), `dependsOn`, `dependencyPayload` (`output`, `structured`, `both`), `requires` (`requiredTools`, `requiredCapabilities`, `requiredBackend`, `requiredProvider`) |
| Readiness | `TaskQueue` emits `task:ready` when no dependency is unresolved. A ready task starts without waiting for unrelated ready tasks. |
| Concurrency | `AgentPool` semaphore |
| Assignment | Eligible agents are filtered first, then ranked: `dependency-first`, `composite` (`schedulingWeights.fit` 0.7, `load` 0.3), `round-robin`, `least-busy`. Unmet requirements raise `INVALID_TASK_REQUIREMENTS`. |
| Retries | Opt-in `maxRetries`. Provider rate limits, server, network and timeout errors are retried. Validation errors, cancellation and budget exhaustion are terminal. |
| Failure | A failed or skipped task cascades to its dependents. Unrelated branches keep running. On abort, budget exhaustion or rejection, no new tasks start and remaining tasks are marked `skipped`. |
| Gates | `onTaskDispatch` runs after assignment and before dispatch. `onApproval` is the legacy option; setting both throws. |
| Budget | A cumulative token budget is checked before dispatch and after each completion. |

- model routing (docs/model-routing.md): `modelRouting.rules` is a list of `{ match, route }` pairs, checked in order, first match wins. Match fields are `phase`, `agent`, `taskRole`, `taskPriority`, `leaf` and `hasDependencies`. `route.model` is required, and `route.fallback` is an ordered backup list that advances only on retryable provider errors. Routing is decided per orchestration call in five phases (`coordinator`, `synthesis`, `worker`, `delegated`, `short-circuit`). The doc describes no per-turn routing, no cost-based selection and no learning.
- borrow: the task spec and failure semantics above for `core/task`, the step DAG and `verify/best_of_n` fan-out. Also the `{match, route, fallback}` rule shape for the rule layer in `router/rules`. Its offline-verifiable run records (`verifyRun()`) and tamper-evident approvals are design inputs for §11 trajectories and the §15 audit log.
- avoid: depending on the TypeScript runtime, and letting a coordinator plan run without Switchyard gates.
- risks: weekly minor releases (1.19.0 on 2026-09-11 to 1.21.2 on 2026-10-09). The repo and package have moved once already.
- sources: https://github.com/JackChen-me/open-multi-agent ; https://api.github.com/repos/open-multi-agent/open-multi-agent/releases?per_page=5 ; https://registry.npmjs.org/@open-multi-agent/core ; https://registry.npmjs.org/@jackchen_me/open-multi-agent/latest ; https://raw.githubusercontent.com/open-multi-agent/open-multi-agent/main/docs/task-scheduling.md ; https://raw.githubusercontent.com/open-multi-agent/open-multi-agent/main/docs/model-routing.md ; https://github.com/CopilotKit/open-multi-agent-canvas

#### Conductor
- verified: yes (2026-10-16). Basis: GitHub repo page and releases API, plus `pyproject.toml`, the docs listing and `docs/workflow-syntax.md` from the repo.
- link: https://github.com/microsoft/conductor
- license: MIT
- version: 0.1.42 (released 2026-10-06). The Python distribution is named `conductor-cli`, needs Python >=3.12 and installs the `conductor` script. The README installs it from the Git repo with `install.sh` or `install.ps1` (uv), or with `uv tool install`, `pipx` or `pip`, and documents no PyPI install.
- platforms: Windows native (`install.ps1`), macOS and Linux (`install.sh`)
- name ambiguity (SPEC: "Conductor (deterministic YAML routes)"):

| Candidate | URL | What it is | Match |
|---|---|---|---|
| Conductor (YAML workflow CLI) | https://github.com/microsoft/conductor | CLI for multi-agent workflows defined in YAML with deterministic routing | **Pick.** Only candidate with YAML routes evaluated without an LLM. |
| Conductor (macOS app) | https://www.conductor.build/ | "Run parallel Claude Code, Codex, and Cursor agents in isolated workspaces on your Mac." No license or source link on the site. | A worktree-fleet-style app, not YAML routes |
| Conductor OSS | https://github.com/conductor-oss/conductor | Apache-2.0 durable workflow engine. Orchestration is declared in JSON. 32.3k stars. | JSON, not YAML; a general workflow engine |
| Conductor (agent plugin) | https://github.com/gemini-cli-extensions/conductor | Apache-2.0 plugin for spec-driven development in coding agents | No routes |

- what it does: A YAML file declares `workflow` (`name`, `entry_point`, `runtime`, `limits`), `agents` (`name`, `model`, `prompt`, `output`, `routes`) and `output`. Providers are Copilot (the default), chat-completions/responses APIs, Claude, Claude Agent SDK (experimental) and Hermes (experimental). Custom compatible endpoints such as Ollama and vLLM go through the Copilot provider. `--web` starts a live dashboard with a DAG graph, and `conductor fleet` is a TUI over running processes.
- route format: `routes: - to: <agent|$end>` with an optional `when: "{{ <jinja2 expr> }}"`. Script and set steps use bare simpleeval expressions such as `exit_code == 0`. The first match wins and no LLM is involved. Limits sit under `workflow.limits`: `max_iterations` (default 10, max 500), `timeout_seconds`, `budget_usd`, `budget_mode` (`audit` by default, or `enforce`). Steps include `parallel` groups, `for_each` (`source`, `as`, `max_concurrent`), script steps (`command`, `args`, `env`, `working_dir`, `timeout`, `stdin`; outputs `stdout`, `stderr`, `exit_code`), `type: human_gate`, and `type: workflow` (sub-workflows, `MAX_SUBWORKFLOW_DEPTH = 10`). `context_mode` is `accumulate` (default), `snapshot` or `minimal`.
- borrow: `{to, when}` routes with first-match semantics and `$end` for a declarative "pipeline mode" in `router/rules` (fixed plan, implement and test routes). Script-step routing on `exit_code` maps directly onto §8.1 gates. Also the `limits` block (iterations, timeout, budget, audit or enforce mode) for §7.4 budgets and RN10 limits, and run bundles (0.1.41) as a design input for exporting §11 trajectories.
- avoid: importing Conductor as a library. Its Python floor (>=3.12) is above Switchyard's (>=3.11), and its 21 runtime deps include a Copilot SDK and `pydantic-ai-slim`. Also avoid routing only at agent granularity, because Switchyard routes per step.
- risks: pre-1.0, with 5 releases between 2026-09-21 and 2026-10-06, so the YAML format may change. The name collides with at least 3 other projects.
- sources: https://github.com/microsoft/conductor ; https://api.github.com/repos/microsoft/conductor/releases?per_page=5 ; https://raw.githubusercontent.com/microsoft/conductor/main/pyproject.toml ; https://github.com/microsoft/conductor/tree/main/docs ; https://raw.githubusercontent.com/microsoft/conductor/main/docs/workflow-syntax.md ; https://www.conductor.build/ ; https://github.com/conductor-oss/conductor ; https://github.com/gemini-cli-extensions/conductor

#### Claude Squad
- verified: yes (2026-10-16). Basis: GitHub repo page, releases Atom feed, release page, Homebrew formula JSON, and two source files fetched raw.
- link: https://github.com/smtg-ai/claude-squad (Homebrew homepage https://smtg-ai.github.io/claude-squad/). The searches made found no other project with this name.
- license: AGPL-3.0-only in Homebrew metadata; GitHub shows AGPL-3.0.
- version: 1.0.20 (released 2026-08-20). Earlier releases: 1.0.19 on 2026-06-17 and 1.0.18 on 2026-05-23.
- platforms: Windows via WSL2, inferred. goreleaser builds a `windows` zip, but tmux is a stated prerequisite. macOS and Linux are supported, with Homebrew bottles for macOS arm64/x86 and Linux arm64/x86_64.
- what it does: Go TUI (`cs`) that runs several terminal agents. The default program is `claude`; codex, gemini, aider and others are set with `-p`. Each instance gets its own tmux session and git worktree. Keys: `n`/`N` new, `↵`/`o` attach, `ctrl-q` detach, `s` commit and push, `c` checkout (commits and pauses), `r` resume, `D` kill. `-y/--autoyes` (experimental) auto-accepts prompts. Config lives in `~/.claude-squad/config.json` (`default_program`, `profiles[{name, program}]`). Prerequisites are tmux and gh.
- worktree handling (`session/git/worktree.go`): the worktree base is `<configDir>/worktrees`. Each path is the sanitized branch name plus `_` plus the hex Unix-nanosecond time. The branch is `cfg.BranchPrefix + sessionName`, sanitized. An existing-branch session "will not be deleted on cleanup", and the base commit SHA is stored (`GetBaseCommitSHA`). Worktrees are created per session, with no pre-created pool. v1.0.20 fixed recording the base commit for existing-branch sessions and recovering instances whose tmux session died with the server.
- borrow: for RN8 / `verify/best_of_n`, the path layout (unique suffix), configurable branch prefix, and a stored base commit per worktree for the diff-size tie-break in §8.3. Also the pause/resume (checkout) lifecycle, re-attaching after tmux restarts, and a name-to-command profile map for harness launch commands.
- avoid: copying code (AGPL-3.0-only vs Switchyard MIT), and tmux as a hard dependency, which would block native Windows (`install.ps1` is planned in §21). Use headless subprocess or ACP with event streaming, and make tmux optional for viewing. Also avoid auto-accepting agent prompts (`-y`), and creating worktrees on demand at dispatch time.
- risks: the release pace slowed (about one release every 1-2 months in 2026). It drives each agent's interactive TUI, and per the README auto-accept covers only "claude code & aider".
- similar worktree fleet runners (active = release in the last 2 months):

| Name | URL | Version (date) | License | One line |
|---|---|---|---|---|
| ccmanager | https://github.com/kbwo/ccmanager | 4.4.7 (2026-10-15) | MIT | TUI for managing multiple Claude Code sessions across git worktrees (npm description) |
| workmux | https://github.com/raine/workmux | 0.1.276 (2026-10-14) | MIT | "An opinionated workflow tool that orchestrates git worktrees and tmux" (crates.io) |
| git-worktree-runner (`git gtr`) | https://github.com/coderabbitai/git-worktree-runner | 2.11.1 (2026-09-14) | Apache-2.0 | Bash worktree manager with editor and agent launchers. Windows only via Git Bash or WSL. |
| dmux | https://github.com/standardagents/dmux | 5.11.1 (2026-08-16) | MIT | "Every pane gets its own git worktree and branch". Merge or open a PR from the pane menu. Needs tmux >=3.0 and Node >=18. |
| Vibe Kanban (not recommended) | https://github.com/BloopAI/vibe-kanban | npm 0.1.44 (2026-04-24); tagged builds until 2026-09-19 | npm license field empty | Sunset notice in the v0.1.42 and v0.1.44 release notes |
| container-use (stale) | https://github.com/dagger/container-use | 0.4.2 (2025-08-19) | Apache-2.0 | A container plus a git branch per agent, through MCP. Marked experimental. No release in 14 months. |

- sources: https://github.com/smtg-ai/claude-squad ; https://github.com/smtg-ai/claude-squad/releases.atom ; https://github.com/smtg-ai/claude-squad/releases/tag/v1.0.20 ; https://formulae.brew.sh/api/formula/claude-squad.json ; https://raw.githubusercontent.com/smtg-ai/claude-squad/main/session/git/worktree.go ; https://raw.githubusercontent.com/smtg-ai/claude-squad/main/.goreleaser.yaml ; https://registry.npmjs.org/-/v1/search?text=ccmanager&size=2 ; https://crates.io/api/v1/crates/workmux ; https://github.com/coderabbitai/git-worktree-runner ; https://github.com/coderabbitai/git-worktree-runner/releases.atom ; https://github.com/standardagents/dmux ; https://registry.npmjs.org/-/v1/search?text=dmux&size=2 ; https://registry.npmjs.org/-/v1/search?text=vibe-kanban&size=2 ; https://github.com/BloopAI/vibe-kanban/releases.atom ; https://github.com/dagger/container-use ; https://github.com/dagger/container-use/releases.atom

#### Agent Client Protocol
- verified: yes (2026-10-16). Basis: the spec site (overview, transports, initialization, schema), the agents and clients pages, the spec repo releases feed, PyPI, the npm registry, crates.io, the SDK repo and docs, and `interfaces.py` fetched raw.
- link: https://agentclientprotocol.com (spec repo https://github.com/agentclientprotocol/agent-client-protocol)
- license: Apache-2.0 (crate `agent-client-protocol-schema` built from the spec repo, and all three SDKs)
- version: schema 1.25.0 (released 2026-10-09). The wire-level `protocolVersion` is the integer `1`, which counts major versions only. Schema 2.0.0-alpha.8 (2026-10-09) is a pre-release.
- platforms: OS-neutral protocol. The agent runs as a subprocess of the client.
- transport: JSON-RPC 2.0 over stdio, UTF-8 and newline-delimited. Messages "MUST NOT contain embedded newlines". stdout carries only ACP messages; stderr is free for logs. The client shuts down by closing stdin and ending the subprocess. Streamable HTTP is "In discussion, draft proposal in progress". Custom transports are allowed.
- conventions: file paths "MUST be absolute", line numbers are 1-based, keys are camelCase, and discriminators are snake_case. Extensions use `_meta`, and custom methods are prefixed with `_`.
- methods (stable schema page):

| Direction | Method | Kind | Gate |
|---|---|---|---|
| client → agent | `initialize` | request | baseline |
| client → agent | `authenticate` | request | baseline (when `authMethods` is non-empty) |
| client → agent | `session/new` | request | baseline (`cwd`, `mcpServers`, `additionalDirectories`) |
| client → agent | `session/prompt` | request | baseline; result `stopReason` |
| client → agent | `session/cancel` | notification | baseline |
| client → agent | `session/load` | request | `loadSession` |
| client → agent | `session/resume`, `session/list`, `session/close`, `session/delete` | request | session capabilities |
| client → agent | `session/set_mode`, `session/set_config_option` | request | optional |
| client → agent | `logout` | request | `auth.logout` |
| agent → client | `session/update` | notification | baseline |
| agent → client | `session/request_permission` | request | baseline |
| agent → client | `fs/read_text_file`, `fs/write_text_file` | request | `fs.readTextFile`, `fs.writeTextFile` |
| agent → client | `terminal/create`, `terminal/output`, `terminal/wait_for_exit`, `terminal/kill`, `terminal/release` | request | `terminal` |
| agent → client | `elicitation/create` / `elicitation/complete` | request / notification | `elicitation` |
| either | `$/cancel_request` | notification | protocol level |

- `session/update` kinds: `user_message_chunk`, `agent_message_chunk`, `agent_thought_chunk`, `tool_call`, `tool_call_update`, `plan`, `available_commands_update`, `current_mode_update`, `config_option_update`, `session_info_update`, `usage_update`, `notice`, `compaction_update`, `compaction_summary_chunk`.
- `StopReason` values: `end_turn`, `max_tokens`, `max_turn_requests`, `refusal`, `cancelled`. Permission option kinds: `allow_once`, `allow_always`, `reject_once`, `reject_always`.
- capabilities: the client sends `fs.readTextFile`, `fs.writeTextFile`, `terminal`, `auth.terminal`, `elicitation`, `session.configOptions.boolean`, `session.compaction` and `session.notices`. The agent sends `loadSession`, `promptCapabilities` (`image`, `audio`, `embeddedContext`), `mcpCapabilities` (`http`, plus `sse`, which is deprecated), `auth` (`logout`), and session capabilities (`delete`, `additionalDirectories`, `close`, ...). The Python SDK also exposes `fork_session`, NES, provider and MCP-over-ACP methods that the stable schema page does not list.
- official SDKs:

| Language | Package | Version (date) | License | Notes |
|---|---|---|---|---|
| Python | `agent-client-protocol` (import `acp`) | 0.12.1 (2026-08-16); pre-release 1.0.0rc3 (2026-10-12) | Apache-2.0 (GitHub; the PyPI license field is empty) | `requires_python` <3.15,>=3.10; https://github.com/agentclientprotocol/python-sdk |
| TypeScript | `@agentclientprotocol/sdk` | 1.8.0 (2026-10-09) | Apache-2.0 | peer dep `zod` ^3.25 or ^4; https://github.com/agentclientprotocol/typescript-sdk |
| Rust | `agent-client-protocol` | 3.3.0 (2026-10-09) | Apache-2.0 | MSRV 1.88.0; https://github.com/agentclientprotocol/rust-sdk |
| Rust (schema types) | `agent-client-protocol-schema` | 1.11.0 (2026-10-09) | Apache-2.0 | lives in the spec repo |

- support today: the agents page lists 43 agents. Those relevant to §12 include Gemini CLI, OpenCode, Kimi CLI, Qwen Code, Cursor, GitHub Copilot (public preview), Goose, OpenHands, Cline, Kiro CLI, Junie and fast-agent. Some work through adapters: Claude Agent (https://github.com/zed-industries/claude-agent-acp), Codex CLI (https://github.com/agentclientprotocol/codex-acp) and Pi (https://github.com/svkozak/pi-acp). Clients include Zed, Neovim (CodeCompanion, avante.nvim, agentic.nvim), Emacs (agent-shell.el), VS Code extensions, Sublime Text, Qt Creator, Obsidian plugins, marimo, the CLIs acpx and Toad, and frameworks such as LangChain/LangGraph, LlamaIndex, Mastra and Koog.
- minimal generic Python client (SPEC §12 item 6), mapped to `start/events/cancel/result`:
  1. Spawn the agent with `cwd` set to the worktree, using stdio pipes, newline-delimited JSON-RPC and stderr written to a log. With the SDK: `spawn_agent_process(client, cmd, *args)` returns `(conn, proc)`, where `client` subclasses `acp.interfaces.Client`.
  2. Call `initialize` with `protocolVersion: 1`. Advertise `fs.*` and `terminal` only if they are implemented. Fail closed if the version the agent returns is unsupported. Store `agentCapabilities` and `authMethods`.
  3. Call `authenticate` if the agent requires it, then `session/new` with the absolute worktree `cwd` and optional `mcpServers` (so Switchyard tools can be exposed through MCP).
  4. `start()` sends `session/prompt`. `events()` streams each `session/update` into the trajectory recorder (§11) and the token ledger (`usage_update`). `result()` maps `stopReason` to `RunResult` and adds the worktree diff.
  5. Handle `session/request_permission` with Switchyard policy: allow inside the worktree, reject otherwise, and respect budgets and kill switches. The SDK callbacks are `request_permission` and `session_update`.
  6. Optional handlers, confined to the worktree with absolute paths: `read_text_file`, `write_text_file`, `create_terminal`, `terminal_output`, `wait_for_terminal_exit`, `kill_terminal`, `release_terminal`. Terminals go through the `run(cmd)` sandbox and §15 egress rules.
  7. `cancel()` sends the `session/cancel` notification and waits for `stopReason: cancelled`. After a timeout it closes stdin and kills the process.
  8. Optional, feature-detected: `session/load` or `session/resume` for replay, and `session/set_config_option` for model choice. Whether an agent exposes a model option has to be verified per agent.
- borrow: the protocol as-is. The `workers/acp` adapter uses only stable methods, and a mock stdio agent is needed for conformance tests.
- avoid: the draft HTTP transport, v2 alpha features, and SDK-only extras (NES, provider management, MCP-over-ACP) until they reach the stable schema.
- risks: schema v2 is in alpha, so breaking changes are expected and the client must negotiate versions. The Python SDK is moving 0.12 → 1.0 (RC), and its docs site still shows a 0.11 migration guide. Agents support capabilities unevenly, so everything must be feature-detected.
- sources: https://agentclientprotocol.com/protocol/overview ; https://agentclientprotocol.com/protocol/transports ; https://agentclientprotocol.com/protocol/initialization ; https://agentclientprotocol.com/protocol/schema ; https://agentclientprotocol.com/overview/agents ; https://agentclientprotocol.com/overview/clients ; https://github.com/agentclientprotocol/agent-client-protocol/releases.atom ; https://pypi.org/pypi/agent-client-protocol/json ; https://github.com/agentclientprotocol/python-sdk ; https://agentclientprotocol.github.io/python-sdk/quickstart/ ; https://raw.githubusercontent.com/agentclientprotocol/python-sdk/main/src/acp/interfaces.py ; https://registry.npmjs.org/@agentclientprotocol/sdk/latest ; https://github.com/agentclientprotocol/typescript-sdk/releases.atom ; https://crates.io/api/v1/crates/agent-client-protocol ; https://crates.io/api/v1/crates/agent-client-protocol-schema

### Context and tokens

#### Aider repo map
- verified: yes (2026-10-16), basis: PyPI JSON (aider-chat, grep-ast); `aider/repomap.py`, `LICENSE.txt` and both query-folder READMEs on the main branch; query folder listing.
- link: https://github.com/Aider-AI/aider
- license: Apache-2.0 (aider). `queries/tree-sitter-languages/*.scm`: README says they are modified `tags.scm` files from listed grammar repos (all MIT except tree-sitter-elixir, Apache-2.0). `queries/tree-sitter-language-pack/*.scm`: README says they are adapted from the grammar repos listed in the language pack's `language_definitions.json` and defers licensing to that project; no per-file license record.
- version: aider-chat 0.86.2 (released 2026-02-12); previous release 0.86.1 (2025-08-13). `requires_python` >=3.10,<3.13. Pins: grep-ast 0.9.0, networkx 3.4.2, scipy 1.15.3, diskcache 5.6.3, tree-sitter 0.25.2, tree-sitter-language-pack 0.13.0.
- platforms: Windows native, macOS native, Linux native (pure-Python wheel; Python 3.10-3.12 only).
- what we use: nothing at runtime; no dependency on aider-chat.
- what we borrow: the ranking design below (ideas, reimplemented); def-only-to-lexer reference backfill; binary-search fit to the token budget; bigger map when no files are in focus. Tag queries may be vendored with attribution (Apache-2.0 text for aider's modifications plus each upstream grammar's MIT/Apache notice) only after a per-file license check for the language-pack folder; prefer the `tags.scm` shipped inside per-language grammar wheels.
- what we avoid: aider-chat as a dependency (Python <3.13 cap, heavy pins); mtime-only cache validation (use content hash, shared with TK3); sampled token estimates (count exactly; it is cheap).
- risks: one release since 2025-08-13; language-pack query folder lacks per-file provenance; aider pins tree-sitter-language-pack 0.13.0, which predates the 1.x download-on-first-use model.
- sources: https://pypi.org/pypi/aider-chat/json, https://pypi.org/pypi/grep-ast/json, https://raw.githubusercontent.com/Aider-AI/aider/main/aider/repomap.py, https://raw.githubusercontent.com/Aider-AI/aider/main/LICENSE.txt, https://github.com/Aider-AI/aider/tree/main/aider/queries/tree-sitter-language-pack, https://raw.githubusercontent.com/Aider-AI/aider/main/aider/queries/tree-sitter-language-pack/README.md, https://raw.githubusercontent.com/Aider-AI/aider/main/aider/queries/tree-sitter-languages/README.md

How it works (`repomap.py`, main branch, fetched 2026-10-16):

| Step | Behavior |
|---|---|
| Parse | grep-ast `get_language`/`get_parser` (tree-sitter-language-pack if installed, else tree-sitter-languages); runs `queries/<pack>/<lang>-tags.scm` |
| Tags | captures `name.definition.*` -> def, `name.reference.*` -> ref; if a file yields defs but no refs, refs are backfilled from Pygments `Token.Name` tokens |
| Tag cache | diskcache (SQLite) in `.aider.tags.cache.v4` (`v3` without the pack); key = file path; valid while mtime is unchanged |
| Graph | networkx `MultiDiGraph`; nodes = files; edge referencer -> definer per identifier; weight = mul x sqrt(ref count); mul x10 if identifier mentioned in chat, x10 if snake/kebab/camel case and length >= 8, x0.1 if leading `_`, x0.1 if defined in > 5 files, x50 if referencer is a chat file; self-edge weight 0.1 for unreferenced definitions |
| Rank | `nx.pagerank(G, weight="weight")` with personalization 100/len(files) for chat files, mentioned files, and paths whose components match mentioned identifiers; each node's rank is split over its out-edges by weight into a score per (file, identifier); chat files are excluded from the map |
| Budget | `map_tokens` default 1024; with no chat files: min(map_tokens x 8, context window - 4096); binary search over the number of ranked tags rendered with grep-ast `TreeContext`; accepts a result within 15% of the budget |
| Map cache | in memory; key = chat files, other files, budget (+ mentioned files/identifiers in `auto`); `auto` reuses only if the last build took > 1.0 s; modes `auto`, `always`, `files`, `manual` |
| Token count | texts of 200+ chars are estimated from a line sample (every n/100-th line) |

#### tree-sitter
- verified: yes (2026-10-16), basis: PyPI JSON (tree-sitter, tree-sitter-language-pack, tree-sitter-languages, tree-sitter-python); language-pack README, Python README, architecture and download-model docs; contents of the tree-sitter-python 0.25.0 win_amd64 wheel inspected.
- link: https://github.com/tree-sitter/py-tree-sitter
- license: MIT (py-tree-sitter, tree-sitter-language-pack, tree-sitter-python); tree-sitter-languages Apache-2.0 (PyPI license field). Language-pack README: all bundled grammars permissive (MIT, Apache-2.0, BSD, ISC or similar); copyleft grammars not accepted.
- version: py-tree-sitter 0.26.0 (released 2026-06-30); `requires_python` >=3.10; wheels cp310-cp314.
- platforms: Windows native (win_amd64, win_arm64), macOS native (x86_64, arm64), Linux native (manylinux x86_64/aarch64, riscv64, musllinux x86_64).
- what we use: py-tree-sitter 0.26 plus pinned per-language grammar wheels for the core languages (offline; `TAGS_QUERY` inside the wheel). tree-sitter-language-pack 1.x only as an optional extra for long-tail languages, prefetched by an explicit command into a cache under `.switchyard/` (`configure` with `cache_dir`, `download`, `downloaded_languages`; CLI `ts-pack download <langs>`).
- what we borrow: the pack's bundled `tags` queries where a grammar wheel has none; prefetch-then-parse.
- what we avoid: tree-sitter-languages (no release since 2024-02-04, no cp313+ wheels); lazy parser downloads at runtime; making the pack a hard dependency (wheels only for manylinux_2_34, i.e. glibc >= 2.34, and no musllinux).
- risks: pack churn (85 releases since 2024-07-01; at least 8 between 2026-09-12 and 2026-10-12); 1.0.0 (2026-03-21) switched from 16.9-33.0 MB wheels (0.13.0) to 2.0-2.5 MB wheels plus first-use downloads; the manifest lists a sha256 per bundle but client-side verification is not documented; grammar/runtime ABI pairing must be pinned (tree-sitter-python `core` extra requires `tree-sitter~=0.24`; pack parsers are ABI 14, "backwards compatible with tree-sitter 0.21-0.26").
- sources: https://pypi.org/pypi/tree-sitter/json, https://pypi.org/pypi/tree-sitter-language-pack/json, https://pypi.org/pypi/tree-sitter-languages/json, https://pypi.org/pypi/tree-sitter-python/json, https://files.pythonhosted.org/packages/ed/a2/996fc2dfa1076dc460d3e2f3c75974ea4b8f02f6bc925383aaae519920e8/tree_sitter_python-0.25.0-cp310-abi3-win_amd64.whl, https://raw.githubusercontent.com/xberg-io/tree-sitter-language-pack/main/README.md, https://raw.githubusercontent.com/xberg-io/tree-sitter-language-pack/main/packages/python/README.md, https://docs.tree-sitter-language-pack.xberg.io/, https://docs.tree-sitter-language-pack.xberg.io/concepts/architecture/, https://docs.tree-sitter-language-pack.xberg.io/concepts/download-model/

Grammar packaging today:

| Option | Version (date) | Wheels | Offline | Tag queries | Status |
|---|---|---|---|---|---|
| tree-sitter-language-pack | 1.22.1 (2026-10-12) | abi3 cp310: win_amd64, win_arm64, macOS x86_64/arm64, manylinux_2_34 x86_64/aarch64; no musllinux; 2.2-2.5 MB | no by default: platform bundle downloaded on first use (manifest `parsers.json` on the project's release assets); cache `~/.cache/tree-sitter-language-pack/<ver>/libs` (Linux, honors `XDG_CACHE_HOME`), `~/Library/Caches/...` (macOS), `%LOCALAPPDATA%\tree-sitter-language-pack\<ver>\libs` (Windows); offline after prefetch | bundled `tags` for Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, C#, Ruby, Kotlin, Swift (none for Bash) | active, 372 languages, fast-moving |
| tree-sitter-languages | 1.10.2 (2024-02-04) | cp37-cp312 only; win32/win_amd64, macOS, manylinux, musllinux; no win_arm64 | yes (bundled) | not checked | stale; avoid |
| Per-language wheels (e.g. tree-sitter-python) | 0.25.0 (2025-09-11) | abi3 cp310: win_amd64, win_arm64, macOS x86_64/arm64, manylinux x86_64/aarch64, musllinux x86_64/aarch64; 0.07-0.11 MB | yes | `queries/tags.scm` in wheel, exposed as `TAGS_QUERY` (plus `HIGHLIGHTS_QUERY`) | maintained per grammar; check each pinned wheel |

#### LSP client libraries
- verified: yes (2026-10-16), basis: PyPI JSON (pygls, lsprotocol, multilspy, serena-agent, sansio-lsp-client, pylspclient; `solidlsp` returns 404); multilspy README and LICENSE; serena LICENSE, README and language-support docs; pygls `client.py`.
- link: https://github.com/openlawlibrary/pygls (recommended base)
- license: see table.
- version: see table.
- platforms: all candidates are pure-Python wheels (Windows, macOS, Linux); real platform support depends on each language server. SolidLSP docs: C# requires `pwsh` on Windows; Go and Rust servers need their toolchains.
- what we use: a thin client on pygls `JsonRPCClient` (`start_io` spawns the server via `asyncio.create_subprocess_exec` over stdio; `start_ws` for websockets) with lsprotocol types; launch commands per language in `registry/` TOML; servers installed by the user or by an explicit `sy` prefetch command; `sy doctor` reports what is present.
- what we borrow: per-language initialization options and quirks from SolidLSP (MIT files; keep their SPDX headers and attribution) and multilspy (MIT).
- what we avoid: depending on serena-agent (GPL-3.0-or-later as a whole from 2.0.0); implicit language-server downloads (multilspy, SolidLSP) in zero-egress mode; pylspclient (no release since 2024-03-27); sansio-lsp-client's runtime pins on black and mypy.
- risks: SolidLSP is not published separately, so vendoring means tracking upstream fixes by hand; multilspy releases are sparse (0.0.15 on 2025-04-03, then 0.1.1 on 2026-10-13) and it is classified "Pre-Alpha"; Windows behavior of each server is untested by us (add to conformance tests).
- sources: https://pypi.org/pypi/pygls/json, https://raw.githubusercontent.com/openlawlibrary/pygls/main/pygls/client.py, https://pypi.org/pypi/lsprotocol/json, https://pypi.org/pypi/multilspy/json, https://raw.githubusercontent.com/microsoft/multilspy/main/README.md, https://raw.githubusercontent.com/microsoft/multilspy/main/LICENSE, https://pypi.org/pypi/serena-agent/json, https://raw.githubusercontent.com/oraios/serena/main/LICENSE, https://raw.githubusercontent.com/oraios/serena/main/README.md, https://oraios.github.io/serena/01-about/020_programming-languages.html, https://pypi.org/pypi/solidlsp/json, https://pypi.org/pypi/sansio-lsp-client/json, https://pypi.org/pypi/pylspclient/json

| Library | Version (date) | License | Python | How servers are launched | Runtime downloads |
|---|---|---|---|---|---|
| pygls | 2.1.1 (2026-03-25) | Apache-2.0 | >=3.9 | `JsonRPCClient.start_io(cmd, *args)` subprocess over stdio, or `start_ws` | none |
| lsprotocol | 2025.0.0 (2025-06-17) | MIT | >=3.8 | types and cattrs converters only | none |
| multilspy | 0.1.1 (2026-10-13) | MIT (LICENSE file; not in PyPI metadata) | >=3.10 | built-in configs for 12 languages; pins jedi-language-server 0.41.3 | yes: "Automatically handling the download of platform-specific server binaries" |
| SolidLSP (serena repo) | in serena-agent 2.0.0 (2026-10-13) | SolidLSP files MIT; serena-agent GPL-3.0-or-later as a whole; last all-MIT release v1.7.0 | serena-agent >=3.11,<3.15 | abstraction over 40+ language servers | yes, on first project activation; `serena download-ls-dependencies <langs>` prefetches |
| sansio-lsp-client | 0.13.0 (2025-03-01) | MIT | >=3.10 | sans-I/O; caller owns the process and pipes | none |
| pylspclient | 0.1.2 (2024-03-27) | not stated in PyPI metadata | >=3.10,<4.0 | not checked | not checked |

#### LLMLingua
- verified: yes (2026-10-16), basis: PyPI JSON; README and `llmlingua/prompt_compressor.py` on main; model-hub API for both LLMLingua-2 checkpoints.
- link: https://github.com/microsoft/LLMLingua
- license: MIT (package). llmlingua-2-xlm-roberta-large-meetingbank: MIT. llmlingua-2-bert-base-multilingual-cased-meetingbank: Apache-2.0.
- version: llmlingua 0.2.2 (released 2024-04-09); no later PyPI release; newest README news item dated 24/12/13.
- platforms: pure-Python wheel; runtime support follows torch and transformers (not checked).
- what we use: at most an optional extra, off by default, docs only: LLMLingua-2 small checkpoint on CPU, loaded from a local path; adopt only if the bench shows answer quality holds.
- what we borrow: token classification keep/drop; `force_tokens` (e.g. `\n`, `?`); structured spans (`<llmlingua, compress=False>`) to protect identifiers, numbers and code blocks.
- what we avoid: LLMLingua/LongLLMLingua causal-LM path (default `model_name` Llama-2-7b-hf, default `device_map="cuda"`; fp32 7B is about 28 GB, derived from 7B x 4 bytes); torch in core dependencies.
- risks: no release since 2024-04-09; dependencies transformers, accelerate, torch, tiktoken, nltk, numpy; constructor calls `tiktoken.encoding_for_model("gpt-3.5-turbo")` (BPE download on first use unless cached) and loads models with `from_pretrained` (hub download unless a local path); checkpoints trained on meeting transcripts (MeetingBank) may drop identifiers in technical docs.
- sources: https://pypi.org/pypi/llmlingua/json, https://raw.githubusercontent.com/microsoft/LLMLingua/main/README.md, https://raw.githubusercontent.com/microsoft/LLMLingua/main/llmlingua/prompt_compressor.py, https://huggingface.co/api/models/microsoft/llmlingua-2-xlm-roberta-large-meetingbank?blobs=true, https://huggingface.co/api/models/microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank?blobs=true

| Variant | Model | Size | License | CPU on T0 (8 GB) |
|---|---|---|---|---|
| LLMLingua, LongLLMLingua | causal LM; default Llama-2-7b-hf (README also shows phi-2) | 7B default | not checked | code accepts `device_map="cpu"` (float32); 7B not viable |
| LLMLingua-2 large | llmlingua-2-xlm-roberta-large-meetingbank | 558,945,282 params; 2,235.8 MB fp32 safetensors | MIT | heavy next to a T0 LLM |
| LLMLingua-2 small | llmlingua-2-bert-base-multilingual-cased-meetingbank | 177,341,186 params; 709.4 MB fp32 safetensors | Apache-2.0 | candidate; measure RAM and latency |

README claims (not reproduced): LLMLingua "up to 20x compression with minimal performance loss"; LongLLMLingua "improving RAG performance by up to 21.4% using only 1/4 of the tokens"; LLMLingua-2 "3x-6x faster" than LLMLingua.

#### simplicio
- verified: yes (2026-10-16), basis: PyPI JSON for simplicio-cli (current 0.18.16 and the 0.18.14 README), simplicio-loop README and `bench/llm_ab/README.md`. Claims not reproduced by us.
- link: https://github.com/simpletibr/simplicio-loop (package `packages/dev-cli`, PyPI `simplicio-cli`). No PyPI project named `simplicio` (404). Same-name repos seen in search results only, not fetched: https://github.com/simpletibr/simplicio, https://github.com/wesleysimplicio/simplicio, https://github.com/simpletibr/simplicio-27b
- license: MIT (PyPI classifier; repo README badge).
- version: simplicio-cli 0.18.16 (released 2026-09-28); first release 0.2.0 (2026-05-26); 47 releases.
- platforms: Windows native, macOS native, Linux native (py3-none-any, "OS Independent", Python >=3.10).
- technique: a one-line task is wrapped in a "six-layer contract" prompt; the CLI flags shown are goal, stack, target file, acceptance criteria and constraints, plus mapper context; the model must return DIFF + TEST + EVIDENCE blocks; then apply, run tests, retry on failure (up to 3x). Current 0.18.16 "never executes or provisions an LLM": the agent writes a find/replace plan (each `find` must match once), the CLI applies it, verifies, and prints one JSON receipt with sorted keys and a bounded output tail.
- what we use: nothing as a dependency.
- what we borrow: contract fields for small-model prompts (they overlap HANDOFF v0.1: goal, constraints, acceptance, files); exact-once find/replace edit plans; compact sorted-key receipts appended at the end to keep the cached prefix intact (TK1).
- what we avoid: regex-scored "adherence" as evidence of capability; claims about tiny models without execution scoring.
- risks: the project moved away from its numbers: the 0.18.14 README calls its model benchmarks "historical evidence only"; the simplicio-loop README "quotes no savings figure"; samples are small (10 cases; 4 tasks). Its own text: "simplicio multiplies capable models, it does not create capability in tiny ones."
- sources: https://pypi.org/pypi/simplicio/json, https://pypi.org/pypi/simplicio-cli/json, https://pypi.org/pypi/simplicio-cli/0.18.14/json, https://raw.githubusercontent.com/simpletibr/simplicio-loop/main/README.md, https://raw.githubusercontent.com/simpletibr/simplicio-loop/main/packages/dev-cli/README.md, https://raw.githubusercontent.com/simpletibr/simplicio-loop/main/bench/llm_ab/README.md

| Claim (0.18.14 README) | Scoring | Sample | Result (raw -> contract) |
|---|---|---|---|
| qwen2.5-coder:1.5b, local, 8 GB laptop | regex: target-file mention, DIFF block, TEST block, contract-state keywords | 10 cases, 30 runs/side, 156 checks (2026-05-27) | 32% -> 88% |
| 5 sub-4B models, average | same regex | 50 runs/side, 260 checks (2026-05-26) | 35% -> 74% (Llama 3.2 1B: 26% -> 40%) |
| Execution bench, 9 models | phpunit exit code, hidden tests, real PHP repo | 4 tasks, 36 runs/side (2026-05-28) | 33% -> 64%; Llama 3.2 1B 0% -> 0%; Gemma 3n E4B 0% -> 0%; Gemma 3 4B 0% -> 75% |
| Cost (frontier runs) | API usage field | 60 runs/side | +61% tokens/run, +24% wall-clock |
| Current evidence (`bench/llm_ab`) | harness-owned HTML checkers | one model via OpenCode; 1, 2 or 4 dependent tasks | README quotes no figure |

How to test it in our bench (proposal; sizes set in bench config):
- Arms: A raw task; B contract prompt (goal, target files, acceptance, constraints, repo-map slice, required diff + test); C contract rendered from HANDOFF v0.1 fields.
- Models: §5 T0 candidates plus one T1 control; identical sampling settings across arms.
- Tasks: edit tasks with hidden tests in `bench/suites`; primary score = patch applies and tests pass; secondary = valid-format rate, tokens in/out, wall-clock.
- Stats: paired per-task comparison with k samples and confidence intervals; register a `sy prove` claim only if B or C beats A.

### Testing and verification

#### pytest-testmon
- verified: yes (2026-10-16). Basis: PyPI JSON (latest and 2.2.0), testmon.org, the "Determining affected tests" and xdist posts, the repository commit log.
- link: https://testmon.org/
- license: MIT
- version: 2.2.0 (released 2025-12-01). Classifier: Development Status 4 - Beta. Requires Python >= 3.10, `pytest>=5,<10`, `coverage>=6,<8`.
- platforms: Windows native (OS classifier; an older undated post says "should work on Windows now"), macOS native (classifier), Linux native (POSIX classifier).
- pytest compatibility: 8.x and 9.x are within range. Commit "allow pytest 9" (2025-11-21) shipped in 2.2.0. Current pytest is 9.1.1 (2026-06-19) and coverage.py is 7.16.2 (2026-09-27); both are inside testmon's pins.
- How it selects tests:
  - It records which code each test executes, using coverage.py. Source is split into function-level blocks, where nested blocks become placeholders. It stores checksums plus line ranges, not the code ("doesn't store the whole code of the block but just a checksum").
  - A test is re-run when a block it executed changes. Failed tests are always re-run.
  - It also tracks environment variables, the Python version and third-party package versions.
  - It does not track static files ("txt, xml, other project assets") or network services. Its reliability is "pretty much the same as limits of coverage.py".
- Data file: `.testmondata` in the pytest rootdir. Relocate it with the `TESTMON_DATAFILE` env var, and split variants with `--testmon-env` or `run_variant_expression`.
- Options: `--testmon`, `--testmon-noselect` (reorder only), `--testmon-nocollect`, `--testmon-forceselect`, `--no-testmon`, `--testmon-env`, `--tmnet` (cloud storage instead of the local file).
- pytest-xdist: supported since 1.4.0. 2.2.0 passes `exec_id` to xdist workers (commit 2025-11-21). pytest-xdist is at 3.8.0 (2025-07-01).
- Alternatives:

| Tool | Version (date) | License | Method | Fit for §8.2 |
|---|---|---|---|---|
| coverage.py dynamic contexts (`dynamic_context = "test_function"`; pytest-cov `--cov-context`) | 7.16.2 (2026-09-27) | Apache-2.0 | Per-test contexts in the `.coverage` SQLite file; we would build our own file-to-tests map | Do-it-yourself, line-level; no checksum invalidation |
| pytest-impacted | 0.35.0 (2026-10-01) | MIT | "git diff, AST parsing, and dependency graph analysis"; optional `fast` extra (pytest-impacted-rs) | Static, no tracing cost, module granularity; needs Python >= 3.11 and pytest >= 8 |
| pytest-picked | 0.5.1 (2024-11-06) | MIT | "Run the tests related to the changed files" | Coarse; last release 2024 |
| pytest-incremental | 0.6.0 (2021-04-24) | MIT | "analyses your project structure and file modifications between test-runs" | Stale |

- Recommendation for SPEC §8.2:
  - What we use: pytest-testmon as the default impacted-test selector.
  - Build a baseline `.testmondata` when the worktree pool is created. Point each worktree at its own copy with `TESTMON_DATAFILE`. Whether copied data stays valid at the same base commit is not verified, so test it in a spike.
  - Force a full run (or the relevant directory) whenever a non-`.py` file changes: data, configs, `science.toml`, `tests/golden/**`, the lockfile.
  - The final gate still runs the full suite.
  - Fallback: pytest-impacted (static) if coverage tracing is too slow or testmon stalls.
- avoid: `--tmnet`, because it sends test data to a cloud service and conflicts with §15.
- risks:
  - No commits since 2025-12-01, about 10.5 months as of today.
  - Beta classifier.
  - The coverage.py upper pin (<8).
  - Non-Python inputs are blind spots.
- sources: https://pypi.org/pypi/pytest-testmon/json ; https://pypi.org/pypi/pytest-testmon/2.2.0/json ; https://testmon.org/ ; https://testmon.org/blog/determining-affected-tests/ ; https://testmon.org/blog/v14-with-xdist-support-is-out/ ; https://github.com/tarpas/pytest-testmon ; https://github.com/tarpas/pytest-testmon/commits/main/ ; https://pypi.org/pypi/pytest/json ; https://pypi.org/pypi/pytest/9.1.1/json ; https://pypi.org/pypi/coverage/json ; https://pypi.org/pypi/coverage/7.16.2/json ; https://coverage.readthedocs.io/en/latest/contexts.html ; https://pypi.org/pypi/pytest-xdist/json ; https://pypi.org/pypi/pytest-impacted/json ; https://pypi.org/pypi/pytest-picked/json ; https://pypi.org/pypi/pytest-incremental/json

#### Hypothesis
- verified: yes (2026-10-16). Basis: PyPI JSON (latest and 6.170.0) and the official changelog.
- link: https://hypothesis.readthedocs.io
- license: MPL-2.0
- version: 6.170.0 (released 2026-10-15). Requires Python >= 3.11; classifiers cover CPython 3.11-3.14 and PyPy.
- platforms: Windows native (win_amd64, win_arm64 and win32 wheels), macOS native (x86_64, arm64), Linux native (manylinux and musllinux for x86_64, aarch64, armv7l, ppc64le, s390x, riscv64, i686). There is also a pyemscripten wasm32 wheel and free-threaded cp314t wheels.
- Notable 2026 changes:

| Version (date) | Change |
|---|---|
| 6.156.0 (2026-07-01) | "start of our migration of Hypothesis internals from Python to Rust"; compiled and abi3 wheels follow (6.156.4 to 6.168.1) |
| 6.169.0 (2026-10-11) | "drops support for Python 3.10" |
| 6.169.4 (2026-10-15) | Fixes an error when applying `@given` on Python 3.14 |
| 6.152.0 (2026-04-14) | Auto-creates a `.gitignore` with `*` in the `.hypothesis` directory |
| 6.152.9 (2026-05-19) | Smooth distribution for `st.integers()` and `st.integers(a, b)` |
| 6.158.1 (2026-07-21) | Contradictory filters give an empty strategy instead of failing health checks |
| 6.160.0 / 6.161.0 / 6.167.0 (2026-07/08) | `Bundle` gains `.filter()` / `.map()`; `note()` values appear in observability reports; observations give more detail on failures |
| 6.155.0 (2026-05-28) | `from_dtype()` supports the variable-width string dtype (numpy extra) |

- What we use for invariant tests (§6.2): `@given` templates for normalization, bounds, monotonicity and symmetry, with numpy inputs via the `numpy` extra.
- What we use for C-003 (switching rule):
  - Draw gain, cost and margin as `st.integers()` in fixed-point units, which avoids float ties.
  - Assert `switch == (gain - cost > margin)`, and pin the equality boundary (no switch) as explicit examples.
  - Use `Bundle`-based stateful tests for multi-step trajectories.
  - Attach observability output to the evidence JSON. How to enable it was not checked today.
- risks:
  - Native wheels are new (since July 2026). Platforms without a wheel fall back to the sdist, whose build needs were not checked.
  - The `.hypothesis/` example database affects reproducibility. Decide whether CI caches it or disables it; setting names were not checked today.
- sources: https://pypi.org/pypi/hypothesis/json ; https://pypi.org/pypi/hypothesis/6.170.0/json ; https://hypothesis.readthedocs.io/en/latest/changelog.html

#### jupytext
- verified: yes (2026-10-16). Basis: PyPI JSON (latest and 1.19.6), the repository README and the docs sources (cli, paired-notebooks, config).
- link: https://jupytext.org
- license: MIT
- version: 1.19.6 (released 2026-10-04). Requires Python >= 3.9. Dependencies: markdown-it-py, mdit-py-plugins, nbformat, packaging, pyyaml.
- platforms: Windows native, macOS native, Linux native (pure-Python `py3-none-any` wheel).
- Pairing:
  - Per notebook: `jupytext --set-formats ipynb,py:percent notebook.ipynb`.
  - Global: `jupytext.toml` containing `formats = "ipynb,py:percent"`, or the same key under `[tool.jupytext]` in `pyproject.toml`. "The individual pairing takes precedence over the global pairing."
- Sync rules:
  - `jupytext --sync notebook.ipynb` updates "whichever of notebook.ipynb/notebook.py is outdated", based on timestamps.
  - Inputs load "from the most recent file in the pair"; outputs load "from the `.ipynb` file".
  - `--update` updates "the input cells in the .ipynb file and preserve[s] outputs and metadata".
- Other CLI: `--to`, `--pipe black`, and `--check 'pytest {}' notebook.ipynb`, which exports to a temporary py:percent file and runs pytest.
- What we use:
  - Commit the global `jupytext.toml`.
  - Agents edit only the `.py`.
  - The gate then runs `jupytext --to ipynb --update notebook.py`. This makes the direction explicit and keeps outputs.
  - Use `--check` to run the science tests on notebooks.
- avoid: relying on timestamp-based `--sync` in freshly checked-out worktrees. That is a risk we inferred, not verified: both files can carry new mtimes after checkout.
- sources: https://pypi.org/pypi/jupytext/json ; https://pypi.org/pypi/jupytext/1.19.6/json ; https://github.com/jupytext/jupytext ; https://raw.githubusercontent.com/jupytext/jupytext/main/website/src/content/docs/using/cli.md ; https://raw.githubusercontent.com/jupytext/jupytext/main/website/src/content/docs/using/paired-notebooks.md ; https://raw.githubusercontent.com/jupytext/jupytext/main/website/src/content/docs/using/config.md

#### Playwright
- verified: yes (2026-10-16). Basis: PyPI JSON, the npm registry (`time` field), the Python release notes, the docs (intro, browsers, test-runners, PageAssertions), the playwright-core 1.64.1 package source (download URLs, cache dirs), and CDN HEAD `Content-Length` today.
- link: https://playwright.dev/python/
- license: Apache-2.0
- version:
  - Python `playwright` 1.64.0 (released 2026-10-13). requires_python >= 3.10; the intro page still says "Python 3.8 or higher", so trust PyPI.
  - npm `playwright` 1.64.1 (2026-10-14); 1.64.0 was 2026-10-07; engines `node >=20`.
  - `pytest-playwright` 0.10.0 (2026-10-08), Apache-2.0, `pytest>=7,<10`.
  - Bundled browsers: Chromium 156.0.8078.4 (Chrome for Testing builds, revision 1248), Firefox 157.0 (revision 1555), WebKit 27.2 (revision 2370).
- platforms:
  - Windows native: wheels win32 / win_amd64 / win_arm64. Docs: "Windows 11+, Windows Server 2019+ or Windows Subsystem for Linux (WSL)".
  - macOS 14 or later: wheels x86_64, arm64 and universal2.
  - Linux: "Debian 12 / 13, Ubuntu 22.04 / 24.04 / 26.04 (x86-64 or arm64)".
  - Python wheel sizes: 38.7 MB (win_amd64), 48.3 MB (manylinux x86_64), 43.0 MB (macOS arm64).
- Browser download sizes. These are compressed archives at the exact installer URLs (Chromium: `https://cdn.playwright.dev/builds/cft/156.0.8078.4/...`). Firefox and WebKit file names were inferred and returned HTTP 200 on the CDN.

| Archive | Windows x64 | Linux x64 | macOS arm64 |
|---|---|---|---|
| chromium (full) | 207.1 MB | 200.3 MB | 194.8 MB |
| chromium-headless-shell | 123.8 MB | 125.8 MB | 103.8 MB |
| firefox | 129.8 MB | 116.4 MB (ubuntu-24.04 build) | not checked |
| webkit | 63.1 MB | 108.1 MB (ubuntu-24.04 build) | not checked |
| ffmpeg | 1.7 MB | 2.3 MB | not checked |

  The default `playwright install` fetches all `installByDefault` entries: about 526 MB (Windows) or 553 MB (Linux), compressed.
- Cache: `%LOCALAPPDATA%\ms-playwright` (Windows), `~/Library/Caches/ms-playwright` (macOS), `$XDG_CACHE_HOME/ms-playwright`, else `~/.cache/ms-playwright` (Linux). Override with `PLAYWRIGHT_BROWSERS_PATH`.
- Headless smoke test of the Vite-built dashboard served by FastAPI:
  1. Build the dashboard with the Vite build script.
  2. Mount `StaticFiles(directory=<build dir>, html=True)`. `html` mode "automatically loads index.html for directories" and serves `404.html` if present (signature from the Starlette docs).
  3. A session fixture starts the app on 127.0.0.1:<free port>.
  4. Run `pytest --base-url http://127.0.0.1:<port> --browser chromium --tracing retain-on-failure --screenshot only-on-failure`. Tests are headless by default; the `base-url` extra installs pytest-base-url.
  5. Assert with `expect(page).to_have_title(...)` and `to_match_aria_snapshot(...)`.
  6. In CI, run `playwright install --only-shell chromium`, which skips the full browser (the two flags are documented separately; the combination was not run). On Linux, add OS packages with `playwright install --with-deps chromium`. Cache the `ms-playwright` directory.
  7. Parallelize with pytest-xdist `--numprocesses auto` if needed.
- avoid:
  - Expecting a Python screenshot-diff assertion: PageAssertions has only `to_have_title`, `to_have_url` and `to_match_aria_snapshot`.
  - Running the browser inside the C-006 audit-hook flow: the browser is a native child process that hooks cannot see.
- risks:
  - 1.64 breaking change: "Elements inside hidden iframes are considered hidden".
  - The browser download adds about 104-126 MB per OS to `sy prove --quick` unless cached.
- sources: https://pypi.org/pypi/playwright/json ; https://pypi.org/pypi/playwright/1.64.0/json ; https://registry.npmjs.org/playwright/latest ; https://registry.npmjs.org/playwright ; https://registry.npmjs.org/playwright-core/-/playwright-core-1.64.1.tgz ; https://unpkg.com/playwright-core@1.64.1/browsers.json ; https://github.com/microsoft/playwright-python/releases/tag/v1.64.0 ; https://playwright.dev/python/docs/intro ; https://playwright.dev/python/docs/browsers ; https://playwright.dev/python/docs/test-runners ; https://playwright.dev/python/docs/api/class-pageassertions ; https://pypi.org/pypi/pytest-playwright/json ; https://starlette.dev/staticfiles/ ; https://cdn.playwright.dev/builds/cft/156.0.8078.4/win64/chrome-win64.zip ; https://cdn.playwright.dev/builds/cft/156.0.8078.4/win64/chrome-headless-shell-win64.zip ; https://cdn.playwright.dev/builds/cft/156.0.8078.4/linux64/chrome-linux64.zip ; https://cdn.playwright.dev/builds/cft/156.0.8078.4/linux64/chrome-headless-shell-linux64.zip ; https://cdn.playwright.dev/builds/cft/156.0.8078.4/mac-arm64/chrome-mac-arm64.zip ; https://cdn.playwright.dev/builds/cft/156.0.8078.4/mac-arm64/chrome-headless-shell-mac-arm64.zip ; https://cdn.playwright.dev/dbazure/download/playwright/builds/firefox/1555/firefox-win64.zip ; https://cdn.playwright.dev/dbazure/download/playwright/builds/firefox/1555/firefox-ubuntu-24.04.zip ; https://cdn.playwright.dev/dbazure/download/playwright/builds/webkit/2370/webkit-win64.zip ; https://cdn.playwright.dev/dbazure/download/playwright/builds/webkit/2370/webkit-ubuntu-24.04.zip ; https://cdn.playwright.dev/dbazure/download/playwright/builds/ffmpeg/1013/ffmpeg-win64.zip ; https://cdn.playwright.dev/dbazure/download/playwright/builds/ffmpeg/1013/ffmpeg-linux.zip

#### CI network isolation
- verified: no (2026-10-16), partial.
  - Verified: the Linux mechanisms, Python guards, hosted runner labels, privileges, and where Docker is available.
  - Not verified: macOS and Windows OS-level enforcement on hosted runners (`sandbox-exec`, pf, the firewall's DNS behaviour, Windows `none` networking, AppContainer). That needs a CI spike.
  - Basis: the Python docs (3.15.0 and 3.11.17), the code host's runner reference and the runner-images readmes and scripts, the Docker docs, the Ubuntu 24.04 release notes, man pages, PowerShell and Windows Sandbox docs.
- link: https://docs.python.org/3/library/audit_events.html
- license: n/a (pytest-socket MIT; bubblewrap LGPL-2.1-or-later per its 0.12.0 notes)
- version: n/a. pytest-socket 0.8.1 (released 2026-08-19; Python >= 3.10, pytest >= 7). bubblewrap 0.13.1 (2026-10-14; the release page omits the current year). util-linux man page 2.43.devel.
- platforms: Windows partial (recorder only verified), macOS partial (recorder only verified), Linux native (OS-level isolation plus recorder).
- Hosted runner images today (labels; public-repo specs):

| OS | Labels | Arch / specs |
|---|---|---|
| Linux | `ubuntu-latest` = `ubuntu-24.04` (image 20261004.327.1), `ubuntu-26.04`, `ubuntu-22.04`; `ubuntu-24.04-arm`, `ubuntu-26.04-arm`, `ubuntu-22.04-arm`; `ubuntu-slim` | x64 and arm64: 4 vCPU / 16 GB. `ubuntu-slim`: 1 vCPU / 5 GB, unprivileged container |
| macOS | `macos-latest` = `macos-26`, `macos-15`, `macos-14` (deprecated); x64: two vendor-suffixed labels (26 and 15), `macos-latest-large`, `macos-26-large`, `macos-15-large`, `macos-14-large` (deprecated); `xcode-27` (preview). The `-large` / `-xlarge` labels are larger runners | arm64: 3 vCPU / 7 GB, no nested virtualization. x64: 4 vCPU / 14 GB |
| Windows | `windows-latest` = `windows-2025` = `windows-2025-vs2026` (Windows Server 2025, image 20260925.250.1); `windows-2022`; `windows-11-arm`, `windows-11-vs2026-arm` | x64 and arm64: 4 vCPU / 16 GB. Runs as administrator with UAC disabled |

- Python audit events. Confirmed identical in the 3.11.17 and 3.15.0 docs:
  - `socket.__new__`, `socket.bind`, `socket.connect`, `socket.getaddrinfo`, `socket.gethostbyaddr`, `socket.gethostbyname`, `socket.gethostname`, `socket.getnameinfo`, `socket.getservbyname`, `socket.getservbyport`, `socket.sendmsg`, `socket.sendto`, `socket.sethostname`.
  - Also relevant: `http.client.connect`, `urllib.Request`, `subprocess.Popen`, `os.posix_spawn`, `os.exec`, `os.system`, `_winapi.CreateProcess` (internal), `ctypes.dlopen`, `ctypes.call_function`.
  - There are no `ssl.*` events.
  - Hooks can "raise an exception to abort the operation".
  - Limits: the docs say hooks "are not suitable for implementing a 'sandbox'" and that security-sensitive hooks need `PySys_AddAuditHook()` before runtime init. Hooks are per process, so child processes and native code that bypasses the socket module are not covered.
- Recommended C-006 / C-007 harness:
  - Recorder (all three OS): installed in every Python process `sy` starts. It logs `socket.connect`, `socket.sendto`, `socket.sendmsg`, `socket.bind` and the four lookup events. In offline mode it raises on a non-local address. It also logs the process-spawn events, so children the hook cannot see are listed in evidence.
  - OS enforcement (Linux only): run the same flow inside `docker run --network none` or `sudo unshare --net`, with the canary first.
  - Evidence: the recorder log and the `sy audit` log, compared as sets, plus the runner label, image version and mechanism.
  - "Local" means 127.0.0.0/8, `::1` and Unix sockets. CI binds 127.0.0.1, because `none` containers have no IPv6 loopback.
- pytest-socket: "Disables all network calls flowing through Python's `socket` interface, including DNS resolution". `--allow-hosts` takes hostnames, IPs or CIDR ranges. `--force-enable-socket` overrides. Use it as the unit-test-level guard; it does not replace the recorder.
- C-001 container proof (Linux only):
  - Run `docker run --rm --memory=8g --memory-swap=8g --network none <image> sy run <toy task>`. Docs: if `--memory-swap` equals `--memory`, "the container doesn't have access to swap".
  - Start it without `--gpus`. Docs: "Include the `--gpus` flag when you start a container to access GPU resources".
  - Evidence:
    - The cgroup v2 files `memory.max` ("hard limit"), `memory.swap.max`, `memory.peak` and `memory.events` (`oom_kill`). The mount path inside the container was not verified.
    - The absence of GPU device nodes and of `nvidia-smi`, which the GPU `utility` capability would add.
    - The engine's reported backend.
  - Hosted standard runners list no GPU; GPU runners are a larger-runner option on paid plans.
  - Limits:
    - macOS images have no Docker, and arm64 VMs have no nested virtualization.
    - Windows images run Windows containers only; container jobs and service containers need Linux.
    - Windows Sandbox is not on Windows Server images.
    - So C-001 can only be proven on Linux. A macOS arm64 runner has 7 GB RAM in total, but GPU visibility there is not controlled.
- risks:
  - Changing `apparmor_restrict_unprivileged_userns` weakens the runner VM. Prefer `sudo unshare` or Docker.
  - macOS and Windows OS-level isolation is unproven on hosted runners.
  - A per-program firewall rule misses child executables.
- sources: https://docs.python.org/3/library/audit_events.html ; https://docs.python.org/3.11/library/audit_events.html ; https://docs.python.org/3/library/sys.html ; https://pypi.org/pypi/pytest-socket/json ; https://github.com/miketheman/pytest-socket ; https://docs.github.com/en/actions/reference/runners/github-hosted-runners ; https://github.com/actions/runner-images ; https://raw.githubusercontent.com/actions/runner-images/main/images/ubuntu/Ubuntu2404-Readme.md ; https://raw.githubusercontent.com/actions/runner-images/main/images/ubuntu/Ubuntu2604-Readme.md ; https://raw.githubusercontent.com/actions/runner-images/main/images/ubuntu-slim/ubuntu-slim-Readme.md ; https://github.com/actions/runner-images/blob/main/images/macos/macos-15-arm64-Readme.md ; https://raw.githubusercontent.com/actions/runner-images/main/images/macos/macos-26-arm64-Readme.md ; https://raw.githubusercontent.com/actions/runner-images/main/images/macos/macos-15-Readme.md ; https://github.com/actions/runner-images/blob/main/images/windows/Windows2025-VS2026-Readme.md ; https://raw.githubusercontent.com/actions/runner-images/main/images/windows/Windows2022-Readme.md ; https://raw.githubusercontent.com/actions/runner-images/main/images/windows/Windows11-Arm64-Readme.md ; https://github.com/actions/runner-images/tree/main/images/windows/scripts/build ; https://raw.githubusercontent.com/actions/runner-images/main/images/windows/toolsets/toolset-2025.json ; https://github.com/actions/runner-images/issues/10443 ; https://github.com/opaque-dev/opaque/issues/147 ; https://documentation.ubuntu.com/release-notes/24.04/ ; https://man7.org/linux/man-pages/man1/unshare.1.html ; https://github.com/containers/bubblewrap/releases ; https://docs.docker.com/engine/network/drivers/none/ ; https://docs.docker.com/engine/containers/resource_constraints/ ; https://docs.docker.com/engine/containers/gpu/ ; https://docs.kernel.org/admin-guide/cgroup-v2.html ; https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers ; https://manp.gs/mac/1/sandbox-exec ; https://manp.gs/mac/5/pf.conf ; https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule ; https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-install ; https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file ; https://learn.microsoft.com/en-us/virtualization/windowscontainers/container-networking/network-drivers-topologies ; https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation

### Name and stack

#### name: Switchyard
- verified: yes (2026-10-16). Basis: PyPI JSON API, npm registry and downloads API, code-host search API, Debian and Ubuntu contents search, Homebrew API, crates.io API, the Windows command reference, a PATH check on the local Windows machine, and web searches. macOS built-in commands could not be checked against a primary source.
- PyPI: `switchyard` 1.0.1 was uploaded 2020-07-03. It has 18 releases since 2017-01-02, the summary "Switchyard is a framework for creating networked systems", requires-python >=3.6, a GPLv3 classifier and a CC BY-NC-SA 4.0 license field. Its source repo was last pushed 2024-02-20. The quickstart command `uv tool install switchyard` would resolve to this project.
- The large same-name project is an open-source library that helps an agent choose which model handles each request, with routing that "can change as the agent continues its work". It is pre-1.0 (components rated Beta, Alpha and Demo), Apache-2.0, offered through a hosted LLM gateway and gateway plugins, and ships integration guides for Pi and Oh My Pi, which overlaps SPEC §12 workers. Its embeddable routing-library component is named `libsy`. Name confusion is near-certain, and the overlap matters for §1 positioning.
- npm `switchyard-ai` (2026-09-25) installs a `sy` binary for the same purpose, a router for AI coding agents.
- Java SwitchYard: 6 of the 7 repos shown are archived and marked deprecated (last pushes 2016 to 2023). A learning-materials repo was last pushed in 2014.
- `sy` collisions at the OS level: none found on Debian, Ubuntu, Homebrew or Windows. Package-level collisions: crates.io `sy` (file sync tool, bin `sy`, 0.4.0, updated 2026-06-23), npm `switchyard-ai` (bin `sy`), npm `sy` 0.1.0 (2015, no bin), PyPI `sy` 0.3 (2011).
- Risks if the name is kept: users installing the wrong package, search confusion with an active same-domain project, and a CLI clash with `switchyard-ai`.
- sources: https://pypi.org/pypi/switchyard/json, https://pypi.org/pypi/Switchyard/json, https://pypi.org/pypi/switch-yard/json, https://pypi.org/pypi/switch_yard/json, https://pypi.org/pypi/switchyard-ai/json, https://pypi.org/pypi/sy/json, https://registry.npmjs.org/switchyard, https://registry.npmjs.org/switchyard-ai, https://api.npmjs.org/downloads/point/last-month/switchyard-ai, https://registry.npmjs.org/sy, https://api.github.com/search/repositories?q=switchyard+in%3Aname&sort=stars&order=desc&per_page=12, https://api.github.com/search/repositories?q=switchyard+llm+OR+switchyard+agent+OR+switchyard+model&sort=stars&order=desc&per_page=12, https://raw.githubusercontent.com/NVIDIA-NeMo/Switchyard/main/README.md, https://crates.io/api/v1/crates/switchyard, https://crates.io/api/v1/crates/sy, https://formulae.brew.sh/api/formula/sy.json, https://formulae.brew.sh/api/cask/sy.json, https://formulae.brew.sh/api/formula/switchyard.json, https://packages.debian.org/search?searchon=contents&keywords=sy&mode=exactfilename&suite=stable&arch=any, https://packages.ubuntu.com/search?searchon=contents&keywords=sy&mode=exactfilename&suite=noble&arch=any, https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/windows-commands
- alternative-name sources: https://pypi.org/pypi/stepswitch/json, https://pypi.org/pypi/step-switch/json, https://pypi.org/pypi/shuntyard/json, https://pypi.org/pypi/shunt-yard/json, https://pypi.org/pypi/signalyard/json, https://pypi.org/pypi/signal-yard/json, https://pypi.org/pypi/stepyard/json, https://pypi.org/pypi/yardmaster/json, https://pypi.org/pypi/ssw/json, https://registry.npmjs.org/stepswitch, https://registry.npmjs.org/shuntyard, https://registry.npmjs.org/signalyard, https://registry.npmjs.org/ssw, https://api.github.com/search/repositories?q=stepswitch+in%3Aname&sort=stars&order=desc&per_page=4, https://api.github.com/search/repositories?q=shuntyard+in%3Aname&sort=stars&order=desc&per_page=4, https://api.github.com/search/repositories?q=signalyard+in%3Aname&sort=stars&order=desc&per_page=4, https://api.github.com/search/repositories?q=modelyard+in%3Aname&sort=stars&order=desc&per_page=4, https://packages.debian.org/search?searchon=contents&keywords=ssw&mode=exactfilename&suite=stable&arch=any, https://packages.debian.org/search?searchon=contents&keywords=stepswitch&mode=exactfilename&suite=stable&arch=any, https://packages.debian.org/search?searchon=contents&keywords=shuntyard&mode=exactfilename&suite=stable&arch=any, https://packages.debian.org/search?searchon=contents&keywords=signalyard&mode=exactfilename&suite=stable&arch=any, https://formulae.brew.sh/api/formula/ssw.json, https://formulae.brew.sh/api/formula/stepswitch.json, https://crates.io/api/v1/crates/ssw

#### uv
- verified: yes (2026-10-16). Basis: PyPI JSON, uv docs source, uv changelog.
- Version: 0.13.1 (2026-10-14), license MIT OR Apache-2.0. `uv_build` is released in lockstep (0.13.1).
- 0.13.0 (2026-10-09) made Python 3.15 the default when uv downloads an interpreter. `uv tool install` on a machine without a suitable Python will therefore run the tool on 3.15, so every runtime dependency must install on 3.15.
- `uv tool install`: installs into an isolated virtual environment, puts executables on PATH, and installs the latest version unless one is requested.
- Dependency groups: PEP 735 `[dependency-groups]`, with `--group`, `--only-group`, `--all-groups` and `--no-default-groups`. The `dev` group syncs by default.
- Build backend: `uv init` templates use `uv_build>=0.13.1,<0.14`. `uv_build` supports pure Python only and has no build scripts; the docs suggest hatchling when scripts are needed. Wheels include only the module under `module-root` plus data directories.
- Minimum pins: the uv docs recommend a CI run with `--resolution lowest-direct` to test declared lower bounds.
- sources: https://pypi.org/pypi/uv/json, https://pypi.org/pypi/uv-build/json, https://pypi.org/pypi/hatchling/json, https://raw.githubusercontent.com/astral-sh/uv/main/CHANGELOG.md, https://raw.githubusercontent.com/astral-sh/uv/main/docs/concepts/build-backend.md, https://raw.githubusercontent.com/astral-sh/uv/main/docs/concepts/tools.md, https://raw.githubusercontent.com/astral-sh/uv/main/docs/concepts/projects/dependencies.md, https://raw.githubusercontent.com/astral-sh/uv/main/docs/concepts/projects/init.md, https://raw.githubusercontent.com/astral-sh/uv/main/docs/concepts/resolution.md

#### Typer
- verified: yes (2026-10-16). Basis: PyPI JSON and the release notes.
- Version: 0.27.3 (2026-10-06), MIT, requires-python >=3.10, Beta classifier.
- Runtime dependencies are rich, shellingham, annotated-doc, and colorama on Windows; Click is not a dependency.
- Release history:

  | Version (date) | Change |
  |---\|---|
  | 0.21.0 (2025-12-25) | Dropped Python 3.8 |
  | 0.24.0 (2026-02-16) | Dropped Python 3.9 |
  | 0.24.1 (2026-02-21) | `typer-slim` and `typer-cli` discontinued (`typer-slim` 0.24.0 is the last release) |
  | 0.26.0 (2026-05-26) | Vendored Click; Click-specific functionality is unsupported |
  | 0.27.0 (2026-07-15) | Changed metavar printing |

- Risk: 0.x minor releases contain breaking changes. Test against a locked version.
- sources: https://pypi.org/pypi/typer/json, https://pypi.org/pypi/typer-slim/json, https://typer.tiangolo.com/release-notes/

#### FastAPI
- verified: yes (2026-10-16). Basis: PyPI JSON and the FastAPI docs.
- Version: 0.143.2 (2026-10-15), MIT, requires-python >=3.10, Beta classifier.
- Dependencies include `starlette>=0.46.0`, `pydantic>=2.9.0` and `opentelemetry-api>=1.44.0`. Starlette 1.8.0 (2026-10-13) requires Python >=3.11.
- SSE has been built in since 0.135.0 (released 2026-03-01): `from fastapi.sse import EventSourceResponse, ServerSentEvent`. It sends a keep-alive ping every 15 s, sets `Cache-Control: no-cache` and `X-Accel-Buffering: no`, and supports resume through `Last-Event-ID`. The page does not describe client-disconnect handling, so test it.
- sse-starlette 3.5.0 (2026-09-28, BSD-3-Clause) remains an alternative but is not needed.
- Serve with plain `uvicorn` 0.54.0. Avoid `uvicorn[standard]`, which pulls PyYAML and compiled extras.
- sources: https://pypi.org/pypi/fastapi/json, https://pypi.org/pypi/starlette/json, https://pypi.org/pypi/uvicorn/json, https://pypi.org/pypi/sse-starlette/json, https://fastapi.tiangolo.com/tutorial/server-sent-events/

#### Pydantic
- verified: yes (2026-10-16). Basis: PyPI JSON.
- Version: 2.14.1 (2026-10-11), with 2.14.0 on 2026-10-08. MIT, requires-python >=3.10, Production/Stable, classifiers through 3.15.
- It pins `pydantic-core==2.50.1`, which has cp310 through cp315 wheels, including Windows cp315. No 3.x release or pre-release exists.
- sources: https://pypi.org/pypi/pydantic/json, https://pypi.org/pypi/pydantic-core/2.50.1/json

#### httpx
- verified: yes (2026-10-16). Basis: PyPI JSON, including the 1.0.dev6 release JSON, and the repository page.
- Stable: 0.28.1 (2024-12-06), BSD-3-Clause, requires-python >=3.8, Beta classifier. Dependencies: anyio, certifi, `httpcore==1.*` and idna. httpcore's latest release is 1.0.9 (2025-04-24).
- 1.0 status: pre-releases 1.0.dev1 (2025-07-02) through 1.0.dev6 (2026-08-31). The 1.0.dev6 page describes a redesigned "HTTP toolkit" with both client and server, requires-python >=3.10 and only `truststore` and `typing-extensions` as dependencies. Its page does not mention async support or a migration path.
- Repository: 15.6k stars, not archived, and only collaborators can open issues.
- Maintenance concern: no stable release in 22 months, and the 1.0 API differs from 0.28.
- Recommendation: pin `httpx>=0.28.1,<1` and keep all HTTP calls in one internal client module so the library can be swapped. The 1.0.dev page installs it with `uv add httpx --pre`, so pre-releases need an explicit opt-in.
- Alternatives (PyPI): aiohttp 3.14.5 (2026-10-11, Apache-2.0 AND MIT, >=3.10, Production/Stable); niquests 3.21.2 (2026-09-23, Apache-2.0, >=3.7, Production/Stable).
- sources: https://pypi.org/pypi/httpx/json, https://pypi.org/pypi/httpx/1.0.dev6/json, https://pypi.org/pypi/httpcore/json, https://github.com/encode/httpx, https://pypi.org/pypi/aiohttp/json, https://pypi.org/pypi/niquests/json

#### SQLite (WAL)
- verified: yes (2026-10-16). Basis: sqlite.org pages, the python-build-standalone build pins, and local interpreters.
- Latest: 3.54.0 (2026-10-09). The license is public domain.
- WAL caveats (wal.html):
  - All processes must be on the same host. WAL does not work over a network filesystem, which includes SMB shares and mapped network drives on Windows.
  - Opening the database needs write access to the `-shm` file or its directory. Since 3.22.0 there are read-only exceptions.
  - It creates extra `-wal` and `-shm` files.
  - Checkpoint starvation: with always-overlapping readers, the WAL file grows without bound. Large write transactions also grow it.
  - `page_size` cannot change while in WAL mode.
  - Commits across ATTACHed databases are not atomic as a set.
  - The page lists no Windows-specific disadvantage.
- WAL-reset bug: a rare corruption bug in 3.7.0 through 3.51.2. It needs two connections writing or checkpointing at the same instant. Fixed in 3.51.3 (2026-03-13), with backports in 3.44.6 and 3.50.7.
- This workstation: Python 3.11.1 links SQLite 3.39.4 and 3.12.1 links 3.43.1, so both are affected. python-build-standalone, the source of uv-managed Pythons, pins SQLite 3.53.1 on its main branch today. Python security branches get source-only releases, so python.org installers for those branches are not refreshed.
- Recommendations:
  - `sy doctor` reports `sqlite3.sqlite_version` and warns below 3.51.3 (except 3.44.6+ in the 3.44 line and 3.50.7+ in the 3.50 line).
  - Detect network volumes and use `journal_mode=DELETE` there.
  - Keep transactions small and run `PRAGMA wal_checkpoint(TRUNCATE)` periodically.
- sources: https://www.sqlite.org/wal.html, https://www.sqlite.org/chronology.html, https://www.sqlite.org/copyright.html, https://raw.githubusercontent.com/astral-sh/python-build-standalone/main/pythonbuild/downloads.json, https://devguide.python.org/developer-workflow/development-cycle/

#### React
- verified: yes (2026-10-16). Basis: npm registry.
- Version: 19.3.1 (2026-10-14), with 19.3.0 on 2026-09-09. MIT, engines node >=0.10.0. Canary 19.3.0 builds are tagged `next` and `canary`.
- Recommendation: `react@^19.3`. No concerns found.
- sources: https://registry.npmjs.org/react

#### Vite
- verified: yes (2026-10-16). Basis: npm registry and the Node release schedule.
- Version: 8.3.4 (2026-10-08), MIT, engines `^20.19.0` or `>=22.12.0`. 8.0.0 was released 2026-03-12. Previous-line backports: 7.3.7 and 6.4.4 (2026-10-06).
- `@vitejs/plugin-react` 6.1.2 (2026-10-05) requires vite ^8.0.0 and has the same Node range.
- Node 20.12.1 on this workstation does not satisfy the engines range, and Node 20 reached EOL on 2026-04-30. Upgrade to Node 24 LTS (maintenance from 2026-10-20, EOL 2028-04-30) or Node 22 >=22.12 (EOL 2027-04-30). Node 26 becomes LTS on 2026-10-28.
- sources: https://registry.npmjs.org/vite, https://registry.npmjs.org/@vitejs/plugin-react, https://raw.githubusercontent.com/nodejs/Release/main/schedule.json

#### TypeScript
- verified: yes (2026-10-16). Basis: npm registry and the release announcement.
- Version: 7.0.2 (2026-07-08), Apache-2.0, engines node >=16.20.0, bin `tsc`, and per-platform binary packages as optional dependencies.
- 7.0 is the native compiler port written in Go and ships in the regular `typescript` package. 7.0.1-rc came on 2026-06-18. The latest `@typescript/native-preview` is 7.0.0-dev.20260707.2 (2026-07-07).
- 7.0 has no programmatic API; a new, different API is planned for 7.1. Tools that need the API, such as typescript-eslint and Volar-based tools, should use `@typescript/typescript6` (6.0.2, bin `tsc6`) through an npm alias.
- Last 6.x release: 6.0.3 (2026-04-16).
- Recommendation: `typescript@^7.0` for `tsc --noEmit` in `dashboard/`. In `adapters/omp/`, use 6.0 if the extension or its lint tooling imports the compiler API.
- sources: https://registry.npmjs.org/typescript, https://registry.npmjs.org/@typescript/native-preview, https://registry.npmjs.org/@typescript%2Ftypescript6, https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/

#### ruff
- verified: yes (2026-10-16). Basis: PyPI JSON.
- Version: 0.17.1 (2026-10-15), MIT, requires-python >=3.7, Production/Stable, classifiers through 3.15. 0.17.0 was released 2026-10-09.
- Recommendation: `ruff>=0.17` in the dev group. Pin the exact version in the lockfile because minor releases can change rule behavior.
- sources: https://pypi.org/pypi/ruff/json

#### mypy
- verified: yes (2026-10-16). Basis: PyPI JSON and the ty README.
- Version: 2.4.0 (2026-10-01), MIT, requires-python >=3.10, Production/Stable. 2.0.0 was released 2026-05-06.
- ty 0.0.88 (2026-10-15) is MIT with a Beta classifier. Its README says "ty is currently in beta", uses 0.0.x versions, has no stable API, and allows breaking changes (including to diagnostics) between any two versions.
- Recommendation: mypy is the blocking checker. ty may run as a non-blocking CI job.
- sources: https://pypi.org/pypi/mypy/json, https://pypi.org/pypi/ty/json, https://raw.githubusercontent.com/astral-sh/ty/main/README.md

#### pytest
- verified: yes (2026-10-16). Basis: PyPI JSON.
- Version: 9.1.1 (2026-06-19), MIT, requires-python >=3.10, "6 - Mature" classifier. 9.0.0 was released 2025-11-08.
- Recommendation: `pytest>=9.1`. Plugin compatibility (pytest-testmon, Hypothesis; SPEC §8, §22) was not checked here.
- sources: https://pypi.org/pypi/pytest/json

#### mkdocs-material
- verified: yes (2026-10-16). Basis: PyPI JSON and the project blog.
- Version: 9.7.7 (2026-07-17), MIT, requires-python >=3.8. It requires `mkdocs>=1.6,<2`; the cap was added in 9.7.5 (2026-03-10).
- Status: maintenance mode since the 2025-11-11 post. The team promises critical bug and security fixes for at least 12 months, which runs to roughly 2026-11, and no new features.
- MkDocs: 1.6.1 (2024-08-30) is the last stable release. 2.0.dev1 through 2.0.dev6 came out between 2026-08-29 and 2026-09-15. The project blog says MkDocs 2.0 is a rewrite that is incompatible with Material: it has no plugin system, uses TOML config, and offers no migration path. The MkDocs 1.x repo was closed to outside issues and pull requests on 2026-10-02.
- Successor: Zensical 0.0.70 (2026-10-12), MIT, Alpha, requires-python >=3.11. It reads `mkdocs.yml`, but plugin support is partial.
- Recommendation: write docs as `mkdocs.yml` plus Markdown, build with mkdocs-material 9.7.x now, and decide the Zensical migration in an ADR before v0.1.
- sources: https://pypi.org/pypi/mkdocs-material/json, https://pypi.org/pypi/mkdocs/json, https://pypi.org/pypi/zensical/json, https://squidfunk.github.io/mkdocs-material/blog/, https://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/, https://zensical.org/docs/compatibility/mkdocs/

#### Supporting components (no index row)
- **Python**:
  - verified: yes. Latest releases: 3.15.0 (2026-10-09), 3.14.8 (2026-09-30), 3.13.16 (2026-09-30), 3.12.15 (2026-09-30), 3.11.17 (2026-10-01), 3.10.22 (2026-10-01). 3.16.0a1 came out 2026-10-13.
  - Status from the devguide:

    | Branch | Status | End of life |
    |---\|---\|---|
    | 3.10 | EOL | 2026-10-01 |
    | 3.11 | security | 2027-10 |
    | 3.12 | security | 2028-10 |
    | 3.13 | security | 2029-10 |
    | 3.14 | bugfix | 2030-10 |
    | 3.15 | bugfix | 2031-10 |

  - Security branches get source-only releases.
  - Sources: https://www.python.org/api/v2/downloads/release/?is_published=true, https://devguide.python.org/versions/, https://devguide.python.org/developer-workflow/development-cycle/
- **YAML for claims.yaml**:
  - PyYAML 6.0.3 (MIT) dropped all comments in a local `safe_load`/`safe_dump` round trip. It has no cp315 wheels.
  - ruamel.yaml 0.19.1 (MIT) preserves comments, flow style and key order in round-trip mode. It is a pure-Python wheel and no longer needs `ruamel.yaml.clib` or `clibz`.
  - Risk: the ruamel.yaml maintainer warns that PEP 625 naming may block future PyPI uploads, and its source is on a different code host.
  - Use ruamel.yaml if any command rewrites claims.yaml. Read-only use could take either library, but PyYAML lacks 3.15 wheels.
  - Sources: https://pypi.org/pypi/pyyaml/json, https://pypi.org/pypi/ruamel.yaml/json
- **jsonschema**: 4.26.0 (2026-01-07), MIT, >=3.10, full Draft 2020-12 support. It depends on rpds-py; rpds-py 2026.9.1 ships cp315 wheels, Windows included. Sources: https://pypi.org/pypi/jsonschema/json, https://pypi.org/pypi/rpds-py/json

#### Recommended requires-python and pins
- `requires-python = ">=3.11"`. Starlette 1.x needs it, and every chosen dependency supports it. 3.11 reaches EOL in 2027-10, so plan to raise the floor to 3.12 in Q3 2027. CI matrix: 3.11 to 3.15 on Windows, macOS and Linux, plus one `--resolution lowest-direct` job.
- Runtime:

  ```
  typer>=0.26
  fastapi>=0.135
  uvicorn>=0.54
  pydantic>=2.14,<3
  httpx>=0.28.1,<1
  ruamel.yaml>=0.19.1
  jsonschema>=4.26
  ```

  Do not add sse-starlette, PyYAML or `uvicorn[standard]`.
- Dev group: `ruff>=0.17`, `mypy>=2.4`, `pytest>=9.1`. `ty>=0.0.88` is optional and non-blocking.
- Docs group: `mkdocs-material>=9.7.5`, which brings `mkdocs>=1.6,<2`.
- Build backend: `uv_build>=0.13.1,<0.14`, with the dashboard built into `src/<pkg>/api/static/` before `uv build`. Use `hatchling>=1.32` with a build hook only if the Vite build must run inside `uv build`.
- Dashboard: `engines.node >=22.12` (develop on Node 24 LTS), `vite@^8.3`, `@vitejs/plugin-react@^6.1`, `react@^19.3`, `typescript@^7.0`.

## SPEC corrections

Each research task ended with the SPEC statements it found wrong, outdated or underspecified.
The table says how each was resolved. All of them went into SPEC v0.3, and the design decisions
went into ADRs. The raw lists, with their sources, follow per research task.

| # | SPEC | Finding | Resolution |
|---|---|---|---|
| 1 | §0, §19, §24 | The package-index name `switchyard` belongs to an unrelated project; an active project in the same domain uses the name; two other packages install a `sy` command, one in the same domain | Rename to Stepswitch (CLI `ssw`) as the first change of Phase 1 (ADR 0001); SPEC §0, §19, §24 |
| 2 | §1 | Per-turn routing inside agent sessions, cache-aware switching, per-task (harness, model) selection and rule-based routing with fallback chains already exist | Positioning reworded: "combines", never "first" (ADR 0010; SPEC §1) |
| 3 | §4.1 | An open, vendor-neutral responses specification with compliance tests exists | The responses codec follows it; the proxy runs its compliance tests (ADR 0003) |
| 4 | §4.1 | The generate-content reference is now labelled legacy, and a newer interactions format is generally available | Codec kept; the interactions format is a watch item outside the C-004 wording (SPEC §4.1) |
| 5 | §4.1 | Local engines also serve responses and messages routes, with gaps | Conformance runs against each engine in each format it serves; gaps go into the matrix (SPEC §4.1) |
| 6 | §4.1 | "Lossless for messages and tool calls" cannot hold for every pair: server-side tools, signed reasoning, differing tool-result payloads, missing tool-call IDs, harmony channel metadata | Lossless scope spelled out, IDs synthesized, everything else reported (ADR 0003 items 1, 3, 9) |
| 7 | §4.1 | The named gateway library shipped two compromised releases in 2026; its proxy extra pulls a proprietary package; it fetches a remote cost map by default | Bridge optional and off by default: base package only, own environment, out of process, hash-locked, seven-day release cooldown, local cost map (ADR 0003 item 10) |
| 8 | §4.2 | TensorRT-LLM is Linux-only and does not list our GPU class | Dropped as an adapter; still reachable as a plain endpoint (ADR 0008) |
| 9 | §4.2 | AirLLM has no server; KTransformers serves through an SGLang fork on Linux x86-64 only; MLX-LM has no constrained decoding; `transformers serve` parses tool calls only for some tokenizers; ExLlamaV3 needs a separate AGPL server; vLLM and SGLang need Linux and a CUDA 13 driver | Per-engine table with platforms and access paths (ADR 0008; SPEC §4.2) |
| 10 | §4.3, §4.4 | About a dozen tool-call syntax classes exist; syntax changes between versions of one family; engine parser names differ per engine; some families ship no Jinja template | Parser chosen by hub ID plus template (or encoder) hash with marker fallback; per-engine recipes; template source recorded (ADR 0003 items 5, 9; ADR 0008 item 5) |
| 11 | §4.4 | Server-side tool parsing varies (malformed arguments, missing IDs, unsupported forced tool choice, parallel calls off by default); some guided-decoding fields were removed | Probe before trusting; never rely on forced tool choice or prefill; current field names only (ADR 0003 items 4, 6) |
| 12 | §4.4 | Constrained output breaks reasoning models unless it starts after the reasoning block | Trigger-based grammars or reasoning-aware structured output, plus a probe case (ADR 0003 item 6) |
| 13 | §4.5 | Effort vocabularies include off, minimal, xhigh, numeric scales, token budgets and adaptive modes; some models cannot switch thinking off | Seven levels plus `budget_tokens`; per-endpoint subset, mechanism, default and off-capability; clamps reported (ADR 0003 item 7) |
| 14 | §4.5 | Reasoning arrives under three field names; signed or encrypted reasoning must go back unchanged and only to its origin; retention rules differ per model | Reasoning origin in the internal representation; per-model retention policy; template switches passed explicitly (ADR 0003 items 1, 8) |
| 15 | §4.6 | The probe lacked checks for tool-call IDs, forced tool choice, prefill, streamed arguments, constrained output under reasoning, retention, usage semantics and served formats | Added to SPEC §4.6 |
| 16 | §5 | One candidate ID was renamed, one model has no GGUF path yet, one T1 size and one T2 fit were wrong, the MoE slot was unnamed, several licenses carry commercial conditions, and one small model is outdated | Candidates updated, `commercial_use` field added (ADR 0009; SPEC §5) |
| 17 | §5.2 | The 8 GB cap was a download size; resident memory decides whether a CPU-only 8 GB machine can run the set | Disk cap plus a RAM budget (ADR 0009; SPEC §5.2) |
| 18 | §5.2 | A plain Ollama pull fetches from its own registry, not from the original source | Pull `hf.co/...` names or import a downloaded GGUF (SPEC §5.2) |
| 19 | §5.3 | Slot save/restore exists only on the llama.cpp server (verified); most engines lack metrics or prefix-cache counters; llama.cpp has a router mode; some engines manage their own memory | Optional metrics, cache statistics from per-response usage, router mode first, no double management (ADR 0008 items 1-4; SPEC §5.3) |
| 20 | §6.1 | The Python Playwright assertions have no screenshot comparison | ARIA-snapshot smoke test; pixel diff is a separate, optional step (SPEC §6.1) |
| 21 | §6.2 | `jupytext --sync` picks the source by timestamp | The gate runs `jupytext --to ipynb --update` (SPEC §6.2) |
| 22 | §7.2 | Handing a trajectory to a stronger model recovers less than half of the quality gap; prior art blocks switches inside tool loops | Handoff-conditional `p_success`; no switch inside an open tool loop or with endpoint-held state (ADR 0005; SPEC §7.2, §12) |
| 23 | §8.2 | testmon ignores non-Python files and offers a cloud storage mode | One data file per worktree; non-Python changes force the full run; cloud mode never used (SPEC §8.2) |
| 24 | §9 | The receiving model's tokenizer is not always reachable (no tokenize route on some servers; BPE files download on first use) | Tokenizer resolution order; `tokenizer` and `exact` recorded in provenance (SPEC §9) |
| 25 | §11 | The llama.cpp server restores matching prefixes from a RAM prompt cache automatically | Slot files only across restarts and reloads (SPEC §11) |
| 26 | §12 | Adapter mechanisms differ from the SPEC: Pi has virtual models rather than roles; Oh My Pi has roles and spawn hooks; one harness speaks only the messages format and its upstream does not support other models; OpenCode and Kimi Code are ACP agents; ACP is now specified | Adapters rewritten per harness; ACP v1 methods named (SPEC §12 items 3-7; ADR 0004) |
| 27 | §12, §15 | Harnesses make their own network calls | Adapters set each harness's egress switches in offline mode (SPEC §12, §15) |
| 28 | §12 | The reference hash-anchored edit format changed | Switchyard defines its own edit format under `spec/` (SPEC §12 item 1) |
| 29 | §12 | No LSP client was named; one candidate library is GPL as a whole and another auto-downloads servers | Thin client on pygls and lsprotocol; user-installed servers (SPEC §12 item 1) |
| 30 | §13 | Repo-map caching per commit misses worktree edits; the language pack downloads parsers on first use; the default LLMLingua model is a 7B GPU model | SPEC TK2 and TK11 |
| 31 | §14 | Speculative decoding, KV-cache quantization and MoE offload flags differ per engine | Flags verified and listed per engine (SPEC RN3-RN5) |
| 32 | §15 | Egress can happen behind a localhost URL (cloud-backed model names, remote peers) and outside the egress module (lazy downloads, harnesses, child processes) | SPEC §15; C-006 and C-007 scopes (ADR 0011) |
| 33 | §18.4, §18.5 | Hosted CI offers containers and OS-level isolation on Linux runners only; runner labels move | C-001, C-006, C-007 and C-016 scopes made precise; runner labels pinned (ADR 0011; SPEC §18.4) |
| 34 | §19 | Port 4000 collides with another harness's gateway default | Proxy default port 7778 (ADR 0004) |
| 35 | §3, §20, §21 | SQLite WAL bug before 3.51.3; the plain YAML library drops comments; the build backend runs no build scripts; Vite needs Node 22.12 or later; TypeScript 7 has no compiler API yet | ADR 0002; SPEC §3, §20, §21 |
| 36 | §22 | Feature attributions and canonical repositories changed (Pi, Oh My Pi, open-multi-agent, Conductor); simplicio's tiny-model gains were not reproduced; oh-my-openagent's license limits use | Notes under SPEC §22; details above |
| 37 | §24 | The docs theme is in maintenance mode; uv now installs Python 3.15 by default | Docs tool chosen by ADR in Phase 10; 3.15 in the CI matrix (ADR 0002; SPEC §24) |

Proposals not adopted as written:

- An `off` effort level (A06): the internal level is named `none`, as in current request fields,
  with the same meaning; `budget_tokens` was adopted.
- Excluding DeepSeek V4.1 Flash because no local engine supports it (A08): the GPU servers
  already ship its parsers and tokenizer mode (A02, A06), so it gets vLLM and SGLang recipes and
  is excluded only from GGUF pulls (ADR 0009).
- Further T1 and T2 candidates from new releases (A07): registry candidates to verify in Phase 1,
  not defaults.
- An exact version pin for the gateway library (A05): chosen when the bridge is built, under the
  cooldown rule.

Recorded for later phases (no SPEC change needed):

- Codec tests cover both reasoning field names that vLLM has used (A02; C-004 tests, Phase 1).
- The server manager always enables engine metrics flags where they exist (A02; Phase 1 and
  Phase 3 adapters).
- The rename covers `switchyard.toml`, `.switchyard/` and the package directory (A13; Phase 1).
- testmon data copied between worktrees at the same base commit needs a spike (A12; Phase 2).

### Raw corrections by research task

#### A01: Big models on small hardware

- SPEC §5 (SL row): "slow lane, any GPU ≥ 4 GB ... T3 models via layer streaming (AirLLM) or CPU-GPU hybrid MoE (KTransformers)" -> AirLLM's own figures span ~1-12 GB VRAM per model (DeepSeek-V3 ~12 GB; Qwen3.8-Flash-Next 5.95 GB plus host RAM and ~360 GB disk) and its list covers Kimi K3 and DeepSeek V2/V3/R1 but not GLM-5.x, MiMo or DeepSeek V4; KTransformers native precision needs 1-2 GPUs with >= 24 GB VRAM, RAM >= model size, Linux x86-64, and covers DeepSeek V3.2/V4-Flash, GLM-5.x (incl. GLM-5.3-Flash), Kimi K2.x, MiniMax-M2.x/M3, Qwen3 MoE -> replace "any GPU ≥ 4 GB" with per-engine requirements and record supported T3 families per engine in the registry (https://raw.githubusercontent.com/lyogavin/airllm/main/README.md, https://raw.githubusercontent.com/kvcache-ai/ktransformers/main/doc/en/kt-kernel/Native-Precision-Tutorial.md)
- SPEC §4.2: "AirLLM and KTransformers (slow lane, §5)" as engine targets behind the HTTP-only core -> AirLLM has no HTTP server (Python API only); KTransformers serves through its SGLang fork `sglang-kt` with Linux x86-64 wheels only -> specify an out-of-process chat-completions shim for AirLLM (own venv, optional extra) and implement KTransformers as an SGLang-adapter profile with `--kt-*` flags; mark Windows as WSL2-only (verify) (https://raw.githubusercontent.com/kvcache-ai/ktransformers/main/kt-kernel/README.md, https://pypi.org/pypi/kt-kernel/json)
- SPEC §5.3: "verify llama.cpp slot save/restore" -> verified: `POST /slots/{id_slot}?action=save|restore|erase` with `filename`, files under `--slot-save-path` (env `LLAMA_ARG_SLOT_SAVE_PATH`); v0.7.0 slot files keep context checkpoints and draft-model state -> drop "(verify)" and have the server manager set `--slot-save-path` (https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/tools/server/README.md, https://github.com/ggml-org/llama.cpp/releases/tag/v0.7.0)
- SPEC §5.3: "hot-swap (study llama-swap)" -> llama-server has a built-in router mode (no `-m`; `--models-dir`, `--models-preset`, `--models-max` default 4, `--models-autoload`; `/models`, `/models/load`, `/models/unload`, `/models/sse`; `--sleep-idle-seconds`) -> use router mode as the first hot-swap option for llama.cpp, keep llama-swap for cross-engine swaps (https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/tools/server/README.md)
- SPEC §5.3 and §7.2: "`prefix_cache_stats`" / "engine prefix-cache metrics where available" -> llama.cpp `/metrics` has no prefix-cache counters; reuse is reported per response (`timings.cache_n`, `usage.prompt_tokens_details.cached_tokens`, `tokens_cached`) -> compute llama.cpp `prefix_cache_stats` from per-response fields in the ledger (https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/tools/server/README.md)
- SPEC §11: "Same-model resume uses engine KV slot save/restore when supported (verify)" -> verified for llama.cpp; llama-server also keeps a host-RAM prompt cache (`-cram, --cache-ram`, default 8192 MiB; `--cache-idle-slots` on) that restores matching prefixes automatically -> use slot files only across server restarts or model reloads (https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/tools/server/README.md)
- SPEC §14 RN3: "Speculative decoding with a T0 draft model where supported (verify per engine)" -> llama.cpp uses `-md, --spec-draft-model`, `--spec-type` (draft-simple, draft-eagle3, draft-mtp, draft-dflash, draft-dspark, ngram-simple, ngram-map-k, ngram-map-k4v, ngram-mod, ngram-cache), `--spec-draft-n-max`/`--spec-draft-n-min`; `--draft-max`/`--draft-min` are removed -> name these flags and add n-gram modes (no draft model) as the CPU-only option (https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/tools/server/README.md)
- SPEC §14 RN5: "llama.cpp `--n-cpu-moe`, KTransformers ... (verify flags)" -> verified: `-ncmoe, --n-cpu-moe N`, `-cmoe, --cpu-moe`, `-ot, --override-tensor`, new `--moe-cache-mib N`; KTransformers `--kt-method`, `--kt-num-gpu-experts`, `--kt-cpuinfer`, `--kt-threadpool-count` -> replace "(verify flags)" with these (https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/tools/server/README.md, https://raw.githubusercontent.com/kvcache-ai/ktransformers/main/kt-kernel/README.md)
- SPEC §4.1: "engine-native formats: ... llama.cpp server native endpoints (completion, slots)" -> llama-server also serves the responses (`/v1/responses`, converted to chat-completions internally) and messages (`/v1/messages`, `/v1/messages/count_tokens`) formats, not generate-content -> note this and use llama-server as a live conformance target for three of the four formats (https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/tools/server/README.md)

#### A02: Serving and engines: GPU servers, KV cache, engine-side tool parsers

- SPEC §4.1: "chat-completions format (the de facto standard for open-model servers and most gateways)" -> vLLM 0.31.0, SGLang 0.5.22 and Ollama 0.40.5 also serve the responses (`/v1/responses`) and messages (`/v1/messages`, `/v1/messages/count_tokens`) formats natively, with gaps (gpt-oss on vLLM responses: usage zeros, no streamed function calls) -> run each codec's conformance tests directly against a local engine in that format and record per-engine gaps in the compatibility matrix (https://docs.vllm.ai/en/stable/serving/online_serving/ , https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/srt/entrypoints/http_server.py , https://raw.githubusercontent.com/ollama/ollama/v0.40.5/server/routes.go , https://docs.vllm.ai/projects/recipes/en/latest/OpenAI/GPT-OSS.html)
- SPEC §4.1: "shared internal representation (messages, tool calls, tool results, reasoning, ...)" -> reasoning arrives under different keys: vLLM `reasoning` (renamed from `reasoning_content`; docs warn old clients "silently read an empty `reasoning_content`"), SGLang and llama.cpp `reasoning_content`, Ollama native `thinking` -> the codec maps all of them and a claim test covers both vLLM key names (https://docs.vllm.ai/en/stable/features/reasoning_outputs/ , https://docs.sglang.io/docs/advanced_features/separate_reasoning.md , https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md , https://docs.ollama.com/capabilities/tool-calling)
- SPEC §4.2: "Supported targets ... vLLM, SGLang" (no platform or driver scope) -> both ship Linux-only wheels; vLLM docs: no native Windows, WSL suggested; SGLang documents Linux and ARM macOS (MLX), not WSL2; vLLM 0.31.0 wheel is CUDA 13.0 (CUDA 13 images need an R580+ driver); SGLang requires CUDA 13 since 0.5.20 (0.5.19 last CUDA 12) -> mark both "Linux; Windows via WSL2/Docker (vLLM documented, SGLang unverified)" and have `sy doctor` report the driver branch and choose CUDA 12.9 builds (vLLM image, SGLang 0.5.19) below R580 (https://docs.vllm.ai/en/stable/getting_started/installation/gpu/ , https://github.com/vllm-project/vllm/releases/tag/v0.31.0 , https://docs.sglang.io/get_started/install.html , https://pypi.org/pypi/sglang/json)
- SPEC §4.4: "When an engine already parses tool calls server-side, use its output; otherwise parse raw text." -> server-side results vary by parser and mode: vLLM auto mode extracts calls from raw text and arguments "may be malformed" unless `strict`/`--tool-strict-level`; vLLM gpt-oss chat-completions accepts only `tool_choice="auto"`; SGLang `gpt-oss` drops analysis-channel text; llama.cpp parallel calls are off unless `parallel_tool_calls: true`; Ollama's template-derived parser cannot parse calls without `arguments` and its compatibility API does not support `tool_choice` -> use server output only after `sy probe` passes single, parallel, streamed and schema-valid calls for that (engine, parser, model); else send tools in the prompt and parse raw text (https://docs.vllm.ai/en/stable/features/tool_calling/ , https://docs.sglang.io/advanced_features/tool_parser.html , https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/function-calling.md , https://raw.githubusercontent.com/ollama/ollama/v0.40.5/tools/tools.go , https://docs.ollama.com/api/openai-compatibility)
- SPEC §4.4 (with §4.3 step 3): "per-family parsers ... Select the tool-call parser from the chat template" -> SGLang 0.5.22 accepts `--tool-call-parser auto` and `--reasoning-parser auto`; llama.cpp builds a PEG parser from the template automatically; vLLM 0.31.0 needs an explicit name (50 tool, 35 reasoning parsers); names differ by engine (`minimax_m2`/`minimax-m2`, `deepseek_v31`/`deepseekv31`, `--tool-call-parser openai`/`gpt-oss`/`harmony`) -> store per-engine parser names in `registry/*.toml`, use `auto` on SGLang, and validate names against the running engine at probe time (https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/tool_parsers/__init__.py , https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/srt/server_args.py , https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/autoparser.md)
- SPEC §4.4: "grammar or JSON-Schema constrained decoding (llama.cpp grammars, engine guided decoding)" -> vLLM removed `guided_json`, `guided_regex`, `guided_choice`, `guided_grammar` in v0.12.0; current fields are `structured_outputs` (json, regex, choice, grammar, structural_tag) or `response_format` json_schema; SGLang uses `response_format` json_schema, `regex` or `ebnf`, one per request -> the codec emits current per-engine field names and never `guided_*` (https://docs.vllm.ai/en/stable/features/structured_outputs/ , https://docs.sglang.io/docs/advanced_features/structured_outputs.md)
- SPEC §5.3: "slot_save / slot_restore (... verify llama.cpp slot save/restore)" -> llama.cpp verified: `--slot-save-path` and `POST /slots/{id_slot}?action=save`, `action=restore`, `action=erase`; vLLM and SGLang have no per-slot API (nearest: vLLM `--kv-offloading-backend native` or `lmcache`, SGLang HiCache `--enable-hierarchical-cache` with `--hicache-storage-backend`) -> keep slot_* llama.cpp-only and add an optional `kv_offload` capability (https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md , https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/config/cache.py , https://docs.sglang.io/advanced_features/server_arguments.html)
- SPEC §5.3: "metrics / prefix_cache_stats" -> vLLM lists `/metrics` without a flag and exports `vllm:prefix_cache_hits`/`vllm:prefix_cache_queries`; SGLang exports `sglang:cache_hit_rate` only with `--enable-metrics` (default off) and per-request cached tokens only with `--enable-cache-report`; llama.cpp `/metrics` needs `--metrics` and lists no prefix-cache hit metric -> the server manager always passes these flags; `prefix_cache_stats` returns "unsupported" where no metric exists (https://docs.vllm.ai/en/stable/usage/metrics/ , https://docs.sglang.io/references/production_metrics.html , https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)
- SPEC §5.3: "hot-swap (study llama-swap)" -> vLLM `/sleep`, `/wake_up`, `/is_sleeping` exist only with `--enable-sleep-mode` and `VLLM_SERVER_DEV_MODE=1` (docs: do not expose); SGLang memory release needs `--enable-memory-saver`; neither loads a different model in-process -> keep hot-swap process-level (stop/start behind the proxy) and treat sleep or memory-saver as an opt-in fast path on loopback-bound servers (https://docs.vllm.ai/en/stable/features/sleep_mode/ , https://docs.sglang.io/advanced_features/server_arguments.html)
- SPEC §14 RN3: "Speculative decoding with a T0 draft model where supported (verify per engine)" -> all engines also offer draft-free methods: vLLM `--speculative-config` method `draft_model`, `ngram`, `suffix`, `eagle3`, `mtp`, ...; SGLang `--speculative-algorithm` STANDALONE, NGRAM, EAGLE, EAGLE3, NEXTN; llama.cpp `--spec-type` draft-simple, ngram-simple, ngram-map-k, ngram-mod, ngram-cache, draft-eagle3, draft-mtp, ... -> list n-gram methods (no extra model memory) and model-shipped MTP heads next to the T0 draft model; `sy calibrate` reads acceptance counters such as `llamacpp:spec_decode_num_accepted_tokens_total` (https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/config/speculative.py , https://docs.sglang.io/advanced_features/server_arguments.html , https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)
- SPEC §14 RN4: "KV cache quantization where supported" -> engine flags only: vLLM `--kv-cache-dtype` (on sm_86 FP8 KV needs the Triton or FlashInfer backend; FlashAttention backend allows FP8 KV only on sm90/sm100), SGLang `--kv-cache-dtype` fp8_e4m3/fp8_e5m2 (FP4 experimental, native path SM100), llama.cpp `-ctk`/`-ctv` (quantized V needs Flash Attention), Ollama `OLLAMA_KV_CACHE_TYPE` -> RN4 becomes one registry key `kv_cache_dtype` mapped per engine, default off, no external KV-quant library (https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/v1/attention/backends/fa_utils.py , https://docs.sglang.io/docs/advanced_features/quantized_kv_cache.md , https://raw.githubusercontent.com/ggml-org/llama.cpp/master/src/llama-context.cpp , https://raw.githubusercontent.com/ollama/ollama/v0.40.5/docs/faq.mdx)
- SPEC §14 RN5: "MoE expert offload to system RAM (llama.cpp `--n-cpu-moe`, KTransformers) ... (verify flags)" -> llama.cpp flags verified (`-cmoe, --cpu-moe`; `-ncmoe, --n-cpu-moe N`); SGLang 0.5.22 carries the KTransformers path as `--kt-method` (default AMXINT4), `--kt-weight-path`, `--kt-cpuinfer`, `--kt-threadpool-count`, `--kt-num-gpu-experts`, `--kt-max-deferred-experts-per-token`; vLLM has only generic weight offload (`--cpu-offload-gb`, `--offload-*`) -> add an SGLang `--kt-*` recipe for AMX hosts and do not list vLLM under RN5 (https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md , https://docs.sglang.io/advanced_features/server_arguments.html , https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/vllm/engine/arg_utils.py)

#### A03: Serving and engines: local engines and hot-swap

- SPEC §4.2: "Optional: ExLlamaV3, TensorRT-LLM (verify)" -> TensorRT-LLM is Linux-only (Windows deprecated since v0.18.0), its supported-hardware page lists A100 as the only Ampere GPU (RTX A6000 sm_86 not listed), and the latest stable is 1.2.1 (2026-04-20) with 1.3.0 still at rc29 -> drop TensorRT-LLM as an engine adapter; reach any user-run `trtllm-serve` through the chat-completions/responses codecs. (https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/release-notes.md ; https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/supported-hardware.md)
- SPEC §4.2: "Optional: ExLlamaV3" -> ExLlamaV3 is a library with no server of its own; its recommended server is TabbyAPI (AGPL-3.0, rolling release with no tagged versions, README says not for production); ExLlamaV3 itself runs natively on Windows and builds for sm_86 -> name the target "ExLlamaV3 via TabbyAPI (external process over HTTP, pinned by commit, never vendored)". (https://raw.githubusercontent.com/turboderp-org/exllamav3/master/README.md ; https://raw.githubusercontent.com/theroyallab/tabbyAPI/main/README.md)
- SPEC §4.2: "MLX-LM (macOS on ARM)" -> mlx-lm 0.32.2 also declares Linux (extras `cuda12`, `cuda13`, `cpu`), and `mlx_lm.server` implements no `response_format` (no constrained decoding) -> "MLX-LM (macOS on ARM; Linux via extras, unverified); JSON fallback without grammar on this engine". (https://pypi.org/pypi/mlx-lm/0.32.2/json ; https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/server.py)
- SPEC §4.2: "a plain transformers fallback (any architecture the library supports, slow but universal)" -> universal for architectures only: `transformers serve` (v5) parses tool calls only for tokenizers that declare tool-call tokens, separates reasoning only with a response template, and documents no constrained decoding; the core must not import it -> "transformers fallback = `transformers serve` process over HTTP (chat-completions/responses), always paired with the JSON fallback tool protocol plus validation and retry". (https://raw.githubusercontent.com/huggingface/transformers/main/docs/source/en/serve-cli/serving.md)
- SPEC §5.2: "Uses llama.cpp server (preferred) or Ollama if present ... downloads from the original source per license" -> a plain `ollama pull <name>` fetches from Ollama's own registry, not the original repo -> with Ollama, pull by `hf.co/{username}/{repository}:{quantization}` or import a downloaded GGUF via Modelfile `FROM`. (https://huggingface.co/docs/hub/ollama ; https://raw.githubusercontent.com/ollama/ollama/main/docs/import.mdx)
- SPEC §5.3: "`start(model) / stop / health / metrics / prefix_cache_stats / slot_save / slot_restore`" -> Ollama, `mlx_lm.server`, `transformers serve` and TabbyAPI expose no metrics endpoint; cache hits are reported only per response (`prompt_eval_cached_count`, `usage.prompt_tokens_details.cached_tokens`); none supports slot save/restore -> mark `metrics` and `prefix_cache_stats` optional and derive cache statistics from per-response usage when the engine has no endpoint. (https://raw.githubusercontent.com/ollama/ollama/main/docs/api/usage.mdx ; https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/server.py)
- SPEC §5.3: "LRU eviction when memory is tight ... hot-swap (study llama-swap)" -> llama-swap has no memory accounting (eviction by `groups` swap/exclusive/persistent rules or matrix `evict_costs`; `globalTTL` default 0 = never unload), while Ollama and `transformers serve` already load and unload models themselves -> the server manager owns memory accounting (inputs: `sy doctor`, Ollama `/api/ps` `size_vram`) and must not double-manage self-managing engines (set their own TTLs instead). (https://raw.githubusercontent.com/mostlygeek/llama-swap/main/config-schema.json ; https://raw.githubusercontent.com/ollama/ollama/main/docs/faq.mdx)
- SPEC §15 (outside A03, caused by an engine behavior): "`--offline` or `privacy.mode = "local"` blocks every non-local connection" -> a signed-in local Ollama serves `:cloud` model names by forwarding to its hosted service, and llama-swap `peers` forward to remote servers, so egress can occur behind a localhost URL -> in local mode, reject Ollama models with `remote_host`/`remote_model` set in `/api/tags` (or require `OLLAMA_NO_CLOUD=1`) and llama-swap configs with `peers`. (https://raw.githubusercontent.com/ollama/ollama/main/docs/faq.mdx ; https://raw.githubusercontent.com/ollama/ollama/main/docs/openapi.yaml ; https://raw.githubusercontent.com/mostlygeek/llama-swap/main/config-schema.json)

#### A04: API formats

- SPEC §4.1: "generate-content format" -> the generate-content guides are now titled "Gemini Generate Content API (Legacy)"; the interactions format is GA since June 2026 and "recommended for all new projects", while generateContent "remains fully supported" -> keep the generate-content codec; add the interactions format as a tracked format and defer its codec until the version path settles (guides show `v1beta`, the migration guide `v1beta2`) (https://ai.google.dev/gemini-api/docs/interactions-overview ; https://ai.google.dev/gemini-api/docs/migrate-to-interactions ; https://ai.google.dev/gemini-api/docs/generate-content/thinking)
- SPEC §4.1: "responses format" (no normative reference named) -> a vendor-neutral spec derived from it exists: Open Responses, version 2026-04-24, Apache-2.0, with an OpenAPI reference and compliance tests; it names the reasoning delta `response.reasoning.delta` where the hosted reference uses `response.reasoning_text.delta` -> base the responses codec on Open Responses, treat hosted-only items and events as extensions, accept both reasoning event names, and run its compliance tests against `sy proxy` (https://www.openresponses.org/specification ; https://www.openresponses.org/reference ; https://github.com/openresponses/openresponses)
- SPEC §4.1: "Conversion between any two formats is lossless for messages and tool calls" -> not achievable for every pair: custom grammar tools exist only in chat-completions and responses; tool-result payloads differ (chat-completions tool messages take text only, messages-format `tool_result` takes images and documents, generate-content `functionResponse.response` is an object); Ollama tool calls carry no ids (superseded in Phase 1: the documented schema has none, but current servers send them); built-in and server tools are format-specific -> "lossless for text and image messages, JSON-Schema function tools, function calls and text tool results (ids synthesized where absent); every other feature follows the conversion matrix and is reported or rejected" (https://developers.openai.com/api/reference/resources/chat ; https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/tool_result_block_param.py ; https://ai.google.dev/gemini-api/docs/generate-content/function-calling ; https://docs.ollama.com/api/chat)
- SPEC §4.1: "engine-native formats: Ollama native API, llama.cpp server native endpoints (completion, slots)" -> llama.cpp also exposes `/infill`, `/tokenize`, `/detokenize`, `/apply-template`, `/props`, `/v1/chat/completions/control`; llama.cpp, Ollama and vLLM all serve `/v1/messages` and `/v1/responses` (stateless or converted to chat) -> add those llama.cpp endpoints to the codec scope and record per-endpoint format support in the probe profile (https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md ; https://docs.ollama.com/api/anthropic-compatibility ; https://docs.vllm.ai/en/latest/serving/online_serving/)
- SPEC §4.1: "raw text-completion endpoints (no chat template applied server-side)" -> Ollama `/api/generate` applies the model template unless `raw` is set; llama.cpp `/apply-template` renders a chat template without generating -> the raw codec sets `raw: true` on Ollama and can use `/apply-template` for prompt rendering (https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)
- SPEC §4.4: "force a JSON action object with grammar or JSON-Schema constrained decoding" -> correct, but forcing via tools is not portable: several current hosted messages-format models return 400 for `tool_choice` `any`/`tool` and for assistant prefill; Ollama's compat layers do not support `tool_choice` -> never rely on `tool_choice` or prefill in the fallback; use `format` (Ollama), `json_schema`/`grammar` (llama.cpp), `response_format`/`output_config.format` (hosted); add "forced tool choice" and "assistant prefill" checks to the §4.6 probe (https://platform.claude.com/docs/en/api/errors ; https://docs.ollama.com/api/openai-compatibility ; https://docs.ollama.com/api/anthropic-compatibility)
- SPEC §4.5: "Map reasoning output (dedicated fields, think tags, harmony channels) into one internal field" -> three hosted formats return signed or encrypted reasoning that must be replayed unchanged to the same model: messages-format `thinking.signature` and `redacted_thinking.data` (400 if modified during tool use; newest models bind blocks to an unchanged prefix), generate-content `thoughtSignature` ("mandatory for function calling"; finish reason `MISSING_THOUGHT_SIGNATURE`), responses `encrypted_content` (needed with `store: false`) -> internal reasoning item = {text, summary, opaque blob, origin endpoint and model}; replay the blob only to its origin; drop and report on any switch (https://platform.claude.com/docs/en/api/errors ; https://ai.google.dev/gemini-api/docs/generate-content/function-calling ; https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_reasoning_item.py)
- SPEC §4.5: "Map effort controls to `effort: low | medium | high | max` per endpoint" -> current enums: chat-completions and responses `none, minimal, low, medium, high, xhigh, max`; messages-format `output_config.effort` `low, medium, high, xhigh, max` plus `thinking.type` `adaptive, enabled, disabled, between_tools`; generate-content `thinkingLevel` `minimal, low, medium, high`; Ollama `think` bool or model-defined strings; vLLM maps `reasoning_effort` to `enable_thinking`; reasoning text arrives as `reasoning_content` (llama.cpp), `reasoning` (vLLM, renamed from `reasoning_content`) or `thinking` (Ollama native) -> internal enum `none, minimal, low, medium, high, xhigh, max` with a per-endpoint mapping table stored in the capability profile; codecs read all three reasoning field names (https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/shared/reasoning.py ; https://platform.claude.com/docs/en/api/messages ; https://ai.google.dev/gemini-api/docs/generate-content/thinking ; https://docs.ollama.com/api/chat ; https://docs.vllm.ai/en/latest/features/reasoning_outputs/)
- SPEC §12 (item 2): "accepts every supported API format (§4.1) and forwards to any endpoint in any format ... This is how mid-session switching works" -> server-side state does not transfer between endpoints: responses `previous_response_id`, `conversation`, `background` (Ollama `/v1/responses` rejects `previous_response_id` and `conversation`; llama.cpp converts responses to chat), WebSocket mode keeps a socket of up to 60 minutes with `response.create` events, and signed reasoning cannot cross models -> the proxy keeps its own response store to expand `previous_response_id` into full input, strips signed reasoning on cross-model routes with a report, supports or explicitly rejects WebSocket mode, and also serves `/v1/models` and `/v1/messages/count_tokens` (https://developers.openai.com/api/docs/guides/websocket-mode ; https://docs.ollama.com/api/openai-compatibility ; https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)

#### A05: Gateways and routers

- SPEC §1 item 4: "Cache-aware switching: a cost model that weighs re-reading context on the new model against the expected quality gain" (listed as new) -> vLLM Semantic Router has priced handoff cost and "prefix-cache checkout" per turn in agent sessions since v0.3 (2026-06), and a hosted router applies a cache-aware switching budget -> move this to "Already exists elsewhere" and keep our time-based, measured-prefill cost model as a feature with no novelty claim (https://vllm-sr.ai/blog/session-aware-agentic-routing/, https://www.digitalocean.com/blog/inference-router-cache-aware)
- SPEC §0 headline and §1 item 1: "Automatic step-level routing inside one task" (as new) -> automatic per-call or per-turn routing inside agent sessions already exists in vLLM Semantic Router, the OpenHands SDK router and claude-code-router 2.x scenarios -> reword to "step-type-aware routing whose policy learns from the user's own verified outcomes", and do not claim step-level switching itself as new (https://github.com/vllm-project/semantic-router, https://docs.openhands.dev/sdk/guides/llm-routing, https://raw.githubusercontent.com/musistudio/claude-code-router/v2.0.0/README.md)
- SPEC §1 item 2: "Routing over (harness, model) pairs, not just models" (as new) -> agent-router v0.1.0 (2026-09-17) already picks agent CLI, model and effort per task -> reword to "learned, verification-scored routing over (harness, model) pairs, per task and per step" (https://github.com/nidhi-singh02/agent-router)
- SPEC §1 items 5 to 14: listed as "New in Switchyard" -> each has partial prior art (see the positioning table) -> add a "closest prior art" column to §1 and make the claim wording say "combines" or "integrates", not "first" (URLs in the table above)
- SPEC §4.1: "an optional bridge to a multi-provider gateway library (study LiteLLM), pinned to an exact verified version and isolated behind the codec interface" -> four facts make in-process isolation insufficient:
  - LiteLLM 1.82.7 and 1.82.8 (2026-03-24) shipped a credential stealer, including a `.pth` file that runs at interpreter start without any import.
  - Releases since 1.84.0 carry no package-index provenance.
  - The `[proxy]` extra pulls the proprietary `litellm-enterprise`.
  - By default the library fetches a remote cost map at startup.

  -> require the following:
  - SDK only, with no extras.
  - A separate environment and an out-of-process call path behind the codec interface.
  - A hash-locked install and a 7-day release cooldown.
  - `LITELLM_LOCAL_MODEL_COST_MAP=True`.
  - The bridge is disabled in zero-egress mode unless it targets a local endpoint.
  - Pin `litellm==1.105.1` from 2026-10-21 (interim pin: 1.103.4).

  (https://docs.litellm.ai/blog/security-update-march-2026, https://pypi.org/pypi/litellm/json, https://docs.litellm.ai/docs/proxy/config_settings)
- SPEC §4.1: "Conversion between any two formats is lossless for messages and tool calls; anything that can't be represented is reported, never silently dropped" -> LiteLLM's bridges degrade by design ("loses only its encrypted payload and id"; turns are "served in degraded form rather than failed") -> make the bridge adapter diff the internal representation before and after each call and emit loss reports; exclude provider-held state (encrypted reasoning, server-side conversation IDs) from the lossless claim scope in §4.8 (https://docs.litellm.ai/docs/response_api)
- SPEC §12 item 2 and §7.2: "the router picks a model per request. This is how mid-session switching works inside third-party harnesses" -> prior art treats switching during an active tool loop, or when a turn continues provider-held state, as unsafe and blocks it with "hard locks" -> add a rule that forbids switching while a tool-call loop is open or when the turn depends on provider-held state (https://vllm-sr.ai/blog/session-aware-agentic-routing/)
- SPEC §7.2: "expected_gain(m) = (p_success(m) - p_success(current)) * value_of_success_seconds" -> research reports that handing a low-cost model's full trajectory to a high-cost model "recovers less than half" of the quality gap, at a cost premium -> estimate p_success for a mid-trajectory switch conditional on the handoff (not the model's fresh-start rate) and add a learned handoff penalty term (https://arxiv.org/abs/2608.24358)
- SPEC §4.1 format list: lists chat-completions, responses, messages and generate-content -> claude-code-router 3.x also targets a newer "interactions" API in the generate-content family -> add "interactions format (verify)" as a watch item in §4.1 and §4.7 (https://raw.githubusercontent.com/musistudio/claude-code-router/main/README.md)

#### A06: Open-weight model families, part 1

Checked against docs/SPEC.md v0.3 draft (2026-10-16). Parser choice by hub ID plus template hash, the seven effort levels and the retention policy are already in v0.3; the items below remain.
- SPEC §5 T3: "MiMo-V2.6-Pro" -> no repo has that ID; the V2.6 Pro checkpoints are `XiaomiMiMo/MiMo-V2.6-Pro-RL` and `XiaomiMiMo/MiMo-V2.6-Pro-MOPD` (MOPD is the later upgrade that reduces repeated identical tool calls; 1.02T/42B; MIT) -> name `XiaomiMiMo/MiMo-V2.6-Pro-MOPD` (https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Pro-MOPD/raw/main/README.md)
- SPEC §5 T2: "GLM-5.3-Flash (tight at 4-bit)" on "workstation, 96–192 GB" -> 4-bit GGUFs are 156.8 GB (UD-IQ4_XS) to 199.7 GB (UD-Q4_K_XL); at 96 GB only ~1-bit files (93.1-97.6 GB) come close -> reword to "192 GB only (UD-IQ4_XS)" (https://huggingface.co/api/models/unsloth/GLM-5.3-Flash-GGUF/tree/main?recursive=true)
- SPEC §5 T1: "Qwen3.8-27B (4-bit)" on "one GPU, 12–24 GB" -> the official Q4_K_M GGUF is 19.0 GB before KV cache, so 4-bit fits 24 GB cards, not 12-16 GB -> state "24 GB" for this candidate (https://huggingface.co/api/models/ggml-org/Qwen3.8-27B-GGUF/tree/main?recursive=true)
- SPEC §5 T2: "Qwen3.8-Flash-Next (~111 GB Q4 GGUF)" -> size confirmed (UD-Q4_K_XL 111.3 GB, UD-IQ4_XS 93.7 GB), but the license is `qwen-community-1.0`, not Apache-2.0 (commercial model-as-a-service and AI work-assistant businesses need a separate license), and MLX-LM has no `qwen4_exp` module -> keep; record the license and "no MLX-LM" in the registry entry (https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/LICENSE, https://github.com/ml-explore/mlx-lm/tree/main/mlx_lm/models)
- SPEC §5 T3: "DeepSeek V4.1 Flash" -> `deepseek-ai/DeepSeek-V4.1-Flash` exists (MIT, multimodal, 1M context), but llama.cpp and transformers have no V4.1 architecture today and vLLM needs `--tokenizer-mode deepseek_v41` -> engine recipes for vLLM, SGLang and MLX-LM only (https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md, https://raw.githubusercontent.com/ggml-org/llama.cpp/master/src/llama-arch.cpp, https://github.com/huggingface/transformers/tree/main/src/transformers/models)
- SPEC §5 registry fields ("license"): GLM-5.3 (`glm-5.3`) and Kimi K3 (`kimi-k3`) use custom licenses with revenue thresholds; MiniMax-M2.7 is non-commercial; MiniMax-M3 (`minimax-community`) allows commercial use only with attribution and a notice, or written authorization above $20M yearly revenue -> add `commercial_use: permitted | conditional | prohibited` beside `license` (https://huggingface.co/zai-org/GLM-5.3/raw/main/LICENSE, https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE, https://huggingface.co/MiniMaxAI/MiniMax-M2.7/raw/main/LICENSE, https://huggingface.co/MiniMaxAI/MiniMax-M3/raw/main/LICENSE)
- SPEC §4.3 steps 1 and 3: "chat template" and "parser by hub ID plus chat-template hash" -> DeepSeek V3.2, V4, V4-Flash-0731, V4-Pro-0813 and Kimi K3 ship no Jinja chat template (Python encoders `encoding/encoding_dsv32.py`, `encoding/encoding_dsv4.py`, `encoding_k3.py`; vLLM uses `--tokenizer-mode deepseek_v32|deepseek_v4|deepseek_v41`) -> hash the encoder script when no template exists, record the tokenizer mode in the engine recipe, and ship native prompt renderers for these families (https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/raw/main/README.md, https://huggingface.co/moonshotai/Kimi-K3/raw/main/tokenization_kimi.py)
- SPEC §4.4: syntax catalog "(harmony…, JSON-in-tags, XML-style function/parameter tags, key/value-tag pairs, special-token sections, bracketed name/arguments markers, pythonic call lists and non-JSON argument syntaxes)" -> two formats in use fit none of these classes: Kimi K3 control-token markup with typed arguments (F11) and MiniMax-M3 namespaced nested elements (F8) -> add "control-token markup" and "namespaced nested-element XML" (https://huggingface.co/moonshotai/Kimi-K3/raw/main/encoding_k3.py, https://huggingface.co/MiniMaxAI/MiniMax-M3/raw/main/chat_template.jinja)
- SPEC §4.5: "`effort: none | minimal | low | medium | high | xhigh | max`" -> MiniMax-M3 (`thinking_mode` adaptive, the default) and MiniCPM5 (`enable_thinking` unset) let the model decide whether to think; DeepSeek V4.1 accepts integers 1-100; Seed-OSS and LongCat take token budgets (`thinking_budget`) -> add `auto` (model decides) and an optional numeric budget to the stored mechanism (https://huggingface.co/MiniMaxAI/MiniMax-M3/raw/main/chat_template.jinja, https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/chat_template.jinja, https://huggingface.co/ByteDance-Seed/Seed-OSS-36B-Instruct/raw/main/chat_template.jinja)
- SPEC §4.5: "A per-model retention policy (`drop | keep-since-last-user | keep-for-tool-turns | keep-all`)" -> templates apply retention themselves through kwargs whose defaults depend on the model and on whether tools are present (`preserve_thinking`: Qwen3.6 opt-in, Qwen3.8 and Step-5 on; `clear_thinking`: GLM; `preserved_thinking`: Hy3/Hy4 on only with tools; `drop_thinking`: DeepSeek V3.2 and V4.1, V4.1 off when tools are present; `save_history_reasoning_content`: LongCat; Kimi K3 requires full replay) -> store the retention kwarg in the profile and always pass it explicitly (https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/chat_template.jinja, https://huggingface.co/tencent/Hy3/raw/main/chat_template.jinja, https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/chat_template.jinja, https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md)

#### A07: Open-weight model families, part 2 (incl. harmony)

- SPEC §4.5: "Map effort controls to `effort: low | medium | high | max` per endpoint" -> controls in use today include an explicit off level and levels above high:
  - llama-server: `--reasoning-effort` minimal/low/medium/high/xhigh/max; request `reasoning_effort: none` disables reasoning;
  - vLLM: `reasoning_effort: none` maps to `enable_thinking=false`;
  - Muse: only low/medium/high/xhigh, other values error;
  - Mistral Small 4 and Medium 3.5: only `none`/`high`;
  - gpt-oss: low/medium/high, no off level;
  - Granite 4.2 and Nemotron 3.5 Bolt use template kwargs (`low_effort`, `medium_effort`).

  -> Use `effort: none | minimal | low | medium | high | xhigh | max`. Store each endpoint's supported subset and mechanism (request field, template kwarg, or system-prompt line) in the capability profile. Clamp to the nearest supported level and report the clamp. (https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md, https://docs.vllm.ai/en/latest/features/reasoning_outputs.html, https://huggingface.co/meta-models/Muse-Spark-Open-816B-A42B, https://huggingface.co/mistralai/Mistral-Small-4-119B-2603)
- SPEC §4.5: "Map reasoning output (dedicated fields, think tags, harmony channels) into one internal field" -> field names differ and retention rules differ by model.
  - Field names: `reasoning` in vLLM (renamed from `reasoning_content`), `reasoning_content` in llama.cpp, `message.thinking` in Ollama, `thinking` in the gpt-oss hub template.
  - Models that need their own prior reasoning sent back within tool loops: gpt-oss tool-call turns, Ministral 3 Reasoning, Trinity Large Thinking, Laguna, North-Mini-Code.
  - Models whose templates strip prior reasoning by default: Gemma 4, Granite 4.2.

  -> Add a per-model reasoning-retention policy (`drop | keep-since-last-user | keep-for-tool-turns | keep-all`) to registry entries and to `sy probe`. Codecs accept all of these field names. The cross-model rule is unchanged: reasoning is replayed only to the same model. (https://docs.vllm.ai/en/latest/features/reasoning_outputs.html, https://huggingface.co/openai/gpt-oss-20b/raw/main/chat_template.jinja, https://huggingface.co/mistralai/Ministral-3-8B-Reasoning-2512, https://huggingface.co/google/gemma-4-E4B-it/raw/main/chat_template.jinja)
- SPEC §4.4: "per-family parsers for the tool-call formats in use today (for example the harmony format used by gpt-oss, XML-tag formats, JSON-in-tags formats, plus whatever Phase 0 finds)" -> the formats in use also include:
  - pythonic lists (Llama 4, LFM2, OLMo 3);
  - Mistral `[TOOL_CALLS]NAME[ARGS]{...}`;
  - Gemma 4 `<|tool_call>call:NAME{...}` with `<|"|>` string delimiters (not JSON);
  - harmony-like recipient formats with XML arguments (Muse).

  Parser choice is per model version, not per family: Granite 4.1 uses JSON-in-tags and 4.2 uses XML; Trinity Mini uses `hermes` and Trinity Large Thinking uses `qwen3_coder`; Nemotron 3.5 and Granite 4.2 reuse the Qwen3-Coder parser. -> Key parser selection on hub ID + chat-template hash. Keep a format catalog (harmony, harmony dialect, Mistral, Gemma 4, pythonic, JSON-in-tags, XML-function, Command) with a conformance fixture per format. (https://huggingface.co/ibm-granite/granite-4.2-30b, https://huggingface.co/ibm-granite/granite-4.1-8b, https://huggingface.co/arcee-ai/Trinity-Mini, https://huggingface.co/arcee-ai/Trinity-Large-Thinking)
- SPEC §4.3 step 3: "Select the tool-call parser from the chat template and probe results" -> engines differ:
  - llama.cpp has Jinja on by default and derives the parser from the template (differential autoparser plus dedicated handlers in `common/parsers/`);
  - SGLang accepts `--tool-call-parser auto` and `--reasoning-parser auto`;
  - vLLM needs explicit names;
  - some models need out-of-tree plugins (Nemotron 3.5 Bolt `ultra_v3_reasoning_parser.py`) or forks (Apertus 1.5 vLLM image, Laguna llama.cpp branch).

  -> Engine recipes in the registry carry parser flags, plugin files, fork/branch and minimum engine version. Prefer engine-side detection where it exists, and confirm it with `sy probe`. (https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/chat.h, https://huggingface.co/ibm-granite/granite-4.2-30b, https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-BF16, https://huggingface.co/swiss-ai/Apertus-v1.5-8B, https://huggingface.co/poolside/Laguna-S-2.1)
- SPEC §4.1: "Conversion between any two formats is lossless for messages and tool calls" -> for harmony models, chat-completions cannot carry channel metadata, preambles, or built-in tool calls (vLLM serves built-in tools only on `/v1/responses`). The gpt-oss hub template also re-renders past calls as `commentary json` without `<|constrain|>` and stamps the current date, so the rendered prefix differs from the generated tokens. -> Add harmony channel and recipient fields to the internal representation. Report a loss whenever the outbound format cannot express them. Render history with the harmony renderer, or a byte-identical equivalent, with the date pinned per session so prefix caches survive. (https://docs.vllm.ai/projects/recipes/en/latest/OpenAI/GPT-OSS.html, https://huggingface.co/openai/gpt-oss-20b/raw/main/chat_template.jinja, https://developers.openai.com/cookbook/articles/openai-harmony)
- SPEC §4.4 fallback: "force a JSON action object with grammar or JSON-Schema constrained decoding" -> for reasoning models the constraint must start after the reasoning block.
  - llama.cpp's gpt-oss handler applies the schema after `<|channel|>final` and uses lazy grammar triggers.
  - vLLM documents structured-output support per reasoning parser.
  - gpt-oss and LFM2.5-24B-A2B cannot turn reasoning off.

  -> Specify trigger-based (lazy) grammars or reasoning-aware structured outputs. Add a probe case that checks the reasoning still parses under constraints. (https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/parsers/gpt-oss.cpp, https://docs.vllm.ai/en/latest/features/reasoning_outputs.html, https://huggingface.co/LiquidAI/LFM2.5-24B-A2B)
- SPEC §5 T0: "Gemma 4 E2B / E4B" -> the models exist (`google/gemma-4-E2B-it`, `google/gemma-4-E4B-it`, `Apache-2.0`, 128K). E2B has 5.1B parameters with embeddings (2.3B effective) and E4B has 8B (4.5B effective). -> Size RAM and VRAM from total parameters, and record effective parameters separately. (https://huggingface.co/google/gemma-4-26B-A4B-it)
- SPEC §5 (addition, not an error; gpt-oss-120b, Devstral Small 2 (24B) and Phi-4-mini check out) -> new releases in this scope that fit the tiers:
  - T1 (around 30B-A3B): Gemma 4 26B-A4B, North-Mini-Code-1.0 (`Apache-2.0`), Nemotron 3.5 Lightning, Laguna XS 2.1 (33B-A3B), Muse Glimmer 30B (`Apache-2.0`), Nemotron 3.5 Bolt 24GB (native 2-bit);
  - T2: Mistral Small 4 119B-A6B (`Apache-2.0`, 256K), Laguna S 2.1 118B-A8B (OpenMDW-1.1, 1M), Nemotron 3 Super 120B-A12B.

  -> Add these as candidates (verify fit). Record Devstral 2 123B as modified `MIT` with a $20M monthly-revenue cap if it is added. (https://huggingface.co/mistralai/Mistral-Small-4-119B-2603, https://huggingface.co/poolside/Laguna-S-2.1, https://huggingface.co/CohereLabs/North-Mini-Code-1.0, https://huggingface.co/meta-models/Muse-Glimmer-30B, https://huggingface.co/mistralai/Devstral-2-123B-Instruct-2512/raw/main/LICENSE)

#### A08: Default model candidates (SPEC §5)

- SPEC §5: "Phi-4-mini" -> exists as `microsoft/Phi-4-mini-instruct` (released February 2025, MIT); card MMLU-Pro 52.8 vs 69.6 (Qwen3-4B-Instruct-2507 card) and 69.4% (Gemma 4 E4B card) at similar Q4_K_M size -> drop from T0 defaults; leave user-addable (https://huggingface.co/microsoft/Phi-4-mini-instruct/raw/main/README.md)
- SPEC §5: "Qwen3.5 4B / 2B / 0.8B" -> exist (Apache-2.0) but are vision-language models; 4B thinks by default; 2B non-thinking card scores (IFEval 61.2, MMLU-Pro 55.3) trail Qwen3-4B-Instruct-2507 (83.4, 69.6) -> keep as alternates; registry sets `enable_thinking=false` for router roles; router default = Qwen3-4B-Instruct-2507 (https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md)
- SPEC §5: "Qwen3.8-27B (4-bit)" under T1 "one GPU, 12-24 GB" -> 4-bit GGUF is 17.56 GB (UD-Q4_K_XL) to 18.97 GB (Q4_K_M), so it fits 24 GB only -> "4-bit on 24 GB; UD-Q3_K_XL (13.15 GB) or partial offload on 12-16 GB" (https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF/tree/main?recursive=true)
- SPEC §5: "Devstral Small 2 (24B)" -> full ID `mistralai/Devstral-Small-2-24B-Instruct-2512`; context 256k (card) vs 393,216 (config) -> store the full ID and the card value (https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512/raw/main/README.md)
- SPEC §5: "small-active MoE models with expert offload" -> unnamed -> name `Qwen/Qwen3.6-35B-A3B` (UD-Q4_K_M 22.13 GB; card SWE-bench Verified 73.4) with `openai/gpt-oss-20b` (MXFP4 12.11 GB) as other-family option; offload via llama.cpp `--n-cpu-moe` (https://huggingface.co/Qwen/Qwen3.6-35B-A3B/raw/main/README.md)
- SPEC §5: "Qwen3.8-Flash-Next (~111 GB Q4 GGUF)" -> size correct (UD-Q4_K_XL 111.33 GB) but license is Qwen Community License 1.0: separate license required for commercial use by "Model as a Service" or "AI Work Assistant" (AI-assisted coding) businesses; no Ollama tag -> add a license flag and engine = llama.cpp in its registry entry; do not pull it silently (https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/LICENSE)
- SPEC §5: "GLM-5.3-Flash (tight at 4-bit)" -> 320B total / 18B active; 4-bit GGUF 156.82 GB (UD-IQ4_XS) to 199.71 GB (UD-Q4_K_XL); llama.cpp support merged 2026-09-30 (PR #27773); Ollama `:cloud` only -> "fits 192 GB at IQ4_XS; 96-128 GB needs UD-Q2_K_XL (108.72 GB) plus expert offload to RAM" (https://huggingface.co/api/models/unsloth/GLM-5.3-Flash-GGUF/tree/main?recursive=true)
- SPEC §5: "MiMo-V2.6-Pro" -> no repo with that ID; checkpoints are `XiaomiMiMo/MiMo-V2.6-Pro-RL` and `XiaomiMiMo/MiMo-V2.6-Pro-MOPD` (later upgrade) -> use `XiaomiMiMo/MiMo-V2.6-Pro-MOPD` (https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Pro-MOPD/raw/main/README.md)
- SPEC §5: "DeepSeek V4.1 Flash" -> exists (`deepseek-ai/DeepSeek-V4.1-Flash`, MIT) but llama.cpp support is an open PR (#28696), Ollama has only a `:cloud` tag, and the only GGUF targets another engine -> mark "no supported local engine yet"; exclude from default pulls until the PR merges (https://api.github.com/search/issues?q=repo:ggml-org/llama.cpp+%22DeepSeek+V4%22+is:pr&per_page=10)
- SPEC §5: "GLM-5.3, Kimi K3" (T3) -> both exist; licenses are custom (GLM-5.3 License, Kimi K3 License) with revenue/MAU conditions; Ollama library offers only `:cloud` tags, which are remote and conflict with zero-egress -> registry license field carries these terms; local path = llama.cpp GGUF (https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE)
- SPEC §5.2: "Respects a footprint cap (default 8 GB)" -> proposed set is 4.78 GB on disk but ~7.2 GB resident at 8K ctx per model -> define the cap as disk size and add a RAM budget (per-role ctx, lazy start, LRU) (https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/raw/main/config.json)
- SPEC §5: "Search ranking: T0 embeddings + ripgrep" -> Ollama API has no rerank endpoint; llama.cpp has `/v1/rerank`; Qwen3-Reranker-0.6B card shows no gain on code (MTEB-Code 73.42 vs 75.41 embedding-only) -> keep the reranker optional and llama.cpp-only (https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/raw/main/README.md)

#### A09: Harnesses

- SPEC §12 (item 4): "Oh My Pi / Pi adapter: TypeScript extension registering Switchyard as a provider and a model-role resolver." -> Pi has no model roles; its per-request hook is `pi.registerVirtualModel({ provider, id, route(request, ctx) })` next to `pi.registerProvider()`. Oh My Pi has config-level `modelRoles` plus hooks (`before_subagent_spawn` returns a model per spawned child, `before_provider_request` rewrites payloads) but documents no role-resolver or virtual-model API. The two have diverged (separate npm packages, Node >= 22.19 vs Bun >= 1.3.14, `~/.pi/agent/models.json` vs `~/.omp/agent/models.yml`). -> Split into two thin adapters on a shared core: "Pi: provider + virtual model whose `route()` calls the router (in-process step-level routing)" and "Oh My Pi: provider + generated `modelRoles` + `before_subagent_spawn`; mid-turn switching through `sy proxy`". (https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/virtual-models.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/extensions.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/models.md)
- SPEC §22 (Harnesses row): "Oh My Pi and Pi (model roles, hash-anchored edits, LSP tools, extensions)" -> model roles, hashline edits and the `lsp` tool are Oh My Pi features; Pi ships `read`, `bash`, `edit`, `write` by default and skips sub-agents and plan mode; Pi's repo moved (https://github.com/earendil-works/pi) and its npm package was renamed (https://registry.npmjs.org/@earendil-works%2Fpi-coding-agent); the former package stopped at 0.73.1 on 2026-05-07. -> Attribute features per project and record the new repo and package names. (https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/README.md ; https://registry.npmjs.org/@mariozechner%2Fpi-coding-agent ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/README.md)
- SPEC §12 (item 3): "Claude Code adapter: headless runs pointed at the proxy, model slots mapped by the router." -> The mechanism exists (`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`; slot variables `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`), but: the vendor docs state that routing Claude Code to non-Claude models through any gateway is not supported; Claude Code speaks only the messages format and needs exact SSE behavior (full event sequence, `ping` keep-alives); unrecognized model IDs are assumed to have a 200K window unless `CLAUDE_CODE_MAX_CONTEXT_TOKENS` is set; gateway model discovery drops IDs without the vendor's model-family or company name. -> Add these as adapter requirements; set `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` so `sy proxy` receives `x-claude-code-request-class` (main / subagent / workflow / compaction / auxiliary) and agent IDs for step-level routing; word the claim as "works through `sy proxy`; not supported upstream". (https://code.claude.com/docs/en/llm-gateway.md ; https://code.claude.com/docs/en/llm-gateway-protocol.md ; https://code.claude.com/docs/en/model-config.md)
- SPEC §12 (item 5): "OpenCode and Kimi Code adapters: subprocess adapters with config generation." -> Kimi Code is now a TypeScript CLI, version 2.2.0 (https://registry.npmjs.org/@moonshot-ai%2Fkimi-code, https://github.com/MoonshotAI/kimi-code); the Python `kimi-cli` and `kimi-code` PyPI packages are archived. Both harnesses take generated config without touching user files (`OPENCODE_CONFIG_CONTENT`; `KIMI_MODEL_*` env or `KIMI_CODE_HOME` + `config.toml`), and both are ACP agents (`opencode acp`, `kimi acp`). -> Specify env-based or isolated-dir config generation, pin the new Kimi Code package, and allow driving both through the ACP client (item 6). (https://pypi.org/pypi/kimi-cli/json ; https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/docs/en/configuration/env-vars.md ; https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/cli.mdx)
- SPEC §12 (item 6): "Agent Client Protocol client: generic adapter for any ACP-capable agent (verify spec)." -> Verified: JSON-RPC 2.0 with the agent as a subprocess; baseline `initialize`, `authenticate`, `session/new`, `session/prompt`, notifications `session/update` and `session/cancel`; client-side `session/request_permission`, optional `fs/read_text_file`, `fs/write_text_file`, `terminal/*`; per-session model choice through a config option with category `model`, set by `session/set_config_option`. Native ACP among our harnesses: Oh My Pi, OpenCode, Kimi Code; Claude Code and Pi only through third-party adapters (`@agentclientprotocol/claude-agent-acp` 0.91.0, Apache-2.0; `pi-acp` 0.0.34, MIT). -> Replace "(verify spec)" with these method names; use `session/set_config_option` for model selection when offered, otherwise route through `sy proxy`. (https://agentclientprotocol.com/protocol/overview ; https://agentclientprotocol.com/protocol/session-config-options ; https://registry.npmjs.org/@agentclientprotocol%2Fsdk)
- SPEC §1: "Already exists elsewhere: role-based model assignment in harnesses, manual mid-session model switching, request-type routing proxies for coding agents, ..." -> Also existing today: per-request router hooks inside a harness (Pi virtual models "route by task, cost, or conversation state"), automatic category-to-model routing for delegated subtasks (oh-my-openagent categories), and per-request class hints for gateway routers (Claude Code hint headers). -> Add these to the list and keep novelty item 1 focused on "a classifier plus a learned policy" rather than step-level routing as such. (https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/virtual-models.md ; https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/docs/guide/agent-model-matching.md ; https://code.claude.com/docs/en/llm-gateway-protocol.md)
- SPEC §22 (Harnesses row): "OpenCode and oh-my-openagent" -> oh-my-openagent is under SUL-1.0 (use limited to internal business, non-commercial or personal purposes) and now ships two editions (OpenCode plugin; standalone `omo` from `omo-ai`); telemetry is on by default. -> Mark it ideas-only (no code reuse, no adapter). (https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/LICENSE.md ; https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/docs/reference/cli.md ; https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/docs/guide/telemetry.md)
- SPEC §12 (adapters, no egress handling stated): harnesses make their own network calls by default -> each adapter must set the harness's egress switches in offline mode: Claude Code `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` (fast-mode and WebFetch domain checks still go direct), OpenCode `OPENCODE_DISABLE_AUTOUPDATE` and `OPENCODE_DISABLE_MODELS_FETCH`, Pi `PI_OFFLINE` and `PI_SKIP_VERSION_CHECK`, Kimi Code `KIMI_DISABLE_TELEMETRY`; the audit log should list residual calls. (https://code.claude.com/docs/en/llm-gateway-connect.md ; https://code.claude.com/docs/en/llm-gateway-protocol.md ; https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/cli.mdx ; https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/environment-variables.md ; https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/docs/en/configuration/env-vars.md)
- SPEC §19: "sy proxy [--port 4000]" -> Oh My Pi treats `http://localhost:4000/v1` as its LiteLLM fallback (`LITELLM_BASE_URL`), so a user running both collides on 4000 -> pick another default port or document the overlap. (https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/environment-variables.md)
- SPEC §12 (item 1) and TK5: "`edit(hash-anchored)`" -> the reference implementation (Oh My Pi hashline) now uses a per-file 4-hex snapshot tag `[PATH#TAG]` plus line-number operations (`PUT`, `CUT`, `REM`, `MV`), and its separate `readHashLines` setting was removed -> define Switchyard's own edit format in the SPEC and cite Oh My Pi (MIT) as prior art. (https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/edit/hashline-compact.md ; https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/settings.md)

#### A10: Multi-agent orchestration

- SPEC §12 item 6: "Agent Client Protocol client: generic adapter for any ACP-capable agent (verify spec)" -> ACP is JSON-RPC 2.0 over stdio with `protocolVersion` 1. Schema 1.25.0 is current (2026-10-09) and v2 is in alpha. The Python SDK is `agent-client-protocol` (import `acp`, Python >=3.10,<3.15) -> replace "(verify spec)" with "ACP v1 (schema 1.25.x) over stdio via the `agent-client-protocol` SDK; stable methods only; feature-detect at `initialize`; track v2" (https://agentclientprotocol.com/protocol/transports , https://pypi.org/pypi/agent-client-protocol/json)
- SPEC §12 items 3 and 5: "Claude Code adapter: headless runs pointed at the proxy" / "OpenCode and Kimi Code adapters: subprocess adapters with config generation" -> OpenCode and Kimi CLI are listed as ACP agents, and Claude Agent, Codex CLI and Pi are listed through ACP adapters -> drive these adapters through the item 6 ACP client where available, keep config generation only to point model endpoints at `sy proxy`, and confirm that "Kimi Code" is the listed Kimi CLI (https://agentclientprotocol.com/overview/agents)
- SPEC §22: "open-multi-agent (task DAG)" -> the repo is now https://github.com/open-multi-agent/open-multi-agent and the npm package is `@open-multi-agent/core` (`@jackchen_me/open-multi-agent` is deprecated) -> name the canonical repo and package (https://registry.npmjs.org/@jackchen_me/open-multi-agent/latest)
- SPEC §22: "Conductor (deterministic YAML routes)" -> at least 4 projects are named Conductor. The YAML one is https://github.com/microsoft/conductor (`conductor-cli`, MIT, Python >=3.12) -> pin that URL in the row (https://raw.githubusercontent.com/microsoft/conductor/main/pyproject.toml)
- SPEC §1: the "already exists" list does not cover rule-based model routing in orchestrators -> open-multi-agent ships `modelRouting.rules` (first match wins, error-driven `fallback`), decided per orchestration call. It is not per step and not learned -> add "rule-based per-task model routing with fallback chains in orchestration frameworks" to the list. Items 1 and 7 stay new. (https://raw.githubusercontent.com/open-multi-agent/open-multi-agent/main/docs/model-routing.md)
- SPEC §21: "tests/ … conformance/ (mock servers per API format)" -> ACP is a two-way stdio protocol in which the agent calls back for permissions, files and terminals, and the codec mocks do not cover it -> add a mock ACP agent under `tests/conformance/acp/` for `workers/acp` (https://agentclientprotocol.com/protocol/overview)

#### A11: Context and tokens

- SPEC §9: "Target ≤ 1,500 tokens, measured with the receiving model's tokenizer" -> not always possible: the Ollama API docs list no tokenize endpoint, its Messages-format layer lists `/v1/messages/count_tokens` as not supported, and tiktoken downloads its BPE file on first use -> add a tokenizer resolution order (local `tokenizer.json`, llama.cpp `/tokenize`, tiktoken with pre-seeded cache, flagged estimate), record `tokenizer` and `exact` in `provenance`, and add `context/tokens` to §21 (https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md, https://docs.ollama.com/api/anthropic-compatibility, https://raw.githubusercontent.com/openai/tiktoken/main/tiktoken/load.py)
- SPEC §13 TK2: "tree-sitter symbol graph ranked by references, token-budgeted (default 2k), cached per commit" -> the reference design caches tags per file (mtime-validated) and re-renders per request; worktrees hold uncommitted edits; tree-sitter-language-pack >= 1.0 downloads parsers on first use -> "personalized PageRank over def/ref edges; tags cached per file content hash; core grammars from pinned per-language wheels (offline); language pack optional and prefetched" (https://raw.githubusercontent.com/Aider-AI/aider/main/aider/repomap.py, https://docs.tree-sitter-language-pack.xberg.io/concepts/download-model/)
- SPEC §13 TK11: "optional, docs only, never code (study LLMLingua)" -> llmlingua 0.2.2 (2024-04-09) is the latest release; the default LLMLingua model is a 7B causal LM with `device_map="cuda"`; only the LLMLingua-2 small checkpoint (177M params, 709.4 MB fp32, Apache-2.0) fits a CPU T0 budget -> "optional extra, LLMLingua-2 small only, local model path, off by default, bench-gated" (https://pypi.org/pypi/llmlingua/json, https://raw.githubusercontent.com/microsoft/LLMLingua/main/llmlingua/prompt_compressor.py, https://huggingface.co/api/models/microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank?blobs=true)
- SPEC §15: "`--offline` or `privacy.mode = "local"` blocks every non-local connection at `switchyard.net`" -> context libraries open their own connections outside `switchyard.net`: tree-sitter-language-pack 1.x (parser bundles), tiktoken (BPE files), multilspy and SolidLSP (language servers), tokenizers `from_pretrained` and LLMLingua (model hub) -> add an install-time prefetch step, forbid lazy downloads in offline mode (cache dirs under `.switchyard/`), and test context services with networking disabled (https://docs.tree-sitter-language-pack.xberg.io/concepts/download-model/, https://raw.githubusercontent.com/openai/tiktoken/main/tiktoken/load.py, https://raw.githubusercontent.com/microsoft/multilspy/main/README.md, https://oraios.github.io/serena/01-about/020_programming-languages.html)
- SPEC §12: "`lsp(defs|refs|hover|diagnostics)`" with no client specified -> serena-agent 2.0.0 is GPL-3.0-or-later as a whole (SolidLSP files remain MIT; no standalone package); multilspy auto-downloads server binaries -> specify "thin client on pygls + lsprotocol; user-installed servers; launch commands in registry TOML" (https://raw.githubusercontent.com/oraios/serena/main/LICENSE, https://pypi.org/pypi/serena-agent/json, https://raw.githubusercontent.com/microsoft/multilspy/main/README.md)
- SPEC §22: "small-model "contract" prompting (e.g. simplicio, which claims large gains for tiny models)" -> current simplicio-cli 0.18.16 does not call models; the tiny-model gains in the 0.18.14 README were regex-scored structure checks, and in its execution bench the 1B and E4B models scored 0% with and without the contract -> reword to "contract prompting (simplicio; earlier claims regex-scored, not reproduced)" and test it with the arms above (https://pypi.org/pypi/simplicio-cli/json, https://pypi.org/pypi/simplicio-cli/0.18.14/json)

#### A12: Testing and verification

- SPEC §6.1: "screenshot diff with Playwright" -> Python Playwright PageAssertions are only `to_have_title`, `to_have_url` and `to_match_aria_snapshot`; pytest-playwright only captures screenshots (`--screenshot`) -> "build check plus a headless Playwright smoke test (ARIA snapshot); screenshots on failure; pixel diff, if wanted, is a separate step" (https://playwright.dev/python/docs/api/class-pageassertions, https://playwright.dev/python/docs/test-runners)
- SPEC §6.2: "pair `.ipynb` with py:percent via jupytext; agents edit the paired script" -> `--sync` picks the source by timestamp; inputs come from the newest file and outputs from `.ipynb` -> "global `jupytext.toml` (`formats = "ipynb,py:percent"`); after an agent edit the gate runs `jupytext --to ipynb --update <script>.py`" (https://raw.githubusercontent.com/jupytext/jupytext/main/website/src/content/docs/using/cli.md)
- SPEC §8.2: "pytest-testmon or a coverage map, verify" -> testmon 2.2.0 (MIT) allows pytest 9 and supports xdist, but does not track non-Python files and offers cloud storage (`--tmnet`) -> "impacted tests via `pytest --testmon` with a per-worktree `TESTMON_DATAFILE`; any non-`.py` change (data, configs, `science.toml`, `tests/golden/**`, lockfile) forces the full run; `--tmnet` is never used; the full suite runs at the final gate" (https://testmon.org/)
- SPEC §15 and §18.5 C-006: "blocks every non-local connection" / "socket-level guard plus OS-level network isolation in CI" -> audit hooks "are not suitable for implementing a sandbox" and miss child processes and native code; OS isolation on hosted CI is verified only on Linux runners (`docker --network none`, netns via sudo); no containers on hosted macOS and no Linux containers on hosted Windows -> "Linux: full flow inside `--network none` plus the audit-hook recorder; macOS and Windows: recorder only; scope wording says OS-enforced on Linux CI" (https://docs.python.org/3/library/sys.html, https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- SPEC §18.5 C-006: "except to local endpoints" (undefined) -> Docker `none` networking has IPv4 loopback only, no IPv6 loopback -> "local = 127.0.0.0/8, ::1, Unix sockets; CI binds 127.0.0.1" (https://docs.docker.com/engine/network/drivers/none/)
- SPEC §18.5 C-007: "opens zero outbound connections" -> in-process observation cannot see child executables -> "evidence also lists every spawned process (`subprocess.Popen`, `os.posix_spawn`, `os.exec`, `_winapi.CreateProcess` events); the Linux run is OS-isolated" (https://docs.python.org/3/library/audit_events.html)
- SPEC §18.5 C-001: "in a container capped at 8 GB RAM with no GPU visible" -> containers on hosted CI work only on Linux runners (Docker container actions, job containers and service containers need Linux; no Docker on macOS images) -> "Linux x64 only: `--memory=8g --memory-swap=8g`, no `--gpus`; evidence records cgroup `memory.max`, `memory.swap.max`, `memory.peak` and no GPU device nodes; claim scope names Linux" (https://docs.docker.com/engine/containers/resource_constraints/, https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)
- SPEC §18.4 and §18.5 C-016: "CI runs `--quick` on Linux, macOS and Windows" -> `-latest` now resolves to `ubuntu-24.04`, `macos-26` (arm64, 3 vCPU / 7 GB) and `windows-2025` (Windows Server 2025); `macos-14` is deprecated -> "pin `ubuntu-24.04`, `macos-26` and `windows-2025`; record runner label and image version in `prove-report.json`; claim scope names OS version and arch" (https://github.com/actions/runner-images)

#### A13: Name and stack

- SPEC §0: "Working name: Switchyard. CLI: `sy`." -> The PyPI name `switchyard` is taken (1.0.1, an unrelated networking framework). npm `switchyard-ai` (bin `sy`) and an active Apache-2.0 LLM model-routing library named Switchyard (3,386 stars) target the same problem. -> Rename in the name ADR. Recommended: Stepswitch (CLI `ssw`/`stepswitch`). Runner-up: Shuntyard (keeps `sy`). Rename `switchyard.toml`, `.switchyard/` and `src/switchyard/` with it. (https://pypi.org/pypi/switchyard/json, https://registry.npmjs.org/switchyard-ai, https://api.github.com/search/repositories?q=switchyard+in%3Aname&sort=stars&order=desc&per_page=12)
- SPEC §19: "sy init ... sy audit" (every CLI example) -> No OS-level `sy` command was found (Debian, Ubuntu, Homebrew, Windows), but crates.io `sy` (file sync tool) and npm `switchyard-ai` (a same-domain router) both install a `sy` binary. -> Keep `sy` only if the new name keeps the s-y initials; otherwise rename the CLI with the project. (https://crates.io/api/v1/crates/sy, https://registry.npmjs.org/switchyard-ai)
- SPEC §24: "`uv tool install switchyard && sy init && sy run "..."`" -> This installs the unrelated 2020 `switchyard` package. Also, uv 0.13 downloads Python 3.15 by default, so every runtime dependency must install on 3.15 (PyYAML 6.0.3 has no cp315 wheels). -> Use the new distribution name, keep runtime dependencies to pure-Python or cp315-wheel packages, and test 3.15 in CI. (https://pypi.org/pypi/switchyard/json, https://raw.githubusercontent.com/astral-sh/uv/main/CHANGELOG.md, https://pypi.org/pypi/pyyaml/json)
- SPEC §24: "Docs site (mkdocs-material)." -> mkdocs-material has been in maintenance mode since 2025-11-11, with critical fixes promised for at least 12 months. MkDocs 2.0 pre-releases are incompatible with it. The successor, Zensical, is alpha. -> "Docs site: mkdocs-material >=9.7.5 (caps mkdocs below 2); Zensical migration decided by ADR." (https://squidfunk.github.io/mkdocs-material/blog/, https://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/, https://pypi.org/pypi/zensical/json)
- SPEC §21: "dashboard/ React + Vite" sits outside `src/switchyard/`, and no Node version is stated. -> `uv_build` wheels include only the module root and data directories, and `uv_build` runs no build scripts. Vite 8.3.4 requires Node ^20.19.0 or >=22.12.0, and Node 20 reached EOL on 2026-04-30 (this workstation runs 20.12.1). -> Build the dashboard into `src/<pkg>/api/static/` in CI before `uv build` (or use hatchling with a build hook), and state Node >=22.12 (Node 24 LTS recommended). (https://raw.githubusercontent.com/astral-sh/uv/main/docs/concepts/build-backend.md, https://registry.npmjs.org/vite, https://raw.githubusercontent.com/nodejs/Release/main/schedule.json)
- SPEC §21: `adapters/omp/` "TypeScript extension" -> TypeScript 7.0 (the current `latest`) has no compiler API until 7.1. -> Pin TypeScript 6.0 (`@typescript/typescript6`) for any adapter code or lint tooling that imports the compiler API. (https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/)
- SPEC §20: "`claims/claims.yaml` (committed): claims registry" (no YAML library stated) -> PyYAML drops comments on a round trip; ruamel.yaml 0.19.1 preserves them and ships a pure-Python wheel. -> Specify ruamel.yaml in round-trip mode for any tool that rewrites claims.yaml. (https://pypi.org/pypi/ruamel.yaml/json, https://pypi.org/pypi/pyyaml/json)
- SPEC §3: "one SQLite file per project at `.switchyard/state.db`" -> WAL does not work on network filesystems. The WAL-reset corruption bug affects SQLite 3.7.0 through 3.51.2 (fixed in 3.51.3). This workstation's Pythons ship SQLite 3.39.4 and 3.43.1. -> `sy doctor` checks the SQLite version and the volume type, falls back to `journal_mode=DELETE` on network drives, and docs recommend uv-managed Python. (https://www.sqlite.org/wal.html, https://raw.githubusercontent.com/astral-sh/python-build-standalone/main/pythonbuild/downloads.json)

## Verified during Phase 1

Facts checked while building Phase 1, each with its source and date. Where one supersedes an
earlier entry in this file, that entry is marked where it stands.

### Default CPU model files (registry entries)

Checked 2026-10-16 against the model hub API, then by downloading each file at the pinned revision
and hashing it locally; all three sha256 values matched the hub's LFS object IDs.

| Registry id | Hub repo @ revision | File | Bytes | sha256 | GGUF metadata (hub `gguf` field) |
|---|---|---|---|---|---|
| qwen3-4b-instruct-2507 | `unsloth/Qwen3-4B-Instruct-2507-GGUF` @ `a06e946bb6b655725eafa393f4a9745d460374c9` | `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` | 2,497,281,120 | `3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597` | arch qwen3, ctx 262,144, 4,022,468,096 params, eos `<\|im_end\|>` |
| qwen3-embedding-0.6b | `Qwen/Qwen3-Embedding-0.6B-GGUF` @ `370f27d7550e0def9b39c1f16d3fbaa13aa67728` | `Qwen3-Embedding-0.6B-Q8_0.gguf` | 639,150,592 | `06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439` | arch qwen3, ctx 32,768, 595,776,512 params, pooling type 3 (last token) |
| qwen2.5-coder-1.5b | `ggml-org/Qwen2.5-Coder-1.5B-Q8_0-GGUF` @ `8be1b8a895a84beea772817caaa71eba6b6e0d07` | `qwen2.5-coder-1.5b-q8_0.gguf` | 1,646,573,056 | `29871c94d15727a6e243f79a37113d4ae625a6215b5e800bf41a23af2da32832` | arch qwen2, ctx 32,768, 1,543,714,304 params |

Sources: https://huggingface.co/api/models/unsloth/Qwen3-4B-Instruct-2507-GGUF/tree/main ;
https://huggingface.co/api/models/unsloth/Qwen3-4B-Instruct-2507-GGUF ;
https://huggingface.co/api/models/Qwen/Qwen3-Embedding-0.6B-GGUF/tree/main ;
https://huggingface.co/api/models/Qwen/Qwen3-Embedding-0.6B-GGUF ;
https://huggingface.co/api/models/ggml-org/Qwen2.5-Coder-1.5B-Q8_0-GGUF/tree/main ;
https://huggingface.co/api/models/ggml-org/Qwen2.5-Coder-1.5B-Q8_0-GGUF. All three repos are
tagged `license:apache-2.0`.

### Ollama native API (supersedes parts of "Ollama native API" above)

Checked 2026-10-16 against the request and response structs on main
(https://raw.githubusercontent.com/ollama/ollama/main/api/types.go), https://docs.ollama.com/api/chat
and a local Ollama 0.34.4 server.

- Tool calls are `{"id", "function": {"index", "name", "arguments"}}`. Current servers send an
  `id` (`call_` plus 8 characters on 0.34.4) and a `function.index`; the documented examples and
  older servers have neither. Tool results are `tool` messages with `tool_name` and
  `tool_call_id`.
- Streams send each tool call whole, one call per line (0.34.4).
- `prompt_eval_count` is the whole prompt, cached tokens included; `prompt_eval_cached_count` is
  the cached part (0.34.4 reported 20 and 19 for a repeated prompt).
- `think`: the server accepts true, false, "low", "medium", "high" and "max" and resolves the
  levels per model (gpt-oss renders "max" as its "high"); "minimal" and "xhigh" get HTTP 400.
  A model without thinking accepts only `false`.

### llama.cpp server native API (adds to "llama.cpp native API" above)

Checked 2026-10-16 against `tools/server/README.md` and the server sources at tag v0.7.0
(build b11669) of the llama.cpp repository.

- `tokens_evaluated` is the whole prompt, cached tokens included (the same value the server
  reports as chat-completions `prompt_tokens`). `timings.cache_n` counts prompt tokens reused
  from the cache and `timings.prompt_n` the ones processed. `tokens_cached` is the slot's cache
  size after the request (prompt plus generated tokens), not a prompt count.
- `/completion` streams `data: {json}` events and ends with a `stop: true` event that carries
  `stop_type` (`eos`, `word`, `limit` or `none`), the counts and `timings`; there is no `[DONE]`
  line.

### CI actions (for `.github/workflows/prove.yml`)

Latest releases on 2026-10-16, pinned by commit:

| Action | Release | Commit |
|---|---|---|
| actions/checkout | v7.0.1 (2026-07-20) | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| actions/upload-artifact | v7.0.2 (2026-10-07) | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
| actions/download-artifact | v8.0.2 (2026-10-07) | `9000827ccba6bdab643e8b6fd33ac0654aef8333` |
| astral-sh/setup-uv | v10.3.0 (2026-10-09) | `1c37ad07a6a961277cf70c0d37d6f313000f5884` |
| actions/setup-node | v7.1.0 (2026-10-08) | not used yet |

Sources: https://api.github.com/repos/actions/checkout/releases/latest and the matching
`releases/latest` and `git/ref/tags/<tag>` endpoints for each action.

### Python packages considered

- tomlkit 0.15.2 was released on 2026-10-16, the day of the check (0.15.1 on 2026-07-17). Not
  adopted: Stepswitch writes only generated TOML and managed blocks, which a small writer covers
  (`stepswitch.tomlw`), so a new dependency, released the same day, was not worth adding.
  https://pypi.org/pypi/tomlkit/json
- For later phases: numpy 2.5.4 (2026-10-10) requires Python >= 3.12; pygls 2.1.1; lsprotocol
  2025.0.0; pytest-testmon 2.2.0; hypothesis 6.170.0; jupytext 1.19.6; playwright 1.64.0
  (2026-10-13). https://pypi.org/pypi/<name>/json

### Interpreter audit hooks on a host with endpoint-security software

Observed 2026-10-16 on a Windows 11 workstation: after `sys.addaudithook(...)` with any hook (even
`lambda e, a: None`), importing a module from cached bytecode fails with `AttributeError: 'bytes'
object has no attribute 'co_filename'` (or `TypeError: bad argument type for built-in
operation`), in every CPython installed there (3.10 to 3.14). The Python hook is never called; a
behaviour-monitor library that the endpoint-security software injects into every process is
loaded in the failing interpreters. The same code works in a clean `python:3.13-slim` container
(3.13.16). Consequence: the C-007 recorder falls back to wrapping the socket and process
functions on such hosts and records which mode ran (ADR 0013).

### Messages and generate-content formats (adds to "API formats" above)

Checked 2026-10-16 while building the two codecs.

- Messages format (https://platform.claude.com/docs/en/api/messages/create): usage carries
  `output_tokens_details.thinking_tokens`, and `output_tokens` stays the inclusive total; a
  refusal carries `stop_details` `{type: "refusal", category, explanation}` on the message and
  on `message_delta.delta`; tool IDs must match `^[a-zA-Z0-9_-]+$`; `temperature` ranges 0.0 to
  1.0; structured output is `output_config.format` `{type: "json_schema", schema}`.
- Mid-conversation `role: "system"` entries inside `messages` need no beta header but are not
  supported by every model, and a misplaced one is rejected with HTTP 400
  (https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages). The
  codec hoists them into the top-level system field and reports the change as approximated.
- Generate-content format (discovery document
  https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta, revision 20261015):
  `FunctionDeclaration.parametersJsonSchema` exists next to `parameters`, and
  `GenerationConfig.responseJsonSchema` exists (marked unverified above; now verified);
  `thinkingLevel` values are uppercase on the wire (MINIMAL, LOW, MEDIUM, HIGH);
  `totalTokenCount` = prompt + tool-use prompt + thoughts + candidates, and `promptTokenCount`
  already includes cached content; `stopSequences` takes at most 5 entries;
  `FunctionResponse` also has `willContinue` and `scheduling`. Function calls and responses are
  matched by `id`; a thought signature sits on the first function-call part, otherwise on the
  last part, and streams may put it on a final empty-text part
  (https://ai.google.dev/gemini-api/docs/generate-content/function-calling ;
  https://ai.google.dev/gemini-api/docs/generate-content/structured-output ;
  https://ai.google.dev/gemini-api/docs/generate-content/thinking).
- Generate-content errors are `{"error": {code, message, status, details[]}}`; each `details`
  entry has an `@type`, and the retry-info detail carries `retryDelay` as a duration string
  such as "13s". Status names map to HTTP codes (RESOURCE_EXHAUSTED 429, UNAVAILABLE 503,
  DEADLINE_EXCEEDED 504, UNAUTHENTICATED 401, UNIMPLEMENTED 501)
  (https://ai.google.dev/gemini-api/docs/api-errors ; https://google.aip.dev/193 ;
  https://protobuf.dev/programming-guides/json/).

### llama.cpp release b11669 (adds to "llama.cpp" above)

Checked 2026-10-16 against https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b11669
(36 assets, each with a `sha256:` digest; a prerelease published 2026-10-15; target commit
`620d2225cc436ab0b001e3200e590e3b286ef9ab`, also named by the v0.7.0 `nightly-tag.txt`), the
b11669 `tools/server/README.md` and the server binary itself.

- CPU assets: `llama-b11669-bin-win-cpu-x64.zip` (19,537,886 bytes, sha256
  `37b3efcf962847ca869745fd4ccf7f5e7e8dacfeff9fa3523b4ca8cdc66d632b`),
  `llama-b11669-bin-ubuntu-x64.tar.gz` (sha256
  `cf07c1c112d709bd67e522017471de57ed0ec8bea031d732c5422351c07f65ac`) and
  `llama-b11669-bin-macos-arm64.tar.gz` (sha256
  `dbed55493d72bc1ba2babc8770d669c2289b5bc4ee9fb2a4dfc158d45b7d13fd`); all three were downloaded
  and their hashes matched. CUDA builds exist for Windows (12.4, 13.4) and Linux (12.8, 13.4)
  with separate `cudart-*` runtime archives, plus Vulkan, ROCm and arm64 builds.
- Layouts: Windows zips are flat; Linux and macOS tarballs sit under `llama-b11669/` with
  relative symlinks.
- `llama-server --version` prints to stderr `version: 0.7.0-dev (build 11669, commit 620d2225c)`
  and `built with <compiler> for <target>`; older builds print `version: N (H)`.
- Flags used: `--host` (default 127.0.0.1), `--port`, `--api-key` (or the `LLAMA_API_KEY`
  environment variable), `--metrics`, `--slot-save-path`, `--alias`, `--offline`,
  `--ctx-size`, `--parallel`, `--kv-unified`, `--embedding`, `--pooling`, `--ubatch-size`,
  `--device none` (no offload). `/health` is public and returns 503 `Loading model` until the
  model is loaded, then 200; with a key set, other routes return 401 without it, and an
  `X-Api-Key` header also works. A port that is taken gives "couldn't bind HTTP server socket"
  and exit code 1. `/metrics` counts prompt tokens reused from the cache in
  `llamacpp:prompt_tokens_cached_total` (corrected in Phase 3; see "llama.cpp server
  usage and metrics" below).

### Ollama 0.34.4 model loading (adds to "Ollama native API" above)

Checked 2026-10-16 against https://raw.githubusercontent.com/ollama/ollama/v0.34.4/docs/api.md,
https://raw.githubusercontent.com/ollama/ollama/v0.34.4/envconfig/config.go and, read-only, a
local 0.34.4 server.

- A request with an empty prompt loads the model; adding `keep_alive: 0` unloads it
  (`done_reason: "unload"`). The default keep-alive is 5 minutes.
- Pull streams NDJSON statuses: pulling manifest, `pulling <digest>` with `digest`, `total` and
  `completed`, verifying sha256 digest, writing manifest, removing any unused layers, success;
  errors arrive mid-stream as `{"error": ...}`.
- `OLLAMA_HOST` set to `0.0.0.0` or `::` makes the server listen on every interface, while its
  own client connects to loopback (`Host()` and `ConnectableHost()` in `envconfig/config.go`).
- `/api/show` for a missing model returns 404 with `{"error": "model <name> not found"}`.

### Hardware detection (`ssw doctor`)

Checked 2026-10-16; the Windows and CUDA facts were also observed on a Windows 11 workstation.

- Windows: `IsProcessorFeaturePresent` constants AVX 39, AVX2 40, AVX512F 41, ARM v8 29,
  DotProd 43, SVE 46, I8MM 66, BF16 68; the x86 ones exist from build 19041, DotProd from 20348,
  SVE, I8MM and BF16 from 26100, and the call also returns 0 when a feature cannot be detected
  (https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-isprocessorfeaturepresent).
  `MEMORYSTATUSEX` and `GetLogicalProcessorInformationEx` layouts as documented
  (https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/ns-sysinfoapi-memorystatusex ;
  https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-getlogicalprocessorinformationex).
  There is no API for FMA, F16C, VNNI, AVX512-BF16 or AMX short of CPUID, so doctor reports
  them as unknown on Windows.
- CUDA GPUs: `nvidia-smi --query-gpu=...,compute_cap --format=csv,noheader,nounits` reports
  memory in MiB; `driver_version` is deprecated in favour of `kmd_version`; an invalid field
  exits 2; `--version` prints `CUDA Version : <x.y>`; under WSL2 the tool is at
  `/usr/lib/wsl/lib/nvidia-smi`. Tensor-core FP8 needs compute capability 8.9 and FP4 10.0
  (https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/compute-capabilities.html).
- macOS: `sysctl` names `hw.memsize`, `hw.physicalcpu`, `hw.logicalcpu`, `hw.optional.*` and
  `hw.optional.arm.FEAT_*`, `machdep.cpu.brand_string`; there is no SVE flag
  (https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/kern/kern_mib.c ;
  https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/osfmk/arm/arm_features.inc).
  Not run on macOS hardware yet.
- Linux: `/proc/cpuinfo` flag names (`avx512_bf16`, `amx_tile`, `asimddp`, `i8mm`, `sve`,
  `sme`, ...) and the separate `vmx flags` line
  (https://raw.githubusercontent.com/torvalds/linux/master/arch/x86/include/asm/cpufeatures.h ;
  https://raw.githubusercontent.com/torvalds/linux/master/arch/arm64/kernel/cpuinfo.c).
  WSL2 kernels contain `WSL2` in their release, and `wsl.exe` writes UTF-16LE without a
  byte-order mark.
- `docker version --format {{.Server.Version}}` and `docker context inspect`; `DOCKER_HOST` and
  `DOCKER_CONTEXT` override the active context
  (https://docs.docker.com/engine/manage-resources/contexts/).

### Python library behaviour

- httpx 0.28.1 ignores proxy environment variables whenever a transport is passed
  (`allow_env_proxies = trust_env and transport is None` in `_client.py`); Stepswitch's egress
  clients always pass one and set `trust_env=False`.
- On Windows, CPython 3.12 `shutil.which` searches the current directory first, so tool lookup
  in doctor uses absolute `PATH` entries only.

### Model hub API (for `ssw models pull` and `ssw models add`)

Checked 2026-10-16 against the live API and the client library sources on main
(https://raw.githubusercontent.com/huggingface/huggingface_hub/main/src/huggingface_hub/ :
`constants.py`, `file_download.py`, `hf_api.py`, `utils/_pagination.py`,
`utils/_validators.py`).

- `GET /api/models/{repo}` and `/api/models/{repo}/revision/{rev}` return `sha` (the 40-hex
  commit), `cardData.license` (with `license_name` when the license is "other"),
  `license:<id>` tags, `gated` (false, "auto" or "manual") and, for GGUF repos, a `gguf`
  summary with `total`, `architecture`, `context_length` and `chat_template`. An unknown repo
  answers 401 without a token; an unknown revision 404.
- `GET /api/models/{repo}/tree/{rev}?recursive=true` lists `{type, path, size, oid, lfs{oid,
  size}}`; `lfs.oid` is the file's sha256 (it matches the `X-Linked-ETag` of the download
  redirect). Pagination follows `Link: rel="next"`.
- `/{repo}/resolve/{rev}/{file}` answers 302 to a CDN host with `Accept-Ranges: bytes` and
  `X-Linked-Size`; a `Range: bytes=0-63` request gets 206 with `Content-Range`. The client
  library quotes the revision with `safe=""`.
- Repo IDs: at most 96 characters, `--` and `..` forbidden (`_validators.py`).
- httpx 0.28.1 drops `Authorization` on cross-origin redirects and keeps `Range`
  (`_redirect_headers`), so a bearer token never reaches the CDN host.
- Ollama can run hub GGUFs as `hf.co/{user}/{repo}:{quant}`, where the tag may also be the
  full file name; Q4_K_M is the default when present
  (https://raw.githubusercontent.com/huggingface/hub-docs/main/docs/hub/ollama.md).

### GGUF files

Checked 2026-10-16 against https://raw.githubusercontent.com/ggml-org/ggml/master/docs/gguf.md
and, at llama.cpp tag v0.7.0, `include/llama.h`, `gguf-py/gguf/constants.py`,
`gguf-py/gguf/gguf_reader.py`, `ggml/src/gguf.cpp`, `src/llama-model-loader.cpp` and
`tools/gguf-split/README.md`.

- Header: magic, version (3 current; 1 is obsolete), tensor count and key/value count as u64;
  strings are u64-length-prefixed; value types 0 to 12; nested arrays are rejected by the
  reference reader; `general.alignment` defaults to 32.
- `general.file_type` follows the `llama_ftype` table (GUESSED = 1024); `{arch}.pooling_type`
  above 0 marks an embedding model; `split.count` and `split.no` mark split files, named
  `-NNNNN-of-NNNNN.gguf` and merged with `llama-gguf-split --merge`.
- The chat template in `tokenizer.chat_template` of the Qwen3-4B-Instruct-2507 file matched
  the hub's `gguf.chat_template`, and the parameter count matched `gguf.total`.

### Capability probe and calibration (live runs)

Checked 2026-10-16 against https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
and live runs on the development workstation (Ollama 0.34.4; llama.cpp b11669, CPU only).

- llama.cpp server: `GET /props` has `build_info`, `model_path` and
  `default_generation_settings.n_ctx`; `GET /v1/models` ids default to the `-m` path and carry
  `meta.n_ctx_train`; `/health` answers 503 while the model loads. Responses carry `timings`
  (`prompt_n`, `prompt_ms`, `predicted_n`, `predicted_ms`, `cache_n`); on `/v1/chat/completions`
  the object sits at the top level of the response, next to `usage`.
- Ollama: native durations are in nanoseconds and the native response has
  `prompt_eval_cached_count`; `/health` answers 404.
- Observed live: for an assistant prefill, llama.cpp b11669 returns the prefill plus the
  continuation, Ollama only the continuation. llama.cpp b11669 accepts a named `tool_choice`
  but does not enforce it; no engine tested enforced a named tool choice. Both engines report an
  input token count that includes cached tokens.
- A reasoning preamble before schema-constrained JSON can use up a small output budget on a
  CPU model; the probe retries a reply cut at the limit that contains reasoning once with 1,024
  more tokens.
- Full probe of a 4B instruct model (Q4_K_M) on CPU only (`-ngl 0`, context 4096) with
  llama.cpp b11669: 60.0 s for the whole battery (SPEC §4.6 asks for under 3 minutes); 18
  checks passed; prefill 309 tokens/s, decode 23.5 tokens/s, time to first token 133 ms.
- A 32B model on Ollama (GPU): full probe 16.4 s (native) and 27.1 s (chat-completions);
  calibration prefill about 1,250 tokens/s and decode about 31 tokens/s (server timings),
  time to first token about 190 ms.
- A 120B mixture-of-experts reasoning model on Ollama answers one tool call per reply (no
  parallel calls), reasons in `thinking` (native) or `reasoning` (chat-completions), and keeps
  reasoning for tool turns only.

## Verified during Phase 2

### Grammar-constrained output for the fallback protocol (checked 2026-10-16)

Sources, at pinned tags: llama.cpp v0.7.0
(https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/common/json-schema-to-grammar.cpp,
`common/json-schema.cpp`, `grammars/README.md`, `common/chat.cpp`, `common/sampling.cpp`,
`tools/server/server-common.cpp`, `tools/server/server-schema.cpp` at the same tag) and Ollama
v0.40.5 (https://raw.githubusercontent.com/ollama/ollama/v0.40.5/llm/llama_server.go,
`llm/gbnf.go`, `llm/server.go`, `server/routes.go` at the same tag).

- The schema-to-grammar converter reads: same-document `$ref`; `anyOf`/`oneOf` as a union
  (keywords beside them are ignored, so `properties` must not sit next to `anyOf`); `allOf`;
  `type` (a name or a list); `const`; `enum`; `properties`; `required`; `additionalProperties`
  (absent means no extra keys once `properties` is given); `items`, `prefixItems`, `minItems`,
  `maxItems`, `minLength`, `maxLength`; integer bounds; `pattern` only when anchored with
  `^...$` (otherwise a warning and any string); `format` date, time, date-time and uuid.
- The request fails on a boolean subschema, an empty enum, invalid counts or bounds, an unknown
  type, an unresolved or remote `$ref`, or an invalid regex. Other keywords are skipped
  silently. The schema is not added to the prompt.
- Object keys keep their insertion order, so a schema listing `tool` before `arguments` makes
  the model write `tool` first.
- chat-completions: a `response_format` JSON schema becomes the server's `json_schema`. For
  templates the automatic parser handles, the grammar allows an optional reasoning block and
  then the schema (optionally inside a json code fence); the constraint starts after the
  reasoning block. On `/completion`, `json_schema` applies from the first token unless the
  request sets `grammar_lazy` with `grammar_triggers`.
- Ollama runs llama-server. A `format` schema is passed on as `json_schema` (completion path)
  or as a `response_format` JSON schema named "schema" (chat-template path); `format: "json"`
  becomes a built-in grammar (completion) or `json_object` (chat). When the model has a
  thinking close tag, the schema is converted to a grammar first and the text before the
  closing tag stays unconstrained.
- Spot-checked again for this record: in `json-schema-to-grammar.cpp` an unanchored pattern
  gives a warning and the plain string rule, unresolved refs are collected as errors and
  `check_errors()` throws `std::invalid_argument`; in `llama_server.go` the `json_schema`,
  `response_format` ("schema"), built-in JSON grammar and `json_object` paths are as above.

### Schema validation without network access (checked 2026-10-16)

- jsonschema 4.26.0 (installed source, `jsonschema/validators.py`): the default registry
  (`_REMOTE_WARNING_REGISTRY`) retrieves a remote `$ref` with `urllib.request.urlopen`
  (`_warn_for_remote_retrieve`) while validating, which would be egress outside
  `stepswitch.net`. The fallback protocol validates with `jsonschema_specifications.REGISTRY`,
  which never fetches; an unresolvable reference then raises
  `referencing.exceptions.Unresolvable` (referencing 0.37.0).
- CPython 3.12.15 `json.loads` raises RecursionError for deeply nested input (1,400 levels
  parsed, 3,000 failed) and ValueError for integers over 4,300 digits; the fallback parser turns
  both into repair hints.

### Context overflow on the llama.cpp server (live, b11669, 2026-10-16)

- A chat-completions request longer than the slot's context gets HTTP 400 with the message
  "request (5071 tokens) exceeds the available context size (4096 tokens), try increasing it".
  The chat codec maps it to the error kind `too_large`, and the agent then caps that worker's
  prompt size instead of taking the worker out of the task.

### A chat model without native tool calling (checked 2026-10-16)

- `microsoft/Phi-3.5-mini-instruct` (revision 2fe192450127e6a83f7441aef6e3ca586c338b77):
  license MIT; the tokenizer's chat template (430 characters) has system, user and assistant
  roles only, with no tool role and no tool-call syntax.
  https://huggingface.co/api/models/microsoft/Phi-3.5-mini-instruct
- Its `config.json`: 32 layers, hidden size 3072, 32 attention heads and 32 key-value heads (no
  grouped-query attention), original context 4096 and 131072 with rope scaling. An f16 KV cache
  takes 384 KiB per token, 3.22 GB at 8,192 tokens.
  https://huggingface.co/microsoft/Phi-3.5-mini-instruct/raw/main/config.json
- GGUF: `bartowski/Phi-3.5-mini-instruct-GGUF` (revision
  6d70da17e749a471ccb62ade694486011a75cda3, MIT, not gated). `Phi-3.5-mini-instruct-Q4_K_M.gguf`
  is 2,393,232,672 bytes, sha256
  e4165e3a71af97f1b4820da61079826d8752a2088e313af0c7d346796c38eff5. GGUF metadata: 3,821,079,648
  parameters, architecture phi3, context 131072, the same 430-character template.
  https://huggingface.co/api/models/bartowski/Phi-3.5-mini-instruct-GGUF/tree/main
- The Ollama library has the tag `phi3.5:3.8b-mini-instruct-q4_K_M`.
  https://ollama.com/library/phi3.5/tags
- Also checked: `01-ai/Yi-Coder-1.5B-Chat` (Apache-2.0, template without tools) and
  `google/gemma-3-4b-it` (Gemma license, gated on the hub; `ggml-org/gemma-3-4b-it-GGUF` is not
  gated; template without tools). Phi-3.5-mini-instruct was chosen for its MIT license and its
  size; it is in no default tier because its KV cache does not fit the CPU tier's RAM budget.

### Small models through the fallback protocol (live, 2026-10-16)

- Qwen2.5-Coder-1.5B, the base model for fill-in-the-middle (Q8_0, CPU, managed llama.cpp
  b11669), every step of the toy task pinned to it ("Fix the bug in add() in calc.py so that the
  tests pass"):
  - With the `edits` argument shown as `object[]` in the protocol, every reply was a valid JSON
    action, but after reading calc.py the model ran the same test call nine times and never
    edited; the run stopped as stuck.
  - With nested fields shown (`edits?: {op: ..., start: string, end?: string, text?:
    string}[]`) and a hint on repeated calls, the model sent valid `edit` actions, but anchored
    on the wrong function (line 14, `mul`), then cycled read, test and edit; stuck again.
  - So the protocol carried every action, but a 1.5B base model does not solve the task. The
    C-005 proof uses an instruction-tuned chat model without tool calling instead.
- Phi-3.5-mini-instruct (Q4_K_M, CPU, managed llama.cpp b11669), same task, every step pinned:
  - First run: it read only lines 10-16 of calc.py and so never saw `add()`, inserted the fix
    in the wrong place, reused anchors it had seen in the test file, and called `finish`
    without running the tests; the gates failed twice and the run ended `gates_failed`.
  - After two toolbox changes (files of at most 60 lines are always shown whole; a stale anchor
    in a file of at most 100 lines shows the whole file with fresh anchors and offers `text`):
    it read the test, grepped, read calc.py (shown whole), sent one stale anchor (refused),
    read again, made the right anchored edit, ran the tests (3 passed) and finished; the gates
    passed and the run ended `done` after 11 model turns in 170 s.

### Gate tools: formatter, type checker, test runner (checked 2026-10-16)

- ruff 0.16.0 changed `ruff format --check` output (a breaking change): unformatted files are
  reported as `unformatted: File would be reformatted` followed by ` --> path:line:col`, where
  earlier versions printed `Would reformat: path`; `--output-format` and the
  `RUFF_OUTPUT_FORMAT` variable also apply to `format` now.
  https://raw.githubusercontent.com/astral-sh/ruff/main/changelogs/0.16.x.md. Seen with ruff
  0.17.1 on this machine. The format gate parses both forms, passes no `--output-format` (older
  versions reject it) and drops `RUFF_OUTPUT_FORMAT` for its default commands.
- `ruff check --output-format concise` exists since 0.2.0.
  https://raw.githubusercontent.com/astral-sh/ruff/main/changelogs/0.2.x.md
- pytest exit code 5 means no tests were collected; the gates count it as passed.
  https://docs.pytest.org/en/stable/reference/exit-codes.html
- mypy reads its configuration from `mypy.ini`, `.mypy.ini`, `pyproject.toml` or `setup.cfg`
  (`[mypy]`), in that order; `--python-executable` points a mypy from elsewhere at the project
  interpreter. https://mypy.readthedocs.io/en/stable/config_file.html,
  https://mypy.readthedocs.io/en/stable/command_line.html
- Hypothesis settings `derandomize`, `deadline=None` and `database=None`:
  https://hypothesis.readthedocs.io/en/latest/reference/api.html. Observed with 6.170.0: a
  `.hypothesis/constants/` cache is still written with `database=None` (it is gitignored).

### Numerical and runtime details for the science track (checked 2026-10-16)

- `numpy.trapezoid` was added in numpy 2.0 and `numpy.trapz` was removed in 2.4.0 (2.5.4 here:
  no `trapz`). https://numpy.org/doc/stable/release/2.0.0-notes.html,
  https://numpy.org/doc/stable/release/2.4.0-notes.html. `numpy.load` defaults to
  `allow_pickle=False`. https://numpy.org/doc/stable/reference/generated/numpy.load.html
- CPython trusts a cached `.pyc` while the source's size and modification time in whole seconds
  match the values recorded in it, so a same-size edit within the same second runs the old
  code. https://peps.python.org/pep-0552/ and `Lib/importlib/_bootstrap_external.py`. Seen here
  twice: a control test in the science unit tests, and the C-008 claim fixture, whose
  reference runs measured the previous mutation until it deleted the caches and ran with `-B`.
  The gates delete stale bytecode before their first command.
- PNG chunks carry a CRC-32 equal to `zlib.crc32` over type and data; IHDR first, one filter
  byte per row. https://www.w3.org/TR/png-3/
- `python -m jupytext` works through the package's `__main__.py`.

### git and process control for gates (checked 2026-10-16)

- `git rev-parse --end-of-options`, `git diff --relative -z` and
  `git ls-files --others --exclude-standard`; `git hash-object` applies clean filters;
  `GIT_OPTIONAL_LOCKS=0` keeps read-only commands from taking optional locks.
  https://git-scm.com/docs/git-rev-parse, https://git-scm.com/docs/git-hash-object,
  https://git-scm.com/docs/git
- Killing a process tree: on Windows `taskkill /F /T` (started with `CREATE_NO_WINDOW`); on
  POSIX `start_new_session=True` and `os.killpg`.
  https://learn.microsoft.com/windows-server/administration/windows-commands/taskkill,
  https://docs.python.org/3/library/subprocess.html. Tested on Windows: a grandchild process is
  killed on timeout.

### Agent tools: search, git plumbing, LSP and embeddings (checked 2026-10-16)

- ripgrep 14.1.1 (local `rg --help` and live output;
  https://docs.rs/grep-printer/latest/grep_printer/struct.JSON.html): `--json` emits `begin`,
  `match`, `context`, `end` and `summary` messages; `path` and `lines` are `{"text": ...}` or
  `{"bytes": <base64>}`; `submatches[].start/end` are byte offsets. Flags used: `--no-config`,
  `--no-require-git`, `--no-ignore-parent`, `--no-ignore-global`, `--max-filesize`. Checked by
  hand on 11 cases (globs, ignore-case, path, literal, look-behind fallback, truncation,
  non-UTF-8, long lines): the Python fallback gives identical results.
- git 2.53 (https://git-scm.com/docs/git-read-tree and the pages of the other commands): a
  single-tree `read-tree -m` keeps index stat data and `-i` skips the working-tree check;
  `update-index --stdin` must be the last argument and `-z` makes input NUL-separated;
  `ls-files --exclude=<pattern>`; `commit-tree` reads the message from stdin;
  `rev-parse --git-path`; `worktree add --detach`; `apply --check`; `core.hooksPath`;
  `GIT_CEILING_DIRECTORIES`. The worktree pool builds diffs from a temporary index seeded from
  the worktree's own index: on a 3,000-file repository a diff went from 1.7 s to 0.3 s.
- LSP 3.17
  (https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/):
  Content-Length framing; zero-based positions in UTF-16 code units by default
  (`positionEncodings` negotiates others); `shutdown` returns null and is followed by `exit`;
  `initialized` is sent once; `workspace/configuration` returns one `LSPAny` per item (null when
  unknown); hover, definition, references (`includeDeclaration`) and
  `PublishDiagnosticsParams` shapes.
- pygls 2.1.1 and lsprotocol 2025.0.0 (https://pypi.org/pypi/pygls/json,
  https://pypi.org/pypi/lsprotocol/json,
  https://raw.githubusercontent.com/openlawlibrary/pygls/main/pygls/client.py): the client is
  asyncio-only; `start_io` pipes the server's stderr but never reads it; `stop()` has no
  timeout. These are the reasons for the built-in client (ADR 0017).
- Embeddings: llama.cpp's `format_embeddings_response_oaicompat`
  (https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/server-common.cpp)
  returns `data[]` entries with `index`, `object` and `embedding`, the embedding as a base64
  string when base64 encoding is requested. Ollama `POST /api/embed` returns
  `{model, embeddings, ...}` (https://docs.ollama.com/api/embed); the page does not say that
  `embeddings` follows the input order. Assumption, isolated in `compat/embeddings.py`: it
  does. `/v1/embeddings` request fields: https://docs.ollama.com/api/openai-compatibility.
- Offline hints passed to child tools when a task runs offline: `UV_OFFLINE`
  (https://docs.astral.sh/uv/reference/environment/), `CARGO_NET_OFFLINE`
  (https://doc.rust-lang.org/cargo/reference/config.html), `GOPROXY=off`
  (https://go.dev/ref/mod), `npm_config_offline`
  (https://docs.npmjs.com/cli/v11/using-npm/config), `HF_HUB_OFFLINE`
  (https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables).
  `PIP_NO_INDEX` follows pip's rule for naming environment variables after options
  (https://pip.pypa.io/en/stable/topics/configuration/); the page does not list it. Assumption.
- Repo map: compared with the repo map described under "Context and tokens", the ranking
  drops self-edges (they made a file a rank sink), gives unreferenced definitions 0.1 of their
  file's rank, lets a file whose references weigh less than 1 pass on only part of its rank,
  and matches plain names to top-level definitions and called attributes to methods. On this
  project's own `src/`, the core modules (paths, IR, errors, egress) now rank first.

## Verified during Phase 3

### llama.cpp server usage and metrics (b11669, checked 2026-10-16)

Source: tag `b11669` is commit `620d2225cc436ab0b001e3200e590e3b286ef9ab`; files under
https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/
(`server-task.cpp` L357-364, L494-510, L1526-1620; `server-context.cpp` L1448-1453, L2256-2258,
L3908-3917, L4478, L4687-4752; `server-common.cpp` L84-105). Confirmed live on this build with
four requests (streamed and not) against a 4B model.

- `/metrics` (with `--metrics`) has the counters `prompt_tokens_total` ("prompt tokens
  processed, excluding cached tokens"), `prompt_tokens_cached_total` ("prompt tokens reused
  from the cache"), `tokens_predicted_total`, `prompt_seconds_total`,
  `tokens_predicted_seconds_total`, `n_decode_total`, `n_tokens_max` and the speculative-decoding
  counters, all prefixed `llamacpp:`. The cached counter is missing from the server README.
- Usage in chat-completions responses: `prompt_tokens` is the whole prompt,
  `prompt_tokens_details.cached_tokens` the part reused from the cache, `completion_tokens` the
  generated tokens including the final end-of-generation token. Streamed responses carry usage
  only in an extra last chunk with empty `choices`, and only when the request sets
  `stream_options.include_usage`. The messages format reports `input_tokens` without the cached
  part, plus `cache_read_input_tokens`.
- For each finished request with one choice: the prompt counter grows by `prompt_tokens -
  cached_tokens`, the cached counter by `cached_tokens`, the predicted counter by
  `completion_tokens`. When the whole prompt is cached the server still evaluates its last
  token, in both numbers alike. Response `timings` hold the same values (`prompt_n`, `cache_n`,
  `predicted_n`).
- The counters and the usage objects disagree for requests with more than one choice,
  cancelled or failed requests (counted, but no usage returned), other traffic on the same
  server and scrapes taken while requests run. `/health`, `/props` and `/tokenize` change no
  counter.

### Python engine servers (checked 2026-10-16)

vLLM v0.31.0 (https://raw.githubusercontent.com/vllm-project/vllm/v0.31.0/ plus the paths
named here):
- Usage statistics: no flag; reporting is off when `VLLM_NO_USAGE_STATS=1`,
  `VLLM_DO_NOT_TRACK=1` or `DO_NOT_TRACK=1` (exactly "1"), or when
  `$VLLM_CONFIG_ROOT/do_not_track` exists (`vllm/envs.py` L827-831, `vllm/usage/usage_lib.py`
  L30, L50-68). Stepswitch sets the variables.
- Key: `VLLM_API_KEY` holds one key; `--api-key` wins (`envs.py` L814,
  `entrypoints/serve/middleware/register.py` L32-36). Only `/v1`, `/v2`, `/inference` and one
  publisher-named prefix are guarded; `/health`, `/metrics`, `/version`, `/load`, `/ping` and
  `/invocations` are open (`middleware/authenticate.py` L11).
- The model is positional after `serve` (`--model` is deprecated). Without `--host` it binds
  every interface; default port 8000. The port is bound before the model loads but accepts
  connections only afterwards; `/health` answers 200, or 503 once the engine died.
- Metrics: `vllm:prefix_cache_queries_total`, `vllm:prefix_cache_hits_total`,
  `vllm:prompt_tokens_cached_total` and external prefix-cache counters, labelled `model_name`
  and `engine` (`vllm/v1/metrics/loggers.py` L480, L600-720); `--disable-log-stats` removes
  them. `VLLM_USE_MODELSCOPE` (default false) switches downloads to another model hub.
  `python -m vllm.entrypoints.cli.main` works.

SGLang v0.5.22 (https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/):
- The key comes only from `--api-key` or `api-key:` in a `--config` YAML file (`.yaml`/`.yml`;
  the `--config=x` form is not merged; the command line wins) (`srt/server_args.py` L400-404,
  L669-675). `/health*`, `/ready` and `/metrics` need no key; only `Authorization: Bearer` is
  accepted (`srt/utils/auth.py` L98-117).
- The server listens after the model has loaded; `/ready` answers 200 once it is up;
  `/health` runs a one-token generation (at least 1 s).
- `/metrics` needs `--enable-metrics`: `sglang:prompt_tokens_total` (label `is_streaming`),
  `sglang:cached_tokens_total` (label `cache_source`), gauge `sglang:cache_hit_rate`.
- It logs its arguments, keys included, at startup, and `GET /server_info` returns them.
  `python -m sglang.launch_server` is deprecated for `sglang serve`. Wheels are manylinux
  x86_64/aarch64 only (https://pypi.org/pypi/sglang/0.5.22/json); `uvloop` rules out native
  Windows. `SGLANG_USE_MODELSCOPE` defaults to false; there is no usage-statistics code.

MLX-LM v0.32.2 (https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.32.2/mlx_lm/server.py):
no authentication; the request model `default_model` maps to `--model` and any other name
loads that model (L384-395, L477-483); `/health` only reports that the generation thread is
alive, which is true before the model has loaded (L539-560); `--allowed-origins` defaults to
`*`; it fetches image URLs itself (L142-152); use `mlx_lm.server` or `python -m mlx_lm server`.

transformers v5.20.0 (https://raw.githubusercontent.com/huggingface/transformers/v5.20.0/src/transformers/):
`apply_chat_template(tokenize=False)` returns a string and passes extra keyword arguments to
the template; `from_pretrained` takes `dtype` (replacing `torch_dtype`), `local_files_only` and
`trust_remote_code`; any `device_map` needs the accelerate package; `TextIteratorStreamer`
supports batch size 1 with `generate` in its own thread; v5 no longer reads
`TRANSFORMERS_OFFLINE`.

Model-hub client
(https://raw.githubusercontent.com/huggingface/huggingface_hub/main/src/huggingface_hub/constants.py,
https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables):
`HF_HUB_OFFLINE` accepts 1/ON/YES/TRUE, is read at import and limits the library to cached
files; telemetry is off when `HF_HUB_DISABLE_TELEMETRY`, `DISABLE_TELEMETRY` or `DO_NOT_TRACK`
is set.

### Granite 3.3 tool calls on the llama.cpp server (live, b11669, checked 2026-10-16)

Model: Granite-3.3-2B-Instruct Q4_K_M (registry `granite-3.3-2b-instruct`, template sha256
`8a58e18a…`), on a private llama-server started with the registry arguments, the same build
the probe used.

- The GGUF chat template tells the model to "respond only with <|tool_call|> followed by a
  JSON list of tools used". It contains `<|tool_call|>` twice and no `<tool_call>`, and it
  renders only `role` and `content` for every message, so assistant `tool_calls` in a history
  never reach the model.
- `POST /v1/chat/completions` with `tools` and `tool_choice: "auto"` returns the content
  `[{"arguments": {"city": "Paris"}, "name": "get_weather"}]`, no `tool_calls`, finish reason
  `stop`.
- `POST /completion` with the `/apply-template` prompt and `return_tokens: true` shows the first
  generated token is 49154. `POST /tokenize` with `parse_special: true` maps `<|tool_call|>` to
  that single token, and `/detokenize` renders it as `<|tool_call|>`. The model writes the
  marker; the server leaves it out of the reply text, so its own Granite handling never sees it.
- With `--special` the marker appears in the content (`<|tool_call|>[...]<|end_of_text|>`), but
  the server still returns no `tool_calls`, and the end-of-text token leaks into the content.
  It is no fix.
- A named `tool_choice` (`{"type": "function", "function": {"name": "get_time"}}`) is ignored:
  the reply is the get_weather list. The probe records "forced tool choice: no" for all four CI
  models on this build.
- Consequence (ADR 0022): the Granite recipe sets `tool_parsing = "client"`, and Stepswitch
  parses the `granite` syntax from the reply text, bare arrays included.

### vLLM Granite tool parser (main 07f3857f, checked 2026-10-16)

Sources: `vllm/parser/granite.py`, `vllm/tool_parsers/granite_engine_tool_parser.py` and
`tests/tool_parsers/test_granite_tool_parser.py` at commit
07f3857fb93772fed64e06642cdb0b8c03e0b249 (https://github.com/vllm-project/vllm).

- Markers: `<|tool_call|>` (Granite 3.0; a single special token, also matched by token id) and
  `<tool_call>` (Granite 3.1, plain text).
- After the marker comes a JSON array with no closing marker; any prose around it is content.
  The parser is selected with `--tool-call-parser granite`.
- The tests treat `[{"name": "func", "arguments": "not a dict"}]` without a marker as no call:
  vLLM never reads bare arrays, because it sees the marker's token id. Stepswitch, reading text
  over HTTP, reads a bare array only when the granite syntax is configured and the whole reply
  is a strict call list (ADR 0022).

## Verified during Phase 4

### Web server stack for `ssw dash` (installed versions, checked 2026-10-16)

- **FastAPI 0.143.2.**
  - `FastAPI(docs_url=None, redoc_url=None, openapi_url="/api/openapi.json", lifespan=...)` is
    used.
  - `fastapi.sse` exists (`EventSourceResponse`, `ServerSentEvent`, `format_sse_event`). Path
    operations that `yield` with `response_class=EventSourceResponse` get `: ping` keep-alive
    comments after 15 idle seconds (`fastapi.sse._PING_INTERVAL`).
  - Not used: the stream is written with `StreamingResponse` (ADR 0023), and the dependency
    floor stays `fastapi>=0.135`.
- **Starlette 1.8.0.**
  - `starlette.testclient.TestClient` warns "Using `httpx` with `starlette.testclient` is
    deprecated; install `httpx2` instead." (`StarletteDeprecationWarning`).
  - Tests call the app through `httpx.ASGITransport` (httpx 0.28.1) with `httpx.AsyncClient`.
    That transport does not run the lifespan.
  - `MutableHeaders(scope=message)` edits response headers in a pure ASGI middleware.
- **uvicorn 0.54.0.**
  - `uvicorn.Config` accepts `timeout_graceful_shutdown`, `access_log`, `server_header` and
    `lifespan`. `Server.started` and `Server.should_exit` are plain attributes.
  - `Server.capture_signals` handles SIGINT and SIGTERM (plus SIGBREAK on Windows) only in the
    main thread. After a graceful shutdown it re-raises each captured signal with
    `signal.raise_signal`. A caller therefore sees `KeyboardInterrupt` after Ctrl+C, and the
    process's own SIGTERM handler after SIGTERM.
  - In a worker thread no signals are captured: tests run the server in a thread and stop it
    with `should_exit`.
- **Python 3.12.15, `concurrent.futures`** (observed in tests).
  - Futures cancelled by `ThreadPoolExecutor.shutdown(cancel_futures=True)` stay `CANCELLED`
    and are never notified. `concurrent.futures.wait` on them blocks until its timeout, so the
    task runner leaves cancelled futures out before waiting.

### Wheel build hook (hatchling docs, checked 2026-10-16)

- Custom hook: `[tool.hatch.build.targets.wheel.hooks.custom]`; the default file is
  `hatch_build.py`. Subclass `hatchling.builders.hooks.plugin.interface.BuildHookInterface`.
  `initialize(version: str, build_data: dict[str, Any]) -> None` runs before the build.
  Sources: https://hatch.pypa.io/latest/plugins/build-hook/custom/ and
  https://hatch.pypa.io/latest/plugins/build-hook/reference/.
- Build data: `force_include` (`dict[str, str]`, "a mapping of extra forced inclusion paths")
  and `artifacts` (`list[str]`). The hook attribute `root` is the project root.
- Wheel versions are `standard` and `editable` ("only ships `.pth` files or import hooks").
  `force_include_editable` applies to editable builds only.
  Source: https://hatch.pypa.io/latest/plugins/builder/wheel/.
- `hatch_build.py` adds `dashboard/dist` as `stepswitch/_dashboard` to standard wheels when the
  dashboard has been built. A wheel built without it serves a page explaining how to build it.

### Playwright for Python (PyPI, checked 2026-10-16)

- `playwright` 1.64.0 resolves with `greenlet` 3.5.6 and `pyee` 14.0.0 (`uv add --group e2e`).
- The smoke test launches the installed Chrome with `chromium.launch(channel="chrome")`. It
  falls back to Playwright's own Chromium build (`playwright install chromium`) and skips when
  neither starts.

### CI for the dashboard (checked 2026-10-16)

- `actions/setup-node`:
  - Latest release v7.1.0 (2026-10-08). The tag ref `v7.1.0` points to commit
    `949feb2413d6458794dcd2491c4babbbce0c15c1` (a lightweight tag).
  - Inputs at that commit include `node-version`, `node-version-file`, `cache` (npm, yarn,
    pnpm), `package-manager-cache` (default true) and `cache-dependency-path`. It runs on
    `node24`.
- Node.js releases (nodejs.org/dist/index.json):
  - v26.12.0 (2026-10-14, current, not LTS);
  - v24.22.0 (2026-10-14, LTS "Krypton");
  - v22.23.3 (2026-09-23, LTS "Jod").

  The CI job uses Node 24.
- GitHub's `ubuntu-24.04` image 20261012.334.1 lists Chrome 155.0.8059.39, Chromium
  155.0.8059.0 and Node.js 22.23.3 (cached: 22.23.3, 24.21.0). The smoke test uses the
  installed Chrome (`channel="chrome"`), so CI downloads no browser.
  Source: actions/runner-images, `images/ubuntu/Ubuntu2404-Readme.md`.

### Dashboard toolchain (npm registry, checked 2026-10-16)

- **Picking rule.** Each package was pinned exactly to its newest stable version that was at
  least 14 days old, using `npm view <pkg> time --json` (last checked 10:55 UTC). It was
  installed with `npm install --before=2026-10-02T09:54:00Z`.
- **Pinned versions.**

  | Package | Version | Released |
  |---|---|---|
  | react, react-dom | 19.3.0 | 2026-09-09 |
  | vite | 8.3.2 | 2026-10-01 |
  | @vitejs/plugin-react | 6.1.1 | 2026-08-28 |
  | typescript | 7.0.2 | 2026-07-08 |
  | @types/react, @types/react-dom | 19.3.0 | 2026-09-09 |

  `npm audit`: 0 vulnerabilities.
- **Newer releases, not yet eligible.**
  - vite 8.3.3 (2026-10-06; same day as 8.2.4, 8.1.6, 7.3.7 and 6.4.4) and 8.3.4 (2026-10-08).
  - react and react-dom 19.3.1 (2026-10-14; same day as 19.2.9, 19.1.10 and 19.0.9).
  - @vitejs/plugin-react 6.1.2 (2026-10-05).

  Releases on several lines at once often mean a security fix; none is recorded for the pinned
  set. Vite runs only at build and dev time, since the served dashboard is static files. The
  pins become eligible to move on 2026-10-19 (plugin-react), 2026-10-20 (vite) and 2026-10-28
  (react).
- **Node.**
  - `engines`: `node >=22.12.0`; Vite 8 needs Node 20.19+ or 22.12+.
  - The build was checked with Node 24.21.0 and npm 11.19.0. The host's Node 20.12.1 is too old
    for Vite 8.
  - `npm ci` then `npm run build` gives the same asset hashes on every run:
    `index-CcNq5Dsr.js` (315.93 kB, gzip 92.17 kB) and `index-DPpCAl0d.css`.
  - The lockfile lists the macOS and Linux binaries of rolldown, lightningcss and the
    TypeScript compiler, so `npm ci` works on every platform.
- **CSP in two places.** The built `index.html` carries a Content-Security-Policy meta tag. A
  meta tag cannot set `frame-ancestors`, so the server sends the full policy, including
  `frame-ancestors 'none'`, as a header (ADR 0023).

## Verified during Phase 5

### Tokenizer files (model hub, checked 2026-10-19)

HANDOFF briefs are counted with the receiving model's tokenizer (SPEC §9, ADR 0024). Endpoint
models such as the Ollama test models have no registry entry. They are mapped by name
(`registry/tokenizers/tokenizers.toml`) to a `tokenizer.json` of the same family, pinned by
revision and sha256:

| Name | Matches | Hub ID | Revision | Size (bytes) | sha256 | License |
|---|---|---|---|---|---|---|
| `gpt-oss` | `gpt-oss*` | `openai/gpt-oss-120b` | `b5c939de8f754692c1647ca79fbf85e8c1e70f8a` | 27,868,174 | `0614fe83cadab421296e664e1f48f4261fa8fef6e03e63bb75c20f38e37d07d3` | Apache-2.0 |
| `qwen2.5` | `qwen2.5*` | `Qwen/Qwen2.5-32B-Instruct` | `5ede1c97bbab6ce5cda5812749b4c0bdf79b18dd` | 7,031,645 | `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` | Apache-2.0 |
| `qwen3` | `qwen3*` | `Qwen/Qwen3-4B-Instruct-2507` | `cdbee75f17c01a7cc42f958dc650907174af0554` | 11,422,654 | `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4` | Apache-2.0 |

- `ssw models pull --tokenizers` downloaded all three. The files matched the sizes and hashes
  above, and `tokenizers` 0.23.3 (`Tokenizer.from_file`) loaded each one.
- The `qwen3` file is also the registry tokenizer of `qwen3-4b-instruct-2507`, the T0 handoff
  writer.
- The live relay smoke run (calc-bug, relay at step 4) sent a 417-token brief to
  `gpt-oss:120b`. The count was exact, through the mapped `gpt-oss` tokenizer.
- Sources: https://huggingface.co/api/models/openai/gpt-oss-120b?blobs=true,
  https://huggingface.co/api/models/Qwen/Qwen2.5-32B-Instruct?blobs=true,
  https://huggingface.co/api/models/Qwen/Qwen3-4B-Instruct-2507?blobs=true

### tree-sitter grammar wheels (installed versions, checked 2026-10-19)

The repository map (TK2) uses py-tree-sitter with one grammar wheel per language. The tags
queries that ship inside the wheels are not used; Stepswitch has its own (ADR 0024).

- py-tree-sitter 0.26.0: `LANGUAGE_VERSION` 15, `MIN_COMPATIBLE_LANGUAGE_VERSION` 13. The pin
  is `tree-sitter>=0.25,<0.27`, because the ABI-15 grammars need 0.25 or later.
- The grammar is compiled into each wheel, so nothing is downloaded at runtime:

  | Wheel | Version | ABI | License |
  |---|---|---|---|
  | tree-sitter-c | 0.24.2 | 15 | MIT |
  | tree-sitter-cpp | 0.23.4 | 14 | MIT |
  | tree-sitter-go | 0.25.0 | 15 | MIT |
  | tree-sitter-java | 0.23.5 | 14 | MIT |
  | tree-sitter-javascript | 0.25.0 | 15 | MIT |
  | tree-sitter-ruby | 0.23.1 | 14 | MIT |
  | tree-sitter-rust | 0.24.2 | 15 | MIT |
  | tree-sitter-typescript (`language_typescript`, `language_tsx`) | 0.23.2 | 14 | MIT |

- Basis: `importlib.metadata` versions and license fields, and `Language(...).abi_version` for
  every grammar.

### Structured output on the local test models (live, 2026-10-19)

The constrained reviewer (TK8) sends a strict JSON Schema (`{approve: bool, issues: [str]}`).
A one-line diff was reviewed through the Ollama native format (`format` set to the schema):

| Model | Effort | Wall time | Tokens in / out | Result |
|---|---|---|---|---|
| `gpt-oss:120b` | medium | 1.4 s | 199 / 66 | schema-valid JSON, approve |
| `qwen2.5:32b` | none | 0.6 s | 136 / 11 | schema-valid JSON, approve |

Both answers parsed (`parse_review`), and the codec reported no losses.

### The reasoning model's chat template and the prompt cache (live, checked 2026-10-19)

The chat template that the local Ollama server (0.34.4) applies to `gpt-oss:120b` (MXFP4,
116.8B parameters) was read from `POST /api/show` (`template`). Two of its rules decide what
a server can keep in its prompt cache between the steps of one transcript:

- **The effort is part of the first block.** The first system block carries the current date
  and a reasoning line taken from the request's think level (`Reasoning: <level>`, medium when
  the request sets no level). A step sent with another effort than the step before it
  therefore shares no prefix with it, so every token is computed again. The date changes the
  block at midnight too.
- **Reasoning is shown only after the last user message.** An assistant turn's thinking is
  rendered only for turns after the last user message; earlier turns are rendered without it.
  A user message added in the middle of a transcript (a nudge) therefore changes how every
  earlier assistant turn is rendered. The cached prefix ends before the first of them.

ADR 0024 decisions 9 (one effort for every work step of a transcript) and 14 (a text answer
after a tested change finishes, with no nudge) follow from these rules. In the first Phase 5
bench round, before both, the optimized runs used more uncached tokens than the baseline
(docs/PROGRESS.md, Phase 5).

### LLMLingua-2 for the reference compression server (source, checked 2026-10-19)

`extras/llmlingua-server` (TK11) was written against llmlingua 0.2.2 and the `main` source of
`llmlingua/prompt_compressor.py`:

- **Constructor.** `PromptCompressor(model_name="NousResearch/Llama-2-7b-hf",
  device_map="cuda", model_config={}, open_api_config={}, use_llmlingua2=False,
  use_slingua=False, llmlingua2_config={})`.
  - It calls `tiktoken.encoding_for_model("gpt-3.5-turbo")`, so it needs the `cl100k_base` BPE
    file. The file is downloaded on first use unless it is already cached.
  - With `use_llmlingua2`, it calls `init_llmlingua2(**llmlingua2_config)`.
- **Model loading.** `load_model` sets `trust_remote_code` to True unless `model_config` sets
  it. The server passes `{"trust_remote_code": False}`.
  - `device_map="cpu"` loads in float32.
  - The config, tokenizer and model come from `from_pretrained(model_name)`, so a local
    directory works.
- **Compression.** `compress_prompt(context, ..., rate=0.5, ..., force_tokens=[],
  drop_consecutive=False, ...)` returns `compress_prompt_llmlingua2(...)` when
  `use_llmlingua2` is set.
  - That path defaults `use_context_level_filter` to False.
  - It returns `compressed_prompt`, `compressed_prompt_list`, `origin_tokens`,
    `compressed_tokens`, `ratio`, `rate` and `saving`.
- **Checkpoint.** `microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank` at
  revision `5f0c82792b7ea14c6484e015b6a072009496b7f2` (last modified 2025-01-08).
  - Files: `config.json` (875 B), `model.safetensors` (709,388,104 B, sha256
    `22b9ecde52fec5c97e8c54a293be768727df95a81c6c8dccb03f262a50c58324`), `tokenizer.json`
    (2,919,362 B), `tokenizer_config.json` (1,191 B), `special_tokens_map.json` (125 B) and
    `vocab.txt` (995,526 B).
- **Word rules come from the model name.** `utils.is_begin_of_new_word` and `get_pure_token`
  look for `bert-base-multilingual-cased` or `xlm-roberta-large` in `model_name`; any other
  name raises `NotImplementedError` on every compression. A checkpoint stored as
  `llmlingua-2-small` loaded fine and then failed every request. The server therefore requires
  the directory to carry the checkpoint's name.
- **Live check (2026-10-19).** Environment: llmlingua 0.2.2, torch 2.14.1, transformers
  5.20.1, tiktoken 0.14.0, Python 3.12, Windows, CPU only. The checkpoint download matched
  the sha256 above.
  - `--prefetch` took 14.5 s, including fetching the tiktoken file into `TIKTOKEN_CACHE_DIR`.
    Afterwards the server loaded in about 13 s behind the offline guard and needed no network.
  - Through `HttpCompressor` at rate 0.5, 6,000 characters of docs/ARCHITECTURE.md came back
    as 3,409 characters. Three runs took 0.42 to 0.51 s each, and every request was audited as
    local.
  - The interpreter's working set was 1,231 MB.
  - transformers warns that the sequence is longer than 512 tokens; LLMLingua-2 splits long
    text into windows itself.
  - The output drops function words and inserts spaces inside paths and numbers (`docs / SPEC.
    md`, `2026 - 10 - 16`). A model reading such text cannot use it to name files.
- Sources: https://raw.githubusercontent.com/microsoft/LLMLingua/main/llmlingua/prompt_compressor.py,
  `llmlingua/utils.py` of the installed 0.2.2 wheel,
  https://huggingface.co/api/models/microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank?blobs=true

## Verified during Phase 6

### pytest JUnit reports for cross-running tests (pytest 9.1.1, checked 2026-10-19)

The disagreement signal (ADR 0025) runs one candidate's tests against another candidate's code
and reads the outcomes from a JUnit XML report. Checked with the installed pytest 9.1.1 on
scratch projects (and pinned by `tests/unit/test_disagreement.py`):

- A test module that fails to import is a collection error. By default one collection error
  interrupts the session (exit code 2) and no test runs. With
  `--continue-on-collection-errors` the other modules run; the exit code is then 1 when any
  test failed or errored.
- In the `--junitxml` report a collection error is a `<testcase>` with an empty `classname`,
  `name` set to the module's dotted path and an `<error message="collection failure">` child.
- A collected test is a `<testcase>` whose `classname` is the dotted path of its file relative
  to the rootdir (`.ssw-probe/c0/tests/test_mod.py` gives `.ssw-probe.c0.tests.test_mod`) and
  whose `name` is the test function. A failed test has a `<failure>` child, a test whose setup
  failed an `<error>` child and a skipped one a `<skipped>` child.
- `--import-mode=importlib` lets two test files with the same name in different directories
  run in one session.
- `python -B` writes no `__pycache__`, `-p no:cacheprovider` no `.pytest_cache`, and
  `-o addopts=` clears the project's `addopts` (so options such as `--testmon` stay off).
- `python -m pytest` run from a worktree puts that directory first on `sys.path` (the
  interpreter's `-m` rule, docs.python.org/3/using/cmdline.html), so the copied tests import
  the candidate's own modules.

### The review schema on the local models (live, checked 2026-10-19)

The cross-family review (SPEC §8.5, ADR 0025) asks for the `review.REVIEW_SCHEMA` answer as a
strict `json_schema` response format. Checked live against the workstation's local Ollama
endpoint over the native API, at temperature 0.2 and seed 0, with one correct and one wrong
one-line diff of `add()` each:

| Model | Diff | Answer | Schema check | Seconds | Prompt / output tokens |
|---|---|---|---|---|---|
| gpt-oss:120b (effort medium) | correct | `approve: true`, no issues | conforms | 1.9 | 260 / 84 |
| gpt-oss:120b (effort medium) | wrong | `approve: false`, one `high` issue at `calc.py` line 2 | conforms | 2.2 | 260 / 145 |
| qwen2.5:32b | correct | `approve: true`, no issues | conforms | 20.0 (model load) | 199 / 11 |
| qwen2.5:32b | wrong | `approve: false`, one `high` issue at `calc.py` line 2 | conforms | 2.5 | 199 / 54 |

- The native API's `format` field carries the schema, and no compatibility losses were
  reported for either model, so the grammar-constrained path holds on both families.
- The reasoning model puts its reasoning in its own field; the answer text is bare JSON.
- `review.parse` read all four answers, with the confidence 1.0 for an approval and 0.25 for one
  high-severity issue.

## Verified during Phase 8

These are the wire shapes and switches of the harnesses that `ssw run --harness` drives (ADR
0029), read in each harness's documentation and source on 2026-10-19. Only Claude Code is
installed on the development machine. The other adapters' contract fixtures are built from the
shapes below (`source: "documented"` in `tests/fixtures/harness/*/fixture.json`).

### Agent Client Protocol (schema 1.26.0, checked 2026-10-19)

Sources: https://raw.githubusercontent.com/agentclientprotocol/agent-client-protocol/main/schema/v1/schema.json,
`schema.unstable.json` and `CHANGELOG.md` beside it, and the v1 protocol docs (initialization,
session setup, config options, prompt turn, tool calls, file system, transports, cancellation).

- **Versions.** schema-v1.26.0 was released 2026-10-16 (1.25.0 on 2026-10-09).
  `protocolVersion` is still the integer `1`. A v2 draft exists (schema-v2.0.0-alpha.9); it is
  tracked, not used.
- **Transport.** Newline-delimited JSON-RPC 2.0 over the agent's stdio, UTF-8, with no embedded
  newlines. The agent may log to stderr.
- **`initialize`.** Only `protocolVersion` is required in the request and in the answer.
  - Client capabilities include `fs.readTextFile`, `fs.writeTextFile` and `terminal`.
  - The answer carries `agentCapabilities` (`loadSession`, `promptCapabilities`,
    `mcpCapabilities`, `sessionCapabilities`), `agentInfo` and `authMethods`.
- **`session/new`.** It requires `cwd` (absolute) and `mcpServers`. The result requires
  `sessionId` and may carry `modes` and `configOptions`.
  - A config option is `{id, name, category, type: "select", currentValue, options}`. Its
    category is `mode`, `model`, `model_config`, `thought_level` or a custom `_…`, and its
    options may be grouped.
  - `session/set_config_option` (`{sessionId, configId, value}`) answers with the complete
    option list.
  - `models` and `session/set_model` are not in the stable schema.
- **`session/prompt`.** The request is `{sessionId, prompt: [{type: "text", text}]}`.
  - The answer is a `stopReason`: `end_turn`, `max_tokens`, `max_turn_requests`, `refusal` or
    `cancelled`.
  - Token usage on the result exists only in the unstable schema.
- **`session/update`.** These notifications are discriminated by `update.sessionUpdate`:
  - `agent_message_chunk`, `agent_thought_chunk`, `user_message_chunk`;
  - `tool_call` (requires `toolCallId` and `title`; carries `kind`, `status`, `rawInput` and
    `locations`) and `tool_call_update` (requires only `toolCallId`);
  - `usage_update` (`used` and `size` of the context window, optional `cost`; there is no
    input/output split);
  - `plan`, `config_option_update`, `current_mode_update`, `available_commands_update`,
    `session_info_update`, `notice`, `compaction_update`, `compaction_summary_chunk`.
- **`session/request_permission`.** This is a request from the agent to the client.
  - It carries `toolCall` and `options`, with option kinds `allow_once`, `allow_always`,
    `reject_once` and `reject_always`.
  - The answer is `{outcome: {outcome: "selected", optionId}}` or
    `{outcome: {outcome: "cancelled"}}`.
  - `session/cancel` is a notification. After it, the client must answer every pending
    permission request with `cancelled`, and the agent ends the prompt with `cancelled`.
- **File system.** `fs/read_text_file` (`path`, optional 1-based `line` and `limit`) answers
  `{content}`. `fs/write_text_file` creates a missing file and answers `{}`.
- **Python SDK.** Its current release requires an older Python than the newest supported one,
  so Stepswitch implements the client side itself (ADR 0029).

### Claude Code headless runs (checked 2026-10-19)

Sources: https://code.claude.com/docs/en/headless and, on the same site, `cli-reference`,
`permission-modes`, `permissions`, `env-vars`, `fast-mode`, `data-usage`, `network-config` and
`agent-sdk/typescript`.

- **Output.** `claude -p "<prompt>" --output-format stream-json --verbose` writes NDJSON, in
  this order:
  - `system`/`init` (`session_id`, `model`, `tools`, `permissionMode`, `claude_code_version`, …);
  - `assistant` and `user` messages in the messages format;
  - other `system` events: `api_retry`, `permission_denied`, and `compact_boundary` with
    `compact_metadata: {trigger, pre_tokens}`;
  - a last `result`.
- **The `result` line.**
  - On success it has `subtype: "success"`, `result` (the final text), `num_turns`, `usage`,
    `modelUsage` and `permission_denials`.
  - The error subtypes (`error_max_turns`, `error_during_execution`, `error_max_budget_usd`,
    `error_max_structured_output_retries`) carry `errors` and no `result`.
  - Exit codes are 0 on success, non-zero on failure and 143 on SIGTERM.
  - The docs don't say whether `--verbose` is still required with stream-json. The adapter
    always passes it.
- **Prompt.** The prompt is positional. Print mode reads stdin, but every documented example
  pairs stdin with a positional prompt. A stdin-only text prompt is documented only for
  `--input-format stream-json`. The tool-list flags are variadic, so the prompt goes right
  after `-p`.
- **Permission modes.** `--permission-mode` takes `default`, `acceptEdits`, `plan`, `auto`,
  `dontAsk` or `bypassPermissions`.
  - With no mode given, a `-p` run that doesn't fetch feature flags (a third-party endpoint, or
    telemetry off) starts in `auto` (v2.1.285 and later), so always pass one.
  - Without a permission host, calls that would prompt are denied.
  - Bypass mode is refused as root outside a recognised sandbox.
- **Allow rules.**
  - `Bash(npm run test *)` matches the prefix as written, and a trailing ` *` also matches the
    bare command: `Bash(ls *)` matches `ls` but not `lsof`.
  - `Bash(ls:*)` equals `Bash(ls *)`.
  - Compound commands are split on `&&`, `||`, `;`, `|`, `|&`, `&` and newlines, and every part
    must match an allow rule.
- **Config and history.** `CLAUDE_CONFIG_DIR` relocates the settings directory and
  `~/.claude.json`. `CLAUDE_CODE_SKIP_PROMPT_HISTORY` keeps sessions out of `--resume` and
  `--continue`, so the adapter doesn't set it.
- **Egress.** `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, with any non-empty value, turns off:
  - auto-updates, telemetry and error reporting;
  - feedback commands and release notes;
  - availability checks, including the fast-mode check;
  - plugin source re-runs and feature-flag fetching.

  It doesn't cover three things:
  - the web-fetch domain preflight, which is a settings key;
  - the official marketplace auto-install (`CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL`);
  - gateway model discovery, which has its own opt-in.

  `DISABLE_TELEMETRY` and `DISABLE_ERROR_REPORTING` each opt out with any non-empty value.
- **Variables the adapter sets** (see also the Phase 0 notes above):
  - `ANTHROPIC_BASE_URL`, and `ANTHROPIC_AUTH_TOKEN` (sent as a bearer token);
  - `ANTHROPIC_MODEL` and the `ANTHROPIC_DEFAULT_*_MODEL` pins;
  - `ANTHROPIC_CUSTOM_HEADERS`;
  - `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1`, which adds a request-class header (main, subagent,
    workflow, compaction, auxiliary);
  - `CLAUDE_CODE_MAX_CONTEXT_TOKENS` (unknown model names otherwise get 200K).

### Claude Code live through `ssw proxy` (2.1.296 on Windows, checked 2026-10-19)

Three runs of `ssw run --harness claude-code` on the calc-bug toy repository, with one local
model (gpt-oss:120b on Ollama) behind the proxy. All three ended `done` and the gates passed.
The third run is the contract fixture `tests/fixtures/harness/claude-code/recorded-live.jsonl`.

- **Isolation held.** The `init` event reported `apiKeySource: none`, analytics and product
  feedback disabled, no MCP servers, the slot name as the model, and memory paths inside the
  run's own config directory.
- **Thinking counts.** Besides the documented messages, the stream carries one
  `system`/`thinking_tokens` event per thinking delta (an estimated running count): 767 to 895
  lines in these runs. The adapter keeps them as log events.
- **The Bash tool is a POSIX shell on Windows**, so the model types Windows paths with forward
  slashes. In the first run the allow rule held the interpreter path with backslashes, so the
  test command was refused: a `system`/`permission_denied` event, with the reason that the
  session has no approval surface. The harness then tells the model not to retry anything that
  needs approval. The rule and the prompt now spell the command with forward slashes.
- **Wording matters.** In the second run the prompt only said that Stepswitch would run the
  tests afterwards, and the model ran a bare `pytest -q`, which the rule refused. In the third
  run the prompt asked for the command exactly as written, and the model ran it (3 passed).
  These are single runs, not a measured rate.
- The harness prints `[claude-code:unrecognized_model]` to stderr for the slot name. The
  `result` event's `total_cost_usd` comes from the harness's own price table and means
  nothing for local models. Its `modelUsage` reports the context window that
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS` set.

### Pi (checked 2026-10-19)

Sources: https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/json.md
and, in the same folder, `models.md`, `custom-provider.md`, `extensions.md`, `virtual-models.md`,
`packages.md`, `cli.md` and `environment-variables.md`; source `src/core/extensions/types.ts`,
`src/core/virtual-models.ts` and `src/core/model-registry.ts`.

- **Output.** `pi --mode json "<prompt>"` writes strict JSONL on stdout; split it on LF only.
  - A `session` header comes first (`version` 3, `id`, `cwd`).
  - Then `agent_start`, `turn_start`, `message_start`, `message_update` (with `usage`),
    `message_end`, `turn_end`, `agent_end` and `agent_settled {aborted}`.
  - Also `tool_execution_start`/`_end`, `compaction_start`/`_end` and
    `auto_retry_start`/`_end`.
- **The answer.** It is the last assistant `message_end`: `content`, `model`, `usage`
  (`input`, `output`, `cacheRead`, `cacheWrite`), `stopReason` (`stop`, `length`, `toolUse`,
  `error`, `aborted`) and `errorMessage`. JSON mode exits 0 even when the answer failed, so the
  stop reason decides the outcome.
- **`models.json`.** It lives in the agent dir (`~/.pi/agent`, or `PI_CODING_AGENT_DIR`). Each
  `providers.<name>` has:
  - `baseUrl`;
  - `api` (`openai-completions` is the chat-completions format);
  - `apiKey`: a literal, `$NAME`, `${NAME}` or `!command`;
  - `models`: `id`, `name`, `contextWindow` (default 128000) and `maxTokens` (default 16384).
- **Egress.** `--no-session`, `--offline` / `PI_OFFLINE=1`, `PI_SKIP_VERSION_CHECK` and
  `PI_TELEMETRY=0`.
- **Extensions.** An extension is a default-exported factory `(pi) => void | Promise<void>`.
- **Virtual models.** `pi.registerVirtualModel({provider, id, name, thinkingLevels?,
  contextWindow?, maxTokens?, route(request, ctx)})` registers one.
  - `route` runs before every request. Its `request` is `{model, thinkingLevel, reason:
    "user" | "continuation" | "retry" | "direct", previous?, failed?: {model, thinkingLevel?,
    message}, state?, messages, signal?}`.
  - It returns `{model, thinkingLevel, state?}`, looking models up with
    `ctx.modelRegistry.find(provider, id)`.
  - A virtual model's `id` may not equal a physical model's `id` in the same provider.
- **Loading.** `-e <path>` loads an extension module without a build step (through jiti) and is
  repeatable. `--no-extensions` turns off discovered, configured and built-in extensions, but
  explicit `-e` paths still load.
- **Packages.** Add `"pi": {"extensions": [...]}` to `package.json` and the keyword
  `pi-package`. List the host's packages in `peerDependencies` with `"*"`, never in
  `dependencies`.

### Oh My Pi (checked 2026-10-19)

Sources: https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/cli-reference.md and, in
the same folder, `models.md`, `settings.md`, `config-usage.md`, `environment-variables.md`,
`extensions.md`, `extension-loading.md` and `approval-mode.md`; source
`packages/coding-agent/src/modes/print-mode.ts`,
`packages/coding-agent/src/extensibility/extensions/types.ts` and
`packages/coding-agent/src/commands/acp.ts`.

- **Output.** `omp -p --mode json "<prompt>"` writes Pi's event stream with a `session` header,
  except that `message_update` carries no `usage`. The answer is the last assistant
  `message_end`, with usage as in Pi. The process exits non-zero when the stop reason is
  `error` or `aborted`.
- **`models.yml`** (in `~/.omp/agent/`). Each `providers.<name>` has `baseUrl`, `api`, and either
  `apiKey` (read as an environment variable name first, else as a literal) or `auth: none`.
- **`config.yml` `modelRoles`.** It maps roles (`default`, `smol`, `slow`, `plan`, `commit`,
  `tiny`, `memory`, `task`, `advisor`) to `provider/model[:thinking]` selectors.
  `PI_CODING_AGENT_DIR` overrides the agent directory for the default profile only.
- **Approval modes.**
  - Tools declare a tier: `read`, `write` or `exec`. Undeclared tools count as `exec`.
  - `write` auto-approves `read` and `write` and asks for `exec`.
  - `yolo`, the schema default, approves everything.
  - Without an interactive UI, a call that still needs a prompt fails (fails closed).
- **Extensions.** They have the same factory shape as Pi's, and Bun imports the modules. There
  is no `registerVirtualModel`.
- **`before_subagent_spawn`.** `pi.on("before_subagent_spawn", (event, ctx) => result)` fires
  in the parent once per child, before the child's model is resolved.
  - `event` is `{agent, invocationKind, modelRole?, patterns, spawnKey?}`.
  - `result` is `{model?: string | string[], block?, reason?, note?}`.
  - The last returned `model` wins.
- **`--trusted-extension <absolute path>`.** It is an exact allowlist:
  - it turns off ambient discovery;
  - it cannot be combined with `-e`;
  - a load failure aborts startup.

  `omp acp` accepts the same launch flags.

### OpenCode (v1.19.3, checked 2026-10-19)

Sources: https://github.com/anomalyco/opencode/blob/v1.19.3/packages/opencode/src/config/parse.ts
and, in the same tree, `config.ts`, `src/cli/cmd/run.ts`, `src/acp/config-option.ts` and
`packages/core/src/v1/config/permission.ts`; the docs pages `cli.mdx`, `config.mdx`,
`providers.mdx`, `permissions.mdx` and `acp.mdx`.

- **ACP.** `opencode acp` speaks ACP on stdio. Its config options are `model` (category
  `model`), `effort` (`thought_level`) and `mode`, and it sends `usage_update`.
- **`OPENCODE_CONFIG_CONTENT`.** This is inline JSON.
  - It overrides the global, custom and project config; managed config still overrides it.
  - It skips global config seeding.
  - Unknown keys are ignored, not rejected. A wrong type on a known key is an error.
  - OpenCode writes `$schema` back into on-disk config files that lack it; inline config avoids
    that.
- **Custom chat-completions provider.**
  - `provider.<name>` is `{npm: "@ai-sdk/openai-compatible", options: {baseURL, apiKey:
    "{env:NAME}"}, models: {<id>: {name, limit: {context, output}}}}`.
  - `model` and `small_model` are `provider/model` selectors.
- **`permission`.** It is either an action (`ask`, `allow`, `deny`) or a map per tool (`read`,
  `edit`, `bash`, `external_directory`, …). In a pattern map the last matching rule wins.
  `webfetch` and `websearch` take an action only.
- **Sharing and updates.** `share` is `manual`, `auto` or `disabled`. `autoupdate: false` turns
  off update checks.
- **Environment switches.** `OPENCODE_DISABLE_AUTOUPDATE`, `OPENCODE_DISABLE_MODELS_FETCH`, and
  a switch that stops OpenCode reading another harness's config directory.

### Kimi Code (checked 2026-10-19)

Sources: https://raw.githubusercontent.com/MoonshotAI/kimi-code/main/docs/en/reference/kimi-acp.md,
`kimi-command.md`, `docs/en/configuration/env-vars.md`, `config-files.md` and `providers.md`;
source under https://github.com/MoonshotAI/kimi-code/blob/main/packages/agent-core-v2/src/
(`app/kosongConfig/envOverlay.ts`, `app/bootstrap/bootstrap.ts`, `human/llm-kimi/trait.ts`,
`llm-adapter/provider/provider-definition.ts`) and `packages/acp-server/src/`.

- **ACP.** Kimi Code is the current TypeScript CLI, and `kimi acp` speaks ACP on stdio.
  - `loadSession` is true.
  - `configOptions` cover model, thinking and mode.
  - It uses `fs/*` and `terminal/*` only when the client advertises them.
- **Model variables.** `kimi acp` boots the same engine as `kimi -p`, so it reads the
  `KIMI_MODEL_*` overlay:
  - `KIMI_MODEL_NAME` and `KIMI_MODEL_API_KEY` (required);
  - `KIMI_MODEL_BASE_URL`: always set it, since it otherwise falls back to the vendor's URL;
  - `KIMI_MODEL_PROVIDER_TYPE`;
  - `KIMI_MODEL_MAX_CONTEXT_SIZE` (default 262144);
  - `KIMI_MODEL_MAX_COMPLETION_TOKENS`.
- **The default provider type `kimi`.** It sends chat-completions requests:
  `POST {base}/chat/completions` with a bearer key, `stream: true` and
  `stream_options.include_usage`.
  - It adds fields of its own: `thinking`, `prompt_cache_key` and `max_completion_tokens`. The
    last defaults to the context size minus the tokens used, so the adapter caps it.
  - In print mode it also sends device headers to the base URL: platform, version, host name,
    device model, OS version and a persistent device ID.
  - The other provider types are named after vendors, so they are set in local config only.
- **Isolation.** `KIMI_CODE_HOME` isolates config, sessions and the device ID.
- **Egress.** The switches are `KIMI_DISABLE_TELEMETRY=1`, `KIMI_CODE_NO_AUTO_UPDATE=1` (no
  update preflight at all) and `KIMI_DISABLE_CRON=1`. The web tools are auto-allowed, and only
  `config.toml` turns them off (`[tools] disabled = ["WebSearch", "FetchURL"]`, with
  `telemetry = false`).

## Verified during Phase 9

These are the facts behind the slow lane (ADR 0030) and zero-egress enforcement (ADR 0031),
read in each project's documentation and source on 2026-10-19. The development machine has
neither AirLLM nor a Linux host with AMX, so the two slow-lane adapters are tested against
fakes built from these notes.

### AirLLM (4.0.0, checked 2026-10-19)

Sources: https://pypi.org/pypi/airllm/json, https://github.com/lyogavin/airllm/releases,
https://raw.githubusercontent.com/lyogavin/airllm/main/air_llm/airllm/airllm_base.py with
`auto_model.py` and `utils.py` beside it, and the README.

- **Release.** 4.0.0 was uploaded on 2026-09-05 (3.3.0 on 2026-08-28). The license is
  Apache-2.0, and `requires_python` is not declared.
- **Dependencies (setup.py).** tqdm, torch>=2.4, transformers>=4.49,<6, accelerate>=1.0,
  safetensors, huggingface-hub, scipy and sentencepiece.
  - bitsandbytes is needed only for `compression`, and is asserted at run time.
  - mlx is needed on macOS.
- **Model class.** `AirLLMBaseModel` is a plain class, not a transformers model. Its signature:
  `(model_local_path_or_repo_id, device="cuda:0", dtype=None, max_seq_len=512,
  layer_shards_saving_path=None, profiling_mode=False, compression=None, hf_token=None,
  prefetching=True, delete_original=False, install_hooks=True, load_resident=True)`.
  - `generate(*args, **kwargs)` forwards to the wrapped transformers model, so sampling,
    stopping and `streamer=` behave as in transformers. This is not documented upstream; it
    follows from the source.
  - `compression` is `None`, `"4bit"` or `"8bit"`.
- **`AutoModel.from_pretrained`.** It reads `AutoConfig.from_pretrained(...,
  trust_remote_code=True)` and picks the class from `config.architectures`, falling back to the
  base class.
  - On macOS it always returns `AirLLMLlamaMlx`. That class's
    `generate(x, temperature=0, max_new_tokens=None, **kwargs)` returns decoded text. The shim
    cannot drive it, so the adapter refuses macOS.
- **Tokenizer and generation config.** The tokenizer is
  `AutoTokenizer.from_pretrained(local_path, trust_remote_code=True)`, and the generation config
  is read from the local path. Remote code is always trusted; issue #293 about this is open.
- **Loading.** A path that exists and holds `model.safetensors.index.json`,
  `pytorch_model.bin.index.json`, `model.safetensors` or `pytorch_model.bin` is split into
  per-layer shards locally. Every `snapshot_download` call sits behind `assert repo_id is not
  None`, so the local branch never downloads.
  - Any other string is a model-hub ID and is downloaded. AirLLM has no offline handling of
    its own. The shim therefore serves local directories only, and the adapter sets
    `HF_HUB_OFFLINE=1` and `HF_HUB_DISABLE_TELEMETRY=1`.
  - Shards go to `<checkpoint>/splitted_model[.4bit]` or to `layer_shards_saving_path`, and are
    reused once complete.
- **No server.** The serving request (#205) was closed as not planned. A local API pull
  request (#308) was closed unmerged. The open #358 adds only a terminal chat.
- **Platforms.** The default device is `cuda:0`. CPU inference is mentioned only in the
  v2.10.1 changelog (2024-08-18), and Windows not at all.
- **Speed.**
  - The v3.1.0 release notes (2026-07-29) report about 292 s per token for a 2.8T MoE model on
    a 48 GB card, disk-bound, after about 900 s of start-up.
  - A community report (issue #186) gives about 2 minutes per token for a 32B 4-bit model read
    from a hard disk.
  - There is no official figure for a 70B model.
- **Used by.** `stepswitch/serve/airllm_server.py` and `stepswitch/serve/airllm.py`.

### KTransformers and sglang-kt (0.7.2, checked 2026-10-19)

Sources:

- https://pypi.org/pypi/ktransformers/json, https://pypi.org/pypi/kt-kernel/json and
  https://pypi.org/pypi/sglang-kt/json;
- https://github.com/kvcache-ai/ktransformers/tree/main/kt-kernel;
- https://raw.githubusercontent.com/kvcache-ai/ktransformers/main/doc/en/Kimi-K2-Thinking.md;
- https://newreleases.io/project/github/kvcache-ai/ktransformers/release/v0.7.0.post4;
- the fork's `python/sglang/srt/server_args.py` and `python/sglang/srt/entrypoints/http_server.py`.

| Package | Version (date) | Notes |
|---|---|---|
| `ktransformers` | 0.7.2 (2026-10-15), Python >= 3.11 | Meta-package: `kt-kernel==0.7.2`; the extra `[sglang]` adds `sglang-kt==0.7.2` |
| `kt-kernel` | 0.7.2 (2026-10-14) | Wheels for cp311 and cp312 `manylinux_2_35_x86_64` only; torch==2.9.1; ships the `kt` CLI |
| `sglang-kt` | 0.7.2 (2026-10-15) | One wheel, `py3-none-manylinux_2_35_x86_64`: a fork of SGLang |

All three are Apache-2.0.

- **Install.** `pip uninstall sglang -y && pip install kt-kernel sglang-kt`. The fork replaces
  the official package; the two must not share an environment.
- **Launch.**

  ```
  python -m sglang.launch_server --host 127.0.0.1 --port 30000 --model <dir> \
    --kt-method AMXINT8 --kt-weight-path <cpu weights> --kt-cpuinfer 64 \
    --kt-threadpool-count 2 --kt-num-gpu-experts 32 --kt-max-deferred-experts-per-token 2
  ```

  - GGUF weights use `--kt-method LLAMAFILE --kt-weight-path <gguf dir>`.
  - Older guides spell the flags `--kt-amx-weight-path` and `--kt-amx-method`.
- **Flags.**
  - `--kt-method`: AMXINT4, AMXINT8, RAWINT4, FP8, FP8_PERCHANNEL, BF16 or LLAMAFILE.
  - `--kt-weight-path`: the CPU expert weights. Convert them for AMX with
    `scripts/convert_cpu_weights.py`.
  - `--kt-cpuinfer`: CPU threads. The README says to use the physical cores.
  - `--kt-threadpool-count`: one pool per NUMA node, default 2. `--kt-numa-nodes` lists the
    node IDs.
  - `--kt-num-gpu-experts`: GPU experts per MoE layer. `--kt-gpu-experts-ratio` overrides it.
  - `--kt-max-deferred-experts-per-token`: 0 is synchronous; the README recommends 1 to 4.
- **`kt run <model>`.** It only execs `python -m sglang.launch_server`, with host 0.0.0.0 by
  default and no health check. The adapter launches the server itself on 127.0.0.1 instead.
- **HTTP.** The fork keeps SGLang's routes: `/health`, `/health_generate`, `/model_info`,
  `/server_info`, `/v1/models`, `/v1/chat/completions`, `/v1/completions`, `/v1/messages`,
  `/v1/responses` and `/generate`.
- **Requirements.**
  - Linux x86-64 with glibc 2.35 or newer, and Python 3.11 or 3.12. The v0.7.0.post4 notes ask
    for Python 3.12, CUDA 12.8 and Torch 2.9.1.
  - The README's mentions of manylinux_2_17 and Python 3.10 are out of date.
  - CPU features per method:
    - AVX2 is the minimum (LLAMAFILE).
    - RAWINT4 needs AVX-512 F and BW.
    - BF16 needs AVX-512 BF16.
    - FP8 needs AVX-512 BF16 and VBMI.
    - AMXINT4 and AMXINT8 need AMX.
    - FP8_PERCHANNEL's needs are not stated.

    The adapter reads these from `/proc/cpuinfo` (`avx512f`, `avx512bw`, `avx512_bf16`,
    `avx512vbmi`, `amx_tile`).
  - A GPU is optional (compute capability 8.0 or newer).
- **Legacy server.** The old integrated server is archived and unmaintained. It is not
  targeted.
- **Assumption, not verified.** `sglang-kt` keeps SGLang's `--config` file, which the adapter
  uses for the API key. Until a live run confirms it, this stays isolated in the SGLang
  adapter's key handling.

### SGLang health and keys (0.5.22, checked 2026-10-19)

Sources: https://docs.sglang.io/advanced_features/server_arguments.html,
`python/sglang/srt/entrypoints/http_server.py`, `python/sglang/srt/environ.py` and
`python/sglang/srt/utils/auth.py`, upstream and in the fork
(https://raw.githubusercontent.com/kvcache-ai/sglang/main/python/sglang/srt/entrypoints/http_server.py).

- **Health endpoints.** `/health` and `/health_generate` share one handler.
  - It returns 503 while the server starts or shuts down.
  - Otherwise `/health_generate` always runs a one-token generation. It returns 503 when
    nothing comes back within the health-check timeout.
  - `/health` runs the same generation unless `SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION` is
    false. Its default is true (`EnvBool(True)` in `environ.py`); with it off, `/health`
    answers 200 at once.
- **No `/ready` in the fork.** The fork has `/health` and `/health_generate`, but no `/ready`.
- **Keys.** The key is compared in constant time, and failures get 401. OPTIONS requests and
  paths starting with `/health`, `/ready` or `/metrics` are exempt, so `/v1/models` needs the
  key.
- **Readiness.** The SGLang adapter polls `/ready` (Phase 3 notes). The KTransformers adapter
  polls `/health` with `SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION=false`, so a poll never runs a
  generation on a slow server.

### Local endpoints that forward (checked 2026-10-19)

- **Ollama.** The Phase 0 notes record that a signed-in server forwards cloud-backed models to
  the hosted service, and that `/api/tags` marks them with `remote_host` and `remote_model`.
  - Cloud tags are either `cloud` (for example `glm-5.3-flash:cloud`) or end in `-cloud` (for
    example `gpt-oss:120b-cloud`, with `gpt-oss:20b-cloud` alongside it). Source:
    https://ollama.com/library/gpt-oss/tags.
- **llama-swap.** Source: https://raw.githubusercontent.com/mostlygeek/llama-swap/main/config-schema.json.
  - `peers` is a map of peer servers. Each peer requires `proxy` (a URI) and `models` (a list).
    Optional fields:
    - `apiKey`, sent as a bearer key and as `x-api-key`;
    - `tailcatKey`, for `tailcat://` URLs only;
    - `filters` (`stripParams`, `setParams`);
    - `timeouts`.
  - A model's `proxy` defaults to `http://localhost:${PORT}`, where `${PORT}` is the port
    llama-swap assigns. Other `${name}` macros come from `macros`.
  - `aliases` give a model more names.
  - Only the config file shows forwarding, so an endpoint names it in `engine_config`.

### Harness deny rules (checked 2026-10-19)

- **Claude Code.** Source: https://code.claude.com/docs/en/permissions.
  - Read and Edit rules use gitignore syntax:
    - `//path` is absolute;
    - `~/path` is under the home directory;
    - `/path` is relative to the settings source (for CLI flags, the primary working
      directory);
    - `path` and `./path` are relative to the current directory.
  - A single-segment rule such as `Read(secrets/**)` matches at any depth.
  - Rules are evaluated deny, then ask, then allow.
  - A Read deny also blocks Edit and Write (v2.1.208 and v2.1.228 and later).
  - Windows paths are normalized to POSIX form.
  - Grep and Glob apply the Read deny rules, and deny rules also cover recognized Bash file
    commands and redirects.
  - The adapter passes `Read(<p>)` and `Edit(<p>)` in `--disallowedTools`.
- **OpenCode.** Source: https://opencode.ai/docs/permissions/.
  - In patterns, `*` matches zero or more characters and `?` matches one.
  - The last matching rule wins.
  - `read` matches the file path. Its defaults allow everything except `*.env` and `*.env.*`,
    with `*.env.example` allowed again.
  - The docs do not say whether patterns match relative or absolute paths, so the adapter
    emits each pattern both bare and as `*/<pattern>`.
- **Pi.** Sources: `extensions.md`, `src/core/extensions/types.ts` and
  `src/core/tools/read.ts` (the Phase 8 sources).
  - A `tool_call` handler can change a call's input or block it with
    `ToolCallEventResult {block?, reason?, terminate?}`.
  - A handler that throws blocks the tool.
  - Events carry `toolName` and `input`. The read tool's input is `{path, offset?, limit?}`,
    with the path relative or absolute.
- **Oh My Pi.** Sources: `extensibility/extensions/types.ts` and `shared-events.ts`.
  - `tool_call` events cover Bash, Read, Edit, Write, Grep, Glob and custom tools.
  - Handlers return `ToolCallEventResult {block?: boolean, reason?: string}`.

### Offline enforcement on Linux CI (checked 2026-10-19)

- **Runner pin.** `ubuntu-latest` moves to Ubuntu 26.04 gradually from 2026-10-19 to
  2026-11-19
  (https://github.blog/changelog/2026-09-17-ubuntu-26-generally-available-and-latest-migration),
  so the workflow keeps `ubuntu-24.04` pinned.
- **Unprivileged namespaces.** Hosted 24.04 images keep the AppArmor restriction on them;
  runner-images PR #14412 (merged 2026-07-20) installs a profile only when
  `/proc/sys/kernel/apparmor_restrict_unprivileged_userns` reads 1.
  - Under the restriction, `unshare -rn` fails with
    `write failed /proc/self/uid_map: Operation not permitted`.
  - The workflow therefore sets `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`
    before the C-006 and C-007 runs. The Phase 1 notes above give the trade-off.
- **Loopback.** A fresh network namespace has `lo` down (https://lwn.net/Articles/580893/), so
  the claim tests run `ip link set lo up` inside it first.
- **Not covered.** A network namespace does not block AF_UNIX sockets (systemd.exec(5)). A
  daemon socket can still reach the network, and the namespace alone does not prevent that.

### Observing connections from Python (checked 2026-10-19)

- **Audit events.** Source: the Python 3.15 audit events table.
  - Network: `socket.connect` (also raised by `connect_ex`), `socket.sendto`,
    `socket.sendmsg`, `socket.getaddrinfo`, `socket.gethostbyname`, `socket.gethostbyaddr`,
    `socket.getnameinfo`, `socket.bind`, `socket.__new__`.
  - Processes: `subprocess.Popen`, `os.exec`, `os.posix_spawn`, `os.system`, `os.fork`,
    `_winapi.CreateProcess`.
- **What hooks cannot do.** Hooks apply per interpreter and cannot be removed (PEP 578). The
  docs call them "not suitable for implementing a sandbox". A child that starts a new
  interpreter has no hooks (inferred).
- **Gaps in CPython main.**
  - On Windows, asyncio's default event loop connects through `_overlapped.ConnectEx`, which
    raises no audit event.
  - asyncio skips `getaddrinfo` for IP literals.
  - C extensions that open their own sockets bypass hooks.

  The claim recorder therefore also wraps the socket functions, and on Linux the evidence
  comes from the network namespace.

### Secret patterns (checked 2026-10-19)

| Source | License | Latest | Usable as data |
|---|---|---|---|
| gitleaks `config/gitleaks.toml` (about 220 rules) | MIT | v8.30.1 (2026-03-21) | yes, with its notice |
| Betterleaks `config/betterleaks.toml` | MIT | v1.9.0 (2026-09-29) | yes |
| detect-secrets (27 plugins) | Apache-2.0 | 1.5.0 (2024-05-06) | yes, with NOTICE; its CLI verifies found secrets over the network unless `--no-verify` |
| Kingfisher (YAML rules) | Apache-2.0 | active (last push 2026-10-13) | yes; `--no-validate` turns network validation off |
| Nosey Parker | Apache-2.0 | archived | historical only |
| TruffleHog | AGPL-3.0 | - | no |
| secrets-patterns-db | CC-BY-SA-4.0 (with AGPL-derived parts) | last push 2025-08-06 | no |

- **What the redactor uses.** `stepswitch.redact` vendors no rule file and calls no scanner.
  - Its formats are short regular expressions written for Python's `re` from the high-signal
    rule descriptions: private-key blocks, cloud access-key IDs, code-host tokens,
    chat-workspace tokens, JWTs, model-hub tokens, PyPI and npm tokens, and payment keys.
  - Each format is tested against true and false positives in `tests/unit/test_redact.py`.
- **Weak markers.** A bare `sk-` prefix is weak, so the redactor pairs it with a randomness
  test. So does every `name = value` assignment.
- **Entropy thresholds (Shannon bits per character).**
  - detect-secrets defaults to 4.5 for base64 and 3.0 for hex strings.
  - gitleaks sets a minimum per rule, from 2 to 3.5; its generic rule uses 3.5.
  - The redactor counts a value as random when it mixes letters and digits at 3.0 or more, or
    when it has at least 32 characters at 3.5 or more.

## Verified during Phase 10

These are the facts behind the launch work: the docs site (ADR 0032), and the installers, the
release workflow and the C-017 measurement (ADR 0033). Each was read in the project's
documentation, package metadata or source on 2026-10-19.

### Docs site tools (checked 2026-10-19)

| Tool | Latest (date) | License | Status |
|---|---|---|---|
| MkDocs 1.x | 1.6.1 (2024-08-30) | BSD-2-Clause | no release since |
| MkDocs 2.0 | pre-releases only, 2.0.dev0 (2026-08-28) to 2.0.dev6 (2026-09-15) | none in the wheel | not released |
| Material for MkDocs | 9.7.7 (2026-07-17) | MIT | maintenance mode since 9.7.0 (2025-11-11); critical and security fixes until 2027-05-05 |
| Zensical | 0.0.71 (2026-10-16) | MIT | alpha; the 0.1 "dependable release line" is planned for 2026-11-05 |
| Sphinx | 9.1.0 (2025-12-31) | BSD-2-Clause | maintained |
| MyST-Parser | 5.1.0 (2026-05-13) | MIT | maintained |
| Furo | 2025.12.19 | MIT | maintained |

- **MkDocs 2.0.dev6** (wheel read directly): a rewrite with no plugin API that reads no config
  file yet. It stops with "Found mkdocs.yml config, but mkdocs 2.0 pre-release is installed".
  Material for MkDocs pins `mkdocs<2` since 9.7.5.
- **Zensical**: `zensical new|build|serve`. It reads `zensical.toml` and "natively reads
  mkdocs.yml". The default theme loads web fonts from a font CDN in the reader's browser
  unless `[project.theme] font = false`. The 0.0.71 sdist has no telemetry or update check.
- **Sphinx 9.1.0 needs Python ≥ 3.12; 9.0.4 supports 3.11.** With `sphinx>=9.0.4,<10` in a
  dependency group, `uv lock` forks: 9.0.4 below Python 3.12, 9.1.0 from 3.12 (checked in
  uv.lock).
- **MyST-Parser 5.1.0** requires Sphinx `>=8,<10`, docutils `>=0.20,<0.23` and markdown-it-py
  `~=4.2`. **Furo 2025.12.19** requires Sphinx `>=7,<10`. Its wheel has no network code and no
  external fonts.
- **Math.** `sphinx.ext.mathjax` loads MathJax from cdn.jsdelivr.net on pages that contain
  math. `mathjax_path` can point at a vendored copy. MyST parses math only with the
  `dollarmath` or `amsmath` extensions.
- **Observed in the build of `docs/site`** (Sphinx 9.1.0, MyST-Parser 5.1.0, Furo):
  - A link to a local file that is not a page becomes a download, copied under `_downloads/`.
  - `myst_heading_anchors` gives headings slugs, so `[text](#heading)` links resolve.
  - The `source-read` event hands the handler `(app, docname, source)`, where `source` is a
    one-item list. Replacing `source[0]` changes the text that is parsed.
  - JSON code blocks holding placeholders (`[TaskSummary, ...]`) raise a highlighting
    warning, which `-W` turns into an error.
  - No built page or stylesheet references a remote asset. The only remote URLs are plain
    links, such as the theme's footer credits.
- Sources: https://pypi.org/project/mkdocs/#history,
  https://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/,
  https://zensical.org/docs/compatibility/mkdocs/, https://pypi.org/project/Sphinx/,
  https://pypi.org/project/myst-parser/, https://pypi.org/project/furo/,
  https://www.sphinx-doc.org/en/master/extdev/event_callbacks.html,
  https://myst-parser.readthedocs.io/en/latest/syntax/cross-referencing.html

### Release workflow (checked 2026-10-19)

- **`pypa/gh-action-pypi-publish` v1.14.2 (2026-07-29).**
  - The job needs `permissions: id-token: write`, with no stored credentials. The README's
    example sets `environment: {name: pypi, url: https://pypi.org/p/<name>}`.
  - The action is a container action, so it runs on Linux jobs only, and not in jobs with
    `container:`.
  - PEP 740 attestations are on by default with trusted publishing since v1.11.0.
  - `verbose` and `print-hash` are on by default since v1.14.0.
- **`uv build`** builds the sdist, then the wheel from that sdist. The docs recommend
  `uv build --no-sources` before publishing.
- **Hatch `artifacts`** includes files the VCS ignores and is not affected by `exclude`.
  Because the wheel is built from the sdist, the sdist must list the built dashboard. Verified:
  `uv build --no-sources` with `[tool.hatch.build.targets.sdist] artifacts =
  ["dashboard/dist/**"]` gave a wheel holding `stepswitch/_dashboard/index.html` and an sdist
  with neither `.stepswitch/` nor `node_modules/`.
- **The GitHub CLI** sends pseudonymous telemetry by default from 2.91.0 (2026-04-22), and its
  docs do not exempt CI. `GH_TELEMETRY=false` or `DO_NOT_TRACK=true` turns it off.
  `gh release create <tag> <files> --verify-tag --notes-file <file>` creates a release from an
  existing tag.
- **git-cliff 2.14.2** (2026-09-18) is MIT OR Apache-2.0 and ships wheels on the package
  index, so `uvx git-cliff@2.14.2` runs it. `--latest --strip header` prints the newest
  release's notes without the changelog header.
- **Pinned actions.** These are the commits the tags point to, from `git ls-remote` on
  2026-10-19:

  | Action | Tag | Commit |
  |---|---|---|
  | actions/checkout | v7.0.1 | 3d3c42e5aac5ba805825da76410c181273ba90b1 |
  | actions/setup-node | v7.1.0 | 949feb2413d6458794dcd2491c4babbbce0c15c1 |
  | astral-sh/setup-uv | v10.3.0 | 1c37ad07a6a961277cf70c0d37d6f313000f5884 |
  | actions/upload-artifact | v7.0.2 | cf430e030ddbb5b0abf93d22962f4752f3646cd9 |
  | actions/download-artifact | v8.0.2 | 9000827ccba6bdab643e8b6fd33ac0654aef8333 |
  | pypa/gh-action-pypi-publish | v1.14.2 | dc37677b2e1c63e2034f94d8a5b11f265b73ba33 |

- Sources: https://github.com/pypa/gh-action-pypi-publish,
  https://docs.pypi.org/attestations/producing-attestations/,
  https://docs.astral.sh/uv/guides/integration/github/,
  https://hatch.pypa.io/latest/config/build/#artifacts,
  https://docs.github.com/en/github-cli/github-cli/github-cli-telemetry,
  https://git-cliff.org/docs/usage/args

### uv installers (0.13.1, checked 2026-10-19)

- **`install.sh`.** Pinned at `https://astral.sh/uv/0.13.1/install.sh`, SHA-256
  `7c482d409673a53f4d9bd34ae95c9166be3b8d89ca82f27dd7bca3cab457afff` (downloaded and hashed).
  - It embeds a SHA-256 per artifact and checks it with `sha256sum`, but skips the check
    when `sha256sum` is missing.
  - It takes `--quiet`, `UV_INSTALL_DIR`, and `UV_NO_MODIFY_PATH=1`, which leaves shell
    profiles alone. The `--no-modify-path` flag is deprecated in favour of the variable.
- **`install.ps1`** checks no hash. The release attaches each archive with a `.sha256` file:

  | Archive | SHA-256 |
  |---|---|
  | uv-x86_64-pc-windows-msvc.zip | 3b2661a463a5baea7f5076ea2b25ab71d77f36a6c0d809e5350651792f80a672 |
  | uv-aarch64-pc-windows-msvc.zip | ec7846a5939b4654afee24f2d82bf6fde71bd7a6952d7748f1135ab301dd0a49 |

- `uv tool install <package>` installs into an isolated environment. `uv tool dir --bin`
  prints the directory of its executables.
- Sources: https://docs.astral.sh/uv/getting-started/installation/,
  https://docs.astral.sh/uv/reference/installer/,
  https://github.com/astral-sh/uv/releases/tag/0.13.1

### Community files (checked 2026-10-19)

- **Contributor Covenant 3.0** is the current version: announced 2025-07-28, licensed
  CC BY-SA 4.0. Source: https://www.contributor-covenant.org/version/3/0/code_of_conduct/
- **Issue forms** are YAML files in `.github/ISSUE_TEMPLATE/`. `name`, `description` and `body`
  are required. A `config.yml` there sets `blank_issues_enabled` and `contact_links`.
- **PR templates** are read from `pull_request_template.md` in the root, `docs/` or `.github/`.
- **Community health files** (SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md) are looked up
  in `.github/`, then the root, then `docs/`. Private vulnerability reporting is available for
  public repositories.
- Sources: https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms,
  https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file

### llama.cpp threads and the CPU build (b11669, checked 2026-10-19)

- **How llama.cpp counts cores.** In `common/common.cpp` at tag b11669, the default thread
  count is `common_cpu_get_num_math()`. Except on hybrid CPUs, it returns
  `common_cpu_get_num_physical_cores()`.
  - On Linux, that function reads `/sys/devices/system/cpu/cpu<N>/topology/thread_siblings`
    for N = 0, 1, … until a file is missing, and counts the distinct sets.
  - Neither function reads the CPU affinity mask or the cgroup CPU quota. A container limited
    to some of the CPUs still counts every core of the machine.
- **Observed in `python:3.12-slim`** started with `--cpuset-cpus=0-7` on the development host:
  - sysfs lists every CPU of the host;
  - the sibling sets of CPUs 0–7 are 0-1, 2-3, 4-5 and 6-7, so four physical cores;
  - `os.sched_getaffinity(0)` has eight CPUs, while Python 3.12's `os.cpu_count()` counts the
    whole machine.
- **cgroup v2 quotas.** `/sys/fs/cgroup/cpu.max` holds `<quota> <period>`, or `max` without a
  quota.
- **The Linux CPU asset** is `llama-b11669-bin-ubuntu-x64.tar.gz`, SHA-256
  `cf07c1c112d709bd67e522017471de57ed0ec8bea031d732c5422351c07f65ac`. Its ELF headers need
  `libgomp.so.1`, `libssl.so.3`/`libcrypto.so.3`, `libstdc++.so.6` and glibc ≥ 2.34. The slim
  Python images have no `libgomp.so.1` (`apt-get install libgomp1`).
- Since v0.7.0, `llama-server` listens on 127.0.0.1:9931 by default. The adapter passes
  `--host` and `--port` itself.
- Sources: https://github.com/ggml-org/llama.cpp/blob/b11669/common/common.cpp,
  https://github.com/ggml-org/llama.cpp/releases/tag/b11669,
  https://docs.kernel.org/admin-guide/cgroup-v2.html

## Verified during Phase 11

### Claude Code on its own login, for native bench runs (2.1.296, checked 2026-10-19)

Sources: `claude --help` (2.1.296 on Windows) and https://code.claude.com/docs/en/authentication
and https://code.claude.com/docs/en/memory.

- **`--bare` cannot use a subscription login.** The help text says it skips hooks, LSP, plugin
  sync, attribution, auto-memory, background prefetches, keychain reads and CLAUDE.md
  auto-discovery, and that "auth is strictly ANTHROPIC_API_KEY or apiKeyHelper via --settings
  (OAuth and keychain are never read)". The authentication page adds that bare mode does not
  read `CLAUDE_CODE_OAUTH_TOKEN` either.
- **`--safe-mode`** starts "with all customizations (CLAUDE.md, skills, installed plugins,
  hooks, MCP servers, custom commands and agents, output styles, workflows, custom themes,
  keybindings, and more) disabled". Admin-managed policy settings still apply, and "Auth,
  model selection, built-in tools and plugins, and permissions work normally". It sets
  `CLAUDE_CODE_SAFE_MODE=1`.
- **Other isolation flags.**
  - `--setting-sources <sources>` takes a comma-separated list of `user`, `project`, `local`.
  - `--no-session-persistence` keeps sessions off the disk, so they cannot be resumed. It
    works only with `--print`.
  - `--model` takes an alias or a full model name.
- **Authentication precedence.**
  1. Cloud provider credentials, when `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` or
     `CLAUDE_CODE_USE_FOUNDRY` is set.
  2. `ANTHROPIC_AUTH_TOKEN`.
  3. `ANTHROPIC_API_KEY`. In `-p` mode, "the key is always used when present".
  4. `apiKeyHelper`.
  5. `CLAUDE_CODE_OAUTH_TOKEN`.
  6. Profile and federation credentials (`ANTHROPIC_PROFILE`, or both
     `ANTHROPIC_FEDERATION_RULE_ID` and `ANTHROPIC_ORGANIZATION_ID`).
  7. The subscription login from `/login`.

  A native run therefore has to come without any of the variables above, so the subscription
  login is the credential used. `minimal_env` passes none of them: it is an allowlist.
- **Where the login lives.** On Windows it is `%USERPROFILE%\.claude\.credentials.json`; on
  Linux, `~/.claude/.credentials.json`; on macOS, the Keychain. With `CLAUDE_CONFIG_DIR` set,
  Claude Code reads the file under that directory instead. A native run therefore leaves
  `CLAUDE_CONFIG_DIR` unset and keeps `USERPROFILE`/`HOME`, which `minimal_env` passes.
- **CLAUDE.md files above the working directory load at launch.** "Claude Code loads
  `CLAUDE.md` and `CLAUDE.local.md` from your current working directory and every directory
  above it." Bench and task worktrees live in the project's state directory, so a harness run
  there reads the enclosing repository's CLAUDE.md. That includes the Phase 8 live runs in this
  repository. `--safe-mode` turns it off.
- **Auto memory.** `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, or the setting `autoMemoryEnabled:
  false`. It is stored in `~/.claude/projects/<project>/memory/`, per git repository.
- **The `result` line's usage**, as recorded in `tests/fixtures/harness/claude-code/recorded-live.jsonl`:
  - `usage` has `input_tokens` (uncached), `cache_creation_input_tokens`,
    `cache_read_input_tokens`, `output_tokens` and `output_tokens_details.thinking_tokens`.
  - `modelUsage` maps each model name to `inputTokens`, `outputTokens`, `cacheReadInputTokens`,
    `cacheCreationInputTokens`, `webSearchRequests`, `costUSD`, `contextWindow`,
    `maxOutputTokens`, `thinkingTokens`, `canonicalModel`, `provider` and `costBasis`.
  - `total_cost_usd`, `num_turns`, `duration_ms`, `duration_api_ms` and `subagent_stats` are
    also reported.
  - Input as the ledger counts it (cached tokens included) is `inputTokens +
    cacheCreationInputTokens + cacheReadInputTokens`.

### Engine tool-call parse failures on Ollama (0.34.4, observed 2026-10-20)

- **What happened.** In the first Phase 11 comparison run, 5 of the router's 42 task runs ended
  on `model_error`, all the same way:
  1. Ollama answered an edit step of gpt-oss:120b with HTTP 500, `error parsing tool call:
     raw='{"path":…,"edits":[…]}'` or `raw='{"patch":"*** Begin Patch…"}'`. The model had
     answered, but the server could not parse the tool call it generated. The client's two
     retries of the same request failed the same way.
  2. The agent failed over to qwen2.5:32b, which Ollama stopped with `prediction aborted, token
     repeat limit reached`.
  3. With both workers out, the task failed.
- **The fallback tool protocol avoids the engine's parser.** The same edit request was sent
  three times to gpt-oss:120b through `FallbackModel`, with the agent's own tool specs. Each
  time the reply was a valid `edit` action with `path` and `edits`, in 2.5 to 3.4 s. On Ollama
  that protocol sends a `format` JSON Schema, so the reply is constrained to JSON.
- Ollama reports these failures as a plain 500. Only the message tells them apart from other
  server errors.

## Verified during Phase 12

### Ollama native requests: `num_ctx`, and a model that stopped answering (0.34.4, checked 2026-10-20)

Sources: plain `POST /api/chat` requests (`stream: false`, `options.num_predict` 40) to the
workstation's local Ollama server, version from `GET /api/version`; the Ollama row in the
table above for the request and reply fields.

- qwen2.5:32b answered with a run of `@` characters at `num_ctx` 32768, which is the context
  the server gave it when a request set none. At `num_ctx` 8192 and 16384 it answered normally
  (a short greeting, `done: true`, `done_reason: "stop"`). gpt-oss:120b answered normally on
  the same server throughout.
- With a JSON Schema `format`, as the fallback tool protocol sends, the same model returned
  HTTP 200 with `done: false` and empty content. Without a `format`, it returned HTTP 500 with
  an error saying the token repeat limit was reached.
- A non-streamed reply that is complete has `done: true` and a `done_reason`. A reply with
  `done: false` is not a finished answer, so Stepswitch treats it as a model error (ADR 0037).
- Setting `options.num_ctx` in a request chose the context for that request. This repeats the
  rule already recorded above: set `num_ctx` explicitly and never assume the server's default.
  The agent's requests had not set it (ADR 0037); from Phase 12 the worker's `context` from the
  catalog is sent as `options.num_ctx` on every Ollama native request.

### The `*** Begin Patch` format: two reference implementations (checked 2026-10-20)

Sources:

- https://github.com/openai/gpt-oss at commit 5681a6b6552635e7b29cde94a61ca6a7aee90ebd:
  `gpt_oss/tools/apply_patch.py` (Python) and `gpt_oss/tools/apply_patch.md` (the tool
  description given to the model).
- https://github.com/openai/codex at commit 091008729d5572712ac08964a9602eb71cf31428:
  `codex-rs/core/assets/tools/apply_patch.lark` (the grammar) and `codex-rs/apply-patch/src/`
  (`parser.rs`, `file_update.rs`, `seek_sequence.rs`; Rust).

Grammar (`apply_patch.lark`):

```text
start: begin_patch hunk+ end_patch
begin_patch: "*** Begin Patch" LF
end_patch: "*** End Patch" LF?
hunk: add_hunk | delete_hunk | update_hunk
add_hunk: "*** Add File: " filename LF add_line+
delete_hunk: "*** Delete File: " filename LF
update_hunk: "*** Update File: " filename LF change_move? change?
add_line: "+" /(.*)/ LF
change_move: "*** Move to: " filename LF
change: (change_context | change_line)+ eof_line?
change_context: ("@@" | "@@ " /(.+)/) LF
change_line: ("+" | "-" | " ") /(.*)/ LF
eof_line: "*** End of File" LF
```

Behaviour of the two implementations:

- **Sentinels.** Both need `*** Begin Patch` first and `*** End Patch` last. The Rust parser
  also accepts the patch inside a heredoc (`<<EOF`, `<<'EOF'` or `<<"EOF"` first, `EOF` last)
  and trims the marker lines.
- **Old and new text.** A hunk's old text is its context and `-` lines; its new text is its
  context and `+` lines. Context lines stay as they are in the file; only the removed and
  added lines change. Added lines are written as given.
- **Matching.** The old text is matched as whole lines: exactly, then ignoring trailing
  whitespace, then ignoring leading and trailing whitespace. The Rust one adds a fourth pass
  that reads typographic dashes (U+2010 to U+2015, U+2212), quotes (U+2018 to U+201B,
  U+201C to U+201F) and odd spaces (U+00A0, U+2002 to U+200A, U+202F, U+205F, U+3000) as
  their ASCII forms.
- **Search.** The search moves forward: each hunk is looked for after the end of the previous
  one, and the first match wins. Neither checks for a second match. An `@@ <line>` header is
  looked for first, and the hunk's old text after that line.
- **`*** End of File`.** The Rust code searches only from the position where the old text
  would end the file; the comment on it mentions a fallback that the code does not have. The
  Python code tries the end first and then searches from the current position.
- **Only added lines.** The Rust code appends them to the end of the file. The Python code
  inserts them at the search position: after the `@@` line, or at the top of the file for a
  first hunk without one.

What Stepswitch takes (spec/edit-format.md §4.3, ADR 0038): the grammar, the forward search,
the `@@` line, context lines kept from the file, and all four matching passes. It refuses a
hunk that matches more than once instead of taking the first match. It accepts a missing
sentinel, and after a looser match it gives the added lines the file's indentation. A hunk with
only added lines goes after its `@@` line, as the Python code puts it, or at the end of the
file without one, as the Rust code does. `*** End of File` falls back as in the Python code.

### gpt-oss on Ollama: a new user turn costs the prompt cache (0.34.4, observed 2026-10-20)

Observed from the server's own counters (`prompt_eval_count` and the cached share the client
derives from it), not from reading the chat template:

- Within one tool-calling chain, each request of the router's dev runs found all but the newest
  tool result in the server's prompt cache.
- When a user message followed earlier assistant turns, the next request was shorter than the
  one before (about 300 to 600 tokens), and the cache matched only the system prompt and the
  first user message (about 1,000 tokens): the rendered prompt no longer carries the earlier
  turns' reasoning, so it differs from the cached prefix at the first assistant turn.
- In the dev runs before the fix (`bench/results/2026-10-20-dev-router`) one such turn per task
  run, the nudge after a text answer, made 35% of the router's uncached input. The agent now
  avoids that turn when the tests passed after an edit (ADR 0024, ADR 0038 bookkeeping fix).

## Verified during Phase 13

### SWE-bench Verified and its Mini subset (checked 2026-10-20)

Sources: the dataset `SWE-bench/SWE-bench_Verified` (split `test`, 500 rows) and the subset
`MariusHobbhahn/swe-bench-verified-mini` (50 rows), read through the dataset hub's rows service
(https://datasets-server.huggingface.co/rows, pages of at most 100 rows) and its API for the
revision (https://huggingface.co/api/datasets/<name>, field `sha`).

- The Mini subset has 50 instance ids, 25 from django and 25 from sphinx. Its authors chose it
  to match the full Verified set's spread of difficulty and resolve rates with small images.
  Every id is an instance of Verified; `bench/suites/swebench-verified-mini.yaml` lists them.
- A row holds the repository, base commit, problem statement, hints, the gold patch, the test
  patch and the FAIL_TO_PASS and PASS_TO_PASS test names. Only the problem statement reaches
  an agent; the harness applies the test patch itself when it grades.
- The rows are third-party text. The bench caches them in the state directory and records the
  revision and a hash of the rows it used; it never commits them.

### The SWE-bench harness (swebench 5.0.2, checked 2026-10-20)

Sources: the package installed from PyPI into the harness volume (`swebench/cli/evaluate.py`,
`swebench/harness/run_evaluation.py`, `harness/docker_utils.py`, `harness/constants`), and a
graded run of one instance.

- `swebench eval <dataset .json> -p preds.jsonl --run-id R -j N -t SECONDS -i ID` grades
  predictions (`--gold` grades the reference patches). A prediction is a JSON line with
  `instance_id`, `model_name_or_path` and `model_patch`.
- The per-instance report is `logs/run_evaluation/<run id>/<model, "/" read as "__">/<id>/
  report.json` under the working directory, with `resolved`, `patch_successfully_applied` and
  `tests_status`. A summary `<model>.<run id>.json` goes to the report directory.
- Instance images are `swebench/sweb.eval.x86_64.<id, "__" read as "_1776_">:latest`. They are
  pulled when missing and never removed by `eval`.
- Each graded instance runs in a container named `sweb.eval.<id>.<run id>`. The harness stops
  and removes it by running the `docker` command line, so the grading container needs it on
  `PATH`. Without it the grade still finished, but the container stayed up and the run exited
  with an error.

### SWE-bench instance images (checked 2026-10-20)

Sources: `docker run` of the django and sphinx images of the dev and Mini instances.

- `/testbed` is a checkout at the base commit, with one more empty commit on top. The task's
  diff is taken against that `HEAD`.
- The project's interpreter is `/opt/miniconda3/envs/testbed/bin/python`, a conda
  environment (Python 3.6.13 for the django 3.1 instances), with the project installed in
  editable mode by an `easy-install.pth` that points at `/testbed`. A worktree elsewhere
  therefore imports `/testbed` unless `PYTHONPATH` puts the worktree first (ADR 0042).
- django runs its own runner (`tests/runtests.py --settings=test_sqlite`), which takes dotted
  labels (`backends.base.test_operations`), not file paths, and prints unittest output.
  With the smoke task's change applied, its whole suite took 398 s: 12,598 tests passed, 3
  failed, 2 errored and 1,048 were skipped. The import map chose 11 test files for that
  change's two files, in 2.7 s.

### mini-SWE-agent (2.4.6, checked 2026-10-20)

Sources: the package installed from PyPI (`minisweagent/config/benchmarks/swebench.yaml`,
`models/litellm_model.py`) and a run of one instance.

- `mini-extra swebench --subset <dataset dir> --split test --filter '^<id>$' -o <out> -w 1 -m
  <model> -c swebench.yaml -c <key>=<value>` runs instances. It writes `preds.json` and
  `<id>/<id>.traj.json`, and submits an empty patch when a limit or an exception stops it.
- The SWE-bench config: tool calling (one `bash` tool, parallel calls allowed), `step_limit`
  250, `cost_limit` 3.0, 60 s per command, commands run in the instance's own container
  through the `docker` command line (`environment_class: docker`).
- A model without a price needs `MSWEA_COST_TRACKING=ignore_errors`.
- Its dependency litellm, resolved to 1.106.0 on that day, imports the `mcp` package inside
  `litellm.completion` without depending on it, so every call failed with
  `ModuleNotFoundError: No module named 'mcp'` and the run submitted an empty patch. With
  mcp 2.3.0 installed, a tool-calling completion against the local server worked. The harness
  volume pins mini-swe-agent 2.4.6, litellm 1.106.0 and mcp 2.3.0.
- Tool calls on the local server (observed on the dev set, 2026-10-20). With the default
  config, three of six gpt-oss:120b runs ended with `litellm.InternalServerError: ...
  error parsing tool call: raw='...'`: the server answered HTTP 500 because it could not parse
  the tool call the model wrote (the same failure ADR 0035 works around for Stepswitch). In
  one of them the call's patch held the instance's fix, and its JSON ended with a stray `]`
  (`invalid character ']' after object key:value pair`). The others ended at the step limit
  (250), on `RepeatedFormatError`, or submitted.
- Text actions. The package also ships `config/benchmarks/swebench_backticks.yaml`: the
  SWE-bench prompt with one command per reply in a fenced ```` ```mswea_bash_command ````
  block. Neither the installed file nor upstream's main branch sets `model_class`, so it is
  used with `--model-class litellm_textbased` (`models/__init__.py`,
  `_MODEL_CLASS_MAPPING`), a client that sends no tools and whose default `action_regex`
  reads that block. The v2 migration guide gives `mini-extra swebench -c
  swebench_backticks.yaml` for text-based parsing. Sources: the installed package;
  https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/docs/advanced/v2_migration.md ;
  https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/config/benchmarks/swebench_backticks.yaml

### Running the bench in Linux containers on a Windows host (checked 2026-10-20)

- Docker Desktop's Linux containers reach the host's model server at `host.docker.internal`
  with `--add-host host.docker.internal:host-gateway`. A container that starts others (the
  harness, mini-SWE-agent) gets the host's socket at `/var/run/docker.sock` and a static
  `docker` command line (27.0.3) from the harness volume.
- The harness must not run under Windows Python: it writes the test patch with CRLF line ends
  (swebench issue 652). The bench runs it in a Linux container.
- The local model server (0.34.4) answers one request at a time, so task runs go one after
  another; grading a run overlaps with the next run's agent.
