// One shared EventSource on GET /api/events for the whole dashboard. The browser reconnects by
// itself after network errors (and sends Last-Event-ID, so the server continues where it left
// off). Only when the browser gives up for good (the server answered with an error status) does
// this module open a new stream, after a pause.
import { useEffect, useRef, useSyncExternalStore } from "react";
import { EVENTS_URL, type LiveEventMap } from "./api";

export type LiveEventType = keyof LiveEventMap;
export type ConnectionState = "connecting" | "live" | "reconnecting" | "offline";

const EVENT_TYPES: LiveEventType[] = ["task", "trajectory", "ledger", "decision"];
const FIRST_RETRY_MS = 5_000;
const MAX_RETRY_MS = 60_000;

type Handler<K extends LiveEventType> = (data: LiveEventMap[K]) => void;

const handlers: { [K in LiveEventType]: Set<Handler<K>> } = {
  task: new Set(),
  trajectory: new Set(),
  ledger: new Set(),
  decision: new Set(),
};
const reopenHandlers = new Set<() => void>();
const stateListeners = new Set<() => void>();

let source: EventSource | null = null;
let state: ConnectionState = "connecting";
let everOpen = false;
let stopped = true;
let retryTimer: ReturnType<typeof setTimeout> | undefined;
let retryDelay = FIRST_RETRY_MS;

function setState(next: ConnectionState): void {
  if (state === next) return;
  state = next;
  stateListeners.forEach((listener) => listener());
}

function dispatch<K extends LiveEventType>(type: K, raw: string): void {
  let data: LiveEventMap[K];
  try {
    data = JSON.parse(raw) as LiveEventMap[K];
  } catch {
    return;
  }
  (handlers[type] as Set<Handler<K>>).forEach((handler) => handler(data));
}

function open(): void {
  if (source || stopped || typeof EventSource === "undefined") return;
  const stream = new EventSource(EVENTS_URL);
  source = stream;
  setState(everOpen ? "reconnecting" : "connecting");
  stream.onopen = () => {
    const reopened = everOpen;
    everOpen = true;
    retryDelay = FIRST_RETRY_MS;
    setState("live");
    // Events may have been missed while the stream was down: let views refresh.
    if (reopened) reopenHandlers.forEach((handler) => handler());
  };
  stream.onerror = () => {
    if (stream.readyState === EventSource.CLOSED) {
      stream.close();
      if (source === stream) source = null;
      setState("offline");
      scheduleRetry();
    } else {
      setState(everOpen ? "reconnecting" : "connecting");
    }
  };
  for (const type of EVENT_TYPES) {
    stream.addEventListener(type, (event) => dispatch(type, (event as MessageEvent<string>).data));
  }
}

function scheduleRetry(): void {
  if (stopped || retryTimer !== undefined) return;
  retryTimer = setTimeout(() => {
    retryTimer = undefined;
    open();
  }, retryDelay);
  retryDelay = Math.min(retryDelay * 2, MAX_RETRY_MS);
}

/** Opens the shared stream (idempotent). */
export function connect(): void {
  stopped = false;
  open();
}

/** Closes the stream and stops retrying, e.g. when the access token is missing. */
export function disconnect(): void {
  stopped = true;
  if (retryTimer !== undefined) clearTimeout(retryTimer);
  retryTimer = undefined;
  source?.close();
  source = null;
  setState("offline");
}

export function subscribe<K extends LiveEventType>(type: K, handler: Handler<K>): () => void {
  const set = handlers[type] as Set<Handler<K>>;
  set.add(handler);
  return () => {
    set.delete(handler);
  };
}

function subscribeState(listener: () => void): () => void {
  stateListeners.add(listener);
  return () => {
    stateListeners.delete(listener);
  };
}

export function useConnectionState(): ConnectionState {
  return useSyncExternalStore(subscribeState, () => state);
}

/** Calls `handler` for every live event of `type`; the latest handler is always used. */
export function useLiveEvent<K extends LiveEventType>(type: K, handler: Handler<K>): void {
  const ref = useRef(handler);
  useEffect(() => {
    ref.current = handler;
  });
  useEffect(() => subscribe(type, (data) => ref.current(data)), [type]);
}

/** Calls `handler` when the stream opens again after an interruption. */
export function useStreamReopened(handler: () => void): void {
  const ref = useRef(handler);
  useEffect(() => {
    ref.current = handler;
  });
  useEffect(() => {
    const listener = () => ref.current();
    reopenHandlers.add(listener);
    return () => {
      reopenHandlers.delete(listener);
    };
  }, []);
}
