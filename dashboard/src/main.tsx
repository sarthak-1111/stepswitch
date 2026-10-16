import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { HOME } from "./routes";
import "./styles.css";

// "/" opens the task board. The query string is dropped on purpose: the server consumes the
// one-time ?token=... itself, and the dashboard never keeps it in its history.
if (window.location.pathname === "/" || window.location.pathname === "/index.html") {
  window.history.replaceState(null, "", HOME + window.location.hash);
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
