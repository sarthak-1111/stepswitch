"""`ssw doctor`: detect the host and recommend a profile (SPEC §5.1).

`collect()` reports the OS, the CPU (cores, vector and matrix extensions), memory, GPUs (memory,
compute capability with FP8 and FP4 support, driver), free disk space, installed engines and the
SQLite library. `recommend()` turns a report into a hardware tier with defaults, and
`write_profile()` stores a compact copy in the managed "doctor" block of `.stepswitch/local.toml`.

Ground rules:

* Probes never raise. Commands run through a `Runner` with a timeout, failures become lines in
  ``report["warnings"]`` and unknown values are ``None``.
* GB means 10**9 bytes everywhere, like ``ram_gb`` and ``file_size_gb`` in the registry.
* The only network request is ``GET /api/version`` to an Ollama server at a local address, made
  through `stepswitch.net` with purpose "doctor". A non-local ``OLLAMA_HOST`` or Docker endpoint
  is never contacted, not even through the engine's own command-line tool.
* Parsers are pure functions of text, so every platform's code path is tested on every OS.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import csv
import ctypes
import importlib.metadata
import ipaddress
import json
import os
import platform
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TypeAlias

from stepswitch import net, paths, state, tomlw

SCHEMA = 1
PURPOSE = "doctor"

GB = 1_000_000_000
GIB = 1 << 30
MIB = 1 << 20

# Defaults from ADR 0009: the T0 download cap, the RAM floor and per-role context sizes.
DEFAULT_MAX_GB = 8.0
T0_MIN_RAM_GB = 8.0
T0_CONTEXT = {"router": 4096, "embeddings": 2048, "fim": 4096}

# Share of unified memory counted as GPU memory on arm64 macOS.
UNIFIED_USABLE = 0.75

# Tier thresholds in GiB. GPU memory is sold in binary units (a "24 GB" card has 24 GiB, about
# 25.8 GB) and drivers report a little less than the nominal size (ECC and firmware carve-outs),
# so the lower bounds allow 5% slack.
TIER_SLACK = 0.95
CPU_TIER_MAX_CARD_GIB = 10.0
T1_MAX_TOTAL_GIB = 80.0
T2_MAX_TOTAL_GIB = 200.0

# Tensor cores take FP8 inputs from compute capability 8.9 and FP4 inputs from 10.0.
FP8_MIN_CC = (8, 9)
FP4_MIN_CC = (10, 0)

X86_FEATURES = (
    "avx",
    "avx2",
    "fma",
    "f16c",
    "avx_vnni",
    "avx512f",
    "avx512_vnni",
    "avx512_bf16",
    "amx",
)
ARM_FEATURES = ("neon", "dotprod", "i8mm", "bf16", "sve", "sme")

CUDA_SMI = "nvidia-smi"
ROCM_SMI = "rocm-smi"
LLAMA_SERVER = "llama-server"
OLLAMA_DEFAULT_PORT = "11434"

ENGINE_LABELS = {
    "llamacpp": "llama.cpp",
    "ollama": "Ollama",
    "vllm": "vLLM",
    "sglang": "SGLang",
    "mlx": "MLX-LM",
    "docker": "Docker",
    "wsl": "WSL",
}

Runner: TypeAlias = Callable[[list[str], float], tuple[int, str, str]]
"""(argv, timeout in seconds) -> (exit code, stdout, stderr). Tests inject fakes."""

# --------------------------------------------------------------------------------------------
# Running commands
# --------------------------------------------------------------------------------------------

_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def decode_output(data: bytes) -> str:
    """Decode command output. wsl.exe writes UTF-16LE without a byte-order mark."""
    if not data:
        return ""
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", "replace")
    odd = data[1::2]
    if odd and odd.count(0) * 2 >= len(odd):
        return data.decode("utf-16-le", "replace")
    return data.decode("utf-8", "replace")


def run_command(argv: list[str], timeout: float) -> tuple[int, str, str]:
    """The default `Runner`: no shell, no stdin, bounded time, never raises.

    Returns exit code 127 when the program cannot start and 124 when it timed out and was killed.
    """
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
    except (OSError, ValueError) as exc:
        return 127, "", f"cannot run {argv[0] if argv else '?'}: {exc}"
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            proc.kill()
        try:
            out, err = proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:  # a child of the child still holds the pipes
            out, err = b"", b""
        return 124, decode_output(out or b""), f"timed out after {timeout:g} s"
    return int(proc.returncode), decode_output(out or b""), decode_output(err or b"")


class _Probe:
    """Runs commands for one part of a report and keeps that part's warnings."""

    def __init__(self, runner: Runner, timeout: float) -> None:
        self.runner = runner
        self.timeout = timeout
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def run(self, argv: list[str], *, quiet: bool = False) -> tuple[int, str, str] | None:
        """(code, stdout, stderr), or None when the runner itself failed (already warned)."""
        name = Path(argv[0]).name
        try:
            code, out, err = self.runner(argv, self.timeout)
        except Exception as exc:
            self.warn(f"{name}: {type(exc).__name__}: {exc}")
            return None
        if code == 124 and not quiet:
            self.warn(f"{name} timed out after {self.timeout:g} s")
        return int(code), str(out or ""), str(err or "")


def _which(name: str) -> str | None:
    """`shutil.which` over the absolute PATH entries only.

    On Windows `shutil.which` looks in the current directory first, and doctor often runs inside
    a project that may hold untrusted files, so the current directory and relative PATH entries
    are never searched.
    """
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        directory = entry.strip().strip('"')
        if directory and Path(directory).is_absolute():
            found = shutil.which(str(Path(directory) / name))
            if found:
                return found
    return None


def _find_tool(name: str) -> str | None:
    """A program on PATH, or in its usual install location when that is not on PATH."""
    found = _which(name)
    if found:
        return found
    for candidate in _extra_locations(name):
        with contextlib.suppress(OSError):
            if candidate.is_file():
                return str(candidate)
    return None


def _extra_locations(name: str) -> list[Path]:
    if name == "ollama" and sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return [Path(local) / "Programs" / "Ollama" / "ollama.exe"] if local else []
    if name == CUDA_SMI and sys.platform.startswith("linux"):
        return [Path("/usr/lib/wsl/lib") / CUDA_SMI]  # WSL2 ships the CUDA driver tool here
    return []


# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _gb(nbytes: float | None, digits: int = 2) -> float | None:
    return None if nbytes is None else round(float(nbytes) / GB, digits)


