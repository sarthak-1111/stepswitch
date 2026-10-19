"""C-022: on the mini suites, Stepswitch processes fewer tokens per task than Claude Code on
Sonnet 5.5 and on Opus 5.5, counting cached tokens, and sends none to a hosted model.

Proof: bench evidence on the mini suites (ADR 0034), the runs of C-021. Tokens are input tokens
with cached tokens included, plus output tokens. A native run's counts are what its harness
reports per model. For each baseline the test takes the ratio of the router's tokens to the
baseline's over the task runs they share. The claim holds when the router uses fewer and, with
the `significant` threshold, when the ratio's interval (a cluster bootstrap over tasks) lies
below one, and when the router sent no token to a hosted model.

The claim was registered on uncached tokens and failed there (ADR 0036). The test reports that
measure too, with how many percent more uncached tokens the router used, which the README shows
beside the claim.
"""

from __future__ import annotations

import pytest

from tests.claims import evidence, frontier

pytestmark = [pytest.mark.claim("C-022")]


def test_local_router_processes_fewer_tokens_than_claude_code() -> None:
    report, problems = frontier.tokens_comparison(evidence.thresholds("C-022"))
    evidence.write(
        "C-022",
        "comparison",
        report,
        command="the runs of C-021; uv run ssw prove --claim C-022",
    )
    assert not problems, problems
