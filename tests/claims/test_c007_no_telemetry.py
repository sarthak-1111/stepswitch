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

import ipaddress
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.claims import evidence

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

RECORDER = Path(__file__).with_name("recorder.py")

LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def is_local(host: str) -> bool:
    host = host.strip("[]").split("%", 1)[0].lower()
    if host in LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_outbound(row: dict[str, Any]) -> bool:
    if row["kind"] in {"connect", "send"}:
        return not row.get("unix") and not is_local(str(row.get("host", "")))
    if row["kind"] == "lookup":
        return not is_local(row["host"])
    return False


def isolation_prefix() -> tuple[list[str], str]:
    """A command prefix that runs the flow without network access, where the OS offers one."""
    if not sys.platform.startswith("linux"):
        return [], "none: in-process recorder only (no OS-level isolation on this platform)"
    unshare = shutil.which("unshare")
    if unshare is None:
        pytest.fail("C-007 scope: the Linux run must be OS-isolated, but `unshare` is missing")
    probe = subprocess.run(
        [unshare, "--net", "--map-root-user", "true"], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        pytest.fail(
            "C-007 scope: the Linux run must be OS-isolated, but unprivileged network "
            f"namespaces are unavailable: {probe.stderr.strip()}"
        )
    ip = shutil.which("ip")
    if ip:
        script = f'{ip} link set lo up && exec "$@"'
        return [unshare, "--net", "--map-root-user", "sh", "-c", script, "sh"], (
            "linux network namespace with loopback only (unshare --net)"
        )
    return [unshare, "--net", "--map-root-user"], "linux network namespace (unshare --net)"


def recorder_mode() -> tuple[str, str]:
    """Audit hooks when this interpreter can import with one installed, else patching."""
    canary = subprocess.run(
        [sys.executable, "-c", "import sys; sys.addaudithook(lambda e, a: None); import json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if canary.returncode == 0:
        return "audit", "interpreter audit hooks"
    lines = canary.stderr.strip().splitlines()
    reason = lines[-1] if lines else f"exit code {canary.returncode}"
    return "patch", (
        "wrapped socket, name-lookup and process functions; audit hooks are unusable in this "
        f"environment (importing with a hook installed fails: {reason})"
    )


def test_default_flow_opens_no_outbound_connections(tmp_path: Path) -> None:
    limits = evidence.thresholds("C-007")
    project = tmp_path / "project"
    project.mkdir()
    record = tmp_path / "record.jsonl"
    prefix, isolation = isolation_prefix()
    mode, recorder = recorder_mode()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("STEPSWITCH_")
        and key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    }
    env.update(
        {
            "SSW_RECORD": str(record),
            "SSW_RECORDER": mode,
            "STEPSWITCH_HOME": str(tmp_path / "home"),
            "NO_COLOR": "1",
            "PYTHONUTF8": "1",
            "COLUMNS": "120",
        }
    )
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

    rows = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    current: list[str] = []
    events = []
    for row in rows:
        if row["kind"] == "command":
            current = row["argv"]
            continue
        events.append({"command": " ".join(current), **row, "outbound": is_outbound(row)})
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
