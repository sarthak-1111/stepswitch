// Typed client for the task API. docs/API.md is the contract: every route, query parameter and
// JSON shape below comes from it. All requests are same-origin. The access token lives in an
// HttpOnly cookie that the server sets; this module never reads, stores or sends it.

// ------------------------------------------------------------------------------------------
// Shapes
// ------------------------------------------------------------------------------------------

/** Known statuses; the board also shows any other status the server reports. */
export const TASK_STATUSES = [
  "queued",
  "routing",
  "running",
  "verifying",
  "done",
  "failed",
  "needs-human",
  "stopped",
] as const;
export type KnownTaskStatus = (typeof TASK_STATUSES)[number];
export type TaskStatus = KnownTaskStatus | (string & {});
export const FINAL_STATUSES: ReadonlySet<string> = new Set(["done", "failed", "needs-human", "stopped"]);

export interface TokenCounts {
  input: number;
  output: number;
  cached_input: number;
  reasoning: number;
}

export interface TaskSummary {
  id: string;
  created: number;
  updated: number;
  status: TaskStatus;
  stop_reason: string | null;
  track: string;
  instruction: string;
  worker: string | null;
  steps: number;
  escalations: number;
  switches: number;
  tokens: TokenCounts;
  /** Elapsed time of the task: so far while it runs, the measured total once it has ended. */
  wall_seconds: number;
  gpu_seconds: number;
  summary: string;
  cancel_requested: boolean;
}

export interface Budgets {
  tokens: number | null;
  wall_seconds: number | null;
  gpu_seconds: number | null;
  escalations: number | null;
}

/** Body of POST /api/tasks; only `instruction` is required. */
export interface NewTask {
  instruction: string;
  repo?: string;
  track?: string;
  acceptance?: string[];
  budgets?: Budgets;
  worker?: string | null;
  workers?: string[];
  max_steps?: number;
  review?: boolean;
  allow_remote?: boolean;
  offline?: boolean;
}

export interface TaskCreated {
  id: string;
  status: TaskStatus;
}

/** POST /api/runs/{id}/replay: no field replays the run; `from_step` and `worker` fork it. */
export interface ReplayRequest {
  from_step?: number;
  worker?: string;
}

export interface ReplayCreated {
  id: string;
  status: TaskStatus;
  replay_of: string;
  from_step: number | null;
  worker: string | null;
}

export interface CancelAnswer {
  id: string;
  status: TaskStatus;
  cancel_requested: boolean;
}

export interface Estimate {
  input_tokens?: number;
  output_tokens?: number;
  seconds?: number;
  tokens?: number;
  switch_tokens?: number;
  switch_seconds?: number;
}

export interface Candidate {
  worker: string;
  tier: string;
  level: number;
  score: number | null;
  estimate: Estimate | null;
  excluded: string | null;
  endpoint?: string;
  model?: string;
  prior?: number | null;
  bonus?: number;
  relaxed?: boolean;
}

export interface SwitchCost {
  tokens: number;
  seconds: number;
}

export interface Decision {
  task_id: string;
  step: number;
  ts: number;
  step_type: string;
  chosen: string | null;
  previous: string | null;
  switched: boolean;
  reason: string;
  rule: string | null;
  level: number;
  expected_gain: number | null;
  switch_cost: SwitchCost | null;
  estimate: Estimate | null;
  candidates: Candidate[];
  notes: string[];
}

export interface LedgerRow {
  step: number;
  ts: number;
  step_type: string;
  endpoint: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  reasoning_tokens: number;
  /** Model time of this one call. */
  wall_seconds: number;
  gpu_seconds: number;
  usage_source: string | null;
}

export interface GateResult {
  name: string;
  status: string;
  seconds: number;
  summary: string;
  details: Record<string, unknown>;
  report: string;
}

export interface Verdict {
  passed: boolean;
  failed_gate: string | null;
  report: string;
  results: GateResult[];
}

