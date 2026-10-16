import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, FINAL_STATUSES, type TrajectoryEvent, type TrajectorySummary } from "../api";
import {
  Badge,
  Empty,
  ErrorState,
  Loading,
  PageHeader,
  ResourceView,
  StatusBadge,
  TableWrap,
  TimeText,
  type Tone,
} from "../components";
import { useLiveEvent, useStreamReopened } from "../events";
import {
  asArray,
  asNumber,
  asRecord,
  asString,
  asStrings,
  formatClock,
  formatDuration,
  formatInt,
  formatNumber,
  formatTime,
  formatValue,
  isoTime,
  stopReasonText,
  truncate,
} from "../format";
import { useDebounced, useResource } from "../hooks";
import { Link } from "../router";
import { taskPath, trajectoryPath } from "../routes";

// ------------------------------------------------------------------------------------------
// List
// ------------------------------------------------------------------------------------------

export function TrajectoriesView() {
  const list = useResource((signal) => api.trajectories(100, signal), []);
  const refresh = useDebounced(() => list.reload({ silent: true }), 1000);

  useLiveEvent("trajectory", (notice) => {
    const known = list.data?.trajectories.some((item) => item.id === notice.task_id);
    if (!known) {
      refresh();
      return;
    }
    list.update((data) => ({
      ...data,
      trajectories: data.trajectories.map((item) =>
        item.id === notice.task_id ? { ...item, events: Math.max(item.events, notice.seq) } : item,
      ),
    }));
  });
  useLiveEvent("task", (task) =>
    list.update((data) => ({
      ...data,
      trajectories: data.trajectories.map((item) => (item.id === task.id ? { ...item, status: task.status } : item)),
    })),
  );
  useStreamReopened(refresh);

  return (
    <>
      <PageHeader title="Trajectories" lead="The recorded events of each task: model calls, tool calls, routing and gates." />
      <ResourceView
        resource={list}
        what="the trajectories"
        isEmpty={(data) => data.trajectories.length === 0}
        empty={
          <Empty title="No trajectories yet">
            <p>Every task records one as it runs.</p>
          </Empty>
        }
      >
        {(data) => <TrajectoryTable items={data.trajectories} />}
      </ResourceView>
    </>
  );
}

