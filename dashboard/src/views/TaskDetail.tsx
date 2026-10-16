import { useState } from "react";
import { api, FINAL_STATUSES, type GateResult, type LedgerRow, type TaskDetail, type TaskResult } from "../api";
import {
  Badge,
  PageHeader,
  ResourceView,
  Section,
  Stat,
  StatGrid,
  StatusBadge,
  TableWrap,
  TimeText,
} from "../components";
import { useLiveEvent, useStreamReopened } from "../events";
import {
  asRecord,
  formatClock,
  formatDuration,
  formatInt,
  formatNumber,
  formatTime,
  formatValue,
  stopReasonText,
} from "../format";
import { useDebounced, useResource } from "../hooks";
import { Link, withQuery } from "../router";
import { trajectoryPath } from "../routes";
import { DecisionsTable } from "./Router";
import { CancelButton } from "./Tasks";

export function TaskDetailView({ id }: { id: string }) {
  const task = useResource((signal) => api.task(id, signal), [id]);
  const refresh = useDebounced(() => task.reload({ silent: true }), 800);

  useLiveEvent("task", (summary) => {
    if (summary.id !== id) return;
    task.update((data) => (data.updated > summary.updated ? data : { ...data, ...summary }));
    refresh();
  });
  useLiveEvent("ledger", (row) => {
    if (row.task_id === id) refresh();
  });
  useLiveEvent("decision", (decision) => {
    if (decision.task_id === id) refresh();
  });
  useStreamReopened(refresh);

  return (
    <>
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/tasks">Tasks</Link>
        <span aria-hidden="true"> / </span>
        <span aria-current="page">
          <code>{id}</code>
        </span>
      </nav>
      <PageHeader title="Task" lead={task.data?.instruction} />
      <ResourceView resource={task} what="this task">
        {(data) => <TaskBody task={data} onChanged={(summary) => task.update((d) => ({ ...d, ...summary }))} />}
      </ResourceView>
    </>
  );
}

function TaskBody({ task, onChanged }: { task: TaskDetail; onChanged: (task: Partial<TaskDetail>) => void }) {
  const tokens = task.tokens ?? { input: 0, output: 0, cached_input: 0, reasoning: 0 };
  const ended = FINAL_STATUSES.has(task.status);
  // In step order, oldest first, whatever order the server sends.
  const decisions = [...(task.decisions ?? [])].sort((a, b) => a.step - b.step || a.ts - b.ts);
  const ledger = [...(task.ledger ?? [])].sort((a, b) => a.step - b.step || a.ts - b.ts);
  return (
    <>
      <div className="detail-head">
        <div className="chips">
          <StatusBadge status={task.status} />
          {task.stop_reason ? (
            <Badge title={`Stop reason: ${task.stop_reason}`}>stopped: {stopReasonText(task.stop_reason)}</Badge>
          ) : null}
          {task.escalations > 0 ? <Badge tone="orange">Escalated ×{task.escalations}</Badge> : null}
          {task.cancel_requested && !ended ? <Badge tone="warn">cancel requested</Badge> : null}
          <code className="muted small">{task.id}</code>
        </div>
        <CancelButton task={task} onChanged={onChanged} className="danger" />
      </div>
      <StatGrid label="Task figures">
        <Stat label="Track" value={task.track} />
        <Stat label="Worker" value={task.worker ?? "routed"} hint={task.worker ? "pinned" : "chosen per step"} />
        <Stat
          label="Steps"
          value={formatInt(task.steps)}
          hint={`${formatInt(task.switches)} switches, ${formatInt(task.escalations)} escalations`}
        />
        <Stat label="Input tokens" value={formatInt(tokens.input)} hint={`${formatInt(tokens.cached_input)} cached`} />
        <Stat
          label="Output tokens"
          value={formatInt(tokens.output)}
          hint={tokens.reasoning ? `${formatInt(tokens.reasoning)} reasoning` : undefined}
        />
        <Stat label="Wall time" value={formatDuration(task.wall_seconds)} hint={ended ? "elapsed" : "elapsed so far"} />
        <Stat label="GPU-seconds" value={formatNumber(task.gpu_seconds, 1)} />
        <Stat label="Created" value={<TimeText epoch={task.created} />} small />
        <Stat label="Updated" value={<TimeText epoch={task.updated} />} small />
      </StatGrid>
      <ul className="link-row">
        <li>
          <Link to={trajectoryPath(task.id)}>Trajectory ({formatInt(task.events)} events)</Link>
        </li>
        <li>
          <Link to={withQuery("/router", { task: task.id })}>Routing decisions</Link>
        </li>
        <li>
          <Link to={withQuery("/audit", { task: task.id })}>Egress audit</Link>
        </li>
        <li>
          <Link to={withQuery("/science", { task: task.id })}>Science reports</Link>
        </li>
      </ul>
      {task.summary && !task.result ? <p className="summary">{task.summary}</p> : null}
      <Section title="Result" id="result">
        {task.result ? (
          <ResultView result={task.result} />
        ) : (
          <p className="muted">The result appears here when the task ends.</p>
        )}
      </Section>
      <Section title="Spec" id="spec">
        <SpecList spec={task.spec} />
      </Section>
      <Section title={`Routing decisions (${decisions.length})`} id="decisions">
        {decisions.length ? (
          <DecisionsTable decisions={decisions} showTask={false} />
        ) : (
          <p className="muted">No routing decisions yet.</p>
        )}
      </Section>
      <Section title={`Ledger (${ledger.length} model calls)`} id="ledger">
        {ledger.length ? <LedgerRowsTable rows={ledger} /> : <p className="muted">No model calls yet.</p>}
      </Section>
    </>
  );
}

