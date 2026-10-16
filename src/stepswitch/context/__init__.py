"""Context services for the built-in agent (SPEC §12 item 1, §13; ARCHITECTURE §8).

* `repomap`: a ranked, token-budgeted outline of a repository (TK2; the Phase 2 version
  extracts definitions with `ast` and regexes, tree-sitter arrives in Phase 5).
* `search`: `grep` (ripgrep when installed, a Python fallback otherwise) and keyword or hybrid
  semantic search (TK9).
* `edits`: Stepswitch's hash-anchored line edit format (`spec/edit-format.md`, TK5).
* `lsp`: a thin client for language servers that the user installed.
* `walk` and `proc`: the shared file-walking rules and subprocess helpers.

Nothing here downloads anything or opens a network connection. Embeddings for semantic search
come from `stepswitch.compat.embeddings`, which goes through `stepswitch.net`. Submodules are
imported on demand, so importing this package stays cheap.
"""
