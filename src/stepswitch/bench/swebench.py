"""SWE-bench suites (ADR 0042): real issues of real repositories, graded by the official harness.

A suite of kind `swebench` names instances of a SWE-bench dataset, never their contents. The rows
(issue text, base commit, the instance's container image and its evaluation script) are fetched
from the dataset through the egress module and kept in the state directory
(`<state>/bench/swebench/`), so no third-party text enters the repository. The manifest records
the dataset's revision and a digest of the rows a run used.

Each task run starts a container from the instance's own image (named `ssw-swe-...`, removed
when the run ends; nothing else in Docker is touched).

* `router` and worker contestants run `ssw run` inside that container, on the code under test.
  Its dependencies come from a volume built once per lockfile (`ssw-swe-deps-<digest>`: a
  relocatable CPython and the locked packages); the source is copied in when the container
  starts. The container's configuration lists the contestant's workers only, each on an
  endpoint without credentials, with loopback addresses mapped to the Docker host: keys never
  enter a container. The agent gets the problem statement and nothing else (no hints, no names
  of the evaluation's tests). The project settings per repository (interpreter, test command,
  gates) are in `PROJECTS`. The prediction is the diff of the task's branch, whatever the
  run's status, as the other suites score a run's diff.
* `mini-swe-agent:<worker>` runs mini-SWE-agent (pinned), the reference agent of the SWE-bench
  bash-only leaderboard, with its own SWE-bench runner and default configuration, on the
  worker's model and server. The runner starts and removes the instance containers itself,
  through the Docker socket. Its prediction is what it submits.

Grading: the official harness (`swebench`, pinned) evaluates each prediction in a Linux
container with the Docker socket mounted, never on the Windows interpreter (it writes the
evaluation scripts with CRLF line endings there). A run passes when its instance is resolved:
every FAIL_TO_PASS test passes and every PASS_TO_PASS test still passes. Grading runs beside
the next task run. The harness and mini-SWE-agent live in a volume built once per pinned
version (`ssw-swe-harness-<digest>`), with the Docker command line for mini-SWE-agent.

Building the volumes downloads the packages, an interpreter and the Docker command line inside
builder containers, and the instance images are pulled when missing: the bench announces both
before it starts. Rows have the usual fields (`stepswitch.bench.runner`).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import threading
import time
import tomllib
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from stepswitch import __version__, net, paths, tomlw
from stepswitch.bench.report import MANIFEST_FILE, RESULTS_FILE, write_report

KIND = "swebench"
MINI_PREFIX = "mini-swe-agent:"
GOLD = "gold"  # the dataset's reference patches, graded like a contestant's (ADR 0042)
SWEBENCH_VERSION = "5.0.2"
MINI_SWE_AGENT_VERSION = "2.4.6"
# Pinned beside mini-SWE-agent: litellm 1.106.0 imports `mcp` on every completion without
# depending on it (docs/RESEARCH.md, Phase 13).
LITELLM_VERSION = "1.106.0"
MCP_VERSION = "2.3.0"
UV_VERSION = "0.12.23"
DOCKER_CLI_VERSION = "27.0.3"
RUNTIME_PYTHON = "3.12"
BUILDER_IMAGE = "python:3.12-slim-bookworm"
CONTAINER_PREFIX = "ssw-swe-"
TESTBED = "/testbed"
TESTBED_PYTHON = "/opt/miniconda3/envs/testbed/bin/python"
DOCKER_HOST = "host.docker.internal"
DEPS_MOUNT = "/opt/ssw"
SOURCE_DIR = "/opt/ssw-src"
TASK_DIR = "/opt/ssw-task"
HARNESS_MOUNT = "/opt/h"
SOCKET = "/var/run/docker.sock"
DATASETS_SERVER = "https://datasets-server.huggingface.co/rows"
HUB_API = "https://huggingface.co/api/datasets/"
PAGE_ROWS = 100
GRADE_TIMEOUT_S = 1800
EVAL_LOGS = "logs/run_evaluation"  # the harness's log directory, under its working directory
PREDICTIONS_DIR = "predictions"  # each contestant's patches, in the harness's format
SCORING = (
    f"passes when the official harness (swebench {SWEBENCH_VERSION}) resolves the instance with"
    " the run's patch: every FAIL_TO_PASS test passes and every PASS_TO_PASS test still passes"
)
NOTICE = (
    "SWE-bench suite: instance images are pulled when missing, and the first run builds two"
    " Docker volumes (the Stepswitch runtime and the pinned harness), which downloads Python"
    " packages, an interpreter and the Docker command line inside builder containers."
)

# Django's own runner, with pytest-style selectors turned into test labels. It runs on the
# instance's interpreter (Python 3.6 for old Django versions): keep it Python 3.6+.
DJANGO_TESTS = '''\
"""Django's test runner, with pytest-style selectors turned into test labels.

