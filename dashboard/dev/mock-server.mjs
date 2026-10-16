#!/usr/bin/env node
// Dev-only mock of the Stepswitch task API (docs/API.md), for working on the dashboard without
// the backend. It serves the built dashboard from dist/ (index.html for every other non-API
// path) and answers each /api/ route from the fixtures in dev/fixtures/. Plain Node, no
// dependencies, loopback only. Not part of the build.
//
//   node dev/mock-server.mjs [--port 7777] [--token <token>] [--no-live] [--close-stream]
//
// --token <token>  require the access token like `ssw dash`: open /?token=<token> once, which sets
//                  the HttpOnly cookie and redirects; without it every /api/ route answers 401.
// --no-live        do not send the sample live events (one of each type) on the event stream.
// --close-stream   end each event stream after its first events (the browser then reconnects).
//                  For headless render checks: a virtual-time budget waits for open requests,
//                  and an event stream that stays open would hold the clock forever.

import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DIST = resolve(HERE, "..", "dist");
const FIXTURES = join(HERE, "fixtures");
const HOST = "127.0.0.1";

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const PORT = Number(option("port", process.env.PORT ?? "7777"));
const TOKEN = option("token", "");
const LIVE = !process.argv.includes("--no-live");
const CLOSE_STREAM = process.argv.includes("--close-stream");
const COOKIE = `ssw_token_${PORT}`;
const FINAL = new Set(["done", "failed", "needs-human", "stopped"]);

const load = (name) => JSON.parse(readFileSync(join(FIXTURES, name), "utf8"));
const clone = (value) => JSON.parse(JSON.stringify(value));

const state = {
  tasks: load("tasks.json").tasks,
  details: load("task-details.json"),
  events: load("events.json"),
  workers: load("workers.json"),
  agents: load("agents.json"),
  ledger: load("ledger.json"),
  endpoints: load("endpoints.json"),
  leaderboard: load("leaderboard.json"),
  claims: load("claims.json"),
  compatibility: load("compatibility.json"),
  audit: load("audit.json").rows,
  science: load("science.json").reports,
  live: load("live.json"),
  liveApplied: false,
};

// Registry models that exist but are not installed (start answers 409 for them).
const NOT_INSTALLED = new Set(["qwen2.5-coder-1.5b"]);

// ------------------------------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------------------------------

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const now = () => Date.now() / 1000;
const taskOf = (id) => state.tasks.find((task) => task.id === id);

function eventsOf(id) {
  return state.events[id] ?? [];
}

/** Decisions of a task, rebuilt from its trajectory (the fixtures store them once). */
function decisionsOf(id) {
  return eventsOf(id)
    .filter((event) => event.type === "decision")
    .map((event) => ({ task_id: id, step: event.step, ts: event.ts, ...event.data }));
}

/** Ledger rows of a task, rebuilt from its `step` events. */
function ledgerOf(id) {
  return eventsOf(id)
    .filter((event) => event.type === "step")
    .map((event) => ({
      step: event.step,
      ts: event.ts,
      step_type: event.data.step_type,
      endpoint: event.data.endpoint,
      model: event.data.model,
      ...event.data.usage,
      wall_seconds: event.data.wall_seconds,
      gpu_seconds: event.data.gpu_seconds,
    }));
}

function featuresOf(id) {
  return eventsOf(id).find((event) => event.type === "classify")?.data.features;
}

function intParam(query, name, fallback) {
  const raw = query.get(name);
  if (raw === null || raw === "") return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0) throw new HttpError(422, `${name} must be a whole number`);
  return value;
}

function escapeXml(text) {
  return String(text).replace(/[<>&"']/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" })[c]);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new HttpError(422, "the body is not valid JSON");
  }
}

// ------------------------------------------------------------------------------------------
// Live events
// ------------------------------------------------------------------------------------------

const clients = new Set();
let lastEventId = 0;

