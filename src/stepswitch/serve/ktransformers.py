"""The KTransformers adapter: CPU-GPU hybrid MoE for the slow lane, as an SGLang profile (SPEC
§4.2, §14 RN5; ADR 0008, ADR 0030).

KTransformers runs the experts of a large mixture-of-experts model on the CPU (AMX or AVX
kernels from `kt-kernel`) and the rest on the GPU, through `sglang-kt`, its fork of SGLang (the
official `sglang` package must not be in the same environment). The adapter is the SGLang
adapter with these differences (docs/RESEARCH.md, KTransformers 0.7.2):

* **Launcher.** `python -m sglang.launch_server` from the interpreter configured as
  `[engines.ktransformers] python` (or a `command`); there is no console-script lookup.
* **Flags.** The recipe (or `extra_args`) must set `--kt-method` (`AMXINT4`, `AMXINT8`,
  `RAWINT4`, `FP8`, `FP8_PERCHANNEL`, `BF16` or `LLAMAFILE`) and `--kt-weight-path` (the CPU
  expert weights);
  `--kt-cpuinfer`, `--kt-threadpool-count`, `--kt-num-gpu-experts` and
  `--kt-max-deferred-experts-per-token` are optional. Everything SGLang's adapter owns stays
  owned.
* **Platform.** Linux on x86-64 only (glibc 2.35 or newer, Python 3.11 or 3.12) and a CPU
  with AVX2, which is all `LLAMAFILE` needs. The other methods need more, read from
  `/proc/cpuinfo` (`METHOD_CPU_FLAGS`): AMX tiles for `AMXINT4` and `AMXINT8`, AVX-512 F and BW
  for `RAWINT4`, AVX-512 BF16 for `BF16`, and AVX-512 BF16 and VBMI for `FP8`. The kernels'
  needs for `FP8_PERCHANNEL` are not documented, so it is not checked. Elsewhere, run it on a
  Linux host and add it as an endpoint with `engine = "ktransformers"`.
* **Readiness.** `/health`: the fork has no `/ready`. `/health` answers 503 while the server
  starts and is exempt from the key; by default it also generates one token per call
  (`SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION`, default true), which the adapter turns off.
* **Key.** As for SGLang: a private `--config` file. Assumption, not yet run against the fork:
  that `sglang-kt` keeps SGLang's `--config` option (docs/RESEARCH.md).

Models on this engine are slow-lane workers (tier `sl`): asynchronous jobs only.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from stepswitch.registry import ModelEntry
from stepswitch.serve.base import EngineError
from stepswitch.serve.sglang import SglangEngine

ENGINE = "ktransformers"
KT_METHODS = ("AMXINT4", "AMXINT8", "RAWINT4", "FP8", "FP8_PERCHANNEL", "BF16", "LLAMAFILE")
# The `/proc/cpuinfo` flags each method's CPU kernels need beyond AVX2 (kt-kernel 0.7.2 README).
METHOD_CPU_FLAGS: dict[str, tuple[str, ...]] = {
    "AMXINT4": ("amx_tile",),
    "AMXINT8": ("amx_tile",),
    "RAWINT4": ("avx512f", "avx512bw"),
    "FP8": ("avx512_bf16", "avx512vbmi"),
    "BF16": ("avx512_bf16",),
}
REQUIRED_FLAGS = ("--kt-method", "--kt-weight-path")
X86_MACHINES = frozenset({"x86_64", "amd64"})
CPUINFO = Path("/proc/cpuinfo")


def cpu_flags(cpuinfo: Path = CPUINFO) -> set[str] | None:
    """The CPU feature flags from `/proc/cpuinfo`, or None where it cannot be read."""
    try:
        text = cpuinfo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "flags":
            return set(value.split())
    return None


def flag_value(args: Sequence[str], flag: str) -> str | None:
    """The value of `flag` (`--flag value` or `--flag=value`), the last one when repeated."""
    found: str | None = None
    items = list(args)
    for index, arg in enumerate(items):
        name, sep, value = arg.partition("=")
        if name == flag and sep:
            found = value
        elif arg == flag and index + 1 < len(items):
            found = items[index + 1]
    return found


class KTransformersEngine(SglangEngine):
    """Starts and controls `sglang-kt` servers with KTransformers' CPU experts."""

    name = ENGINE
    label = "KTransformers"
    dist = "sglang-kt"
    dist_aliases = ()
    requires = ("kt-kernel",)
    script = None
    script_args = ()
    module_args = ("-m", "sglang.launch_server")
    install_hint = (
        "install kt-kernel and sglang-kt (not the official sglang) in their own environment and "
        "set [engines.ktransformers] python in .stepswitch/local.toml"
    )
    health_path = "/health"
    # `/health` without its one-token generation: a cheap check of a slow server.
    fixed_env = {**SglangEngine.fixed_env, "SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION": "false"}

    def __init__(self, *, cpuinfo: Path = CPUINFO, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.cpuinfo = cpuinfo

    def platform_problem(self, system: str, machine: str) -> str | None:
        if not system.startswith("linux") or machine.lower() not in X86_MACHINES:
            return (
                f"KTransformers runs on Linux x86-64 only (this host is {system} {machine}); run "
                'it on a Linux host and add it as an endpoint with engine = "ktransformers"'
            )
        flags = cpu_flags(self.cpuinfo)
        if flags is not None and "avx2" not in flags:
            return "KTransformers needs a CPU with AVX2"
        return None

    def check_kt_args(self, entry: ModelEntry, args: Sequence[str]) -> None:
        missing = [flag for flag in REQUIRED_FLAGS if flag_value(args, flag) is None]
        if missing:
            raise EngineError(
                f"the KTransformers recipe of {entry.id} needs {' and '.join(missing)}"
            )
        method = str(flag_value(args, "--kt-method"))
        if method not in KT_METHODS:
            raise EngineError(f"--kt-method is one of {', '.join(KT_METHODS)}, not {method!r}")
        flags = cpu_flags(self.cpuinfo)  # None where /proc/cpuinfo cannot be read
        lacking = [
            f for f in METHOD_CPU_FLAGS.get(method, ()) if flags is not None and f not in flags
        ]
        if lacking:
            raise EngineError(
                f"--kt-method {method} needs a CPU with {', '.join(lacking)}, which this one "
                "lacks; LLAMAFILE needs only AVX2"
            )

    def command(
        self,
        entry: ModelEntry,
        model: str,
        *,
        port: int,
        ctx_size: int | None = None,
        extra_args: Sequence[str] = (),
    ) -> list[str]:
        """The SGLang command line, with the `--kt-*` flags checked."""
        recipe = entry.engines.get(ENGINE)
        self.check_kt_args(entry, [*(recipe.args if recipe else []), *extra_args])
        return super().command(entry, model, port=port, ctx_size=ctx_size, extra_args=extra_args)
