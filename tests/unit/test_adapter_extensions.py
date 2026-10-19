"""The extensions for TypeScript harnesses (`adapters/`, SPEC §12 items 4-5, ADR 0029): their
package manifests, where the Python adapters find them, their own tests under Node, and one routed
request from the Pi extension through a real `ssw proxy`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from stepswitch.ledger import Ledger
from stepswitch.proxy.runtime import ProxyServer
from stepswitch.proxy.service import ProxyService, ProxySettings
from stepswitch.state import open_project_state
from stepswitch.trajectory.record import Trajectory
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.harness import HarnessSettings, HarnessTask, get_adapter
from tests.unit.test_harness_contract import endpoint
from tests.unit.test_proxy import says, worker

REPO = Path(__file__).resolve().parents[2]
ADAPTERS = REPO / "adapters"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
HOSTS = {"pi": "@earendil-works/pi-coding-agent", "omp": "@oh-my-pi/pi-coding-agent"}


def manifest(name: str) -> dict[str, Any]:
    return json.loads((ADAPTERS / name / "package.json").read_text(encoding="utf-8"))


def node(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    clean = {k: v for k, v in os.environ.items() if not k.startswith("STEPSWITCH_")}
    return subprocess.run(
        [NODE, *args],
        cwd=ADAPTERS,
        env={**clean, **(env or {})},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def test_the_packages_are_mit_es_modules_with_the_hosts_as_peers() -> None:
    assert manifest(".")["type"] == "module"  # the shared core and the tests
    for name, host in HOSTS.items():
        data = manifest(name)
        assert data["license"] == "MIT" and data["type"] == "module"
        assert data["peerDependencies"][host] == "*"
        assert "dependencies" not in data  # the host's packages are never bundled
    pi = manifest("pi")
    assert "pi-package" in pi["keywords"]
    assert pi["pi"]["extensions"] == ["./index.js"]
    assert (ADAPTERS / "pi" / "index.js").is_file()


def test_the_adapters_load_only_the_extensions_of_a_source_checkout(tmp_path: Path) -> None:
    settings = HarnessSettings(state_dir=tmp_path / "state")
    pi = get_adapter("pi")
    assert pi.extension() == ADAPTERS / "pi" / "index.js"
    args = pi.command(HarnessTask("Fix it."), tmp_path, endpoint(), settings)
    assert args[args.index("-e") + 1] == str(ADAPTERS / "pi" / "index.js")
    assert "--no-extensions" in args
    assert args[args.index("--model") + 1] == "stepswitch/routed"
    omp = get_adapter("oh-my-pi")
    assert omp.extension() == ADAPTERS / "omp" / "index.js"
    args = omp.command(HarnessTask("Fix it."), tmp_path, endpoint(), settings)
    assert args[args.index("--trusted-extension") + 1] == str(ADAPTERS / "omp" / "index.js")
    assert "-e" not in args  # cannot be combined with `--trusted-extension`
    assert args[args.index("--model") + 1] == "stepswitch/ssw-auto"  # no virtual models
    env = omp.full_environment(HarnessTask("x"), tmp_path, endpoint(), settings, base={})
    roles = json.loads(env["STEPSWITCH_ROLE_MODELS"])
    assert roles["plan"] == "stepswitch/ssw-plan" and roles["task"] == "stepswitch/ssw-auto"


@needs_node
def test_the_extensions_pass_their_own_tests_under_node() -> None:
    done = node("--test", "test/extensions.test.js")
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]


@needs_node
def test_the_routed_virtual_model_resolves_through_a_real_proxy(tmp_path: Path) -> None:
    db = open_project_state(tmp_path)
    workers = [
        worker("a/small", says(), tier="t1", context=8192),
        worker("b/large", says(), tier="t2", context=131072),
    ]
    service = ProxyService(
        tmp_path, Catalog(workers), db=db, settings=ProxySettings(heartbeat_seconds=0.2)
    )
    task = service.attach(
        "t-ext",
        task_id="t-ext",
        trajectory=Trajectory(tmp_path, "t-ext", db=db),
        ledger=Ledger(db, "t-ext"),
        instruction="Fix the bug.",
    )
    server = ProxyServer(service, token="ssw-ext-token", port=0)
    env = {
        "STEPSWITCH_PROXY_URL": server.start(),
        "STEPSWITCH_PROXY_TOKEN": "ssw-ext-token",
        "STEPSWITCH_SESSION": "t-ext",
        "STEPSWITCH_CONTEXT_TOKENS": "131072",
    }
    registry = json.dumps(["ssw-auto", "a/small", "b/large"])
    try:
        # A transcript of about 20000 tokens fits only the large worker's window.
        big = node(
            "test/route_once.js", registry, json.dumps({"reason": "user", "pad": 80000}), env=env
        )
        assert big.returncode == 0, big.stderr[-2000:]
        route = json.loads(big.stdout.strip().splitlines()[-1])
        assert route["contextWindow"] == 131072
        assert route["model"] == {"provider": "stepswitch", "id": "b/large"}
        assert set(task.reserved) == {"b/large"}  # the step of the request that follows
        # A wrong token is refused, and the extension fails the request instead of guessing.
        refused = node(
            "test/route_once.js",
            registry,
            json.dumps({"reason": "user"}),
            env={**env, "STEPSWITCH_PROXY_TOKEN": "ssw-wrong"},
        )
        assert refused.returncode != 0 and "HTTP 401" in refused.stderr
    finally:
        server.stop(reason="test over")
        service.detach("t-ext")
        db.close()
