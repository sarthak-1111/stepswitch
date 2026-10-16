import { useState, type FormEvent } from "react";
import {
  api,
  FINAL_STATUSES,
  TASK_STATUSES,
  type Budgets,
  type NewTask,
  type TaskCreated,
  type TaskSummary,
} from "../api";
import { Badge, Empty, PageHeader, ResourceView, StatusBadge, TimeText } from "../components";
import { useLiveEvent, useStreamReopened } from "../events";
import { formatDuration, formatInt, stopReasonText, truncate } from "../format";
import { useResource } from "../hooks";
import { Link } from "../router";
import { taskPath } from "../routes";

const KNOWN_STATUSES: ReadonlySet<string> = new Set<string>(TASK_STATUSES);

/** Inserts or replaces a task (newest first); an older copy never replaces a newer one. */
export function upsertTask(list: TaskSummary[], task: TaskSummary): TaskSummary[] {
  const index = list.findIndex((item) => item.id === task.id);
  if (index >= 0) {
    if ((list[index].updated ?? 0) > (task.updated ?? 0)) return list;
    const copy = list.slice();
    copy[index] = { ...list[index], ...task };
    return copy;
  }
  return [task, ...list].sort((a, b) => (b.created ?? 0) - (a.created ?? 0));
}

export function TasksView() {
  const tasks = useResource((signal) => api.tasks({ limit: 200 }, signal), []);
  const [formOpen, setFormOpen] = useState(false);
  const [created, setCreated] = useState<TaskCreated | null>(null);

  useLiveEvent("task", (task) => tasks.update((data) => ({ ...data, tasks: upsertTask(data.tasks, task) })));
  useStreamReopened(() => tasks.reload({ silent: true }));

  const changed = (task: TaskSummary) => tasks.update((data) => ({ ...data, tasks: upsertTask(data.tasks, task) }));

  return (
    <>
      <PageHeader
        title="Tasks"
        lead="Each task from the queue to its final status, updated live."
        actions={
          <button
            type="button"
            className="primary"
            aria-expanded={formOpen}
            aria-controls="new-task"
            onClick={() => setFormOpen((open) => !open)}
          >
            {formOpen ? "Close the form" : "New task"}
          </button>
        }
      />
      {formOpen ? (
        <NewTaskForm
          onCreated={(answer) => {
            setCreated(answer);
            setFormOpen(false);
            tasks.reload({ silent: true });
          }}
          onClose={() => setFormOpen(false)}
        />
      ) : null}
      {created ? (
        <p className="banner banner-ok" role="status">
          Task <Link to={taskPath(created.id)}>{created.id}</Link> is {created.status}.
        </p>
      ) : null}
      <ResourceView
        resource={tasks}
        what="the tasks"
        isEmpty={(data) => data.tasks.length === 0}
        empty={
          <Empty title="No tasks yet">
            <p>
              Start one with <strong>New task</strong>, or from a terminal with <code>ssw run "…"</code>.
            </p>
          </Empty>
        }
      >
        {(data) => <TaskBoard tasks={data.tasks} onChanged={changed} />}
      </ResourceView>
    </>
  );
}

function TaskBoard({ tasks, onChanged }: { tasks: TaskSummary[]; onChanged: (task: TaskSummary) => void }) {
  const others = Array.from(new Set(tasks.map((task) => task.status))).filter((status) => !KNOWN_STATUSES.has(status));
  const columns: string[] = [...TASK_STATUSES, ...others];
  return (
    <div className="board" role="region" aria-label="Task board">
      {columns.map((status) => {
        const items = tasks.filter((task) => task.status === status);
        const headingId = `column-${status}`;
        return (
          <section key={status} className="board-column" aria-labelledby={headingId}>
            <h2 id={headingId} className="board-column-title">
              <StatusBadge status={status} />
              <span className="count">
                {items.length}
                <span className="visually-hidden"> {items.length === 1 ? "task" : "tasks"}</span>
              </span>
            </h2>
            {items.length ? (
              <ul className="card-list">
                {items.map((task) => (
                  <TaskCard key={task.id} task={task} onChanged={onChanged} />
                ))}
              </ul>
            ) : (
              <p className="muted small column-empty">None</p>
            )}
          </section>
        );
      })}
    </div>
  );
}