def _number(value: object) -> float | None:
    """A float from a number or the leading number of a string ("49140 MiB" -> 49140.0)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        match = re.match(r"\s*([-+]?\d+(?:\.\d+)?)", value)
        return float(match.group(1)) if match else None
    return None


def _int(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _na(value: object) -> str | None:
    """None for empty or "not available" fields such as "[N/A]" and "[Not Supported]"."""
    if value is None:
        return None
    text = str(value).strip().strip('"').strip()
    if not text or text.upper() in {"N/A", "NA"} or (text.startswith("[") and text.endswith("]")):
        return None
    return text


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def _dict(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _parse_ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------------------------
# OS
# --------------------------------------------------------------------------------------------


def normalize_arch(machine: str) -> str:
    """'AMD64', 'x86_64' -> 'x86_64'; 'aarch64', 'ARM64' -> 'arm64'; others lowercased."""
    value = machine.strip().lower()
    if value in {"x86_64", "amd64", "x64", "em64t"}:
        return "x86_64"
    if value in {"arm64", "aarch64", "arm64e", "aarch64_be"}:
        return "arm64"
    if value in {"i386", "i486", "i586", "i686", "x86"}:
        return "x86"
    if value.startswith("arm"):
        return "arm"
    return value or "unknown"


def is_wsl(osrelease: str | None, environ: Mapping[str, str], interop: bool = False) -> bool:
    """Linux under WSL: the kernel release names WSL, WSL's variables are set, or its interop
    handler is registered (WSL1 kernels do not carry the name)."""
    if osrelease and "wsl" in osrelease.lower():
        return True
    if environ.get("WSL_DISTRO_NAME") or environ.get("WSL_INTEROP"):
        return True
    return interop


def _fast_platform(environ: Mapping[str, str]) -> tuple[str, str]:
    """System name and machine, avoiding `platform.uname()`, which can be slow on Windows."""
    if sys.platform == "win32":
        machine = environ.get("PROCESSOR_ARCHITEW6432") or environ.get("PROCESSOR_ARCHITECTURE")
        return "Windows", machine or ""
    uname = os.uname()
    return uname.sysname, uname.machine


def _os_info(system: str, machine: str, environ: Mapping[str, str]) -> dict[str, Any]:
    uname = platform.uname()
    machine = machine or uname.machine
    info: dict[str, Any] = {
        "system": system,
        "release": uname.release or None,
        "version": uname.version or None,
        "machine": machine or None,
        "arch": normalize_arch(machine),
        "name": None,
        "wsl": False,
    }
    if system == "Windows":
        info["name"] = f"Windows {uname.release}".strip()
        if sys.platform == "win32":
            info["windows_build"] = sys.getwindowsversion().build
    elif system == "Linux":
        osrelease = _read_text(Path("/proc/sys/kernel/osrelease"))
        interop = _exists(Path("/proc/sys/fs/binfmt_misc/WSLInterop"))
        info["wsl"] = is_wsl(osrelease, environ, interop)
        if info["wsl"]:
            info["wsl_version"] = 2 if osrelease and "wsl2" in osrelease.lower() else None
        with contextlib.suppress(OSError):
            info["name"] = platform.freedesktop_os_release().get("PRETTY_NAME")
    elif system == "Darwin":
        info["name"] = f"macOS {platform.mac_ver()[0]}".strip()
    return info


# --------------------------------------------------------------------------------------------
# CPU and memory
# --------------------------------------------------------------------------------------------


def parse_proc_cpuinfo(text: str) -> dict[str, Any]:
    """Model name, flag set (x86 "flags", arm "Features") and core counts from /proc/cpuinfo.

    Physical cores are distinct (physical id, core id) pairs; None when the kernel does not list
    them (arm64).
    """
    model: str | None = None
    flags: set[str] | None = None
    logical = 0
    cores: set[tuple[str, str]] = set()
    package = "0"
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        value = value.strip()
        if key == "processor":
            if value.isdigit():
                logical += 1
                package = "0"
            elif value and model is None:  # 32-bit arm: "Processor : <name>"
                model = value
        elif key == "model name" and value and model is None:
            model = value
        elif key in {"flags", "features"} and flags is None:
            flags = set(value.lower().split())
        elif key == "physical id":
            package = value
        elif key == "core id":
            cores.add((package, value))
    return {
        "model": model,
        "flags": flags,
        "cores_logical": logical or None,
        "cores_physical": len(cores) or None,
    }


def cpu_features(flags: Iterable[str] | None, arch: str) -> dict[str, bool | None]:
    """Feature table from Linux /proc/cpuinfo flag names.

    Extensions of another architecture are False; with no flags (None) the native ones are
    unknown (None). `amx` means AMX tiles with INT8 or BF16 support.
    """
    present = {flag.lower() for flag in flags} if flags is not None else None

    def has(*names: str) -> bool | None:
        return None if present is None else any(name in present for name in names)

    features: dict[str, bool | None] = dict.fromkeys((*X86_FEATURES, *ARM_FEATURES), False)
    if arch in {"x86_64", "x86"}:
        amx = has("amx_tile")
        if amx:
            amx = has("amx_int8", "amx_bf16")
        features.update(
            avx=has("avx"),
            avx2=has("avx2"),
            fma=has("fma"),
            f16c=has("f16c"),
            avx_vnni=has("avx_vnni"),
            avx512f=has("avx512f"),
            avx512_vnni=has("avx512_vnni"),
            avx512_bf16=has("avx512_bf16"),
            amx=amx,
        )
    elif arch in {"arm64", "arm"}:
        features.update(
            neon=has("asimd", "neon"),
            dotprod=has("asimddp"),
            i8mm=has("i8mm"),
            bf16=has("bf16"),
            sve=has("sve"),
            sme=has("sme"),
        )
    return features


# IsProcessorFeaturePresent values (processthreadsapi.h) and the first Windows build that knows
# each one; older builds return 0 for flags they do not know, so 0 there means "unknown".
_PF_X86 = {"avx": 39, "avx2": 40, "avx512f": 41}
_PF_ARM = {"neon": 29, "dotprod": 43, "sve": 46, "i8mm": 66, "bf16": 68}
_PF_MIN_BUILD = {39: 19041, 40: 19041, 41: 19041, 29: 0, 43: 20348, 46: 26100, 66: 26100, 68: 26100}


def windows_cpu_features(
    is_present: Callable[[int], bool], arch: str, build: int | None
) -> dict[str, bool | None]:
    """Feature table from IsProcessorFeaturePresent; features it cannot report stay None."""
    features = cpu_features(None, arch)
    if arch in {"x86_64", "x86"}:
        table = _PF_X86
    elif arch in {"arm64", "arm"}:
        table = _PF_ARM
    else:
        table = {}
    for key, flag in table.items():
        try:
            present = bool(is_present(flag))
        except Exception:
            continue
        if present:
            features[key] = True
        elif build is not None and build >= _PF_MIN_BUILD[flag]:
            features[key] = False
    return features


def count_core_records(data: bytes) -> int:
    """Processor-core records in a GetLogicalProcessorInformationEx(RelationProcessorCore) buffer.

    Each variable-size record starts with its relationship (0 = core) and its size, both DWORDs.
    """
    count = offset = 0
    while offset + 8 <= len(data):
        relationship, size = struct.unpack_from("<II", data, offset)
        if size < 8:
            break
        if relationship == 0:
            count += 1
        offset += size
    return count


class _MemoryStatusEx(ctypes.Structure):
    """MEMORYSTATUSEX (sysinfoapi.h)."""

    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _windows_memory() -> tuple[int | None, int | None]:
    if sys.platform != "win32":
        return None, None
    status = _MemoryStatusEx(dwLength=ctypes.sizeof(_MemoryStatusEx))
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except Exception:
        return None, None
    if not ok:
        return None, None
    return int(status.ullTotalPhys), int(status.ullAvailPhys)


def _windows_physical_cores() -> int | None:
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        length = ctypes.c_uint32(0)
        kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(length))
        if not length.value:
            return None
        buffer = ctypes.create_string_buffer(length.value)
        if not kernel32.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(length)):
            return None
        return count_core_records(buffer.raw[: length.value]) or None
    except Exception:
        return None


def _windows_cpu(arch: str, probe: _Probe) -> dict[str, Any]:
    info: dict[str, Any] = {}
    if sys.platform != "win32":
        return info
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "ProcessorNameString")
        info["model"] = " ".join(str(value).split()) or None
    except OSError as exc:
        probe.warn(f"cpu name: {exc}")
    try:
        kernel32 = ctypes.windll.kernel32
        build = sys.getwindowsversion().build
        info["features"] = windows_cpu_features(
            lambda flag: bool(kernel32.IsProcessorFeaturePresent(flag)), arch, build
        )
    except Exception as exc:
        probe.warn(f"cpu features: {type(exc).__name__}: {exc}")
    info["cores_physical"] = _windows_physical_cores()
    return info


def parse_meminfo(text: str) -> tuple[int | None, int | None]:
    """(total, available) bytes from /proc/meminfo ("kB" there means 1024 bytes)."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, sep, rest = line.partition(":")
        parts = rest.split()
        if sep and parts and parts[0].isdigit():
            factor = 1024 if len(parts) > 1 and parts[1].lower() == "kb" else 1
            values[key.strip()] = int(parts[0]) * factor
    available = values.get("MemAvailable")
    if available is None and "MemFree" in values:  # kernels before 3.14
        available = sum(values.get(name, 0) for name in ("MemFree", "Buffers", "Cached"))
    return values.get("MemTotal"), available


