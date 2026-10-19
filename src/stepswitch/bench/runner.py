"""The bench runner (SPEC §16): contestants x tasks x seeds over one suite.

A contestant is `router` (step-level routing over every configured worker, as `ssw run` does),
a worker id or model name (every step pinned to it, as `ssw run --worker`), a deterministic
fake (`fake:oracle`, `fake:noop`) for CI, or `relay` (`--relay first,second`): the first worker
starts every task and hands it to the second with a HANDOFF brief at work step `relay_step`,
which measures briefs and what a handoff costs in success (SPEC §7.2, §9; ADR 0024), or
`best-of-n` (`--best-of N`): N candidates from the pool's model families on every task, the
best one kept (SPEC §8.3; ADR 0025). Its pool is `--router-pool`, as for `router`; running the
pool's workers as contestants too gives the comparison of claim C-012.

Two kinds compare Stepswitch with a frontier harness (ADR 0034). `harness:<name>` runs every
task in that external harness through `ssw proxy`, routed among the `--router-pool` workers
(`ssw run --harness`). `native:<harness>:<model>` runs the harness alone on its own login and
the named model, one session per task run, with the prompt a harness run's first attempt gets
and no routing, gates, retries or review (`stepswitch.bench.native`). Harness runs in the bench
are isolated from the user's and the enclosing project's customizations. Every row reports its
tokens per model (`models`), the tokens that went to hosted models (`hosted_tokens`) and whether
the run ended on an infrastructure error (`infra_error`).

`--baseline` runs the Phase 2 configuration, every token optimization off (`optimize` false in
`run.json`), for the comparison of SPEC §13 (claim C-011). Optimized runs also write
`handoffs.jsonl`: one record per HANDOFF brief, with the suite, task, seed and contestant.

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
from typing import Any, Literal

from stepswitch import __version__, paths
from stepswitch.bench import fakes, native, workspace
from stepswitch.bench.report import MANIFEST_FILE, RESULTS_FILE, write_report
from stepswitch.bench.suites import BenchTask, Suite
from stepswitch.core.task import Budgets, TaskResult, TaskSpec
from stepswitch.verify import commands

ROUTER = "router"
RELAY = "relay"
HARNESS_PREFIX = "harness:"
BEST_OF_N = "best-of-n"
POOLED = frozenset({ROUTER, BEST_OF_N})
HANDOFFS_FILE = "handoffs.jsonl"
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
    harness: str | None = None  # runs in this external harness (`harness:` and `native:`)
    native: str | None = None  # the model of a native run: the harness alone, on its login

    @property
    def pooled(self) -> bool:
        """Routes among the `--router-pool` workers."""
        return self.name in POOLED or (self.harness is not None and self.native is None)


def parse_contestants(text: str | None) -> list[Contestant]:
    """`router,managed/qwen3-4b-instruct-2507,fake:oracle` -> contestants, in order."""
    names = [part.strip() for part in (text or ROUTER).split(",") if part.strip()]
    contestants = []
    for name in dict.fromkeys(names):
        if name in {ROUTER, RELAY, BEST_OF_N}:
            contestants.append(Contestant(name))
        elif name.startswith(fakes.PREFIX):
            kind = name.removeprefix(fakes.PREFIX)
            if kind not in fakes.KINDS:
                known = ", ".join(fakes.PREFIX + k for k in fakes.KINDS)
                raise ValueError(f"unknown fake contestant {name} (known: {known})")
            contestants.append(Contestant(name, worker=fakes.worker_id(kind), fake=kind))
        elif name.startswith(HARNESS_PREFIX):
            from stepswitch.workers.harness import adapters

            harness = name.removeprefix(HARNESS_PREFIX)
            if harness not in adapters():
                known = ", ".join(HARNESS_PREFIX + n for n in sorted(adapters()))
                raise ValueError(f"unknown harness contestant {name} (known: {known})")
            contestants.append(Contestant(name, harness=harness))
        elif name.startswith(native.PREFIX):
            harness, model = native.parse(name)
            contestants.append(Contestant(name, harness=harness, native=model))
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
    # False runs the Phase 2 baseline (`--baseline`): every token optimization off.
    optimize: bool = True
    # The `relay` contestant's two workers and the work step of the handoff.
    relay: list[str] | None = None
    relay_step: int = 6
    # Candidates of the `best-of-n` contestant.
    best_of: int = 2
    # The learning router (ADR 0026): off by default, so a run's routing does not depend on
    # what earlier tasks taught the project's state database.
    learning: Literal["on", "frozen", "off"] = "off"


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
    """The worker ids the `router` and `best-of-n` contestants route among, recorded in the
    manifest: the given pool, or every configured worker when none is given."""
    if all(not c.pooled for c in contestants) or config is None:
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
    relay = list(options.relay or []) if contestant.name == RELAY else []
    pool = list(options.router_pool or []) if contestant.pooled else relay
    best_of = options.best_of if contestant.name == BEST_OF_N else 1
    if contestant.native is not None:
        return _run_native(
            task,
            contestant,
            seed,
            suite=suite,
            repo=repo,
            place=place,
            budgets=budgets,
            options=options,
            config=config,
        )
    spec = TaskSpec(
        instruction=task.instruction,
        repo=repo,
        track=task.track,  # type: ignore[arg-type]
        acceptance=acceptance_commands(task),
        budgets=budgets,
        worker=contestant.worker,
        workers=pool,
        seed=seed,
        max_steps=task.max_steps or options.max_steps or 40,
        review=options.review,
        optimize=options.optimize,
        relay=relay,
        relay_step=options.relay_step,
        best_of=best_of,
        learning=options.learning,
        harness=contestant.harness,
        isolated=contestant.harness is not None,
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
    briefs = task_handoffs(result)
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
            "failovers": result.failovers if result is not None else 0,
            "handoffs": len(briefs),
            "compactions": result.compactions if result is not None else 0,
            "context": dict(result.context) if result is not None else {},
            "best_of": result.best_of if result is not None else best_of,
            "candidates": [
                {k: c.get(k) for k in ("index", "family", "status", "changed_lines")}
                for c in (result.candidates if result is not None else [])
            ],
            "winner": result.winner if result is not None else None,
            "uncertain": result.uncertain if result is not None else False,
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
    usage = result.usage_by_worker if result is not None else {}
    row["models"] = {
        worker: {
            **{k: int(counts.get(k) or 0) for k in ("calls", "input", "cached_input", "output")},
            "hosted": not counts.get("local"),
        }
        for worker, counts in sorted(usage.items())
    }
    row["hosted_tokens"] = hosted_tokens(row["models"])
    row["infra_error"] = "error" in row
    if not options.keep:
        workspace.remove_tree(place)
    if briefs:
        row["_handoffs"] = [
            {
                "suite": suite.name,
                "task": task.id,
                "seed": seed,
                "contestant": contestant.name,
                **record,
            }
            for record in briefs
        ]
    return row


def hosted_tokens(models: Mapping[str, Mapping[str, Any]]) -> int:
    """Input plus output tokens of a row's hosted models."""
    return sum(
        int(counts.get("input") or 0) + int(counts.get("output") or 0)
        for counts in models.values()
        if counts.get("hosted")
    )


