// @ts-check
// The shared core of the Pi and Oh My Pi extensions (SPEC §12 items 4-5, ADR 0029).
//
// A harness run started by `ssw run --harness pi|oh-my-pi` has its own `ssw proxy`, bound to the
// task, and a generated agent directory whose provider `stepswitch` lists the proxy's slots and
// workers. The Python adapter passes the rest in variables:
//
//   STEPSWITCH_PROXY_URL        http://127.0.0.1:<port>
//   STEPSWITCH_PROXY_TOKEN      the run's own token (bearer)
//   STEPSWITCH_SESSION          the task the run's requests belong to
//   STEPSWITCH_CONTEXT_TOKENS   the largest context window among the workers
//   STEPSWITCH_ROLE_MODELS      Oh My Pi only: JSON, harness role -> `stepswitch/<slot>`
//
// Without the first two the extensions register nothing, so loading one by hand is harmless.
// Plain ES modules with JSDoc types: both hosts load them without a build step (ADR 0029).

export const PROVIDER = "stepswitch";
export const MAIN_SLOT = "ssw-auto";
export const DEFAULT_CONTEXT = 128000;
export const MAX_OUTPUT_TOKENS = 16384;

/**
 * @typedef {Record<string, string | undefined>} Env
 * @typedef {{model?: string, reason?: string, previous?: string, failed?: string,
 *   transcript_tokens?: number, tools?: boolean}} RouteAsk
 * @typedef {{worker: string, context?: number | null, step: number, step_type: string,
 *   reason: string}} RouteAnswer
 * @typedef {{url: string, session: string,
 *   route(ask: RouteAsk, signal?: AbortSignal): Promise<RouteAnswer>}} Proxy
 */

/**
 * The run's proxy, or undefined outside a Stepswitch run.
 * @param {Env} env
 * @returns {Proxy | undefined}
 */
export function connect(env) {
  const url = env.STEPSWITCH_PROXY_URL;
  const token = env.STEPSWITCH_PROXY_TOKEN;
  if (!url || !token) return undefined;
  const session = env.STEPSWITCH_SESSION ?? "";
  const headers = {
    authorization: `Bearer ${token}`,
    "content-type": "application/json",
    "x-stepswitch-session": session,
  };
  return {
    url,
    session,
    async route(ask, signal) {
      const reply = await fetch(`${url}/stepswitch/v1/route`, {
        method: "POST",
        headers,
        body: JSON.stringify(ask),
        signal,
      });
      if (!reply.ok) throw new Error(`ssw proxy routed nothing: HTTP ${reply.status}`);
      return /** @type {RouteAnswer} */ (await reply.json());
    },
  };
}

/** @param {Env} env */
export function contextTokens(env) {
  const value = Number(env.STEPSWITCH_CONTEXT_TOKENS);
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_CONTEXT;
}

/** @param {number} contextWindow */
export function outputTokens(contextWindow) {
  return Math.min(MAX_OUTPUT_TOKENS, Math.floor(contextWindow / 4));
}

/**
 * @param {Env} env
 * @returns {Record<string, string>}
 */
export function roleModels(env) {
  try {
    const parsed = JSON.parse(env.STEPSWITCH_ROLE_MODELS ?? "{}");
    return parsed !== null && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

/**
 * A token estimate for the router (characters / 4, the proxy's own estimate).
 * @param {unknown} messages
 */
export function estimateTokens(messages) {
  return Math.ceil(JSON.stringify(messages ?? []).length / 4);
}
