"""The worker catalog: workers from endpoints and installed registry models (ADR 0015)."""

from __future__ import annotations

from pathlib import Path

import pytest

from stepswitch.config import Config
from stepswitch.registry import ModelEntry
from stepswitch.state import open_project_state
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import (
    Catalog,
    model_facts,
    params_from_name,
    tier_for_params,
)
from stepswitch.workers.fake import ScriptedModel


@pytest.mark.parametrize(
    ("params", "tier"),
    [
        (0.6, "cpu"),
        (4.5, "cpu"),
        (8.0, "t1"),
        (16.0, "t1"),
        (32.8, "t2"),
        (116.8, "t2"),
        (480.0, "t3"),
    ],
)
def test_tier_for_params(params: float, tier: str) -> None:
    assert tier_for_params(params) == tier


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("qwen2.5:32b", 32.0),
        ("Coder-1.5B-Instruct", 1.5),
        ("big-120b-moe", 120.0),
        ("model-v2", None),
        ("abc", None),
    ],
)
def test_params_from_name(name: str, params: float | None) -> None:
    assert params_from_name(name) == params


def entry(**overrides: object) -> ModelEntry:
    data: dict[str, object] = {
        "id": "tiny",
        "name": "Tiny",
        "hub_id": "org/tiny",
        "quant": "Q4_K_M",
        "file_size_gb": 1.0,
        "ram_gb": 2.0,
        "context": 32768,
        "license": "apache-2.0",
        "params_b": 1.5,
        "tool_calls": "none",
        "roles": ["router"],
    }
    data.update(overrides)
    return ModelEntry.model_validate(data)


def test_model_facts_precedence() -> None:
    facts = model_facts("tiny", local=True, entry=entry())
    assert (facts.tier, facts.tool_protocol, facts.context, facts.roles) == (
        "cpu",
        "fallback",
        32768,
        ("router",),
    )
    configured = model_facts(
        "tiny",
        local=True,
        entry=entry(),
        table={"tier": "t2", "tool_protocol": "native", "roles": ["editor"], "context": 8192},
    )
    assert (configured.tier, configured.tool_protocol, configured.roles, configured.context) == (
        "t2",
        "native",
        ("editor",),
        8192,
    )
    probed = model_facts(
        "x",
        local=True,
        table={"params_total_b": 32.8, "tool_calls": "native; parsed server-side"},
        profile={"capabilities": {"native_tools": False, "context_ok_tokens": 4096}},
        calibration={"prefill_tps": 1200.0, "decode_tps": 30.0},
    )
    assert (probed.tier, probed.tool_protocol, probed.context) == ("t2", "fallback", 4096)
    assert (probed.prefill_tps, probed.decode_tps) == (1200.0, 30.0)
    unknown = model_facts("mystery", local=True)
    assert unknown.tier == "t1" and "size unknown" in unknown.notes[0]
    assert model_facts("mystery-70b", local=False).tier == "remote"
    assert model_facts("x", local=True, table={"tool_calls": "native; harmony"}).tool_protocol == (
        "native"
    )


def test_catalog_from_config_endpoints_profiles_and_calibration(tmp_path: Path) -> None:
    db = open_project_state(tmp_path)
    db.save_probe(
        "lab",
        "small-coder:7b",
        "chat-completions",
        {"capabilities": {"native_tools": False, "fallback_tools": True}},
    )
    db.save_calibration("lab", "big:70b", 2000.0, 40.0, 150.0, {})
    config = Config.model_validate(
        {
            "endpoints": [
                {
                    "name": "lab",
                    "url": "http://127.0.0.1:9",
                    "models": ["small-coder:7b", "big:70b"],
                },
                {"name": "far", "url": "https://models.example.invalid/v1", "models": ["huge"]},
            ],
            "models": {"big:70b": {"roles": ["planner"], "family": "bigfam"}},
            "storage": {"models_dir": str(tmp_path / "models")},
        }
    )
    catalog = Catalog.from_config(config, tmp_path, db=db, include_managed=False)
    db.close()
    specs = {s.id: s for s in catalog.specs()}
    assert set(specs) == {"lab/small-coder:7b", "lab/big:70b", "far/huge"}
    small, big, far = specs["lab/small-coder:7b"], specs["lab/big:70b"], specs["far/huge"]
    assert (small.tier, small.tool_protocol) == ("t1", "fallback")
    assert (big.tier, big.roles, big.family, big.prefill_tps) == (
        "t2",
        ("planner",),
        "bigfam",
        2000.0,
    )
    assert (far.tier, far.local, far.level) == ("remote", False, 3)
    assert catalog.levels() == [1]
    assert catalog.levels(allow_remote=True) == [1, 3]
    assert catalog.levels(allow_remote=True, offline=True) == [1]
    assert catalog.find("big:70b") is catalog.get("lab/big:70b")
    assert catalog.find("lab") is None  # ambiguous
    assert catalog.classifier() is None


