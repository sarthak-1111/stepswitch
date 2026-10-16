"""C-016: runs on macOS, Linux and Windows.

Proof (SPEC §18.5): `ssw prove --quick` passes in CI on all three. Scope (ADR 0011): pinned
hosted runner labels for Linux x86-64, macOS arm64 and Windows x86-64; the prove report records
each runner label and image version.

The CI workflow (.github/workflows/prove.yml) runs `ssw prove --quick --skip-claim C-016` on
each platform and uploads the reports; its aggregation job runs scripts/platform_evidence.py,
which writes claims/evidence/C-016.json. This test validates that evidence against the current
sources, so it passes only for code that CI has actually run on all three platforms. Without
evidence for the current sources the claim stays pending (the test skips); evidence for the
current sources that shows a failed or missing platform fails it (ADR 0014).
"""

from __future__ import annotations

import pytest

from stepswitch.prove.digest import source_digest
from tests.claims import evidence

pytestmark = [pytest.mark.claim("C-016")]

ARCH_BY_OS = {"linux": "x86-64", "macos": "arm64", "windows": "x86-64"}
ARCH_NAMES = {"x86_64": "x86-64", "amd64": "x86-64", "x86-64": "x86-64", "arm64": "arm64"}


def test_prove_quick_passed_on_linux_macos_windows() -> None:
    limits = evidence.thresholds("C-016")
    data = evidence.read("C-016")
    if data is None:
        pytest.skip(
            "pending: no platform evidence yet; the CI workflow writes "
            "claims/evidence/C-016.json after `ssw prove --quick` ran on all three platforms"
        )
    digest = source_digest(evidence.ROOT)
    if data["source_digest"] != digest:
        pytest.skip(
            "pending: the platform evidence was produced from other sources; run the CI "
            "workflow on this commit (ADR 0014)"
        )
    runs = data["sections"]["platforms"]["runs"]
    by_os = {run["os"]: run for run in runs}
    problems = []
    for os_name in limits["operating_systems"]:
        run = by_os.get(os_name)
        if run is None:
            problems.append(f"{os_name}: no run")
            continue
        arch = ARCH_NAMES.get(str(run.get("machine", "")).lower(), run.get("machine"))
        checks = {
            "mode is quick": run.get("mode") == "quick",
            "report found": run.get("report_found") is True,
            "no failing claims": run.get("failing") == 0,
            "same sources": run.get("source_digest") == digest,
            f"architecture {ARCH_BY_OS[os_name]}": arch == ARCH_BY_OS[os_name],
            "runner label recorded": bool(run.get("runner_label")),
            "image version recorded": bool(run.get("image_version")),
        }
        problems += [f"{os_name}: {name}" for name, ok in checks.items() if not ok]
    assert not problems, problems
