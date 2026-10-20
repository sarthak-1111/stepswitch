"""SWE-bench suites (ADR 0042) with a fake `docker`: suites, container settings, task runs of
both contestants, grading and the suite runner."""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from stepswitch.bench import runner
from stepswitch.bench import swebench as S
from stepswitch.bench.suites import SuiteError, load_suite
from stepswitch.config import Config, EndpointConfig

ROOT = Path(__file__).resolve().parents[2]
INSTANCE = "django__django-12741"
IMAGE = "swebench/sweb.eval.x86_64.django_1776_django-12741:latest"
LOCAL = "local/big-model"
ROW: dict[str, Any] = {
    "instance_id": INSTANCE,
    "repo": "django/django",
    "base_commit": "537d422942b5",
    "problem_statement": "Simplify the signature of execute_sql_flush().",
    "hints_text": "HINT-NEVER-SHOWN",
    "FAIL_TO_PASS": ["test_flush (backends.tests.FlushTests)"],
    "PASS_TO_PASS": ["test_other (backends.tests.FlushTests)"],
    "image": IMAGE,
    "difficulty": "<15 min fix",
    "patch": "GOLD-NEVER-SHOWN",
    "test_patch": "TESTS-NEVER-SHOWN",
}
PATCH = (
    "diff --git a/django/db/ops.py b/django/db/ops.py\n--- a/django/db/ops.py\n"
    "+++ b/django/db/ops.py\n@@ -1 +1 @@\n-def f(self, using, sql):\n+def f(self, sql):\n"
)
RESULT = {
    "status": "done",
    "stop_reason": "finished",
    "steps": 12,
    "switches": 0,
    "escalations": 0,
    "failovers": 0,
    "handoffs": 2,  # a count, as `ssw run --json` prints it
    "compactions": 1,
    "context": {},
    "endpoints": ["local"],
    "tokens": {"input_tokens": 9000, "output_tokens": 300, "cached_input_tokens": 6000},
    "usage_by_worker": {
        LOCAL: {"calls": 12, "input": 9000, "cached_input": 6000, "output": 300, "local": True}
    },
    "wall_seconds": 120.5,
    "budget": {"gpu_seconds": 0.0},
    "task_id": "t1",
    "branch": "stepswitch/t1",
}
TRAJECTORY = {
    "info": {"exit_status": "Submitted", "submission": PATCH},
    "messages": [
        {"role": "system", "content": "..."},
        {
            "role": "assistant",
            "extra": {
                "response": {
                    "usage": {
                        "prompt_tokens": 4000,
                        "completion_tokens": 100,
                        "prompt_tokens_details": {"cached_tokens": 3000},
                    }
                }
            },
        },
        {
            "role": "assistant",
            "extra": {"response": {"usage": {"prompt_tokens": 5000, "completion_tokens": 50}}},
        },
    ],
}


def config(**extra: Any) -> Config:
    endpoints = [
        EndpointConfig(
            name="local",
            url="http://127.0.0.1:11434",
            engine="ollama",
            formats=["ollama-native", "chat-completions"],
            models=["big-model"],
        ),
        EndpointConfig(
            name="keyed",
            url="http://127.0.0.1:9000",
            auth="bearer",
            api_key="SECRET-KEY",
            models=["m"],
        ),
        EndpointConfig(name="remote", url="https://models.example.org", models=["r"]),
    ]
    return Config(endpoints=endpoints, models={"big-model": {"context": 131072}}, **extra)