export interface TaskResult {
  summary: string;
  changed_files: string[];
  diff: string;
  verdict: Partial<Verdict> | null;
  [key: string]: unknown;
}

export interface TaskDetail extends TaskSummary {
  spec: Record<string, unknown>;
  result: TaskResult | null;
  decisions: Decision[];
  ledger: LedgerRow[];
  events: number;
}

export interface TrajectoryEvent {
  seq: number;
  ts: number;
  type: string;
  step: number | null;
  data: Record<string, unknown>;
}

export interface TrajectorySummary {
  id: string;
  status: TaskStatus;
  instruction: string;
  created: number;
  events: number;
}

export interface Worker {
  id: string;
  endpoint: string;
  model: string;
  format: string;
  tier: string;
  level: number;
  family: string | null;
  local: boolean;
  roles: string[];
  tool_protocol: string | null;
  tool_parser: string | null;
  context: number | null;
  gpu: boolean;
  pin_only: boolean;
  prefill_tps: number | null;
  decode_tps: number | null;
}

export interface WorkersAnswer {
  workers: Worker[];
  notes: string[];
}

export interface Agent {
  task_id: string;
  status: TaskStatus;
  worker: string | null;
  step: number | null;
  step_type: string | null;
  tokens_per_s: number | null;
  context_used: number | null;
  context: number | null;
  cache_hit: number | null;
  since: number | null;
}

export interface RouterAnswer {
  decisions: Decision[];
  features: Record<string, Record<string, unknown>>;
}

export type LedgerGroupBy = "model" | "task" | "day";

export interface LedgerTotals {
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  reasoning_tokens: number;
  wall_seconds: number;
  gpu_seconds: number;
  steps: number;
}

export interface LedgerGroup extends LedgerTotals {
  key: string;
}

export interface ReferenceCost {
  price_in: number;
  price_out: number;
  avoided: number;
}

export interface LedgerAnswer {
  totals: LedgerTotals;
  groups: LedgerGroup[];
  reference_cost: ReferenceCost | null;
}

export interface ProbeProfile {
  created: number | null;
  format: string | null;
  capabilities: Record<string, unknown>;
}

export interface Endpoint {
  name: string;
  engine: string | null;
  format: string | null;
  formats: string[];
  local: boolean;
  url: string | null;
  auth: string | null;
  models: string[];
  tool_parser: string | null;
  /** `reachable`, `http_status` and `error` (the failure kind) come with ?check=true only. */
  reachable?: boolean | null;
  http_status?: number | null;
  error?: string | null;
  probes: Record<string, ProbeProfile>;
}

export interface ManagedServer {
  model: string;
  engine: string | null;
  base_url: string | null;
  /** Null unless the answer is to ?check=true. */
  alive: boolean | null;
  started: number | null;
  last_used: number | null;
}

export type ModelState = "running" | "starting" | "failed" | "stopped";

export interface InstalledModel {
  id: string;
  name: string;
  ram_gb: number | null;
  roles: string[];
  state?: ModelState | (string & {});
  /** Older answers carried only this flag. */
  running?: boolean;
}

export interface EndpointsAnswer {
  endpoints: Endpoint[];
  managed: ManagedServer[];
  installed: InstalledModel[];
  /** Why a model's server failed to start, by model id. */
  model_errors?: Record<string, string>;
}

export interface ModelStarted {
  model: string;
  status: string;
}

export interface ModelStopped {
  model: string;
  stopped: boolean;
}

export interface BenchRun {
  name: string;
  suite: string | null;
  started: string | null;
  finished: string | null;
  contestants: string[];
  rows: number;
}

export interface Interval {
  low: number;
  high: number;
  confidence: number;
  method: string;
  resamples?: number;
}

