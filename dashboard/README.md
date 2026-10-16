# Stepswitch dashboard

The web dashboard that `ssw dash` serves: React, Vite and TypeScript, built to static files in
`dist/`. It talks only to the same-origin `/api/` routes described in `docs/API.md`, loads nothing
from other hosts and sends no telemetry.

## Install and build

Needs Node.js 22.12 or newer.

```sh
cd dashboard
npm ci              # exact versions from package-lock.json
npm run build       # type check, then write dist/
npm run typecheck   # type check only
```

`dist/` and `node_modules/` are not committed.

## Serving

The API server serves `dist/` as static files and answers every other path outside `/api/` with
`dist/index.html`, so deep links such as `/tasks/<id>` and `/trajectories/<id>` load the app,
which then picks the view from the address. `/` opens the task board.

The access token lives in the HttpOnly cookie the server sets when you open the address
`ssw dash` printed (it ends in `?token=...`). The page never reads, stores or sends the token
itself; when the API answers 401 it asks you to open that address again. The built `index.html`
carries a Content-Security-Policy that allows resources from its own origin only.

## Development

```sh
npm run dev       # http://127.0.0.1:5173, hot reload
npm run preview   # serves the built dist/ on http://127.0.0.1:4173
```

Both forward `/api/` to `SSW_DASH_BACKEND` (default `http://127.0.0.1:7777`). With a backend
that needs a token, open its `?token=...` address once in the same browser on the same host
first: the cookie is then sent to the dev server too, which passes it on.

## Mock API

`dev/mock-server.mjs` answers every `/api/` route from the fixtures in `dev/fixtures/` and serves
`dist/`, so the dashboard can be worked on without the backend. Plain Node, no dependencies,
loopback only, not part of the build.

```sh
npm run build
npm run mock -- --port 7777   # then open http://127.0.0.1:7777/ or run npm run dev
```

| Option | Effect |
| --- | --- |
| `--port N` | Port to listen on (default 7777). |
| `--token VALUE` | Require the token like `ssw dash`: open `/?token=VALUE` once to set the cookie. |
| `--no-live` | Do not send the sample live events on `/api/events`. |
| `--close-stream` | End each event stream after its first events, for headless render checks. |

The live stream sends one event of each type (`task`, `trajectory`, `ledger`, `decision`), and
starting or stopping an installed model is simulated.
