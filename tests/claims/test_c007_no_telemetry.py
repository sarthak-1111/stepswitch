"""C-007: sends no telemetry.

Proof (SPEC §18.5): a default-config flow with no remote endpoints configured opens zero
outbound connections. Scope (ADR 0011): the evidence lists every connection and every spawned
process, and the Linux run is OS-isolated.

Each command of the flow runs in a fresh interpreter under tests/claims/recorder.py, which
records socket connects, datagram sends, name lookups and process spawns before any Stepswitch
code is imported. It uses interpreter audit hooks where they work and wrapped socket and
process functions where security software on the host breaks audit hooks; the evidence says
which. On Linux the flow also runs inside a new network namespace that has only loopback.
Neither recorder sees traffic of child processes or native code; the evidence lists the child
processes so a reader can judge them, and the Linux isolation covers them.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.claims import evidence
from tests.claims.egress import (
    RECORDER,
    isolation_prefix,
    read_events,
    recorder_env,
    recorder_mode,
)

pytestmark = [pytest.mark.claim("C-007")]

# The default flow available so far; later phases add their commands here.
FLOW: list[list[str]] = [
    ["--help"],
    ["init", "--json"],
    ["doctor", "--json"],
    ["models", "list", "--tier", "cpu", "--json"],
    ["policy", "show", "--json"],
    ["audit", "--json"],
]


def test_default_flow_opens_no_outbound_connections(tmp_path: Path) -> None:
    limits = evidence.thresholds("C-007")
    project = tmp_path / "project"
    project.mkdir()
    record = tmp_path / "record.jsonl"
    prefix, isolation = isolation_prefix("C-007")
    mode, recorder = recorder_mode()
    env = recorder_env(record, mode, tmp_path / "home")
    commands = []
    for args in FLOW:
        started = time.perf_counter()
        with record.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": "command", "argv": ["ssw", *args]}) + "\n")
        proc = subprocess.run(
            [*prefix, sys.executable, str(RECORDER), *args],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        commands.append(
            {
                "argv": ["ssw", *args],
                "exit_code": proc.returncode,
                "seconds": round(time.perf_counter() - started, 2),
                "stderr_tail": proc.stderr.strip().splitlines()[-3:],
            }
        )
        if args[:1] == ["doctor"] and proc.returncode == 0:
            assert "profile" in json.loads(proc.stdout), "doctor printed no profile"

    events = read_events(record)
    connections = [e for e in events if e["kind"] in {"connect", "send"}]
    lookups = [e for e in events if e["kind"] == "lookup"]
    processes = [e for e in events if e["kind"] == "process"]
    outbound = [e for e in events if e["outbound"]]
    evidence.write(
        "C-007",
        "default_flow",
        {
            "flow": commands,
            "configuration": "fresh `ssw init` project, no endpoints configured",
            "isolation": isolation,
            "recorder": recorder,
            "connections": connections,
            "name_lookups": lookups,
            "processes": processes,
            "outbound_connections": len(outbound),
            "outbound": outbound,
        },
    )
    failed = [c for c in commands if c["exit_code"] != 0]
    assert not failed, f"flow commands failed: {failed}"
    assert (project / "stepswitch.toml").is_file(), "`ssw init` did not create stepswitch.toml"
    assert len(outbound) <= limits["outbound_connections"], outbound
