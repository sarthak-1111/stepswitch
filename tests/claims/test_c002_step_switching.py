"""C-002: switches models between steps inside a single task.

Proof (SPEC §18.5): one task's trajectory contains at least two distinct endpoints across typed
steps; every switch has a logged reason and cost estimate.

Two model servers run on loopback ports: "alpha" and "beta", each serving one model through the
chat-completions format (`fake_server.ScriptedServer`, a scripted model that can fix the calc-bug
toy repository). The project's stepswitch.toml routes planning, review and summary steps to
alpha and the working steps to beta. `ssw run` then fixes the toy repository as a subprocess,
exactly as a user would run it, and the test reads the task's recorded trajectory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from stepswitch.core.task import STEP_TYPES
from stepswitch.trajectory.record import read_events
from tests.claims import evidence
from tests.claims.fake_server import ScriptedServer
from tests.claims.toy import TOY_INSTRUCTION, toy_repo

pytestmark = [pytest.mark.claim("C-002")]

RULES = """
[[policy.rules]]
name = "plan-and-review-on-alpha"
when = { step_type = ["plan", "review", "summarize"] }
to = { endpoint = "alpha" }

[[policy.rules]]
name = "work-on-beta"
when = { step_type = ["navigate", "read", "edit", "run", "triage"] }
to = { endpoint = "beta" }
"""


def ssw(repo: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("STEPSWITCH_")}
    env.update({"STEPSWITCH_HOME": str(home), "PYTHONUTF8": "1", "NO_COLOR": "1"})
    return subprocess.run(
        [sys.executable, "-m", "stepswitch", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=600,
        check=False,
    )


def test_single_task_switches_endpoints_with_logged_reasons(tmp_path: Path) -> None:
    limits = evidence.thresholds("C-002")
    repo = toy_repo("calc-bug", tmp_path / "project")
    home = tmp_path / "home"
    with ScriptedServer("alpha-model") as alpha, ScriptedServer("beta-model") as beta:
        init = ssw(repo, home, "init", "--no-detect")
        assert init.returncode == 0, init.stderr
        local = repo / ".stepswitch" / "local.toml"
        local.write_text(
            local.read_text(encoding="utf-8")
            + "\n".join(
                f'\n[[endpoints]]\nname = "{name}"\nurl = "{server.url}"\n'
                f'format = "chat-completions"\nmodels = ["{server.model}"]\n'
                for name, server in (("alpha", alpha), ("beta", beta))
            ),
            encoding="utf-8",
        )
        project = repo / "stepswitch.toml"
        project.write_text(project.read_text(encoding="utf-8") + RULES, encoding="utf-8")
        run = ssw(repo, home, "run", TOY_INSTRUCTION, "--json")
        assert run.returncode == 0, run.stderr + run.stdout
        result = json.loads(run.stdout)
        served = {"alpha": alpha.requests, "beta": beta.requests}

    events = read_events(Path(result["run_dir"]))
    steps = [e for e in events if e["type"] == "step"]
    switches = [e for e in events if e["type"] == "switch"]
    endpoints = sorted({s["endpoint"] for s in steps})
    complete = [
        s
        for s in switches
        if isinstance(s.get("reason"), str)
        and s["reason"].strip()
        and isinstance(s.get("cost"), dict)
        and all(isinstance(s["cost"].get(k), int | float) for k in ("seconds", "tokens"))
    ]
    evidence.write(
        "C-002",
        "trajectory",
        {
            "task_id": result["task_id"],
            "status": result["status"],
            "endpoints": endpoints,
            "steps": [
                {k: s.get(k) for k in ("step", "step_type", "endpoint", "model", "tool_calls")}
                for s in steps
            ],
            "switches": switches,
            "switches_with_reason_and_cost": len(complete),
            "requests_served": {name: len(rows) for name, rows in served.items()},
        },
    )
    assert result["status"] == "done", result
    assert all(s["step_type"] in STEP_TYPES for s in steps), steps
    assert len(endpoints) >= limits["min_distinct_endpoints"], endpoints
    assert switches, "no switch events recorded"
    share: Any = len(complete) / len(switches)
    assert share >= limits["switches_with_reason_and_cost"], switches
    for switch in switches:
        assert switch["from_endpoint"] != switch["to_endpoint"], switch
    assert all(served.values()), {k: len(v) for k, v in served.items()}