def _run_native(
    task: BenchTask,
    contestant: Contestant,
    seed: int,
    *,
    suite: Suite,
    repo: Path,
    place: Path,
    budgets: Budgets,
    options: BenchOptions,
    config: Any,
) -> dict[str, Any]:
    """One task run of a native contestant (`stepswitch.bench.native`): its results row."""
    assert contestant.harness is not None and contestant.native is not None
    row: dict[str, Any] = {
        "suite": suite.name,
        "task": task.id,
        "track": task.track,
        "tags": list(task.tags),
        "contestant": contestant.name,
        "seed": seed,
        "started": _now(),
    }
    outcome: native.NativeResult | None = None
    try:
        outcome = native.run_native(
            task,
            repo,
            harness=contestant.harness,
            model=contestant.native,
            acceptance=acceptance_commands(task),
            place=place,
            config=config,
            budgets=budgets,
            keep=options.keep,
        )
    except Exception as exc:  # noqa: BLE001 - a crashed run is a failed row, not a crashed bench
        row["error"] = f"{type(exc).__name__}: {exc}"[:500]
    check = check_acceptance(
        task,
        outcome.diff if outcome is not None else "",
        place / "check",
        protected=suite.protected,
        timeout=options.acceptance_timeout,
        config=config,
    )
    models = {
        name: {**counts, "hosted": True}
        for name, counts in sorted((outcome.models if outcome is not None else {}).items())
    }
    totals = {
        key: sum(int(counts.get(key) or 0) for counts in models.values())
        for key in ("input", "output", "cached_input", "cache_write")
    }
    row.update(
        {
            "status": outcome.status if outcome is not None else "error",
            "stop_reason": outcome.stop_reason if outcome is not None else "error",
            "passed": check.passed,
            "acceptance": check.commands,
            "tests_modified": check.tests_modified,
            "apply_error": check.apply_error,
            "steps": outcome.turns if outcome is not None else 0,
            "switches": 0,
            "escalations": 0,
            "failovers": 0,
            "handoffs": 0,
            "compactions": 0,
            "context": {},
            "best_of": 1,
            "candidates": [],
            "winner": None,
            "uncertain": False,
            "endpoints": [],
            "changed_files": outcome.changed_files if outcome is not None else [],
            "tokens": {**totals, "reasoning": 0},
            "models": models,
            "hosted_tokens": hosted_tokens(models),
            "harness": {
                "name": contestant.harness,
                "model": contestant.native,
                "reported_model": outcome.model if outcome is not None else None,
                "version": outcome.version if outcome is not None else None,
                "auth": outcome.auth if outcome is not None else None,
                "turns": outcome.turns if outcome is not None else 0,
                "tool_calls": outcome.tool_calls if outcome is not None else 0,
                "reported_cost": outcome.reported_cost if outcome is not None else None,
                "detail": outcome.detail if outcome is not None else "",
            },
            "infra_error": "error" in row or bool(outcome is not None and outcome.infra_error),
            "wall_seconds": outcome.wall_seconds if outcome is not None else 0.0,
            "gpu_seconds": 0.0,
            "task_id": None,
            "finished": _now(),
        }
    )
    if not options.keep:
        workspace.remove_tree(place)
    return row


