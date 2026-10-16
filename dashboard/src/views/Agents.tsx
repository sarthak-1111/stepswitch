import { api, type Agent } from "../api";
import { Empty, PageHeader, ResourceView, StatusBadge, TableWrap, TimeText } from "../components";
import { useLiveEvent, useStreamReopened } from "../events";
import { formatInt, formatNumber, formatPercent } from "../format";
import { useDebounced, useResource } from "../hooks";
import { Link } from "../router";
import { taskPath } from "../routes";

export function AgentsView() {
  const agents = useResource((signal) => api.agents(signal), []);
  const refresh = useDebounced(() => agents.reload({ silent: true }), 500);
  useLiveEvent("ledger", refresh);
  useLiveEvent("task", refresh);
  useStreamReopened(refresh);

  return (
    <>
      <PageHeader
        title="Agents"
        lead="The latest model call of every task that has not ended, refreshed after each model call."
      />
      <ResourceView
        resource={agents}
        what="the agents"
        isEmpty={(data) => data.agents.length === 0}
        empty={
          <Empty title="No agents at work">
            <p>Rows appear here while tasks run.</p>
          </Empty>
        }
      >
        {(data) => <AgentsTable agents={data.agents} />}
      </ResourceView>
    </>
  );
}

function ContextUse({ used, size }: { used: number | null; size: number | null }) {
  if (used === null || used === undefined) return <span className="muted">-</span>;
  if (!size) return <span>{formatInt(used)}</span>;
  const share = Math.max(0, Math.min(1, used / size));
  return (
    <div className="context-use">
      <div className={`bar${share > 0.85 ? " bar-high" : ""}`} aria-hidden="true">
        <span style={{ width: `${(share * 100).toFixed(1)}%` }} />
      </div>
      <span className="small">
        {formatInt(used)} of {formatInt(size)} ({formatPercent(share)})
      </span>
    </div>
  );
}

function AgentsTable({ agents }: { agents: Agent[] }) {
  return (
    <TableWrap label="Agents">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Task</th>
            <th scope="col">Status</th>
            <th scope="col">Worker</th>
            <th scope="col" className="num">
              Step
            </th>
            <th scope="col">Step type</th>
            <th scope="col" className="num">
              Tokens/s
            </th>
            <th scope="col">Context used</th>
            <th scope="col" className="num">
              Cache hit
            </th>
            <th scope="col">Since</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((agent) => (
            <tr key={agent.task_id}>
              <th scope="row">
                <Link to={taskPath(agent.task_id)}>
                  <code>{agent.task_id}</code>
                </Link>
              </th>
              <td>
                <StatusBadge status={agent.status} />
              </td>
              <td>{agent.worker ? <code>{agent.worker}</code> : <span className="muted">-</span>}</td>
              <td className="num">{agent.step ?? "-"}</td>
              <td>{agent.step_type ?? "-"}</td>
              <td className="num">{formatNumber(agent.tokens_per_s, 1)}</td>
              <td>
                <ContextUse used={agent.context_used} size={agent.context} />
              </td>
              <td className="num">{formatPercent(agent.cache_hit)}</td>
              <td>
                <TimeText epoch={agent.since} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
