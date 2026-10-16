"""`ssw doctor` (SPEC §5.1): parsers for every OS from synthetic fixtures, tier rules, the
managed profile block, collect() with a fake runner, and one real smoke run on this machine."""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import tomllib
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import doctor, net

MIB = doctor.MIB
GB = doctor.GB

REQUIRED_KEYS = {
    "schema",
    "created",
    "os",
    "cpu",
    "ram_gb",
    "gpus",
    "disk",
    "engines",
    "sqlite",
    "python",
    "profile",
    "warnings",
}
GPU_KEYS = {
    "index",
    "name",
    "backend",
    "vram_gb",
    "vram_free_gb",
    "driver",
    "compute_capability",
    "native_fp8",
    "native_fp4",
    "unified_memory",
}


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def cuda(index: int, mib: int, cc: str = "8.6", cuda_version: str | None = None) -> dict[str, Any]:
    fp8, fp4 = doctor.cuda_precision_flags(cc)
    return {
        "index": index,
        "name": f"Example GPU {round(mib / 1024)}G",
        "backend": "cuda",
        "vram_gb": round(mib * MIB / GB, 2),
        "vram_free_gb": None,
        "driver": "580.00",
        "compute_capability": cc,
        "native_fp8": fp8,
        "native_fp4": fp4,
        "unified_memory": False,
        "cuda_version": cuda_version,
    }


def metal(ram_gb: float) -> dict[str, Any]:
    return doctor.metal_gpu("Example M-series", int(ram_gb * GB))


def make_report(
    gpus: list[dict[str, Any]],
    ram_gb: float | None = 64.0,
    system: str = "Linux",
    arch: str = "x86_64",
    engines: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": 1,
        "created": "2026-10-16T05:00:00+00:00",
        "os": {"system": system, "arch": arch, "wsl": False},
        "cpu": {"model": "Example Processor", "arch": arch, "features": {}},
        "ram_gb": ram_gb,
        "gpus": gpus,
        "disk": {},
        "engines": engines or {"llamacpp": {"installed": True}},
        "sqlite": {"version": "3.53.1", "wal_safe": True},
        "python": {"version": "3.12.15"},
        "warnings": [],
    }


def synthetic_report() -> dict[str, Any]:
    report = make_report([cuda(i, 49140) for i in range(4)], ram_gb=515.4)
    report["os"] = {
        "system": "Linux",
        "release": "6.8.0-example",
        "version": "#1 SMP",
        "machine": "x86_64",
        "arch": "x86_64",
        "name": "Example Linux 1.0",
        "wsl": False,
    }
    report["cpu"] = {
        "model": "Example 32-Core Processor",
        "arch": "x86_64",
        "cores_physical": 32,
        "cores_logical": 64,
        "features": doctor.cpu_features(["avx", "avx2", "fma", "avx512f"], "x86_64"),
    }
    report["ram_available_gb"] = 400.0
    report["gpus"][1]["vram_free_gb"] = 12.5
    report["disk"] = {
        "models_dir": {"path": "/srv/models", "free_gb": 900.0, "total_gb": 2000.0},
        "project": {"path": "/srv/work", "free_gb": 900.0, "total_gb": 2000.0},
    }
    report["engines"] = {
        "llamacpp": {
            "installed": True,
            "version": "0.7.0",
            "build": "b11669",
            "path": "/opt/llama/llama-server",
        },
        "ollama": {
            "installed": True,
            "running": True,
            "version": "0.40.5",
            "url": "http://127.0.0.1:11434",
            "local": True,
            "listens_on_all_interfaces": False,
            "path": "/usr/local/bin/ollama",
        },
        "vllm": {"installed": False, "version": None, "path": None},
        "sglang": {"installed": False, "version": None, "path": None},
        "mlx": {"installed": False, "version": None, "path": None},
        "docker": {"installed": True, "running": False, "version": None, "path": "/opt/docker"},
    }
    report["python"] = {
        "version": "3.12.15",
        "implementation": "CPython",
        "executable": "/opt/python/bin/python3",
    }
    report["problems"] = []
    report["profile"] = doctor.recommend(report)
    return report


Response = tuple[int, str, str] | Exception