def _linux_physical_cores() -> int | None:
    """Distinct sibling sets in sysfs topology; one per physical core."""
    siblings: set[str] = set()
    with contextlib.suppress(OSError):
        for cpu_dir in Path("/sys/devices/system/cpu").glob("cpu[0-9]*"):
            topology = cpu_dir / "topology"
            text = _read_text(topology / "core_cpus_list") or _read_text(
                topology / "thread_siblings_list"
            )
            if text and text.strip():
                siblings.add(text.strip())
    return len(siblings) or None


def parse_lscpu_model(text: str) -> str | None:
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "model name" and value.strip() not in {"", "-"}:
            return value.strip()
    return None


_SYSCTL_LINE = re.compile(r"^([A-Za-z0-9_.]+)\s*[:=]\s?(.*)$")


def parse_sysctl(text: str) -> dict[str, str]:
    """`sysctl` output ("name: value" lines) as a mapping."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _SYSCTL_LINE.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def macos_cpu(values: Mapping[str, str], arch: str) -> dict[str, Any]:
    """CPU facts from `sysctl hw machdep.cpu` on macOS (hw.optional.* feature flags)."""

    def flag(name: str) -> bool | None:
        raw = values.get(name)
        return None if raw is None else raw.strip() not in {"", "0"}

    # A translated (x86-64) Python on arm64 still reports the hardware here.
    hw_arch = "arm64" if values.get("hw.optional.arm64", "").strip() == "1" else arch
    features = cpu_features(None, hw_arch)
    if hw_arch == "arm64":
        neon = flag("hw.optional.arm.AdvSIMD")
        features.update(
            neon=neon if neon is not None else flag("hw.optional.neon"),
            dotprod=flag("hw.optional.arm.FEAT_DotProd"),
            i8mm=flag("hw.optional.arm.FEAT_I8MM"),
            bf16=flag("hw.optional.arm.FEAT_BF16"),
            sme=flag("hw.optional.arm.FEAT_SME"),
        )
    elif hw_arch == "x86_64":
        leaf7 = values.get("machdep.cpu.leaf7_features")
        features.update(
            avx=flag("hw.optional.avx1_0"),
            avx2=flag("hw.optional.avx2_0"),
            fma=flag("hw.optional.fma"),
            f16c=flag("hw.optional.f16c"),
            avx512f=flag("hw.optional.avx512f"),
            avx512_vnni=None if leaf7 is None else "AVX512VNNI" in leaf7.upper().split(),
        )
    return {
        "model": values.get("machdep.cpu.brand_string") or None,
        "arch": hw_arch,
        "cores_physical": _int(values.get("hw.physicalcpu")),
        "cores_logical": _int(values.get("hw.logicalcpu")),
        "features": features,
    }


def parse_vm_stat(text: str) -> int | None:
    """Available memory in bytes from macOS `vm_stat`: free, inactive and speculative pages."""
    match = re.search(r"page size of (\d+) bytes", text)
    if not match:
        return None
    counts: dict[str, int] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        value = value.strip().rstrip(".")
        if sep and value.isdigit():
            counts[key.strip().lower()] = int(value)
    if "pages free" not in counts:
        return None
    pages = sum(
        counts.get(name, 0) for name in ("pages free", "pages inactive", "pages speculative")
    )
    return pages * int(match.group(1))


def _host(system: str, arch: str, probe: _Probe) -> dict[str, Any]:
    """CPU and memory (one task, because macOS reads both from one sysctl call)."""
    cpu: dict[str, Any] = {
        "model": None,
        "arch": arch,
        "cores_physical": None,
        "cores_logical": os.cpu_count(),
        "features": cpu_features(None, arch),
    }
    total: int | None = None
    available: int | None = None
    if system == "Windows":
        cpu.update(_windows_cpu(arch, probe))
        total, available = _windows_memory()
    elif system == "Linux":
        text = _read_text(Path("/proc/cpuinfo"))
        if text is None:
            probe.warn("cpu: cannot read /proc/cpuinfo")
        else:
            parsed = parse_proc_cpuinfo(text)
            cpu["model"] = parsed["model"]
            cpu["features"] = cpu_features(parsed["flags"], arch)
            cpu["cores_logical"] = parsed["cores_logical"] or cpu["cores_logical"]
            cpu["cores_physical"] = parsed["cores_physical"]
        if cpu["cores_physical"] is None:
            cpu["cores_physical"] = _linux_physical_cores()
        if cpu["model"] is None:
            lscpu = _find_tool("lscpu")
            result = probe.run([lscpu], quiet=True) if lscpu else None
            if result and result[0] == 0:
                cpu["model"] = parse_lscpu_model(result[1])
        meminfo = _read_text(Path("/proc/meminfo"))
        if meminfo is None:
            probe.warn("memory: cannot read /proc/meminfo")
        else:
            total, available = parse_meminfo(meminfo)
    elif system == "Darwin":
        sysctl = _find_tool("sysctl") or "/usr/sbin/sysctl"
        result = probe.run([sysctl, "hw", "machdep.cpu"])
        if result is None or not result[1].strip():
            probe.warn("cpu: sysctl gave no output")
        else:
            values = parse_sysctl(result[1])
            cpu.update({k: v for k, v in macos_cpu(values, arch).items() if v is not None})
            total = _int(values.get("hw.memsize"))
        vm_stat = _find_tool("vm_stat") or "/usr/bin/vm_stat"
        result = probe.run([vm_stat], quiet=True)
        if result and result[0] == 0:
            available = parse_vm_stat(result[1])
    else:
        probe.warn(f"cpu and memory detection is not implemented for {system or 'this OS'}")
    if total is None:
        probe.warn("memory: total RAM unknown")
    return {"cpu": cpu, "ram_total": total, "ram_available": available}


# --------------------------------------------------------------------------------------------
# GPUs
# --------------------------------------------------------------------------------------------

# Field sets for --query-gpu, newest first: driver_version is deprecated in favour of
# kmd_version, and compute_cap is missing from old drivers.
_CUDA_QUERIES = (
    "index,name,memory.total,memory.free,driver_version,compute_cap",
    "index,name,memory.total,memory.free,kmd_version,compute_cap",
    "index,name,memory.total,memory.free,driver_version",
)


def parse_compute_capability(value: object) -> tuple[int, int] | None:
    text = _na(value)
    match = re.fullmatch(r"(\d+)\.(\d+)", text) if text else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def cuda_precision_flags(compute_capability: object) -> tuple[bool | None, bool | None]:
    """(native FP8, native FP4) tensor-core inputs for a CUDA compute capability like "8.9"."""
    cc = parse_compute_capability(compute_capability)
    if cc is None:
        return None, None
    return cc >= FP8_MIN_CC, cc >= FP4_MIN_CC


def parse_cuda_smi_csv(text: str) -> list[dict[str, Any]]:
    """Rows of `nvidia-smi --query-gpu=index,name,memory.total,memory.free,driver_version,
    compute_cap --format=csv,noheader,nounits` (memory in MiB). "[N/A]" fields become None."""
    gpus: list[dict[str, Any]] = []
    for row in csv.reader(text.splitlines(), skipinitialspace=True):
        cells = [cell.strip() for cell in row]
        index = _int(_na(cells[0])) if cells else None
        if len(cells) < 5 or index is None:
            continue
        total = _number(_na(cells[2]))
        free = _number(_na(cells[3]))
        compute_capability = _na(cells[5]) if len(cells) > 5 else None
        fp8, fp4 = cuda_precision_flags(compute_capability)
        gpus.append(
            {
                "index": index,
                "name": _na(cells[1]),
                "backend": "cuda",
                "vram_gb": _gb(total * MIB) if total is not None else None,
                "vram_free_gb": _gb(free * MIB) if free is not None else None,
                "driver": _na(cells[4]),
                "compute_capability": compute_capability,
                "native_fp8": fp8,
                "native_fp4": fp4,
                "unified_memory": False if total is not None else None,
            }
        )
    return gpus


def parse_cuda_version(text: str) -> str | None:
    """The CUDA version the driver supports, from `nvidia-smi --version` or its table header."""
    match = re.search(r"CUDA Version\s*:\s*(\d+(?:\.\d+)*)", text)
    return match.group(1) if match else None


def _cuda_gpus(exe: str, probe: _Probe) -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] | None = None
    for fields in _CUDA_QUERIES:
        result = probe.run([exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits"])
        if result is None:
            return []
        code, out, err = result
        if code == 0:
            gpus = parse_cuda_smi_csv(out)
            break
        if "not a valid field" in f"{out}\n{err}".lower():
            continue
        if code != 124:
            probe.warn(f"{CUDA_SMI} exited with {code}: {_first_line(err) or _first_line(out)}")
        return []
    if gpus is None:
        probe.warn(f"{CUDA_SMI} accepts none of the known --query-gpu field sets")
        return []
    if gpus:
        result = probe.run([exe, "--version"], quiet=True)
        cuda = parse_cuda_version(result[1]) if result and result[0] == 0 else None
        for gpu in gpus:
            gpu["cuda_version"] = cuda
    return gpus


def parse_rocm_smi_json(text: str) -> list[dict[str, Any]]:
    """Cards from `rocm-smi --showproductname --showmeminfo vram --showdriverversion --json`.

    Raises ValueError for output that holds no JSON object.
    """
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in output")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON output is not an object")
    driver = None
    for key, value in _dict(data.get("system")).items():
        if "driver version" in str(key).lower():
            driver = _na(value)
    gpus: list[dict[str, Any]] = []
    for key, card in data.items():
        if not (str(key).lower().startswith("card") and isinstance(card, dict)):
            continue
        fields = {str(k).strip().lower(): v for k, v in card.items()}
        total = _number(_na(fields.get("vram total memory (b)")))
        used = _number(_na(fields.get("vram total used memory (b)")))
        suffix = str(key)[4:]
        gpus.append(
            {
                "index": int(suffix) if suffix.isdigit() else len(gpus),
                "name": _na(fields.get("card series"))
                or _na(fields.get("card model"))
                or _na(fields.get("device name")),
                "backend": "rocm",
                "vram_gb": _gb(total),
                "vram_free_gb": _gb(total - used)
                if total is not None and used is not None
                else None,
                "driver": driver,
                "compute_capability": None,
                "arch": _na(fields.get("gfx version")),
                "native_fp8": None,
                "native_fp4": None,
                "unified_memory": False if total is not None else None,
            }
        )
    return sorted(gpus, key=lambda gpu: int(gpu["index"]))


def _rocm_gpus(exe: str, probe: _Probe) -> list[dict[str, Any]]:
    argv = [exe, "--showproductname", "--showmeminfo", "vram", "--showdriverversion", "--json"]
    result = probe.run(argv)
    if result is None:
        return []
    code, out, err = result
    if code != 0:
        if code != 124:
            probe.warn(f"{ROCM_SMI} exited with {code}: {_first_line(err) or _first_line(out)}")
        return []
    try:
        return parse_rocm_smi_json(out)
    except ValueError as exc:
        probe.warn(f"{ROCM_SMI}: unreadable output ({exc})")
        return []


def metal_gpu(name: str | None, ram_bytes: int | None) -> dict[str, Any]:
    """The GPU of an arm64 Mac, which shares system memory (counted as RAM here)."""
    return {
        "index": 0,
        "name": name,
        "backend": "metal",
        "vram_gb": _gb(ram_bytes),
        "vram_free_gb": None,
        "driver": None,
        "compute_capability": None,
        "native_fp8": None,
        "native_fp4": None,
        "unified_memory": True,
    }


def _gpus(probe: _Probe) -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    smi = _find_tool(CUDA_SMI)
    if smi:
        gpus.extend(_cuda_gpus(smi, probe))
    rocm = _find_tool(ROCM_SMI)
    if rocm:
        gpus.extend(_rocm_gpus(rocm, probe))
    return gpus


# --------------------------------------------------------------------------------------------
# Disk
# --------------------------------------------------------------------------------------------


def nearest_existing(path: Path) -> Path:
    """`path` itself or its nearest existing parent directory."""
    target = path.expanduser().absolute()
    for candidate in (target, *target.parents):
        if _exists(candidate):
            return candidate
    return Path(target.anchor or ".")


def disk_info(path: Path) -> dict[str, Any]:
    checked = nearest_existing(path)
    usage = shutil.disk_usage(checked)
    return {
        "path": str(path),
        "checked": str(checked),
        "free_gb": _gb(usage.free),
        "total_gb": _gb(usage.total),
    }


# --------------------------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------------------------

_LLAMA_VERSION = re.compile(
    r"^version:\s*(?P<version>\S+)\s*\(build\s+(?P<build>\d+),\s*commit\s+(?P<commit>[^)\s]+)\)",
    re.M,
)
_LLAMA_LEGACY_VERSION = re.compile(r"^version:\s*(?P<build>\d+)\s*\((?P<commit>[^)\s]+)\)", re.M)
_LLAMA_TARGET = re.compile(r"^built with (?P<compiler>.+?) for (?P<target>.+?)\s*$", re.M)


def parse_llama_server_version(text: str) -> dict[str, Any] | None:
    """`llama-server --version` (printed on stderr), in either of its formats:

    ``version: 0.7.0 (build 11669, commit abc1234)`` (semantic versions, current) or
    ``version: 6000 (abc1234)`` (build number only, older builds); then ``built with X for T``.
    """
    text = text.replace("\r\n", "\n")
    match = _LLAMA_VERSION.search(text)
    if match:
        number = int(match.group("build"))
        version = match.group("version")
    else:
        match = _LLAMA_LEGACY_VERSION.search(text)
        if not match:
            return None
        number = int(match.group("build"))
        version = f"b{number}"
    target = _LLAMA_TARGET.search(text)
    return {
        "version": version,
        "build": f"b{number}",
        "build_number": number,
        "commit": match.group("commit"),
        "target": target.group("target") if target else None,
    }


def llama_server_names(system: str) -> tuple[str, ...]:
    return (f"{LLAMA_SERVER}.exe", LLAMA_SERVER) if system == "Windows" else (LLAMA_SERVER,)


def _build_rank(directory: Path) -> tuple[int, float]:
    """Newest first: the build number in names like 'b11669' or 'llama-b11669-bin', then mtime."""
    match = re.search(r"(?:^|[^a-z])b(\d{2,})", directory.name.lower())
    try:
        mtime = directory.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (int(match.group(1)) if match else -1, mtime)


def _find_in_build(build: Path, names: tuple[str, ...]) -> Path | None:
    roots = [build]
    with contextlib.suppress(OSError):
        roots += sorted(child for child in build.iterdir() if child.is_dir())
    for root in roots:
        for relative in ("", "bin", "build/bin"):
            base = root / relative if relative else root
            for name in names:
                candidate = base / name
                if candidate.is_file():
                    return candidate
    return None


def find_llama_server(engines_root: Path, system: str) -> tuple[Path | None, str | None]:
    """(path, source): the newest managed build under <engines>/llamacpp/<build>/, else PATH."""
    names = llama_server_names(system)
    found: list[tuple[tuple[int, float], Path]] = []
    with contextlib.suppress(OSError):
        for build in (engines_root / "llamacpp").iterdir():
            if build.is_dir():
                exe = _find_in_build(build, names)
                if exe is not None:
                    found.append((_build_rank(build), exe))
    if found:
        return max(found, key=lambda item: item[0])[1], "managed"
    on_path = _find_tool(LLAMA_SERVER)
    return (Path(on_path), "path") if on_path else (None, None)


def _llamacpp(system: str, probe: _Probe) -> dict[str, Any]:
    exe, source = find_llama_server(paths.engines_dir(), system)
    info: dict[str, Any] = {
        "installed": exe is not None,
        "version": None,
        "build": None,
        "path": str(exe) if exe else None,
        "source": source,
    }
    if exe is None:
        return info
    result = probe.run([str(exe), "--version"])
    if result is None:
        return info
    code, out, err = result
    parsed = parse_llama_server_version(f"{out}\n{err}")
    if parsed is None:
        if code != 124:
            probe.warn(f"{LLAMA_SERVER} --version: unrecognized output (exit {code})")
        return info
    info.update(parsed)
    return info


def _split_host_port(hostport: str) -> tuple[str, str] | None:
    """Like Go's net.SplitHostPort: None unless the text holds a host and a port."""
    if hostport.startswith("["):
        end = hostport.find("]")
        if end < 0 or not hostport[end + 1 :].startswith(":"):
            return None
        return hostport[1:end], hostport[end + 2 :]
    if hostport.count(":") != 1:
        return None
    host, _, port = hostport.partition(":")
    return host, port


