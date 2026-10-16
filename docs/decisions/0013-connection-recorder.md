# ADR 0013: Connection recorder for the network claims

- Status: accepted
- Date: 2026-10-16
- Phase: 1

## Context

C-007 (no telemetry) and later C-006 (offline mode) need evidence of every connection and every
spawned process during a flow. ADR 0011 chose interpreter audit hooks as the in-process
recorder, with OS-level network isolation on Linux CI.

On the development workstation, installing any audit hook makes the next import from a cached
bytecode file fail ("'bytes' object has no attribute 'co_filename'"), with every interpreter on
the machine (3.10 to 3.14). The same code works in a clean Linux container. A behaviour-monitor
library that endpoint-security software injects into every process is loaded in the failing
interpreters; the hook machinery fails before the Python hook is even called. Security
software of this kind is common on managed machines, so the claim tests must not depend on audit
hooks working.

The Windows event loop also connects through an overlapped-I/O call that bypasses
`socket.connect`, so neither audit hooks nor wrappers of `socket.connect` see those connects.

## Decision

The recorder (`tests/claims/recorder.py`) has two modes:

- `audit`: interpreter audit hooks (socket connects and sends, name lookups, process spawns).
  Used wherever a canary interpreter can import with a hook installed.
- `patch`: the same events captured by wrapping the socket methods, the name-lookup functions,
  `subprocess.Popen` and the `os` spawn functions before any Stepswitch code is imported. Used
  only when the canary fails.

In both modes the Windows event loop's connect path is wrapped as well. The evidence states the
mode and, for `patch`, why audit hooks were unusable. On Linux the flow additionally runs in a
network namespace with loopback only, so traffic from native code and child processes is
blocked there regardless of the recorder.

## Consequences

- C-007 can be proven on hosts with endpoint-security software; the evidence is explicit about
  the weaker recorder.
- Neither mode sees traffic from child processes or native code outside Python's socket module;
  the evidence lists child processes, and the Linux run's isolation covers them.
