"""pytest plugin used by `ssw prove`: records one outcome per test node.

Loaded with `-p stepswitch.prove.pytest_plugin`; writes JSON to the path in
STEPSWITCH_PROVE_RESULTS. Outcomes: passed, failed, skipped, error (setup or teardown failure,
or a collection error in the test's module). Expected failures count as failed: a claim test
must pass outright.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

RESULTS_ENV = "STEPSWITCH_PROVE_RESULTS"

_outcomes: dict[str, str] = {}
_reasons: dict[str, str] = {}
_collect_errors: dict[str, str] = {}
_root: list[str] = []


def pytest_configure(config: pytest.Config) -> None:
    _root[:] = [str(config.rootpath)]


def _relative(text: str) -> str:
    """Reports are committed: no checkout paths (working rule 5)."""
    for root in _root:
        for variant in {root, root.replace("\\", "/")}:
            for sep in ("\\", "/"):
                text = text.replace(variant + sep, "")
            text = text.replace(variant, ".")
    return text


def _reason(report: Any) -> str:
    longrepr = getattr(report, "longrepr", None)
    if report.skipped and isinstance(longrepr, tuple) and len(longrepr) == 3:
        # (path, line, message) for skips: keep the message only.
        text = str(longrepr[2]).removeprefix("Skipped: ")
    else:
        text = str(getattr(report, "longreprtext", "") or "")
    text = _relative(text)
    return text.strip().splitlines()[-1][:500] if text.strip() else ""


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    node = report.nodeid
    if report.when == "call":
        if hasattr(report, "wasxfail"):
            outcome = "failed" if report.skipped else "passed"
        else:
            outcome = report.outcome
        _outcomes[node] = outcome
        if outcome != "passed":
            _reasons[node] = _reason(report)
    elif report.failed:
        _outcomes[node] = "error"
        _reasons[node] = _reason(report)
    elif report.when == "setup" and report.skipped:
        _outcomes[node] = "skipped"
        _reasons[node] = _reason(report)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        _collect_errors[report.nodeid] = _reason(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    target = os.environ.get(RESULTS_ENV)
    if not target:
        return
    payload = {
        "outcomes": _outcomes,
        "reasons": _reasons,
        "collect_errors": _collect_errors,
        "exitstatus": int(exitstatus),
    }
    Path(target).write_text(json.dumps(payload, indent=2), encoding="utf-8")