class FakeDocker:
    """Answers the docker commands the module sends; records them."""

    def __init__(self, *, resolved: bool = True, fail_run: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.stdin: dict[str, bytes] = {}
        self.resolved = resolved
        self.fail_run = fail_run

    def __call__(
        self, args: Sequence[str], *, stdin: bytes | None = None, timeout: float | None = None
    ) -> S.Done:
        args = list(args)
        self.calls.append(args)
        text = " ".join(args)
        if args[:2] == ["cp", "-"]:
            self.stdin[args[2]] = stdin or b""
            return S.Done(0, b"", b"")
        if args[0] == "run" and "cat" in args and args[-1].endswith("/STAMP"):
            volume = next(a for a in args if a.startswith(S.CONTAINER_PREFIX)).split(":")[0]
            return S.Done(0, volume.rsplit("-", 1)[1].encode() + b"\n", b"")
        if args[0] == "run" and "sh" in args and "mini-extra" in args[-1]:
            self.stdin["mini"] = stdin or b""
            files = {
                "/preds.json": json.dumps({INSTANCE: {"model_patch": PATCH}}),
                f"/{INSTANCE}/{INSTANCE}.traj.json": json.dumps(TRAJECTORY),
            }
            return S.Done(0, S.tar_bytes(files), b"progress")
        if args[0] == "run" and "sh" in args and "swebench eval" in args[-1]:
            run_id = re.search(r"--run-id (\S+)", args[-1]).group(1)  # type: ignore[union-attr]
            self.stdin["grade"] = stdin or b""
            sent = S.untar(stdin or b"")
            model = json.loads(sent["work/preds.jsonl"])["model_name_or_path"]
            report = {INSTANCE: {"resolved": self.resolved, "patch_successfully_applied": True}}
            name = f"/logs/run_evaluation/{run_id}/{model}/{INSTANCE}/report.json"
            return S.Done(0, S.tar_bytes({name: json.dumps(report)}), b"")
        if args[0] == "exec" and "stepswitch run" in text:
            if self.fail_run:
                return S.Done(1, b"", b"Traceback: boom")
            return S.Done(0, json.dumps(RESULT).encode(), b"log")
        if args[0] == "exec" and "git" in args and "diff" in args:
            return S.Done(0, PATCH.encode(), b"")
        return S.Done(0, b"", b"")


# -- suites ----------------------------------------------------------------------------------


def write_suite(tmp_path: Path, instances: list[str]) -> Path:
    path = tmp_path / "bench" / "suites" / "swe.yaml"
    path.parent.mkdir(parents=True)
    lines = ["schema: 1", "name: swe", "kind: swebench"]
    lines += ["dataset: {name: SWE-bench/SWE-bench_Verified, split: test}", "instances:"]
    lines += [f"  - {item}" for item in instances]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_swebench_suite_lists_instance_ids(tmp_path: Path) -> None:
    path = write_suite(tmp_path, [INSTANCE, "sphinx-doc__sphinx-8459"])
    suite = load_suite(str(path), tmp_path)
    assert suite.kind == "swebench" and suite.protected == ()
    assert suite.dataset == {"name": "SWE-bench/SWE-bench_Verified", "split": "test"}
    assert [task.id for task in suite.tasks] == [INSTANCE, "sphinx-doc__sphinx-8459"]
    picked = suite.select(["sphinx-doc__sphinx-8459"])
    assert picked.kind == "swebench" and picked.dataset == suite.dataset
    bad = write_suite(tmp_path / "x", ["not an id"])
    with pytest.raises(SuiteError, match="not instance ids"):
        load_suite(str(bad), tmp_path)


def test_the_committed_swebench_suites_load() -> None:
    mini = load_suite("swebench-verified-mini", ROOT)
    dev = load_suite("swebench-dev", ROOT)
    assert len(mini.tasks) == 50 and len(dev.tasks) == 6
    assert not {t.id for t in mini.tasks} & {t.id for t in dev.tasks}  # never tuned on
    repos = {t.id.split("__")[0] for t in (*mini.tasks, *dev.tasks)}
    assert repos == {"django", "sphinx-doc"}


def test_mini_swe_agent_runs_swebench_suites_only(tmp_path: Path) -> None:
    contestants = runner.parse_contestants(f"router,{S.MINI_PREFIX}{LOCAL}")
    assert [c.name for c in contestants] == ["router", f"{S.MINI_PREFIX}{LOCAL}"]
    suite = load_suite("dev", ROOT)
    with pytest.raises(ValueError, match="SWE-bench suites only"):
        runner.run_bench(suite, contestants[1:], root=tmp_path, out_dir=tmp_path / "out")
    with pytest.raises(ValueError, match="give the worker"):
        runner.parse_contestants(S.MINI_PREFIX)
    gold = runner.parse_contestants(S.GOLD)
    assert gold[0].worker is None and runner.canary_workers(gold, None) == []
    with pytest.raises(ValueError, match="SWE-bench suites only"):
        runner.run_bench(suite, gold, root=tmp_path, out_dir=tmp_path / "out")


def test_the_gold_check_grades_the_reference_patch_without_an_agent(tmp_path: Path) -> None:
    path = write_suite(tmp_path, [INSTANCE])
    suite = load_suite(str(path), tmp_path)
    docker = FakeDocker(resolved=True)
    rows = S.Rows("SWE-bench/SWE-bench_Verified", "test", "rev123", "now", {INSTANCE: ROW})
    out = tmp_path / "gold"
    S.run_suite(
        suite,
        runner.parse_contestants(S.GOLD),
        root=ROOT,
        out_dir=out,
        options=S.SwebenchOptions(seeds=1),
        config=config(),
        command="ssw bench run --suite swe --workers gold",
        echo=lambda message: None,
        docker=docker,
        rows=rows,
    )
    lines = (out / "results.jsonl").read_text("utf-8").splitlines()
    (result,) = [json.loads(line) for line in lines]
    assert result["contestant"] == S.GOLD and result["passed"] and result["steps"] == 0
    graded = next(call for call in docker.calls if "swebench eval" in call[-1])
    assert graded and not any(call[:2] == ["run", "-d"] for call in docker.calls)  # no agent
    assert not any("ssw-swe-deps-" in " ".join(call) for call in docker.calls)  # no runtime
    sent = S.untar(docker.stdin["grade"])
    assert json.loads(sent["work/preds.jsonl"])["model_patch"] == "GOLD-NEVER-SHOWN"
    assert not (out / S.PREDICTIONS_DIR).exists()  # dataset text is never written out


# -- container settings ----------------------------------------------------------------------


def test_container_config_maps_loopback_and_never_carries_credentials() -> None:
    assert S.container_url("http://127.0.0.1:11434") == "http://host.docker.internal:11434"
    assert S.container_url("http://localhost/v1") == "http://host.docker.internal/v1"
    assert S.container_url("http://10.0.0.2:8000") == "http://10.0.0.2:8000"
    text = S.container_config(config(), [LOCAL], "django/django")
    data = tomllib.loads(text)
    assert data["endpoints"] == [
        {
            "name": "local",
            "url": "http://host.docker.internal:11434",
            "engine": "ollama",
            "formats": ["ollama-native", "chat-completions"],
            "local": True,
            "models": ["big-model"],
        }
    ]
    assert data["models"] == {"big-model": {"context": 131072}}
    general = data["tracks"]["general"]
    assert general["gates"] == ["impacted_tests"] and general["python"] == S.TESTBED_PYTHON
    assert general["commands"]["tests"] == ["{python}", f"{S.TASK_DIR}/django_tests.py"]
    sphinx = tomllib.loads(S.container_config(config(), [LOCAL], "sphinx-doc/sphinx"))
    assert sphinx["tracks"]["general"]["commands"]["tests"][:3] == ["{python}", "-m", "pytest"]
    with pytest.raises(S.SwebenchError, match="without credentials"):
        S.container_config(config(), ["keyed/m"], "django/django")
    with pytest.raises(S.SwebenchError, match="local endpoints only"):
        S.container_config(config(), ["remote/r"], "django/django")
    with pytest.raises(S.SwebenchError, match="does not list"):
        S.container_config(config(), ["local/other"], "django/django")


def test_the_django_wrapper_turns_selectors_into_labels(tmp_path: Path, monkeypatch: Any) -> None:
    namespace: dict[str, Any] = {}
    exec(compile(S.DJANGO_TESTS, "django_tests.py", "exec"), namespace)  # noqa: S102
    (tmp_path / "tests" / "backends").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    label = namespace["label"]
    assert label("tests/backends/tests.py::LongNameTest::test_x") == (
        "backends.tests.LongNameTest.test_x"
    )
    assert label("tests/backends/tests.py") == "backends.tests"
    assert label("tests/backends") == "backends"
    assert label("backends.tests.LongNameTest") == "backends.tests.LongNameTest"
    assert label("-k") == "-k" and label("tests") is None
    # gis_tests labels stop django's runner without a GIS backend: they are left out.
    ran: list[list[str]] = []
    monkeypatch.setattr(namespace["subprocess"], "call", lambda cmd, env: ran.append(cmd) or 0)
    gis = "tests/gis_tests/gis_migrations/test_operations.py"
    assert namespace["main"]([gis, "tests/backends/tests.py"]) == 0
    assert ran[-1][-1] == "backends.tests" and not any("gis_tests" in arg for arg in ran[-1])
    assert namespace["main"]([gis]) == 5 and len(ran) == 1  # never the whole suite instead


# -- task runs -------------------------------------------------------------------------------


def test_router_runs_inside_the_instance_container(tmp_path: Path) -> None:
    docker = FakeDocker()
    source = S.source_files(ROOT)
    assert f"{S.SOURCE_DIR}/stepswitch/cli.py" in source
    assert any(name.startswith(f"{S.SOURCE_DIR}/stepswitch/_registry/") for name in source)
    # The package data a wheel ships, as the agent loads it (the handoff schema, ADR 0042).
    assert f"{S.SOURCE_DIR}/stepswitch/_spec/handoff-v0.1.schema.json" in source
    assert not any("/_bench/" in name for name in source)
    settings = S.RouterSettings(workers=[LOCAL], max_steps=60, budget_seconds=1800)
    row, patch = S.run_router(
        docker,
        ROW,
        contestant="router",
        seed=1,
        suite="swe",
        deps_volume="ssw-swe-deps-abc",
        source=source,
        config=config(),
        settings=settings,
    )
    assert patch == PATCH
    assert row["status"] == "done" and row["steps"] == 12 and row["task"] == INSTANCE
    assert row["handoffs"] == 2
    assert row["tokens"] == {"input": 9000, "output": 300, "cached_input": 6000, "reasoning": 0}
    assert row["models"] == {
        LOCAL: {"calls": 12, "input": 9000, "cached_input": 6000, "output": 300, "hosted": False}
    }
    assert row["changed_files"] == ["django/db/ops.py"] and "error" not in row
    started = next(call for call in docker.calls if call[:2] == ["run", "-d"])
    name = started[3]
    assert name.startswith("ssw-swe-run-django__django-12741-")
    assert "ssw-swe-deps-abc:/opt/ssw:ro" in started and IMAGE in started
    copied = S.untar(docker.stdin[f"{name}:/"])
    assert copied["opt/ssw-task/problem.md"] == ROW["problem_statement"].encode()
    assert b"django_tests.py" in copied["testbed/.stepswitch/local.toml"]
    assert "opt/ssw-task/django_tests.py" in copied
    everything = b"".join(copied.values())
    for secret in (b"SECRET-KEY", b"HINT-NEVER-SHOWN", b"GOLD-NEVER-SHOWN", b"TESTS-NEVER"):
        assert secret not in everything
    run = next(call for call in docker.calls if "stepswitch run" in " ".join(call))
    flags = run[run.index("sh", run.index(name) + 3) :]
    assert shlex.join(flags) == "sh --json --seed 1 --max-steps 60 --budget-seconds 1800"
    assert docker.calls[-1] == ["rm", "-f", name]


def test_a_failed_router_run_is_an_error_row_and_the_container_goes(tmp_path: Path) -> None:
    docker = FakeDocker(fail_run=True)
    row, patch = S.run_router(
        docker,
        ROW,
        contestant="router",
        seed=0,
        suite="swe",
        deps_volume="v",
        source={},
        config=config(),
        settings=S.RouterSettings(workers=[LOCAL]),
    )
    assert patch == "" and row["status"] == "error" and "Traceback: boom" in row["error"]
    assert docker.calls[-1][:2] == ["rm", "-f"]


def test_mini_swe_agent_runs_with_its_own_runner(tmp_path: Path) -> None:
    docker = FakeDocker()
    row, patch = S.run_mini(
        docker,
        ROW,
        contestant=f"{S.MINI_PREFIX}{LOCAL}",
        seed=0,
        suite="swe",
        harness_volume="ssw-swe-harness-x",
        dataset_line=json.dumps(ROW),
        config=config(),
        settings=S.MiniSettings(worker=LOCAL, keep_logs=tmp_path / "logs"),
    )
    assert patch == PATCH and row["status"] == "done" and row["stop_reason"] == "Submitted"
    assert row["tokens"] == {"input": 9000, "output": 150, "cached_input": 3000, "reasoning": 0}
    assert row["models"][LOCAL]["calls"] == 2 and row["models"][LOCAL]["hosted"] is False
    started = next(call for call in docker.calls if call[0] == "run")
    script = started[-1]
    assert f"--filter '^{re.escape(INSTANCE)}$'" in script
    assert "-m 'openai/big-model'" in script
    assert "api_base=http://host.docker.internal:11434/v1" in script
    assert f"{S.SOCKET}:{S.SOCKET}" in started and "HF_HUB_OFFLINE=1" in started
    assert json.loads(docker.stdin["mini"]) == ROW  # its runner reads the instance's row
    kept = tmp_path / "logs" / "mini-swe-agent-local-big-model" / INSTANCE / "s0"
    assert json.loads((kept / "trajectory.json").read_text(encoding="utf-8")) == TRAJECTORY


def test_grading_reads_the_harness_report() -> None:
    docker = FakeDocker(resolved=True)
    verdict = S.grade(docker, ROW, PATCH, harness_volume="h", run_id="r1", model="router")
    assert verdict["resolved"] is True and verdict["patch_applied"] is True
    call = next(call for call in docker.calls if call[0] == "run")
    script = call[-1]
    assert f"-i {INSTANCE}" in script and "--run-id r1" in script
    # The harness stops its containers with the Docker command line from the volume.
    assert f"PATH={S.HARNESS_MOUNT}/bin:/usr/local/bin:/usr/bin:/bin" in call
    assert ["rm", "-f", f"sweb.eval.{INSTANCE}.r1"] in docker.calls
    empty = FakeDocker()
    assert S.grade(empty, ROW, "  \n", harness_volume="h", run_id="r", model="m") == {
        "resolved": False,
        "empty_patch": True,
    }
    assert empty.calls == []


def test_the_manifest_names_each_image_by_its_registry_digest() -> None:
    name = "swebench/sweb.eval.x86_64.django_1776_django-12741"
    digest = "sha256:" + "0" * 64
    found = json.dumps([f"elsewhere/copy@sha256:{'1' * 64}", f"{name}@{digest}"]).encode()

    def docker(args: Sequence[str], **_: Any) -> S.Done:
        return S.Done(0, found if "RepoDigests" in " ".join(args) else b"", b"")

    assert S.image_digest(docker, f"{name}:latest") == digest
    assert S.image_digest(lambda args, **_: S.Done(1, b"", b"no such image"), name) is None


def test_patch_helpers_and_trajectory_usage() -> None:
    assert S.patch_paths(PATCH) == ["django/db/ops.py"]
    assert not S.touches_tests(PATCH)
    assert S.touches_tests(PATCH.replace("django/db/ops.py", "tests/backends/tests.py"))
    assert S.touches_tests(PATCH.replace("django/db/ops.py", "sphinx/test_util.py"))
    usage = S.trajectory_usage(TRAJECTORY)
    assert usage == {"calls": 2, "input": 9000, "cached_input": 3000, "output": 150, "reasoning": 0}
    assert S.trajectory_usage({}) == {
        "calls": 0,
        "input": 0,
        "cached_input": 0,
        "output": 0,
        "reasoning": 0,
    }


# -- the dataset and the suite ---------------------------------------------------------------


class FakeHttp:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, params: dict[str, str]) -> Any:
        self.requests.append((url, params))
        if url.startswith(S.HUB_API):
            return _Response({"sha": "rev123"})
        offset = int(params["offset"])
        rows = [
            {"row": {"instance_id": f"a__b-{i}"}} for i in range(offset, min(offset + 100, 150))
        ]
        return _Response({"rows": rows, "num_rows_total": 150})