/** One leaderboard entry. Runs written by older versions may lack some fields. */
export interface Contestant {
  rank: number;
  contestant: string;
  pass_rate: number;
  passed?: number;
  runs?: number;
  tasks?: number;
  ci?: Interval | null;
  median_wall_seconds?: number | null;
  median_tokens?: number | null;
  input_tokens?: number;
  output_tokens?: number;
  cached_input_tokens?: number;
  cache_hit_rate?: number | null;
  gpu_seconds?: number | null;
  escalation_rate?: number | null;
  reference_cost?: number | null;
  tests_modified?: number;
  statuses?: Record<string, number>;
}

export interface Leaderboard {
  suite?: string | null;
  date?: string | null;
  runs?: number;
  tasks?: number;
  seeds?: number[] | null;
  command?: string | null;
  commit?: string | null;
  version?: string | null;
  scoring?: string | null;
  router_pool?: string[] | null;
  hardware?: Record<string, unknown> | string | null;
  interval?: { confidence?: number; resamples?: number; seed?: number } | null;
  prices?: Record<string, unknown> | null;
  contestants: Contestant[];
}

export interface LeaderboardAnswer {
  runs: BenchRun[];
  run: string | null;
  leaderboard: Leaderboard | null;
  chart: string | null;
}

export interface ClaimRow {
  id: string;
  text: string | null;
  phase: number | null;
  status: string;
  tests: Record<string, string>;
  notes?: Record<string, string>;
  parts?: string[];
}

export interface ProveReport {
  mode?: string;
  created?: string;
  duration_s?: number;
  summary?: Record<string, number>;
  platform?: Record<string, unknown>;
}

export interface ClaimsAnswer {
  source: string | null;
  report: ProveReport | null;
  claims: ClaimRow[];
}

export interface CompatCheck {
  name: string;
  label: string;
}

export type CheckCell = "yes" | "no" | "skipped" | "error";

export interface CompatResult {
  file: string;
  model: string;
  family: string | null;
  engine: string | null;
  engine_version: string | null;
  format: string | null;
  date: string | null;
  passes: boolean;
  failed: string[];
  tool_path: string | null;
  tool_parser: string | null;
  checks: Record<string, CheckCell | string>;
}

export interface LocalProbe {
  endpoint: string;
  model: string;
  created: number | null;
  capabilities: Record<string, unknown>;
}

export interface CompatibilityAnswer {
  ci_set: string[];
  checks: CompatCheck[];
  results: CompatResult[];
  local: LocalProbe[];
  /** Probe files that could not be read. */
  problems?: string[];
}

export interface AuditRow {
  id: number;
  ts: number;
  method: string;
  scheme: string;
  host: string;
  port: number | null;
  path: string;
  purpose: string | null;
  task_id: string | null;
  allowed: boolean;
  local: boolean;
  status: number | null;
  bytes_out: number | null;
  bytes_in: number | null;
  error: string | null;
}

export interface AuditTotals {
  requests: number;
  denied: number;
  non_local: number;
}

export interface AuditAnswer {
  rows: AuditRow[];
  totals: AuditTotals;
}

export interface ScienceCase {
  case: string;
  status: string;
}

export interface ScienceReport {
  task_id: string;
  run: number;
  step: number | null;
  status: string;
  summary: string;
  table: string;
  cases: ScienceCase[];
  plots: string[];
}

export interface ScienceAnswer {
  reports: ScienceReport[];
}

// Payloads of the live event stream (GET /api/events).
export interface TrajectoryNotice {
  task_id: string;
  seq: number;
  ts: number;
  type: string;
  step: number | null;
}

export type LedgerNotice = LedgerRow & { task_id: string };

export interface LiveEventMap {
  task: TaskSummary;
  trajectory: TrajectoryNotice;
  ledger: LedgerNotice;
  decision: Decision;
}

// ------------------------------------------------------------------------------------------
// Errors and the access-token state
// ------------------------------------------------------------------------------------------

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let unauthorized = false;
const authListeners = new Set<() => void>();