`tests/backends/tests.py::LongNameTest::test_x` becomes `backends.tests.LongNameTest.test_x`;
labels and options pass through, and `tests` alone runs the whole suite. The test_sqlite
settings have no GIS database backend, and the runner stops before any test when a label is
in gis_tests, so those labels are left out with a note.
"""
import os
import subprocess
import sys

GIS = "gis_tests"


def label(arg):
    path, sep, rest = arg.partition("::")
    if arg.startswith("-"):
        return arg
    if path.endswith(".py"):
        path = path[:-3]
    elif not (sep or os.path.isdir(path)):
        return arg
    path = path.strip("/")
    if path in ("tests", ""):
        return None
    if path.startswith("tests/"):
        path = path[len("tests/"):]
    name = path.replace("/", ".")
    if rest:
        name += "." + rest.replace("::", ".")
    return name


def main(argv):
    labels = [name for name in (label(arg) for arg in argv) if name]
    gis = [name for name in labels if name == GIS or name.startswith(GIS + ".")]
    if gis:
        print("not run (test_sqlite has no GIS database backend): " + " ".join(gis))
        labels = [name for name in labels if name not in gis]
        if not labels:
            print("no test left to run")
            return 5
    command = [sys.executable, "tests/runtests.py", "--settings=test_sqlite", "--parallel", "1",
               "--verbosity", "2"] + labels
    return subprocess.call(command, env=dict(os.environ, PYTHONUNBUFFERED="1"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

PYTEST = ["{python}", "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider"]
# The project settings of each repository: the test command the agent's `test` tool and the
# gates run (as `[tracks.general.commands].tests`), and files written into the container.
PROJECTS: dict[str, dict[str, Any]] = {
    "django/django": {
        "tests": ["{python}", f"{TASK_DIR}/django_tests.py"],
        "files": {f"{TASK_DIR}/django_tests.py": DJANGO_TESTS},
    },
    "sphinx-doc/sphinx": {"tests": PYTEST},
}
DEFAULT_PROJECT: dict[str, Any] = {"tests": PYTEST}
# The whole suite of these repositories takes many minutes and fails in places on the images.
GATES = ["impacted_tests"]


class SwebenchError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------------------------


@dataclass
class Done:
    code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out

    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    def error(self) -> str:
        return self.stderr.decode("utf-8", errors="replace").strip()[-1500:]


class Docker:
    """The `docker` command line; the daemon does the work."""

    def __init__(self, program: str | None = None) -> None:
        found = program or shutil.which("docker")
        if not found:
            raise SwebenchError("SWE-bench suites need Docker: `docker` was not found on PATH")
        self.program = found

    def __call__(
        self, args: Sequence[str], *, stdin: bytes | None = None, timeout: float | None = None
    ) -> Done:
        try:
            done = subprocess.run(
                [self.program, *args],
                input=stdin if stdin is not None else b"",
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return Done(124, exc.stdout or b"", exc.stderr or b"", timed_out=True)
        except OSError as exc:
            return Done(127, b"", str(exc).encode())
        return Done(done.returncode, done.stdout, done.stderr)


DockerRun = Callable[..., Done]


def _check(done: Done, what: str) -> Done:
    if not done.ok:
        raise SwebenchError(f"{what} failed (exit {done.code}): {done.error()}")
    return done


def tar_bytes(files: Mapping[str, bytes | str]) -> bytes:
    """A tar archive of `files` (absolute POSIX paths), for `docker cp - <container>:/`."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in sorted(files.items()):
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = tarfile.TarInfo(name.lstrip("/"))
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def untar(data: bytes) -> dict[str, bytes]:
    """The regular files of a tar archive, by path."""
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive.getmembers():
            if member.isfile():
                handle = archive.extractfile(member)
                if handle is not None:
                    found[member.name.removeprefix("./")] = handle.read()
    return found


def package_data(root: Path) -> list[tuple[Path, str]]:
    """(directory, place in the package) of the data a wheel ships inside the package: the
    wheel's force-include table in pyproject.toml, without the bench suites."""
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        include = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise SwebenchError(f"{root}/pyproject.toml has no wheel force-include table") from exc
    return [
        (root / source, str(target))
        for source, target in include.items()
        if not str(source).startswith("bench/")
    ]


