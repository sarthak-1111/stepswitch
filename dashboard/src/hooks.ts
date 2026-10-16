import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type DependencyList } from "react";
import { isUnauthorized, subscribeUnauthorized } from "./api";

export interface Resource<T> {
  data: T | undefined;
  error: Error | undefined;
  /** True while a load runs that the view should show (the first one, or a visible reload). */
  loading: boolean;
  /** Reloads; `silent` keeps the current data on screen without a loading state. */
  reload: (options?: { silent?: boolean }) => void;
  /**
   * Changes the data in place, e.g. from a live event. Updates that arrive while a load is
   * running are applied again to its answer, so they must be idempotent (upserts, not appends).
   */
  update: (updater: (data: T) => T) => void;
}

interface ResourceState<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** Loads data with `load` whenever `deps` change; stale answers are dropped. */
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, deps: DependencyList): Resource<T> {
  const [state, setState] = useState<ResourceState<T>>({ data: undefined, error: undefined, loading: true });
  const loadRef = useRef(load);
  loadRef.current = load;
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const pending = useRef<Array<(data: T) => T>>([]);
  const inFlight = useRef(false);

  const run = useCallback((silent: boolean) => {
    controller.current?.abort();
    const own = new AbortController();
    controller.current = own;
    const id = ++generation.current;
    inFlight.current = true;
    pending.current = [];
    setState((previous) =>
      silent && previous.data !== undefined ? previous : { ...previous, loading: true, error: undefined },
    );
    loadRef.current(own.signal).then(
      (data) => {
        if (id !== generation.current) return;
        inFlight.current = false;
        const updates = pending.current;
        pending.current = [];
        const merged = updates.reduce((value, updater) => updater(value), data);
        setState({ data: merged, error: undefined, loading: false });
      },
      (error: unknown) => {
        if (id !== generation.current || isAbort(error)) return;
        inFlight.current = false;
        pending.current = [];
        const failure = error instanceof Error ? error : new Error(String(error));
        setState((previous) => ({ ...previous, error: failure, loading: false }));
      },
    );
  }, []);

  // `deps` stands for everything `load` reads, like the dependency list of useEffect.
  useEffect(() => {
    run(false);
    return () => {
      generation.current += 1;
      controller.current?.abort();
    };
  }, deps);

  const reload = useCallback((options?: { silent?: boolean }) => run(options?.silent ?? false), [run]);

  const update = useCallback((updater: (data: T) => T) => {
    if (inFlight.current) pending.current.push(updater);
    setState((previous) => (previous.data === undefined ? previous : { ...previous, data: updater(previous.data) }));
  }, []);

  return { data: state.data, error: state.error, loading: state.loading, reload, update };
}

/** A stable function that runs `callback` once, `delay` ms after the last call. */
export function useDebounced(callback: () => void, delay: number): () => void {
  const ref = useRef(callback);
  ref.current = callback;
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(
    () => () => {
      if (timer.current !== undefined) clearTimeout(timer.current);
    },
    [],
  );
  return useCallback(() => {
    if (timer.current !== undefined) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = undefined;
      ref.current();
    }, delay);
  }, [delay]);
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} · Stepswitch` : "Stepswitch";
  }, [title]);
}

export function useUnauthorized(): boolean {
  return useSyncExternalStore(subscribeUnauthorized, isUnauthorized);
}