/** True once any request was answered with 401: the dashboard then shows the token screen. */
export function isUnauthorized(): boolean {
  return unauthorized;
}

export function subscribeUnauthorized(listener: () => void): () => void {
  authListeners.add(listener);
  return () => authListeners.delete(listener);
}

function markUnauthorized(): void {
  if (unauthorized) return;
  unauthorized = true;
  authListeners.forEach((listener) => listener());
}

// ------------------------------------------------------------------------------------------
// Requests
// ------------------------------------------------------------------------------------------

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${path}?${text}` : path;
}

function segment(value: string): string {
  return encodeURIComponent(value);
}

/** The message of an error body: {"error": "..."}, or a validation `detail` list. */
function errorMessage(body: unknown, status: number, statusText: string): string {
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    if (typeof record.error === "string" && record.error) return record.error;
    if (typeof record.detail === "string" && record.detail) return record.detail;
    if (Array.isArray(record.detail)) {
      const parts = record.detail.map((item) => {
        if (item && typeof item === "object") {
          const entry = item as Record<string, unknown>;
          const where = Array.isArray(entry.loc) ? entry.loc.filter((p) => p !== "body").join(".") : "";
          const msg = typeof entry.msg === "string" ? entry.msg : JSON.stringify(entry);
          return where ? `${where}: ${msg}` : msg;
        }
        return String(item);
      });
      if (parts.length) return parts.join("; ");
    }
  }
  return `The server answered ${status}${statusText ? ` ${statusText}` : ""}.`;
}

async function request<T>(
  method: "GET" | "POST",
  url: string,
  options: { body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = { method, credentials: "same-origin", headers, signal: options.signal };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("The dashboard server did not answer. Is `ssw dash` still running?", 0);
  }
  const text = await response.text();
  let body: unknown = undefined;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = undefined;
    }
  }
  if (response.status === 401) {
    markUnauthorized();
    throw new ApiError(errorMessage(body, 401, response.statusText), 401);
  }
  if (!response.ok) throw new ApiError(errorMessage(body, response.status, response.statusText), response.status);
  if (body === undefined) throw new ApiError("The server sent an answer that is not JSON.", response.status);
  return body as T;
}

function get<T>(path: string, query?: Query, signal?: AbortSignal): Promise<T> {
  return request<T>("GET", withQuery(path, query), { signal });
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("POST", path, { body: body ?? {} });
}

// ------------------------------------------------------------------------------------------
// Routes (docs/API.md)
// ------------------------------------------------------------------------------------------

export const api = {
  /** GET /api/tasks?limit=50&status=<status>, newest first. */
  tasks: (query: { limit?: number; status?: string } = {}, signal?: AbortSignal) =>
    get<{ tasks: TaskSummary[] }>("/api/tasks", query, signal),

  /** POST /api/tasks: 201 {"id", "status": "queued"}. */
  createTask: (body: NewTask) => post<TaskCreated>("/api/tasks", body),

  /** GET /api/tasks/{id}: a TaskSummary plus spec, result, decisions, ledger and event count. */
  task: (id: string, signal?: AbortSignal) => get<TaskDetail>(`/api/tasks/${segment(id)}`, undefined, signal),

  /** POST /api/tasks/{id}/cancel: 202, or 404 (unknown task) and 409 (task has ended). */
  cancelTask: (id: string) => post<CancelAnswer>(`/api/tasks/${segment(id)}/cancel`),

  /** GET /api/tasks/{id}/events?after=<seq>: the trajectory, in order. */
  taskEvents: (id: string, after?: number, signal?: AbortSignal) =>
    get<{ events: TrajectoryEvent[] }>(`/api/tasks/${segment(id)}/events`, { after }, signal),

  /** POST /api/runs/{id}/replay: 201, a replay of the run (or a fork of it) as a new task;
   * 404 unknown run, 422 a run that cannot be replayed as asked. */
  replayRun: (id: string, body: ReplayRequest = {}) =>
    post<ReplayCreated>(`/api/runs/${segment(id)}/replay`, body),

  /** GET /api/trajectories?limit=50 */
  trajectories: (limit?: number, signal?: AbortSignal) =>
    get<{ trajectories: TrajectorySummary[] }>("/api/trajectories", { limit }, signal),

  /** GET /api/workers */
  workers: (signal?: AbortSignal) => get<WorkersAnswer>("/api/workers", undefined, signal),

  /** GET /api/agents: the latest model call of every task that has not ended. */
  agents: (signal?: AbortSignal) => get<{ agents: Agent[] }>("/api/agents", undefined, signal),

  /** GET /api/router?task=<id>&limit=200, newest first. */
  router: (query: { task?: string; limit?: number } = {}, signal?: AbortSignal) =>
    get<RouterAnswer>("/api/router", query, signal),

  /** GET /api/ledger?group=task|model|day&since=<epoch> */
  ledger: (query: { group?: LedgerGroupBy; since?: number } = {}, signal?: AbortSignal) =>
    get<LedgerAnswer>("/api/ledger", query, signal),

  /** GET /api/endpoints (with check=true the server also tests whether each one answers). */
  endpoints: (query: { check?: boolean } = {}, signal?: AbortSignal) =>
    get<EndpointsAnswer>("/api/endpoints", query, signal),

  /** POST /api/models/{id}/start: 202, the model loads in the background; 404 unknown, 409 not installed. */
  startModel: (id: string) => post<ModelStarted>(`/api/models/${segment(id)}/start`),

  /** POST /api/models/{id}/stop */
  stopModel: (id: string) => post<ModelStopped>(`/api/models/${segment(id)}/stop`),

  /** GET /api/leaderboard?run=<name>; the newest run when `run` is not given. */
  leaderboard: (run?: string, signal?: AbortSignal) =>
    get<LeaderboardAnswer>("/api/leaderboard", { run }, signal),

  /** GET /api/claims: the newest prove report. */
  claims: (signal?: AbortSignal) => get<ClaimsAnswer>("/api/claims", undefined, signal),

  /** GET /api/compatibility: probe results and the local probes of this project. */
  compatibility: (signal?: AbortSignal) => get<CompatibilityAnswer>("/api/compatibility", undefined, signal),

  /** GET /api/audit?task=<id>&limit=200&denied=true, newest first. */
  audit: (query: { task?: string; limit?: number; denied?: boolean } = {}, signal?: AbortSignal) =>
    get<AuditAnswer>("/api/audit", query, signal),

  /** GET /api/science?task=<id>: numerical diff reports, newest first. */
  science: (query: { task?: string } = {}, signal?: AbortSignal) =>
    get<ScienceAnswer>("/api/science", query, signal),
};

/** URL of GET /api/runs/{task_id}/files/{path}: a file from a task's run directory. */
export function runFileUrl(taskId: string, path: string): string {
  const parts = path.split("/").filter(Boolean).map(segment).join("/");
  return `/api/runs/${segment(taskId)}/files/${parts}`;
}

/** URL of the live event stream, GET /api/events (Server-Sent Events). */
export const EVENTS_URL = "/api/events";

/**
 * True for same-origin /api/ paths: the only addresses this dashboard loads, so a value from
 * the server can never make the browser contact another host.
 */
export function isApiUrl(url: string | null | undefined): url is string {
  return typeof url === "string" && url.startsWith("/api/");
}

const IMAGE_FILE = /\.(png|svg|jpe?g|gif|webp|avif)$/i;

/**
 * True for run files that can be shown with <img>: /api/runs/... paths of image files. Other run
 * files are served as text or as downloads.
 */
export function isRunImageUrl(url: string | null | undefined): url is string {
  return typeof url === "string" && url.startsWith("/api/runs/") && IMAGE_FILE.test(url.split(/[?#]/)[0]);
}
