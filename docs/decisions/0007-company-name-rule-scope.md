# ADR 0007: Scope and enforcement of the no-company-names rule

- Status: accepted
- Date: 2026-10-16
- Phase: 0

## Context

Working rule 4 forbids company names anywhere: code, docs, comments, commit messages,
examples and test data. API formats are described generically ("chat-completions",
"responses", "messages", "generate-content"). Phase 0 also requires research with links to
current specifications, and those URLs, repository paths and model-hub IDs contain
organization names. A few tools carry a company name in their executable or package name, and
several model families share a name with the organization that publishes them.

## Decision

1. Prose uses product, project, model and format names only.
2. URLs and `org/name` identifiers (repository paths, model-hub IDs) are identifiers, not prose,
   and are allowed. In prose, a hub ID appears in code formatting.
3. A short allowlist of interoperability identifiers that contain a company name is kept in
   `tests/unit/test_repo_hygiene.py`: tool, package, protocol header, environment variable,
   engine parser, configuration value and license names that other software requires verbatim
   (for example the GPU management CLI, or the version header of the messages format). Bare
   company names are never allowlisted; prose describes them instead ("the publisher-named
   parser"). When code first needs a bare company name as a value, the way to hold it is
   decided then, in this ADR.
4. A name that is also a model-family or product name is allowed when used as that name.
5. Enforcement: `tests/unit/test_repo_hygiene.py` holds SHA-256 hashes of lowercase company
   names that are not also product names, and scans every tracked or committable text file for
   matching single words and word pairs, after removing URLs and whole `org/name`
   identifiers. Storing
   hashes keeps the denylist itself free of names. To add a name, hash its lowercase form in a
   scratch shell and append the digest.
6. Commit messages follow the same rule. Automatic co-author trailers that carry a company
   e-mail domain are left out of commits in this repository.

## Consequences

- Names that are both a company and a model family cannot be machine-checked; review catches
  those.
- Common words that happen to be company names (a fruit, for example) are flagged in test
  data; pick other words.
- Research and docs stay citable: every source keeps its real URL.
