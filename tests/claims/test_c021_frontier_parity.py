"""C-021: on the mini suites, Stepswitch on local models passes as many task runs as Claude Code
on Sonnet 5.5 and on Opus 5.5.

Proof: bench evidence on the mini suites (ADR 0034). The `router` contestant routes among local
models only; each baseline is `native:claude-code:<model>`, Claude Code alone on its own login,
one session per task run. `tests.claims.frontier` reads the newest run of each contestant per
suite and compares passes over the task runs they share, both suites together. The claim holds
when the router passes at least as many as each baseline, every baseline run used the model the
claim names, the router sent no token to a hosted model, and the runs cover every task of both
suites with the seeds the thresholds ask for.
"""

from __future__ import annotations

import pytest

from tests.claims import evidence, frontier

pytestmark = [pytest.mark.claim("C-021")]


def test_local_router_passes_as_many_task_runs_as_claude_code() -> None:
    report, problems = frontier.parity(evidence.thresholds("C-021"))
    evidence.write("C-021", "comparison", report)
    assert not problems, problems
