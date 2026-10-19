"""C-006: in offline mode, makes no network connections except to local endpoints.

Proof (SPEC §18.5): a full task flow with `--offline` runs under a socket-level guard, plus
OS-level network isolation on Linux CI. Zero non-local connections are observed, and the audit
log matches the observed connections exactly. Scope (ADR 0011): local means 127.0.0.0/8, ::1
and Unix sockets; on macOS and Windows the evidence comes from the in-process recorder only and
says so.

The flow (`offline_flow.py`) runs `ssw init`, configures a local endpoint (a scripted
chat-completions model on 127.0.0.1 that fixes the calc-bug toy repository) next to a hosted one,
runs the task with `--offline`, then reads `ssw audit` for the task. Every command runs under
`recorder.py` with its guard on: a connect, send or name lookup to anything non-local is recorded
and refused.

"Matches exactly" (C-006 evidence, `audit`): the set of destinations (host, port) the task's
audit rows name equals the set of destinations the `ssw run` process was seen connecting to,
leaving out sockets the process bound itself (a connect to those never leaves the process).
Requests share connections, so the comparison is per destination, not per request.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.claims import evidence
from tests.claims.egress import (
    is_local,
    isolation_prefix,
    read_events,
    recorder_env,
    recorder_mode,
)
from tests.claims.toy import toy_repo

pytestmark = [pytest.mark.claim("C-006")]

FLOW = Path(__file__).with_name("offline_flow.py")


@pytest.fixture(scope="module")
def flow(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    base = tmp_path_factory.mktemp("c006")
    project = toy_repo("calc-bug", base / "project")
    record = base / "record.jsonl"
    out = base / "out.json"
    prefix, isolation = isolation_prefix("C-006")
    mode, recorder = recorder_mode()
    env = recorder_env(record, mode, base / "home", guard=True)
    proc = subprocess.run(
        [*prefix, sys.executable, str(FLOW), str(project), str(record), str(out)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        check=False,
    )
    assert proc.returncode == 0 and out.is_file(), proc.stderr[-3000:]
    data: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    data["events"] = read_events(record)
    data["isolation"] = isolation
    data["recorder"] = f"{recorder}; guard: non-local connects, sends and lookups refused"
    return data


def _destinations(rows: list[dict[str, Any]]) -> set[tuple[str, int]]:
    return {(str(r["host"]), int(r["port"])) for r in rows if r.get("port") is not None}


def test_offline_flow_makes_only_local_connections(flow: dict[str, Any]) -> None:
    limits = evidence.thresholds("C-006")
    events = flow["events"]
    connections = [e for e in events if e["kind"] in {"connect", "send"}]
    lookups = [e for e in events if e["kind"] == "lookup"]
    outbound = [e for e in events if e["outbound"]]
    blocked = [e for e in events if e.get("blocked")]
    result = flow["result"]
    steps = result.get("steps", 0)
    audit_remote = [r for r in flow["audit"] if not r["local"]]
    evidence.write(
        "C-006",
        "flow",
        {
            "flow": flow["commands"],
            "configuration": (
                "calc-bug toy repository; endpoints: a scripted chat-completions model on "
                f"127.0.0.1 and a hosted one at {flow['remote_url']}; `ssw run --offline`"
            ),
            "isolation": flow["isolation"],
            "recorder": flow["recorder"],
            "task": {
                "status": result.get("status"),
                "steps": steps,
                "local_model_requests": flow["server"]["requests"],
                "changed_files": result.get("changed_files"),
            },
            "connections": connections,
            "name_lookups": lookups,
            "processes": [e for e in events if e["kind"] == "process"],
            "non_local_connections": len(outbound),
            "refused_by_guard": len(blocked),
            "non_local_audit_rows": len(audit_remote),
            "outbound": outbound,
        },
    )
    failed = [c for c in flow["commands"] if c["exit_code"] != 0]
    assert not failed, f"flow commands failed: {failed}"
    assert result.get("status") == "done", result
    assert flow["server"]["requests"] > 0, "the task never asked the local model"
    assert len(outbound) <= limits["non_local_connections"], outbound
    assert not blocked, blocked  # nothing even tried to leave
    assert not audit_remote, audit_remote
    hosted = flow["remote_url"].split("//", 1)[1].split("/", 1)[0]
    assert not any(hosted in json.dumps(e) for e in events), "the hosted endpoint was contacted"


def test_audit_log_matches_observed_connections(flow: dict[str, Any]) -> None:
    limits = evidence.thresholds("C-006")
    run = [e for e in flow["events"] if e["command"].startswith("ssw run")]
    bound = _destinations([e for e in run if e["kind"] == "bind"])
    seen = _destinations([e for e in run if e["kind"] == "connect"]) - bound
    audited = _destinations(flow["audit"])
    missing = sorted(seen - audited)  # connections the audit log does not name
    extra = sorted(audited - seen)  # audit rows for connections never seen
    mismatches = len(missing) + len(extra)
    evidence.write(
        "C-006",
        "audit",
        {
            "rule": (
                "destinations (host, port) of the task's audit rows equal the destinations the "
                "`ssw run` process connected to, minus sockets it bound itself"
            ),
            "observed": [list(d) for d in sorted(seen)],
            "audited": [list(d) for d in sorted(audited)],
            "audit_rows": len(flow["audit"]),
            "audit_purposes": sorted({r["purpose"] for r in flow["audit"]}),
            "missing_from_audit": [list(d) for d in missing],
            "audited_but_not_observed": [list(d) for d in extra],
            "audit_mismatches": mismatches,
            "processes_logged": len(flow["processes"]),
        },
    )
    assert flow["audit"], "the task left no audit rows"
    assert all(is_local(r["host"]) for r in flow["audit"]), flow["audit"]
    assert flow["processes"], "the task's spawned processes were not logged"
    assert mismatches <= limits["audit_mismatches"], {"missing": missing, "extra": extra}
