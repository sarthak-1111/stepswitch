"""C-014: replays recorded runs deterministically.

Proof (SPEC §18.5): replay of a recorded fake-worker run reproduces identical diffs and gate
results. Scope: recorded runs of deterministic (fake) workers with recorded tool outputs.

`tests/claims/replay_fixture.py` records six runs of scripted workers with the real tools,
worktrees and gates: a fix with a failing test on the way, a failover after a model error, a
relay with a HANDOFF brief from a summarizer, a run stopped by its time budget, a best-of-2
run and a science-track run with a golden case. Each is replayed with `replay_run` and a fresh
copy of its workers. A seventh run goes through the CLI as a user would: `ssw run` against
two scripted model servers on loopback ports, which are then shut down before
`ssw replay last --json`.

The claim holds when every replay reproduces the recorded diffs (the result's and each
candidate's) and gate results, with every model call answered from the recording, no model
asked and no point where the replay departed from the recording.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from stepswitch.trajectory.replay import replay_run
from tests.claims import evidence, replay_fixture
from tests.claims.fake_server import ScriptedServer
from tests.claims.toy import TOY_INSTRUCTION, toy_repo

pytestmark = [pytest.mark.claim("C-014")]


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


def row(name: str, recorded: dict[str, Any], report: dict[str, Any], asked: int) -> dict[str, Any]:
    return {
        "run": name,
        "status": recorded.get("status"),
        "stop_reason": recorded.get("stop_reason"),
        "best_of": recorded.get("best_of"),
        "failovers": recorded.get("failovers"),
        "handoffs": recorded.get("handoffs"),
        "diffs": {k: v["recorded_lines"] for k, v in report["diffs"].items()},
        "gate_runs": sum(len(v) for v in report["gate_results"]["recorded"].values()),
        "model_calls": report["model_calls"],
        "models_asked": asked,
        "tools_run_again": report["tools_run_again"],
        "identical_diffs": report["identical_diffs"],
        "identical_gate_results": report["identical_gate_results"],
        "replayed_status": report["status"]["replayed"],
        "departures": report["departures"],
    }


def cli_run(tmp_path: Path) -> dict[str, Any]:
    """`ssw run` on scripted servers, then `ssw replay last --json` with the servers gone."""
    repo = toy_repo("calc-bug", tmp_path / "cli")
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
        run = ssw(repo, home, "run", TOY_INSTRUCTION, "--json")
        assert run.returncode == 0, run.stderr + run.stdout
        served = len(alpha.requests) + len(beta.requests)
    recorded = json.loads(run.stdout)
    replay = ssw(repo, home, "replay", "last", "--json")
    assert replay.returncode in (0, 1), replay.stderr + replay.stdout
    report = json.loads(replay.stdout)
    assert report["replay_of"] == recorded["task_id"]
    return {**row("cli", recorded, report, 0), "requests_while_recording": served}


def test_replay_reproduces_diffs_and_gate_results(tmp_path: Path) -> None:
    limits = evidence.thresholds("C-014")
    rows: list[dict[str, Any]] = []
    for name in replay_fixture.SCENARIOS:
        recorded = replay_fixture.record(name, tmp_path)
        live = recorded.catalog()
        replayed = replay_run(recorded.result.task_id, recorded.root, catalog=live)
        result = recorded.result.model_dump(mode="json", exclude={"diff"})
        rows.append(row(name, result, replayed.comparison, replay_fixture.model_calls(live)))
    rows.append(cli_run(tmp_path))

    problems: list[str] = []
    for item in rows:
        calls = item["model_calls"]
        if limits["identical_diffs"] and not item["identical_diffs"]:
            problems.append(f"{item['run']}: the diffs differ")
        if limits["identical_gate_results"] and not item["identical_gate_results"]:
            problems.append(f"{item['run']}: the gate results differ")
        if item["departures"]:
            problems.append(f"{item['run']}: departed: {item['departures'][:3]}")
        if calls["live"] or item["models_asked"] or calls["from_recording"] != calls["recorded"]:
            problems.append(f"{item['run']}: not every model call came from the recording")
        if item["replayed_status"] != item["status"]:
            problems.append(f"{item['run']}: {item['replayed_status']} vs {item['status']}")
    covered = {
        "failover": any(item["failovers"] for item in rows),
        "handoff_brief": any(item["handoffs"] for item in rows),
        "time_budget_stop": any(item["stop_reason"] == "budget_wall_seconds" for item in rows),
        "best_of_n": any((item["best_of"] or 1) > 1 for item in rows),
        "gate_runs": sum(item["gate_runs"] for item in rows),
    }
    evidence.write(
        "C-014",
        "replays",
        {
            "runs": len(rows),
            "reproduced": sum(
                1 for item in rows if item["identical_diffs"] and item["identical_gate_results"]
            ),
            "covered": covered,
            "rows": rows,
            "problems": problems,
        },
        command="uv run ssw prove --claim C-014",
    )
    assert all(v for k, v in covered.items()), covered
    assert not problems, problems