export function CancelButton({
  task,
  onChanged,
  className = "small danger",
}: {
  task: TaskSummary;
  onChanged: (task: TaskSummary) => void;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (FINAL_STATUSES.has(task.status)) return null;

  async function cancel() {
    setBusy(true);
    setError(null);
    try {
      const answer = await api.cancelTask(task.id);
      onChanged({ ...task, status: answer.status, cancel_requested: answer.cancel_requested });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }

  const waiting = busy || task.cancel_requested;
  return (
    <span className="cancel">
      <button
        type="button"
        className={className}
        onClick={cancel}
        disabled={waiting}
      >
        {waiting ? "Cancelling…" : "Cancel"}
        <span className="visually-hidden"> task {task.id}</span>
      </button>
      {error ? (
        <span className="field-error" role="alert">
          {error}
        </span>
      ) : null}
    </span>
  );
}

function TaskCard({ task, onChanged }: { task: TaskSummary; onChanged: (task: TaskSummary) => void }) {
  const tokens = task.tokens ?? { input: 0, output: 0, cached_input: 0, reasoning: 0 };
  const ended = FINAL_STATUSES.has(task.status);
  return (
    <li className="card task-card">
      <Link to={taskPath(task.id)} className="card-title">
        {truncate(task.instruction || "(no instruction)", 160)}
      </Link>
      <div className="chips">
        <Badge>{task.track}</Badge>
        {task.escalations > 0 ? (
          <Badge tone="orange" title="Moves up the escalation ladder">
            Escalated ×{task.escalations}
          </Badge>
        ) : null}
        {task.switches > 0 ? (
          <Badge tone="purple">
            {task.switches} {task.switches === 1 ? "switch" : "switches"}
          </Badge>
        ) : null}
        {task.stop_reason ? (
          <Badge title={`Stop reason: ${task.stop_reason}`}>{stopReasonText(task.stop_reason)}</Badge>
        ) : null}
        {task.cancel_requested && !ended ? <Badge tone="warn">cancel requested</Badge> : null}
      </div>
      <dl className="card-meta">
        <div>
          <dt>Worker</dt>
          <dd>{task.worker ?? "routed"}</dd>
        </div>
        <div>
          <dt>Steps</dt>
          <dd>{formatInt(task.steps)}</dd>
        </div>
        <div>
          <dt>Tokens</dt>
          <dd>
            <span className="nowrap">{formatInt(tokens.input)} in</span> ·{" "}
            <span className="nowrap">{formatInt(tokens.output)} out</span>
          </dd>
        </div>
        <div>
          <dt title="Elapsed time of the task">Wall time</dt>
          <dd>{formatDuration(task.wall_seconds)}</dd>
        </div>
      </dl>
      <div className="card-foot">
        <span className="muted small">
          <TimeText epoch={task.created} />
        </span>
        <CancelButton task={task} onChanged={onChanged} />
      </div>
    </li>
  );
}

// ------------------------------------------------------------------------------------------
// New task
// ------------------------------------------------------------------------------------------

interface FormState {
  instruction: string;
  repo: string;
  track: string;
  acceptance: string;
  tokens: string;
  wallSeconds: string;
  gpuSeconds: string;
  escalations: string;
  worker: string;
  maxSteps: string;
  allowRemote: boolean;
  review: boolean;
  offline: boolean;
}

const EMPTY_FORM: FormState = {
  instruction: "",
  repo: "",
  track: "general",
  acceptance: "",
  tokens: "",
  wallSeconds: "",
  gpuSeconds: "",
  escalations: "",
  worker: "",
  maxSteps: "",
  allowRemote: false,
  review: true,
  offline: false,
};

function parseAmount(label: string, text: string, whole: boolean): number | null {
  const value = text.trim();
  if (!value) return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0 || (whole && !Number.isInteger(number))) {
    throw new Error(`${label} must be ${whole ? "a whole number" : "a number"}, 0 or more.`);
  }
  return number;
}

/** The POST /api/tasks body: only the fields that were filled in. */
function buildTask(form: FormState): NewTask {
  const instruction = form.instruction.trim();
  if (!instruction) throw new Error("Describe the task in the instruction field.");
  const body: NewTask = { instruction, track: form.track, allow_remote: form.allowRemote };
  if (form.repo.trim()) body.repo = form.repo.trim();
  const acceptance = form.acceptance
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (acceptance.length) body.acceptance = acceptance;
  const budgets: Budgets = {
    tokens: parseAmount("The token budget", form.tokens, true),
    wall_seconds: parseAmount("The wall-time budget", form.wallSeconds, false),
    gpu_seconds: parseAmount("The GPU-seconds budget", form.gpuSeconds, false),
    escalations: parseAmount("The escalation budget", form.escalations, true),
  };
  if (Object.values(budgets).some((value) => value !== null)) body.budgets = budgets;
  if (form.worker.trim()) body.worker = form.worker.trim();
  const maxSteps = parseAmount("The step limit", form.maxSteps, true);
  if (maxSteps !== null) body.max_steps = maxSteps;
  if (!form.review) body.review = false;
  if (form.offline) body.offline = true;
  return body;
}

function NewTaskForm({ onCreated, onClose }: { onCreated: (task: TaskCreated) => void; onClose: () => void }) {
  const workers = useResource((signal) => api.workers(signal), []);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set =
    <K extends keyof FormState>(key: K) =>
    (value: FormState[K]) =>
      setForm((previous) => ({ ...previous, [key]: value }));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    let body: NewTask;
    try {
      body = buildTask(form);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
      return;
    }
    setSubmitting(true);
    try {
      const answer = await api.createTask(body);
      setForm(EMPTY_FORM);
      onCreated(answer);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form id="new-task" className="panel form" onSubmit={submit} aria-labelledby="new-task-title" noValidate>
      <h2 id="new-task-title">New task</h2>
      <div className="field">
        <label htmlFor="nt-instruction">
          Instruction <span className="muted">(required)</span>
        </label>
        <textarea
          id="nt-instruction"
          rows={3}
          required
          autoFocus
          value={form.instruction}
          onChange={(event) => set("instruction")(event.target.value)}
          placeholder="Fix the failing date test"
        />
      </div>
      <div className="field-row">
        <div className="field grow">
          <label htmlFor="nt-repo">Repository</label>
          <input
            id="nt-repo"
            type="text"
            value={form.repo}
            onChange={(event) => set("repo")(event.target.value)}
            aria-describedby="nt-repo-hint"
            spellCheck={false}
          />
          <p id="nt-repo-hint" className="hint">
            A path on this machine. Empty means the project <code>ssw dash</code> runs in.
          </p>
        </div>
        <div className="field">
          <label htmlFor="nt-track">Track</label>
          <select id="nt-track" value={form.track} onChange={(event) => set("track")(event.target.value)}>
            <option value="general">general</option>
            <option value="science">science</option>
          </select>
        </div>
      </div>
      <div className="field">
        <label htmlFor="nt-acceptance">Acceptance commands</label>
        <textarea
          id="nt-acceptance"
          rows={2}
          value={form.acceptance}
          onChange={(event) => set("acceptance")(event.target.value)}
          aria-describedby="nt-acceptance-hint"
          placeholder="python -m pytest -q"
          spellCheck={false}
        />
        <p id="nt-acceptance-hint" className="hint">
          One command per line; each must exit with 0 for the task to pass.
        </p>
      </div>
      <fieldset className="field-group">
        <legend>Budgets</legend>
        <p className="hint">Leave a field empty for no limit.</p>
        <div className="field-row">
          <NumberField id="nt-budget-tokens" label="Tokens" value={form.tokens} onChange={set("tokens")} whole />
          <NumberField
            id="nt-budget-wall"
            label="Wall time (seconds)"
            value={form.wallSeconds}
            onChange={set("wallSeconds")}
          />
          <NumberField id="nt-budget-gpu" label="GPU-seconds" value={form.gpuSeconds} onChange={set("gpuSeconds")} />
          <NumberField
            id="nt-budget-escalations"
            label="Escalations"
            value={form.escalations}
            onChange={set("escalations")}
            whole
          />
        </div>
      </fieldset>
      <div className="field-row">
        <div className="field grow">
          <label htmlFor="nt-worker">Pinned worker</label>
          <input
            id="nt-worker"
            type="text"
            list="nt-workers"
            value={form.worker}
            onChange={(event) => set("worker")(event.target.value)}
            aria-describedby="nt-worker-hint"
            spellCheck={false}
          />
          <datalist id="nt-workers">
            {(workers.data?.workers ?? []).map((worker) => (
              <option key={worker.id} value={worker.id}>
                {`${worker.model} · ${worker.tier}${worker.local ? "" : " · remote"}`}
              </option>
            ))}
          </datalist>
          <p id="nt-worker-hint" className="hint">
            Empty lets the router choose a worker for every step.
            {workers.error ? " (The worker list could not be loaded; type an id.)" : ""}
          </p>
        </div>
        <NumberField id="nt-max-steps" label="Step limit" value={form.maxSteps} onChange={set("maxSteps")} whole />
      </div>
      <div className="checks">
        <label>
          <input
            type="checkbox"
            checked={form.allowRemote}
            onChange={(event) => set("allowRemote")(event.target.checked)}
          />{" "}
          Allow remote endpoints
        </label>
        <label>
          <input type="checkbox" checked={form.review} onChange={(event) => set("review")(event.target.checked)} />{" "}
          Review step after the gates pass
        </label>
        <label>
          <input type="checkbox" checked={form.offline} onChange={(event) => set("offline")(event.target.checked)} />{" "}
          Offline: local endpoints only
        </label>
      </div>
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="form-actions">
        <button type="submit" className="primary" disabled={submitting}>
          {submitting ? "Starting…" : "Start task"}
        </button>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </form>
  );
}

function NumberField({
  id,
  label,
  value,
  onChange,
  whole = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  whole?: boolean;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="number"
        min={0}
        step={whole ? 1 : "any"}
        inputMode={whole ? "numeric" : "decimal"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}
