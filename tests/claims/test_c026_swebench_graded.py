"""C-026: on SWE-bench Verified Mini, Stepswitch and mini-SWE-agent each ran once on the same
local open-weight model, graded by the official SWE-bench harness, and sent no token to a hosted
model.

Proof: the gold check and the one confirmatory run under bench/results (ADR 0042), read by
`tests.claims.swebench`: every instance of the suite has a graded run of each contestant, apart
from listed ones whose reference patch did not resolve on the machine or whose run hit an
infrastructure error, and no token went to a hosted model. The evidence holds what the README's
SWE-bench table shows: each contestant's resolve rate and means per instance.
"""

from __future__ import annotations

import pytest

from tests.claims import evidence, swebench

pytestmark = [pytest.mark.claim("C-026")]


def test_swebench_results_come_from_one_graded_run_of_each_contestant() -> None:
    comp = swebench.load(evidence.thresholds("C-026"))
    evidence.write("C-026", "swebench", swebench.report(comp))
    assert not comp.problems, comp.problems
