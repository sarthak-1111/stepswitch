# ADR 0011: Scopes of the platform and isolation claims

- Status: accepted
- Date: 2026-10-16
- Phase: 0

## Context

Scope wording is part of each claim (SPEC §18.5). Phase 0 research on CI runners and isolation
mechanisms (docs/RESEARCH.md, "Network isolation", and the testing details) found:

- On hosted CI, containers and OS-level network isolation are available on Linux runners only.
  macOS images have no container runtime, and Windows runners offer no Linux containers.
- Interpreter audit hooks see socket activity in the Python process but not in child processes
  or native code; the Python documentation says they are not a sandbox.
- Containers with no network have IPv4 loopback only.
- Hosted runner labels move. The `-latest` labels currently resolve to an Ubuntu 24.04 image, a
  macOS 26 arm64 image with 7 GB of RAM, and a Windows Server 2025 image.
- Several libraries that context services may use download files on first use, outside any
  egress choke point.

## Decision

The claim texts stay exactly as in SPEC §18.5. Their `scope` fields in `claims/claims.yaml`
become precise:

- **C-001**: Linux x86-64 container with `--memory=8g --memory-swap=8g` and no GPU devices;
  evidence records the cgroup memory limits, peak memory and the absence of GPU device nodes.
- **C-006**: "local" means 127.0.0.0/8, ::1 and Unix sockets. On Linux CI the full flow runs
  under OS-level isolation (no network) plus the in-process connection recorder. On macOS and
  Windows the evidence comes from the recorder only, and the evidence says so. Offline mode
  also refuses engine-side remote forwarding (cloud-backed model names, remote peers),
  libraries' lazy downloads, and harness egress that adapters can switch off.
- **C-007**: default configuration with no remote endpoints; evidence lists every connection
  and every spawned process; the Linux run is OS-isolated.
- **C-016**: CI runs on pinned runner labels for Linux x86-64, macOS arm64 and Windows x86-64;
  the prove report records the runner label and image version.

## Consequences

- Claim tests for C-001 and the OS-enforced part of C-006 run only on Linux; on other systems
  they report "not applicable here" rather than passing. `ssw prove` shows the platform each
  claim was proven on.
- C-016 requires a hosted CI setup on a code host, which is a maintainer action (creating the
  remote repository); until then only the local platform's part can be shown.
