"""C-017: goes from install to first completed task in under 5 minutes on a CPU-only machine.

Proof (SPEC §18.5, ADR 0011, ADR 0033): the README's quickstart, scripted, in a clean CPU-only
Linux x86-64 container. The container is `python:3.12-slim`, started with
`--memory=8g --memory-swap=8g`, eight CPUs (`--cpuset-cpus=0-7`) and no GPU devices. Each
step is timed:

1. **install**: `sh install.sh`, with `STEPSWITCH_PACKAGE` set to a wheel built from these
   sources. It downloads the pinned uv installer, checks its SHA-256 and runs it. It then
   installs Stepswitch with `uv tool install`, whose dependencies come from the package index,
   and runs `ssw prefetch`, which downloads the tokenizer files.
2. **init**: `ssw init` in the toy project (below).
3. **pull**: `ssw models pull --tier cpu --yes`.
   - Its model part is the download the claim excludes and reports separately. The tier's
     model files are mounted read-only from the host's verified store into the default models
     directory, so the step finds them installed instead of downloading them. The evidence
     records their size, from which a reader can work out the download time at their own
     bandwidth.
   - The step also installs the pinned llama.cpp build. That install counts toward the total,
     using the seconds the command reports for it (`engine.seconds` in its JSON).
4. **run**: `ssw run "<task>"`, which must end `done` with the project's tests run and
   passing in the task's gates.

The timed total is install + init + engine install + run. Before the clock starts, the
container installs the README's prerequisites with apt: git, curl, ca-certificates and
libgomp1, which a CPU-only desktop already has. It also sets the toy project up the way a
user's project already is: the bundled calc-bug repository, committed to git, with its own
`.venv` holding pytest and ignored through `.git/info/exclude`.

Needs Docker with Linux x86-64 containers, network access, and the tiny-tier models installed
on the host (`ssw models pull --tier cpu`). Elsewhere the claim is not applicable or pending.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from stepswitch import registry
from tests.claims import evidence
from tests.claims.local_models import ROOT, models_dir
from tests.claims.test_c001_cpu_only_8gb import COLLECT_PY, _build_wheel, _docker_linux
from tests.claims.toy import REPOS, TOY_INSTRUCTION

pytestmark = [pytest.mark.claim("C-017")]

IMAGE = "python:3.12-slim"
GIB = 1024**3
TIMEOUT_S = 3600
# Where Stepswitch keeps models by default for root in the container (stepswitch.paths).
CONTAINER_MODELS = "/root/.local/share/stepswitch/models"

RUN_SH = """\
set -eu
export DEBIAN_FRONTEND=noninteractive PYTHONUTF8=1
apt-get update -qq >/dev/null
apt-get install -y -qq --no-install-recommends git curl ca-certificates libgomp1 >/dev/null
mark() { printf '%s %s\\n' "$1" "$(date +%s.%N)" >> /out/marks; }
mkdir -p /work && cp -r /toy /work/project && cd /work/project
git init -q && git add -A
git -c user.name=toy -c user.email=toy@example.invalid commit -qm toy
python3 -m venv .venv && .venv/bin/python -m pip install -q pytest > /out/project.log 2>&1
echo ".venv/" >> .git/info/exclude
wheel=$(ls /dist/stepswitch-*.whl)
mark start
STEPSWITCH_PACKAGE="$wheel" sh /src/install.sh > /out/install.log 2>&1
mark installed
export PATH="$HOME/.local/bin:$PATH"
ssw init > /out/init.log 2>&1
mark initialized
ssw models pull --tier cpu --yes --json > /out/pull.json 2> /out/pull.stderr
mark pulled
set +e
ssw run "$TASK" --json > /out/result.json 2> /out/run.stderr
echo $? > /out/exit_code
set -e
mark ran
python3 /harness/collect.py > /out/container.json
python3 -c "import os; print(len(os.sched_getaffinity(0)))" > /out/affinity
cp -r .stepswitch/runs /out/runs
"""


def _tier() -> tuple[list[str], int] | None:
    """`-v` arguments mounting the `cpu` tier's model directories into the container's default
    models directory, and their total size; None if one is not installed."""
    models = models_dir()
    mounts: list[str] = []
    size = 0
    for entry in registry.for_tier("cpu"):
        if not entry.installed(models):
            return None
        directory = entry.local_dir(models)
        mounts += ["-v", f"{directory}:{CONTAINER_MODELS}/{directory.name}:ro"]
        size += sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
    return mounts, size


def _marks(path: Path) -> dict[str, float]:
    marks: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition(" ")
        marks[name] = float(value)
    return marks


def test_install_to_first_task_under_five_minutes(tmp_path: Path) -> None:
    limits = evidence.thresholds("C-017")
    if _docker_linux() is None:
        pytest.skip("not applicable here: needs Docker running Linux x86-64 containers (ADR 0011)")
    tier = _tier()
    if tier is None:
        pytest.skip("pending: the cpu tier is not fully installed (`ssw models pull --tier cpu`)")
    mounts, model_bytes = tier
    dist, harness, out = tmp_path / "dist", tmp_path / "harness", tmp_path / "out"
    for directory in (harness, out):
        directory.mkdir()
    wheel = _build_wheel(dist)
    (harness / "run.sh").write_text(RUN_SH, encoding="utf-8", newline="\n")
    (harness / "collect.py").write_text(COLLECT_PY, encoding="utf-8", newline="\n")
    cpus = int(limits["cpus"])
    flags = [
        f"--memory={limits['memory_limit_gb']}g",
        f"--memory-swap={limits['memory_limit_gb']}g",
        f"--cpuset-cpus=0-{cpus - 1}",
    ]
    command = [
        "docker", "run", "--rm", *flags,
        "-e", f"TASK={TOY_INSTRUCTION}",
        "-v", f"{dist}:/dist:ro",
        "-v", f"{ROOT / 'install.sh'}:/src/install.sh:ro",
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
    logs = {
        name: (out / name).read_text(encoding="utf-8", errors="replace")[-3000:]
        for name in ("project.log", "install.log", "init.log", "pull.stderr", "run.stderr")
        if (out / name).is_file()
    }
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-3000:], logs)
    marks = _marks(out / "marks")
    pull = json.loads((out / "pull.json").read_text(encoding="utf-8"))
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    container = json.loads((out / "container.json").read_text(encoding="utf-8"))
    usable_cpus = int((out / "affinity").read_text(encoding="utf-8").strip())
    engine = pull.get("engine") or {}
    install_s = marks["installed"] - marks["start"]
    init_s = marks["initialized"] - marks["installed"]
    pull_s = marks["pulled"] - marks["initialized"]
    engine_s = float(engine.get("seconds") or 0.0)
    run_s = marks["ran"] - marks["pulled"]
    total_s = install_s + init_s + engine_s + run_s
    gates = {g["name"]: g["status"] for g in (result.get("verdict") or {}).get("results", [])}
    tested = [name for name in ("impacted_tests", "full_tests") if gates.get(name) == "passed"]
    digest = subprocess.run(
        ["docker", "image", "inspect", IMAGE, "--format", "{{json .RepoDigests}}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    evidence.write(
        "C-017",
        "quickstart",
        {
            "image": IMAGE,
            "image_digests": json.loads(digest) if digest.startswith("[") else digest,
            "wheel": wheel.name,
            "docker_flags": flags,
            "container": {k: container[k] for k in ("machine", "system", "cgroup")},
            "usable_cpus": usable_cpus,
            "gpu_devices_visible": len(container["gpu_device_nodes"]),
            "steps_seconds": {
                "install": round(install_s, 1),
                "init": round(init_s, 1),
                "engine_install": round(engine_s, 1),
                "first_task": round(run_s, 1),
            },
            "total_seconds": round(total_s, 1),
            "total_minutes": round(total_s / 60, 2),
            "excluded": {
                "model_pull_seconds": round(pull_s - engine_s, 1),
                "model_files": "mounted read-only from the host's verified store",
                "model_bytes": model_bytes,
                "tier_models": sorted(e.id for e in registry.for_tier("cpu")),
                "installed": pull.get("installed"),
                "downloaded": pull.get("downloaded"),
            },
            "engine": {k: engine.get(k) for k in ("name", "status", "tag")},
            "final_status": result["status"],
            "stop_reason": result["stop_reason"],
            "task_steps": result["steps"],
            "gates": gates,
        },
    )
    assert container["machine"] in {"x86_64", "amd64"} and container["system"] == "Linux"
    assert usable_cpus == cpus, usable_cpus
    assert len(container["gpu_device_nodes"]) == 0, container
    assert engine.get("status") == "installed", (pull, logs.get("pull.stderr"))
    assert not pull.get("downloaded"), pull  # nothing was downloaded: the files were mounted
    assert result["status"] == limits["final_status"], (result, logs.get("run.stderr"))
    assert tested, gates  # the project's tests ran in the gates and passed
    assert total_s / 60 < limits["max_minutes_excluding_downloads"], (
        f"{total_s / 60:.2f} min",
        {"install": install_s, "init": init_s, "engine": engine_s, "run": run_s},
    )
