# ADR 0031: Zero-egress enforcement: offline mode, audit, redaction and deny lists

- Status: accepted
- Date: 2026-10-19
- Phase: 9

## Context

SPEC §15 asks for these privacy controls:

- an offline mode that reaches only local addresses;
- an audit log of all egress;
- a secret scanner and redactor before any remote call;
- per-path deny lists;
- downloads only in explicit commands.

C-006 states: "In offline mode, makes no network connections except to local endpoints." It
must be proven under a socket-level guard plus OS-level network isolation on Linux CI, with the
audit log matching the observed connections exactly (ADR 0011).

Up to Phase 8, offline mode was a process-wide switch, and routing skipped remote workers.
Several gaps remained:

- A science task or an offline proxy run in an online process was offline only in routing.
- A local endpoint could still forward to a hosted service, for example an Ollama cloud-backed
  model or a llama-swap peer.
- Audit rows had no response sizes for streams, and spawned processes were invisible.
- External harnesses did not apply `privacy.deny_paths`.

Research checked on 2026-10-19 (docs/RESEARCH.md, "Verified during Phase 9") covered how
Ollama and llama-swap forward to remote hosts, and the deny-rule syntax of each harness.

## Decision

1. **Offline mode per task, enforced at the transport.**
   - Every task and every proxy run gets a child of the process's egress layer
     (`Egress.child`). A child's policy starts as its parent's and can only become stricter.
   - The task's model clients, context services and gates use the child. When `--offline`,
     `privacy.mode = "local"` or the track's policy (the science track is offline by default)
     turns offline mode on, non-local requests are refused before a connection is opened.
   - `ssw run --offline` and an offline `ssw proxy` also switch the whole process to offline,
     so nothing outside the task's clients can connect either.
2. **Child processes.**
   - Commands run by the agent get `minimal_env` with the offline switches of common tools
     (`UV_OFFLINE`, `PIP_NO_INDEX`, `HF_HUB_OFFLINE`, `CARGO_NET_OFFLINE`, `GOPROXY=off`,
     `npm_config_offline`).
   - Harness adapters set their own egress switches on every run (ADR 0029).
   - These switches are best effort. A child process's connections are outside the process's
     transport, so C-006's Linux evidence comes from a network namespace.
3. **Local endpoints that forward count as remote.**
   - A local worker counts as remote when its endpoint can forward the model elsewhere:
     - an Ollama model with a `:cloud` or `-cloud` tag;
     - a model that `/api/tags` lists with `remote_host` or `remote_model`;
     - a llama-swap model reached through `peers`, or whose `proxy` URL is not local, read
       from the endpoint's `engine_config`.
   - A llama-swap endpoint without its config file counts as remote, because it cannot be
     checked.
   - Offline routing skips these workers and the redactor treats them as remote.
   - Offline, the client asks Ollama again before the first request for each model. A model
     that has become cloud-backed since the catalog was built is refused with `blocked`.
4. **Audit.**
   - Every request through `stepswitch.net`, allowed or refused, gets an `egress_audit` row:
     host, port, purpose, task ID, status, and bytes sent and received. Streams are counted
     when the body closes.
   - Every process started through `subprocess` gets a `process_audit` row: program, a
     redacted and truncated command line, working directory, pid, task ID and offline flag.
   - `ssw audit` shows both (`--processes`, `--task`, `--blocked`, `--json`).
5. **Redaction.**
   - Requests to non-local endpoints have secrets replaced by `[REDACTED:<kind>]`
     (`privacy.redact`: `remote` by default, `all` or `off`). Redaction happens in
     `ModelClient.prepare`, and the conversion report notes it.
   - Known values are always replaced: configured endpoint keys, and credential-named
     environment variables of at least 8 characters.
   - High-signal formats are replaced: private keys, repository, package, chat and hub tokens,
     cloud key IDs, JWTs, bearer and basic credentials, and passwords in URLs.
   - Ambiguous prefixes (`sk-`) and `name = value` assignments count only when the value looks
     random (entropy and character classes). Basic credentials must decode to a printable
     `user:password`.
   - The same redactor cleans command lines in the process audit.
6. **One deny list for every harness.** `privacy.deny_paths` (gitignore-style patterns) is one
   `DenyList`, applied:
   - by the built-in tools;
   - by the ACP client, in its file methods and in permission requests, whatever the run's
     permission mode;
   - by Claude Code, as `Read(...)` and `Edit(...)` entries in `--disallowedTools`;
   - by OpenCode, as `read` and `edit` permission maps;
   - by the Pi and Oh My Pi extensions, as a `tool_call` hook that blocks calls naming a denied
     path. The rules travel as regular expressions in `STEPSWITCH_DENY_RULES`, and a test checks
     that the JavaScript matcher agrees with the Python one.
7. **Downloads only when asked.**
   - `ssw prefetch` downloads the registry's tokenizer files through `stepswitch.net`. It also
     checks that the tree-sitter grammars load from their wheels and that the configured
     language servers and ripgrep are found.
   - `--check` never connects. A test runs the agent tools, token counting and the prefetch
     check with every connection and name lookup refused in the process.
8. **C-006's proof.**
   - The flow runs as one process tree under the recorder with its guard on: `ssw init`, a
     scripted local model next to a hosted endpoint, `ssw run --offline`, then `ssw audit`.
   - The recorder records connects, datagram sends, binds, name lookups and process spawns.
     It refuses every non-local connect, send and lookup.
   - On Linux the tree runs in a network namespace that has only loopback
     (`unshare --net --map-root-user`). The CI job allows unprivileged user namespaces for it.
   - "The audit log matches exactly" means that two sets of destinations (host and port) are
     equal: the ones named by the task's audit rows, and the ones the `ssw run` process
     connected to, leaving out sockets it bound itself. Requests share connections, so the
     comparison is per destination.

## Consequences

- **What the evidence covers.** On macOS and Windows, C-006's evidence comes from the
  in-process recorder only, and says so (ADR 0011). On hosts whose security software breaks
  interpreter audit hooks, the recorder wraps the socket functions instead, and the evidence
  names the mode.
- **Redaction can miss secrets.** A secret that is neither a known value nor a recognised
  format, such as a short, low-entropy password in prose, is sent as is. The deny list and the
  local-only modes are the controls for data that must never leave.
- **Unknown forwarding is not caught.** A local server that forwards without saying so is
  invisible to these checks. Only OS-level isolation catches that.