def test_catalog_includes_installed_registry_models_only(tmp_path: Path) -> None:
    models = tmp_path / "models"
    config = Config.model_validate({"storage": {"models_dir": str(models)}})
    catalog = Catalog.from_config(config, tmp_path)
    assert len(catalog) == 0 and catalog.notes  # nothing installed: a note says what to do


def test_catalog_classifier_and_duplicates() -> None:
    tiny = Worker(WorkerSpec(id="m/tiny", tier="cpu", roles=("router",)), ScriptedModel([]))
    other = Worker(WorkerSpec(id="m/other", tier="cpu", roles=("summarizer",)), ScriptedModel([]))
    catalog = Catalog([other, tiny])
    assert catalog.classifier() is tiny
    assert "m/tiny" in catalog and len(catalog) == 2
    with pytest.raises(ValueError, match="duplicate"):
        Catalog([tiny, tiny])


def test_subset_keeps_the_named_workers_once_and_rejects_unknown_names() -> None:
    from stepswitch.workers.base import Worker, WorkerSpec
    from stepswitch.workers.catalog import Catalog

    workers = [
        Worker(WorkerSpec(id=f"ep/{name}", endpoint="ep", model=name, harness="fake"))
        for name in ("small", "large", "other")
    ]
    catalog = Catalog(workers, notes=["a note"])
    subset = catalog.subset(["ep/large", "small", "ep/large"])
    assert list(subset.workers) == ["ep/large", "ep/small"]
    assert subset.notes == ["a note"]
    with pytest.raises(ValueError, match="unknown worker 'nowhere'"):
        catalog.subset(["nowhere"])


def test_a_task_routes_only_within_its_worker_pool(tmp_path: Path) -> None:
    from stepswitch.compat.ir import Usage
    from stepswitch.core.orchestrator import run_task
    from stepswitch.core.task import TaskSpec
    from stepswitch.trajectory.record import read_events
    from stepswitch.workers.base import Worker, WorkerSpec
    from stepswitch.workers.catalog import Catalog
    from stepswitch.workers.fake import Action, ScriptedModel
    from tests.claims.toy import TOY_INSTRUCTION, toy_repo

    def finisher(name: str, tier: str) -> Worker:
        model = ScriptedModel(
            lambda request, index: (
                Action(tool="finish", arguments={"summary": "Done."})
                if request.tools
                else Action(text="APPROVE")
            ),
            usage=Usage(input_tokens=10, output_tokens=2),
        )
        spec = WorkerSpec(id=f"fake/{name}", endpoint=name, harness="fake", tier=tier)
        return Worker(spec, model)

    repo = toy_repo("calc-bug", tmp_path / "repo")
    catalog = Catalog([finisher("alpha", "cpu"), finisher("beta", "t2")])
    spec = TaskSpec(instruction=TOY_INSTRUCTION, repo=repo, workers=["fake/alpha"], max_steps=6)
    result = run_task(spec, root=repo, catalog=catalog)
    assert set(result.endpoints) == {"alpha"}
    events = read_events(Path(str(result.run_dir)))
    assert next(e for e in events if e["type"] == "pool")["workers"] == ["fake/alpha"]