def ollama_endpoint(value: str | None) -> dict[str, Any]:
    """Where Ollama's client connects for a given OLLAMA_HOST.

    Mirrors Ollama's own parsing (default http://127.0.0.1:11434; an explicit http or https
    scheme defaults to port 80 or 443) and maps unspecified bind addresses (0.0.0.0, ::) to
    loopback, as its client does. Returns {"url", "local", "all_interfaces"}.
    """
    raw = (value or "").strip().strip("\"'").strip()
    scheme, sep, hostport = raw.partition("://")
    default_port = OLLAMA_DEFAULT_PORT
    if not sep:
        scheme, hostport = "http", raw
    elif scheme == "http":
        default_port = "80"
    elif scheme == "https":
        default_port = "443"
    hostport, _, path = hostport.partition("/")
    split = _split_host_port(hostport)
    if split is None:
        host, port = "127.0.0.1", default_port
        address = _parse_ip(hostport.strip("[]"))
        if address is not None:
            host = str(address)
        elif hostport:
            host = hostport
    else:
        host, port = split
    if not port.isdigit() or int(port) > 65535:
        port = default_port
    address = _parse_ip(host)
    all_interfaces = host == "" or (address is not None and address.is_unspecified)
    if all_interfaces:
        host = "::1" if address is not None and address.version == 6 else "127.0.0.1"
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    url = f"{scheme}://{netloc}" + (f"/{path}" if path else "")
    return {"url": url, "local": net.is_local_url(url), "all_interfaces": all_interfaces}