function send(res, type, data) {
  lastEventId += 1;
  res.write(`id: ${lastEventId}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
}

function broadcast(type, data) {
  for (const res of clients) send(res, type, data);
}

/** Moves the running sample task one step on: the state the live sample events announce. */
function applyLive() {
  if (state.liveApplied) return;
  state.liveApplied = true;
  const { task, events, ledger } = state.live;
  const index = state.tasks.findIndex((item) => item.id === task.id);
  if (index >= 0) state.tasks[index] = clone(task);
  state.events[task.id] = [...eventsOf(task.id), ...clone(events)];
  const agent = state.agents.agents.find((row) => row.task_id === task.id);
  if (agent) {
    Object.assign(agent, {
      step: ledger.step,
      step_type: ledger.step_type,
      tokens_per_s: Math.round((ledger.output_tokens / ledger.wall_seconds) * 10) / 10,
      context_used: ledger.input_tokens,
      cache_hit: Math.round((ledger.cached_input_tokens / ledger.input_tokens) * 1000) / 1000,
    });
  }
}

function eventStream(req, res) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  res.write("retry: 3000\n\n");
  clients.add(res);
  if (LIVE) applyLive();
  // The stream starts with the current state of tasks that have not ended ...
  for (const task of state.tasks) if (!FINAL.has(task.status)) send(res, "task", task);
  // ... then one sample event of each type.
  if (LIVE) {
    send(res, "trajectory", state.live.trajectory);
    send(res, "ledger", state.live.ledger);
    send(res, "decision", state.live.decision);
  }
  if (CLOSE_STREAM) {
    clients.delete(res);
    res.end();
    return;
  }
  const keepAlive = setInterval(() => res.write(": keep-alive\n\n"), 15_000);
  req.on("close", () => {
    clearInterval(keepAlive);
    clients.delete(res);
  });
}

// ------------------------------------------------------------------------------------------
// Routes
// ------------------------------------------------------------------------------------------

function listTasks({ query }) {
  const status = query.get("status");
  const limit = intParam(query, "limit", 50);
  const tasks = state.tasks
    .filter((task) => !status || task.status === status)
    .sort((a, b) => b.created - a.created)
    .slice(0, limit);
  return [200, { tasks }];
}

async function createTask({ req }) {
  const body = await readBody(req);
  if (typeof body.instruction !== "string" || !body.instruction.trim()) {
    throw new HttpError(422, "instruction: a non-empty string is required");
  }
  const created = now();
  const stamp = new Date(created * 1000).toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
  const id = `t${stamp}-${randomBytes(3).toString("hex")}`;
  const summary = {
    id,
    created,
    updated: created,
    status: "queued",
    stop_reason: null,
    track: body.track ?? "general",
    instruction: body.instruction.trim(),
    worker: body.worker ?? null,
    steps: 0,
    escalations: 0,
    switches: 0,
    tokens: { input: 0, output: 0, cached_input: 0, reasoning: 0 },
    wall_seconds: 0,
    gpu_seconds: 0,
    summary: "",
    cancel_requested: false,
  };
  state.tasks.unshift(summary);
  state.details[id] = {
    spec: {
      instruction: summary.instruction,
      repo: body.repo ?? "C:/work/project",
      track: summary.track,
      acceptance: body.acceptance ?? [],
      budgets: body.budgets ?? { tokens: null, wall_seconds: null, gpu_seconds: null, escalations: null },
      worker: summary.worker,
      workers: body.workers ?? [],
      max_steps: body.max_steps ?? 40,
      review: body.review ?? true,
      allow_remote: body.allow_remote ?? false,
      offline: body.offline ?? false,
    },
    result: null,
  };
  state.events[id] = [];
  broadcast("task", summary);
  return [201, { id, status: "queued" }];
}

function getTask({ params }) {
  const task = taskOf(params[0]);
  if (!task) throw new HttpError(404, `unknown task ${params[0]}`);
  const detail = state.details[task.id] ?? { spec: {}, result: null };
  return [
    200,
    {
      ...task,
      spec: detail.spec,
      result: detail.result,
      decisions: decisionsOf(task.id),
      ledger: ledgerOf(task.id),
      events: eventsOf(task.id).length,
    },
  ];
}

function cancelTask({ params }) {
  const task = taskOf(params[0]);
  if (!task) throw new HttpError(404, `unknown task ${params[0]}`);
  if (FINAL.has(task.status)) throw new HttpError(409, `task ${task.id} has ended (${task.status})`);
  task.cancel_requested = true;
  task.updated = now();
  if (task.status === "queued") {
    task.status = "stopped";
    task.stop_reason = "cancelled";
    task.summary = "Cancelled before it started.";
  }
  broadcast("task", task);
  return [202, { id: task.id, status: task.status, cancel_requested: true }];
}

function taskEvents({ params, query }) {
  if (!taskOf(params[0])) throw new HttpError(404, `unknown task ${params[0]}`);
  const after = intParam(query, "after", 0);
  return [200, { events: eventsOf(params[0]).filter((event) => event.seq > after) }];
}

function trajectories({ query }) {
  const limit = intParam(query, "limit", 50);
  const rows = state.tasks
    .slice()
    .sort((a, b) => b.created - a.created)
    .slice(0, limit)
    .map((task) => ({
      id: task.id,
      status: task.status,
      instruction: task.instruction,
      created: task.created,
      events: eventsOf(task.id).length,
    }));
  return [200, { trajectories: rows }];
}

function router({ query }) {
  const task = query.get("task");
  const limit = intParam(query, "limit", 200);
  if (task && !taskOf(task)) throw new HttpError(404, `unknown task ${task}`);
  const ids = task ? [task] : state.tasks.map((item) => item.id);
  const decisions = ids
    .flatMap((id) => decisionsOf(id))
    .sort((a, b) => b.ts - a.ts)
    .slice(0, limit);
  const features = {};
  for (const id of ids) {
    const found = featuresOf(id);
    if (found) features[id] = found;
  }
  return [200, { decisions, features }];
}

function ledger({ query }) {
  const group = query.get("group") || "model";
  if (!(group in state.ledger)) throw new HttpError(422, "group must be task, model or day");
  return [200, state.ledger[group]];
}

function endpoints({ query }) {
  const answer = clone(state.endpoints);
  if (query.get("check") === "true") {
    const checks = {
      "ws-local": { reachable: true, http_status: 200, error: null },
      "bench-box": { reachable: false, http_status: null, error: "connect_error" },
      "lab-gpu": { reachable: true, http_status: 200, error: null },
    };
    for (const endpoint of answer.endpoints) {
      Object.assign(endpoint, checks[endpoint.name] ?? { reachable: false, http_status: null, error: "timeout" });
    }
    for (const server of answer.managed) server.alive = server.model === "qwen3-4b-instruct-2507";
  }
  return [200, answer];
}

function modelAction({ params }) {
  const [id, action] = params;
  const model = state.endpoints.installed.find((item) => item.id === id);
  if (!model) {
    if (NOT_INSTALLED.has(id)) throw new HttpError(409, `model ${id} is not installed`);
    throw new HttpError(404, `unknown model ${id}`);
  }
  if (action === "start") {
    delete state.endpoints.model_errors?.[id];
    model.state = "starting";
    model.running = false;
    setTimeout(() => {
      model.state = "running";
      model.running = true;
      if (!state.endpoints.managed.some((server) => server.model === id)) {
        state.endpoints.managed.push({
          model: id,
          engine: "llamacpp",
          base_url: `http://127.0.0.1:${50400 + state.endpoints.managed.length}`,
          alive: null,
          started: now(),
          last_used: null,
        });
      }
    }, 3000);
    return [202, { model: id, status: "starting" }];
  }
  const wasRunning = model.state === "running" || model.state === "starting";
  model.state = "stopped";
  model.running = false;
  state.endpoints.managed = state.endpoints.managed.filter((server) => server.model !== id);
  return [200, { model: id, stopped: wasRunning }];
}

