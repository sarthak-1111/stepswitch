# ADR 0012: Build backend

- Status: accepted
- Date: 2026-10-16
- Phase: 1

## Context

ADR 0002 chose the `uv_build` backend, with hatchling as the fallback "if that becomes
awkward". Phase 1 showed two needs that `uv_build` does not cover:

- The model registry (`registry/*.toml`, SPEC §20) lives at the repository root, next to
  `claims/` and `bench/`, so that it is reviewed as data rather than code. The installed package
  must still ship it. `uv_build` packages only the module tree and has no way to map a
  directory from elsewhere in the repository into the package.
- The dashboard (Phase 4) is built by Vite into `dashboard/dist/` and must ship inside the
  package as `stepswitch/api/static/` (SPEC §21). The same mapping is needed.

## Decision

Use hatchling as the build backend. Its wheel target maps `registry/` to
`stepswitch/_registry/` (and, from Phase 4, `dashboard/dist/` to `stepswitch/api/static/`)
through `force-include`. In a source checkout, `stepswitch.paths.packaged_registry_dir()`
falls back to the repository's `registry/`, so editable installs and the wheel see the same
entries. The dashboard is still built before packaging (CI runs the Vite build, then
`uv build`), since neither backend runs Node.

## Consequences

- One build dependency (hatchling >= 1.27) instead of none beyond uv.
- `uv build` must run after the dashboard build from Phase 4 on; a missing `dashboard/dist`
  is a build error rather than a silently empty dashboard.
- ADR 0002's packaging row is superseded by this ADR.
