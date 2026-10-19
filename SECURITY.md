# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately, never in a public issue, discussion or pull request.

1. Use the repository's private vulnerability reporting: the **Report a vulnerability** button
   on its **Security** tab.
2. Describe the problem, how to reproduce it, the version (`ssw --version`), the operating
   system, and the impact you expect.
3. Leave out real keys, tokens and private data. If you need to show them, use placeholders.

The maintainers will confirm that they received the report and keep you posted as they work
on it. A fix is released before the details are published, together with an advisory that
credits you unless you prefer otherwise.

## Supported versions

Fixes go into the latest release. Stepswitch is pre-1.0, so older minor versions get no
backports.

## What counts

Stepswitch runs models and coding agents on your machine, so these are in scope:

- **Network services.** `ssw dash`, `ssw proxy` and the model servers Stepswitch starts bind
  to 127.0.0.1 and require a token or key. Any way to reach them from another machine, or
  without the token, counts.
- **Egress controls.** Any connection that bypasses offline mode, the egress audit log
  (`ssw audit`), the secret redactor or the per-path deny list (`privacy.deny_paths`) counts.
- **Credentials.** So does any way the proxy, an adapter or a log leaks an API key, a token or
  an inbound credential header, or hands the user's own credentials to a harness.
- **Isolation.** A command or a harness that escapes its worktree or its per-run state
  directory counts.
- **Supply chain.** Unpinned or unverified downloads (engines, model files, tokenizer files)
  and dependency problems in `uv.lock` count.

Out of scope:

- what a model writes, and whether it is correct;
- vulnerabilities in third-party engines, harnesses or models themselves (please report those
  upstream);
- problems that need an attacker who already controls your user account.

## Handling secrets

- Keep endpoint keys in `.stepswitch/local.toml`, which is gitignored, or in environment
  variables. Never put them in `stepswitch.toml` or in a commit.
- Requests to non-local endpoints are redacted by default (`privacy.redact = "remote"`). Run
  `--offline` or set `privacy.mode = "local"` for work that must not leave the machine.
