// The Pi and Oh My Pi extensions against a fake host API and a fake `ssw proxy`
// (`node --test test/extensions.test.js`, run by tests/unit/test_adapter_extensions.py).

import assert from "node:assert/strict";
import { createServer } from "node:http";
import { afterEach, test } from "node:test";

import omp from "../omp/index.js";
import pi, { VIRTUAL_MODEL } from "../pi/index.js";

const VARIABLES = [
  "STEPSWITCH_PROXY_URL",
  "STEPSWITCH_PROXY_TOKEN",
  "STEPSWITCH_SESSION",
  "STEPSWITCH_CONTEXT_TOKENS",
  "STEPSWITCH_ROLE_MODELS",
];
const servers = [];

afterEach(() => {
  for (const name of VARIABLES) delete process.env[name];
  for (const server of servers.splice(0)) server.close();
});

/** A proxy that answers every route request with `answer` (and `status`), recording requests. */
async function fakeProxy(answer, status = 200) {
  const seen = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => {
      body += chunk;
    });
    request.on("end", () => {
      seen.push({ url: request.url, headers: request.headers, body: JSON.parse(body || "null") });
      response.writeHead(status, { "content-type": "application/json" });
      response.end(JSON.stringify(answer));
    });
  });
  servers.push(server);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  Object.assign(process.env, {
    STEPSWITCH_PROXY_URL: url,
    STEPSWITCH_PROXY_TOKEN: "ssw-test-token",
    STEPSWITCH_SESSION: "t-1",
  });
  return seen;
}

/** A host API that records what an extension registers. */
function host() {
  const registered = { virtual: [], handlers: {} };
  return {
    registered,
    api: {
      registerVirtualModel: (model) => registered.virtual.push(model),
      registerProvider: () => assert.fail("the provider comes from the generated config"),
      on: (event, handler) => {
        registered.handlers[event] = handler;
      },
    },
  };
}

/** A model registry holding the generated provider's models. */
function registry(ids) {
  const calls = [];
  return {
    calls,
    modelRegistry: {
      find: (provider, id) => {
        calls.push([provider, id]);
        return provider === "stepswitch" && ids.includes(id) ? { provider, id } : undefined;
      },
    },
  };
}

const ANSWER = { worker: "local/coder:32b", context: 32768, step: 3, step_type: "edit", reason: "r" };

test("pi: the routed virtual model asks the proxy and resolves to the chosen worker", async () => {
  const seen = await fakeProxy(ANSWER);
  process.env.STEPSWITCH_CONTEXT_TOKENS = "131072";
  const { api, registered } = host();
  pi(api);
  assert.equal(registered.virtual.length, 1);
  const model = registered.virtual[0];
  assert.equal(model.provider, "stepswitch");
  assert.equal(model.id, VIRTUAL_MODEL);
  assert.equal(model.contextWindow, 131072);
  assert.equal(model.maxTokens, 16384);
  const ctx = registry(["ssw-auto", "local/coder:32b"]);
  const route = await model.route(
    {
      reason: "continuation",
      thinkingLevel: "off",
      state: { kept: true },
      previous: { model: { id: "local/small:3b" } },
      messages: [{ role: "user", content: "Fix the failing test." }],
    },
    ctx,
  );
  assert.deepEqual(route.model, { provider: "stepswitch", id: "local/coder:32b" });
  assert.equal(route.thinkingLevel, "off");
  assert.deepEqual(route.state, { kept: true });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, "/stepswitch/v1/route");
  assert.equal(seen[0].headers.authorization, "Bearer ssw-test-token");
  assert.equal(seen[0].headers["x-stepswitch-session"], "t-1");
  const body = seen[0].body;
  assert.equal(body.model, "ssw-auto");
  assert.equal(body.reason, "continuation");
  assert.equal(body.previous, "local/small:3b");
  assert.equal(body.failed, undefined);
  assert.equal(body.tools, true);
  assert.ok(body.transcript_tokens > 0);
});

test("pi: a retry names the failed worker; an unknown worker falls back to the main slot", async () => {
  const seen = await fakeProxy({ ...ANSWER, worker: "gone/model" });
  const { api, registered } = host();
  pi(api);
  const model = registered.virtual[0];
  assert.equal(model.contextWindow, 128000); // no window passed: the default
  const ctx = registry(["ssw-auto"]);
  const route = await model.route(
    { reason: "retry", failed: { model: { id: "local/coder:32b" }, message: "x" }, messages: [] },
    ctx,
  );
  assert.deepEqual(route.model, { provider: "stepswitch", id: "ssw-auto" });
  assert.equal(seen[0].body.failed, "local/coder:32b");
  await assert.rejects(model.route({ reason: "user", messages: [] }, registry([])), /no model/);
});

test("pi: a refused route (the budget is spent) fails the request", async () => {
  await fakeProxy({ error: "quota" }, 429);
  const { api, registered } = host();
  pi(api);
  await assert.rejects(
    registered.virtual[0].route({ reason: "user", messages: [] }, registry(["ssw-auto"])),
    /HTTP 429/,
  );
});

test("pi and oh-my-pi register nothing outside a Stepswitch run", () => {
  const { api, registered } = host();
  pi(api);
  omp(api);
  assert.equal(registered.virtual.length, 0);
  assert.deepEqual(registered.handlers, {});
});

test("oh-my-pi: every subagent gets the slot of its role", async () => {
  await fakeProxy(ANSWER);
  process.env.STEPSWITCH_ROLE_MODELS = JSON.stringify({
    default: "stepswitch/ssw-auto",
    plan: "stepswitch/ssw-plan",
    advisor: "stepswitch/ssw-review",
  });
  const { api, registered } = host();
  omp(api);
  const spawn = registered.handlers.before_subagent_spawn;
  assert.equal(typeof spawn, "function");
  assert.equal(spawn({ agent: "planner", modelRole: "plan" }).model, "stepswitch/ssw-plan");
  assert.equal(spawn({ agent: "critic", modelRole: "advisor" }).model, "stepswitch/ssw-review");
  assert.equal(spawn({ agent: "x", modelRole: "unknown" }).model, "stepswitch/ssw-auto");
  const plain = spawn({ agent: "explore" });
  assert.equal(plain.model, "stepswitch/ssw-auto");
  assert.match(plain.note, /explore/);
});

test("oh-my-pi: a malformed role map still routes every subagent", async () => {
  await fakeProxy(ANSWER);
  process.env.STEPSWITCH_ROLE_MODELS = "{not json";
  const { api, registered } = host();
  omp(api);
  const spawn = registered.handlers.before_subagent_spawn;
  assert.equal(spawn({ modelRole: "plan" }).model, "stepswitch/ssw-auto");
});
