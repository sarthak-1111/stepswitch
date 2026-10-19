"""Shared pieces of the egress claim tests (C-006 offline, C-007 no telemetry, ADR 0011).

Each command of a flow runs in a fresh interpreter under `recorder.py`, which records socket
connects, datagram sends, binds, name lookups and process spawns before any Stepswitch code is
imported (audit hooks where they work, wrapped functions where security software on the host
breaks audit hooks). With `guard=True` the recorder also refuses every non-local connection,
send and name lookup. On Linux the flow runs inside a new network namespace that has only
loopback.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

RECORDER = Path(__file__).with_name("recorder.py")
LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
PROXY_VARIABLES = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}


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


def isolation_prefix(claim: str) -> tuple[list[str], str]:
    """A command prefix that runs a flow without network access, where the OS offers one."""
    if not sys.platform.startswith("linux"):
        return [], "none: in-process recorder only (no OS-level isolation on this platform)"
    unshare = shutil.which("unshare")
    if unshare is None:
        pytest.fail(f"{claim} scope: the Linux run must be OS-isolated, but `unshare` is missing")
    probe = subprocess.run(
        [unshare, "--net", "--map-root-user", "true"], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        pytest.fail(
            f"{claim} scope: the Linux run must be OS-isolated, but unprivileged network "
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


def recorder_env(record: Path, mode: str, home: Path, *, guard: bool = False) -> dict[str, str]:
    """The environment of recorded commands: no Stepswitch overrides, no proxy variables."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("STEPSWITCH_") and key.upper() not in PROXY_VARIABLES
    }
    env.update(
        {
            "SSW_RECORD": str(record),
            "SSW_RECORDER": mode,
            "STEPSWITCH_HOME": str(home),
            "NO_COLOR": "1",
            "PYTHONUTF8": "1",
            "COLUMNS": "120",
        }
    )
    if guard:
        env["SSW_GUARD"] = "block"
    return env


def read_events(record: Path) -> list[dict[str, Any]]:
    """The recorded events, each tagged with the command it belongs to and whether it is
    outbound."""
    current: list[str] = []
    events = []
    for line in record.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["kind"] == "command":
            current = row["argv"]
            continue
        events.append({"command": " ".join(current), **row, "outbound": is_outbound(row)})
    return events
