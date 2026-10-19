"""AirLLM adapter and shim (ADR 0030): command lines, availability, local directories only, and
the shim's AirLLM options. AirLLM, torch and transformers are not installed here: the loader
and the two libraries are fakes, and generation itself is the transformers wrapper's loop
(tests/unit/test_serve_transformers.py)."""

from __future__ import annotations

import ast
import subprocess
import sys
import types
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from stepswitch.registry import EngineRecipe, ModelEntry
from stepswitch.serve import airllm_server
from stepswitch.serve.airllm import DEFAULT_CTX, SERVER_SCRIPT, AirLLMEngine
from stepswitch.serve.base import EngineError


def entry(recipe: EngineRecipe | None = None, *, context: int = 32768) -> ModelEntry:
    return ModelEntry(
        id="huge-moe",
        name="Huge MoE",
        hub_id="test/huge-moe",
        quant="bf16",
        format="safetensors",
        file_size_gb=400.0,
        ram_gb=8.0,
        context=context,
        license="apache-2.0",
        engines={"airllm": recipe or EngineRecipe()},
    )


def fake_environment(root: Path, dists: Sequence[str]) -> Path:
    (root / "bin").mkdir(parents=True)
    python = root / "bin" / "python"
    python.write_bytes(b"")
    site = root / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    for dist in dists:
        (site / f"{dist}.dist-info").mkdir()
    return python


@pytest.fixture
def fake_libraries(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    torch = types.SimpleNamespace(float16="torch.float16", bfloat16="torch.bfloat16")
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(__version__="5.1.0"))
    return torch


class Loaded:
    """What `AutoModel.from_pretrained` returns: the tokenizer and the generation config."""

    def __init__(self, eos: Any = 7, pad: int | None = None) -> None:
        self.tokenizer = types.SimpleNamespace(pad_token_id=pad, chat_template="{{ x }}")
        self.generation_config = types.SimpleNamespace(eos_token_id=eos)


