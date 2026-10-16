"""Shared machinery of the Python server engines: Prometheus text, flags, versions, environment
layouts and `[engines.<name>]` settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from stepswitch.serve import pyserver
from stepswitch.serve.base import EngineError
from stepswitch.serve.mlx import MlxEngine
from stepswitch.serve.pyserver import (
    Sample,
    check_args,
    counter_total,
    environment_root,
    gauge_mean,
    installed_version,
    meets_minimum,
    parse_samples,
    release,
    safe_name,
    samples_dict,
    with_flag,
)
from stepswitch.serve.sglang import SglangEngine
from stepswitch.serve.transformers import TransformersEngine
from stepswitch.serve.vllm import VllmEngine

METRICS = """\
# HELP demo:requests_total Requests.
# TYPE demo:requests_total counter
demo:requests_total{engine="0",model_name="m"} 3.0
demo:requests_total{engine="1",model_name="m"} 4.5
demo:requests_created{engine="0",model_name="m"} 1.7e+09
demo:plain 2
demo:ratio{path="a \\"quoted\\" one",kind="x"} 0.25 1700000000000
demo:ratio{kind="y"} 0.75
demo:broken{a="1"} not-a-number
garbage line without a value

demo:inf +Inf
"""


def test_prometheus_text_is_parsed() -> None:
    samples = parse_samples(METRICS)
    names = [sample.name for sample in samples]
    assert names == [
        "demo:requests_total",
        "demo:requests_total",
        "demo:requests_created",
        "demo:plain",
        "demo:ratio",
        "demo:ratio",
        "demo:inf",
    ]
    assert samples[0] == Sample("demo:requests_total", (("engine", "0"), ("model_name", "m")), 3.0)
    assert samples[4].labels == (("path", 'a \\"quoted\\" one'), ("kind", "x"))
    assert samples[4].value == 0.25  # the timestamp is not the value
    assert samples[6].value == float("inf")
    flat = samples_dict(samples)
    assert flat['demo:requests_total{engine="1",model_name="m"}'] == 4.5
    assert flat["demo:plain"] == 2.0


def test_counters_are_summed_over_labels_and_gauges_averaged() -> None:
    samples = parse_samples(METRICS)
    assert counter_total(samples, "demo:requests") == 7.5  # the `_total` name
    assert counter_total(samples, "demo:plain") == 2.0  # a counter without the suffix
    assert counter_total(samples, "demo:missing") is None
    assert gauge_mean(samples, "demo:ratio") == 0.5
    assert gauge_mean(samples, "demo:missing") is None


def test_owned_flags_are_refused_in_both_spellings() -> None:
    forbidden = {"--port": "owned"}
    check_args(["--ports", "1", "--x=--port"], forbidden, "recipe")
    with pytest.raises(EngineError, match=r"recipe may not set --port \(owned\)"):
        check_args(["--port", "1"], forbidden, "recipe")
    with pytest.raises(EngineError, match="may not set --port"):
        check_args(["--port=1"], forbidden, "recipe")


def test_a_flag_is_replaced_in_place_or_appended() -> None:
    assert with_flag(["-a", "--len", "1", "-b"], ("--len",), "9") == ["-a", "--len", "9", "-b"]
    assert with_flag(["--len=1", "-b"], ("--len",), "9") == ["--len=9", "-b"]
    assert with_flag(["--alt", "1", "--len", "2"], ("--len", "--alt"), "9") == [
        "--alt",
        "9",
        "--len",
        "9",
    ]
    assert with_flag(["-b"], ("--len", "--alt"), "9") == ["-b", "--len", "9"]
    assert with_flag(["--len"], ("--len",), "9") == ["--len", "9"]  # a dangling flag


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("0.31.0", (0, 31, 0)),
        ("v0.31.0", (0, 31, 0)),
        ("0.31.0rc1", (0, 31, 0)),
        ("0.31.0+cu130", (0, 31, 0)),
        ("5.20", (5, 20)),
        ("dev", None),
        (None, None),
    ],
)
def test_release_numbers(text: str | None, parsed: tuple[int, ...] | None) -> None:
    assert release(text) == parsed


def test_minimum_versions() -> None:
    assert meets_minimum("0.31.0", "0.31")
    assert meets_minimum("0.31", "0.31.0")
    assert meets_minimum("0.32.1", "0.31.9")
    assert not meets_minimum("0.30.9", "0.31.0")
    assert meets_minimum(None, "0.31.0")  # unknown: not refused
    assert meets_minimum("0.1.0", None)


def test_environment_layouts(tmp_path: Path) -> None:
    assert environment_root(tmp_path / "env" / "bin" / "python") == tmp_path / "env"
    assert environment_root(tmp_path / "env" / "Scripts" / "python.exe") == tmp_path / "env"
    assert environment_root(tmp_path / "usr" / "local" / "vllm") is None
    layouts = {
        "posix": "lib/python3.12/site-packages",
        "debian": "lib/python3/dist-packages",
        "lib64": "lib64/python3.11/site-packages",
        "windows": "Lib/site-packages",
    }
    for name, layout in layouts.items():
        site = tmp_path / name / layout
        site.mkdir(parents=True)
        (site / "mlx_lm-0.32.2.dist-info").mkdir()
        (site / "Torch-2.13.0.dist-info").mkdir()
        (site / "mlx-0.30.0.dist-info").mkdir()
        assert installed_version(tmp_path / name, "mlx-lm") == "0.32.2", name
        assert installed_version(tmp_path / name, "torch") == "2.13.0", name
        assert installed_version(tmp_path / name, "mlx") == "0.30.0", name
        assert installed_version(tmp_path / name, "vllm") is None, name
    assert installed_version(tmp_path / "nothing", "vllm") is None


def test_safe_names() -> None:
    assert safe_name("org/model:7b") == "org_model_7b"
    assert safe_name("") == "model"


@pytest.mark.parametrize("engine_class", [VllmEngine, SglangEngine, MlxEngine, TransformersEngine])
def test_settings_tables(
    engine_class: type[pyserver.PythonServerEngine],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))  # no engine on PATH
    engine = engine_class.from_settings(
        {"python": "/opt/engine/bin/python", "env": {"OMP_NUM_THREADS": "4"}},
        models_dir=tmp_path,
    )
    assert engine.python == "/opt/engine/bin/python" and engine.env == {"OMP_NUM_THREADS": "4"}
    assert engine.models_dir == tmp_path and engine.settings_problem is None
    launcher = engine_class.from_settings({"command": "run-engine"})
    assert launcher.command_prefix == ["run-engine"]
    assert launcher.find_launcher() == ["run-engine"]
    listed = engine_class.from_settings({"command": ["run", "--quiet"]})
    assert listed.command_prefix == ["run", "--quiet"]
    assert engine_class.from_settings(None).settings_problem is None
    broken = engine_class.from_settings({"python": 3, "command": [], "env": {"A": 1}})
    assert broken.settings_problem == (
        "`python` must be a path; `command` must be a list of strings; `env` must be a table of "
        "strings"
    )
    reason = broken.unavailable_reason()
    assert reason is not None and reason.startswith(f"[engines.{engine_class.name}]")
    assert not broken.available()
    with pytest.raises(EngineError, match="in the configuration"):
        broken.launcher()
