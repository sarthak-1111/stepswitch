"""C-023: on the mini suites, Claude Code through `ssw proxy` on local models passes as many task
runs as Claude Code on Sonnet 5.5 and on Opus 5.5.

Proof: bench evidence on the mini suites (ADR 0034). The `harness:claude-code` contestant runs
Claude Code with every model request routed among local models by `ssw proxy`, inside
Stepswitch's gates, retries and review (ADR 0029); the baselines are the native contestants of
C-021. The comparison is C-021's (`tests.claims.frontier.parity`).
"""

from __future__ import annotations

import pytest

from tests.claims import evidence, frontier

pytestmark = [pytest.mark.claim("C-023")]


def test_claude_code_on_local_models_passes_as_many_task_runs() -> None:
    report, problems = frontier.parity(evidence.thresholds("C-023"))
    evidence.write("C-023", "comparison", report)
    assert not problems, problems
