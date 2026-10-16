import { useEffect, useState, type FormEvent } from "react";
import { api, isApiUrl, isRunImageUrl, type ScienceReport } from "../api";
import { Badge, Empty, InlineCode, PageHeader, ResourceView, StatusBadge, TableWrap, toneOf } from "../components";
import { formatInt } from "../format";
import { useResource } from "../hooks";
import { parseMarkdownTable } from "../markdownTable";
import { Link, navigate, useLocation, withQuery } from "../router";
import { taskPath } from "../routes";

const RESULT_TONES: Record<string, "ok" | "bad"> = { pass: "ok", passed: "ok", fail: "bad", failed: "bad", error: "bad" };

function plotName(url: string): string {
  const last = url.split("/").pop() ?? url;
  try {
    return decodeURIComponent(last).replace(/\.[a-z0-9]+$/i, "");
  } catch {
    return last;
  }
}

export function ScienceView() {
  const { query } = useLocation();
  const task = query.get("task") ?? "";
  const [draft, setDraft] = useState(task);
  useEffect(() => setDraft(task), [task]);
  const science = useResource((signal) => api.science({ task: task || undefined }, signal), [task]);

  const apply = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    navigate(withQuery("/science", { task: draft.trim() || undefined }), { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Science"
        lead="Numerical diff reports from the golden gate of science-track tasks, newest first."
        actions={
          <button type="button" onClick={() => science.reload()}>
            Refresh
          </button>
        }
      />
      <form className="toolbar" onSubmit={apply} role="search" aria-label="Filter the reports">
        <div className="field">
          <label htmlFor="science-task">Task id</label>
          <input
            id="science-task"
            type="search"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="All tasks"
            spellCheck={false}
          />
        </div>
        <button type="submit">Apply</button>
      </form>
      <ResourceView
        resource={science}
        what="the science reports"
        isEmpty={(data) => data.reports.length === 0}
        empty={
          <Empty title={task ? "No reports for this task" : "No numerical diff reports yet"}>
            <p>A report appears when a science-track task runs its golden gate.</p>
          </Empty>
        }
      >
        {(data) => (
          <div className="report-list">
            {data.reports.map((report, index) => (
              <ReportCard key={`${report.task_id}-${report.run}-${index}`} report={report} />
            ))}
          </div>
        )}
      </ResourceView>
    </>
  );
}

function ReportCard({ report }: { report: ScienceReport }) {
  // Only run files that are images go into <img>; other run files are served as text or as
  // downloads, so they get a plain link. Addresses outside the API are never loaded.
  const all = report.plots ?? [];
  const images = all.filter(isRunImageUrl);
  const files = all.filter((url) => isApiUrl(url) && url.startsWith("/api/runs/") && !isRunImageUrl(url));
  const skipped = all.length - images.length - files.length;
  return (
    <article className="panel report" aria-labelledby={`report-${report.task_id}-${report.run}`}>
      <header className="report-head">
        <h2 id={`report-${report.task_id}-${report.run}`}>
          <StatusBadge status={report.status} /> Gate run {formatInt(report.run)}
          {report.step !== null && report.step !== undefined ? `, step ${report.step}` : ""}
        </h2>
        <Link to={taskPath(report.task_id)}>
          <code>{report.task_id}</code>
        </Link>
      </header>
      {report.summary ? <p className="summary">{report.summary}</p> : null}
      {report.cases?.length ? (
        <ul className="tag-list" aria-label="Cases">
          {report.cases.map((item) => (
            <li key={item.case}>
              <Badge tone={toneOf(item.status)}>
                {item.case}: {item.status}
              </Badge>
            </li>
          ))}
        </ul>
      ) : null}
      {report.table ? <ComparisonTable text={report.table} /> : null}
      {images.length ? (
        <div className="plots">
          {images.map((url) => (
            <figure key={url} className="plot">
              <img src={url} alt={`Plot: ${plotName(url)}`} loading="lazy" />
              <figcaption className="small muted">{plotName(url)}</figcaption>
            </figure>
          ))}
        </div>
      ) : null}
      {files.length ? (
        <ul className="link-row" aria-label="Other files">
          {files.map((url) => (
            <li key={url}>
              <a href={url}>{plotName(url)}</a>
            </li>
          ))}
        </ul>
      ) : null}
      {skipped > 0 ? (
        <p className="muted small">
          {skipped} {skipped === 1 ? "plot is" : "plots are"} not shown: the address is not a run file of this dashboard's
          API.
        </p>
      ) : null}
    </article>
  );
}

function ComparisonTable({ text }: { text: string }) {
  const table = parseMarkdownTable(text);
  if (!table) return <pre className="code-block">{text}</pre>;
  return (
    <TableWrap label="Numerical comparison">
      <table className="data-table">
        <thead>
          <tr>
            {table.header.map((cell, index) => (
              <th scope="col" key={index} className={table.align[index] === "right" ? "num" : undefined}>
                <InlineCode text={cell} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, index) => {
                const tone = RESULT_TONES[cell.toLowerCase()];
                return (
                  <td
                    key={index}
                    className={table.align[index] === "right" ? "num" : table.align[index] === "center" ? "center" : undefined}
                  >
                    {tone ? <Badge tone={tone}>{cell}</Badge> : <InlineCode text={cell} />}
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
