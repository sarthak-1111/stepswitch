import { api, isApiUrl, type Contestant, type Leaderboard, type LeaderboardAnswer } from "../api";
import { Empty, PageHeader, ResourceView, Section, TableWrap } from "../components";
import { formatDateText, formatDuration, formatInt, formatNumber, formatPercent, formatValue } from "../format";
import { useResource } from "../hooks";
import { navigate, useLocation, withQuery } from "../router";

const HARDWARE_LABELS: Record<string, string> = {
  os: "OS",
  arch: "architecture",
  cpu_cores: "CPU cores",
  ram_gb: "RAM (GB)",
  gpus: "GPUs",
};

function hardwareText(hardware: Leaderboard["hardware"]): string {
  if (!hardware) return "not recorded";
  if (typeof hardware === "string") return hardware;
  const parts = Object.entries(hardware)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => `${HARDWARE_LABELS[key] ?? key.replace(/_/g, " ")} ${formatValue(value)}`);
  return parts.length ? parts.join(", ") : "not recorded";
}

export function LeaderboardView() {
  const { query } = useLocation();
  const run = query.get("run") ?? "";
  const board = useResource((signal) => api.leaderboard(run || undefined, signal), [run]);

  return (
    <>
      <PageHeader title="Leaderboard" lead="Bench results per contestant, from the runs under bench/results/." />
      <ResourceView
        resource={board}
        what="the leaderboard"
        isEmpty={(data) => data.runs.length === 0 && !data.leaderboard}
        empty={
          <Empty title="No bench results yet">
            <p>
              Run a suite with <code>ssw bench run --suite mini-general</code>, then <code>ssw bench report</code>.
            </p>
          </Empty>
        }
      >
        {(data) => <LeaderboardBody data={data} />}
      </ResourceView>
    </>
  );
}

function LeaderboardBody({ data }: { data: LeaderboardAnswer }) {
  const board = data.leaderboard;
  const selected = data.run ?? "";
  return (
    <>
      <div className="toolbar">
        <div className="field">
          <label htmlFor="leaderboard-run">Run</label>
          <select
            id="leaderboard-run"
            value={selected}
            onChange={(event) =>
              navigate(withQuery("/leaderboard", { run: event.target.value || undefined }), { replace: true })
            }
          >
            {selected && !data.runs.some((item) => item.name === selected) ? <option value={selected}>{selected}</option> : null}
            {data.runs.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
                {item.suite ? ` · ${item.suite}` : ""} · {formatInt(item.rows)} rows
              </option>
            ))}
          </select>
        </div>
      </div>
      {board ? (
        <>
          <Metadata board={board} />
          <Section title="Contestants" id="contestants">
            {board.contestants?.length ? (
              <ContestantsTable board={board} />
            ) : (
              <p className="muted">This run has no contestants.</p>
            )}
          </Section>
          {isApiUrl(data.chart) ? (
            <Section title="Pass rate" id="chart">
              <figure className="chart">
                <img src={data.chart} alt="Pass rate per contestant with its interval" loading="lazy" />
                <figcaption className="muted small">Bars show the pass rate, whiskers its interval.</figcaption>
              </figure>
            </Section>
          ) : null}
        </>
      ) : (
        <p className="muted">This run has no leaderboard.json yet.</p>
      )}
    </>
  );
}

function Metadata({ board }: { board: Leaderboard }) {
  const seeds = board.seeds?.length ? board.seeds.join(", ") : "-";
  return (
    <dl className="kv meta-grid">
      <div>
        <dt>Suite</dt>
        <dd>{board.suite ?? "-"}</dd>
      </div>
      <div>
        <dt>Date</dt>
        <dd>{formatDateText(board.date)}</dd>
      </div>
      <div>
        <dt>Runs</dt>
        <dd>
          {formatInt(board.runs)} runs over {formatInt(board.tasks)} tasks, seeds {seeds}
        </dd>
      </div>
      <div>
        <dt>Command</dt>
        <dd>{board.command ? <code>{board.command}</code> : "not recorded"}</dd>
      </div>
      <div>
        <dt>Commit</dt>
        <dd>{board.commit ? <code>{board.commit}</code> : "-"}</dd>
      </div>
      <div>
        <dt>Hardware</dt>
        <dd>{hardwareText(board.hardware)}</dd>
      </div>
      <div>
        <dt>Scoring</dt>
        <dd>{board.scoring ? `A run ${board.scoring}.` : "-"}</dd>
      </div>
      <div>
        <dt>Router pool</dt>
        <dd>
          {board.router_pool?.length ? (
            board.router_pool.map((worker, index) => (
              <span key={worker}>
                {index ? ", " : ""}
                <code>{worker}</code>
              </span>
            ))
          ) : (
            "-"
          )}
        </dd>
      </div>
    </dl>
  );
}

function intervalText(entry: Contestant): string {
  if (!entry.ci) return "-";
  return `${formatPercent(entry.ci.low)} to ${formatPercent(entry.ci.high)}`;
}

function ContestantsTable({ board }: { board: Leaderboard }) {
  const confidence = board.contestants.find((entry) => entry.ci)?.ci?.confidence ?? board.interval?.confidence ?? 0.95;
  const method = board.contestants.find((entry) => entry.ci)?.ci?.method;
  return (
    <TableWrap label="Contestants">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col" className="num">
              Rank
            </th>
            <th scope="col">Contestant</th>
            <th scope="col" className="num">
              Pass rate
            </th>
            <th scope="col" className="num" title={method}>
              {formatPercent(confidence)} interval
            </th>
            <th scope="col" className="num">
              Runs
            </th>
            <th scope="col" className="num">
              Median wall time
            </th>
            <th scope="col" className="num">
              Median tokens
            </th>
            <th scope="col" className="num">
              Cache hit rate
            </th>
            <th scope="col" className="num">
              GPU-seconds
            </th>
            <th scope="col" className="num">
              Escalation rate
            </th>
            <th scope="col" className="num">
              Reference cost
            </th>
            <th scope="col" className="num" title="Runs that changed a protected path such as the tests">
              Changed tests
            </th>
          </tr>
        </thead>
        <tbody>
          {board.contestants.map((entry) => (
            <tr key={entry.contestant}>
              <td className="num">{entry.rank}</td>
              <th scope="row">
                <code>{entry.contestant}</code>
              </th>
              <td className="num">
                <strong>{formatPercent(entry.pass_rate, 1)}</strong>
                {entry.passed !== undefined && entry.runs !== undefined ? (
                  <span className="muted small">
                    {" "}
                    ({formatInt(entry.passed)}/{formatInt(entry.runs)})
                  </span>
                ) : null}
              </td>
              <td className="num">{intervalText(entry)}</td>
              <td className="num">{formatInt(entry.runs)}</td>
              <td className="num">{formatDuration(entry.median_wall_seconds)}</td>
              <td className="num">{formatInt(entry.median_tokens)}</td>
              <td className="num">{formatPercent(entry.cache_hit_rate)}</td>
              <td className="num">{formatNumber(entry.gpu_seconds, 1)}</td>
              <td className="num">{formatPercent(entry.escalation_rate)}</td>
              <td className="num">
                {entry.reference_cost === null || entry.reference_cost === undefined
                  ? "-"
                  : formatNumber(entry.reference_cost, 4)}
              </td>
              <td className="num">{formatInt(entry.tests_modified ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {method ? <p className="muted small table-note">Intervals: {method}.</p> : null}
    </TableWrap>
  );
}