function leaderboard({ query }) {
  const { runs, boards } = state.leaderboard;
  const run = query.get("run") || runs[0]?.name || null;
  if (run && !(run in boards)) throw new HttpError(404, `unknown run ${run}`);
  return [
    200,
    {
      runs,
      run,
      leaderboard: run ? boards[run] : null,
      chart: run ? `/api/leaderboard/${encodeURIComponent(run)}/pass-rate.svg` : null,
    },
  ];
}

function chart({ params }, res) {
  const board = state.leaderboard.boards[params[0]];
  if (!board) throw new HttpError(404, `unknown run ${params[0]}`);
  const rows = board.contestants;
  const labelW = 250;
  const barW = 380;
  const rowH = 28;
  const top = 38;
  const width = labelW + barW + 70;
  const height = top + rowH * Math.max(1, rows.length) + 26;
  const x = (rate) => labelW + rate * barW;
  const parts = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="sans-serif" font-size="12">`,
    `<title>Pass rate: ${escapeXml(board.suite)}</title>`,
    `<rect width="100%" height="100%" fill="#ffffff"/>`,
    `<text x="${labelW}" y="20" fill="#333333">Pass rate with interval (${escapeXml(board.suite)})</text>`,
  ];
  for (let tick = 0; tick <= 100; tick += 25) {
    parts.push(
      `<line x1="${x(tick / 100)}" y1="${top - 6}" x2="${x(tick / 100)}" y2="${height - 22}" stroke="#e3e3e3"/>`,
      `<text x="${x(tick / 100)}" y="${height - 8}" text-anchor="middle" fill="#555555">${tick}%</text>`,
    );
  }
  rows.forEach((entry, index) => {
    const y = top + index * rowH;
    parts.push(
      `<text x="${labelW - 8}" y="${y + 16}" text-anchor="end" fill="#222222">${escapeXml(entry.contestant)}</text>`,
      `<rect x="${labelW}" y="${y + 5}" width="${(entry.pass_rate * barW).toFixed(1)}" height="15" fill="#4a7fc1"/>`,
    );
    if (entry.ci) {
      parts.push(
        `<line x1="${x(entry.ci.low)}" y1="${y + 12.5}" x2="${x(entry.ci.high)}" y2="${y + 12.5}" stroke="#111111" stroke-width="1.5"/>`,
      );
    }
    parts.push(
      `<text x="${x(entry.pass_rate) + 6}" y="${y + 16}" fill="#222222">${Math.round(entry.pass_rate * 100)}%</text>`,
    );
  });
  parts.push("</svg>");
  res.writeHead(200, { "Content-Type": "image/svg+xml; charset=utf-8", "Cache-Control": "no-store" });
  res.end(parts.join("\n"));
  return null;
}

function audit({ query }) {
  const task = query.get("task");
  const denied = query.get("denied") === "true";
  const limit = intParam(query, "limit", 200);
  const matching = state.audit.filter((row) => (!task || row.task_id === task) && (!denied || !row.allowed));
  return [
    200,
    {
      rows: matching.slice(0, limit),
      totals: {
        requests: matching.length,
        denied: matching.filter((row) => !row.allowed).length,
        non_local: matching.filter((row) => !row.local).length,
      },
    },
  ];
}

function science({ query }) {
  const task = query.get("task");
  return [200, { reports: state.science.filter((report) => !task || report.task_id === task) }];
}

/** A run file: image paths get a generated SVG plot, other files plain text. */
function runFile({ params }, res) {
  const [taskId, path] = params;
  const parts = path.split("/");
  if (parts.some((part) => part === ".." || part === "" || part.includes("\\"))) {
    throw new HttpError(403, "path outside the run directory");
  }
  if (!taskOf(taskId)) throw new HttpError(404, `unknown task ${taskId}`);
  const name = parts.at(-1);
  if (!/\.(png|svg)$/i.test(name)) {
    res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" });
    res.end(`mock run file ${parts.join("/")}\n`);
    return null;
  }
  let seed = 0;
  for (const char of name) seed = (seed * 31 + char.charCodeAt(0)) % 9973;
  const width = 480;
  const height = 260;
  const pad = 36;
  const points = (shift, noise) =>
    Array.from({ length: 81 }, (_, i) => {
      const t = i / 80;
      const peak = Math.exp(-((t - 0.45 - shift) ** 2) / 0.004) + 0.55 * Math.exp(-((t - 0.7 - shift) ** 2) / 0.002);
      const wobble = noise * Math.sin((i + seed) * 1.7);
      return `${(pad + t * (width - 2 * pad)).toFixed(1)},${(height - pad - (peak + wobble) * (height - 2 * pad) * 0.8).toFixed(1)}`;
    }).join(" ");
  const shift = (seed % 7) / 600;
  const svg = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="sans-serif" font-size="11">`,
    `<title>${escapeXml(name)}</title>`,
    `<rect width="100%" height="100%" fill="#ffffff"/>`,
    `<line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" stroke="#888888"/>`,
    `<line x1="${pad}" y1="${pad / 2}" x2="${pad}" y2="${height - pad}" stroke="#888888"/>`,
    `<polyline fill="none" stroke="#1f5fa8" stroke-width="2" points="${points(0, 0)}"/>`,
    `<polyline fill="none" stroke="#c2410c" stroke-width="1.5" stroke-dasharray="5 3" points="${points(shift, 0.01)}"/>`,
    `<text x="${pad + 8}" y="${pad / 2 + 10}" fill="#1f5fa8">expected</text>`,
    `<text x="${pad + 72}" y="${pad / 2 + 10}" fill="#c2410c">actual</text>`,
    `<text x="${width / 2}" y="${height - 10}" text-anchor="middle" fill="#444444">${escapeXml(name)}</text>`,
    "</svg>",
  ].join("\n");
  res.writeHead(200, { "Content-Type": "image/svg+xml; charset=utf-8", "Cache-Control": "no-store" });
  res.end(svg);
  return null;
}

