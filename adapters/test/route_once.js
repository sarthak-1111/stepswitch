// Drives the Pi extension's routed virtual model once against a running `ssw proxy`
// (tests/unit/test_adapter_extensions.py). Arguments: the model IDs the host's registry holds
// (JSON) and the request (JSON; `pad` adds a user message of that many characters). Prints the
// route as JSON.

import pi from "../pi/index.js";

const registered = [];
pi({
  registerVirtualModel: (model) => registered.push(model),
  registerProvider: () => {},
  on: () => {},
});
if (registered.length !== 1) throw new Error(`registered ${registered.length} virtual models`);
const [model] = registered;
const ids = JSON.parse(process.argv[2] ?? "[]");
const { pad = 0, ...request } = JSON.parse(process.argv[3] ?? "{}");
request.messages = [{ role: "user", content: "x".repeat(pad) }];
const route = await model.route(request, {
  modelRegistry: {
    find: (provider, id) => (ids.includes(id) ? { provider, id } : undefined),
  },
});
console.log(JSON.stringify({ contextWindow: model.contextWindow, model: route.model }));
