"""Tests of withdrawn claims are skipped, with the decision that withdrew them (ADR 0041).

A withdrawn claim keeps its tests, wording and evidence in the repository; it is no longer
proven. `ssw prove` never selects it, and a plain pytest run skips its tests here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stepswitch.prove import claims as claims_module

ROOT = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    withdrawn = claims_module.withdrawn(claims_module.load(ROOT))
    if not withdrawn:
        return
    for item in items:
        for marker in item.iter_markers("claim"):
            cid = marker.args[0] if marker.args else None
            if cid in withdrawn:
                item.add_marker(pytest.mark.skip(reason=f"{cid} is withdrawn: {withdrawn[cid]}"))
