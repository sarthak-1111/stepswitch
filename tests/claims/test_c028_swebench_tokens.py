"""C-028: on SWE-bench Verified Mini, Stepswitch uses fewer tokens per instance than
mini-SWE-agent with the same local model, counting cached tokens.

Proof: the runs of C-026 (`tests.claims.swebench`). The measure is input tokens with cached
tokens included, plus output tokens, as each contestant's model server reported them. The claim
holds when Stepswitch used fewer over the compared instances and, with the `significant`
threshold, when the 95% cluster bootstrap interval of the percentage over instances lies above
zero. Uncached tokens are reported beside it.
"""

from __future__ import annotations

import pytest

from tests.claims import evidence, swebench

pytestmark = [pytest.mark.claim("C-028")]


def test_stepswitch_uses_fewer_tokens_per_instance_than_mini_swe_agent() -> None:
    limits = evidence.thresholds("C-028")
    comp = swebench.load(limits)
    data = swebench.report(comp)
    if not comp.problems:
        claimed = data["tokens"][str(limits["measure"])]
        if claimed["percent"] is None or claimed["percent"] <= 0:
            comp.problems.append(f"{claimed['percent']}% fewer {limits['measure']} tokens")
        elif limits.get("significant") and claimed["interval"]["low"] <= 0:
            comp.problems.append(
                f"{claimed['percent']}% fewer {limits['measure']} tokens, but the interval"
                f" ({claimed['interval']['low']}% to {claimed['interval']['high']}%) does not"
                " lie above zero"
            )
    data["problems"] = comp.problems
    evidence.write("C-028", "swebench", data)
    assert not comp.problems, comp.problems