const ROUTES = [
  ["GET", /^\/api\/tasks$/, listTasks],
  ["POST", /^\/api\/tasks$/, createTask],
  ["GET", /^\/api\/tasks\/([^/]+)$/, getTask],
  ["POST", /^\/api\/tasks\/([^/]+)\/cancel$/, cancelTask],
  ["GET", /^\/api\/tasks\/([^/]+)\/events$/, taskEvents],
  ["GET", /^\/api\/trajectories$/, trajectories],
  ["GET", /^\/api\/workers$/, () => [200, state.workers]],
  ["GET", /^\/api\/agents$/, () => [200, state.agents]],
  ["GET", /^\/api\/router$/, router],
  ["GET", /^\/api\/ledger$/, ledger],
  ["GET", /^\/api\/endpoints$/, endpoints],
  ["POST", /^\/api\/models\/([^/]+)\/(start|stop)$/, modelAction],
  ["GET", /^\/api\/leaderboard$/, leaderboard],
  ["GET", /^\/api\/leaderboard\/([^/]+)\/pass-rate\.svg$/, chart],
  ["GET", /^\/api\/claims$/, () => [200, state.claims]],
  ["GET", /^\/api\/compatibility$/, () => [200, state.compatibility]],
  ["GET", /^\/api\/audit$/, audit],
  ["GET", /^\/api\/science$/, science],
  ["GET", /^\/api\/runs\/([^/]+)\/files\/(.+)$/, runFile],
];

