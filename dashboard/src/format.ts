// Display helpers. Times use the viewer's locale; durations read "42 s" or "3 min 5 s"; token
// counts get thousands separators.

const DASH = "-";

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

const integerFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const decimalFormats = new Map<number, Intl.NumberFormat>();

/** An integer with thousands separators, e.g. token counts. */
export function formatInt(value: number | null | undefined): string {
  return isNumber(value) ? integerFormat.format(Math.round(value)) : DASH;
}

/** A number with at most `digits` decimals. */
export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (!isNumber(value)) return DASH;
  let format = decimalFormats.get(digits);
  if (!format) {
    format = new Intl.NumberFormat(undefined, { maximumFractionDigits: digits });
    decimalFormats.set(digits, format);
  }
  return format.format(value);
}

/** A fraction (0..1) as a percentage, e.g. 0.82 -> "82%". */
export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (!isNumber(value)) return DASH;
  return new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: digits }).format(value);
}

/** Seconds as "0.4 s", "42 s", "3 min 5 s" or "2 h 10 min". */
export function formatDuration(seconds: number | null | undefined): string {
  if (!isNumber(seconds)) return DASH;
  const sign = seconds < 0 ? "-" : "";
  const s = Math.abs(seconds);
  if (s < 10) return `${sign}${formatNumber(s, s < 1 && s > 0 ? 2 : 1)} s`;
  if (s < 60) return `${sign}${Math.round(s)} s`;
  if (s < 3600) {
    let minutes = Math.floor(s / 60);
    let rest = Math.round(s - minutes * 60);
    if (rest === 60) {
      minutes += 1;
      rest = 0;
    }
    return rest ? `${sign}${minutes} min ${rest} s` : `${sign}${minutes} min`;
  }
  let hours = Math.floor(s / 3600);
  let minutes = Math.round((s - hours * 3600) / 60);
  if (minutes === 60) {
    hours += 1;
    minutes = 0;
  }
  return minutes ? `${sign}${hours} h ${minutes} min` : `${sign}${hours} h`;
}

const dateTimeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });
const timeFormat = new Intl.DateTimeFormat(undefined, { timeStyle: "medium" });

/** Unix epoch seconds as a local date and time. */
export function formatTime(epochSeconds: number | null | undefined): string {
  if (!isNumber(epochSeconds)) return DASH;
  return dateTimeFormat.format(new Date(epochSeconds * 1000));
}

/** Unix epoch seconds as a local time of day. */
export function formatClock(epochSeconds: number | null | undefined): string {
  if (!isNumber(epochSeconds)) return DASH;
  return timeFormat.format(new Date(epochSeconds * 1000));
}

/** Epoch seconds as an ISO string for <time dateTime>. */
export function isoTime(epochSeconds: number | null | undefined): string | undefined {
  return isNumber(epochSeconds) ? new Date(epochSeconds * 1000).toISOString() : undefined;
}

/** An ISO date or date-time string as a local date and time; other text is shown as it is. */
export function formatDateText(text: string | null | undefined): string {
  if (!text) return DASH;
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  const ms = Date.parse(text);
  return Number.isNaN(ms) ? text : dateTimeFormat.format(new Date(ms));
}

/** Byte counts as "830 B", "5.1 kB" or "2.3 MB". */
export function formatBytes(value: number | null | undefined): string {
  if (!isNumber(value)) return DASH;
  if (Math.abs(value) < 1000) return `${formatInt(value)} B`;
  if (Math.abs(value) < 1_000_000) return `${formatNumber(value / 1000, 1)} kB`;
  if (Math.abs(value) < 1_000_000_000) return `${formatNumber(value / 1_000_000, 1)} MB`;
  return `${formatNumber(value / 1_000_000_000, 1)} GB`;
}

/** A flag as "yes" or "no". */
export function yesNo(value: unknown): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  return DASH;
}

/** Any JSON value as short display text. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return DASH;
  if (typeof value === "boolean") return yesNo(value);
  if (typeof value === "number") return Number.isInteger(value) ? formatInt(value) : formatNumber(value, 3);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.length ? value.map((item) => formatValue(item)).join(", ") : DASH;
  return JSON.stringify(value);
}

/** Shortens text to `max` characters with an ellipsis. */
export function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

const STOP_REASONS: Readonly<Record<string, string>> = {
  budget_tokens: "token budget reached",
  budget_wall_seconds: "time budget reached",
  budget_gpu_seconds: "GPU-seconds budget reached",
  budget_escalations: "escalation budget reached",
  max_steps: "step limit reached",
  stuck: "stuck without progress",
  cancelled: "cancelled",
  no_worker: "no worker available",
  gates_failed: "gates failed",
  model_error: "model error",
  error: "error",
};

/** A task's stop reason in words; unknown codes keep their text with spaces for underscores. */
export function stopReasonText(reason: string): string {
  return STOP_REASONS[reason] ?? reason.replaceAll("_", " ");
}

// Safe readers for loosely typed JSON (trajectory event data, spec fields, capabilities).
export function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

export function asNumber(value: unknown): number | undefined {
  return isNumber(value) ? value : undefined;
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function asStrings(value: unknown): string[] {
  return asArray(value).filter((item): item is string => typeof item === "string");
}