function ResultView({ result }: { result: TaskResult }) {
  const verdict = asRecord(result.verdict);
  const gates = (Array.isArray(verdict.results) ? verdict.results : []) as GateResult[];
  const files = Array.isArray(result.changed_files) ? result.changed_files : [];
  const passed = typeof verdict.passed === "boolean" ? verdict.passed : undefined;
  const failedGate = typeof verdict.failed_gate === "string" ? verdict.failed_gate : null;
  const report = typeof verdict.report === "string" ? verdict.report : "";
  return (
    <>
      {result.summary ? <p className="summary">{result.summary}</p> : null}
      {passed !== undefined ? (
        <p>
          Gates:{" "}
          {passed ? (
            <Badge tone="ok">passed</Badge>
          ) : (
            <Badge tone="bad">{failedGate ? `failed at ${failedGate}` : "failed"}</Badge>
          )}
        </p>
      ) : null}
      {gates.length ? <GatesTable gates={gates} /> : null}
      {report && passed === false ? (
        <details className="disclosure">
          <summary>Gate report</summary>
          <pre className="code-block">{report}</pre>
        </details>
      ) : null}
      <h3>Changed files ({files.length})</h3>
      {files.length ? (
        <ul className="file-list">
          {files.map((file) => (
            <li key={file}>
              <code>{file}</code>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No files changed.</p>
      )}
      <h3>Diff</h3>
      {result.diff ? <DiffView diff={result.diff} /> : <p className="muted">No diff.</p>}
    </>
  );
}

export function GatesTable({ gates }: { gates: GateResult[] }) {
  return (
    <TableWrap label="Gate results">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Gate</th>
            <th scope="col">Status</th>
            <th scope="col" className="num">
              Time
            </th>
            <th scope="col">Summary</th>
          </tr>
        </thead>
        <tbody>
          {gates.map((gate, index) => (
            <tr key={`${gate.name}-${index}`}>
              <th scope="row">{gate.name}</th>
              <td>
                <StatusBadge status={gate.status} />
              </td>
              <td className="num">{formatDuration(gate.seconds)}</td>
              <td className="wrap">{gate.summary || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

const DIFF_LINE_LIMIT = 4000;

function diffClass(line: string): string | undefined {
  if (line.startsWith("+++") || line.startsWith("---")) return "diff-file";
  if (line.startsWith("@@")) return "diff-hunk";
  if (line.startsWith("+")) return "diff-add";
  if (line.startsWith("-")) return "diff-del";
  if (line.startsWith("diff ") || line.startsWith("index ")) return "diff-meta";
  return undefined;
}

function DiffView({ diff }: { diff: string }) {
  const [all, setAll] = useState(false);
  const lines = diff.replace(/\n$/, "").split("\n");
  const shown = all ? lines : lines.slice(0, DIFF_LINE_LIMIT);
  return (
    <>
      <pre className="code-block diff" tabIndex={0} aria-label="Diff">
        {shown.map((line, index) => (
          <span key={index} className={diffClass(line)}>
            {line}
            {"\n"}
          </span>
        ))}
      </pre>
      {lines.length > shown.length ? (
        <button type="button" onClick={() => setAll(true)}>
          Show all {formatInt(lines.length)} lines
        </button>
      ) : null}
    </>
  );
}

function SpecValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">none</span>;
    return (
      <ul className="plain-list">
        {value.map((item, index) => (
          <li key={index}>
            <code>{formatValue(item)}</code>
          </li>
        ))}
      </ul>
    );
  }
  if (value !== null && typeof value === "object") {
    // Unset fields (null) are left out, so budgets show only the limits that were set.
    const entries = Object.entries(value as Record<string, unknown>).filter(
      ([, item]) => item !== null && item !== undefined,
    );
    if (!entries.length) return <span className="muted">none</span>;
    return (
      <span>
        {entries.map(([key, item], index) => (
          <span key={key}>
            {index ? " · " : ""}
            {key.replace(/_/g, " ")}: {formatValue(item)}
          </span>
        ))}
      </span>
    );
  }
  return <>{formatValue(value)}</>;
}

function SpecList({ spec }: { spec: Record<string, unknown> | null | undefined }) {
  const entries = Object.entries(spec ?? {});
  if (!entries.length) return <p className="muted">No spec recorded.</p>;
  return (
    <dl className="kv">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key.replace(/_/g, " ")}</dt>
          <dd>
            <SpecValue value={value} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function LedgerRowsTable({ rows }: { rows: LedgerRow[] }) {
  const total = rows.reduce(
    (sum, row) => ({
      input: sum.input + (row.input_tokens ?? 0),
      output: sum.output + (row.output_tokens ?? 0),
      cached: sum.cached + (row.cached_input_tokens ?? 0),
      reasoning: sum.reasoning + (row.reasoning_tokens ?? 0),
      wall: sum.wall + (row.wall_seconds ?? 0),
      gpu: sum.gpu + (row.gpu_seconds ?? 0),
    }),
    { input: 0, output: 0, cached: 0, reasoning: 0, wall: 0, gpu: 0 },
  );
  return (
    <TableWrap label="Ledger rows">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col" className="num">
              Step
            </th>
            <th scope="col">Time</th>
            <th scope="col">Step type</th>
            <th scope="col">Endpoint</th>
            <th scope="col">Model</th>
            <th scope="col" className="num">
              Input
            </th>
            <th scope="col" className="num">
              Output
            </th>
            <th scope="col" className="num">
              Cached input
            </th>
            <th scope="col" className="num">
              Reasoning
            </th>
            <th scope="col" className="num">
              Model time
            </th>
            <th scope="col" className="num">
              GPU-s
            </th>
            <th scope="col">Usage from</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.step}-${row.ts}-${index}`}>
              <td className="num">{row.step}</td>
              <td>
                <time title={formatTime(row.ts)}>{formatClock(row.ts)}</time>
              </td>
              <td>{row.step_type}</td>
              <td>{row.endpoint}</td>
              <td>
                <code>{row.model}</code>
              </td>
              <td className="num">{formatInt(row.input_tokens)}</td>
              <td className="num">{formatInt(row.output_tokens)}</td>
              <td className="num">{formatInt(row.cached_input_tokens)}</td>
              <td className="num">{formatInt(row.reasoning_tokens)}</td>
              <td className="num">{formatDuration(row.wall_seconds)}</td>
              <td className="num">{formatNumber(row.gpu_seconds, 1)}</td>
              <td>{row.usage_source ?? "-"}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row" colSpan={5}>
              Total
            </th>
            <td className="num">{formatInt(total.input)}</td>
            <td className="num">{formatInt(total.output)}</td>
            <td className="num">{formatInt(total.cached)}</td>
            <td className="num">{formatInt(total.reasoning)}</td>
            <td className="num">{formatDuration(total.wall)}</td>
            <td className="num">{formatNumber(total.gpu, 1)}</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </TableWrap>
  );
}
