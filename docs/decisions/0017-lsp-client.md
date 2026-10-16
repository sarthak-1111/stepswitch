# ADR 0017: A small built-in LSP client instead of pygls

- Status: accepted
- Date: 2026-10-16
- Phase: 2

## Context

SPEC §12 item 1 asks for a thin client on pygls and lsprotocol for the agent's `lsp` tool
(defs, refs, hover, diagnostics against user-installed language servers). Building the tool
showed three problems with pygls 2.1.1 as the base (checked 2026-10-16 in its `client.py`,
docs/RESEARCH.md "Verified during Phase 2"):

- `start_io` pipes the server's stderr but never reads it, so a server that logs a lot fills
  the pipe and blocks.
- `stop()` has no timeout, so a server that hangs on shutdown hangs the agent.
- The client is asyncio-only, while the agent loop and its tools are synchronous.

## Decision

`stepswitch.context.lsp` implements the small part of LSP 3.17 the tool needs, with the
standard library only: Content-Length framing over the server's stdio, a reader thread that
also drains stderr, `initialize`/`initialized`, `textDocument/didOpen`, definition, references
(with `includeDeclaration`), hover, published and pulled diagnostics, `workspace/configuration`
answered with nulls, and `shutdown`/`exit` with a timeout followed by a process-tree kill.
Positions are converted between the tool's 1-based lines and LSP's zero-based UTF-16 columns.
Servers are still user-installed and never downloaded; their launch commands come from
configuration or registry TOML (none ship yet). pygls and lsprotocol are not dependencies.

## Consequences

- No extra dependency, and no async bridge in the agent.
- The client covers only the requests above. Anything more (rename, code actions, semantic
  tokens) is new code against the 3.17 specification.
- Tests run against a fake language server (`tests/unit/fixtures/context/fake_lsp.py`); real
  servers are checked when their registry entries are added.