// ------------------------------------------------------------------------------------------
// Server
// ------------------------------------------------------------------------------------------

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".txt": "text/plain; charset=utf-8",
  ".map": "application/json",
};

function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

function authorized(req) {
  if (!TOKEN) return true;
  if (req.headers.authorization === `Bearer ${TOKEN}`) return true;
  const cookies = Object.fromEntries(
    (req.headers.cookie ?? "")
      .split(";")
      .map((part) => part.trim().split("="))
      .filter((pair) => pair.length === 2),
  );
  return cookies[COOKIE] === TOKEN;
}

async function serveStatic(res, pathname) {
  let file = resolve(DIST, `.${decodeURIComponent(pathname)}`);
  if (file !== DIST && !file.startsWith(DIST + sep)) file = join(DIST, "index.html");
  try {
    const info = await stat(file);
    if (info.isDirectory()) file = join(DIST, "index.html");
  } catch {
    file = join(DIST, "index.html");
  }
  try {
    const body = await readFile(file);
    res.writeHead(200, { "Content-Type": TYPES[extname(file)] ?? "application/octet-stream", "Cache-Control": "no-store" });
    res.end(body);
  } catch {
    res.writeHead(503, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("dist/ is missing: run `npm run build` in dashboard/ first.\n");
  }
}

async function handle(req, res) {
  const url = new URL(req.url ?? "/", `http://${HOST}:${PORT}`);
  const allowedHosts = new Set([`${HOST}:${PORT}`, `localhost:${PORT}`]);
  if (!allowedHosts.has(req.headers.host ?? "")) return json(res, 403, { error: "forbidden host" });
  const origin = req.headers.origin;
  if (req.method === "POST" && origin && !allowedHosts.has(origin.replace(/^https?:\/\//, ""))) {
    return json(res, 403, { error: "forbidden origin" });
  }

  // Like `ssw dash`: /?token=... stores the token in an HttpOnly cookie and redirects without it.
  if (TOKEN && url.searchParams.has("token")) {
    const given = url.searchParams.get("token");
    url.searchParams.delete("token");
    const headers = { Location: url.pathname + url.search, "Cache-Control": "no-store" };
    if (given === TOKEN) headers["Set-Cookie"] = `${COOKIE}=${TOKEN}; HttpOnly; SameSite=Strict; Path=/`;
    res.writeHead(303, headers);
    return res.end();
  }

  if (!url.pathname.startsWith("/api/")) return serveStatic(res, url.pathname);
  if (!authorized(req)) return json(res, 401, { error: "unauthorized" });
  if (url.pathname === "/api/events" && req.method === "GET") return eventStream(req, res);

  let pathMatched = false;
  for (const [method, pattern, handler] of ROUTES) {
    const match = pattern.exec(url.pathname);
    if (!match) continue;
    pathMatched = true;
    if (method !== req.method) continue;
    try {
      const params = match.slice(1).map((part) => decodeURIComponent(part));
      const answer = await handler({ req, params, query: url.searchParams }, res);
      if (answer) json(res, answer[0], answer[1]);
    } catch (error) {
      if (error instanceof HttpError) json(res, error.status, { error: error.message });
      else json(res, 500, { error: String(error?.message ?? error) });
    }
    return undefined;
  }
  return json(res, pathMatched ? 405 : 404, { error: pathMatched ? "method not allowed" : "not found" });
}

const server = createServer((req, res) => {
  handle(req, res).catch((error) => {
    if (res.headersSent) res.end();
    else json(res, 400, { error: String(error?.message ?? error) });
  });
});

server.listen(PORT, HOST, () => {
  const address = `http://${HOST}:${PORT}/`;
  console.log(`Stepswitch mock API on ${address}${TOKEN ? `?token=${TOKEN}` : ""}`);
  console.log(`Serving ${DIST}${LIVE ? "; the event stream sends one sample event of each type" : ""}.`);
});
