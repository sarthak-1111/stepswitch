## What and why

<!-- What this changes and why. Link the issue, SPEC section or ADR it relates to. -->

## How it was tested

<!-- The commands you ran, for example `uv run pytest tests/unit/test_router.py -q`. -->

## Checklist

- [ ] Tests cover the change, and the narrowest relevant ones pass.
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy src` pass.
- [ ] `uv run pytest tests/unit/test_repo_hygiene.py` passes (no company names, nothing machine-specific).
- [ ] New external facts are recorded in `docs/RESEARCH.md` with the date checked.
- [ ] Public claims change only through `claims/claims.yaml` and their claim tests.
- [ ] Commits follow the conventional commit format.
