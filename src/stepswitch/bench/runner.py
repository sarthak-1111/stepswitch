"""The bench runner (SPEC §16): contestants x tasks x seeds over one suite.

A contestant is `router` (step-level routing over every configured worker, as `ssw run` does),
a worker id or model name (every step pinned to it, as `ssw run --worker`), or a deterministic
fake (`fake:oracle`, `fake:noop`) for CI.

Each run copies the task's repository into a fresh git repository under the project's state
directory and calls `run_task` with the project's configuration, so endpoints and managed model
servers are shared with `ssw run`. Whether the run passed is decided afterwards, independently
of what the agent reported: the run's diff is applied to another fresh copy, the suite's
protected paths (`tests/` by default) are restored from the original, and the acceptance
commands run there, the way the gates run them (platform shell, project interpreter first on
PATH). The run passes when every acceptance command exits with 0 (`SCORING`). Restoring the
tests makes changing them pointless, so a run that changed a protected path is not failed for
it; it is marked `tests_modified` and counted in its own leaderboard column.

Rows are appended to `results.jsonl` as runs finish, so an interrupted bench keeps its finished
runs; `run.json` records the suite, command, hardware, version and dates, and the leaderboard is
written at the end (`stepswitch.bench.report`).
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stepswitch import __version__, paths
from stepswitch.bench import fakes, workspace
from stepswitch.bench.report import MANIFEST_FILE, RESULTS_FILE, write_report
from stepswitch.bench.suites import BenchTask, Suite
from stepswitch.core.task import Budgets, TaskResult, TaskSpec
from stepswitch.verify import commands

ROUTER = "router"
SCORING = (
    "passes when every acceptance command exits with 0 on a fresh copy with the run's diff"
    " applied and the suite's protected paths restored"
)
ACCEPTANCE_TIMEOUT_S = 600.0
ACCEPTANCE_ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
_MACHINES = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}


@dataclass(frozen=True)
class Contestant:
    name: str
    worker: str | None = None  # every step pinned to this worker; None routes freely
    fake: str | None = None  # a fake contestant kind (`fakes.KINDS`)


def parse_contestants(text: str | None) -> list[Contestant]:
    """`router,managed/qwen3-4b-instruct-2507,fake:oracle` -> contestants, in order."""
    names = [part.strip() for part in (text or ROUTER).split(",") if part.strip()]
    contestants = []
    for name in dict.fromkeys(names):
        if name == ROUTER:
            contestants.append(Contestant(name))
        elif name.startswith(fakes.PREFIX):
            kind = name.removeprefix(fakes.PREFIX)
            if kind not in fakes.KINDS:
                known = ", ".join(fakes.PREFIX + k for k in fakes.KINDS)
                raise ValueError(f"unknown fake contestant {name} (known: {known})")
            contestants.append(Contestant(name, worker=fakes.worker_id(kind), fake=kind))
        else:
            contestants.append(Contestant(name, worker=name))
    if not contestants:
        raise ValueError("no contestants given")
    return contestants


@dataclass
class BenchOptions:
    seeds: int = 1
    review: bool = True
    max_steps: int | None = None
    budgets: Budgets = field(default_factory=Budgets)
    acceptance_timeout: float = ACCEPTANCE_TIMEOUT_S
    keep: bool = False
    prices: dict[str, float] | None = None
    # Workers the `router` contestant may route among; None means every configured worker.
    router_pool: list[str] | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def hardware_summary(config: Any = None) -> dict[str, Any]:
    """Counts and sizes only: no product or vendor names (ADR 0007)."""
    detected = ((getattr(config, "host", None) or {}).get("detected") or {}) if config else {}
    cpu = detected.get("cpu") or {}
    gpus = [gpu for gpu in detected.get("gpus") or [] if isinstance(gpu, dict)]
    machine = platform.machine().lower()
    summary: dict[str, Any] = {
        "os": platform.system(),
        "arch": _MACHINES.get(machine, machine),
        "cpu_cores": cpu.get("cores_logical"),
        "ram_gb": detected.get("ram_gb"),
        "gpus": len(gpus),
    }
    memory = [gpu.get("vram_gb") for gpu in gpus if gpu.get("vram_gb")]
    if memory:
        summary["gpu_memory_gb"] = memory
    return {key: value for key, value in summary.items() if value is not None}


def _commit(root: Path) -> str | None:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            env=workspace.git_env(),
        )
    except OSError:
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def _router_pool(
    contestants: Sequence[Contestant],
    options: BenchOptions,
    root: Path,
    config: Any,
    egress: Any,
) -> list[str] | None:
    """The worker ids the `router` contestant routes among, recorded in the manifest: the
    given pool, or every configured worker when none is given."""
    if all(c.name != ROUTER for c in contestants) or config is None:
        return list(options.router_pool) if options.router_pool else None
    from stepswitch.workers.catalog import Catalog

    catalog = Catalog.from_config(config, root, egress=egress)
    if options.router_pool:
        catalog = catalog.subset(options.router_pool)
    return sorted(catalog.workers)


def _slug(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in name).strip("-")


def acceptance_commands(task: BenchTask) -> list[str]:
    """The task's acceptance commands as the gates take them: a leading `python` (the suites'
    name for the project's interpreter) becomes the `{python}` placeholder, so the command
    never depends on which `python` comes first on PATH."""
    commands_ = []
    for line in task.acceptance:
        head, _, rest = line.strip().partition(" ")
        commands_.append(f"{{python}} {rest}".rstrip() if head == "python" else line)
    return commands_


@dataclass
class Acceptance:
    passed: bool
    commands: list[dict[str, Any]]
    tests_modified: bool
    apply_error: str | None = None


def check_acceptance(
    task: BenchTask,
    diff: str,
    where: Path,
    *,
    protected: Sequence[str],
    timeout: float,
    config: Any = None,
) -> Acceptance:
    """Apply `diff` to a fresh copy of the task, restore protected paths, run the acceptance
    commands. Passing means every command exited with 0."""
    repo = workspace.copy_repo(task.repo, where)
    workspace.init_repo(repo)
    changed = _changed_paths(diff)
    tests_modified = any(_protected(path, protected) for path in changed)
    try:
        workspace.apply_diff(repo, diff)
    except subprocess.CalledProcessError as exc:
        error = exc.stderr.decode("utf-8", errors="replace").strip()[:500]
        return Acceptance(False, [], tests_modified, apply_error=error or "git apply failed")
    for prefix in protected:
        _restore(task.repo, repo, prefix)
    python = commands.project_python(repo, config, task.track)
    env = commands.project_env(python, extra=ACCEPTANCE_ENV)
    values = {"python": commands.shell_quote(python), "repo": commands.shell_quote(str(repo))}
    results = []
    passed = True
    for line in acceptance_commands(task):
        command = commands.expand([line], values)[0]
        if " -m pytest" in f" {command}" and "no:cacheprovider" not in command:
            command += " -p no:cacheprovider"
        result = commands.run(command, cwd=repo, timeout=timeout, env=env, shell=True)
        results.append(
            {
                "command": line,
                "exit_code": result.returncode,
                "seconds": round(result.seconds, 3),
                "timed_out": result.timed_out,
            }
        )
        if not result.ok:
            passed = False
            break
    return Acceptance(passed, results, tests_modified)


def _changed_paths(diff: str) -> list[str]:
    found = []
    for line in diff.splitlines():
        if line.startswith(("--- a/", "+++ b/")):
            found.append(line[6:].strip())
    return sorted(set(found))


def _protected(path: str, protected: Sequence[str]) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in protected)


def _restore(original: Path, repo: Path, prefix: str) -> None:
    target = repo / prefix.rstrip("/")
    source = original / prefix.rstrip("/")
    if target.exists():
        if target.is_dir():
            workspace.remove_tree(target)
        else:
            target.unlink()
    if source.is_dir():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(*workspace.GENERATED))
    elif source.is_file():
        shutil.copy2(source, target)


RunTask = Callable[..., TaskResult]


def _run_task(*args: Any, **kwargs: Any) -> TaskResult:
    from stepswitch.core.orchestrator import run_task

    return run_task(*args, **kwargs)


def run_one(
    task: BenchTask,
    contestant: Contestant,
    seed: int,
    *,
    suite: Suite,
    root: Path,
    work: Path,
    options: BenchOptions,
    config: Any = None,
    egress: Any = None,
    run_task: RunTask = _run_task,
) -> dict[str, Any]:
    """Run one task for one contestant and seed; return its results row."""
    place = work / _slug(contestant.name) / task.id / f"s{seed}"
    repo = workspace.copy_repo(task.repo, place / "repo")
    workspace.init_repo(repo)
    budgets = Budgets(**{**options.budgets.model_dump(exclude_none=True), **task.budgets})
    spec = TaskSpec(
        instruction=task.instruction,
        repo=repo,
        track=task.track,  # type: ignore[arg-type]
        acceptance=acceptance_commands(task),
        budgets=budgets,
        worker=contestant.worker,
        workers=list(options.router_pool or []) if contestant.name == ROUTER else [],
        seed=seed,
        max_steps=task.max_steps or options.max_steps or 40,
        review=options.review,
    )
    catalog = fakes.catalog_for(contestant.fake, task) if contestant.fake else None
    row: dict[str, Any] = {
        "suite": suite.name,
        "task": task.id,
        "track": task.track,
        "tags": list(task.tags),
        "contestant": contestant.name,
        "seed": seed,
        "started": _now(),
    }
    result: TaskResult | None = None
    try:
        result = run_task(spec, root=root, config=config, catalog=catalog, egress=egress)
    except Exception as exc:  # noqa: BLE001 - a crashed run is a failed row, not a crashed bench
        row["error"] = f"{type(exc).__name__}: {exc}"[:500]
    check = check_acceptance(
        task,
        result.diff if result is not None else "",
        place / "check",
        protected=suite.protected,
        timeout=options.acceptance_timeout,
        config=config,
    )
    tokens = (result.tokens if result is not None else {}) or {}
    budget = (result.budget if result is not None else {}) or {}
    row.update(
        {
            "status": result.status if result is not None else "error",
            "stop_reason": result.stop_reason if result is not None else "error",
            "passed": check.passed,
            "acceptance": check.commands,
            "tests_modified": check.tests_modified,
            "apply_error": check.apply_error,
            "steps": result.steps if result is not None else 0,
            "switches": result.switches if result is not None else 0,
            "escalations": result.escalations if result is not None else 0,
            "endpoints": result.endpoints if result is not None else [],
            "changed_files": result.changed_files if result is not None else [],
            "tokens": {
                "input": tokens.get("input_tokens", 0),
                "output": tokens.get("output_tokens", 0),
                "cached_input": tokens.get("cached_input_tokens", 0),
                "reasoning": tokens.get("reasoning_tokens", 0),
            },
            "wall_seconds": result.wall_seconds if result is not None else 0.0,
            "gpu_seconds": round(float(budget.get("gpu_seconds", 0.0)), 3),
            "task_id": result.task_id if result is not None else None,
            "finished": _now(),
        }
    )
    if not options.keep:
        workspace.remove_tree(place)
    return row


def run_bench(
    suite: Suite,
    contestants: Sequence[Contestant],
    *,
    root: Path,
    out_dir: Path,
    options: BenchOptions | None = None,
    config: Any = None,
    egress: Any = None,
    command: str | None = None,
    echo: Callable[[str], None] = lambda _msg: None,
    run_task: RunTask = _run_task,
) -> Path:
    """Run every contestant on every task with every seed; return the results directory."""
    options = options or BenchOptions()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = paths.state_dir(root) / "bench" / out_dir.name
    manifest: dict[str, Any] = {
        "schema": 1,
        "suite": suite.name,
        "suite_file": suite.path.name,
        "command": command,
        "started": _now(),
        "finished": None,
        "hardware": hardware_summary(config),
        "version": __version__,
        "commit": _commit(root),
        "seeds": list(range(options.seeds)),
        "contestants": [c.name for c in contestants],
        "tasks": [t.id for t in suite.tasks],
        "review": options.review,
        "prices": options.prices,
        "router_pool": _router_pool(contestants, options, root, config, egress),
        "scoring": SCORING,
    }
    manifest_path = out_dir / MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    results = out_dir / RESULTS_FILE
    results.write_text("", encoding="utf-8")
    total = len(contestants) * len(suite.tasks) * options.seeds
    done = 0
    for contestant in contestants:
        for task in suite.tasks:
            for seed in range(options.seeds):
                done += 1
                echo(f"[{done}/{total}] {contestant.name} {task.id} seed {seed}")
                row = run_one(
                    task,
                    contestant,
                    seed,
                    suite=suite,
                    root=root,
                    work=work,
                    options=options,
                    config=config,
                    egress=egress,
                    run_task=run_task,
                )
                with results.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
                verdict = "passed" if row["passed"] else "failed"
                echo(f"    {verdict} ({row['status']}, {row['wall_seconds']:.0f} s)")
    if not options.keep:
        workspace.remove_tree(work)
    manifest["finished"] = _now()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    write_report(out_dir)
    return out_dir


def passed_from(row: Mapping[str, Any]) -> bool:
    """The `SCORING` verdict from a row's recorded acceptance results."""
    acceptance = row.get("acceptance") or []
    return (
        not row.get("apply_error")
        and bool(acceptance)
        and all(c.get("exit_code") == 0 and not c.get("timed_out") for c in acceptance)
    )


def rescore(run_dir: Path, *, commit: str | None = None) -> int:
    """Recompute every row's `passed` under `SCORING` from its recorded acceptance results
    (rows written under an earlier rule); note it in `run.json`. Returns the rows changed."""
    path = run_dir / RESULTS_FILE
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    changed = 0
    for row in rows:
        verdict = passed_from(row)
        changed += verdict != bool(row.get("passed"))
        row["passed"] = verdict
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    manifest_path = run_dir / MANIFEST_FILE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("scoring") != SCORING:
        manifest["rescored"] = {
            "from": manifest.get("scoring") or "passed only without changes to protected paths",
            "rows_changed": changed,
            "at": _now(),
            "commit": commit,
        }
        manifest["scoring"] = SCORING
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return changed
