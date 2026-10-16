// The dashboard's views (SPEC §17), one route each. "/" redirects to "/tasks".

export const ROUTES = [
  { view: "tasks", path: "/tasks", label: "Tasks", detail: true },
  { view: "agents", path: "/agents", label: "Agents", detail: false },
  { view: "router", path: "/router", label: "Router", detail: false },
  { view: "ledger", path: "/ledger", label: "Ledger", detail: false },
  { view: "leaderboard", path: "/leaderboard", label: "Leaderboard", detail: false },
  { view: "trajectories", path: "/trajectories", label: "Trajectories", detail: true },
  { view: "endpoints", path: "/endpoints", label: "Endpoints", detail: false },
  { view: "compatibility", path: "/compatibility", label: "Compatibility", detail: false },
  { view: "audit", path: "/audit", label: "Audit", detail: false },
  { view: "science", path: "/science", label: "Science", detail: false },
  { view: "claims", path: "/claims", label: "Claims", detail: false },
] as const;

export type ViewName = (typeof ROUTES)[number]["view"];

export const HOME = "/tasks";

export interface RouteMatch {
  view: ViewName | "home" | "not-found";
  /** The id in /tasks/<id> and /trajectories/<id>. */
  id?: string;
}

function decode(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

export function matchRoute(pathname: string): RouteMatch {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/" || path === "/index.html") return { view: "home" };
  for (const route of ROUTES) {
    if (path === route.path) return { view: route.view };
    if (route.detail && path.startsWith(`${route.path}/`)) {
      const id = decode(path.slice(route.path.length + 1));
      if (id && !id.includes("/")) return { view: route.view, id };
    }
  }
  return { view: "not-found" };
}

export function taskPath(id: string): string {
  return `/tasks/${encodeURIComponent(id)}`;
}

export function trajectoryPath(id: string): string {
  return `/trajectories/${encodeURIComponent(id)}`;
}
