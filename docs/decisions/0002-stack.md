# ADR 0002: Stack and minimum versions

- Status: accepted
- Date: 2026-10-16
- Phase: 0

## Context

The working rules fix the stack: Python >= 3.11, uv, Typer, FastAPI, Pydantic v2, SQLite in WAL
mode, httpx, and a React + Vite + TypeScript dashboard served as static assets. Phase 0 checked
current versions and found constraints the SPEC does not mention (docs/RESEARCH.md, "Stack"):

- Python 3.15 was released on 2026-10-09, and uv 0.13 now downloads 3.15 by default. Not every
  dependency ships 3.15 wheels yet (the C-accelerated YAML library does not).
- SQLite releases before 3.51.3 have a WAL-reset corruption bug. Interpreters that bundle an
  older SQLite (common for older installer builds) are affected, while uv-managed Python builds
  bundle a fixed version.
- FastAPI has built-in server-sent events since 0.135, so a separate SSE package is not needed.
- httpx has had no stable release in 22 months while a redesigned 1.0 is in development.
- Vite 8 needs Node ^20.19 or >= 22.12. Node 20 reached end of life on 2026-04-30.
- TypeScript 7 is a native compiler with no programmatic API yet.
- The documentation theme named in SPEC §24 has been in maintenance mode since 2025-11 and is
  incompatible with its host tool's 2.0 release.
- The plain YAML loader drops comments on a round trip; `sy prove` rewrites claim statuses in a
  hand-commented file.

## Decision

| Area | Choice |
|---|---|
| Python | `requires-python = ">=3.11"`; CI matrix 3.11 to 3.15; development on a **uv-managed** interpreter (`python-preference = "only-managed"`), currently 3.12 |
| Packaging | uv with dependency groups; build backend `uv_build`; the dashboard is built before packaging into the package directory, because `uv_build` runs no build scripts (hatchling is the fallback if that becomes awkward) |
| CLI | `typer>=0.26` |
| API and SSE | `fastapi>=0.135` with its built-in SSE; `uvicorn>=0.54` without extras |
| Models | `pydantic>=2.14,<3` |
| HTTP | `httpx>=0.28.1,<1`, used only inside the egress module, so the client library can be swapped without touching callers |
| YAML | `ruamel.yaml>=0.19.1` (round-trip mode keeps comments in `claims.yaml`) |
| JSON Schema | `jsonschema>=4.26` (Draft 2020-12) for HANDOFF and probe outputs |
| State | SQLite in WAL mode on local disks only; `sy doctor` fails when `sqlite3.sqlite_version` is below 3.51.3 (or the 3.50.7 / 3.44.6 backports) and falls back to `journal_mode=DELETE` on network volumes |
| Quality | `ruff>=0.17`, `mypy>=2.4` (blocking), `pytest>=9.1`; the beta Rust type checker only as an optional, non-blocking CI job |
| Dashboard | Node >= 22.12 (CI and development on the current LTS), `react@^19.3`, `vite@^8.3`, `@vitejs/plugin-react@^6.1`, `typescript@^7.0` for type checking |
| Harness extensions | TypeScript; code or lint tooling that imports the compiler API pins TypeScript 6 until TypeScript 7 ships one |
| Docs site | decided in Phase 10 by its own ADR (maintenance-mode theme with the host tool pinned below 2.0, or its alpha successor) |

## Consequences

- The development environment uses a downloaded uv-managed interpreter, not a system Python.
- The workstation's Node 20 must be upgraded before Phase 4 (dashboard); this is an environment
  prerequisite recorded in PROGRESS.md.
- Contributors need uv >= 0.13 for the same defaults as CI.
- Phase 0 test tooling switches from the plain YAML loader to `ruamel.yaml` so that tests and the
  future `sy prove` read `claims.yaml` the same way.
