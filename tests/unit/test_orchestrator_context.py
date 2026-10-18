"""The orchestrator's Phase 5 wiring (ADR 0024): the relay, briefs in the run directory and the
task result, the learned handoff recovery, and the baseline switch."""

from __future__ import annotations

import json
from pathlib import Path

from stepswitch.compat.ir import Request, Usage
from stepswitch.core.orchestrator import run_task
from stepswitch.core.task import TaskSpec
from stepswitch.router import calibration
from stepswitch.state import open_project_state
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, ScriptedModel
from tests.claims.toy import TOY_INSTRUCTION, toy_repo


def reader(name: str, tier: str) -> Worker:
    def script(request: Request, _index: int) -> Action:
        if not request.tools:
            return Action(text="APPROVE")
        return Action(tool="read", arguments={"path": "calc.py"})

    model = ScriptedModel(script, usage=Usage(input_tokens=10, output_tokens=2))
    return Worker(WorkerSpec(id=f"fake/{name}", endpoint=name, harness="fake", tier=tier), model)


def test_a_relay_writes_a_brief_and_teaches_the_handoff_recovery(tmp_path: Path) -> None:
    repo = toy_repo("calc-bug", tmp_path / "repo")
    catalog = Catalog([reader("alpha", "cpu"), reader("beta", "t2")])
    spec = TaskSpec(
        instruction=TOY_INSTRUCTION,
        repo=repo,
        workers=["fake/alpha", "fake/beta"],
        relay=["fake/alpha", "fake/beta"],
        relay_step=3,
        max_steps=5,
        review=False,
    )
    result = run_task(spec, root=repo, catalog=catalog)
    assert result.optimize and result.handoffs == 1 and result.compactions == 0
    assert set(result.context) >= {"pruned", "dedup", "distilled"}
    assert result.run_dir is not None
    [record] = [
        json.loads(line)
        for line in (Path(result.run_dir) / "handoffs.jsonl").read_text("utf-8").splitlines()
    ]
    assert record["reason"] == "relay" and record["to_worker"] == "fake/beta"
    text = json.dumps(record)
    assert str(repo) not in text and str(Path.home()) not in text
    db = open_project_state(repo)
    recovery = calibration.load_recovery(db)
    db.close()
    assert recovery.observations == 1  # the relay went to a stronger worker


def test_the_baseline_runs_without_the_optimizations(tmp_path: Path) -> None:
    repo = toy_repo("calc-bug", tmp_path / "repo")
    catalog = Catalog([reader("alpha", "cpu"), reader("beta", "t2")])
    spec = TaskSpec(
        instruction=TOY_INSTRUCTION,
        repo=repo,
        relay=["fake/alpha", "fake/beta"],
        relay_step=3,
        max_steps=5,
        review=False,
        optimize=False,
    )
    result = run_task(spec, root=repo, catalog=catalog)
    assert not result.optimize and result.handoffs == 0 and result.switches == 1
    assert not (Path(result.run_dir or "") / "handoffs.jsonl").exists()
