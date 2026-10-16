"""Routing rules as data (`[[policy.rules]]`) and the classifier (SPEC §7.1)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Request, Usage
from stepswitch.config import Config
from stepswitch.router.classifier import classify, heuristic
from stepswitch.router.features import CLASSIFIER_SCHEMA
from stepswitch.router.rules import (
    RuleError,
    parse_rules,
    rules_from_config,
    selector_matches,
    value_matches,
    when_matches,
)
from stepswitch.workers.base import WorkerSpec
from stepswitch.workers.fake import Action, ScriptedModel


@pytest.mark.parametrize(
    ("expected", "actual", "result"),
    [
        ("plan", "plan", True),
        (["plan", "review"], "review", True),
        (["plan", "review"], "edit", False),
        (">=3", 3, True),
        (">=3", 2, False),
        ("<2", 1, True),
        ("!=low", "high", True),
        ("!=low", "low", False),
        (True, True, True),
        ("true", True, True),
        (">=3", None, False),
        (">=x", 3, False),
    ],
)
def test_value_matches(expected: Any, actual: Any, result: bool) -> None:
    assert value_matches(expected, actual) is result


def test_when_and_selectors() -> None:
    assert when_matches(
        {"step_type": ["plan"], "difficulty": ">=2"}, {"step_type": "plan", "difficulty": 3}
    )
    assert not when_matches({"track": "science"}, {"track": "general"})
    spec = WorkerSpec(id="e/m", endpoint="e", model="m", tier="t2", roles=("planner", "editor"))
    assert selector_matches({"role": "planner", "tier": ["t2", "t3"]}, spec)
    assert not selector_matches({"role": ["reviewer"]}, spec)
    assert selector_matches({"local": True}, spec)
    with pytest.raises(RuleError, match="unknown worker field"):
        selector_matches({"color": "red"}, spec)


def test_parse_rules_validates() -> None:
    rules = parse_rules([{"when": {}, "to": {"tier": "t2"}}])
    assert rules[0].name == "rule-1" and rules[0].bonus == 0.1
    for bad, message in [
        ({"when": {}}, "needs `to`"),
        ({"to": {"tier": "t2"}, "colour": 1}, "unknown keys"),
        ({"to": {"gpu": True}}, "unknown fields"),
    ]:
        with pytest.raises(RuleError, match=message):
            parse_rules([bad])
    with pytest.raises(RuleError, match="must be a table"):
        parse_rules(["plan -> big"])  # type: ignore[list-item]


def test_rules_from_config_reads_policy_rules() -> None:
    config = Config.model_validate(
        {"policy": {"rules": [{"name": "r", "when": {"step_type": "plan"}, "to": {"tier": "t3"}}]}}
    )
    assert [r.name for r in rules_from_config(config)] == ["r"]
    assert rules_from_config(Config()) == []
    assert rules_from_config(SimpleNamespace(policy=None)) == []


def test_heuristic_classifier(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("1\n", encoding="utf-8")
    features = heuristic("Fix the crash in app.py", tmp_path)
    assert (features.task_type, features.language, features.source) == (
        "bugfix",
        "python",
        "heuristic",
    )
    assert features.risk == "low"
    risky = heuristic("Delete the production database migration", tmp_path)
    assert risky.risk == "high"
    science = heuristic("Fit the Raman spectrum baseline", tmp_path, track="science")
    assert science.domain == "spectroscopy"


ANSWER = {
    "task_type": "bugfix",
    "difficulty": 2,
    "domain": "general",
    "risk": "low",
    "est_context_tokens": 1200,
    "needs_new_code": False,
    "frontend": False,
    "confidence": 0.9,
}


def test_model_classifier_uses_schema_and_cache(tmp_path: Path) -> None:
    model = ScriptedModel(
        [Action(text=json.dumps(ANSWER))], usage=Usage(input_tokens=90, output_tokens=40)
    )
    cache: dict[str, dict[str, Any]] = {}
    features, details = classify("Fix it", tmp_path, model=model, worker_id="t0", cache=cache)
    assert features.source == "model:t0" and features.confidence == 0.9
    assert details["classifier"] == "model" and details["usage"]["input_tokens"] == 90
    request: Request = model.requests[0]
    assert request.response_format is not None
    assert request.response_format.json_schema == CLASSIFIER_SCHEMA
    again, details = classify(
        "Fix it", tmp_path / "other", model=model, worker_id="t0", cache=cache
    )
    assert details["classifier"] == "cache" and again.task_type == "bugfix"
    assert model.calls == 1  # the cache does not depend on the checkout path


def test_model_classifier_falls_back_to_the_heuristic(tmp_path: Path) -> None:
    bad = ScriptedModel([Action(text='{"task_type": "nonsense"}')])
    features, details = classify("Fix the bug", tmp_path, model=bad, worker_id="t0")
    assert features.source == "heuristic" and "unusable" in details["reason"]

    class Broken:
        def complete(self, request: Request) -> Any:
            raise ModelError("connection", "down")

    features, details = classify("Fix the bug", tmp_path, model=Broken(), worker_id="t0")
    assert features.source == "heuristic"
    features, details = classify("Fix the bug", tmp_path)
    assert details == {"classifier": "heuristic", "reason": "no classifier model available"}
