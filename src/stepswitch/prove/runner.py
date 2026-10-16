"""`ssw prove`: run claim tests, derive claim statuses, write the report (SPEC §18.4, ADR 0006).

Selection: `--claim` picks claims explicitly; `--phase N` picks every claim part due by phase N
(SPEC §23), and then a missing test counts as an error; with neither, every claim whose tests
exist is evaluated and claims without tests yet stay `pending`.

Status per claim: `failing` if any evaluated test failed or errored; `passing` if every test of
every part passed; otherwise `pending` (parts not due yet, or skipped as not applicable on this
platform). Definitive results (passing, failing) are written back to claims.yaml; a pending
result never overwrites an earlier definitive one, since "not applicable here" says nothing
about other platforms. The README claims section is regenerated afterwards.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stepswitch.prove import claims as claims_module
from stepswitch.prove import render_readme
from stepswitch.prove.digest import source_digest
from stepswitch.prove.pytest_plugin import RESULTS_ENV

MODE_ENV = "STEPSWITCH_PROVE_MODE"
REPORT_JSON = "claims/prove-report.json"
REPORT_MD = "claims/prove-report.md"
FAILED = {"failed", "error"}


def platform_info() -> dict[str, Any]:
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    if system.startswith("linux"):
        system = "linux"
    return {
        "os": system,
        "machine": platform.machine().lower(),
        "release": platform.release(),
        "python": platform.python_version(),
        "ci": bool(os.environ.get("CI")),
        "runner_label": os.environ.get("STEPSWITCH_RUNNER_LABEL"),
        "image_os": os.environ.get("ImageOS"),  # noqa: SIM112 - set by hosted runner images
        "image_version": os.environ.get("ImageVersion"),  # noqa: SIM112
    }


def select(
    registry: dict[str, Any], *, phase: int | None, claims: Sequence[str], skip: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Claim id -> {"parts": due parts, "all_due": bool} for the selected claims."""
    known = {claim["id"]: claim for claim in registry["claims"]}
    unknown = [cid for cid in [*claims, *skip] if cid not in known]
    if unknown:
        raise ValueError(f"unknown claim id(s): {', '.join(unknown)}")
    selected: dict[str, dict[str, Any]] = {}
    for cid, claim in known.items():
        if cid in skip or (claims and cid not in claims):
            continue
        parts = claims_module.parts(claim)
        due = parts if claims or phase is None else [p for p in parts if p["phase"] <= phase]
        if due:
            selected[cid] = {"parts": due, "all_due": len(due) == len(parts)}
    return selected