function TrajectoryTable({ items }: { items: TrajectorySummary[] }) {
  return (
    <TableWrap label="Trajectories">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Task</th>
            <th scope="col">Status</th>
            <th scope="col">Instruction</th>
            <th scope="col">Created</th>
            <th scope="col" className="num">
              Events
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <th scope="row">
                <Link to={trajectoryPath(item.id)}>
                  <code>{item.id}</code>
                </Link>
              </th>
              <td>
                <StatusBadge status={item.status} />
              </td>
              <td className="wrap">{truncate(item.instruction || "-", 140)}</td>
              <td>
                <TimeText epoch={item.created} />
              </td>
              <td className="num">{formatInt(item.events)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

// ------------------------------------------------------------------------------------------
// Viewer
// ------------------------------------------------------------------------------------------

function mergeEvents(known: TrajectoryEvent[], incoming: TrajectoryEvent[]): TrajectoryEvent[] {
  if (!incoming.length) return known;
  const bySeq = new Map<number, TrajectoryEvent>();
  for (const event of known) bySeq.set(event.seq, event);
  for (const event of incoming) bySeq.set(event.seq, event);
  return Array.from(bySeq.values()).sort((a, b) => a.seq - b.seq);
}

/** The events of one task: loaded once, then extended with ?after=<last seq> on live notices. */
function useTrajectory(id: string) {
  const [events, setEvents] = useState<TrajectoryEvent[]>([]);
  const [error, setError] = useState<Error | undefined>(undefined);
  const [loaded, setLoaded] = useState(false);
  const lastSeq = useRef(0);
  const running = useRef(false);
  const again = useRef(false);
  const generation = useRef(0);

  const fetchMore = useCallback(async () => {
    if (running.current) {
      again.current = true;
      return;
    }
    running.current = true;
    const own = generation.current;
    try {
      do {
        again.current = false;
        const answer = await api.taskEvents(id, lastSeq.current || undefined);
        if (own !== generation.current) return;
        const incoming = answer.events ?? [];
        for (const event of incoming) lastSeq.current = Math.max(lastSeq.current, event.seq);
        setEvents((previous) => mergeEvents(previous, incoming));
        setError(undefined);
      } while (again.current);
    } catch (failure) {
      if (own === generation.current) setError(failure instanceof Error ? failure : new Error(String(failure)));
    } finally {
      if (own === generation.current) {
        running.current = false;
        setLoaded(true);
      }
    }
  }, [id]);

  useEffect(() => {
    generation.current += 1;
    lastSeq.current = 0;
    running.current = false;
    again.current = false;
    setEvents([]);
    setError(undefined);
    setLoaded(false);
    void fetchMore();
  }, [fetchMore]);

  useLiveEvent("trajectory", (notice) => {
    if (notice.task_id === id && notice.seq > lastSeq.current) void fetchMore();
  });
  useStreamReopened(() => void fetchMore());

  return { events, error, loaded, retry: fetchMore };
}

export function TrajectoryView({ id }: { id: string }) {
  const task = useResource((signal) => api.task(id, signal), [id]);
  const trajectory = useTrajectory(id);
  const [type, setType] = useState("");

  useLiveEvent("task", (summary) => {
    if (summary.id === id) task.update((data) => ({ ...data, ...summary }));
  });

  const types = Array.from(new Set(trajectory.events.map((event) => event.type))).sort();
  const shown = type ? trajectory.events.filter((event) => event.type === type) : trajectory.events;
  const start = trajectory.events[0]?.ts;
  const live = task.data ? !FINAL_STATUSES.has(task.data.status) : false;

  return (
    <>
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/trajectories">Trajectories</Link>
        <span aria-hidden="true"> / </span>
        <span aria-current="page">
          <code>{id}</code>
        </span>
      </nav>
      <PageHeader
        title="Trajectory"
        lead={task.data?.instruction}
        actions={
          <div className="fork-replay">
            <button type="button" disabled aria-describedby="fork-replay-note">
              Fork
            </button>
            <button type="button" disabled aria-describedby="fork-replay-note">
              Replay
            </button>
            <p id="fork-replay-note" className="muted small">
              Fork and replay arrive in Phase 7
            </p>
          </div>
        }
      />
      <div className="detail-head">
        <div className="chips">
          {task.data ? <StatusBadge status={task.data.status} /> : null}
          <Link to={taskPath(id)}>Task details</Link>
          {live ? <Badge tone="info">following live</Badge> : null}
        </div>
        {trajectory.events.length ? (
          <div className="field inline">
            <label htmlFor="event-type">Event type</label>
            <select id="event-type" value={type} onChange={(event) => setType(event.target.value)}>
              <option value="">All ({formatInt(trajectory.events.length)})</option>
              {types.map((name) => (
                <option key={name} value={name}>
                  {name} ({formatInt(trajectory.events.filter((event) => event.type === name).length)})
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </div>
      {!trajectory.loaded ? (
        <Loading />
      ) : trajectory.error && !trajectory.events.length ? (
        <ErrorState error={trajectory.error} what="this trajectory" onRetry={() => void trajectory.retry()} />
      ) : !trajectory.events.length ? (
        <Empty title="No events recorded">
          <p>Events appear here as the task runs.</p>
        </Empty>
      ) : (
        <>
          {trajectory.error ? (
            <p className="banner banner-error" role="alert">
              Could not fetch new events: {trajectory.error.message}
            </p>
          ) : null}
          <ol className="timeline">
            {shown.map((event) => (
              <TimelineItem key={event.seq} event={event} start={start} />
            ))}
          </ol>
        </>
      )}
    </>
  );
}

const TYPE_TONES: Record<string, Tone> = {
  step: "info",
  tool: "neutral",
  decision: "purple",
  switch: "purple",
  escalation: "orange",
  gates: "warn",
  review: "warn",
  model_error: "bad",
  error: "bad",
  budget: "orange",
  cancelled: "orange",
  task_end: "ok",
  task_start: "ok",
};

function TimelineItem({ event, start }: { event: TrajectoryEvent; start: number | undefined }) {
  const offset = start !== undefined ? event.ts - start : undefined;
  return (
    <li className={`timeline-item type-${event.type.replace(/[^a-z_]/g, "")}`}>
      <div className="timeline-meta">
        <span className="seq">#{event.seq}</span>
        <Badge tone={TYPE_TONES[event.type] ?? "neutral"}>{event.type}</Badge>
        {event.step !== null && event.step !== undefined ? <span className="small">step {event.step}</span> : null}
        <time className="small muted" dateTime={isoTime(event.ts)} title={formatTime(event.ts)}>
          {formatClock(event.ts)}
          {offset !== undefined ? ` (+${formatDuration(offset)})` : ""}
        </time>
      </div>
      <div className="timeline-body">
        <EventSummary event={event} />
        <details className="disclosure">
          <summary>Raw JSON</summary>
          <pre className="code-block">{JSON.stringify(event, null, 2)}</pre>
        </details>
      </div>
    </li>
  );
}

function Line({ children }: { children: ReactNode }) {
  return <p className="event-line">{children}</p>;
}

function usageText(usage: Record<string, unknown>): string {
  const input = asNumber(usage.input_tokens);
  const output = asNumber(usage.output_tokens);
  const cached = asNumber(usage.cached_input_tokens);
  const parts: string[] = [];
  if (input !== undefined) parts.push(`${formatInt(input)} in`);
  if (cached) parts.push(`${formatInt(cached)} cached`);
  if (output !== undefined) parts.push(`${formatInt(output)} out`);
  return parts.join(", ");
}

function EventSummary({ event }: { event: TrajectoryEvent }) {
  const data = asRecord(event.data);
  switch (event.type) {
    case "step": {
      const calls = asStrings(data.tool_calls);
      const usage = usageText(asRecord(data.usage));
      const text = asString(data.text);
      return (
        <>
          <Line>
            <strong>{asString(data.step_type) ?? "step"}</strong> on <code>{asString(data.worker) ?? "?"}</code>
            {usage ? ` · ${usage} tokens` : ""}
            {asNumber(data.wall_seconds) !== undefined ? ` · ${formatDuration(asNumber(data.wall_seconds))}` : ""}
            {asString(data.finish_reason) ? ` · finished: ${asString(data.finish_reason)}` : ""}
          </Line>
          {calls.length ? (
            <Line>
              Tool calls:{" "}
              {calls.map((name, index) => (
                <span key={`${name}-${index}`}>
                  {index ? ", " : ""}
                  <code>{name}</code>
                </span>
              ))}
            </Line>
          ) : null}
          {text ? <blockquote className="reply">{text}</blockquote> : null}
        </>
      );
    }
    case "tool": {
      const changed = asStrings(data.changed_files);
      const output = asString(data.output);
      const args = asString(data.arguments);
      return (
        <>
          <Line>
            <code>
              {asString(data.name) ?? "tool"}({args ? truncate(args, 160) : ""})
            </code>{" "}
            {data.is_error === true ? <Badge tone="bad">error</Badge> : <Badge tone="ok">ok</Badge>}
            {data.repeated === true ? (
              <>
                {" "}
                <Badge tone="warn">repeated</Badge>
              </>
            ) : null}
          </Line>
          {changed.length ? <Line>Changed: {changed.join(", ")}</Line> : null}
          {output ? (
            <details className="disclosure">
              <summary>Output ({formatInt(output.length)} characters)</summary>
              <pre className="code-block">{output}</pre>
            </details>
          ) : null}
        </>
      );
    }
    case "decision": {
      const candidates = asArray(data.candidates);
      return (
        <Line>
          Chose <code>{asString(data.chosen) ?? "no worker"}</code> for {asString(data.step_type) ?? "the step"} at level{" "}
          {formatValue(data.level)}
          {data.switched === true && asString(data.previous) ? (
            <>
              , switching from <code>{asString(data.previous)}</code>
            </>
          ) : null}
          {candidates.length ? ` (${candidates.length} candidates)` : ""}: {asString(data.reason) ?? ""}
        </Line>
      );
    }
    case "switch": {
      const cost = asRecord(data.cost);
      return (
        <Line>
          Switch <code>{asString(data.from) ?? "?"}</code> → <code>{asString(data.to) ?? "?"}</code>
          {asString(data.step_type) ? ` for ${asString(data.step_type)}` : ""}: {asString(data.reason) ?? ""}. Cost{" "}
          {formatDuration(asNumber(cost.seconds))}, {formatInt(asNumber(cost.tokens))} tokens; expected gain{" "}
          {formatNumber(asNumber(data.expected_gain), 2)}.
        </Line>
      );
    }
    case "gates": {
      const verdict = asRecord(data.verdict);
      const results = asArray(verdict.results).map(asRecord);
      return (
        <>
          <Line>
            Gate run {formatValue(data.run)}:{" "}
            {verdict.passed === true ? (
              <Badge tone="ok">passed</Badge>
            ) : (
              <Badge tone="bad">failed{asString(verdict.failed_gate) ? ` at ${asString(verdict.failed_gate)}` : ""}</Badge>
            )}
          </Line>
          {results.length ? (
            <ul className="gate-list">
              {results.map((result, index) => (
                <li key={`${asString(result.name)}-${index}`}>
                  <strong>{asString(result.name) ?? "gate"}</strong> <StatusBadge status={asString(result.status)} />
                  {asNumber(result.seconds) !== undefined ? ` (${formatDuration(asNumber(result.seconds))})` : ""}
                  {asString(result.summary) ? ` · ${asString(result.summary)}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      );
    }
    case "task_end": {
      const ledger = asRecord(data.ledger);
      return (
        <>
          <Line>
            Ended <StatusBadge status={asString(data.status)} />
            {asString(data.stop_reason) ? ` (${stopReasonText(asString(data.stop_reason) ?? "")})` : ""}
            {asString(data.detail) ? `: ${asString(data.detail)}` : ""}
          </Line>
          {asString(data.summary) ? <Line>{asString(data.summary)}</Line> : null}
          {Object.keys(ledger).length ? (
            <Line>
              Ledger:{" "}
              {Object.entries(ledger)
                .map(([key, value]) => `${key.replace(/_/g, " ")} ${formatValue(value)}`)
                .join(", ")}
            </Line>
          ) : null}
        </>
      );
    }
    case "task_start": {
      const spec = asRecord(data.spec);
      return (
        <Line>
          Started{asString(spec.track) ? ` on the ${asString(spec.track)} track` : ""}
          {asString(spec.instruction) ? `: ${truncate(asString(spec.instruction) ?? "", 200)}` : ""}
        </Line>
      );
    }
    case "status":
      return (
        <Line>
          Status <StatusBadge status={asString(data.status)} />
        </Line>
      );
    case "classify": {
      const features = asRecord(data.features);
      return (
        <Line>
          Classified as {formatValue(features.task_type)}, difficulty {formatValue(features.difficulty)}
          {asString(features.source) ? ` (${asString(features.source)})` : ""}
        </Line>
      );
    }
    case "ladder":
      return (
        <Line>
          Escalation ladder levels {formatValue(data.levels)}; starts on {formatValue(data.rung ?? data.start)}
          {asString(data.reason) ? `: ${asString(data.reason)}` : ""}
        </Line>
      );
    case "escalation":
      return (
        <Line>
          Escalated from {formatValue(data.from)} to {formatValue(data.to)}
          {asString(data.reason) ? `: ${asString(data.reason)}` : ""}
        </Line>
      );
    case "model_error":
      return (
        <Line>
          <code>{asString(data.worker) ?? "?"}</code> failed ({asString(data.kind) ?? "error"}): {asString(data.message) ?? ""}
        </Line>
      );
    case "error":
      return <Line>{asString(data.message) ?? "Error"}</Line>;
    case "budget":
      return (
        <Line>
          Budget reached ({asString(data.reason) ?? "?"}): tokens {formatInt(asNumber(data.tokens))}, wall time{" "}
          {formatDuration(asNumber(data.wall_seconds))}, GPU-seconds {formatNumber(asNumber(data.gpu_seconds), 1)}
        </Line>
      );
    case "review":
      return (
        <>
          <Line>
            Review: {data.approved === true ? <Badge tone="ok">approved</Badge> : <Badge tone="warn">issues</Badge>}
          </Line>
          {asString(data.text) ? <blockquote className="reply">{truncate(asString(data.text) ?? "", 600)}</blockquote> : null}
        </>
      );
    case "applied":
      return <Line>Applied to the working tree: {formatValue(data.files)}</Line>;
    case "worktree":
      return (
        <Line>
          Worktree <code>{asString(data.path) ?? "?"}</code>
          {asString(data.base) ? ` at ${truncate(asString(data.base) ?? "", 12)}` : ""}
        </Line>
      );
    case "pool": {
      const workers = asStrings(data.workers);
      return <Line>Worker pool: {workers.length ? workers.join(", ") : "-"}</Line>;
    }
    case "cancelled":
      return <Line>Cancelled.</Line>;
    default: {
      const entries = Object.entries(data).filter(([, value]) => value === null || typeof value !== "object");
      return entries.length ? (
        <Line>
          {entries
            .slice(0, 5)
            .map(([key, value]) => `${key.replace(/_/g, " ")}: ${truncate(formatValue(value), 80)}`)
            .join(" · ")}
        </Line>
      ) : null;
    }
  }
}
