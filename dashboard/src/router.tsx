// A small client-side router on the History API. The server answers every non-API path with
// index.html, so deep links such as /tasks/<id> and /trajectories/<id> work on reload.
import { useMemo, useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from "react";

const NAVIGATE_EVENT = "stepswitch:navigate";

function subscribe(listener: () => void): () => void {
  window.addEventListener("popstate", listener);
  window.addEventListener(NAVIGATE_EVENT, listener);
  return () => {
    window.removeEventListener("popstate", listener);
    window.removeEventListener(NAVIGATE_EVENT, listener);
  };
}

function snapshot(): string {
  return window.location.pathname + window.location.search;
}

export function navigate(to: string, options: { replace?: boolean } = {}): void {
  if (to === snapshot()) return;
  if (options.replace) window.history.replaceState(null, "", to);
  else window.history.pushState(null, "", to);
  window.dispatchEvent(new Event(NAVIGATE_EVENT));
}

export interface Location {
  path: string;
  query: URLSearchParams;
}

export function useLocation(): Location {
  const href = useSyncExternalStore(subscribe, snapshot, () => "/");
  return useMemo(() => {
    const at = href.indexOf("?");
    const path = at < 0 ? href : href.slice(0, at);
    return { path, query: new URLSearchParams(at < 0 ? "" : href.slice(at + 1)) };
  }, [href]);
}

/** `path` plus the non-empty parameters of `query`. */
export function withQuery(path: string, query: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) if (value) params.set(key, value);
  const text = params.toString();
  return text ? `${path}?${text}` : path;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { to: string };

/** An anchor that navigates without reloading; modified clicks keep their usual meaning. */
export function Link({ to, onClick, target, ...rest }: LinkProps) {
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || target) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(to);
  };
  return <a href={to} target={target} onClick={handle} {...rest} />;
}
