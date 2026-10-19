// @ts-check
// Stepswitch for Pi (SPEC §12 item 4, ADR 0029).
//
// The Python adapter writes the run's provider (`models.json`: `stepswitch` at the run's
// `ssw proxy`, one model per slot and one per worker with its own context window) and selects
// `stepswitch/routed`, the virtual model this extension adds. Before every request Pi calls its
// `route()`, which asks the proxy's router which worker takes the request
// (`POST /stepswitch/v1/route`) and resolves to that worker's model, so Pi's context limits and
// compaction follow the model that answers. The proxy keeps the decision for the request that
// names the worker.
//
// Its `tool_call` hook also blocks tool calls that name a path of the privacy deny list.
//
// Loaded by the Python adapter with `--no-extensions -e <this file>`.

import {
  MAIN_SLOT,
  PROVIDER,
  connect,
  contextTokens,
  estimateTokens,
  guardDeniedPaths,
  outputTokens,
} from "../core/stepswitch.js";

export const VIRTUAL_MODEL = "routed";

/** @param {import("@earendil-works/pi-coding-agent").ExtensionAPI} pi */
export default function stepswitch(pi) {
  const proxy = connect(process.env);
  if (proxy === undefined) return;
  guardDeniedPaths(pi, process.env);
  const contextWindow = contextTokens(process.env);
  pi.registerVirtualModel({
    provider: PROVIDER,
    id: VIRTUAL_MODEL,
    name: "Stepswitch (routed per request)",
    contextWindow,
    maxTokens: outputTokens(contextWindow),
    /**
     * @param {any} request
     * @param {any} ctx
     */
    async route(request, ctx) {
      const answer = await proxy.route(
        {
          model: MAIN_SLOT,
          reason: request.reason,
          previous: request.previous?.model?.id,
          failed: request.failed?.model?.id,
          transcript_tokens: estimateTokens(request.messages),
          tools: true,
        },
        request.signal,
      );
      const model =
        ctx.modelRegistry.find(PROVIDER, answer.worker) ??
        ctx.modelRegistry.find(PROVIDER, MAIN_SLOT);
      if (model === undefined) throw new Error(`no model ${PROVIDER}/${answer.worker}`);
      return { model, thinkingLevel: request.thinkingLevel, state: request.state };
    },
  });
}
