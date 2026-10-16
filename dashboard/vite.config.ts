import { defineConfig, type Plugin, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

// Where `npm run dev` and `npm run preview` send /api/ requests: `ssw dash` (default port 7777)
// or the mock server in dev/. Override with SSW_DASH_BACKEND=http://127.0.0.1:<port>.
const backend = process.env.SSW_DASH_BACKEND ?? "http://127.0.0.1:7777";
const DEV_PORT = 5173;
const PREVIEW_PORT = 4173;

// The built page may load scripts, styles, images and data from its own origin only (zero
// telemetry: no CDN, web font, remote image or analytics). Added at build time only, because
// the dev server injects an inline script for hot reloading.
const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
].join("; ");

function contentSecurityPolicy(): Plugin {
  return {
    name: "stepswitch-content-security-policy",
    apply: "build",
    transformIndexHtml() {
      return [
        {
          tag: "meta",
          attrs: { "http-equiv": "Content-Security-Policy", content: CONTENT_SECURITY_POLICY },
          injectTo: "head-prepend",
        },
      ];
    },
  };
}

// The backend refuses POST requests whose Origin names another site. Requests from this dev or
// preview server are rewritten to the backend's own origin; any other Origin passes unchanged,
// so the backend still refuses foreign pages.
function apiProxy(port: number): Record<string, ProxyOptions> {
  const local = new Set([`http://127.0.0.1:${port}`, `http://localhost:${port}`]);
  const target = new URL(backend).origin;
  return {
    "/api": {
      target,
      changeOrigin: true,
      configure(proxy) {
        proxy.on("proxyReq", (proxyReq, req) => {
          const origin = req.headers.origin;
          if (typeof origin === "string" && local.has(origin)) proxyReq.setHeader("origin", target);
        });
      },
    },
  };
}

export default defineConfig({
  base: "/",
  plugins: [react(), contentSecurityPolicy()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    host: "127.0.0.1",
    port: DEV_PORT,
    strictPort: true,
    proxy: apiProxy(DEV_PORT),
  },
  preview: {
    host: "127.0.0.1",
    port: PREVIEW_PORT,
    strictPort: true,
    proxy: apiProxy(PREVIEW_PORT),
  },
});
