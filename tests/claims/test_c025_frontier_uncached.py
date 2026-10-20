"""C-025: on the mini suites, Stepswitch on local models uses fewer uncached tokens per task than
Claude Code on Sonnet 5.5 and on Opus 5.5.

Proof: bench evidence on the mini suites, the task runs of C-021. Uncached tokens are the input
tokens a server did not serve from its prompt cache, plus the output tokens (ADR 0028). A native
run's counts are what its harness reports per model (ADR 0034). For each baseline the test takes
the ratio of the router's uncached tokens to the baseline's over the task runs they share. The
claim holds when that ratio's 95% interval (a cluster bootstrap over tasks) lies below one, and
when the router sent no token to a hosted model.

C-022 was registered on this measure first and failed on the Phase 11 runs (ADR 0036). This claim
was registered before the router's confirmatory run of Phase 12 (ADR 0040), against the native
runs of Phase 11.
"""

from __future__ import annotations

import pytest

from tests.claims import evidence, frontier

pytestmark = [pytest.mark.claim("C-025")]


def test_local_router_uses_fewer_uncached_tokens_than_claude_code() -> None:
    report, problems = frontier.tokens_comparison(evidence.thresholds("C-025"))
    evidence.write("C-025", "comparison", report)
    assert not problems, problems
