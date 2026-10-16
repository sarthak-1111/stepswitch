"""C-005 (fallback part): a model without native tool calling completes a tool-using task.

Proof (SPEC §18.5): a model with no native tool calling completes a tool-using task through the
fallback protocol. The model is a chat model whose template has no tool calling (its registry
entry says `tool_calls = "none"`); it runs on the llama.cpp server that Stepswitch manages, on
CPU. `ssw run` pins every step to it, so every tool call of the run (read, edit, test, finish)
goes through the fallback protocol: the tools are described in the prompt and each reply is one
JSON action, constrained by JSON Schema where the server supports it. The run must end with the
toy repository's tests passing (status done).

The test needs the model's file (`ssw models pull phi-3.5-mini-instruct`, or
`STEPSWITCH_MODELS_DIR`) and a llama.cpp server build; without them the claim stays pending.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stepswitch import registry
from stepswitch.ledger import task_rows
from stepswitch.trajectory.record import read_events
from tests.claims import evidence
from tests.claims.local_models import installed, llamacpp_server, ssw, ssw_env
from tests.claims.toy import TOY_INSTRUCTION, toy_repo

pytestmark = [pytest.mark.claim("C-005")]

MODEL_ID = "phi-3.5-mini-instruct"
WORKER = f"managed/{MODEL_ID}"


def test_model_without_native_tools_completes_task_via_fallback(tmp_path: Path) -> None:
    limits = evidence.thresholds("C-005")
    entry = registry.load()[MODEL_ID]
    assert entry.tool_calls == "none", "the claim needs a model without native tool calling"
    found = installed(MODEL_ID)
    if found is None:
        pytest.skip(f"pending: {MODEL_ID} is not installed (`ssw models pull {MODEL_ID}`)")
    if llamacpp_server() is None:
        pytest.skip("pending: no llama.cpp server build is installed")
    _, models = found
    repo = toy_repo("calc-bug", tmp_path / "project")
    env = ssw_env()
    init = ssw(repo, "init", "--no-detect", env=env)
    assert init.returncode == 0, init.stderr
    local = repo / ".stepswitch" / "local.toml"
    local.write_text(
        local.read_text(encoding="utf-8") + f'\n[storage]\nmodels_dir = "{models.as_posix()}"\n',
        encoding="utf-8",
    )
    run = ssw(repo, "run", TOY_INSTRUCTION, "--worker", WORKER, "--no-review", "--json", env=env)
    assert run.stdout.strip(), run.stderr
    result: dict[str, Any] = json.loads(run.stdout)
    events = read_events(Path(result["run_dir"]))
    ladder = next(e for e in events if e["type"] == "ladder")
    spec = next(w for w in ladder["workers"] if w["id"] == WORKER)
    steps = [e for e in events if e["type"] == "step"]
    tools = [e for e in events if e["type"] == "tool"]
    rows = task_rows(repo, result["task_id"])
    fallback_turns = [
        r
        for r in rows
        if any("fallback" in str(loss.get("detail", "")) for loss in r["data"].get("losses", []))
    ]
    edits = [t for t in tools if t["name"] == "edit" and not t["is_error"]]
    evidence.write(
        "C-005",
        "fallback",
        {
            "model": MODEL_ID,
            "registry_tool_calls": entry.tool_calls,
            "worker": spec,
            "status": result["status"],
            "stop_reason": result["stop_reason"],
            "steps": [
                {k: s.get(k) for k in ("step", "step_type", "worker", "tool_calls")} for s in steps
            ],
            "tool_calls": [{"name": t["name"], "is_error": t["is_error"]} for t in tools],
            "turns_through_fallback": len(fallback_turns),
            "model_turns": len(rows),
            "changed_files": result["changed_files"],
        },
    )
    assert spec["tool_protocol"] == "fallback", spec
    assert {s["worker"] for s in steps} == {WORKER}, steps
    assert edits, tools
    assert fallback_turns, rows
    assert result["status"] == limits["fallback_final_status"], run.stderr[-2000:]