def test_the_generator_loads_a_local_directory_with_the_airllm_options(
    tmp_path: Path, fake_libraries: types.SimpleNamespace
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def loader(path: str, **options: Any) -> Loaded:
        calls.append((path, options))
        return Loaded(eos=[7, 9])

    generator = airllm_server.AirLLMGenerator(
        str(tmp_path),
        dtype="float16",
        ctx_size=4096,
        compression="4bit",
        layer_shards_dir=str(tmp_path / "shards"),
        prefetching=False,
        loader=loader,
    )
    assert calls == [
        (
            str(tmp_path),
            {
                "device": "cuda:0",
                "max_seq_len": 4096,
                "prefetching": False,
                "compression": "4bit",
                "dtype": "torch.float16",
                "layer_shards_saving_path": str(tmp_path / "shards"),
            },
        )
    ]
    assert generator.ctx_size == 4096 and generator.device == "cuda:0"
    assert generator.eos_ids == {7, 9} and generator.pad_id == 7


def test_generator_defaults(tmp_path: Path, fake_libraries: types.SimpleNamespace) -> None:
    seen: dict[str, Any] = {}

    def loader(path: str, **options: Any) -> Loaded:
        seen.update(options)
        return Loaded(eos=None, pad=0)

    generator = airllm_server.AirLLMGenerator(str(tmp_path), device="cuda:1", loader=loader)
    assert seen == {
        "device": "cuda:1",
        "max_seq_len": airllm_server.DEFAULT_CTX,
        "prefetching": True,
        "compression": None,
    }
    assert generator.eos_ids == set() and generator.pad_id == 0


def test_names_that_airllm_would_download_are_refused(tmp_path: Path) -> None:
    def loader(path: str, **options: Any) -> Loaded:
        raise AssertionError("never loaded")

    with pytest.raises(RuntimeError, match="not a local model directory"):
        airllm_server.AirLLMGenerator("org/huge-moe", loader=loader)
    with pytest.raises(RuntimeError, match="--compression"):
        airllm_server.AirLLMGenerator(str(tmp_path), compression="2bit", loader=loader)


def test_main_returns_1_when_the_model_does_not_load() -> None:
    assert airllm_server.main(["--model", "org/not-local", "--port", "0"]) == 1


def test_the_shim_imports_only_the_standard_library() -> None:
    tree = ast.parse(SERVER_SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


def test_the_shim_runs_by_path_in_isolated_mode() -> None:
    done = subprocess.run(
        [sys.executable, "-I", str(SERVER_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "--model" in done.stdout


def _engine(**kwargs: Any) -> AirLLMEngine:
    return AirLLMEngine(command_prefix=["python", "-I", "airllm_server.py"], **kwargs)


def test_command_line_from_the_recipe() -> None:
    recipe = EngineRecipe(args=["--compression", "4bit", "--layer-shards-dir", "/fast/shards"])
    cmd = _engine().command(entry(recipe), "/models/huge-moe", port=8123)
    assert cmd == [
        "python",
        "-I",
        "airllm_server.py",
        "--model",
        "/models/huge-moe",
        "--compression",
        "4bit",
        "--layer-shards-dir",
        "/fast/shards",
        "--ctx-size",
        "32768",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
        "--served-model-name",
        "huge-moe",
    ]
    asked = _engine().command(entry(), "/m", port=1, ctx_size=4096)
    assert asked[asked.index("--ctx-size") + 1] == "4096"
    unknown = _engine().command(entry(context=0), "/m", port=1)
    assert unknown[unknown.index("--ctx-size") + 1] == str(DEFAULT_CTX)


@pytest.mark.parametrize(
    "bad", [["--port", "1"], ["--host=0.0.0.0"], ["--model", "x"], ["--ctx-size", "9"]]
)
def test_owned_flags_cannot_be_overridden(bad: list[str]) -> None:
    with pytest.raises(EngineError, match="may not set"):
        _engine().command(entry(EngineRecipe(args=bad)), "/m", port=1)


def test_availability_needs_airllm_torch_and_transformers(tmp_path: Path) -> None:
    python = fake_environment(tmp_path / "env", ["airllm-4.0.0", "torch-2.9.1"])
    reason = AirLLMEngine(python=python, check_platform=False).unavailable_reason()
    assert reason is not None and "transformers" in reason
    full = fake_environment(
        tmp_path / "full", ["airllm-4.0.0", "torch-2.9.1", "transformers-5.1.0"]
    )
    engine = AirLLMEngine(python=full, check_platform=False)
    assert engine.unavailable_reason() is None and engine.version() == "4.0.0"
    assert engine.launcher()[1:] == ["-I", str(SERVER_SCRIPT)]


def test_platforms() -> None:
    engine = AirLLMEngine()
    for system, machine in (("linux", "x86_64"), ("win32", "AMD64"), ("linux", "aarch64")):
        assert engine.platform_problem(system, machine) is None
    problem = engine.platform_problem("darwin", "arm64")
    assert problem is not None and "MLX" in problem


def test_models_must_be_local_directories(tmp_path: Path) -> None:
    python = fake_environment(
        tmp_path / "env", ["airllm-4.0.0", "torch-2.9.1", "transformers-5.1.0"]
    )
    engine = AirLLMEngine(python=python, models_dir=tmp_path / "models", check_platform=False)
    by_id = entry(EngineRecipe(name="org/huge-moe"))
    problem = engine.serve_problem(by_id)
    assert problem is not None and "local model directory only" in problem
    with pytest.raises(EngineError, match="local model directory only"):
        engine.start(by_id)
    local = tmp_path / "weights"
    local.mkdir()
    assert engine.serve_problem(entry(EngineRecipe(name=str(local)))) is None


def test_the_key_and_the_offline_settings_travel_in_the_environment() -> None:
    env = AirLLMEngine().server_env("k-123", base={"PATH": "/bin"})
    assert env["STEPSWITCH_SERVER_API_KEY"] == "k-123"
    assert env["HF_HUB_OFFLINE"] == "1" and env["HF_HUB_DISABLE_TELEMETRY"] == "1"
