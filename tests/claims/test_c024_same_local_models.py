"""C-024: on the mini suites, on the same local models, Stepswitch uses fewer uncached tokens per
task than Claude Code through `ssw proxy`, and passes as many task runs.

Proof: bench evidence on the mini suites (ADR 0037). Both contestants run on the bench's router
pool of local models: `router` is Stepswitch's own agent, and `harness:claude-code` is Claude
Code with every model request sent through `ssw proxy` to that pool. So the models are the
same and the agents differ. `tests.claims.frontier` reads the newest run of each contestant per
suite and compares the task runs they share. The claim holds when:

- the router passes at least as many of them;
- its uncached tokens per task are fewer, with the ratio's 95% interval (a cluster bootstrap
  over tasks) below one;
- it sent no token to a hosted model;
- every compared run passed the bench's canary check;
- both contestants' runs used the same router pool.

The claim was registered before its confirmatory run. The canary check makes runs from before
it ineligible.
"""

from __future__ import annotations

import pytest

from tests.claims import evidence, frontier

pytestmark = [pytest.mark.claim("C-024")]


def test_router_uses_fewer_uncached_tokens_than_claude_code_on_the_same_models() -> None:
    limits = evidence.thresholds("C-024")
    parity, parity_problems = frontier.parity(limits)
    tokens, token_problems = frontier.tokens_comparison(limits)
    evidence.write("C-024", "parity", parity)
    evidence.write("C-024", "comparison", tokens)
    problems = sorted(set(parity_problems) | set(token_problems))
    assert not problems, problems
