"""C-001: runs end to end on a CPU-only machine with 8 GB RAM.

Proof (SPEC §18.5, ADR 0011): `ssw run` completes a bundled toy task with tiny-tier models
inside a Linux x86-64 container started with `--memory=8g --memory-swap=8g` and no GPU
devices. Stepswitch is installed in the container from a wheel built from these sources; the
container installs the pinned llama.cpp CPU build the way a user's machine would, and the
tiny-tier model files (the registry's `cpu` tier, what `ssw models pull --tier cpu` installs)
are mounted read-only; models outside the tier are not. The evidence records the container's cgroup
memory limits (memory and memory plus swap), its peak memory, the GPU device nodes it can see
(none), and the task's final status.

Needs Docker with Linux x86-64 containers and the tiny-tier model installed on the host
(`ssw models pull --tier cpu`). Elsewhere the claim is not applicable or pending.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from stepswitch import registry
from tests.claims import evidence
from tests.claims.local_models import ROOT, installed, models_dir
from tests.claims.toy import REPOS, TOY_INSTRUCTION

pytestmark = [pytest.mark.claim("C-001")]

IMAGE = "python:3.12-slim"
MODEL_ID = "qwen3-4b-instruct-2507"
GIB = 1024**3
TIMEOUT_S = 3600

RUN_SH = """\
set -eu
export DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUTF8=1
apt-get update -qq >/dev/null
apt-get install -y -qq --no-install-recommends git libgomp1 ca-certificates >/dev/null
pip install -q --no-cache-dir /dist/*.whl pytest
python -c "from stepswitch.serve.install import install_llamacpp; install_llamacpp(echo=print)"
mkdir -p /work && cp -r /toy /work/project && cd /work/project
git init -q && git add -A
git -c user.name=toy -c user.email=toy@example.invalid commit -qm toy
ssw init --no-detect >/dev/null
printf '\\n[storage]\\nmodels_dir = "/models"\\n' >> .stepswitch/local.toml
set +e
ssw run "$TASK" --json > /out/result.json 2> /out/run.stderr
echo $? > /out/exit_code
set -e
python /harness/collect.py > /out/container.json
cp -r .stepswitch/runs /out/runs
"""

COLLECT_PY = """\
import glob, json, os, platform

def read(name):
    try:
        with open("/sys/fs/cgroup/" + name) as handle:
            return handle.read().strip()
    except OSError:
        return None

def number(text):
    return int(text) if text and text.isdigit() else None

devices = sorted(
    p for pattern in ("/dev/nvidia*", "/dev/dri", "/dev/dri/*", "/dev/kfd", "/dev/accel*")
    for p in glob.glob(pattern)
)
print(json.dumps({
    "machine": platform.machine(),
    "system": platform.system(),
    "cpus": os.cpu_count(),
    "cgroup": {
        "memory.max": read("memory.max"),
        "memory.swap.max": read("memory.swap.max"),
        "memory.peak": read("memory.peak"),
    },
    "memory_max_bytes": number(read("memory.max")),
    "swap_max_bytes": number(read("memory.swap.max")),
    "peak_bytes": number(read("memory.peak")),
    "gpu_device_nodes": devices,
}))
"""


def _docker_linux() -> dict[str, Any] | None:
    if shutil.which("docker") is None:
        return None
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if info.returncode != 0:
        return None
    data: dict[str, Any] = json.loads(info.stdout)
    if data.get("OSType") != "linux" or data.get("Architecture") not in {"x86_64", "amd64"}:
        return None
    return data


def _build_wheel(dest: Path) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("pending: uv is needed to build the wheel for the container")
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(dest), str(ROOT)],
        capture_output=True,
        text=True,
        timeout=600,
        check=True,
    )
    wheels = sorted(dest.glob("stepswitch-*.whl"))
    assert wheels, "no wheel built"
    return wheels[-1]


def _tier_mounts() -> list[str] | None:
    """`-v` arguments for every model directory of the `cpu` tier, or None if one is missing."""
    models = models_dir()
    mounts: list[str] = []
    for entry in registry.for_tier("cpu"):
        if not entry.installed(models):
            return None
        directory = entry.local_dir(models)
        mounts += ["-v", f"{directory}:/models/{directory.name}:ro"]
    return mounts


def test_toy_task_runs_in_cpu_only_8gb_container(tmp_path: Path) -> None:
    limits = evidence.thresholds("C-001")
    docker = _docker_linux()
    if docker is None:
        pytest.skip("not applicable here: needs Docker running Linux x86-64 containers (ADR 0011)")
    found = installed(MODEL_ID)
    if found is None:
        pytest.skip(f"pending: {MODEL_ID} is not installed (`ssw models pull --tier cpu`)")
    mounts = _tier_mounts()
    if mounts is None:
        pytest.skip("pending: the cpu tier is not fully installed (`ssw models pull --tier cpu`)")
    dist, harness, out = tmp_path / "dist", tmp_path / "harness", tmp_path / "out"
    for directory in (harness, out):
        directory.mkdir()
    wheel = _build_wheel(dist)
    (harness / "run.sh").write_text(RUN_SH, encoding="utf-8", newline="\n")
    (harness / "collect.py").write_text(COLLECT_PY, encoding="utf-8", newline="\n")
    memory = f"{limits['memory_limit_gb']}g"
    swap = f"{limits['memory_swap_limit_gb']}g"
    command = [
        "docker", "run", "--rm",
        f"--memory={memory}", f"--memory-swap={swap}",
        "-e", f"TASK={TOY_INSTRUCTION}",
        "-v", f"{dist}:/dist:ro",
        *mounts,
        "-v", f"{REPOS / 'calc-bug'}:/toy:ro",
        "-v", f"{harness}:/harness:ro",
        "-v", f"{out}:/out",
        IMAGE, "bash", "/harness/run.sh",
    ]  # fmt: skip
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_S,
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-3000:])
    container = json.loads((out / "container.json").read_text(encoding="utf-8"))
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    digest = subprocess.run(
        ["docker", "image", "inspect", IMAGE, "--format", "{{json .RepoDigests}}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    memory_gb = (container["memory_max_bytes"] or 0) / GIB
    swap_total_gb = (
        (container["memory_max_bytes"] or 0) + (container["swap_max_bytes"] or 0)
    ) / GIB
    peak = container["peak_bytes"]
    evidence.write(
        "C-001",
        "container",
        {
            "image": IMAGE,
            "image_digests": json.loads(digest) if digest.startswith("[") else digest,
            "wheel": wheel.name,
            "model": MODEL_ID,
            "tier_models": sorted(e.id for e in registry.for_tier("cpu")),
            "docker_flags": [f"--memory={memory}", f"--memory-swap={swap}"],
            "container": {k: container[k] for k in ("machine", "system", "cpus", "cgroup")},
            "memory_limit_gb": round(memory_gb, 3),
            "memory_swap_limit_gb": round(swap_total_gb, 3),
            "peak_memory_gb": round(peak / GIB, 3) if peak else None,
            "gpu_devices_visible": len(container["gpu_device_nodes"]),
            "final_status": result["status"],
            "stop_reason": result["stop_reason"],
            "steps": result["steps"],
            "budget": result["budget"],
            "changed_files": result["changed_files"],
        },
    )
    assert container["machine"] in {"x86_64", "amd64"} and container["system"] == "Linux"
    assert memory_gb == limits["memory_limit_gb"], container["cgroup"]
    assert swap_total_gb == limits["memory_swap_limit_gb"], container["cgroup"]
    assert len(container["gpu_device_nodes"]) == limits["gpu_devices_visible"], container
    assert result["status"] == limits["final_status"], (
        result,
        (out / "run.stderr").read_text(encoding="utf-8")[-3000:],
    )
