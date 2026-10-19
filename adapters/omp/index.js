// @ts-check
// Stepswitch for Oh My Pi (SPEC §12 item 5, ADR 0029).
//
// The Python adapter writes the run's provider (`models.yml`: `stepswitch` at the run's
// `ssw proxy`) and model roles (`config.yml`: every role mapped to a slot). This extension adds
// what configuration cannot: the `before_subagent_spawn` hook, which gives every subagent the
// slot of its role (from `STEPSWITCH_ROLE_MODELS`), including subagents whose definition names
// a model of their own, so that the proxy routes each one as that step type. Oh My Pi has no
// virtual models, so the proxy routes every request.
//
// Loaded by the Python adapter with `--trusted-extension <this file>` (an exact allowlist: no
// other extension loads).

import { MAIN_SLOT, PROVIDER, connect, roleModels } from "../core/stepswitch.js";

/** @param {import("@oh-my-pi/pi-coding-agent").ExtensionAPI} pi */
export default function stepswitch(pi) {
  if (connect(process.env) === undefined) return;
  const roles = roleModels(process.env);
  const fallback = roles.default ?? `${PROVIDER}/${MAIN_SLOT}`;
  pi.on(
    "before_subagent_spawn",
    /** @param {any} event */
    (event) => {
      const model = (event.modelRole && roles[event.modelRole]) || fallback;
      return { model, note: `Stepswitch routes ${event.agent ?? "this subagent"} as ${model}` };
    },
  );
}