class FakeRunner:
    """Answers commands by prefix ("<program name> <args...>"); unknown commands exit 127."""

    def __init__(self, responses: dict[str, Response] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []

    def __call__(self, argv: list[str], timeout: float) -> tuple[int, str, str]:
        key = " ".join([Path(argv[0]).name, *argv[1:]])
        self.calls.append(key)
        for prefix, response in self.responses.items():
            if key.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        return 127, "", f"{argv[0]}: not found"


class MockEgress(net.Egress):
    """The real egress layer (policy and audit) over an in-memory transport."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        super().__init__(net.EgressPolicy(offline=True))
        self.handler = handler

    def client(self, purpose: str, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(self.handler)
        return super().client(purpose, **kwargs)


def ollama_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/version":
        return httpx.Response(200, json={"version": "0.40.5"})
    return httpx.Response(404)


def refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected request to {request.url}")


# --------------------------------------------------------------------------------------------
# Command output decoding and the default runner
# --------------------------------------------------------------------------------------------


def test_decode_output_handles_utf16_without_bom_and_utf8() -> None:
    text = "WSL version: 2.4.13.0\r\nKernel version: 6.6.87.2-1\r\n"
    assert doctor.decode_output(text.encode("utf-16-le")) == text
    assert doctor.decode_output(b"\xff\xfe" + text.encode("utf-16-le")) == text
    assert doctor.decode_output("café 1.0\n".encode()) == "café 1.0\n"
    assert doctor.decode_output(b"") == ""


def test_run_command_returns_code_and_output() -> None:
    script = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    code, out, err = doctor.run_command([sys.executable, "-c", script], 30.0)
    assert (code, out.strip(), err.strip()) == (3, "out", "err")


def test_run_command_reports_missing_program(tmp_path: Path) -> None:
    code, out, err = doctor.run_command([str(tmp_path / "no-such-program")], 5.0)
    assert code == 127 and out == "" and err


def test_find_tool_never_runs_a_program_from_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filename = "example-tool.exe" if sys.platform == "win32" else "example-tool"
    project, trusted = tmp_path / "project", tmp_path / "bin"
    for directory in (project, trusted):
        directory.mkdir()
        (directory / filename).write_bytes(b"#!/bin/sh\n")
        (directory / filename).chmod(0o755)
    monkeypatch.chdir(project)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", "bin", str(trusted)]))
    found = doctor._find_tool("example-tool")
    assert found is not None and Path(found).parent == trusted
    monkeypatch.setenv("PATH", os.pathsep.join([".", ""]))
    assert doctor._find_tool("example-tool") is None


def test_run_command_times_out() -> None:
    started = time.monotonic()
    code, _out, err = doctor.run_command([sys.executable, "-c", "import time; time.sleep(30)"], 0.5)
    assert code == 124 and "timed out" in err
    assert time.monotonic() - started < 15


# --------------------------------------------------------------------------------------------
# OS and CPU parsers
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("machine", "arch"),
    [
        ("AMD64", "x86_64"),
        ("x86_64", "x86_64"),
        ("aarch64", "arm64"),
        ("ARM64", "arm64"),
        ("arm64", "arm64"),
        ("armv7l", "arm"),
        ("i686", "x86"),
        ("riscv64", "riscv64"),
        ("", "unknown"),
    ],
)
def test_normalize_arch(machine: str, arch: str) -> None:
    assert doctor.normalize_arch(machine) == arch


def test_is_wsl() -> None:
    assert doctor.is_wsl("6.6.87.2-example-standard-WSL2\n", {})
    assert doctor.is_wsl("4.4.0-19041-Example", {"WSL_DISTRO_NAME": "Ubuntu"})
    assert doctor.is_wsl("4.4.0-19041-Example", {}, interop=True)
    assert not doctor.is_wsl("6.8.0-45-generic", {})
    assert not doctor.is_wsl(None, {})


def x86_cpuinfo(flags: str, packages: int = 2, cores: int = 2, threads: int = 2) -> str:
    blocks = []
    number = 0
    for package in range(packages):
        for _thread in range(threads):
            for core in range(cores):
                blocks.append(
                    f"processor\t: {number}\n"
                    "vendor_id\t: ExampleVendor\n"
                    "model name\t: Example 4-Core Processor\n"
                    f"physical id\t: {package}\n"
                    f"core id\t\t: {core}\n"
                    f"flags\t\t: {flags}\n"
                    "vmx flags\t: vnmi preemption_timer invvpid ept_x_only\n"
                    "bugs\t\t: spectre_v1\n"
                )
                number += 1
    return "\n".join(blocks)


PROC_CPUINFO_ARM64 = """\
processor\t: 0
BogoMIPS\t: 50.00
Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics asimddp i8mm bf16
CPU implementer\t: 0x41
CPU architecture: 8
CPU part\t: 0xd0c

processor\t: 1
BogoMIPS\t: 50.00
Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics asimddp i8mm bf16
CPU implementer\t: 0x41
CPU architecture: 8
CPU part\t: 0xd0c
"""


def test_parse_proc_cpuinfo_x86() -> None:
    flags = "fpu sse2 avx avx2 fma f16c avx512f avx512_vnni avx512_bf16 amx_tile amx_int8"
    parsed = doctor.parse_proc_cpuinfo(x86_cpuinfo(flags))
    assert parsed["model"] == "Example 4-Core Processor"
    assert parsed["cores_logical"] == 8
    assert parsed["cores_physical"] == 4
    assert "vnmi" not in parsed["flags"]  # the "vmx flags" line is not the feature list
    features = doctor.cpu_features(parsed["flags"], "x86_64")
    assert features["avx2"] is True and features["avx512f"] is True
    assert features["avx512_bf16"] is True and features["amx"] is True
    assert features["avx_vnni"] is False
    assert features["neon"] is False and features["sve"] is False


def test_cpu_features_x86_without_avx512_or_amx() -> None:
    features = doctor.cpu_features(["avx", "avx2", "fma", "amx_tile"], "x86_64")
    assert features["avx2"] is True
    assert features["avx512f"] is False and features["avx512_bf16"] is False
    assert features["amx"] is False  # tiles without INT8 or BF16 support


def test_parse_proc_cpuinfo_arm64() -> None:
    parsed = doctor.parse_proc_cpuinfo(PROC_CPUINFO_ARM64)
    assert parsed["model"] is None
    assert parsed["cores_logical"] == 2
    assert parsed["cores_physical"] is None
    features = doctor.cpu_features(parsed["flags"], "arm64")
    assert features["neon"] is True and features["dotprod"] is True
    assert features["i8mm"] is True and features["bf16"] is True
    assert features["sve"] is False and features["sme"] is False
    assert features["avx2"] is False and features["amx"] is False


def test_cpu_features_unknown_flags_are_none_for_the_native_architecture() -> None:
    features = doctor.cpu_features(None, "x86_64")
    assert features["avx2"] is None and features["amx"] is None
    assert features["neon"] is False


def test_parse_lscpu_model() -> None:
    text = "Architecture:  aarch64\nVendor ID:     ExampleVendor\nModel name:    Example-X1\n"
    assert doctor.parse_lscpu_model(text) == "Example-X1"
    assert doctor.parse_lscpu_model("Model name: -\n") is None


def test_parse_meminfo() -> None:
    text = "MemTotal:       16303364 kB\nMemFree:         1234567 kB\nMemAvailable:   12000000 kB\n"
    assert doctor.parse_meminfo(text) == (16303364 * 1024, 12000000 * 1024)
    old = "MemTotal: 2048 kB\nMemFree: 1000 kB\nBuffers: 200 kB\nCached: 300 kB\n"
    assert doctor.parse_meminfo(old) == (2048 * 1024, 1500 * 1024)
    assert doctor.parse_meminfo("") == (None, None)


@pytest.mark.parametrize(
    ("arch", "build", "present", "expected"),
    [
        (
            "x86_64",
            26100,
            {39, 40},
            {"avx": True, "avx2": True, "avx512f": False, "amx": None, "fma": None, "neon": False},
        ),
        ("x86_64", 17763, set(), {"avx": None, "avx2": None, "avx512f": None}),
        ("x86_64", None, {39}, {"avx": True, "avx2": None}),
        (
            "arm64",
            26100,
            {29, 43, 66},
            {"neon": True, "dotprod": True, "i8mm": True, "bf16": False, "sme": None, "avx": False},
        ),
        ("arm64", 22631, {29}, {"neon": True, "dotprod": False, "i8mm": None, "bf16": None}),
    ],
)
def test_windows_cpu_features(
    arch: str, build: int | None, present: set[int], expected: dict[str, bool | None]
) -> None:
    features = doctor.windows_cpu_features(lambda flag: flag in present, arch, build)
    assert {key: features[key] for key in expected} == expected


def test_windows_cpu_features_survives_a_failing_call() -> None:
    def broken(flag: int) -> bool:
        raise OSError("unavailable")

    assert doctor.windows_cpu_features(broken, "x86_64", 26100)["avx2"] is None


def test_count_core_records() -> None:
    def record(relationship: int, size: int = 48) -> bytes:
        return struct.pack("<II", relationship, size) + bytes(size - 8)

    data = record(0) * 3 + record(2, 56) + record(0, 80) + b"\x00\x00"
    assert doctor.count_core_records(data) == 4
    assert doctor.count_core_records(b"") == 0
    assert doctor.count_core_records(struct.pack("<II", 0, 0)) == 0


SYSCTL_MACOS_ARM64 = """\
hw.ncpu: 12
hw.physicalcpu: 12
hw.logicalcpu: 12
hw.memsize: 137438953472
hw.optional.arm.FEAT_DotProd: 1
hw.optional.arm.FEAT_I8MM: 1
hw.optional.arm.FEAT_BF16: 1
hw.optional.arm.FEAT_SME: 0
hw.optional.arm.AdvSIMD: 1
hw.optional.neon: 1
hw.optional.arm64: 1
machdep.cpu.brand_string: Example M-series
machdep.cpu.core_count: 12
"""

SYSCTL_MACOS_X86 = """\
hw.physicalcpu: 8
hw.logicalcpu: 16
hw.memsize: 34359738368
hw.optional.avx1_0: 1
hw.optional.avx2_0: 1
hw.optional.fma: 1
hw.optional.f16c: 1
hw.optional.avx512f: 0
machdep.cpu.brand_string: Example 8-Core Processor
machdep.cpu.features: FPU VME SSE SSE2 AVX1.0 FMA F16C
machdep.cpu.leaf7_features: SMEP ERMS AVX2 BMI2
"""

VM_STAT = """\
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               10000.
Pages active:                            200000.
Pages inactive:                          150000.
Pages speculative:                         5000.
Pages wired down:                         80000.
Pages purgeable:                           3000.
"""


def test_macos_cpu_arm64() -> None:
    values = doctor.parse_sysctl(SYSCTL_MACOS_ARM64)
    assert values["hw.memsize"] == "137438953472"
    cpu = doctor.macos_cpu(values, "arm64")
    assert cpu["model"] == "Example M-series"
    assert (cpu["cores_physical"], cpu["cores_logical"]) == (12, 12)
    features = cpu["features"]
    assert features["neon"] is True and features["dotprod"] is True and features["i8mm"] is True
    assert features["bf16"] is True and features["sme"] is False
    assert features["sve"] is None  # no sysctl reports it
    assert features["avx2"] is False


def test_macos_cpu_reports_hardware_under_translation() -> None:
    cpu = doctor.macos_cpu(doctor.parse_sysctl(SYSCTL_MACOS_ARM64), "x86_64")
    assert cpu["arch"] == "arm64"


def test_macos_cpu_x86() -> None:
    cpu = doctor.macos_cpu(doctor.parse_sysctl(SYSCTL_MACOS_X86), "x86_64")
    assert cpu["arch"] == "x86_64"
    assert (cpu["cores_physical"], cpu["cores_logical"]) == (8, 16)
    features = cpu["features"]
    assert features["avx"] is True and features["avx2"] is True and features["fma"] is True
    assert features["avx512f"] is False and features["avx512_vnni"] is False
    assert features["avx512_bf16"] is None and features["amx"] is None
    assert features["neon"] is False


def test_parse_vm_stat() -> None:
    assert doctor.parse_vm_stat(VM_STAT) == (10000 + 150000 + 5000) * 16384
    assert doctor.parse_vm_stat("no statistics here") is None


# --------------------------------------------------------------------------------------------
# GPU parsers
# --------------------------------------------------------------------------------------------

CUDA_SMI_ONE = "0, Example GPU 24G, 24576, 23000, 580.00, 8.9\n"
CUDA_SMI_FOUR = (
    "0, Example GPU 48G, 49140, 20000, 580.00, 8.6\n"
    "1, Example GPU 48G, 46068, [N/A], 580.00, 8.6\n"
    "2, Example GPU 48G, [N/A], [N/A], [N/A], [N/A]\n"
    "3, Example GPU 48G, 49140, 5000, 580.00, 8.6\n"
)


def test_parse_cuda_smi_csv_no_gpus() -> None:
    assert doctor.parse_cuda_smi_csv("") == []
    assert doctor.parse_cuda_smi_csv("No devices were found\n") == []


def test_parse_cuda_smi_csv_one_gpu() -> None:
    (gpu,) = doctor.parse_cuda_smi_csv(CUDA_SMI_ONE)
    assert set(gpu) == GPU_KEYS
    assert gpu["index"] == 0 and gpu["name"] == "Example GPU 24G" and gpu["backend"] == "cuda"
    assert gpu["vram_gb"] == round(24576 * MIB / GB, 2) == 25.77
    assert gpu["vram_free_gb"] == round(23000 * MIB / GB, 2)
    assert gpu["driver"] == "580.00" and gpu["compute_capability"] == "8.9"
    assert gpu["native_fp8"] is True and gpu["native_fp4"] is False
    assert gpu["unified_memory"] is False


def test_parse_cuda_smi_csv_four_gpus_with_missing_fields() -> None:
    gpus = doctor.parse_cuda_smi_csv(CUDA_SMI_FOUR.replace("\n", "\r\n"))
    assert [gpu["index"] for gpu in gpus] == [0, 1, 2, 3]
    assert gpus[0]["vram_gb"] == 51.53
    assert gpus[1]["vram_gb"] == 48.31 and gpus[1]["vram_free_gb"] is None
    assert gpus[2]["vram_gb"] is None and gpus[2]["driver"] is None
    assert gpus[2]["compute_capability"] is None and gpus[2]["native_fp8"] is None
    assert gpus[2]["unified_memory"] is None
    assert all(gpu["native_fp8"] is False for gpu in gpus if gpu["compute_capability"])


def test_parse_cuda_smi_csv_without_compute_capability_column() -> None:
    (gpu,) = doctor.parse_cuda_smi_csv("0, Example GPU 16G, 16384, 16000, 470.00\n")
    assert gpu["compute_capability"] is None and gpu["native_fp8"] is None
    assert gpu["vram_gb"] == round(16384 * MIB / GB, 2)


@pytest.mark.parametrize(
    ("cc", "fp8", "fp4"),
    [
        ("7.5", False, False),
        ("8.0", False, False),
        ("8.6", False, False),
        ("8.7", False, False),
        ("8.9", True, False),
        ("9.0", True, False),
        ("10.0", True, True),
        ("10.3", True, True),
        ("12.0", True, True),
        ("12.1", True, True),
        (None, None, None),
        ("[N/A]", None, None),
        ("unknown", None, None),
    ],
)
def test_cuda_precision_flags(cc: str | None, fp8: bool | None, fp4: bool | None) -> None:
    assert doctor.cuda_precision_flags(cc) == (fp8, fp4)


def test_parse_cuda_version() -> None:
    text = "DRIVER version      : 580.00\nCUDA Version        : 13.0\n"
    assert doctor.parse_cuda_version(text) == "13.0"
    assert doctor.parse_cuda_version("| Driver Version: 550.00   CUDA Version: 12.4 |") == "12.4"
    assert doctor.parse_cuda_version("nothing") is None


def query(fields: str) -> str:
    """The FakeRunner key of a --query-gpu call with these fields."""
    return f"nvidia-smi --query-gpu={fields} "


def test_cuda_query_falls_back_to_older_field_sets() -> None:
    invalid = (2, 'Field "compute_cap" is not a valid field to query.\n', "")
    current, kmd, legacy = doctor._CUDA_QUERIES
    runner = FakeRunner(
        {
            query(current): invalid,
            query(kmd): invalid,
            query(legacy): (0, "0, Example GPU 16G, 16384, 16000, 470.00\n", ""),
            "nvidia-smi --version": (2, "", "unknown option"),
        }
    )
    probe = doctor._Probe(runner, 1.0)
    gpus = doctor._cuda_gpus("/fake/bin/nvidia-smi", probe)
    assert len(gpus) == 1 and gpus[0]["cuda_version"] is None
    assert len([call for call in runner.calls if "--query-gpu" in call]) == 3
    assert probe.warnings == []


def test_cuda_query_does_not_retry_after_a_timeout() -> None:
    runner = FakeRunner({"nvidia-smi --query-gpu": (124, "", "timed out")})
    probe = doctor._Probe(runner, 1.0)
    assert doctor._cuda_gpus("/fake/bin/nvidia-smi", probe) == []
    assert len(runner.calls) == 1
    assert probe.warnings and "timed out" in probe.warnings[0]


def test_parse_rocm_smi_json() -> None:
    data = {
        "card1": {
            "Card Series": "Example Accelerator 64G",
            "GFX Version": "gfx90a",
            "VRAM Total Memory (B)": "68702699520",
            "VRAM Total Used Memory (B)": "10737418240",
        },
        "card0": {
            "Card Series": "Example Accelerator 192G",
            "Card Model": "0x1234",
            "Card Vendor": "Example Vendor",
            "GFX Version": "gfx942",
            "VRAM Total Memory (B)": "206141652992",
            "VRAM Total Used Memory (B)": "6141652992",
        },
        "system": {"Driver version": "6.10.5"},
    }
    gpus = doctor.parse_rocm_smi_json("WARNING: example notice\n" + json.dumps(data))
    assert [gpu["index"] for gpu in gpus] == [0, 1]
    assert set(gpus[0]) >= GPU_KEYS
    assert gpus[0]["name"] == "Example Accelerator 192G" and gpus[0]["backend"] == "rocm"
    assert gpus[0]["vram_gb"] == 206.14 and gpus[0]["vram_free_gb"] == 200.0
    assert gpus[0]["driver"] == "6.10.5" and gpus[0]["arch"] == "gfx942"
    assert gpus[0]["native_fp8"] is None
    with pytest.raises(ValueError):
        doctor.parse_rocm_smi_json("no json at all")


def test_metal_gpu_uses_unified_memory() -> None:
    gpu = doctor.metal_gpu("Example M-series", 137438953472)
    assert set(gpu) >= GPU_KEYS
    assert gpu["backend"] == "metal" and gpu["unified_memory"] is True
    assert gpu["vram_gb"] == 137.44


# --------------------------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------------------------

LLAMA_VERSION_CURRENT = (
    "load_backend: loaded CPU backend from example\r\n"
    "version: 0.7.0 (build 11669, commit abc1234)\r\n"
    "built with Clang 20.1.8 for Windows x86_64\r\n"
)
LLAMA_VERSION_LEGACY = "version: 6000 (deadbee)\nbuilt with cc 13.2.0 for x86_64-linux-gnu\n"


def test_parse_llama_server_version_current_format() -> None:
    assert doctor.parse_llama_server_version(LLAMA_VERSION_CURRENT) == {
        "version": "0.7.0",
        "build": "b11669",
        "build_number": 11669,
        "commit": "abc1234",
        "target": "Windows x86_64",
    }


def test_parse_llama_server_version_legacy_format() -> None:
    parsed = doctor.parse_llama_server_version(LLAMA_VERSION_LEGACY)
    assert parsed is not None
    assert parsed["version"] == "b6000" and parsed["build"] == "b6000"
    assert parsed["commit"] == "deadbee" and parsed["target"] == "x86_64-linux-gnu"
    assert doctor.parse_llama_server_version("error: unknown argument") is None


@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_find_llama_server_prefers_the_newest_managed_build(tmp_path: Path, system: str) -> None:
    name = "llama-server.exe" if system == "Windows" else "llama-server"
    builds = tmp_path / "engines" / "llamacpp"
    (builds / "b100").mkdir(parents=True)
    (builds / "b100" / name).write_bytes(b"")
    (builds / "b200" / "bin").mkdir(parents=True)
    (builds / "b200" / "bin" / name).write_bytes(b"")
    (builds / "notes").mkdir()
    exe, source = doctor.find_llama_server(tmp_path / "engines", system)
    assert source == "managed"
    assert exe == builds / "b200" / "bin" / name


def test_find_llama_server_falls_back_to_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "_find_tool", lambda name: f"/opt/bin/{name}")
    exe, source = doctor.find_llama_server(tmp_path / "missing", "Linux")
    assert (exe, source) == (Path("/opt/bin/llama-server"), "path")
    monkeypatch.setattr(doctor, "_find_tool", lambda name: None)
    assert doctor.find_llama_server(tmp_path / "missing", "Linux") == (None, None)


def test_parse_ollama_version() -> None:
    assert doctor.parse_ollama_version("ollama version is 0.40.5\n") == {
        "server": "0.40.5",
        "client": None,
    }
    no_server = (
        "Warning: could not connect to a running Ollama instance\n"
        "Warning: client version is 0.40.5\n"
    )
    assert doctor.parse_ollama_version(no_server) == {"server": None, "client": "0.40.5"}
    both = "ollama version is 0.40.5\nWarning: client version is 0.40.4\n"
    assert doctor.parse_ollama_version(both) == {"server": "0.40.5", "client": "0.40.4"}


@pytest.mark.parametrize(
    ("value", "url", "local", "all_interfaces"),
    [
        (None, "http://127.0.0.1:11434", True, False),
        ("", "http://127.0.0.1:11434", True, False),
        ("0.0.0.0:11434", "http://127.0.0.1:11434", True, True),
        ("0.0.0.0", "http://127.0.0.1:11434", True, True),
        ("[::]:11434", "http://[::1]:11434", True, True),
        (":8000", "http://127.0.0.1:8000", True, True),
        ("127.0.0.1:9999", "http://127.0.0.1:9999", True, False),
        ("  '127.0.0.1:9999'  ", "http://127.0.0.1:9999", True, False),
        ("localhost", "http://localhost:11434", True, False),
        ("http://localhost", "http://localhost:80", True, False),
        ("https://127.0.0.1/ollama", "https://127.0.0.1:443/ollama", True, False),
        ("localhost:notaport", "http://localhost:11434", True, False),
        ("192.0.2.10", "http://192.0.2.10:11434", False, False),
        ("https://models.example.net:8443", "https://models.example.net:8443", False, False),
    ],
)
def test_ollama_endpoint(value: str | None, url: str, local: bool, all_interfaces: bool) -> None:
    assert doctor.ollama_endpoint(value) == {
        "url": url,
        "local": local,
        "all_interfaces": all_interfaces,
    }


@pytest.mark.parametrize(
    ("value", "local"),
    [
        ("unix:///var/run/docker.sock", True),
        ("npipe:////./pipe/docker_engine", True),
        ("tcp://127.0.0.1:2375", True),
        ("tcp://localhost:2375", True),
        ("tcp://192.0.2.20:2376", False),
        ("ssh://user@build.example.net", False),
        ("build.example.net:2375", False),
    ],
)
def test_is_local_docker_host(value: str, local: bool) -> None:
    assert doctor.is_local_docker_host(value) is local


def test_parse_wsl_output() -> None:
    assert doctor.parse_wsl_version("WSL version: 2.4.13.0\nKernel version: 6.6.87.2-1\n") == (
        "2.4.13.0"
    )
    assert doctor.parse_wsl_version("WSL-Version: 2.4.13.0\n") == "2.4.13.0"
    assert doctor.parse_wsl_version("usage: wsl.exe [Argument]\n") is None
    assert doctor.parse_wsl_list("Ubuntu-24.04\r\nExampleDistro\r\n\r\n") == [
        "Ubuntu-24.04",
        "ExampleDistro",
    ]


def test_version_from_dist_info() -> None:
    assert doctor.version_from_dist_info("mlx_lm-0.32.2.dist-info", "mlx-lm") == "0.32.2"
    assert doctor.version_from_dist_info("vllm-0.31.0.dist-info", "vllm") == "0.31.0"
    assert doctor.version_from_dist_info("vllm_extra-1.0.dist-info", "vllm") is None
    assert doctor.version_from_dist_info("vllm-0.31.0.egg-info", "vllm") is None


@pytest.mark.parametrize(
    ("script", "site"),
    [
        ("bin/example-engine", "lib/python3.12/site-packages"),
        ("Scripts/example-engine.exe", "Lib/site-packages"),
    ],
)
def test_dist_version_reads_the_engine_environment(tmp_path: Path, script: str, site: str) -> None:
    env = tmp_path / "venv"
    (env / script).parent.mkdir(parents=True)
    (env / script).write_bytes(b"")
    (env / site / "example_engine-1.2.3.dist-info").mkdir(parents=True)
    (env / site / "other-9.9.dist-info").mkdir()
    assert doctor.dist_version("example-engine", str(env / script)) == "1.2.3"
    assert doctor.dist_version("example-engine", None) is None


# --------------------------------------------------------------------------------------------
# recommend()
# --------------------------------------------------------------------------------------------

TIER_CASES = [
    pytest.param(8.3, [], "cpu", id="cpu-only-8gb-laptop"),
    pytest.param(16.8, [], "cpu", id="16gb-laptop"),
    pytest.param(32.0, [cuda(0, 8188, "8.9")], "cpu", id="1x8gb"),
    pytest.param(64.0, [cuda(0, 12288)], "t1", id="1x12gb"),
    pytest.param(64.0, [cuda(0, 24576, "8.9")], "t1", id="1x24gb"),
    pytest.param(128.0, [cuda(0, 24576), cuda(1, 24576)], "t1", id="2x24gb"),
    pytest.param(512.0, [cuda(i, 49140) for i in range(4)], "t2", id="4x48gb"),
    pytest.param(
        512.0,
        [cuda(0, 49140), cuda(1, 46068), cuda(2, 49140), cuda(3, 49140)],
        "t2",
        id="4x48gb-one-with-ecc",
    ),
    pytest.param(256.0, [cuda(0, 81559, "9.0")], "t2", id="1x80gb"),
    pytest.param(1024.0, [cuda(i, 81559, "9.0") for i in range(3)], "t3", id="3x80gb"),
    pytest.param(2048.0, [cuda(i, 81559, "9.0") for i in range(8)], "t3", id="8x80gb"),
    pytest.param(68.72, [metal(68.72)], "t1", id="arm64-macos-64gb-unified"),
    pytest.param(137.44, [metal(137.44)], "t2", id="arm64-macos-128gb-unified"),
]


@pytest.mark.parametrize(("ram_gb", "gpus", "tier"), TIER_CASES)
def test_recommend_tier(ram_gb: float, gpus: list[dict[str, Any]], tier: str) -> None:
    unified = any(gpu["backend"] == "metal" for gpu in gpus)
    system, arch = ("Darwin", "arm64") if unified else ("Linux", "x86_64")
    profile = doctor.recommend(make_report(gpus, ram_gb, system, arch))
    assert profile["tier"] == tier
    assert profile["max_gb"] == 8.0
    assert profile["context"] == {"router": 4096, "embeddings": 2048, "fim": 4096}
    assert profile["ram_gb"] == ram_gb
    assert profile["reason"]
    assert not any("below the 8 GB minimum" in note for note in profile["notes"])


def test_recommend_reports_totals_in_gb() -> None:
    profile = doctor.recommend(make_report([cuda(i, 49140) for i in range(4)], 512.0))
    assert profile["gpu_vram_total_gb"] == 206.12  # 4 x 51.53 GB, sold as "48 GB" (GiB) cards
    assert "192.0 GiB" in profile["reason"]
    unified = doctor.recommend(make_report([metal(137.44)], 137.44, "Darwin", "arm64"))
    assert unified["gpu_vram_total_gb"] == 137.44 and unified["gpu_usable_gb"] == 103.08
    assert any("75%" in note for note in unified["notes"])


def test_recommend_notes_low_and_unknown_ram() -> None:
    low = doctor.recommend(make_report([], 6.4))
    assert low["tier"] == "cpu"
    assert any("below the 8 GB minimum" in note for note in low["notes"])
    unknown = doctor.recommend(make_report([], None))
    assert any("RAM size unknown" in note for note in unknown["notes"])


def test_recommend_skips_gpus_without_a_memory_size() -> None:
    gpu = cuda(0, 24576)
    gpu["vram_gb"] = None
    profile = doctor.recommend(make_report([gpu], 32.0))
    assert profile["tier"] == "cpu"
    assert any("did not report its memory size" in note for note in profile["notes"])


@pytest.mark.parametrize(
    ("gpus", "system", "arch", "expected"),
    [
        ([], "Linux", "x86_64", ["gguf:q4_k_m"]),
        ([cuda(0, 8188, "8.9")], "Linux", "x86_64", ["gguf:q4_k_m"]),
        (
            [cuda(i, 49140, "8.6") for i in range(4)],
            "Linux",
            "x86_64",
            ["gguf:q4_k_m", "awq:int4", "gptq:int4"],
        ),
        (
            [cuda(0, 24576, "8.9")],
            "Linux",
            "x86_64",
            ["gguf:q4_k_m", "awq:int4", "gptq:int4", "fp8"],
        ),
        (
            [cuda(0, 32607, "12.0")],
            "Linux",
            "x86_64",
            ["gguf:q4_k_m", "awq:int4", "gptq:int4", "fp8", "fp4"],
        ),
        ([metal(137.44)], "Darwin", "arm64", ["gguf:q4_k_m", "mlx:4bit"]),
    ],
)
def test_recommend_quant_preference(
    gpus: list[dict[str, Any]], system: str, arch: str, expected: list[str]
) -> None:
    profile = doctor.recommend(make_report(gpus, 64.0, system, arch))
    assert profile["quant_preference"] == expected


def test_recommend_engines_preferred() -> None:
    four = [cuda(i, 49140) for i in range(4)]
    linux = doctor.recommend(make_report(four, 512.0))
    assert linux["engines_preferred"] == ["llamacpp", "vllm", "sglang"]
    with_ollama = doctor.recommend(
        make_report(
            four, 512.0, engines={"llamacpp": {"installed": True}, "ollama": {"installed": True}}
        )
    )
    assert with_ollama["engines_preferred"] == ["llamacpp", "vllm", "sglang", "ollama"]
    mac = doctor.recommend(make_report([metal(137.44)], 137.44, "Darwin", "arm64"))
    assert mac["engines_preferred"] == ["llamacpp", "mlx"]
    cpu_only = doctor.recommend(make_report([], 16.0))
    assert cpu_only["engines_preferred"] == ["llamacpp"]


def test_recommend_engines_on_windows_depend_on_wsl_or_containers() -> None:
    gpus = [cuda(0, 24576, "8.9")]
    engines = {"llamacpp": {"installed": True}, "wsl": {"installed": True}}
    with_wsl = doctor.recommend(make_report(gpus, 64.0, "Windows", engines=engines))
    assert with_wsl["engines_preferred"] == ["llamacpp", "vllm", "sglang"]
    assert any("WSL2" in note for note in with_wsl["notes"])
    without = doctor.recommend(make_report(gpus, 64.0, "Windows"))
    assert without["engines_preferred"] == ["llamacpp"]
    assert any("neither found" in note for note in without["notes"])


def test_recommend_notes_an_old_cuda_driver_for_vllm() -> None:
    old = doctor.recommend(make_report([cuda(0, 49140, "8.6", cuda_version="12.4")], 64.0))
    assert any("CUDA 13" in note and "12.4" in note for note in old["notes"])
    new = doctor.recommend(make_report([cuda(0, 49140, "8.6", cuda_version="13.2")], 64.0))
    assert not any("CUDA 13" in note for note in new["notes"])


def test_recommend_notes_exposed_or_remote_ollama() -> None:
    engines = {
        "llamacpp": {"installed": True},
        "ollama": {"installed": True, "local": True, "listens_on_all_interfaces": True},
    }
    exposed = doctor.recommend(make_report([], 16.0, engines=engines))
    assert any("all network interfaces" in note for note in exposed["notes"])
    engines["ollama"] = {"installed": True, "local": False, "listens_on_all_interfaces": False}
    remote = doctor.recommend(make_report([], 16.0, engines=engines))
    assert any("did not contact it" in note for note in remote["notes"])


def test_recommend_handles_an_empty_report() -> None:
    profile = doctor.recommend({})
    assert profile["tier"] == "cpu"
    assert profile["engines_preferred"] == ["llamacpp"]
    json.dumps(profile)


# --------------------------------------------------------------------------------------------
# write_profile()
# --------------------------------------------------------------------------------------------

LOCAL_TOML = """\
# Machine-specific Stepswitch settings. Never commit this file (working rule 5).
schema = 1

[host]
# Models live on the big disk.
model_dir = "/srv/models"

[[endpoints]]
name = "local-llama"  # started by hand
url = "http://127.0.0.1:9931"
engine = "llamacpp"

[[endpoints]]
name = "local-ollama"
url = "http://127.0.0.1:11434"
engine = "ollama"
"""


def test_write_profile_keeps_the_rest_of_the_file_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / ".stepswitch" / "local.toml"
    path.parent.mkdir()
    path.write_text(LOCAL_TOML, encoding="utf-8")
    report = synthetic_report()

    doctor.write_profile(path, report)
    text = path.read_text(encoding="utf-8")
    assert text.startswith(LOCAL_TOML)
    data = tomllib.loads(text)
    assert data["schema"] == 1
    assert data["host"]["model_dir"] == "/srv/models"
    assert [entry["name"] for entry in data["endpoints"]] == ["local-llama", "local-ollama"]

    detected = data["host"]["detected"]
    assert detected["date"] == date(2026, 10, 16)
    assert detected["os"]["system"] == "Linux"
    assert detected["cpu"]["model"] == "Example 32-Core Processor"
    assert detected["cpu"]["features"] == ["avx", "avx2", "avx512f", "fma"]
    assert detected["ram_gb"] == 515.4
    assert [gpu["index"] for gpu in detected["gpus"]] == [0, 1, 2, 3]
    assert detected["gpus"][0]["native_fp8"] is False
    assert "vram_free_gb" not in detected["gpus"][1]
    assert detected["engines"]["llamacpp"] == {
        "installed": True,
        "version": "0.7.0",
        "build": "b11669",
    }
    assert detected["engines"]["ollama"] == {
        "installed": True,
        "running": True,
        "version": "0.40.5",
    }
    assert detected["sqlite"] == {"version": "3.53.1", "wal_safe": True}
    assert data["profile"]["tier"] == "t2"
    assert data["profile"]["context"] == {"router": 4096, "embeddings": 2048, "fim": 4096}
    assert data["profile"]["quant_preference"] == ["gguf:q4_k_m", "awq:int4", "gptq:int4"]
    for private in ("/opt/", "/usr/local/bin", "executable", "/srv/work"):
        assert private not in text

    doctor.write_profile(path, report)
    assert path.read_text(encoding="utf-8") == text

    path.write_text(text + "\n[extra]\nkeep = true\n", encoding="utf-8")
    report["gpus"] = []
    doctor.write_profile(path, report)
    updated = path.read_text(encoding="utf-8")
    assert updated.startswith(LOCAL_TOML)
    assert updated.rstrip().endswith("keep = true")
    assert updated.count(">>> doctor:") == 1
    data = tomllib.loads(updated)
    assert data["profile"]["tier"] == "cpu"
    assert data["host"]["detected"]["gpus"] == []
    assert data["extra"] == {"keep": True}


def test_write_profile_creates_the_file(tmp_path: Path) -> None:
    path = tmp_path / "state" / "local.toml"
    doctor.write_profile(path, synthetic_report())
    assert tomllib.loads(path.read_text(encoding="utf-8"))["profile"]["tier"] == "t2"


def test_write_profile_refuses_a_conflicting_file(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    original = '[profile]\ntier = "t3"\n'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        doctor.write_profile(path, synthetic_report())
    assert path.read_text(encoding="utf-8") == original


# --------------------------------------------------------------------------------------------
# format_report()
# --------------------------------------------------------------------------------------------


def test_format_report() -> None:
    text = doctor.format_report(synthetic_report())
    assert "\x1b" not in text
    assert "tier t2" in text
    assert "Example 32-Core Processor" in text
    assert "GPU 3" in text and "compute capability 8.6" in text
    assert "Ollama" in text and "running at http://127.0.0.1:11434" in text
    assert "vLLM      not found" in text
    assert "SQLite    3.53.1, WAL safe" in text
    assert doctor.format_report({}).startswith("Stepswitch doctor")


# --------------------------------------------------------------------------------------------
# collect()
# --------------------------------------------------------------------------------------------

FAKE_TOOLS = {
    "nvidia-smi": "/fake/bin/nvidia-smi",
    "ollama": "/fake/bin/ollama",
    "docker": "/fake/bin/docker",
    "wsl": "/fake/bin/wsl",
}
FAKE_RESPONSES: dict[str, Response] = {
    query(doctor._CUDA_QUERIES[0]): (0, CUDA_SMI_FOUR, ""),
    "nvidia-smi --version": (0, "DRIVER version : 580.00\nCUDA Version : 13.0\n", ""),
    "llama-server --version": (0, "", LLAMA_VERSION_CURRENT),
    "llama-server.exe --version": (0, "", LLAMA_VERSION_CURRENT),
    "ollama --version": (0, "ollama version is 0.40.5\n", ""),
    "docker context inspect": (0, "unix:///var/run/docker.sock\n", ""),
    "docker version": (0, "28.0.1\n", ""),
    "wsl --version": (0, "WSL version: 2.4.13.0\n", ""),
    "wsl --list": (0, "Ubuntu\n", ""),
}


@pytest.fixture
def fake_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake tools on a fake PATH, a managed llama.cpp build and no OLLAMA_HOST or DOCKER_HOST."""
    monkeypatch.setattr(doctor, "_find_tool", lambda name: FAKE_TOOLS.get(name))
    monkeypatch.setenv("STEPSWITCH_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    build = tmp_path / "home" / "engines" / "llamacpp" / "b11669"
    build.mkdir(parents=True)
    for name in ("llama-server", "llama-server.exe"):
        (build / name).write_bytes(b"")
    return tmp_path


def test_collect_with_a_fake_runner(fake_host: Path) -> None:
    runner = FakeRunner(FAKE_RESPONSES)
    egress = MockEgress(ollama_handler)
    report = doctor.collect(
        models_dir=fake_host / "models",
        project_root=fake_host,
        egress=egress,
        timeout=2.0,
        runner=runner,
    )
    json.dumps(report)
    assert set(report) >= REQUIRED_KEYS
    assert report["schema"] == 1
    assert isinstance(report["warnings"], list)

    cuda_gpus = [gpu for gpu in report["gpus"] if gpu["backend"] == "cuda"]
    assert len(cuda_gpus) == 4 and all(gpu["cuda_version"] == "13.0" for gpu in cuda_gpus)
    engines = report["engines"]
    assert {"llamacpp", "ollama", "vllm", "sglang", "mlx", "docker"} <= set(engines)
    assert all(isinstance(engine["installed"], bool) for engine in engines.values())
    assert engines["llamacpp"]["version"] == "0.7.0" and engines["llamacpp"]["source"] == "managed"
    assert engines["ollama"]["running"] is True and engines["ollama"]["version"] == "0.40.5"
    assert engines["ollama"]["client_version"] == "0.40.5"
    assert engines["docker"]["running"] is True and engines["docker"]["version"] == "28.0.1"
    if report["os"]["system"] == "Windows":
        assert engines["wsl"]["version"] == "2.4.13.0"
        assert engines["wsl"]["distributions"] == ["Ubuntu"]
    assert report["disk"]["models_dir"]["free_gb"] > 0
    assert report["disk"]["project"]["path"] == str(fake_host)
    assert report["sqlite"]["version"] and isinstance(report["sqlite"]["wal_safe"], bool)
    assert report["profile"] == doctor.recommend(report)

    rows = list(egress.memory)
    assert [(row.host, row.path, row.purpose, row.local) for row in rows] == [
        ("127.0.0.1", "/api/version", "doctor", True)
    ]
    assert isinstance(doctor.format_report(report), str)


def test_collect_never_contacts_a_remote_ollama_or_docker(
    fake_host: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://192.0.2.10:11434")
    monkeypatch.setenv("DOCKER_HOST", "tcp://192.0.2.20:2376")
    runner = FakeRunner(FAKE_RESPONSES)
    egress = MockEgress(refuse)
    report = doctor.collect(project_root=fake_host, egress=egress, timeout=2.0, runner=runner)
    assert list(egress.memory) == []
    assert not any(call.startswith(("ollama", "docker version")) for call in runner.calls)
    ollama = report["engines"]["ollama"]
    assert ollama["local"] is False and ollama["running"] is None and ollama["url"] is None
    assert report["engines"]["docker"]["endpoint_local"] is False
    notes = report["profile"]["notes"]
    assert any("OLLAMA_HOST points to a non-local address" in note for note in notes)
    assert any("Docker endpoint is not local" in note for note in notes)


def test_collect_skips_a_remote_docker_context(fake_host: Path) -> None:
    responses = {**FAKE_RESPONSES, "docker context inspect": (0, "ssh://user@example.net\n", "")}
    runner = FakeRunner(responses)
    report = doctor.collect(
        project_root=fake_host, egress=MockEgress(ollama_handler), timeout=2.0, runner=runner
    )
    assert "docker version --format {{.Server.Version}}" not in runner.calls
    assert report["engines"]["docker"]["running"] is None


def test_collect_survives_a_failing_runner(fake_host: Path) -> None:
    runner = FakeRunner({"": RuntimeError("runner exploded")})
    report = doctor.collect(
        project_root=fake_host, egress=MockEgress(refuse_quietly), timeout=1.0, runner=runner
    )
    json.dumps(report)
    assert set(report) >= REQUIRED_KEYS
    assert any("runner exploded" in warning for warning in report["warnings"])
    assert report["engines"]["ollama"]["running"] is False


def refuse_quietly(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def test_collect_reports_timeouts_as_warnings(fake_host: Path) -> None:
    runner = FakeRunner({"nvidia-smi --query-gpu": (124, "", "timed out after 1 s")})
    report = doctor.collect(
        project_root=fake_host, egress=MockEgress(refuse_quietly), timeout=1.0, runner=runner
    )
    assert any("nvidia-smi timed out" in warning for warning in report["warnings"])
    assert [gpu for gpu in report["gpus"] if gpu["backend"] == "cuda"] == []


def test_collect_smoke_on_this_machine(tmp_path: Path) -> None:
    """The real probes on the machine running the tests; only local addresses are reachable."""
    egress = net.Egress(net.EgressPolicy(offline=True))
    started = time.monotonic()
    report = doctor.collect(
        models_dir=tmp_path / "models", project_root=tmp_path, egress=egress, timeout=5.0
    )
    assert time.monotonic() - started < 30
    json.dumps(report)
    assert set(report) >= REQUIRED_KEYS
    assert report["schema"] == 1
    assert isinstance(report["created"], str)
    assert isinstance(report["os"]["system"], str) and isinstance(report["os"]["wsl"], bool)
    assert isinstance(report["cpu"]["features"], dict)
    assert report["ram_gb"] is None or report["ram_gb"] > 0
    assert isinstance(report["gpus"], list)
    for gpu in report["gpus"]:
        assert set(gpu) >= GPU_KEYS
    assert all(isinstance(engine["installed"], bool) for engine in report["engines"].values())
    assert isinstance(report["sqlite"]["version"], str)
    assert report["profile"]["tier"] in {"cpu", "t1", "t2", "t3"}
    assert all(isinstance(item, str) for item in report["warnings"])
    for row in egress.memory:
        assert row.local and row.purpose == "doctor", row
    assert doctor.format_report(report).startswith("Stepswitch doctor")
