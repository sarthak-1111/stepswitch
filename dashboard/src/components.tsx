import type { ReactNode } from "react";
import { ApiError } from "./api";
import { formatTime, isoTime } from "./format";
import { useDocumentTitle, type Resource } from "./hooks";

export type Tone = "neutral" | "info" | "ok" | "warn" | "bad" | "purple" | "orange";

const STATUS_TONES: Record<string, Tone> = {
  queued: "neutral",
  routing: "purple",
  running: "info",
  verifying: "warn",
  escalated: "orange",
  done: "ok",
  failed: "bad",
  "needs-human": "orange",
  stopped: "neutral",
  passing: "ok",
  failing: "bad",
  pending: "neutral",
  skipped: "neutral",
  passed: "ok",
  pass: "ok",
  error: "bad",
  yes: "ok",
  no: "bad",
};

export function toneOf(status: string | null | undefined): Tone {
  return (status && STATUS_TONES[status.toLowerCase()]) || "neutral";
}

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge tone-${tone}`} title={title}>
      {children}
    </span>
  );
}

/** A status as text with a matching color; the text alone carries the meaning. */
export function StatusBadge({ status, title }: { status: string | null | undefined; title?: string }) {
  const text = status || "unknown";
  return (
    <Badge tone={toneOf(text)} title={title}>
      {text}
    </Badge>
  );
}

export function PageHeader({ title, lead, actions }: { title: string; lead?: ReactNode; actions?: ReactNode }) {
  useDocumentTitle(title);
  return (
    <header className="page-header">
      <div className="page-heading">
        <h1 id="page-title" tabIndex={-1}>
          {title}
        </h1>
        {lead ? <p className="lead">{lead}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="state state-loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </p>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state-empty">
      <p className="state-title">{title}</p>
      {children ? <div className="state-body">{children}</div> : null}
    </div>
  );
}

function messageOf(error: Error): string {
  if (error instanceof ApiError && error.status === 404) return `Not found: ${error.message}`;
  return error.message || "Unknown error";
}

export function ErrorState({ error, onRetry, what }: { error: Error; onRetry?: () => void; what?: string }) {
  return (
    <div className="state state-error" role="alert">
      <p className="state-title">Could not load {what ?? "this view"}.</p>
      <p>{messageOf(error)}</p>
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

/** A slim error line above data that is still shown, e.g. after a failed background refresh. */
export function ErrorBanner({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="banner banner-error" role="alert">
      <span>Refresh failed: {messageOf(error)}</span>
      {onRetry ? (
        <button type="button" className="link-button" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

/** Loading, error and empty states around the data of a resource. */
export function ResourceView<T>({
  resource,
  isEmpty,
  empty,
  what,
  children,
}: {
  resource: Resource<T>;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  what?: string;
  children: (data: T) => ReactNode;
}) {
  const { data, error, loading, reload } = resource;
  if (data === undefined) {
    if (error) return <ErrorState error={error} what={what} onRetry={() => reload()} />;
    return <Loading />;
  }
  return (
    <div className={loading ? "is-updating" : undefined} aria-busy={loading || undefined}>
      {error ? <ErrorBanner error={error} onRetry={() => reload()} /> : null}
      {loading ? <Loading label="Updating…" /> : null}
      {isEmpty?.(data) ? empty : children(data)}
    </div>
  );
}

/** A table container that scrolls sideways by itself instead of the page. */
export function TableWrap({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  small = false,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  /** Smaller value text, for long values such as dates. */
  small?: boolean;
}) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd>
        <span className={small ? "stat-value small-value" : "stat-value"}>{value}</span>
        {hint ? <span className="stat-hint">{hint}</span> : null}
      </dd>
    </div>
  );
}

export function StatGrid({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <dl className="stat-grid" aria-label={label}>
      {children}
    </dl>
  );
}

export function TimeText({ epoch }: { epoch: number | null | undefined }) {
  const iso = isoTime(epoch);
  return iso ? <time dateTime={iso}>{formatTime(epoch)}</time> : <span>-</span>;
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="code-block">{JSON.stringify(value, null, 2)}</pre>;
}

/** Text with `inline code` spans rendered as <code>. */
export function InlineCode({ text }: { text: string }) {
  const parts = text.split("`");
  if (parts.length < 3 || parts.length % 2 === 0) return <>{text}</>;
  return (
    <>
      {parts.map((part, index) => (index % 2 === 1 ? <code key={index}>{part}</code> : part))}
    </>
  );
}

export function Section({
  title,
  id,
  children,
  actions,
}: {
  title: string;
  id?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section className="section" aria-labelledby={headingId} id={id}>
      <div className="section-header">
        <h2 id={headingId}>{title}</h2>
        {actions ? <div className="section-actions">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}
