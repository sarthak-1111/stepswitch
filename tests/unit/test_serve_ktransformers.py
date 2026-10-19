"""KTransformers adapter (ADR 0030): the SGLang adapter as a profile, with the `--kt-*` flags
checked, Linux x86-64 only, and the CPU features read from `/proc/cpuinfo`."""

from __future__ import annotations

from pathlib import Path

import pytest

from stepswitch.registry import EngineRecipe, ModelEntry
from stepswitch.serve.base import EngineError
from stepswitch.serve.ktransformers import KTransformersEngine, cpu_flags, flag_value

KT = ["--kt-method", "AMXINT4", "--kt-weight-path", "/w/cpu", "--kt-cpuinfer", "56"]


def entry(args: list[str] | None = None) -> ModelEntry:
    return ModelEntry(
        id="big-moe",
        name="Big MoE",
        hub_id="test/big-moe",
        quant="bf16",
        format="safetensors",
        file_size_gb=600.0,
        ram_gb=64.0,
        context=65536,
        license="apache-2.0",
        engines={
            "ktransformers": EngineRecipe(args=args if args is not None else KT),
            "sglang": EngineRecipe(args=["--tp-size", "8"]),
        },
    )


def cpuinfo(tmp_path: Path, flags: str) -> Path:
    path = tmp_path / "cpuinfo"
    path.write_text(f"processor\t: 0\nflags\t\t: {flags}\n", encoding="utf-8")
    return path


def engine(tmp_path: Path, flags: str = "fpu sse2 avx2 amx_tile amx_int8") -> KTransformersEngine:
    return KTransformersEngine(
        command_prefix=["python", "-m", "sglang.launch_server"], cpuinfo=cpuinfo(tmp_path, flags)
    )


def test_command_line_is_sglangs_with_the_kt_recipe(tmp_path: Path) -> None:
    cmd = engine(tmp_path).command(entry(), "/models/big-moe", port=30000, ctx_size=16384)
    assert cmd[:5] == ["python", "-m", "sglang.launch_server", "--model-path", "/models/big-moe"]
    assert cmd[5:11] == KT  # the KTransformers recipe, not the SGLang one
    assert "--tp-size" not in cmd
    assert cmd[cmd.index("--context-length") + 1] == "16384"
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--served-model-name") + 1] == "big-moe"


def test_the_kt_flags_are_checked(tmp_path: Path) -> None:
    with pytest.raises(EngineError, match="needs --kt-method and --kt-weight-path"):
        engine(tmp_path).command(entry([]), "/m", port=1)
    with pytest.raises(EngineError, match="--kt-method is one of"):
        engine(tmp_path).command(
            entry(["--kt-method=INT2", "--kt-weight-path", "/w"]), "/m", port=1
        )
    avx2_only = engine(tmp_path, "fpu avx2")
    with pytest.raises(EngineError, match="needs a CPU with amx_tile"):
        avx2_only.command(entry(), "/m", port=1)
    gguf = ["--kt-method", "LLAMAFILE", "--kt-weight-path", "/w"]
    assert "LLAMAFILE" in avx2_only.command(entry(gguf), "/m", port=1)
    # extra arguments count too
    assert avx2_only.command(entry([]), "/m", port=1, extra_args=gguf)


@pytest.mark.parametrize(
    ("method", "flags", "lacking"),
    [
        ("RAWINT4", "avx2 avx512f", "avx512bw"),
        ("BF16", "avx2 avx512f avx512bw", "avx512_bf16"),
        ("FP8", "avx2 avx512_bf16", "avx512vbmi"),
    ],
)
def test_each_method_needs_its_cpu_features(
    tmp_path: Path, method: str, flags: str, lacking: str
) -> None:
    args = ["--kt-method", method, "--kt-weight-path", "/w"]
    with pytest.raises(EngineError, match=f"needs a CPU with {lacking},"):
        engine(tmp_path, flags).command(entry(args), "/m", port=1)
    full = f"{flags} {lacking}"
    assert method in engine(tmp_path, full).command(entry(args), "/m", port=1)


def test_unreadable_cpuinfo_and_undocumented_methods_are_not_checked(tmp_path: Path) -> None:
    unknown = KTransformersEngine(
        command_prefix=["python", "-m", "sglang.launch_server"], cpuinfo=tmp_path / "missing"
    )
    assert "AMXINT4" in unknown.command(entry(), "/m", port=1)
    per_channel = ["--kt-method", "FP8_PERCHANNEL", "--kt-weight-path", "/w"]
    assert "FP8_PERCHANNEL" in engine(tmp_path, "avx2").command(entry(per_channel), "/m", port=1)


def test_owned_flags_stay_owned(tmp_path: Path) -> None:
    with pytest.raises(EngineError, match="may not set --port"):
        engine(tmp_path).command(entry([*KT, "--port", "1"]), "/m", port=1)


def test_platforms(tmp_path: Path) -> None:
    ok = engine(tmp_path)
    assert ok.platform_problem("linux", "x86_64") is None
    for system, machine in (("win32", "AMD64"), ("darwin", "arm64"), ("linux", "aarch64")):
        problem = ok.platform_problem(system, machine)
        assert problem is not None and "Linux x86-64 only" in problem
    assert engine(tmp_path, "fpu sse2").platform_problem("linux", "x86_64") == (
        "KTransformers needs a CPU with AVX2"
    )


def test_cpu_flags_and_flag_values(tmp_path: Path) -> None:
    assert cpu_flags(cpuinfo(tmp_path, "avx2 amx_tile")) == {"avx2", "amx_tile"}
    assert cpu_flags(tmp_path / "missing") is None
    assert flag_value(["--a", "1", "--a=2"], "--a") == "2"
    assert flag_value(["--a"], "--a") is None


def test_readiness_launcher_and_key(tmp_path: Path) -> None:
    kt = KTransformersEngine(cpuinfo=cpuinfo(tmp_path, "avx2"))
    assert kt.health_path == "/health"
    assert kt.script is None and kt.module_args == ("-m", "sglang.launch_server")
    assert kt.find_launcher() is None  # no interpreter configured, no console-script lookup
    assert kt.key_args("k", tmp_path / "server.log")[0] == "--config"
    env = kt.server_env("k", base={"PATH": "/bin"})
    assert env["SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION"] == "false"  # no generation per poll
    assert env["SGLANG_USE_MODELSCOPE"] == "false" and env["HF_HUB_OFFLINE"] == "1"
