import { useEffect, useState, type FormEvent } from "react";
import { api, type AuditAnswer, type AuditRow } from "../api";
import { Badge, Empty, PageHeader, ResourceView, Stat, StatGrid, TableWrap } from "../components";
import { formatBytes, formatClock, formatInt, formatTime } from "../format";
import { useResource } from "../hooks";
import { Link, navigate, useLocation, withQuery } from "../router";
import { taskPath } from "../routes";

const LIMITS = [50, 200, 500, 1000];

function destination(row: AuditRow): string {
  const port = row.port ? `:${row.port}` : "";
  return `${row.scheme}://${row.host}${port}`;
}

export function AuditView() {
  const { query } = useLocation();
  const task = query.get("task") ?? "";
  const denied = query.get("denied") === "true";
  const limitParam = Number(query.get("limit"));
  const limit = LIMITS.includes(limitParam) ? limitParam : 200;
  const [draft, setDraft] = useState(task);
  const [nonce, setNonce] = useState(0);
  useEffect(() => setDraft(task), [task]);

  const audit = useResource(
    (signal) => api.audit({ task: task || undefined, limit, denied: denied || undefined }, signal),
    [task, denied, limit, nonce],
  );

  const setFilter = (next: { task?: string; denied?: boolean; limit?: number }) => {
    const nextLimit = next.limit ?? limit;
    navigate(
      withQuery("/audit", {
        task: (next.task ?? task) || undefined,
        denied: (next.denied ?? denied) ? "true" : undefined,
        limit: nextLimit === 200 ? undefined : String(nextLimit),
      }),
      { replace: true },
    );
  };

  const apply = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFilter({ task: draft.trim() });
  };

  return (
    <>
      <PageHeader
        title="Audit"
        lead="Every connection the egress layer opened or refused, newest first."
        actions={
          <button type="button" onClick={() => setNonce((value) => value + 1)}>
            Refresh
          </button>
        }
      />
      <form className="toolbar" onSubmit={apply} role="search" aria-label="Filter the audit log">
        <div className="field">
          <label htmlFor="audit-task">Task id</label>
          <input
            id="audit-task"
            type="search"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="All tasks"
            spellCheck={false}
          />
        </div>
        <label className="check">
          <input type="checkbox" checked={denied} onChange={(event) => setFilter({ denied: event.target.checked })} />{" "}
          Denied only
        </label>
        <div className="field">
          <label htmlFor="audit-limit">Rows</label>
          <select id="audit-limit" value={limit} onChange={(event) => setFilter({ limit: Number(event.target.value) })}>
            {LIMITS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>
        <button type="submit">Apply</button>
        {task || denied ? (
          <button type="button" className="link-button" onClick={() => navigate("/audit", { replace: true })}>
            Clear filters
          </button>
        ) : null}
      </form>
      <ResourceView resource={audit} what="the audit log">
        {(data) => <AuditBody data={data} filtered={Boolean(task || denied)} />}
      </ResourceView>
    </>
  );
}

function AuditBody({ data, filtered }: { data: AuditAnswer; filtered: boolean }) {
  const totals = data.totals ?? { requests: 0, denied: 0, non_local: 0 };
  return (
    <>
      <StatGrid label="Totals">
        <Stat label="Requests" value={formatInt(totals.requests)} />
        <Stat label="Denied" value={formatInt(totals.denied)} />
        <Stat label="Non-local" value={formatInt(totals.non_local)} hint="to hosts outside this machine" />
      </StatGrid>
      {data.rows.length ? (
        <TableWrap label="Audit log">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Decision</th>
                <th scope="col">Method</th>
                <th scope="col">Destination</th>
                <th scope="col">Path</th>
                <th scope="col">Where</th>
                <th scope="col">Purpose</th>
                <th scope="col">Task</th>
                <th scope="col" className="num">
                  Status
                </th>
                <th scope="col" className="num">
                  Sent
                </th>
                <th scope="col" className="num">
                  Received
                </th>
                <th scope="col">Error</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.id} className={row.allowed ? undefined : "is-denied"}>
                  <td>
                    <time title={formatTime(row.ts)}>{formatClock(row.ts)}</time>
                  </td>
                  <td>{row.allowed ? <Badge tone="ok">allowed</Badge> : <Badge tone="bad">denied</Badge>}</td>
                  <td>{row.method}</td>
                  <td>
                    <code>{destination(row)}</code>
                  </td>
                  <td className="wrap">
                    <code>{row.path}</code>
                  </td>
                  <td>{row.local ? <Badge tone="info">local</Badge> : <Badge tone="orange">remote</Badge>}</td>
                  <td>{row.purpose ?? "-"}</td>
                  <td>
                    {row.task_id ? (
                      <Link to={taskPath(row.task_id)}>
                        <code>{row.task_id}</code>
                      </Link>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>
                  <td className="num">{row.status ?? "-"}</td>
                  <td className="num">{formatBytes(row.bytes_out)}</td>
                  <td className="num">{formatBytes(row.bytes_in)}</td>
                  <td className="wrap">{row.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      ) : (
        <Empty title={filtered ? "No rows match these filters" : "No connections recorded"}>
          <p>The egress layer records a row for every connection it opens or refuses.</p>
        </Empty>
      )}
    </>
  );
}