_OLLAMA_SERVER_VERSION = re.compile(r"ollama version is\s+(\S+)", re.I)
_OLLAMA_CLIENT_VERSION = re.compile(r"client version is\s+(\S+)", re.I)


def parse_ollama_version(text: str) -> dict[str, str | None]:
    """`ollama --version`: "ollama version is X" (the server's version, when one answered) and
    "Warning: client version is Y" (printed when the client differs or no server answered)."""
    server = _OLLAMA_SERVER_VERSION.search(text)
    client = _OLLAMA_CLIENT_VERSION.search(text)
    return {
        "server": server.group(1) if server else None,
        "client": client.group(1) if client else None,
    }


def _ollama_server_version(
    url: str, egress: net.Egress, timeout: float, probe: _Probe
) -> str | None:
    if not net.is_local_url(url):  # never contact a remote host, whatever the caller passed
        return None
    try:
        with egress.client(PURPOSE, timeout=min(timeout, 3.0)) as client:
            response = client.get(url.rstrip("/") + "/api/version")
    except Exception:  # refused or timed out: no server running there
        return None
    if response.status_code != 200:
        probe.warn(f"Ollama at {url} answered HTTP {response.status_code} for /api/version")
        return None
    try:
        payload = response.json()
    except ValueError:
        probe.warn(f"Ollama at {url} returned an unreadable /api/version body")
        return None
    version = payload.get("version") if isinstance(payload, dict) else None
    return str(version) if version else None


def _ollama(
    environ: Mapping[str, str], egress: net.Egress, timeout: float, probe: _Probe
) -> dict[str, Any]:
    endpoint = ollama_endpoint(environ.get("OLLAMA_HOST"))
    exe = _find_tool("ollama")
    info: dict[str, Any] = {
        "installed": exe is not None,
        "running": False,
        "version": None,
        "client_version": None,
        "url": endpoint["url"] if endpoint["local"] else None,
        "local": endpoint["local"],
        "listens_on_all_interfaces": endpoint["all_interfaces"],
        "path": exe,
    }
    if not endpoint["local"]:
        # The CLI would contact OLLAMA_HOST too, so it is not run either.
        info["running"] = None
        return info
    server = _ollama_server_version(endpoint["url"], egress, timeout, probe)
    if exe:
        result = probe.run([exe, "--version"])
        if result and result[0] == 0:
            parsed = parse_ollama_version(f"{result[1]}\n{result[2]}")
            info["client_version"] = parsed["client"] or parsed["server"]
    if server is not None:
        info.update(installed=True, running=True, version=server)
    else:
        info["version"] = info["client_version"]
    return info


def version_from_dist_info(dirname: str, dist: str) -> str | None:
    """'mlx_lm-0.32.2.dist-info' -> '0.32.2' for the distribution 'mlx-lm'."""
    if not dirname.endswith(".dist-info"):
        return None
    name, sep, version = dirname[: -len(".dist-info")].partition("-")
    if not sep or not version or _normalize_dist(name) != _normalize_dist(dist):
        return None
    return version


def _normalize_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dist_version(dist: str, script: str | None = None) -> str | None:
    """A Python distribution's version: in this environment, else in the environment that holds
    `script` (engines usually live in their own virtual environment)."""
    with contextlib.suppress(Exception):
        return importlib.metadata.version(dist)
    if script is None:
        return None
    exe = Path(script)
    with contextlib.suppress(OSError):
        exe = exe.resolve()
    root = exe.parent.parent
    for site in (*root.glob("lib/python3*/site-packages"), root / "Lib" / "site-packages"):
        try:
            entries = sorted(entry.name for entry in site.iterdir())
        except OSError:
            continue
        for entry in entries:
            version = version_from_dist_info(entry, dist)
            if version:
                return version
    return None


def _python_engine(script: str, dist: str) -> dict[str, Any]:
    exe = _find_tool(script)
    version = dist_version(dist, exe)
    return {"installed": exe is not None or version is not None, "version": version, "path": exe}


def _python_engines() -> dict[str, dict[str, Any]]:
    return {
        "vllm": _python_engine("vllm", "vllm"),
        "sglang": _python_engine("sglang", "sglang"),
        "mlx": _python_engine("mlx_lm.server", "mlx-lm"),
    }


def is_local_docker_host(value: str) -> bool:
    """True for unix and npipe sockets and for tcp endpoints on loopback; ssh is never local."""
    scheme, sep, rest = value.strip().partition("://")
    scheme = scheme.lower()
    if not sep:
        return False
    if scheme in {"unix", "npipe", "fd"}:
        return True
    if scheme in {"tcp", "http", "https"}:
        return net.is_local_url(f"http://{rest}")
    return False


