"""Record the README's demo (docs/assets/demo.gif) from docs/demo/demo.tape.

The tape runs in VHS's container image (`IMAGE`, pinned by digest). Before it starts, the
container:

1. installs git, curl, ca-certificates, libgomp1 and python3-venv with apt;
2. sets up the calc-bug toy project the way a user's project is: the demo's policy
   (docs/demo/stepswitch.toml), committed to git, and a `.venv` holding pytest;
3. installs Stepswitch from a wheel of these sources with install.sh;
4. writes `.stepswitch/local.toml` with one endpoint, this machine's Ollama, which the
   container reaches at host.docker.internal;
5. runs `ssw models pull --tier cpu --yes`. The tier's model files are mounted read-only from
   this machine's models directory, so the command installs only the llama.cpp build.

Needs Docker with Linux containers, the cpu tier's models (`ssw models pull --tier cpu`), and
Ollama on this machine with the demo's larger models pulled (`OLLAMA_MODELS`).

Usage: uv run python scripts/record_demo.py [--validate]
  --validate  only parse the tape with `vhs validate`; needs Docker, not the models
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from stepswitch import config as config_module
from stepswitch import registry

ROOT = Path(__file__).resolve().parents[1]
IMAGE = (
    "ghcr.io/charmbracelet/vhs:v0.12.1"
    "@sha256:ea49a6a1c529be83153e88321892b5585964418f0b9055e8c1e0d732194234a3"
)
DEMO = ROOT / "docs" / "demo"
GIF = ROOT / "docs" / "assets" / "demo.gif"
TOY = ROOT / "bench" / "repos" / "calc-bug"
# Where Stepswitch keeps models by default for root in the container (stepswitch.paths).
CONTAINER_MODELS = "/root/.local/share/stepswitch/models"
OLLAMA_URL = "http://host.docker.internal:11434"
OLLAMA_MODELS = ("qwen2.5:32b", "gpt-oss:120b")
LOCAL_TOML = f"""\
# The recording container's endpoint (scripts/record_demo.py).
schema = 1

[[endpoints]]
name = "ollama"
url = "{OLLAMA_URL}"
engine = "ollama"
local = true  # this machine's Ollama, seen from the container
formats = ["ollama-native", "chat-completions"]
models = [{", ".join(f'"{model}"' for model in OLLAMA_MODELS)}]
"""
SETUP_SH = """\
set -eu
export DEBIAN_FRONTEND=noninteractive PYTHONUTF8=1
apt-get update -qq >/dev/null
apt-get install -y -qq --no-install-recommends \\
    git curl ca-certificates libgomp1 python3-venv >/dev/null
mkdir -p /work && cp -r /toy /work/project && cd /work/project
cp /demo/stepswitch.toml stepswitch.toml
git init -q && git add -A
git -c user.name=demo -c user.email=demo@example.invalid commit -qm calc
python3 -m venv .venv && .venv/bin/python -m pip install -q pytest
echo ".venv/" >> .git/info/exclude
STEPSWITCH_PACKAGE="$(ls /dist/stepswitch-*.whl)" sh /src/install.sh
export PATH="$HOME/.local/bin:$PATH"
mkdir -p .stepswitch && cp /harness/local.toml .stepswitch/local.toml
ssw models pull --tier cpu --yes
mkdir -p /out/docs/assets && cd /out
vhs /demo/demo.tape
"""


def models_dir() -> Path:
    override = os.environ.get("STEPSWITCH_MODELS_DIR")
    if override:
        return Path(override).expanduser()
    return config_module.load(ROOT).models_dir()


def tier_mounts() -> list[str]:
    """`-v` arguments mounting the cpu tier's model directories, read-only."""
    models = models_dir()
    mounts: list[str] = []
    for entry in registry.for_tier("cpu"):
        if not entry.installed(models):
            sys.exit(f"{entry.id} is not installed: run `ssw models pull --tier cpu` first")
        directory = entry.local_dir(models)
        mounts += ["-v", f"{directory}:{CONTAINER_MODELS}/{directory.name}:ro"]
    return mounts


def docker() -> str:
    found = shutil.which("docker")
    if found is None:
        sys.exit("docker is not on PATH")
    return found


def validate() -> int:
    command = [docker(), "run", "--rm", "-v", f"{DEMO}:/demo:ro", IMAGE, "validate"]
    return subprocess.run([*command, "/demo/demo.tape"], check=False).returncode


def record() -> int:
    mounts = tier_mounts()
    with tempfile.TemporaryDirectory(prefix="stepswitch-demo-") as tmp:
        work = Path(tmp)
        dist, harness, out = work / "dist", work / "harness", work / "out"
        for directory in (dist, harness, out):
            directory.mkdir()
        build = ["uv", "build", "--wheel", "--out-dir", str(dist), str(ROOT)]
        subprocess.run(build, check=True)
        (harness / "setup.sh").write_text(SETUP_SH, encoding="utf-8", newline="\n")
        (harness / "local.toml").write_text(LOCAL_TOML, encoding="utf-8", newline="\n")
        command = [
            docker(), "run", "--rm", "--entrypoint", "bash",
            "--add-host=host.docker.internal:host-gateway",
            "-v", f"{dist}:/dist:ro",
            "-v", f"{ROOT / 'install.sh'}:/src/install.sh:ro",
            "-v", f"{DEMO}:/demo:ro",
            "-v", f"{TOY}:/toy:ro",
            "-v", f"{harness}:/harness:ro",
            "-v", f"{out}:/out",
            *mounts,
            IMAGE, "/harness/setup.sh",
        ]  # fmt: skip
        code = subprocess.run(command, check=False).returncode
        recorded = out / "docs" / "assets" / "demo.gif"
        if code != 0 or not recorded.is_file():
            print(f"recording failed (exit {code})", file=sys.stderr)
            return code or 1
        GIF.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(recorded, GIF)
    print(f"wrote {GIF} ({GIF.stat().st_size:,} bytes)")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--validate", action="store_true", help="only parse the tape")
    args = parser.parse_args(argv)
    return validate() if args.validate else record()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
