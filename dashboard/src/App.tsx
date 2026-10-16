import { useEffect, useRef, useState, type ReactNode } from "react";
import { PageHeader } from "./components";
import { connect, disconnect, useConnectionState, type ConnectionState } from "./events";
import { useDocumentTitle, useUnauthorized } from "./hooks";
import { Link, navigate, useLocation } from "./router";
import { HOME, ROUTES, matchRoute, type RouteMatch } from "./routes";
import { AgentsView } from "./views/Agents";
import { AuditView } from "./views/Audit";
import { ClaimsView } from "./views/Claims";
import { CompatibilityView } from "./views/Compatibility";
import { EndpointsView } from "./views/Endpoints";
import { LeaderboardView } from "./views/Leaderboard";
import { LedgerView } from "./views/Ledger";
import { RouterView } from "./views/Router";
import { ScienceView } from "./views/Science";
import { TaskDetailView } from "./views/TaskDetail";
import { TasksView } from "./views/Tasks";
import { TrajectoriesView, TrajectoryView } from "./views/Trajectories";

export function App() {
  const unauthorized = useUnauthorized();
  const { path } = useLocation();
  const match = matchRoute(path);

  useEffect(() => {
    if (match.view === "home") navigate(HOME, { replace: true });
  }, [match.view]);

  useEffect(() => {
    if (unauthorized) disconnect();
    else connect();
  }, [unauthorized]);

  if (unauthorized) return <TokenScreen />;
  return <Shell match={match} path={path} />;
}

function renderView(match: RouteMatch, path: string): ReactNode {
  switch (match.view) {
    case "tasks":
      return match.id ? <TaskDetailView key={match.id} id={match.id} /> : <TasksView />;
    case "agents":
      return <AgentsView />;
    case "router":
      return <RouterView />;
    case "ledger":
      return <LedgerView />;
    case "leaderboard":
      return <LeaderboardView />;
    case "trajectories":
      return match.id ? <TrajectoryView key={match.id} id={match.id} /> : <TrajectoriesView />;
    case "endpoints":
      return <EndpointsView />;
    case "compatibility":
      return <CompatibilityView />;
    case "audit":
      return <AuditView />;
    case "science":
      return <ScienceView />;
    case "claims":
      return <ClaimsView />;
    case "home":
      return null;
    default:
      return <NotFound path={path} />;
  }
}

function Shell({ match, path }: { match: RouteMatch; path: string }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const firstRender = useRef(true);

  // After client-side navigation: close the menu, scroll up and move focus to the new heading,
  // so keyboard and screen-reader users land on the new page.
  useEffect(() => {
    setMenuOpen(false);
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    window.scrollTo(0, 0);
    const target = document.getElementById("page-title") ?? document.getElementById("main");
    target?.focus({ preventScroll: true });
  }, [path]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="sidebar">
        <div className="brand-row">
          <Link to={HOME} className="brand">
            <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
              <rect width="32" height="32" rx="7" />
              <path d="M7 11h9l4 10h5M7 21h5l3-7.5M20 11h5" />
            </svg>
            Stepswitch
          </Link>
          <ConnectionIndicator />
          <button
            type="button"
            className="menu-toggle"
            aria-expanded={menuOpen}
            aria-controls="main-nav"
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? "Close menu" : "Menu"}
          </button>
        </div>
        <nav id="main-nav" aria-label="Main" className={menuOpen ? "nav is-open" : "nav"}>
          <ul>
            {ROUTES.map((route) => (
              <li key={route.view}>
                <Link to={route.path} aria-current={match.view === route.view ? "page" : undefined}>
                  {route.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main id="main" tabIndex={-1}>
        {renderView(match, path)}
      </main>
    </div>
  );
}

const CONNECTION_TEXT: Record<ConnectionState, string> = {
  connecting: "Connecting",
  live: "Live",
  reconnecting: "Reconnecting",
  offline: "Offline",
};

const CONNECTION_HINT: Record<ConnectionState, string> = {
  connecting: "Opening the live event stream",
  live: "Live updates are on",
  reconnecting: "The live event stream dropped; the browser is reconnecting",
  offline: "No live updates; retrying shortly",
};

function ConnectionIndicator() {
  const state = useConnectionState();
  return (
    <span className={`connection connection-${state}`} role="status" title={CONNECTION_HINT[state]}>
      <span className="connection-dot" aria-hidden="true" />
      {CONNECTION_TEXT[state]}
    </span>
  );
}

function TokenScreen() {
  useDocumentTitle("Access token needed");
  return (
    <main id="main" className="token-screen">
      <div className="panel token-card">
        <h1>Access token needed</h1>
        <p>
          This dashboard needs its access token. Open the address <code>ssw dash</code> printed (it ends in{" "}
          <code>?token=...</code>).
        </p>
      </div>
    </main>
  );
}

function NotFound({ path }: { path: string }) {
  return (
    <>
      <PageHeader title="Page not found" />
      <p>
        There is no view at <code>{path}</code>.
      </p>
      <p>
        <Link to={HOME}>Go to the task board</Link>
      </p>
    </>
  );
}