class _Response:
    status_code = 200

    def __init__(self, data: Any) -> None:
        self.data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.data


def test_dataset_rows_are_fetched_once_and_cached(tmp_path: Path) -> None:
    http = FakeHttp()
    rows = S.load_rows(tmp_path, "SWE-bench/SWE-bench_Verified", "test", client=http)
    assert len(rows.rows) == 150 and rows.revision == "rev123"
    assert [params.get("offset") for _, params in http.requests[:2]] == ["0", "100"]
    again = S.load_rows(tmp_path, "SWE-bench/SWE-bench_Verified", "test", client=None)
    assert again.rows == rows.rows and again.revision == "rev123"  # from the state directory
    with pytest.raises(S.SwebenchError, match="not in"):
        rows.select(["a__b-1", "x__y-9"])


def test_the_suite_runs_both_contestants_and_grades_every_run(tmp_path: Path) -> None:
    path = write_suite(tmp_path, [INSTANCE])
    suite = load_suite(str(path), tmp_path)
    contestants = runner.parse_contestants(f"router,{S.MINI_PREFIX}{LOCAL}")
    docker = FakeDocker()
    rows = S.Rows("SWE-bench/SWE-bench_Verified", "test", "rev123", "now", {INSTANCE: ROW})
    out = tmp_path / "results"
    messages: list[str] = []
    S.run_suite(
        suite,
        contestants,
        root=ROOT,
        out_dir=out,
        options=S.SwebenchOptions(seeds=1, router_pool=[LOCAL], max_steps=60),
        config=config(),
        command="ssw bench run --suite swe",
        echo=messages.append,
        docker=docker,
        rows=rows,
        deps_requirements="pydantic==2.14.0\n",
    )
    lines = (out / "results.jsonl").read_text(encoding="utf-8").splitlines()
    results = sorted((json.loads(line) for line in lines), key=lambda r: r["contestant"])
    assert [r["contestant"] for r in results] == [f"{S.MINI_PREFIX}{LOCAL}", "router"]
    assert all(r["passed"] and r["swebench"]["resolved"] for r in results)
    assert all(not r["infra_error"] and r["hosted_tokens"] == 0 for r in results)
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "swebench" and manifest["dataset"]["revision"] == "rev123"
    assert manifest["dataset"]["rows_sha256"] == S.rows_digest([ROW])
    assert manifest["images"] == [IMAGE] and manifest["router_pool"] == [LOCAL]
    assert manifest["image_digests"] == {IMAGE: None}  # the fake has no registry digests
    assert manifest["harness"]["swebench"] == S.SWEBENCH_VERSION
    assert (out / "leaderboard.md").is_file()
    assert any("Resolved 2 of 2" in message for message in messages)
    # Each contestant's patches, in the harness's format: anyone can grade them again.
    saved = sorted(p.name for p in (out / S.PREDICTIONS_DIR).iterdir())
    assert saved == ["mini-swe-agent-local-big-model-s0.jsonl", "router-s0.jsonl"]
    saved_lines = (out / S.PREDICTIONS_DIR / "router-s0.jsonl").read_text("utf-8").splitlines()
    (prediction,) = [json.loads(line) for line in saved_lines]
    assert prediction == {
        "instance_id": INSTANCE,
        "model_name_or_path": "router",
        "model_patch": PATCH,
    }
    names = [call[3] for call in docker.calls if call[:2] == ["run", "-d"]]
    removed = [call[2] for call in docker.calls if call[:2] == ["rm", "-f"]]
    assert set(names) <= set(removed)  # every task container is removed
    # Only containers of this run: its own, and the harness's, named for this run's ids.
    harness = f"sweb.eval.{INSTANCE}.{out.name}-"
    assert all(name.startswith((S.CONTAINER_PREFIX, harness)) for name in removed)


def test_swebench_contestants_need_credential_free_local_workers() -> None:
    options = S.SwebenchOptions(router_pool=["keyed/m"])
    with pytest.raises(S.SwebenchError, match="without credentials"):
        S.check_contestants(runner.parse_contestants("router"), options, config())
    with pytest.raises(S.SwebenchError, match="--router-pool"):
        S.check_contestants(runner.parse_contestants("router"), S.SwebenchOptions(), config())
    with pytest.raises(S.SwebenchError, match="cannot run a SWE-bench suite"):
        S.check_contestants(runner.parse_contestants("fake:oracle"), options, config())
