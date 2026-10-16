import { api, type LedgerAnswer, type LedgerGroupBy } from "../api";
import { Empty, PageHeader, ResourceView, Section, Stat, StatGrid, TableWrap } from "../components";
import { useLiveEvent, useStreamReopened } from "../events";
import { formatDuration, formatInt, formatNumber, formatPercent } from "../format";
import { useDebounced, useResource } from "../hooks";
import { Link, navigate, useLocation, withQuery } from "../router";
import { taskPath } from "../routes";

const GROUPS: { value: LedgerGroupBy; label: string }[] = [
  { value: "model", label: "Model" },
  { value: "task", label: "Task" },
  { value: "day", label: "Day" },
];

const PERIODS: { value: string; label: string; seconds: number | null }[] = [
  { value: "", label: "All time", seconds: null },
  { value: "24h", label: "Last 24 hours", seconds: 86_400 },
  { value: "7d", label: "Last 7 days", seconds: 7 * 86_400 },
  { value: "30d", label: "Last 30 days", seconds: 30 * 86_400 },
];

export function LedgerView() {
  const { query } = useLocation();
  const groupParam = query.get("group");
  const group: LedgerGroupBy = groupParam === "task" || groupParam === "day" ? groupParam : "model";
  const period = PERIODS.find((item) => item.value === (query.get("period") ?? "")) ?? PERIODS[0];
  const ledger = useResource(
    (signal) =>
      api.ledger(
        {
          group,
          since: period.seconds === null ? undefined : Math.floor(Date.now() / 1000 - period.seconds),
        },
        signal,
      ),
    [group, period.value],
  );
  const refresh = useDebounced(() => ledger.reload({ silent: true }), 1500);
  useLiveEvent("ledger", refresh);
  useStreamReopened(refresh);

  const setFilter = (next: { group?: LedgerGroupBy; period?: string }) =>
    navigate(
      withQuery("/ledger", {
        group: (next.group ?? group) === "model" ? undefined : next.group ?? group,
        period: (next.period ?? period.value) || undefined,
      }),
      { replace: true },
    );

  return (
    <>
      <PageHeader title="Ledger" lead="Tokens, model time and GPU-seconds of every model call." />
      <div className="toolbar" role="group" aria-label="Grouping and period">
        <div className="field">
          <label htmlFor="ledger-group">Group by</label>
          <select
            id="ledger-group"
            value={group}
            onChange={(event) => setFilter({ group: event.target.value as LedgerGroupBy })}
          >
            {GROUPS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="ledger-period">Period</label>
          <select id="ledger-period" value={period.value} onChange={(event) => setFilter({ period: event.target.value })}>
            {PERIODS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <ResourceView resource={ledger} what="the ledger">
        {(data) => <LedgerBody data={data} group={group} />}
      </ResourceView>
    </>
  );
}

function LedgerBody({ data, group }: { data: LedgerAnswer; group: LedgerGroupBy }) {
  const totals = data.totals;
  const cachedShare = totals.input_tokens ? totals.cached_input_tokens / totals.input_tokens : null;
  const cost = data.reference_cost;
  return (
    <>
      <StatGrid label="Totals">
        <Stat label="Input tokens" value={formatInt(totals.input_tokens)} />
        <Stat label="Output tokens" value={formatInt(totals.output_tokens)} />
        <Stat
          label="Cached input tokens"
          value={formatInt(totals.cached_input_tokens)}
          hint={cachedShare !== null ? `${formatPercent(cachedShare)} of input` : undefined}
        />
        <Stat label="Reasoning tokens" value={formatInt(totals.reasoning_tokens)} />
        <Stat label="Model time" value={formatDuration(totals.wall_seconds)} />
        <Stat label="GPU-seconds" value={formatNumber(totals.gpu_seconds, 1)} />
        <Stat label="Steps" value={formatInt(totals.steps)} hint="model calls" />
        {cost ? (
          <Stat
            label="Reference cost avoided"
            value={formatNumber(cost.avoided, 2)}
            hint={`at ${formatNumber(cost.price_in, 2)} in / ${formatNumber(cost.price_out, 2)} out per million tokens`}
          />
        ) : null}
      </StatGrid>
      {cost ? null : (
        <p className="note">
          The reference-cost counter is off. To see what this local work would have cost at given prices, set{" "}
          <code>reference_price_in</code> and <code>reference_price_out</code> (per million tokens) in the{" "}
          <code>[ledger]</code> table of <code>stepswitch.toml</code>.
        </p>
      )}
      <Section title={`By ${group}`} id="groups">
        {data.groups.length ? (
          <TableWrap label={`Ledger by ${group}`}>
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">{group === "model" ? "Model" : group === "task" ? "Task" : "Day"}</th>
                  <th scope="col" className="num">
                    Steps
                  </th>
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
                </tr>
              </thead>
              <tbody>
                {data.groups.map((row) => (
                  <tr key={row.key}>
                    <th scope="row">
                      {group === "task" ? (
                        <Link to={taskPath(row.key)}>
                          <code>{row.key}</code>
                        </Link>
                      ) : group === "model" ? (
                        <code>{row.key}</code>
                      ) : (
                        row.key
                      )}
                    </th>
                    <td className="num">{formatInt(row.steps)}</td>
                    <td className="num">{formatInt(row.input_tokens)}</td>
                    <td className="num">{formatInt(row.output_tokens)}</td>
                    <td className="num">
                      {formatInt(row.cached_input_tokens)}
                      {row.input_tokens ? (
                        <span className="muted small"> ({formatPercent(row.cached_input_tokens / row.input_tokens)})</span>
                      ) : null}
                    </td>
                    <td className="num">{formatInt(row.reasoning_tokens)}</td>
                    <td className="num">{formatDuration(row.wall_seconds)}</td>
                    <td className="num">{formatNumber(row.gpu_seconds, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        ) : (
          <Empty title="No model calls recorded">
            <p>Rows appear after the first model call of a task.</p>
          </Empty>
        )}
      </Section>
    </>
  );
}
