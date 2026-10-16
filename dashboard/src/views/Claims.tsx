import { api, type ClaimRow, type ClaimsAnswer } from "../api";
import { Badge, Empty, PageHeader, ResourceView, Section, StatusBadge, TableWrap, toneOf } from "../components";
import { formatDateText, formatDuration, formatInt, formatValue } from "../format";
import { useResource } from "../hooks";
import { navigate, useLocation, withQuery } from "../router";

const STATUS_ORDER = ["passing", "failing", "pending", "skipped"];

export function ClaimsView() {
  const claims = useResource((signal) => api.claims(signal), []);
  return (
    <>
      <PageHeader
        title="Claims"
        lead={
          <>
            The latest <code>ssw prove</code> report: every public claim with the tests that back it.
          </>
        }
      />
      <ResourceView
        resource={claims}
        what="the claims"
        isEmpty={(data) => !data.report && data.claims.length === 0}
        empty={
          <Empty title="No prove report yet">
            <p>
              Run <code>ssw prove --quick</code> to write one.
            </p>
          </Empty>
        }
      >
        {(data) => <ClaimsBody data={data} />}
      </ResourceView>
    </>
  );
}

function ClaimsBody({ data }: { data: ClaimsAnswer }) {
  const { query } = useLocation();
  const filter = query.get("status") ?? "";
  const report = data.report;
  const counts: Record<string, number> = { ...(report?.summary ?? {}) };
  if (!report?.summary) for (const claim of data.claims) counts[claim.status] = (counts[claim.status] ?? 0) + 1;
  const statuses = [
    ...STATUS_ORDER.filter((status) => status in counts),
    ...Object.keys(counts).filter((status) => !STATUS_ORDER.includes(status)),
  ];
  const shown = filter ? data.claims.filter((claim) => claim.status === filter) : data.claims;
  const platform = report?.platform ?? {};
  const platformText = ["os", "machine", "release", "python"]
    .map((key) => platform[key])
    .filter((value) => value !== null && value !== undefined && value !== "")
    .map((value) => formatValue(value))
    .join(" · ");

  return (
    <>
      <dl className="kv meta-grid">
        <div>
          <dt>Mode</dt>
          <dd>{report?.mode ?? "-"}</dd>
        </div>
        <div>
          <dt>Created</dt>
          <dd>{formatDateText(report?.created)}</dd>
        </div>
        {report?.duration_s !== undefined ? (
          <div>
            <dt>Duration</dt>
            <dd>{formatDuration(report.duration_s)}</dd>
          </div>
        ) : null}
        <div>
          <dt>Platform</dt>
          <dd>
            {platformText || "-"}
            {platform.ci === true ? " (CI)" : ""}
          </dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>{data.source ? <code>{data.source}</code> : "-"}</dd>
        </div>
      </dl>
      <ul className="summary-counts" aria-label="Claims by status">
        {statuses.map((status) => (
          <li key={status}>
            <Badge tone={toneOf(status)}>
              {status}: {formatInt(counts[status])}
            </Badge>
          </li>
        ))}
      </ul>
      <Section
        title={`Claims (${shown.length})`}
        id="claims"
        actions={
          <div className="field inline">
            <label htmlFor="claims-status">Status</label>
            <select
              id="claims-status"
              value={filter}
              onChange={(event) =>
                navigate(withQuery("/claims", { status: event.target.value || undefined }), { replace: true })
              }
            >
              <option value="">All</option>
              {Array.from(new Set([...STATUS_ORDER, ...data.claims.map((claim) => claim.status)])).map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </div>
        }
      >
        {shown.length ? <ClaimsTable claims={shown} /> : <p className="muted">No claims with this status.</p>}
      </Section>
    </>
  );
}

function ClaimsTable({ claims }: { claims: ClaimRow[] }) {
  return (
    <TableWrap label="Claims">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Claim</th>
            <th scope="col">Text</th>
            <th scope="col" className="num">
              Phase
            </th>
            <th scope="col">Status</th>
            <th scope="col">Tests</th>
          </tr>
        </thead>
        <tbody>
          {claims.map((claim) => {
            const tests = Object.entries(claim.tests ?? {});
            const notes = Object.entries(claim.notes ?? {});
            return (
              <tr key={claim.id}>
                <th scope="row">{claim.id}</th>
                <td className="wrap claim-text">
                  {claim.text ?? <span className="muted">(not in claims.yaml)</span>}
                  {claim.parts?.length ? <div className="muted small">parts: {claim.parts.join(", ")}</div> : null}
                </td>
                <td className="num">{claim.phase ?? "-"}</td>
                <td>
                  <StatusBadge status={claim.status} />
                </td>
                <td className="wrap">
                  {tests.length ? (
                    <details className="disclosure">
                      <summary>
                        {tests.length} {tests.length === 1 ? "test" : "tests"}
                      </summary>
                      <ul className="test-list">
                        {tests.map(([node, outcome]) => (
                          <li key={node}>
                            <StatusBadge status={outcome} /> <code>{node}</code>
                            {claim.notes?.[node] ? <div className="muted small">{claim.notes[node]}</div> : null}
                          </li>
                        ))}
                      </ul>
                      {notes
                        .filter(([key]) => !(key in (claim.tests ?? {})))
                        .map(([key, note]) => (
                          <p key={key} className="muted small">
                            {key}: {note}
                          </p>
                        ))}
                    </details>
                  ) : (
                    <span className="muted">-</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}
