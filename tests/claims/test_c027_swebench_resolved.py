"""C-027: on SWE-bench Verified Mini, Stepswitch resolves at least as many instances as
mini-SWE-agent with the same local model.

Proof: the runs of C-026 (`tests.claims.swebench`), paired over the instances both are compared
on. The claim holds when Stepswitch's resolved count is at least mini-SWE-agent's. The evidence
also gives the discordant pairs, an exact McNemar test and the difference's interval, which the
claim does not rest on.
"""

from __future__ import annotations

import pytest

from tests.claims import evidence, swebench

pytestmark = [pytest.mark.claim("C-027")]


def test_stepswitch_resolves_at_least_as_many_instances_as_mini_swe_agent() -> None:
    comp = swebench.load(evidence.thresholds("C-027"))
    data = swebench.report(comp)
    if not comp.problems:
        ours = data["results"][swebench.OURS]["resolved"]
        theirs = data["results"][swebench.THEIRS]["resolved"]
        if ours < theirs:
            comp.problems.append(f"Stepswitch resolved {ours} instances, mini-SWE-agent {theirs}")
    data["problems"] = comp.problems
    evidence.write("C-027", "swebench", data)
    assert not comp.problems, comp.problems
