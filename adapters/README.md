# Extensions for TypeScript harnesses

These are Stepswitch's extensions for the harnesses that `ssw run --harness` drives in-process
(SPEC §12 items 4-5, ADR 0029). They are plain ES modules with JSDoc types, so both hosts load
them without a build step.

| Package | Host | What it adds |
|---|---|---|
| `pi/` | Pi | The virtual model `stepswitch/routed`. Before every request its `route()` asks the run's `ssw proxy` which worker takes it (`POST /stepswitch/v1/route`) and resolves to that worker's model. |
| `omp/` | Oh My Pi | A `before_subagent_spawn` hook that gives every subagent the slot of its role. |
| `core/` | both | Connecting to the run's proxy, the route call and the shared helpers. |

The Python adapters (`stepswitch.workers.pi` and `stepswitch.workers.omp`) write the provider
configuration and load these files from a source checkout:

- Pi: `--no-extensions -e adapters/pi/index.js`.
- Oh My Pi: `--trusted-extension adapters/omp/index.js`.

Each extension reads the run's proxy from `STEPSWITCH_PROXY_URL`, `STEPSWITCH_PROXY_TOKEN` and
`STEPSWITCH_SESSION`. Without them it registers nothing. The packages import `../core`, so they
are not published on their own.

Tests (Node 18 or later, no dependencies):

```sh
cd adapters && node --test test/extensions.test.js
```

`tests/unit/test_adapter_extensions.py` runs these tests too. It also runs the Pi extension
against a real `ssw proxy`.