def task_handoffs(result: TaskResult | None) -> list[dict[str, Any]]:
    """The HANDOFF records a task wrote to its run directory (`handoffs.jsonl`)."""
    if result is None or not result.run_dir:
        return []
    path = Path(result.run_dir) / HANDOFFS_FILE
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


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
        "optimize": options.optimize,
        "scoring": SCORING,
    }
    if any(c.name == RELAY for c in contestants):
        manifest["relay"] = {"workers": list(options.relay or []), "step": options.relay_step}
    if any(c.name == BEST_OF_N for c in contestants):
        manifest["best_of"] = options.best_of
    if options.learning != "off":
        manifest["learning"] = options.learning
    harnesses = sorted({c.harness for c in contestants if c.harness is not None})
    if harnesses:
        manifest["harnesses"] = _harness_versions(harnesses, config)
        manifest["isolated_harnesses"] = True
    natives = [c for c in contestants if c.native is not None]
    for harness in sorted({c.harness for c in natives if c.harness is not None}):
        from stepswitch.workers.harness import get_adapter

        names = ", ".join(c.name for c in natives if c.harness == harness)
        echo(native.NOTICE.format(title=get_adapter(harness).title, names=names))
    manifest_path = out_dir / MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    results = out_dir / RESULTS_FILE
    results.write_text("", encoding="utf-8")
    handoffs = out_dir / HANDOFFS_FILE
    handoffs.unlink(missing_ok=True)
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
                briefs = row.pop("_handoffs", [])
                with results.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
                if briefs:
                    with handoffs.open("a", encoding="utf-8", newline="\n") as handle:
                        for record in briefs:
                            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                verdict = "passed" if row["passed"] else "failed"
                echo(f"    {verdict} ({row['status']}, {row['wall_seconds']:.0f} s)")
    if not options.keep:
        workspace.remove_tree(work)
    manifest["finished"] = _now()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    write_report(out_dir)
    return out_dir


def _harness_versions(names: Sequence[str], config: Any) -> dict[str, str | None]:
    """The installed version of each harness a bench runs (recorded in the manifest)."""
    from stepswitch.workers.harness import get_adapter, harness_settings

    versions: dict[str, str | None] = {}
    for name in names:
        try:
            settings = harness_settings(config, name)[0]
        except ValueError:
            settings = None
        versions[name] = get_adapter(name).version(settings)
    return versions


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
