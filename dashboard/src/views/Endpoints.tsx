import { useEffect, useRef, useState } from "react";
import { api, type Endpoint, type EndpointsAnswer, type InstalledModel, type ManagedServer } from "../api";
import { Badge, Empty, PageHeader, ResourceView, Section, TableWrap, TimeText, type Tone } from "../components";
import { formatClock, formatNumber, formatValue, isoTime } from "../format";
import { useResource } from "../hooks";

/** The answers of the last status check (GET /api/endpoints?check=true). */
interface Status {
  at: number;
  endpoints: Record<string, { reachable: boolean | null; httpStatus: number | null; error: string | null }>;
  alive: Record<string, boolean | null>;
}

const POLL_MS = 2500;

function serverKey(server: ManagedServer): string {
  return `${server.model}|${server.base_url ?? ""}`;
}

/** A model's server state; older answers have only the `running` flag. */
function stateOf(model: InstalledModel): string {
  return model.state ?? (model.running ? "running" : "stopped");
}

const STATE_TONES: Record<string, Tone> = { running: "ok", starting: "info", failed: "bad", stopped: "neutral" };

export function EndpointsView() {
  const endpoints = useResource((signal) => api.endpoints({}, signal), []);
  // Status checks contact the endpoints, so they run only when asked for; their answers stay on
  // screen across later refreshes.
  const [status, setStatus] = useState<Status | null>(null);
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const poll = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // While a model server is starting, look again every few seconds (no endpoint is contacted).
  const starting = (endpoints.data?.installed ?? []).some((model) => stateOf(model) === "starting");
  useEffect(() => {
    if (!starting) return undefined;
    poll.current = setTimeout(() => endpoints.reload({ silent: true }), POLL_MS);
    return () => clearTimeout(poll.current);
  }, [starting, endpoints.data, endpoints.reload]);

  async function checkStatus() {
    setChecking(true);
    setMessage(null);
    try {
      const answer = await api.endpoints({ check: true });
      const next: Status = { at: Date.now() / 1000, endpoints: {}, alive: {} };
      for (const endpoint of answer.endpoints) {
        next.endpoints[endpoint.name] = {
          reachable: typeof endpoint.reachable === "boolean" ? endpoint.reachable : null,
          httpStatus: typeof endpoint.http_status === "number" ? endpoint.http_status : null,
          error: endpoint.error ?? null,
        };
      }
      for (const server of answer.managed) next.alive[serverKey(server)] = server.alive;
      setStatus(next);
      endpoints.update(() => answer);
      if (endpoints.data === undefined) endpoints.reload({ silent: true });
    } catch (failure) {
      setMessage({ tone: "error", text: failure instanceof Error ? failure.message : String(failure) });
    } finally {
      setChecking(false);
    }
  }

  async function act(model: InstalledModel, action: "start" | "stop") {
    setBusy(`${action}:${model.id}`);
    setMessage(null);
    try {
      if (action === "start") {
        const answer = await api.startModel(model.id);
        setMessage({ tone: "ok", text: `${answer.model}: ${answer.status}. The model loads in the background.` });
      } else {
        const answer = await api.stopModel(model.id);
        setMessage({ tone: "ok", text: `${answer.model}: ${answer.stopped ? "stopped" : "was not running"}.` });
      }
    } catch (failure) {
      setMessage({ tone: "error", text: failure instanceof Error ? failure.message : String(failure) });
    } finally {
      setBusy(null);
      endpoints.reload({ silent: true });
    }
  }

  return (
    <>
      <PageHeader
        title="Endpoints"
        lead="Configured endpoints, the model servers Stepswitch manages, and the installed registry models."
        actions={
          <button type="button" onClick={checkStatus} disabled={checking}>
            {checking ? "Checking…" : "Check status"}
          </button>
        }
      />
      {message ? (
        <p
          className={`banner ${message.tone === "ok" ? "banner-ok" : "banner-error"}`}
          role={message.tone === "ok" ? "status" : "alert"}
        >
          {message.text}
        </p>
      ) : null}
      <ResourceView resource={endpoints} what="the endpoints">
        {(data) => <EndpointsBody data={data} status={status} busy={busy} onAction={act} />}
      </ResourceView>
    </>
  );
}