def _docker(environ: Mapping[str, str], probe: _Probe) -> dict[str, Any]:
    exe = _find_tool("docker")
    if not exe:
        return {"installed": False, "running": False, "version": None, "path": None}
    info: dict[str, Any] = {
        "installed": True,
        "running": False,
        "version": None,
        "path": exe,
        "endpoint_local": True,
    }
    endpoints = [environ["DOCKER_HOST"]] if environ.get("DOCKER_HOST") else []
    result = probe.run([exe, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"])
    if result and result[0] == 0 and result[1].strip():
        endpoints.append(_first_line(result[1]))
    if not all(is_local_docker_host(endpoint) for endpoint in endpoints):
        info.update(running=None, endpoint_local=False)
        return info
    result = probe.run([exe, "version", "--format", "{{.Server.Version}}"])
    if result and result[0] == 0 and result[1].strip():
        info.update(running=True, version=_first_line(result[1]))
    return info


def parse_wsl_version(text: str) -> str | None:
    """The first line of `wsl --version` ("WSL version: 2.4.13.0"; labels are localized)."""
    first = _first_line(text)
    match = re.search(r"(\d+(?:\.\d+){1,3})\s*$", first)
    return match.group(1) if match else None


def parse_wsl_list(text: str) -> list[str]:
    """Distribution names from `wsl --list --quiet`."""
    return [line.strip().lstrip("*").strip() for line in text.splitlines() if line.strip()]


def _wsl(probe: _Probe) -> dict[str, Any]:
    exe = _find_tool("wsl")
    info: dict[str, Any] = {"installed": False, "version": None, "distributions": [], "path": exe}
    if not exe:
        return info
    result = probe.run([exe, "--version"], quiet=True)
    if result and result[0] == 0:
        info["version"] = parse_wsl_version(result[1])
    result = probe.run([exe, "--list", "--quiet"], quiet=True)
    if result and result[0] == 0:
        info["distributions"] = parse_wsl_list(result[1])
    info["installed"] = info["version"] is not None or bool(info["distributions"])
    return info


# --------------------------------------------------------------------------------------------
# collect()
# --------------------------------------------------------------------------------------------


def _run_parallel(
    tasks: Mapping[str, Callable[[_Probe], Any]], runner: Runner, timeout: float
) -> tuple[dict[str, Any], list[str]]:
    """Run independent probes in threads, so the report takes about as long as the slowest."""
    probes = {name: _Probe(runner, timeout) for name in tasks}
    results: dict[str, Any] = {}
    warnings: list[str] = []
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, len(tasks)), thread_name_prefix="ssw-doctor"
    )
    try:
        futures = {name: pool.submit(task, probes[name]) for name, task in tasks.items()}
        done, _pending = concurrent.futures.wait(futures.values(), timeout=4 * timeout + 10)
        for name, future in futures.items():
            if future not in done:
                warnings.append(f"{name}: probe did not finish in time")
                continue
            try:
                results[name] = future.result()
            except Exception as exc:
                warnings.append(f"{name}: {type(exc).__name__}: {exc}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for name in tasks:
        warnings.extend(list(probes[name].warnings))
    return results, warnings


def _problems(report: Mapping[str, Any]) -> list[str]:
    """Findings that should make `ssw doctor` fail (ADR 0002)."""
    problems: list[str] = []
    sqlite_info = _dict(report.get("sqlite"))
    if sqlite_info.get("wal_safe") is False:
        problems.append(
            f"SQLite {sqlite_info.get('version')} has the WAL-reset corruption bug; use a Python"
            " whose SQLite is 3.51.3 or later (or 3.50.7 / 3.44.6), such as a uv-managed one"
            " (ADR 0002)."
        )
    return problems


def collect(
    *,
    models_dir: Path | None = None,
    project_root: Path | None = None,
    egress: net.Egress | None = None,
    timeout: float = 5.0,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Probe this machine and return a JSON-serializable report (schema 1).

    Every command runs through `runner` (default: `run_command`) with `timeout` seconds; the
    probes run in parallel and never raise. The report's ``profile`` is ``recommend(report)``.
    """
    started = time.monotonic()
    environ = dict(os.environ)
    system, machine = _fast_platform(environ)
    arch = normalize_arch(machine)
    egress = egress or net.default()
    tasks: dict[str, Callable[[_Probe], Any]] = {
        "os": lambda probe: _os_info(system, machine, environ),
        "host": lambda probe: _host(system, arch, probe),
        "gpus": _gpus,
        "llamacpp": lambda probe: _llamacpp(system, probe),
        "ollama": lambda probe: _ollama(environ, egress, timeout, probe),
        "python_engines": lambda probe: _python_engines(),
        "docker": lambda probe: _docker(environ, probe),
    }
    if system == "Windows":
        tasks["wsl"] = _wsl
    results, warnings = _run_parallel(tasks, runner or run_command, timeout)

    os_info = results.get("os") or {
        "system": system,
        "release": None,
        "version": None,
        "machine": machine or None,
        "arch": arch,
        "name": None,
        "wsl": False,
    }
    host = _dict(results.get("host"))
    cpu = dict(_dict(host.get("cpu"))) or {
        "model": None,
        "arch": arch,
        "cores_physical": None,
        "cores_logical": os.cpu_count(),
        "features": cpu_features(None, arch),
    }
    ram_total = host.get("ram_total")
    gpus = list(_list(results.get("gpus")))
    if system == "Darwin" and cpu.get("arch") == "arm64":
        gpus.append(metal_gpu(cpu.get("model"), ram_total))

    python_engines = _dict(results.get("python_engines"))
    engines: dict[str, Any] = {
        "llamacpp": results.get("llamacpp") or {"installed": False},
        "ollama": results.get("ollama") or {"installed": False},
        "vllm": python_engines.get("vllm") or {"installed": False},
        "sglang": python_engines.get("sglang") or {"installed": False},
        "mlx": python_engines.get("mlx") or {"installed": False},
        "docker": results.get("docker") or {"installed": False},
    }
    if system == "Windows":
        engines["wsl"] = results.get("wsl") or {"installed": False}

    disk: dict[str, Any] = {}
    locations: dict[str, tuple[Path | None, Callable[[], Path]]] = {
        "models_dir": (models_dir, paths.default_models_dir),
        "project": (project_root, paths.find_project_root),
    }
    for label, (given, fallback) in locations.items():
        try:
            disk[label] = disk_info(given if given is not None else fallback())
        except Exception as exc:
            warnings.append(f"disk ({label}): {type(exc).__name__}: {exc}")
            disk[label] = {"path": str(given) if given else None, "free_gb": None, "total_gb": None}

    try:
        wal_safe: bool | None = state.wal_is_safe(sqlite3.sqlite_version)
    except Exception:
        wal_safe = None

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "os": os_info,
        "cpu": cpu,
        "ram_gb": _gb(ram_total),
        "ram_available_gb": _gb(host.get("ram_available")),
        "gpus": gpus,
        "disk": disk,
        "engines": engines,
        "sqlite": {"version": sqlite3.sqlite_version, "wal_safe": wal_safe},
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "warnings": warnings,
    }
    report["problems"] = _problems(report)
    report["profile"] = recommend(report)
    report["elapsed_s"] = round(time.monotonic() - started, 2)
    return report


# --------------------------------------------------------------------------------------------
# recommend()
# --------------------------------------------------------------------------------------------


def _installed(engines: Mapping[str, Any], name: str) -> bool:
    return bool(_dict(engines.get(name)).get("installed"))


def _tier(count: int, total_gib: float, largest_gib: float) -> tuple[str, str]:
    if count == 0:
        return "cpu", "no GPU memory detected, so models run on the CPU (tier cpu, T0)"
    if largest_gib < CPU_TIER_MAX_CARD_GIB * TIER_SLACK:
        return "cpu", (
            f"the largest GPU has {largest_gib:.1f} GiB, under 10 GiB, so models run on the CPU"
            " (tier cpu, T0)"
        )
    gpus = f"{count} GPU{'s' if count > 1 else ''}"
    summary = f"{gpus} with {total_gib:.1f} GiB of usable memory (largest {largest_gib:.1f} GiB)"
    if total_gib < T1_MAX_TOTAL_GIB * TIER_SLACK:
        return "t1", f"{summary}: under 80 GiB is tier t1"
    if total_gib <= T2_MAX_TOTAL_GIB:
        return "t2", f"{summary}: 80 to 200 GiB is tier t2"
    return "t3", f"{summary}: over 200 GiB is tier t3"


def recommend(report: Mapping[str, Any]) -> dict[str, Any]:
    """The recommended profile for a report from `collect()`.

    Tiers, by GPU memory (in GiB, since GPU memory is sold in binary units; lower bounds have 5%
    slack because drivers report a little less than the nominal size):

    * ``cpu`` (T0): no GPU, or the largest GPU has less than 10 GiB.
    * ``t1``: less than 80 GiB of GPU memory in total.
    * ``t2``: 80 to 200 GiB in total.
    * ``t3``: more than 200 GiB in total (in practice several GPUs).

    Unified memory (arm64 macOS) counts at 75%, the share the GPU can use. A GPU that does not
    report its memory size is left out, with a note. A note is added when RAM is below the 8 GB
    T0 minimum. Quantization prefers 4-bit integer builds: GGUF Q4_K_M always, AWQ and GPTQ int4
    on CUDA or ROCm GPUs, MLX 4-bit on Metal; GPU tiers add FP8 when a GPU has native FP8
    (compute capability 8.9 or later) and FP4 when one has native FP4 (10.0 or later).
    """
    os_info = _dict(report.get("os"))
    system = str(os_info.get("system") or "")
    arch = str(_dict(report.get("cpu")).get("arch") or os_info.get("arch") or "")
    engines = _dict(report.get("engines"))
    gpus = [gpu for gpu in _list(report.get("gpus")) if isinstance(gpu, Mapping)]
    ram_gb = _number(report.get("ram_gb"))
    notes: list[str] = []

    counted: list[Mapping[str, Any]] = []
    usable_gib: list[float] = []
    for gpu in gpus:
        vram = _number(gpu.get("vram_gb"))
        if vram is None:
            notes.append(
                f"GPU {gpu.get('index')} ({gpu.get('name') or 'unnamed'}) did not report its"
                " memory size and is not counted."
            )
            continue
        share = UNIFIED_USABLE if gpu.get("unified_memory") is True else 1.0
        counted.append(gpu)
        usable_gib.append(vram * GB / GIB * share)
    total_gib = sum(usable_gib)
    largest_gib = max(usable_gib, default=0.0)
    tier, reason = _tier(len(usable_gib), total_gib, largest_gib)
    if any(gpu.get("unified_memory") is True for gpu in counted):
        notes.append("Unified memory is counted at 75%, the share the GPU can use.")
    if tier == "cpu" and counted:
        notes.append("GPUs under 10 GiB can still take offloaded layers of the T0 models.")

    if ram_gb is None:
        notes.append("RAM size unknown; the T0 set needs about 5.7 GB resident.")
    elif ram_gb < T0_MIN_RAM_GB:
        notes.append(
            f"RAM is {ram_gb:.1f} GB, below the {T0_MIN_RAM_GB:g} GB minimum for the T0 set"
            " (about 5.7 GB resident); use smaller alternates or fewer resident roles."
        )

    backends = {str(gpu.get("backend")) for gpu in counted}
    preferred = ["llamacpp"]
    if system == "Darwin" and arch == "arm64":
        preferred.append("mlx")
    if tier != "cpu" and backends & {"cuda", "rocm"}:
        if system == "Linux":
            preferred += ["vllm", "sglang"]
        elif system == "Windows":
            if _installed(engines, "wsl") or _installed(engines, "docker"):
                preferred += ["vllm", "sglang"]
                notes.append(
                    "vLLM and SGLang run through WSL2 or a container on Windows (ADR 0008);"
                    " llama.cpp and Ollama run natively."
                )
            else:
                notes.append("vLLM and SGLang need WSL2 or a container on Windows; neither found.")
        cuda_versions = [
            parse_compute_capability(gpu.get("cuda_version"))
            for gpu in counted
            if gpu.get("backend") == "cuda"
        ]
        old = [v for v in cuda_versions if v is not None and v < (13, 0)]
        if "vllm" in preferred and old:
            notes.append(
                "Current vLLM and SGLang wheels default to CUDA 13; this driver supports CUDA"
                f" {old[0][0]}.{old[0][1]}, so update the driver or use their CUDA 12 builds."
            )
    if _installed(engines, "ollama"):
        preferred.append("ollama")

    quants = ["gguf:q4_k_m"]
    if tier != "cpu":
        if backends & {"cuda", "rocm"}:
            quants += ["awq:int4", "gptq:int4"]
        if "metal" in backends:
            quants.append("mlx:4bit")
        if any(gpu.get("native_fp8") is True for gpu in counted):
            quants.append("fp8")
        if any(gpu.get("native_fp4") is True for gpu in counted):
            quants.append("fp4")

    ollama = _dict(engines.get("ollama"))
    if ollama.get("local") is False:
        notes.append("OLLAMA_HOST points to a non-local address; doctor did not contact it.")
    if ollama.get("listens_on_all_interfaces") is True:
        notes.append(
            "OLLAMA_HOST makes Ollama listen on all network interfaces, and its API has no"
            " authentication; bind it to 127.0.0.1 unless remote access is intended."
        )
    if _dict(engines.get("docker")).get("endpoint_local") is False:
        notes.append("The active Docker endpoint is not local; doctor did not contact it.")
    if not _installed(engines, "llamacpp"):
        notes.append("llama-server (the preferred engine) was not found.")
    free_gb = _number(_dict(_dict(report.get("disk")).get("models_dir")).get("free_gb"))
    if free_gb is not None and free_gb < DEFAULT_MAX_GB:
        notes.append(
            f"Only {free_gb:.1f} GB is free for models, less than the {DEFAULT_MAX_GB:g} GB"
            " download cap."
        )
    sqlite_info = _dict(report.get("sqlite"))
    if sqlite_info.get("wal_safe") is False:
        notes.append(f"SQLite {sqlite_info.get('version')} is not safe for WAL mode (ADR 0002).")

    return {
        "tier": tier,
        "reason": reason,
        "gpu_vram_total_gb": round(sum(_number(gpu.get("vram_gb")) or 0.0 for gpu in counted), 2),
        "gpu_usable_gb": round(total_gib * GIB / GB, 2),
        "ram_gb": ram_gb,
        "max_gb": DEFAULT_MAX_GB,
        "context": dict(T0_CONTEXT),
        "engines_preferred": preferred,
        "quant_preference": quants,
        "notes": notes,
    }


# --------------------------------------------------------------------------------------------
# write_profile()
# --------------------------------------------------------------------------------------------


def _compact(value: Any) -> Any:
    """Drop None values (TOML has no null) from nested mappings and lists."""
    if isinstance(value, Mapping):
        return {str(k): _compact(v) for k, v in value.items() if v is not None}
    if isinstance(value, list | tuple):
        return [_compact(v) for v in value if v is not None]
    return value


def _engine_summary(engine: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"installed": bool(engine.get("installed"))}
    if isinstance(engine.get("running"), bool):
        summary["running"] = engine["running"]
    if engine.get("version"):
        summary["version"] = str(engine["version"])
    if engine.get("build"):
        summary["build"] = str(engine["build"])
    return summary


def profile_data(report: Mapping[str, Any]) -> dict[str, Any]:
    """The managed block: a compact host summary and the recommended profile, without None
    values, paths or anything secret."""
    os_info = _dict(report.get("os"))
    cpu = _dict(report.get("cpu"))
    features = _dict(cpu.get("features"))
    created = str(report.get("created") or "")
    try:
        day: date | None = date.fromisoformat(created[:10])
    except ValueError:
        day = None
    gpu_keys = (
        "index",
        "name",
        "backend",
        "vram_gb",
        "driver",
        "compute_capability",
        "native_fp8",
        "native_fp4",
        "unified_memory",
    )
    sqlite_info = _dict(report.get("sqlite"))
    detected = {
        "date": day,
        "os": {
            key: os_info.get(key)
            for key in ("system", "release", "machine", "arch", "wsl", "windows_build")
        },
        "cpu": {
            "model": cpu.get("model"),
            "arch": cpu.get("arch"),
            "cores_physical": cpu.get("cores_physical"),
            "cores_logical": cpu.get("cores_logical"),
            "features": sorted(name for name, present in features.items() if present is True),
        },
        "ram_gb": report.get("ram_gb"),
        "gpus": [
            {key: _dict(gpu).get(key) for key in gpu_keys} for gpu in _list(report.get("gpus"))
        ],
        "engines": {
            str(name): _engine_summary(_dict(engine))
            for name, engine in _dict(report.get("engines")).items()
        },
        "sqlite": {"version": sqlite_info.get("version"), "wal_safe": sqlite_info.get("wal_safe")},
    }
    data: dict[str, Any] = _compact({"host": {"detected": detected}, "profile": recommend(report)})
    return data


def write_profile(local_config: Path, report: Mapping[str, Any]) -> None:
    """Write the managed "doctor" block of `local_config` (usually `.stepswitch/local.toml`).

    Every line outside the block is kept, and writing the same report twice gives the same file.
    Raises `tomllib.TOMLDecodeError` and leaves the file unchanged when a hand-written table
    conflicts with the block (for example a hand-written ``[profile]``).
    """
    tomlw.write_block(local_config, "doctor", profile_data(report))


# --------------------------------------------------------------------------------------------
# format_report()
# --------------------------------------------------------------------------------------------


def _yes_no(value: object) -> str:
    return "yes" if value is True else "no" if value is False else "unknown"


def _size(value: object) -> str:
    number = _number(value)
    return f"{number:.1f} GB" if number is not None else "unknown"


def _engine_line(name: str, engine: Mapping[str, Any]) -> str:
    label = ENGINE_LABELS.get(name, name)
    if not engine.get("installed") and not engine.get("running"):
        return f"{label:<10}not found"
    parts: list[str] = [str(engine.get("version") or "version unknown")]
    if engine.get("build") and engine.get("build") != engine.get("version"):
        parts.append(f"build {engine['build']}")
    if name == "ollama":
        if engine.get("running") is True:
            parts.append(f"running at {engine.get('url')}")
        elif engine.get("running") is False:
            parts.append("not running")
        else:
            parts.append("server not checked (OLLAMA_HOST is not local)")
    if name == "docker":
        if engine.get("running") is True:
            parts.append("daemon running")
        elif engine.get("running") is False:
            parts.append("daemon not reachable")
        else:
            parts.append("daemon not checked (endpoint is not local)")
    if name == "wsl":
        count = len(_list(engine.get("distributions")))
        parts.append(f"{count} distribution{'s' if count != 1 else ''}")
    return f"{label:<10}{', '.join(parts)}"


def format_report(report: Mapping[str, Any]) -> str:
    """A plain-text summary of a report (no color codes)."""
    lines: list[str] = []

    def row(label: str, text: str) -> None:
        lines.append(f"{label:<10}{text}".rstrip())

    os_info = _dict(report.get("os"))
    cpu = _dict(report.get("cpu"))
    lines.append(
        f"Stepswitch doctor (report schema {report.get('schema')}, {report.get('created')})"
    )
    lines.append("")

    os_parts = [str(os_info.get("name") or os_info.get("system") or "unknown OS")]
    if os_info.get("windows_build"):
        os_parts.append(f"build {os_info['windows_build']}")
    elif os_info.get("release"):
        os_parts.append(str(os_info["release"]))
    os_parts.append(str(os_info.get("arch") or "unknown architecture"))
    if os_info.get("wsl"):
        os_parts.append("WSL")
    row("OS", ", ".join(os_parts))

    features = _dict(cpu.get("features"))
    present = [name for name, value in features.items() if value is True]
    unknown = [name for name, value in features.items() if value is None]
    row(
        "CPU",
        f"{cpu.get('model') or 'unknown model'} ({cpu.get('arch') or '?'}),"
        f" {cpu.get('cores_physical') or '?'} cores, {cpu.get('cores_logical') or '?'} threads",
    )
    row("", f"features: {' '.join(present) or 'none detected'}")
    if unknown:
        row("", f"unknown: {' '.join(unknown)}")
    row("RAM", f"{_size(report.get('ram_gb'))} total, {_size(report.get('ram_available_gb'))} free")

    gpus = [_dict(gpu) for gpu in _list(report.get("gpus"))]
    if not gpus:
        row("GPUs", "none detected")
    for gpu in gpus:
        details = [str(gpu.get("backend") or "?"), _size(gpu.get("vram_gb"))]
        if gpu.get("vram_free_gb") is not None:
            details[-1] += f" ({_size(gpu.get('vram_free_gb'))} free)"
        if gpu.get("unified_memory") is True:
            details.append("unified memory")
        if gpu.get("compute_capability"):
            details.append(f"compute capability {gpu['compute_capability']}")
        if gpu.get("driver"):
            details.append(f"driver {gpu['driver']}")
        if gpu.get("cuda_version"):
            details.append(f"CUDA {gpu['cuda_version']}")
        details.append(f"FP8 {_yes_no(gpu.get('native_fp8'))}")
        details.append(f"FP4 {_yes_no(gpu.get('native_fp4'))}")
        row(f"GPU {gpu.get('index')}", f"{gpu.get('name') or 'unnamed'}: {', '.join(details)}")

    disk = _dict(report.get("disk"))
    first = True
    for label in ("models_dir", "project"):
        entry = _dict(disk.get(label))
        if entry:
            name = "models" if label == "models_dir" else "project"
            text = (
                f"{name}: {_size(entry.get('free_gb'))} free of {_size(entry.get('total_gb'))}"
                f" at {entry.get('path')}"
            )
            row("Disk" if first else "", text)
            first = False

    engines = _dict(report.get("engines"))
    for index, (name, engine) in enumerate(engines.items()):
        row("Engines" if index == 0 else "", _engine_line(str(name), _dict(engine)))

    sqlite_info = _dict(report.get("sqlite"))
    wal_safe = sqlite_info.get("wal_safe")
    wal = "WAL safe" if wal_safe is True else "WAL NOT safe" if wal_safe is False else "WAL unknown"
    row("SQLite", f"{sqlite_info.get('version') or 'unknown'}, {wal}")
    python = _dict(report.get("python"))
    row("Python", f"{python.get('version') or '?'} ({python.get('implementation') or '?'})")

    profile = _dict(report.get("profile")) or recommend(report)
    lines.append("")
    row("Profile", f"tier {profile.get('tier')}: {profile.get('reason')}")
    row("", f"engines: {', '.join(_list(profile.get('engines_preferred')))}")
    row("", f"quantization: {', '.join(_list(profile.get('quant_preference')))}")
    context = _dict(profile.get("context"))
    row(
        "",
        "T0 context: "
        + ", ".join(f"{role} {size}" for role, size in context.items())
        + f"; download cap {profile.get('max_gb')} GB",
    )
    for title, key in (("Notes", "notes"), ("Warnings", "warnings"), ("Problems", "problems")):
        source = profile if key == "notes" else report
        items = [str(item) for item in _list(source.get(key))]
        if items:
            lines.append("")
            for index, item in enumerate(items):
                row(title if index == 0 else "", f"- {item}")
    return "\n".join(lines) + "\n"
