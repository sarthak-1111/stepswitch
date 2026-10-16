import { useState } from "react";
import { api, type Candidate, type Decision, type Estimate } from "../api";
import { Badge, Empty, PageHeader, ResourceView, Section, TableWrap } from "../components";
import { useLiveEvent, useStreamReopened } from "../events";
import { formatClock, formatDuration, formatInt, formatNumber, formatTime, formatValue, truncate } from "../format";
import { useDebounced, useResource } from "../hooks";
import { Link, navigate, useLocation, withQuery } from "../router";
import { taskPath } from "../routes";

function sameDecision(a: Decision, b: Decision): boolean {
  return a.task_id === b.task_id && a.step === b.step && a.ts === b.ts && a.chosen === b.chosen;
}

function idPart(text: string): string {
  return text.replace(/[^A-Za-z0-9_-]/g, "-");
}

export function estimateText(estimate: Estimate | null | undefined): string {
  if (!estimate) return "-";
  const parts: string[] = [];
  const tokens =
    estimate.tokens ??
    (estimate.input_tokens !== undefined || estimate.output_tokens !== undefined
      ? (estimate.input_tokens ?? 0) + (estimate.output_tokens ?? 0)
      : undefined);
  if (tokens !== undefined) parts.push(`${formatInt(tokens)} tokens`);
  if (estimate.seconds !== undefined) parts.push(formatDuration(estimate.seconds));
  return parts.length ? parts.join(" · ") : "-";
}

function switchCostText(decision: Decision): string {
  const cost = decision.switch_cost;
  if (!cost) return "";
  return `${formatDuration(cost.seconds)}, ${formatInt(cost.tokens)} tokens`;
}

export function RouterView() {
  const { query } = useLocation();
  const task = query.get("task") ?? "";
  const switchesOnly = query.get("switches") === "1";
  const decisions = useResource((signal) => api.router({ task: task || undefined, limit: 200 }, signal), [task]);
  const tasks = useResource((signal) => api.tasks({ limit: 100 }, signal), []);
  const refresh = useDebounced(() => decisions.reload({ silent: true }), 1000);

  useLiveEvent("decision", (decision) => {
    if (task && decision.task_id !== task) return;
    decisions.update((data) =>
      data.decisions.some((known) => sameDecision(known, decision))
        ? data
        : { ...data, decisions: [decision, ...data.decisions] },
    );
    // A task's first decision comes with new classifier features: fetch them.
    if (decisions.data && !(decision.task_id in decisions.data.features)) refresh();
  });
  useStreamReopened(refresh);

  const setFilter = (next: { task?: string; switches?: boolean }) => {
    const nextTask = next.task ?? task;
    const nextSwitches = next.switches ?? switchesOnly;
    navigate(withQuery("/router", { task: nextTask || undefined, switches: nextSwitches ? "1" : undefined }), {
      replace: true,
    });
  };

  const options = new Map<string, string>();
  for (const item of tasks.data?.tasks ?? []) options.set(item.id, item.instruction);
  for (const decision of decisions.data?.decisions ?? []) if (!options.has(decision.task_id)) options.set(decision.task_id, "");
  for (const id of Object.keys(decisions.data?.features ?? {})) if (!options.has(id)) options.set(id, "");
  if (task && !options.has(task)) options.set(task, "");

  return (
    <>
      <PageHeader
        title="Router"
        lead="Routing decisions, newest first: the candidates the router scored, the worker it chose and what each switch cost."
      />
      <div className="toolbar" role="group" aria-label="Filters">
        <div className="field">
          <label htmlFor="router-task">Task</label>
          <select id="router-task" value={task} onChange={(event) => setFilter({ task: event.target.value })}>
            <option value="">All tasks</option>
            {Array.from(options.entries()).map(([id, instruction]) => (
              <option key={id} value={id}>
                {instruction ? `${id} · ${truncate(instruction, 60)}` : id}
              </option>
            ))}
          </select>
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={switchesOnly}
            onChange={(event) => setFilter({ switches: event.target.checked })}
          />{" "}
          Switches only
        </label>
      </div>
      <ResourceView
        resource={decisions}
        what="the routing decisions"
        isEmpty={(data) => data.decisions.length === 0 && Object.keys(data.features).length === 0}
        empty={
          <Empty title="No routing decisions yet">
            <p>The router records a decision before every model call of a task.</p>
          </Empty>
        }
      >
        {(data) => {
          const shown = switchesOnly ? data.decisions.filter((decision) => decision.switched) : data.decisions;
          return (
            <>
              <Section title={`Decisions (${shown.length})`} id="decisions">
                {shown.length ? (
                  <DecisionsTable decisions={shown} showTask={!task} />
                ) : (
                  <p className="muted">{switchesOnly ? "No switches in these decisions." : "No decisions."}</p>
                )}
              </Section>
              <Section title="Classifier features" id="features">
                <FeaturesTable features={data.features} />
              </Section>
            </>
          );
        }}
      </ResourceView>
    </>
  );
}