function EndpointsBody({
  data,
  status,
  busy,
  onAction,
}: {
  data: EndpointsAnswer;
  status: Status | null;
  busy: string | null;
  onAction: (model: InstalledModel, action: "start" | "stop") => void;
}) {
  return (
    <>
      <Section title={`Configured endpoints (${data.endpoints.length})`} id="configured">
        {data.endpoints.length ? (
          <EndpointTable endpoints={data.endpoints} status={status} />
        ) : (
          <Empty title="No endpoints configured">
            <p>
              Add one with <code>ssw models add &lt;endpoint URL&gt;</code>.
            </p>
          </Empty>
        )}
      </Section>
      <Section title={`Managed model servers (${data.managed.length})`} id="managed">
        {data.managed.length ? (
          <ManagedTable servers={data.managed} status={status} />
        ) : (
          <p className="muted">No managed model server is running.</p>
        )}
      </Section>
      <Section title={`Installed models (${data.installed.length})`} id="installed">
        {data.installed.length ? (
          <InstalledTable models={data.installed} errors={data.model_errors ?? {}} busy={busy} onAction={onAction} />
        ) : (
          <Empty title="No registry models installed">
            <p>
              Pull the CPU tier with <code>ssw models pull --tier cpu</code>.
            </p>
          </Empty>
        )}
      </Section>
    </>
  );
}

function Reachability({ endpoint, status }: { endpoint: Endpoint; status: Status | null }) {
  const checked = status?.endpoints[endpoint.name];
  const reachable = checked ? checked.reachable : endpoint.reachable;
  const httpStatus = checked ? checked.httpStatus : endpoint.http_status;
  const error = checked ? checked.error : endpoint.error;
  if (reachable === true || reachable === false) {
    const details = [httpStatus ? `HTTP ${httpStatus}` : "", error ?? ""].filter(Boolean).join(", ");
    return (
      <>
        {reachable ? <Badge tone="ok">reachable</Badge> : <Badge tone="bad">unreachable</Badge>}
        {details ? <div className="muted small">{details}</div> : null}
      </>
    );
  }
  return <span className="muted small">{status ? "unknown" : "not checked"}</span>;
}