def source_files(root: Path) -> dict[str, bytes]:
    """The package source and its data (the registry, the open specs, the glossaries), laid out
    under SOURCE_DIR as an installed package sees them (`stepswitch/_registry`, ...)."""
    package = root / "src" / "stepswitch"
    data = package_data(root) if package.is_dir() else []
    if not data or not all(base.is_dir() for base, _ in data):
        raise SwebenchError(f"{root} is not a Stepswitch checkout (src/stepswitch and its data)")
    files: dict[str, bytes] = {}
    for base, prefix in ((package, "stepswitch"), *data):
        for path in sorted(base.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                rel = path.relative_to(base).as_posix()
                files[f"{SOURCE_DIR}/{prefix}/{rel}"] = path.read_bytes()
    return files


def digest(files: Mapping[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + b"\0" + hashlib.sha256(files[name]).digest())
    return h.hexdigest()


# --------------------------------------------------------------------------------------------
# Volumes: the Stepswitch runtime and the harness
# --------------------------------------------------------------------------------------------

_BUILD_DEPS = """\
set -eu
cat > /tmp/requirements.txt
pip install --quiet --root-user-action=ignore uv=={uv}
export UV_PYTHON_INSTALL_DIR={mount}/python UV_CACHE_DIR=/tmp/uv-cache
export UV_PYTHON_PREFERENCE=only-managed UV_LINK_MODE=copy
rm -rf {mount}/python {mount}/venv {mount}/STAMP
uv python install {python}
uv venv {mount}/venv --python "$(uv python find {python})"
uv pip install --python {mount}/venv/bin/python -r /tmp/requirements.txt
PURE="$({mount}/venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
echo {source} > "$PURE/stepswitch-source.pth"
echo {stamp} > {mount}/STAMP
"""

_BUILD_HARNESS = """\
set -eu
rm -rf {mount}/swebench {mount}/mini {mount}/bin {mount}/STAMP
python -m venv {mount}/swebench
{mount}/swebench/bin/pip install --quiet --root-user-action=ignore swebench=={swebench}
python -m venv {mount}/mini
{mount}/mini/bin/pip install --quiet --root-user-action=ignore mini-swe-agent=={mini} \\
    litellm=={litellm} mcp=={mcp}
mkdir -p {mount}/bin
python - <<'EOF'
import io, tarfile, urllib.request
url = "https://download.docker.com/linux/static/stable/x86_64/docker-{docker}.tgz"
data = urllib.request.urlopen(url, timeout=600).read()
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    payload = archive.extractfile("docker/docker").read()
with open("{mount}/bin/docker", "wb") as handle:
    handle.write(payload)
EOF
chmod 755 {mount}/bin/docker
{mount}/swebench/bin/pip freeze > {mount}/swebench.freeze
{mount}/mini/bin/pip freeze > {mount}/mini.freeze
echo {stamp} > {mount}/STAMP
"""


def requirements(root: Path) -> str:
    """The locked runtime dependencies (`uv export`), without the project itself."""
    uv = shutil.which("uv")
    if uv is None:
        raise SwebenchError("SWE-bench suites need uv to export the locked dependencies")
    done = subprocess.run(
        [uv, "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project", "--quiet"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        error = done.stderr.decode("utf-8", errors="replace").strip()[-800:]
        raise SwebenchError(f"uv export failed: {error}")
    return done.stdout.decode("utf-8").replace("\r\n", "\n")


def _volume_ready(docker: DockerRun, volume: str, stamp: str, mount: str) -> bool:
    if not docker(["volume", "inspect", volume]).ok:
        return False
    found = docker(
        ["run", "--rm", "-v", f"{volume}:{mount}:ro", BUILDER_IMAGE, "cat", f"{mount}/STAMP"]
    )
    return found.ok and found.text().strip() == stamp


def ensure_deps_volume(
    docker: DockerRun, requirements_text: str, echo: Callable[[str], None]
) -> str:
    """The runtime volume for these locked dependencies, built when missing."""
    stamp = hashlib.sha256(
        f"{UV_VERSION}|{RUNTIME_PYTHON}|{requirements_text}".encode()
    ).hexdigest()[:16]
    volume = f"{CONTAINER_PREFIX}deps-{stamp}"
    if _volume_ready(docker, volume, stamp, DEPS_MOUNT):
        return volume
    echo(f"Building the Stepswitch runtime volume {volume} (one time per lockfile)")
    script = _BUILD_DEPS.format(
        uv=UV_VERSION, mount=DEPS_MOUNT, python=RUNTIME_PYTHON, source=SOURCE_DIR, stamp=stamp
    )
    name = f"{CONTAINER_PREFIX}build-{uuid.uuid4().hex[:8]}"
    _check(
        docker(
            ["run", "--rm", "-i", "--name", name, "-v", f"{volume}:{DEPS_MOUNT}", BUILDER_IMAGE]
            + ["sh", "-c", script],
            stdin=requirements_text.encode(),
            timeout=3600,
        ),
        "building the runtime volume",
    )
    return volume


def ensure_harness_volume(docker: DockerRun, echo: Callable[[str], None]) -> str:
    """The volume with the pinned harness, mini-SWE-agent and the Docker command line."""
    pins = (SWEBENCH_VERSION, MINI_SWE_AGENT_VERSION, LITELLM_VERSION, MCP_VERSION)
    stamp = hashlib.sha256("|".join([*pins, DOCKER_CLI_VERSION]).encode()).hexdigest()[:16]
    volume = f"{CONTAINER_PREFIX}harness-{stamp}"
    if _volume_ready(docker, volume, stamp, HARNESS_MOUNT):
        return volume
    echo(f"Building the harness volume {volume} (one time per pinned version)")
    script = _BUILD_HARNESS.format(
        mount=HARNESS_MOUNT,
        swebench=SWEBENCH_VERSION,
        mini=MINI_SWE_AGENT_VERSION,
        litellm=LITELLM_VERSION,
        mcp=MCP_VERSION,
        docker=DOCKER_CLI_VERSION,
        stamp=stamp,
    )
    name = f"{CONTAINER_PREFIX}build-{uuid.uuid4().hex[:8]}"
    _check(
        docker(
            ["run", "--rm", "--name", name, "-v", f"{volume}:{HARNESS_MOUNT}", BUILDER_IMAGE]
            + ["sh", "-c", script],
            timeout=3600,
        ),
        "building the harness volume",
    )
    return volume


def harness_versions(docker: DockerRun, volume: str) -> dict[str, str]:
    """`pip freeze` of the harness and of mini-SWE-agent, for the manifest."""
    found: dict[str, str] = {}
    for name in ("swebench", "mini"):
        done = docker(
            ["run", "--rm", "-v", f"{volume}:{HARNESS_MOUNT}:ro", BUILDER_IMAGE]
            + ["cat", f"{HARNESS_MOUNT}/{name}.freeze"]
        )
        found[name] = done.text() if done.ok else ""
    return found


# --------------------------------------------------------------------------------------------
# The dataset
# --------------------------------------------------------------------------------------------


@dataclass
class Rows:
    dataset: str
    split: str
    revision: str | None
    fetched: str
    rows: dict[str, dict[str, Any]]

    def select(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        missing = [i for i in ids if i not in self.rows]
        if missing:
            raise SwebenchError(
                f"{len(missing)} instance(s) are not in {self.dataset} ({self.split}):"
                f" {', '.join(missing[:5])}"
            )
        return [self.rows[i] for i in ids]


def rows_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    text = json.dumps(list(rows), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_file(root: Path, dataset: str, split: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "__", dataset)
    return paths.state_dir(root) / "bench" / "swebench" / f"{slug}-{split}.json"


def load_rows(
    root: Path,
    dataset: str,
    split: str,
    *,
    client: Any = None,
    echo: Callable[[str], None] = lambda _msg: None,
) -> Rows:
    """The dataset's rows, from the state directory or fetched once through the egress module."""
    cache = _cache_file(root, dataset, split)
    if cache.is_file():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return Rows(dataset, split, data.get("revision"), data["fetched"], data["rows"])
    echo(f"Fetching {dataset} ({split}) into {cache.parent}")
    owned = client is None
    http = client or net.client("bench: SWE-bench dataset", timeout=120)
    try:
        listing: list[dict[str, Any]] = []
        offset = 0
        while True:
            params = {"dataset": dataset, "config": "default", "split": split}
            params |= {"offset": str(offset), "length": str(PAGE_ROWS)}
            data = _get_json(http, DATASETS_SERVER, params)
            batch = [item["row"] for item in data.get("rows") or []]
            listing += batch
            offset += len(batch)
            total = data.get("num_rows_total")
            if not batch or (isinstance(total, int) and offset >= total):
                break
        info = _get_json(http, HUB_API + dataset, {})
    finally:
        if owned:
            http.close()
    rows = {str(row["instance_id"]): row for row in listing}
    fetched = datetime.now(UTC).isoformat(timespec="seconds")
    revision = info.get("sha") if isinstance(info, dict) else None
    cache.parent.mkdir(parents=True, exist_ok=True)
    body = {"dataset": dataset, "split": split, "revision": revision, "fetched": fetched}
    cache.write_text(
        json.dumps({**body, "rows": rows}, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    return Rows(dataset, split, revision, fetched, rows)


def _get_json(http: Any, url: str, params: Mapping[str, str]) -> Any:
    last: Exception | None = None
    for attempt in range(5):  # the rows service answers 502 now and then
        try:
            response = http.get(url, params=dict(params))
            if response.status_code < 500:
                response.raise_for_status()
                return response.json()
            last = SwebenchError(f"{url}: HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last = exc
        time.sleep(2.0 * (attempt + 1))
    raise SwebenchError(f"cannot fetch {url}: {last}")


# --------------------------------------------------------------------------------------------
# Contestants
# --------------------------------------------------------------------------------------------


def mini_worker(name: str) -> str | None:
    """The worker of a `mini-swe-agent:<worker>` contestant."""
    return name.removeprefix(MINI_PREFIX) if name.startswith(MINI_PREFIX) else None


def container_url(url: str) -> str:
    """An endpoint URL as a container reaches it: loopback hosts become the Docker host."""
    return re.sub(
        r"^(\w+://)(localhost|127\.\d+\.\d+\.\d+|\[::1\])(?=[:/]|$)",
        lambda m: m.group(1) + DOCKER_HOST,
        url,
    )


def _endpoint(config: Any, worker: str) -> Any:
    endpoint_name, _, model = worker.partition("/")
    for endpoint in getattr(config, "endpoints", None) or []:
        if endpoint.name == endpoint_name:
            if model not in endpoint.models:
                raise SwebenchError(f"{worker}: {endpoint_name} does not list {model}")
            if endpoint.auth != "none" or endpoint.resolved_api_key():
                raise SwebenchError(
                    f"{worker}: SWE-bench runs give the endpoint to a container, so only"
                    " endpoints without credentials can be used"
                )
            if not endpoint.is_local():
                raise SwebenchError(f"{worker}: SWE-bench suites use local endpoints only")
            return endpoint
    raise SwebenchError(f"{worker}: no endpoint named {endpoint_name}")


def container_config(config: Any, workers: Sequence[str], repo: str) -> str:
    """The `.stepswitch/local.toml` of a task run's container: the given workers' endpoints
    (no credentials, loopback mapped to the Docker host), their model settings, and the
    project settings of the repository."""
    endpoints: dict[str, dict[str, Any]] = {}
    models: dict[str, Any] = {}
    known = getattr(config, "models", None) or {}
    for worker in workers:
        endpoint = _endpoint(config, worker)
        model = worker.partition("/")[2]
        entry = endpoints.setdefault(
            endpoint.name,
            {
                "name": endpoint.name,
                "url": container_url(endpoint.url),
                "engine": endpoint.engine,
                "formats": list(endpoint.formats) or None,
                "format": endpoint.format,
                "local": True,
                "tool_parser": endpoint.tool_parser,
                "models": [],
            },
        )
        entry["models"].append(model)
        if isinstance(known.get(model), Mapping):
            models[model] = dict(known[model])
    project = PROJECTS.get(repo, DEFAULT_PROJECT)
    document: dict[str, Any] = {
        "schema": 1,
        "endpoints": [{k: v for k, v in e.items() if v is not None} for e in endpoints.values()],
        "tracks": {
            "general": {
                "gates": list(GATES),
                "python": TESTBED_PYTHON,
                "commands": {"tests": list(project["tests"])},
            }
        },
    }
    if models:
        document["models"] = models
    return "# Written by the SWE-bench bench for one task run (ADR 0042).\n" + tomlw.dumps(document)


# --------------------------------------------------------------------------------------------
# Task runs
# --------------------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")


def _container(kind: str, instance: str) -> str:
    return f"{CONTAINER_PREFIX}{kind}-{_slug(instance)[:60]}-{uuid.uuid4().hex[:6]}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def patch_paths(patch: str) -> list[str]:
    return sorted(
        {line[6:].strip() for line in patch.splitlines() if line.startswith(("--- a/", "+++ b/"))}
    )


def touches_tests(patch: str) -> bool:
    """Whether a patch changes test files (tests/ directories or test_*.py modules)."""
    for path in patch_paths(patch):
        parts = PurePosixPath(path).parts
        name = parts[-1] if parts else ""
        if "tests" in parts or "test" in parts or name.startswith("test_"):
            return True
    return False


def base_row(suite: str, row: Mapping[str, Any], contestant: str, seed: int) -> dict[str, Any]:
    return {
        "suite": suite,
        "task": row["instance_id"],
        "track": "general",
        "tags": [str(row.get("repo") or ""), str(row.get("difficulty") or "")],
        "contestant": contestant,
        "seed": seed,
        "started": _now(),
    }


@dataclass
class RouterSettings:
    workers: list[str]
    pinned: str | None = None  # `ssw run --worker`; None routes among `workers`
    max_steps: int = 40
    budget_seconds: float | None = None
    budget_tokens: int | None = None
    review: bool = True
    keep_logs: Path | None = None


def run_router(
    docker: DockerRun,
    row: Mapping[str, Any],
    *,
    contestant: str,
    seed: int,
    suite: str,
    deps_volume: str,
    source: Mapping[str, bytes],
    config: Any,
    settings: RouterSettings,
) -> tuple[dict[str, Any], str]:
    """One task run of Stepswitch inside the instance's container: (row, patch)."""
    result_row = base_row(suite, row, contestant, seed)
    instance = str(row["instance_id"])
    name = _container("run", instance)
    project = PROJECTS.get(str(row.get("repo")), DEFAULT_PROJECT)
    files: dict[str, bytes | str] = dict(source)
    files[f"{TASK_DIR}/problem.md"] = str(row["problem_statement"])
    files[f"{TESTBED}/.stepswitch/local.toml"] = container_config(
        config, settings.workers, str(row.get("repo"))
    )
    files.update(project.get("files") or {})
    flags = ["--json", "--seed", str(seed), "--max-steps", str(settings.max_steps)]
    flags += ["--budget-seconds", f"{settings.budget_seconds:g}"] if settings.budget_seconds else []
    flags += ["--budget-tokens", str(settings.budget_tokens)] if settings.budget_tokens else []
    flags += [] if settings.review else ["--no-review"]
    flags += ["--worker", settings.pinned] if settings.pinned else []
    command = (
        f'exec {DEPS_MOUNT}/venv/bin/python -m stepswitch run "$@" -- '
        f'"$(cat {TASK_DIR}/problem.md)"'
    )
    limit = (settings.budget_seconds or 3600.0) * 2 + 900
    started = time.monotonic()
    patch = ""
    result: dict[str, Any] = {}
    try:
        _check(
            docker(
                ["run", "-d", "--name", name, "--add-host", f"{DOCKER_HOST}:host-gateway"]
                + ["-v", f"{deps_volume}:{DEPS_MOUNT}:ro", str(row["image"]), "sleep", "infinity"]
            ),
            f"starting {row['image']}",
        )
        _check(docker(["cp", "-", f"{name}:/"], stdin=tar_bytes(files)), "copying files in")
        _check(
            docker(["exec", name, "sh", "-c", f"echo .stepswitch/ >> {TESTBED}/.git/info/exclude"]),
            "excluding the state directory",
        )
        done = docker(
            ["exec", "-w", TESTBED, "-e", "PYTHONIOENCODING=utf-8", name, "sh", "-c", command]
            + ["sh", *flags],
            timeout=limit,
        )
        if done.timed_out:
            raise SwebenchError(f"the run did not end within {limit:.0f} s")
        try:
            result = json.loads(done.text())
        except ValueError as exc:
            raise SwebenchError(
                f"ssw run printed no result (exit {done.code}): {done.error()}"
            ) from exc
        branch = result.get("branch")
        if branch:
            diff = docker(
                ["exec", "-w", TESTBED, name, "git", "diff", "--no-color", "--no-ext-diff"]
                + ["--binary", "HEAD", str(branch), "--", ".", ":(exclude).stepswitch"]
            )
            patch = _check(diff, "reading the task's diff").text()
        if settings.keep_logs is not None and result.get("task_id"):
            events = docker(
                [
                    "exec",
                    name,
                    "cat",
                    f"{TESTBED}/.stepswitch/runs/{result['task_id']}/events.jsonl",
                ]
            )
            if events.ok:
                target = settings.keep_logs / _slug(contestant) / instance / f"s{seed}"
                target.mkdir(parents=True, exist_ok=True)
                (target / "events.jsonl").write_bytes(events.stdout)
                (target / "stderr.txt").write_bytes(done.stderr)
    except SwebenchError as exc:
        result_row["error"] = str(exc)[:500]
    finally:
        docker(["rm", "-f", name])
    usage = result.get("usage_by_worker") or {}
    tokens = result.get("tokens") or {}
    result_row.update(
        {
            "status": result.get("status", "error"),
            "stop_reason": result.get("stop_reason", "error"),
            "steps": int(result.get("steps") or 0),
            "switches": int(result.get("switches") or 0),
            "escalations": int(result.get("escalations") or 0),
            "failovers": int(result.get("failovers") or 0),
            "handoffs": _count(result.get("handoffs")),
            "compactions": int(result.get("compactions") or 0),
            "context": dict(result.get("context") or {}),
            "endpoints": list(result.get("endpoints") or []),
            "changed_files": patch_paths(patch),
            "tokens": {
                "input": int(tokens.get("input_tokens") or 0),
                "output": int(tokens.get("output_tokens") or 0),
                "cached_input": int(tokens.get("cached_input_tokens") or 0),
                "reasoning": int(tokens.get("reasoning_tokens") or 0),
            },
            "models": {
                worker: {
                    **{k: int(c.get(k) or 0) for k in ("calls", "input", "cached_input", "output")},
                    "hosted": not c.get("local"),
                }
                for worker, c in sorted(usage.items())
            },
            "wall_seconds": round(time.monotonic() - started, 3),
            "agent_seconds": float(result.get("wall_seconds") or 0.0),
            "gpu_seconds": round(float((result.get("budget") or {}).get("gpu_seconds", 0.0)), 3),
            "task_id": result.get("task_id"),
        }
    )
    return result_row, patch


def _count(value: Any) -> int:
    """A count `ssw run --json` gives as a number (the handoffs) or, from older runs, a list."""
    if isinstance(value, list | tuple):
        return len(value)
    return int(value or 0)


@dataclass
class MiniSettings:
    worker: str
    keep_logs: Path | None = None


def mini_model(config: Any, worker: str) -> tuple[str, str]:
    """(litellm model name, API base) for mini-SWE-agent on `worker`, through the server's
    chat-completions route, which reports cached prompt tokens."""
    endpoint = _endpoint(config, worker)
    model = worker.partition("/")[2]
    base = container_url(endpoint.url).rstrip("/")
    return f"openai/{model}", base if base.endswith("/v1") else base + "/v1"


def run_mini(
    docker: DockerRun,
    row: Mapping[str, Any],
    *,
    contestant: str,
    seed: int,
    suite: str,
    harness_volume: str,
    dataset_line: str,
    config: Any,
    settings: MiniSettings,
) -> tuple[dict[str, Any], str]:
    """One task run of mini-SWE-agent with its own SWE-bench runner: (row, patch)."""
    result_row = base_row(suite, row, contestant, seed)
    instance = str(row["instance_id"])
    model, api_base = mini_model(config, settings.worker)
    name = _container("mini", instance)
    specs = [
        "swebench.yaml",
        f"model.model_kwargs.api_base={api_base}",
        "model.model_kwargs.api_key=none",
        "model.cost_tracking=ignore_errors",
    ]
    configs = " ".join(f"-c '{spec}'" for spec in specs)
    script = (
        "set -eu; mkdir -p /work/dataset /work/out; cd /work; cat > /work/dataset/test.jsonl; "
        f"{HARNESS_MOUNT}/mini/bin/mini-extra swebench --subset /work/dataset --split test "
        f"--filter '^{re.escape(instance)}$' -o /work/out -w 1 -m '{model}' {configs} 1>&2; "
        "tar -C /work/out -cf - ."
    )
    env = {
        "PATH": f"{HARNESS_MOUNT}/bin:/usr/local/bin:/usr/bin:/bin",
        "HF_HOME": "/tmp/hf",
        "HF_DATASETS_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "MSWEA_COST_TRACKING": "ignore_errors",
        "MSWEA_CONFIGURED": "true",
    }
    args = ["run", "--rm", "-i", "--name", name, "--add-host", f"{DOCKER_HOST}:host-gateway"]
    args += ["-v", f"{SOCKET}:{SOCKET}", "-v", f"{harness_volume}:{HARNESS_MOUNT}:ro"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    args += [BUILDER_IMAGE, "sh", "-c", script]
    started = time.monotonic()
    patch = ""
    trajectory: dict[str, Any] = {}
    try:
        done = docker(args, stdin=(dataset_line + "\n").encode(), timeout=4 * 3600)
        if done.timed_out:
            raise SwebenchError("mini-SWE-agent did not end within 4 hours")
        if not done.ok:
            raise SwebenchError(f"mini-SWE-agent failed (exit {done.code}): {done.error()}")
        out = untar(done.stdout)
        preds = json.loads(out.get("preds.json", b"{}") or b"{}")
        patch = str((preds.get(instance) or {}).get("model_patch") or "")
        raw = out.get(f"{instance}/{instance}.traj.json")
        trajectory = json.loads(raw) if raw else {}
        if settings.keep_logs is not None:
            target = settings.keep_logs / _slug(contestant) / instance / f"s{seed}"
            target.mkdir(parents=True, exist_ok=True)
            if raw:
                (target / "trajectory.json").write_bytes(raw)
            (target / "stderr.txt").write_bytes(done.stderr[-200_000:])
    except (SwebenchError, ValueError, tarfile.TarError) as exc:
        result_row["error"] = str(exc)[:500]
    finally:
        docker(["rm", "-f", name])
    usage = trajectory_usage(trajectory)
    info = trajectory.get("info") or {}
    exit_status = str(info.get("exit_status") or ("error" if "error" in result_row else ""))
    result_row.update(
        {
            "status": "done"
            if exit_status == "Submitted"
            else ("error" if "error" in result_row else "stopped"),
            "stop_reason": exit_status or "unknown",
            "steps": usage["calls"],
            "switches": 0,
            "escalations": 0,
            "failovers": 0,
            "handoffs": 0,
            "compactions": 0,
            "context": {},
            "endpoints": [settings.worker.partition("/")[0]],
            "changed_files": patch_paths(patch),
            "tokens": {
                "input": usage["input"],
                "output": usage["output"],
                "cached_input": usage["cached_input"],
                "reasoning": usage["reasoning"],
            },
            "models": {
                settings.worker: {
                    "calls": usage["calls"],
                    "input": usage["input"],
                    "cached_input": usage["cached_input"],
                    "output": usage["output"],
                    "hosted": False,
                }
            },
            "wall_seconds": round(time.monotonic() - started, 3),
            "gpu_seconds": 0.0,
            "task_id": None,
            "harness": {"name": "mini-swe-agent", "version": MINI_SWE_AGENT_VERSION},
        }
    )
    return result_row, patch


def gold_run(
    row: Mapping[str, Any], *, contestant: str, seed: int, suite: str
) -> tuple[dict[str, Any], str]:
    """The reference patch as a prediction, with no agent (the gold check, ADR 0042)."""
    patch = str(row.get("patch") or "")
    result_row = base_row(suite, row, contestant, seed)
    result_row.update(
        {
            "status": "done",
            "stop_reason": "gold",
            "steps": 0,
            "switches": 0,
            "escalations": 0,
            "failovers": 0,
            "handoffs": 0,
            "compactions": 0,
            "context": {},
            "endpoints": [],
            "changed_files": patch_paths(patch),
            "tokens": {"input": 0, "output": 0, "cached_input": 0, "reasoning": 0},
            "models": {},
            "wall_seconds": 0.0,
            "gpu_seconds": 0.0,
            "task_id": None,
        }
    )
    return result_row, patch


def trajectory_usage(trajectory: Mapping[str, Any]) -> dict[str, int]:
    """Calls and token counts of a mini-SWE-agent trajectory, from the usage each response
    reported (prompt tokens include the cached ones)."""
    totals = {"calls": 0, "input": 0, "cached_input": 0, "output": 0, "reasoning": 0}
    for message in trajectory.get("messages") or []:
        extra = message.get("extra") if isinstance(message, Mapping) else None
        response = extra.get("response") if isinstance(extra, Mapping) else None
        usage = response.get("usage") if isinstance(response, Mapping) else None
        if not isinstance(usage, Mapping):
            continue
        totals["calls"] += 1
        totals["input"] += int(usage.get("prompt_tokens") or 0)
        totals["output"] += int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        if isinstance(details, Mapping):
            totals["cached_input"] += int(details.get("cached_tokens") or 0)
        reasoning = usage.get("completion_tokens_details") or {}
        if isinstance(reasoning, Mapping):
            totals["reasoning"] += int(reasoning.get("reasoning_tokens") or 0)
    return totals


# --------------------------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------------------------


def grade(
    docker: DockerRun,
    row: Mapping[str, Any],
    patch: str,
    *,
    harness_volume: str,
    run_id: str,
    model: str,
) -> dict[str, Any]:
    """The official harness's verdict on one prediction: resolved, and the report."""
    instance = str(row["instance_id"])
    if not patch.strip():
        return {"resolved": False, "empty_patch": True}
    prediction = {"instance_id": instance, "model_name_or_path": model, "model_patch": patch}
    files = {
        "/work/dataset.json": json.dumps([row], ensure_ascii=False),
        "/work/preds.jsonl": json.dumps(prediction, ensure_ascii=False) + "\n",
    }
    script = (
        "set -eu; cd /; tar -xf -; cd /work; "
        f"{HARNESS_MOUNT}/swebench/bin/swebench eval /work/dataset.json -p /work/preds.jsonl "
        f"--run-id {run_id} -j 1 -t {GRADE_TIMEOUT_S} -i {instance} 1>&2 || true; "
        f"tar -cf - {EVAL_LOGS}/{run_id}"
    )
    name = _container("grade", instance)
    done = docker(
        ["run", "--rm", "-i", "--name", name, "-v", f"{SOCKET}:{SOCKET}"]
        + ["-v", f"{harness_volume}:{HARNESS_MOUNT}:ro"]
        # The harness stops its containers with the Docker command line.
        + ["-e", f"PATH={HARNESS_MOUNT}/bin:/usr/local/bin:/usr/bin:/bin"]
        + [BUILDER_IMAGE, "sh", "-c", script],
        stdin=tar_bytes(files),
        timeout=GRADE_TIMEOUT_S + 900,
    )
    docker(["rm", "-f", name])
    docker(["rm", "-f", f"sweb.eval.{instance}.{run_id}"])  # the harness's own, if it stayed
    if not done.ok:
        return {
            "resolved": False,
            "error": f"the harness failed (exit {done.code}): {done.error()}",
        }
    try:
        out = untar(done.stdout)
    except tarfile.TarError as exc:
        return {"resolved": False, "error": f"no harness output: {exc}"}
    report_name = f"{EVAL_LOGS}/{run_id}/{model.replace('/', '__')}/{instance}/report.json"
    raw = out.get(report_name)
    if raw is None:
        return {"resolved": False, "error": "the harness wrote no report", "logs": sorted(out)[:20]}
    report = json.loads(raw).get(instance) or {}
    return {
        "resolved": bool(report.get("resolved")),
        "patch_applied": bool(report.get("patch_successfully_applied")),
        "infra_failure": bool(report.get("infra_failure")),
        "tests_status": _counts(report.get("tests_status") or {}),
    }


def _counts(status: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """Pass and fail counts per test group of a harness report (the names stay out)."""
    found: dict[str, dict[str, int]] = {}
    for group, outcome in status.items():
        if isinstance(outcome, Mapping):
            found[str(group)] = {
                key: len(value) for key, value in outcome.items() if isinstance(value, list)
            }
    return found


# --------------------------------------------------------------------------------------------
# The suite
# --------------------------------------------------------------------------------------------


@dataclass
class SwebenchOptions:
    seeds: int = 1
    max_steps: int | None = None
    budget_seconds: float | None = None
    budget_tokens: int | None = None
    review: bool = True
    router_pool: list[str] | None = None
    keep: bool = False
    pull: bool = True
    preflight: dict[str, Any] = field(default_factory=lambda: {"ok": None, "skipped": True})


def check_contestants(contestants: Sequence[Any], options: SwebenchOptions, config: Any) -> None:
    """SWE-bench suites take `router`, worker contestants and `mini-swe-agent:<worker>`, all on
    endpoints without credentials, and `gold`, the reference patches."""
    for contestant in contestants:
        mini = mini_worker(contestant.name)
        if contestant.name == GOLD:
            continue
        if mini is not None:
            _endpoint(config, mini)
        elif contestant.name == "router":
            if not options.router_pool:
                raise SwebenchError("SWE-bench suites need --router-pool for the router")
            for worker in options.router_pool:
                _endpoint(config, worker)
        elif contestant.worker is not None and contestant.fake is None:
            _endpoint(config, contestant.worker)
        else:
            raise SwebenchError(
                f"{contestant.name} cannot run a SWE-bench suite (router, worker ids,"
                f" {MINI_PREFIX}<worker> and {GOLD} can)"
            )


def run_suite(
    suite: Any,
    contestants: Sequence[Any],
    *,
    root: Path,
    out_dir: Path,
    options: SwebenchOptions,
    config: Any,
    command: str | None,
    echo: Callable[[str], None],
    docker: DockerRun | None = None,
    rows: Rows | None = None,
    deps_requirements: str | None = None,
) -> Path:
    """Run every contestant on every instance with every seed, grade each run; return the
    results directory."""
    check_contestants(contestants, options, config)
    docker = docker or Docker()
    dataset = suite.dataset or {}
    rows = rows or load_rows(
        root, str(dataset["name"]), str(dataset.get("split") or "test"), echo=echo
    )
    ids = [task.id for task in suite.tasks]
    selected = rows.select(ids)
    echo(NOTICE)
    images = sorted({str(row["image"]) for row in selected})
    if options.pull:
        for image in images:
            if not docker(["image", "inspect", image]).ok:
                echo(f"Pulling {image}")
                _check(docker(["pull", image], timeout=3600), f"pulling {image}")
    digests = {image: image_digest(docker, image) for image in images}
    source = source_files(root)
    needs_router = any(mini_worker(c.name) is None and c.name != GOLD for c in contestants)
    deps = (
        ensure_deps_volume(docker, deps_requirements or requirements(root), echo)
        if needs_router
        else None
    )
    harness = ensure_harness_volume(docker, echo)
    manifest: dict[str, Any] = {
        "schema": 1,
        "suite": suite.name,
        "suite_file": suite.path.name,
        "kind": KIND,
        "command": command,
        "started": _now(),
        "finished": None,
        "version": __version__,
        "commit": _commit(root),
        "source_sha256": digest(source),
        "seeds": list(range(options.seeds)),
        "contestants": [c.name for c in contestants],
        "tasks": ids,
        "review": options.review,
        "router_pool": list(options.router_pool or []),
        "max_steps": options.max_steps,
        "budget_seconds": options.budget_seconds,
        "dataset": {
            "name": rows.dataset,
            "split": rows.split,
            "revision": rows.revision,
            "fetched": rows.fetched,
            "rows_sha256": rows_digest(selected),
        },
        "harness": {
            "swebench": SWEBENCH_VERSION,
            "mini_swe_agent": MINI_SWE_AGENT_VERSION,
            "litellm": LITELLM_VERSION,
            "volume": harness,
        },
        "runtime": {"volume": deps, "python": RUNTIME_PYTHON, "gates": list(GATES)},
        "images": images,
        # A tag can move; the registry digest names the image that ran.
        "image_digests": digests,
        "preflight": options.preflight,
        "scoring": SCORING,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    results = out_dir / RESULTS_FILE
    results.write_text("", encoding="utf-8")
    logs = (
        paths.state_dir(root) / "bench" / "swebench-logs" / out_dir.name if options.keep else None
    )
    lock = threading.Lock()
    tally = {"passed": 0, "done": 0}

    def finish(result_row: dict[str, Any], verdict: dict[str, Any]) -> None:
        result_row["passed"] = bool(verdict.get("resolved"))
        result_row["swebench"] = verdict
        result_row["acceptance"] = []
        result_row["apply_error"] = None if verdict.get("patch_applied", True) else "patch failed"
        result_row["infra_error"] = "error" in result_row or bool(
            verdict.get("error") or verdict.get("infra_failure")
        )
        result_row["hosted_tokens"] = sum(
            m["input"] + m["output"]
            for m in result_row.get("models", {}).values()
            if m.get("hosted")
        )
        result_row["finished"] = _now()
        with lock:
            with results.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(result_row, sort_keys=True) + "\n")
            tally["done"] += 1
            tally["passed"] += int(result_row["passed"])
        verdict_text = "resolved" if result_row["passed"] else "not resolved"
        echo(
            f"    {result_row['contestant']} {result_row['task']} s{result_row['seed']}:"
            f" {verdict_text} ({result_row['status']}, {result_row['wall_seconds']:.0f} s)"
        )

    total = len(contestants) * len(selected) * options.seeds
    count = 0
    pending: list[Future[None]] = []
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="swebench-grade") as grader:
        for contestant in contestants:
            mini = mini_worker(contestant.name)
            model = _slug(contestant.name)
            for row in selected:
                line = json.dumps(row, ensure_ascii=False)
                for seed in range(options.seeds):
                    count += 1
                    echo(f"[{count}/{total}] {contestant.name} {row['instance_id']} seed {seed}")
                    if contestant.name == GOLD:
                        result_row, patch = gold_run(
                            row, contestant=contestant.name, seed=seed, suite=suite.name
                        )
                    elif mini is not None:
                        result_row, patch = run_mini(
                            docker,
                            row,
                            contestant=contestant.name,
                            seed=seed,
                            suite=suite.name,
                            harness_volume=harness,
                            dataset_line=line,
                            config=config,
                            settings=MiniSettings(worker=mini, keep_logs=logs),
                        )
                    else:
                        assert deps is not None
                        workers = (
                            list(options.router_pool or [])
                            if contestant.name == "router"
                            else [contestant.worker]
                        )
                        result_row, patch = run_router(
                            docker,
                            row,
                            contestant=contestant.name,
                            seed=seed,
                            suite=suite.name,
                            deps_volume=deps,
                            source=source,
                            config=config,
                            settings=RouterSettings(
                                workers=workers,
                                pinned=None if contestant.name == "router" else contestant.worker,
                                max_steps=options.max_steps or 40,
                                budget_seconds=options.budget_seconds,
                                budget_tokens=options.budget_tokens,
                                review=options.review,
                                keep_logs=logs,
                            ),
                        )
                    run_id = _slug(f"{out_dir.name}-{model}-s{seed}")[:80]
                    if contestant.name != GOLD:  # the reference patches are the dataset's
                        save_prediction(out_dir, f"{model}-s{seed}", row, model, patch)

                    def graded(
                        result_row: dict[str, Any] = result_row,
                        patch: str = patch,
                        row: Mapping[str, Any] = row,
                        run_id: str = run_id,
                        model: str = model,
                    ) -> None:
                        verdict = grade(
                            docker, row, patch, harness_volume=harness, run_id=run_id, model=model
                        )
                        result_row["patch_lines"] = len(patch.splitlines())
                        result_row["tests_modified"] = touches_tests(patch)
                        finish(result_row, verdict)

                    pending.append(grader.submit(graded))
        for future in pending:
            future.result()
    manifest["finished"] = _now()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    write_report(out_dir)
    echo(f"Resolved {tally['passed']} of {tally['done']} task runs")
    return out_dir


def image_digest(docker: DockerRun, image: str) -> str | None:
    """The registry digest (`sha256:...`) of a pulled image, or None when it has none."""
    done = docker(["image", "inspect", "--format", "{{json .RepoDigests}}", image])
    try:
        found = json.loads(done.text()) if done.ok else []
    except ValueError:
        return None
    for entry in found if isinstance(found, list) else []:
        name, _, digest = str(entry).partition("@")
        if digest.startswith("sha256:") and name == image.rsplit(":", 1)[0]:
            return digest
    return None


def save_prediction(
    out_dir: Path, name: str, row: Mapping[str, Any], model: str, patch: str
) -> Path:
    """Add a run's patch to `predictions/<name>.jsonl` in the harness's own format, so anyone
    can grade the results again without running the agents (ADR 0042)."""
    path = out_dir / PREDICTIONS_DIR / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"instance_id": str(row["instance_id"]), "model_name_or_path": model}
    line["model_patch"] = patch
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


def _commit(root: Path) -> str | None:
    with contextlib.suppress(OSError):
        done = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return done.stdout.strip() or None if done.returncode == 0 else None
    return None
