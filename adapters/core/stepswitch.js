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
//   STEPSWITCH_DENY_RULES       the privacy deny list (`privacy.deny_paths`) as JSON rules
//
// Without the first two the extensions register nothing, so loading one by hand is harmless.
// Plain ES modules with JSDoc types: both hosts load them without a build step (ADR 0029).

import * as fs from "node:fs";
import * as path from "node:path";

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

/**
 * @typedef {{regex: RegExp, dirOnly: boolean}} DenyRule
 */

/**
 * The privacy deny list: `{regex, dirOnly}` rules compiled by Stepswitch from the project's
 * gitignore-style `privacy.deny_paths`, matched against "/"-separated paths relative to the
 * worktree. A rule that does not compile is dropped.
 * @param {Env} env
 * @returns {DenyRule[]}
 */
export function denyRules(env) {
  let parsed;
  try {
    parsed = JSON.parse(env.STEPSWITCH_DENY_RULES ?? "[]");
  } catch {
    return [];
  }
  /** @type {DenyRule[]} */
  const rules = [];
  for (const item of Array.isArray(parsed) ? parsed : []) {
    try {
      rules.push({ regex: new RegExp(String(item.regex)), dirOnly: Boolean(item.dirOnly) });
    } catch {
      // not a JavaScript regular expression
    }
  }
  return rules;
}

/** @param {string} target */
function isDirectory(target) {
  try {
    return fs.statSync(target).isDirectory();
  } catch {
    return false;
  }
}

/**
 * Whether a path (absolute, or relative to `cwd`) is on the deny list: it, or a directory above
 * it, matches a rule. A path outside `cwd` is not the list's business.
 * @param {DenyRule[]} rules
 * @param {string} cwd
 * @param {unknown} target
 */
export function deniedPath(rules, cwd, target) {
  if (rules.length === 0 || typeof target !== "string" || target === "") return false;
  const full = path.resolve(cwd, target);
  const rel = path.relative(path.resolve(cwd), full);
  if (rel === "" || rel.startsWith("..") || path.isAbsolute(rel)) return false;
  const parts = rel.split(path.sep).join("/").split("/").filter((p) => p !== "" && p !== ".");
  for (let depth = 1; depth <= parts.length; depth += 1) {
    const prefix = parts.slice(0, depth).join("/");
    const dir = depth < parts.length || isDirectory(full);
    if (rules.some((rule) => (dir || !rule.dirOnly) && rule.regex.test(prefix))) return true;
  }
  return false;
}

/**
 * The denied path a tool call names, or undefined. File tools name it in `path` (Pi and Oh My
 * Pi; `file_path` and `filePath` are accepted too); a shell command is refused when any of its
 * words is a denied path, which can refuse more than it must, never less.
 * @param {DenyRule[]} rules
 * @param {string} cwd
 * @param {any} event a `tool_call` event: `{toolName, input}`
 * @returns {string | undefined}
 */
export function deniedTarget(rules, cwd, event) {
  const input = event?.input ?? {};
  /** @type {unknown[]} */
  const named = [input.path, input.file_path, input.filePath];
  if (event?.toolName === "bash" || event?.toolName === "powershell") {
    named.push(...String(input.command ?? "").split(/[\s"'`;|&<>()]+/));
  }
  return /** @type {string | undefined} */ (named.find((p) => deniedPath(rules, cwd, p)));
}

/**
 * Registers the `tool_call` hook that blocks tool calls naming a denied path (both hosts have
 * the same hook and result shape: `{block, reason}`).
 * @param {{on(event: "tool_call", handler: (event: any, ctx: any) => any): void}} pi
 * @param {Env} env
 */
export function guardDeniedPaths(pi, env) {
  const rules = denyRules(env);
  if (rules.length === 0) return;
  pi.on("tool_call", (event, ctx) => {
    const denied = deniedTarget(rules, ctx?.cwd ?? process.cwd(), event);
    if (denied === undefined) return undefined;
    return { block: true, reason: `${denied} is excluded by the privacy deny list` };
  });
}