def _run_pytest(root: Path, nodes: list[str], mode: str) -> dict[str, Any]:
    if not nodes:
        return {"outcomes": {}, "reasons": {}, "collect_errors": {}, "exitstatus": 0}
    with tempfile.TemporaryDirectory(prefix="ssw-prove-") as tmp:
        results = Path(tmp) / "results.json"
        env = {**os.environ, RESULTS_ENV: str(results), MODE_ENV: mode, "PYTHONUTF8": "1"}
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rs",
            "-p",
            "no:cacheprovider",
            "-p",
            "stepswitch.prove.pytest_plugin",
            *nodes,
        ]
        proc = subprocess.run(
            command,
            cwd=root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if not results.is_file():
            return {
                "outcomes": {},
                "reasons": {},
                "collect_errors": {"pytest": f"pytest exited {proc.returncode} without results"},
                "exitstatus": proc.returncode,
                "output": output,
            }
        data: dict[str, Any] = json.loads(results.read_text(encoding="utf-8"))
        data["output"] = output
        return data


def _status(outcomes: list[str], all_due: bool) -> str:
    if any(outcome in FAILED for outcome in outcomes):
        return "failing"
    if outcomes and all(outcome == "passed" for outcome in outcomes) and all_due:
        return "passing"
    return "pending"


def evaluate(
    root: Path,
    selection: dict[str, dict[str, Any]],
    *,
    mode: str,
    strict_missing: bool,
) -> tuple[dict[str, dict[str, Any]], str]:
    """Per-claim results and the pytest output."""
    wanted = [
        test for entry in selection.values() for part in entry["parts"] for test in part["tests"]
    ]
    existing = claims_module.existing_nodes(root, wanted)
    run = _run_pytest(root, [node for node in wanted if node in existing], mode)
    outcomes: dict[str, str] = run["outcomes"]
    reasons: dict[str, str] = dict(run["reasons"])
    errors: dict[str, str] = run["collect_errors"]
    results: dict[str, dict[str, Any]] = {}
    for cid, entry in selection.items():
        tests: dict[str, str] = {}
        notes: dict[str, str] = {}
        for part in entry["parts"]:
            for node in part["tests"]:
                if node not in existing:
                    if strict_missing:
                        tests[node] = "error"
                        notes[node] = f"test not written (due in phase {part['phase']})"
                    else:
                        notes[node] = f"test not written yet (phase {part['phase']})"
                    continue
                outcome = outcomes.get(node)
                if outcome is None:
                    file_part = node.split("::", 1)[0]
                    outcome = "error"
                    notes[node] = errors.get(file_part) or "not run (collection failed)"
                tests[node] = outcome
                if node in reasons and reasons[node]:
                    notes[node] = reasons[node]
        missing_any = len(tests) < sum(len(part["tests"]) for part in entry["parts"])
        status = _status(list(tests.values()), entry["all_due"] and not missing_any)
        results[cid] = {
            "status": status,
            "tests": tests,
            "parts": sorted({part["name"] for part in entry["parts"] if part["name"]}),
            "notes": notes,
        }
    return results, str(run.get("output", ""))


def _markdown(report: dict[str, Any]) -> str:
    info = report["platform"]
    lines = [
        "# Prove report",
        "",
        f"- Mode: {report['mode']}",
        f"- Created: {report['created']}",
        f"- Source digest: `{report['source_digest']}`",
        f"- Platform: {info['os']} {info['machine']}, Python {info['python']}"
        + (f", runner {info['runner_label']} ({info['image_version']})" if info["ci"] else ""),
        "",
        "| Claim | Status | Tests |",
        "|---|---|---|",
    ]
    for cid, entry in sorted(report["claims"].items()):
        outcome_text = ", ".join(
            f"{node.rsplit('::', 1)[-1]}: {outcome}" for node, outcome in entry["tests"].items()
        )
        lines.append(f"| {cid} | {entry['status']} | {outcome_text or 'no tests yet'} |")
    return "\n".join(lines) + "\n"


def run(
    root: Path,
    *,
    mode: str = "quick",
    phase: int | None = None,
    claims: Sequence[str] = (),
    skip: Sequence[str] = (),
    echo: Callable[[str], None] = print,
    write: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    registry = claims_module.load(root)
    selection = select(registry, phase=phase, claims=claims, skip=skip)
    digest = source_digest(root)
    echo(f"Proving {len(selection)} claim(s) in {mode} mode against {digest[:12]}...")
    results, output = evaluate(
        root, selection, mode=mode, strict_missing=phase is not None or bool(claims)
    )
    report: dict[str, Any] = {
        "schema": 1,
        "mode": mode,
        "source_digest": digest,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_s": round(time.perf_counter() - started, 2),
        "platform": platform_info(),
        "selection": {"phase": phase, "claims": list(claims), "skip": list(skip)},
        "claims": results,
        "summary": {
            status: sum(1 for r in results.values() if r["status"] == status)
            for status in claims_module.STATUSES
        },
    }
    if any(entry["status"] == "failing" for entry in results.values()) and output.strip():
        echo("pytest output (last 60 lines):")
        for line in output.strip().splitlines()[-60:]:
            echo(f"  | {line}")
    for cid, entry in sorted(results.items()):
        echo(f"  {cid}  {entry['status']:8}  {len(entry['tests'])} test(s)")
        for node, note in entry["notes"].items():
            echo(f"      {node.rsplit('::', 1)[-1]}: {note}")
    if write:
        (root / REPORT_JSON).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        (root / REPORT_MD).write_text(_markdown(report), encoding="utf-8", newline="\n")
        current = {claim["id"]: claim.get("status") for claim in registry["claims"]}
        updates = {
            cid: entry["status"]
            for cid, entry in results.items()
            if entry["status"] != "pending" or current.get(cid) == "pending"
        }
        changed = claims_module.update_statuses(root, updates)
        if changed:
            echo(f"Updated claims.yaml: {', '.join(changed)}")
        if render_readme.update_readme(root):
            echo("Regenerated the README claims section.")
        echo(f"Wrote {REPORT_JSON} and {REPORT_MD}")
    summary = report["summary"]
    echo(
        f"{summary['passing']} passing, {summary['failing']} failing, "
        f"{summary['pending']} pending ({report['duration_s']} s)"
    )
    return report
