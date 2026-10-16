import { api, type CompatibilityAnswer, type CompatResult, type LocalProbe } from "../api";
import { Badge, Empty, PageHeader, ResourceView, Section, TableWrap, TimeText } from "../components";
import { formatValue } from "../format";
import { useResource } from "../hooks";

const CELL_MARKS: Record<string, string> = { yes: "✓", no: "✗", skipped: "–", error: "!" };

function toolPathText(result: CompatResult): string {
  if (!result.tool_path) return "-";
  return result.tool_parser ? `${result.tool_path}, parsed from text (${result.tool_parser})` : result.tool_path;
}

export function CompatibilityView() {
  const matrix = useResource((signal) => api.compatibility(signal), []);
  return (
    <>
      <PageHeader
        title="Compatibility"
        lead="What each probed model and engine was verified to do, from the probe results in probes/ and this project's own probes."
      />
      <ResourceView
        resource={matrix}
        what="the compatibility matrix"
        isEmpty={(data) => data.results.length === 0 && data.local.length === 0 && !data.problems?.length}
        empty={
          <Empty title="No probe results">
            <p>
              Probe an endpoint with <code>ssw probe &lt;endpoint&gt;</code>; results appear here.
            </p>
          </Empty>
        }
      >
        {(data) => <MatrixBody data={data} />}
      </ResourceView>
    </>
  );
}

function MatrixBody({ data }: { data: CompatibilityAnswer }) {
  const ciSet = new Set(data.ci_set ?? []);
  return (
    <>
      {data.problems?.length ? (
        <div className="banner banner-warn" role="status">
          <p className="state-title">Some probe files could not be read:</p>
          <ul className="problem-list">
            {data.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <Section title={`Models (${data.results.length})`} id="models">
        {data.results.length ? (
          <ModelsTable results={data.results} ciSet={ciSet} />
        ) : (
          <p className="muted">No committed probe results.</p>
        )}
      </Section>
      {data.results.length ? (
        <Section title="Checks" id="checks">
          <ChecksTable data={data} />
          <p className="muted small">
            “skipped”: the check did not apply or was not run. “error”: it could not complete, so the capability is
            unknown.
          </p>
        </Section>
      ) : null}
      <Section title={`This project's probes (${data.local.length})`} id="local">
        {data.local.length ? (
          <LocalTable probes={data.local} />
        ) : (
          <p className="muted">
            No local probes yet. Run <code>ssw probe &lt;endpoint&gt;</code>.
          </p>
        )}
      </Section>
    </>
  );
}

function ModelsTable({ results, ciSet }: { results: CompatResult[]; ciSet: Set<string> }) {
  return (
    <TableWrap label="Probed models">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Family</th>
            <th scope="col">Engine</th>
            <th scope="col">Format</th>
            <th scope="col">Probe suite</th>
            <th scope="col">Tool calls</th>
            <th scope="col">Date</th>
            <th scope="col">Result file</th>
          </tr>
        </thead>
        <tbody>
          {results.map((result) => (
            <tr key={result.file}>
              <th scope="row">
                <code>{result.model}</code>
                {ciSet.has(result.model) ? (
                  <>
                    {" "}
                    <Badge tone="info" title="Part of the CI model set">
                      CI set
                    </Badge>
                  </>
                ) : null}
              </th>
              <td>{result.family ?? "-"}</td>
              <td>
                {result.engine ?? "-"}
                {result.engine_version ? <div className="muted small">{result.engine_version}</div> : null}
              </td>
              <td>{result.format ?? "-"}</td>
              <td>
                {result.passes ? <Badge tone="ok">passes</Badge> : <Badge tone="bad">fails</Badge>}
                {!result.passes && result.failed?.length ? (
                  <div className="small">failed: {result.failed.join(", ")}</div>
                ) : null}
              </td>
              <td>{toolPathText(result)}</td>
              <td>{result.date ?? "-"}</td>
              <td>
                <code className="small">{result.file}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function ChecksTable({ data }: { data: CompatibilityAnswer }) {
  const known = new Set(data.checks.map((check) => check.name));
  const extra = new Set<string>();
  for (const result of data.results) for (const name of Object.keys(result.checks ?? {})) if (!known.has(name)) extra.add(name);
  const checks = [...data.checks, ...Array.from(extra).map((name) => ({ name, label: name.replace(/_/g, " ") }))];
  return (
    <TableWrap label="Checks per model">
      <table className="data-table matrix">
        <thead>
          <tr>
            <th scope="col" className="sticky-col">
              Check
            </th>
            {data.results.map((result) => (
              <th scope="col" key={result.file}>
                <code>{result.model}</code>
                {result.engine ? <div className="muted small">{result.engine}</div> : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {checks.map((check) => (
            <tr key={check.name}>
              <th scope="row" className="sticky-col">
                {check.label || check.name}
              </th>
              {data.results.map((result) => {
                const value = result.checks?.[check.name] ?? "skipped";
                return (
                  <td key={result.file} className={`cell cell-${value}`}>
                    <span aria-hidden="true">{CELL_MARKS[value] ?? "?"} </span>
                    {value}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function LocalTable({ probes }: { probes: LocalProbe[] }) {
  return (
    <TableWrap label="This project's probes">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Endpoint</th>
            <th scope="col">Model</th>
            <th scope="col">Probed</th>
            <th scope="col">Capabilities</th>
          </tr>
        </thead>
        <tbody>
          {probes.map((probe) => (
            <tr key={`${probe.endpoint}/${probe.model}`}>
              <th scope="row">{probe.endpoint}</th>
              <td>
                <code>{probe.model}</code>
              </td>
              <td>
                <TimeText epoch={probe.created} />
              </td>
              <td className="wrap">
                {Object.keys(probe.capabilities ?? {}).length ? (
                  <ul className="tag-list">
                    {Object.entries(probe.capabilities).map(([name, value]) => (
                      <li key={name}>
                        <Badge tone={value === true ? "ok" : value === false ? "neutral" : "info"}>
                          {name.replace(/_/g, " ")}: {formatValue(value)}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="muted">-</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
