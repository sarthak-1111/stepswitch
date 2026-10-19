"""C-022: on the mini suites, Stepswitch uses fewer uncached tokens per task than Claude Code on
Sonnet 5.5 and on Opus 5.5.

Proof: bench evidence on the mini suites (ADR 0034), the runs of C-021. Uncached tokens are the
input tokens not served from a prompt cache plus the output tokens (ADR 0028). A native run's
counts are what its harness reports per model: uncached input, cache writes and output. For each
baseline the test takes the ratio of the router's uncached tokens to the baseline's over the task
runs they share; the claim holds when the router uses fewer and, with the `significant`
threshold, when the ratio's interval (a cluster bootstrap over tasks) lies below one. The
evidence also reports all tokens (cached included) and hosted tokens.
"""

from __future__ import annotations

import pytest

from tests.claims import evidence, frontier

pytestmark = [pytest.mark.claim("C-022")]


def test_local_router_uses_fewer_uncached_tokens_than_claude_code() -> None:
    report, problems = frontier.tokens_comparison(evidence.thresholds("C-022"))
    evidence.write(
        "C-022",
        "comparison",
        report,
        command="the runs of C-021; uv run ssw prove --claim C-022",
    )
    assert not problems, problems
