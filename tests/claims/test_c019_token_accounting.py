"""C-019: token accounting matches what model servers report.

Proof (SPEC §18.5): ledger totals equal server-reported usage within 1%. Scope: ledger totals
compared with server-reported usage for the same runs.

The reference is the model server's own counters, not the usage objects the ledger is built
from: the llama.cpp server's `/metrics` counts every prompt token it evaluated, every prompt
token it reused from its cache and every token it generated (docs/RESEARCH.md, "llama.cpp server
usage and metrics"). The test runs the toy task twice with `ssw run` in a scratch project whose
managed servers start fresh: pinned to a tiny model with native tool calls, then to one without,
which works through the fallback protocol (so repair attempts count too). Classifier calls go
to the native model's server. After both runs it reads the counters of every server the project
started and compares them with the ledger rows of the same model, over both tasks:

    input tokens        = prompt_tokens_total + prompt_tokens_cached_total
    cached input tokens = prompt_tokens_cached_total
    output tokens       = tokens_predicted_total

Each relative difference must be at most the threshold (1%). Whether the tasks succeed does not
matter here; every model call must be accounted for either way.

The test needs both models' files (`ssw models pull qwen3-4b-instruct-2507
phi-3.5-mini-instruct`, or `STEPSWITCH_MODELS_DIR`) and a llama.cpp server build; without them
the claim stays pending.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from stepswitch import config as config_module
from stepswitch.ledger import task_rows
from stepswitch.serve.manager import ServerManager
from tests.claims import evidence
from tests.claims.local_models import (
    installed,
    llamacpp_server,
    ssw,
    ssw_env,
    stopping_servers,
)
from tests.claims.toy import TOY_INSTRUCTION, toy_repo

pytestmark = [pytest.mark.claim("C-019")]

MODELS = ("qwen3-4b-instruct-2507", "phi-3.5-mini-instruct")
PROMPT = "llamacpp:prompt_tokens_total"
CACHED = "llamacpp:prompt_tokens_cached_total"
PREDICTED = "llamacpp:tokens_predicted_total"


def server_counters(repo: Path) -> dict[str, dict[str, float]]:
    """The token counters of every server the project started, by model."""
    manager = ServerManager(repo, config_module.load(repo))
    counters: dict[str, dict[str, float]] = {}
    for info in manager.status():
        samples = manager.engines[info.engine].metrics(info) or {}
        counters[info.model] = {
            name: float(samples[name]) for name in (PROMPT, CACHED, PREDICTED) if name in samples
        }
    return counters


def ledger_totals(repo: Path, task_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Ledger totals of the tasks, by model."""
    totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"input": 0, "cached_input": 0, "output": 0, "calls": 0, "sources": set()}
    )
    for task_id in task_ids:
        for row in task_rows(repo, task_id):
            entry = totals[row["model"]]
            entry["input"] += row["input_tokens"]
            entry["cached_input"] += row["cached_input_tokens"]
            entry["output"] += row["output_tokens"]
            entry["calls"] += 1
            entry["sources"].add(row["usage_source"])
    return {model: {**t, "sources": sorted(t["sources"])} for model, t in totals.items()}


def relative(ledger: float, server: float) -> float:
    return abs(ledger - server) / max(server, 1.0)


def test_ledger_matches_server_usage_within_one_percent(tmp_path: Path) -> None:
    limit = evidence.thresholds("C-019")["max_relative_error"]
    missing = [model for model in MODELS if installed(model) is None]
    if missing:
        pytest.skip(f"pending: {', '.join(missing)} not installed (`ssw models pull ...`)")
    if llamacpp_server() is None:
        pytest.skip("pending: no llama.cpp server build is installed")
    found = installed(MODELS[0])
    assert found is not None
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
    runs: list[dict[str, Any]] = []
    with stopping_servers(repo, env):
        for model in MODELS:
            run = ssw(
                repo, "run", TOY_INSTRUCTION, "--worker", f"managed/{model}", "--json", env=env
            )
            assert run.stdout.strip(), run.stderr[-2000:]
            result = json.loads(run.stdout)
            runs.append(
                {
                    "worker": f"managed/{model}",
                    "task_id": result["task_id"],
                    "status": result["status"],
                    "steps": result["steps"],
                }
            )
        counters = server_counters(repo)
    ledger = ledger_totals(repo, [r["task_id"] for r in runs])
    comparisons = []
    for model, totals in sorted(ledger.items()):
        server = counters.get(model, {})
        expected = {
            "input": server.get(PROMPT, 0.0) + server.get(CACHED, 0.0),
            "cached_input": server.get(CACHED, 0.0),
            "output": server.get(PREDICTED, 0.0),
        }
        comparisons.append(
            {
                "model": model,
                "calls": totals["calls"],
                "usage_sources": totals["sources"],
                "ledger": {key: totals[key] for key in expected},
                "server": expected,
                "relative_error": {
                    key: round(relative(totals[key], value), 6) for key, value in expected.items()
                },
            }
        )
    worst = max(
        (error for c in comparisons for error in c["relative_error"].values()), default=None
    )
    evidence.write(
        "C-019",
        "accounting",
        {
            "engine": "llama.cpp server",
            "runs": [{k: v for k, v in r.items() if k != "task_id"} for r in runs],
            "comparisons": comparisons,
            "max_relative_error": worst,
            "threshold": limit,
        },
    )
    assert set(ledger) == set(MODELS), (sorted(ledger), sorted(counters))
    assert set(counters) >= set(MODELS), sorted(counters)
    for comparison in comparisons:
        assert comparison["usage_sources"] == ["server"], comparison
        assert all(comparison["server"][key] > 0 for key in ("input", "output")), comparison
    assert worst is not None and worst <= limit, comparisons