export function DecisionsTable({ decisions, showTask }: { decisions: Decision[]; showTask: boolean }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const toggle = (key: string) =>
    setOpen((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const columns = showTask ? 10 : 9;
  return (
    <TableWrap label="Routing decisions">
      <table className="data-table decisions">
        <thead>
          <tr>
            <th scope="col">Candidates</th>
            {showTask ? <th scope="col">Task</th> : null}
            <th scope="col" className="num">
              Step
            </th>
            <th scope="col">Time</th>
            <th scope="col">Step type</th>
            <th scope="col">Chosen</th>
            <th scope="col">Switch</th>
            <th scope="col">Reason</th>
            <th scope="col" className="num">
              Level
            </th>
            <th scope="col">Estimate</th>
          </tr>
        </thead>
        {decisions.map((decision, index) => {
          const key = idPart(`${decision.task_id}-${decision.step}-${decision.ts}-${index}`);
          const expanded = open.has(key);
          const rowId = `candidates-${key}`;
          const candidates = decision.candidates ?? [];
          return (
            <tbody key={key} className={decision.switched ? "is-switch" : undefined}>
              <tr>
                <td>
                  <button
                    type="button"
                    className="expander"
                    aria-expanded={expanded}
                    aria-controls={rowId}
                    onClick={() => toggle(key)}
                  >
                    {expanded ? "Hide" : "Show"} {candidates.length}
                    <span className="visually-hidden"> candidates</span>
                  </button>
                </td>
                {showTask ? (
                  <td>
                    <Link to={taskPath(decision.task_id)}>
                      <code>{decision.task_id}</code>
                    </Link>
                  </td>
                ) : null}
                <td className="num">{decision.step}</td>
                <td>
                  <time title={formatTime(decision.ts)}>{formatClock(decision.ts)}</time>
                </td>
                <td>{decision.step_type}</td>
                <td>
                  <code>{decision.chosen ?? "none"}</code>
                  {decision.rule ? <div className="muted small">rule: {decision.rule}</div> : null}
                </td>
                <td>
                  {decision.switched ? (
                    <>
                      <Badge tone="purple">switch</Badge>
                      {decision.previous ? (
                        <div className="small">
                          from <code>{decision.previous}</code>
                        </div>
                      ) : null}
                      {decision.switch_cost ? <div className="small">cost {switchCostText(decision)}</div> : null}
                      {decision.expected_gain !== null && decision.expected_gain !== undefined ? (
                        <div className="small">expected gain {formatNumber(decision.expected_gain, 2)}</div>
                      ) : null}
                    </>
                  ) : (
                    <span className="muted">stay</span>
                  )}
                </td>
                <td className="wrap">
                  {decision.reason}
                  {decision.notes?.length ? <div className="muted small">{decision.notes.join("; ")}</div> : null}
                </td>
                <td className="num">{decision.level}</td>
                <td>{estimateText(decision.estimate)}</td>
              </tr>
              <tr id={rowId} className="detail-row" hidden={!expanded}>
                <td colSpan={columns}>
                  {expanded ? <CandidatesTable candidates={candidates} chosen={decision.chosen} /> : null}
                </td>
              </tr>
            </tbody>
          );
        })}
      </table>
    </TableWrap>
  );
}

function CandidatesTable({ candidates, chosen }: { candidates: Candidate[]; chosen: string | null }) {
  if (!candidates.length) return <p className="muted">No candidates were recorded.</p>;
  return (
    <table className="data-table nested">
      <caption className="visually-hidden">Candidates</caption>
      <thead>
        <tr>
          <th scope="col">Worker</th>
          <th scope="col">Tier</th>
          <th scope="col" className="num">
            Level
          </th>
          <th scope="col" className="num">
            Score
          </th>
          <th scope="col">Estimate</th>
          <th scope="col">Excluded</th>
        </tr>
      </thead>
      <tbody>
        {candidates.map((candidate) => (
          <tr key={candidate.worker} className={candidate.worker === chosen ? "is-chosen" : undefined}>
            <td>
              <code>{candidate.worker}</code> {candidate.worker === chosen ? <Badge tone="ok">chosen</Badge> : null}
            </td>
            <td>{candidate.tier}</td>
            <td className="num">{candidate.level}</td>
            <td className="num">{formatNumber(candidate.score, 3)}</td>
            <td>
              {estimateText(candidate.estimate)}
              {candidate.estimate?.input_tokens !== undefined ? (
                <div className="muted small">
                  {formatInt(candidate.estimate.input_tokens)} in · {formatInt(candidate.estimate.output_tokens)} out
                </div>
              ) : null}
            </td>
            <td className="wrap">
              {candidate.excluded ? candidate.excluded : <span className="muted">eligible</span>}
              {candidate.relaxed ? <div className="muted small">rung relaxed: nothing else was eligible</div> : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const FEATURE_ORDER = [
  "task_type",
  "difficulty",
  "domain",
  "risk",
  "est_context_tokens",
  "needs_new_code",
  "frontend",
  "confidence",
  "language",
  "track",
  "offline",
  "source",
];

function FeaturesTable({ features }: { features: Record<string, Record<string, unknown>> }) {
  const ids = Object.keys(features);
  if (!ids.length) return <p className="muted">No classifier features for these tasks.</p>;
  const keys = new Set<string>();
  for (const id of ids) for (const key of Object.keys(features[id] ?? {})) keys.add(key);
  const ordered = [
    ...FEATURE_ORDER.filter((key) => keys.has(key)),
    ...Array.from(keys)
      .filter((key) => !FEATURE_ORDER.includes(key))
      .sort(),
  ];
  return (
    <TableWrap label="Classifier features">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Task</th>
            {ordered.map((key) => (
              <th scope="col" key={key}>
                {key.replace(/_/g, " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ids.map((id) => (
            <tr key={id}>
              <th scope="row">
                <Link to={taskPath(id)}>
                  <code>{id}</code>
                </Link>
              </th>
              {ordered.map((key) => (
                <td key={key}>{formatValue(features[id]?.[key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