function Capabilities({ capabilities }: { capabilities: Record<string, unknown> }) {
  const entries = Object.entries(capabilities ?? {});
  if (!entries.length) return <span className="muted">-</span>;
  return (
    <ul className="tag-list">
      {entries.map(([name, value]) => (
        <li key={name}>
          <Badge tone={value === true ? "ok" : value === false ? "neutral" : "info"}>
            {name.replace(/_/g, " ")}: {formatValue(value)}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

function EndpointTable({ endpoints, status }: { endpoints: Endpoint[]; status: Status | null }) {
  return (
    <TableWrap label="Configured endpoints">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Engine</th>
            <th scope="col">Format</th>
            <th scope="col">Where</th>
            <th scope="col">URL</th>
            <th scope="col">Models</th>
            <th scope="col">Tool parser</th>
            <th scope="col">
              Status
              {status ? (
                <span className="muted small th-note">
                  {" "}
                  at <time dateTime={isoTime(status.at)}>{formatClock(status.at)}</time>
                </span>
              ) : null}
            </th>
            <th scope="col">Probe results</th>
          </tr>
        </thead>
        <tbody>
          {endpoints.map((endpoint) => {
            const probes = Object.entries(endpoint.probes ?? {});
            const others = (endpoint.formats ?? []).filter((format) => format !== endpoint.format);
            return (
              <tr key={endpoint.name}>
                <th scope="row">{endpoint.name}</th>
                <td>{endpoint.engine ?? "-"}</td>
                <td>
                  {endpoint.format ?? "-"}
                  {others.length ? <div className="muted small">also {others.join(", ")}</div> : null}
                </td>
                <td>{endpoint.local ? <Badge tone="info">local</Badge> : <Badge tone="orange">remote</Badge>}</td>
                <td>{endpoint.url ? <code>{endpoint.url}</code> : <span className="muted">-</span>}</td>
                <td>
                  {endpoint.models?.length ? (
                    <ul className="plain-list">
                      {endpoint.models.map((model) => (
                        <li key={model}>
                          <code>{model}</code>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <span className="muted">-</span>
                  )}
                </td>
                <td>{endpoint.tool_parser ?? "-"}</td>
                <td>
                  <Reachability endpoint={endpoint} status={status} />
                </td>
                <td className="wrap wrap-wide">
                  {probes.length ? (
                    probes.map(([model, probe]) => (
                      <div key={model} className="probe">
                        <div className="small">
                          <code>{model}</code>
                          {probe.format ? ` · ${probe.format}` : ""} · <TimeText epoch={probe.created} />
                        </div>
                        <Capabilities capabilities={probe.capabilities} />
                      </div>
                    ))
                  ) : (
                    <span className="muted small">not probed</span>
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

function Alive({ server, status }: { server: ManagedServer; status: Status | null }) {
  const key = serverKey(server);
  const alive = server.alive ?? (status && key in status.alive ? status.alive[key] : null);
  if (alive === true) return <Badge tone="ok">alive</Badge>;
  if (alive === false) return <Badge tone="bad">not answering</Badge>;
  return <span className="muted small">not checked</span>;
}

function ManagedTable({ servers, status }: { servers: ManagedServer[]; status: Status | null }) {
  return (
    <TableWrap label="Managed model servers">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Engine</th>
            <th scope="col">Alive</th>
            <th scope="col">Base URL</th>
            <th scope="col">Started</th>
            <th scope="col">Last used</th>
          </tr>
        </thead>
        <tbody>
          {servers.map((server) => (
            <tr key={serverKey(server)}>
              <th scope="row">
                <code>{server.model}</code>
              </th>
              <td>{server.engine ?? "-"}</td>
              <td>
                <Alive server={server} status={status} />
              </td>
              <td>{server.base_url ? <code>{server.base_url}</code> : "-"}</td>
              <td>
                <TimeText epoch={server.started} />
              </td>
              <td>
                <TimeText epoch={server.last_used} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function InstalledTable({
  models,
  errors,
  busy,
  onAction,
}: {
  models: InstalledModel[];
  errors: Record<string, string>;
  busy: string | null;
  onAction: (model: InstalledModel, action: "start" | "stop") => void;
}) {
  return (
    <TableWrap label="Installed models">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col" className="num">
              RAM (GB)
            </th>
            <th scope="col">Roles</th>
            <th scope="col">Server</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {models.map((model) => {
            const state = stateOf(model);
            const canStart = state === "stopped" || state === "failed";
            const canStop = state === "running" || state === "starting";
            return (
              <tr key={model.id}>
                <th scope="row">
                  {model.name}
                  <div className="muted small">
                    <code>{model.id}</code>
                  </div>
                </th>
                <td className="num">{formatNumber(model.ram_gb, 1)}</td>
                <td>{model.roles?.length ? model.roles.join(", ") : "-"}</td>
                <td className="wrap">
                  <Badge tone={STATE_TONES[state] ?? "neutral"}>{state}</Badge>
                  {errors[model.id] ? <div className="field-error small">{errors[model.id]}</div> : null}
                </td>
                <td>
                  <div className="actions">
                    <button
                      type="button"
                      className="small"
                      disabled={!canStart || busy !== null}
                      onClick={() => onAction(model, "start")}
                    >
                      {busy === `start:${model.id}` ? "Starting…" : state === "failed" ? "Retry" : "Start"}
                      <span className="visually-hidden"> {model.name}</span>
                    </button>
                    <button
                      type="button"
                      className="small"
                      disabled={!canStop || busy !== null}
                      onClick={() => onAction(model, "stop")}
                    >
                      {busy === `stop:${model.id}` ? "Stopping…" : "Stop"}
                      <span className="visually-hidden"> {model.name}</span>
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}
