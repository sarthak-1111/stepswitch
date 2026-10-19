"""Offline mode at the transport (SPEC §15, ADR 0011): a task's egress layer refuses non-local
connections whenever the task runs offline, whether asked for, configured or decided by the
track's policy, without changing the process-wide layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import net
from stepswitch.compat.ir import Request, Usage
from stepswitch.core.orchestrator import run_task
from stepswitch.core.task import TaskSpec
from stepswitch.workers import catalog as catalog_module
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, ScriptedModel
from tests.claims.toy import TOY_INSTRUCTION, toy_repo


def answering(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def test_a_child_layer_is_offline_without_touching_its_parent() -> None:
    parent = net.Egress()
    child = parent.child(offline=True)
    assert child.policy.offline and not parent.policy.offline
    transport = httpx.MockTransport(answering)
    with child.client("model", transport=transport) as client:
        assert client.get("http://127.0.0.1:9/x").status_code == 200
        with pytest.raises(net.EgressBlocked):
            client.get("https://models.example.org/v1/models")
    rows = list(parent.memory)  # the child's rows reach the parent's sinks
    assert [(r.host, r.allowed) for r in rows] == [
        ("127.0.0.1", True),
        ("models.example.org", False),
    ]
    assert list(child.memory) == rows  # and the child keeps the task's own view


def test_a_stricter_policy_binds_the_clients_already_handed_out() -> None:
    child = net.Egress().child()
    client = child.client("model", transport=httpx.MockTransport(answering))
    assert client.get("https://models.example.org/").status_code == 200
    child.policy.offline = True
    with pytest.raises(net.EgressBlocked):
        client.get("https://models.example.org/")
    client.close()


def test_an_offline_parent_makes_offline_children() -> None:
    parent = net.Egress(net.EgressPolicy(offline=True))
    assert parent.child().policy.offline


def editor() -> Worker:
    def script(request: Request, index: int) -> Action:
        if not request.tools:
            return Action(text="APPROVE")
        if index == 0:
            return Action(
                tool="edit", arguments={"path": "calc.py", "old": "a - b", "new": "a + b"}
            )
        return Action(tool="finish", arguments={"summary": "fixed add"})

    model = ScriptedModel(script, usage=Usage(input_tokens=10, output_tokens=2))
    return Worker(WorkerSpec(id="fake/editor", endpoint="fake", harness="fake", tier="t2"), model)


@pytest.mark.parametrize(
    ("track", "offline", "expected"),
    [
        ("general", False, False),
        ("general", True, True),
        ("science", False, True),  # the science track runs offline unless configured otherwise
    ],
)
def test_the_tasks_model_clients_get_the_tasks_offline_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, track: str, offline: bool, expected: bool
) -> None:
    seen: list[Any] = []

    def from_config(cls: Any, config: Any, root: Path, *, egress: Any = None, **_: Any) -> Any:
        seen.append(egress)
        return Catalog([editor()])

    monkeypatch.setattr(catalog_module.Catalog, "from_config", classmethod(from_config))
    repo = toy_repo("calc-bug", tmp_path / "repo")
    spec = TaskSpec(
        instruction=TOY_INSTRUCTION,
        repo=repo,
        track=track,  # type: ignore[arg-type]
        offline=offline,
        max_steps=4,
        review=False,
    )
    run_task(spec, root=repo)
    (layer,) = seen
    assert layer is not net.default()
    assert layer.policy.offline is expected
    assert net.default().policy.offline is False  # the process-wide layer is unchanged


def test_an_offline_proxy_refuses_non_local_upstreams_at_the_transport(tmp_path: Path) -> None:
    from stepswitch.config import Config, EndpointConfig
    from stepswitch.proxy.runtime import build_service

    config = Config(
        endpoints=[
            EndpointConfig(
                name="hosted",
                url="https://models.example.org",
                formats=["chat-completions"],
                models=["big"],
            )
        ]
    )
    service = build_service(tmp_path, config, egress=net.Egress(), offline=True)
    worker = service.catalog.find("hosted/big")
    assert worker is not None
    with pytest.raises(Exception) as caught:
        worker.get_model().complete(Request(model="big", messages=[]))
    assert "offline" in str(caught.value).lower()
