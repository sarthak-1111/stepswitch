"""The orchestrator's run loop for one task (SPEC §3, ARCHITECTURE §3).

`run_task` takes a worktree from the pool, classifies the task, builds the escalation ladder
and the track policy, runs the built-in agent and records everything: the task row and its
status transitions, the trajectory (`.stepswitch/runs/<task id>/events.jsonl`), router
decisions, the ledger, the diff and the final result. The repository itself is only changed
when the task is done and `apply` was asked for; otherwise the change stays on the branch
`stepswitch/<task id>`.

With the token optimizations on (Phase 5, ADR 0024) the agent also gets the context settings,
the T0 summarizer that writes HANDOFF briefs, the measured router parameters from the state
database (handoff recovery and base rates, `stepswitch.router.calibration`), and the paths to
keep out of briefs. Each finished task updates the learned handoff recovery with its outcome;
its briefs are in the run directory's `handoffs.jsonl`.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stepswitch import net, paths
from stepswitch.core.budget import BudgetTracker
from stepswitch.core.task import Budgets, TaskResult, TaskSpec, new_task_id
from stepswitch.ledger import Ledger, LedgerRow, usage_row_fields
from stepswitch.router.escalation import RUNG_NAMES, Ladder, start_level
from stepswitch.router.features import TaskFeatures
from stepswitch.router.rules import Rule, RuleError, rules_from_config
from stepswitch.state import StateDB, open_project_state
from stepswitch.trajectory.record import Trajectory

if TYPE_CHECKING:
    from stepswitch.config import Config
    from stepswitch.context.settings import ContextSettings
    from stepswitch.workers.catalog import Catalog

OVERVIEW_FILES = 60
CLASSIFIER_CACHE = "classifier.json"


def _budgets(spec: TaskSpec, config: Config) -> Budgets:
    """Task flags win; the project's [budgets] fill the gaps (SPEC §7.4, §20)."""
    defaults = config.budgets
    given = spec.budgets
    return Budgets(
        tokens=given.tokens if given.tokens is not None else defaults.tokens,
        wall_seconds=given.wall_seconds
        if given.wall_seconds is not None
        else defaults.wall_seconds,
        gpu_seconds=given.gpu_seconds if given.gpu_seconds is not None else defaults.gpu_seconds,
        escalations=given.escalations if given.escalations is not None else defaults.escalations,
    )


def _ensure_state_dir(root: Path) -> Path:
    directory = paths.state_dir(root)
    if not directory.is_dir():
        directory.mkdir(parents=True)
        # Keep machine-specific state out of version control even before `ssw init`.
        (directory / ".gitignore").write_text("*\n", encoding="utf-8", newline="\n")
    return directory


def repo_overview(path: Path, test_command: list[str] | None, limit: int = OVERVIEW_FILES) -> str:
    """A short description of the checkout for the agent's prompt: test command and files."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        listed = []
    files = sorted(f for f in listed if f and not f.startswith(".stepswitch/"))
    lines = []
    if test_command:
        lines.append("Tests: the `test` tool runs the project's test suite.")
    if files:
        shown = files[:limit]
        more = f"\n(and {len(files) - limit} more files)" if len(files) > limit else ""
        lines.append("Files:\n" + "\n".join(shown) + more)
    return "\n".join(lines)


def _load_cache(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(path: Path, data: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8", newline="\n")


def queue_task(db: StateDB, task_id: str, spec: TaskSpec) -> None:
    """Record the task as queued; a row the dashboard queued earlier keeps its creation time."""
    now = time.time()
    db.execute(
        "INSERT INTO tasks (id, created, updated, status, stop_reason, track, instruction,"
        " spec, result) VALUES (?, ?, ?, 'queued', NULL, ?, ?, ?, NULL)"
        " ON CONFLICT(id) DO UPDATE SET updated = excluded.updated, status = 'queued',"
        " stop_reason = NULL, track = excluded.track, instruction = excluded.instruction,"
        " spec = excluded.spec",
        (task_id, now, now, spec.track, spec.instruction, spec.model_dump_json()),
    )


class _Cancelled(Exception):
    """A cancel request arrived before the task started."""


class _TaskRow:
    def __init__(self, db: StateDB, task_id: str, spec: TaskSpec) -> None:
        self.db = db
        self.task_id = task_id
        queue_task(db, task_id, spec)

    def update(
        self, status: str, *, stop_reason: str | None = None, result: str | None = None
    ) -> None:
        self.db.execute(
            "UPDATE tasks SET updated = ?, status = ?, stop_reason = ?,"
            " result = COALESCE(?, result) WHERE id = ?",
            (time.time(), status, stop_reason, result, self.task_id),
        )


def _classify(
    spec: TaskSpec,
    repo: Path,
    *,
    catalog: Catalog,
    offline: bool,
    ledger: Ledger,
    budget: BudgetTracker,
    trajectory: Trajectory,
    clock: Callable[[], float],
    cache_path: Path,
) -> TaskFeatures:
    from stepswitch.router.classifier import classify

    worker = catalog.classifier()
    model = None
    if worker is not None:
        try:
            model = worker.get_model()
        except Exception as exc:  # noqa: BLE001 - fall back to the heuristic classifier
            trajectory.emit("model_error", worker=worker.spec.id, kind="start", message=str(exc))
    cache = _load_cache(cache_path)
    started = clock()
    features, details = classify(
        spec.instruction,
        repo,
        track=spec.track,
        offline=offline,
        model=model,
        worker_id=worker.spec.id if worker is not None else None,
        cache=cache,
    )
    wall = max(0.0, clock() - started)
    if details.get("classifier") == "model" and worker is not None:
        _save_cache(cache_path, cache)
        usage = details.get("usage") or {}
        from stepswitch.compat.ir import Usage

        fields = usage_row_fields(Usage(**usage) if usage else None)
        ledger.record(
            LedgerRow(
                task_id=ledger.task_id,
                step=0,
                step_type="classify",
                endpoint=worker.spec.endpoint,
                model=worker.spec.model,
                wall_seconds=round(wall, 6),
                gpu_seconds=round(wall if worker.spec.gpu else 0.0, 6),
                data={"worker": worker.spec.id},
                **fields,
            )
        )
        budget.charge(tokens=fields["input_tokens"] + fields["output_tokens"])
    trajectory.emit("classify", step=0, features=features.as_dict(), details=details)
    return features


def run_task(
    spec: TaskSpec,
    *,
    root: Path,
    config: Config | None = None,
    catalog: Catalog | None = None,
    egress: net.Egress | None = None,
    clock: Callable[[], float] | None = None,
    echo: Callable[[str], None] | None = None,
    gates: Callable[..., Any] | None = None,
    task_id: str | None = None,
) -> TaskResult:
    """Run one task to a final status and return its result."""
    from stepswitch import config as config_module
    from stepswitch.context.settings import ContextSettings
    from stepswitch.core.track import track_policy
    from stepswitch.workers.builtin_agent import AgentLoop, LoopOutcome
    from stepswitch.workers.catalog import Catalog
    from stepswitch.worktree import WorktreePool

    clock = clock or time.monotonic
    say = echo or (lambda _msg: None)
    root = root.resolve()
    repo = spec.repo.resolve()
    config = config or config_module.load(root)
    task_id = task_id or new_task_id()
    state_dir = _ensure_state_dir(root)
    db = open_project_state(root)
    trajectory = Trajectory(root, task_id, db=db)
    ledger = Ledger(db, task_id)
    row = _TaskRow(db, task_id, spec)
    budget = BudgetTracker(
        _budgets(spec, config), clock=clock, cancelled=lambda: db.cancel_requested(task_id)
    )
    offline = spec.offline or config.offline
    trajectory.emit("task_start", task_id=task_id, spec=json.loads(spec.model_dump_json()))
    say(f"Task {task_id}")

    pool = WorktreePool(repo, state_dir)
    worktree = None
    outcome: LoopOutcome
    agent: AgentLoop | None = None
    toolbox: Any = None
    try:
        if db.cancel_requested(task_id):
            raise _Cancelled
        worktree = pool.acquire(task_id, include_uncommitted=spec.include_uncommitted)
        workdir = Path(worktree.path)
        trajectory.emit("worktree", path=str(workdir), base=worktree.base_commit)
        row.update("routing")
        trajectory.emit("status", status="routing")
        rules: list[Rule] = rules_from_config(config)
        if catalog is None:
            catalog = Catalog.from_config(config, root, egress=egress, db=db, task_id=task_id)
        settings = ContextSettings.from_config(config)
        optimize = settings.optimize if spec.optimize is None else spec.optimize
        # The writer of briefs may be outside the routing pool: it never takes a step.
        summarizer = catalog.summarizer() if optimize else None
        if spec.workers:
            catalog = catalog.subset(spec.workers)
            trajectory.emit("pool", workers=[w.spec.id for w in catalog.workers.values()])
        relay = _relay(spec, catalog)
        for note in catalog.notes:
            say(f"  note: {note}")
        features = _classify(
            spec,
            workdir,
            catalog=catalog,
            offline=offline,
            ledger=ledger,
            budget=budget,
            trajectory=trajectory,
            clock=clock,
            cache_path=state_dir / "cache" / CLASSIFIER_CACHE,
        )
        policy = track_policy(spec.track, workdir, config, features)
        policy_offline = offline or policy.offline
        task_egress = egress or net.default()
        if policy_offline and not task_egress.policy.offline:
            task_egress = net.Egress(
                net.EgressPolicy(offline=True, allow_hosts=set(task_egress.policy.allow_hosts)),
                sinks=list(task_egress.sinks[1:]),
            )
        pinned = catalog.find(spec.worker) if spec.worker else None
        if spec.worker and pinned is None:
            known = ", ".join(sorted(catalog.workers)) or "none configured"
            raise ValueError(f"unknown worker {spec.worker!r} (workers: {known})")
        if pinned is not None and pinned.spec.id != spec.worker:
            spec = spec.model_copy(update={"worker": pinned.spec.id})
        allow_remote = spec.allow_remote or (pinned is not None and not pinned.spec.local)
        if pinned is not None:
            ladder = Ladder.for_levels([pinned.spec.level])
        else:
            ladder = Ladder.for_levels(
                catalog.levels(offline=policy_offline, allow_remote=allow_remote)
            )
        ladder.level, why = start_level(features, ladder, track=spec.track)
        trajectory.emit(
            "ladder",
            levels=ladder.levels,
            start=ladder.level,
            rung=RUNG_NAMES.get(ladder.level, str(ladder.level)),
            reason=why,
            workers=[s.as_dict() for s in catalog.specs()],
        )
        from stepswitch.workers.tools import Toolbox

        toolbox = Toolbox(
            workdir,
            test_command=policy.test_command,
            config=config,
            offline=policy_offline,
            cache_dir=state_dir / "cache" / "context",
        )
        agent = AgentLoop(
            spec=spec,
            task_id=task_id,
            workdir=workdir,
            base=worktree.base_commit,
            catalog=catalog,
            toolbox=toolbox,
            trajectory=trajectory,
            ledger=ledger,
            budget=budget,
            features=features,
            ladder=ladder,
            gates=gates or _default_gates(),
            rules=rules,
            config=config,
            clock=clock,
            echo=say,
            diff=worktree.diff,
            track_notes="\n\n".join(policy.notes),
            overview=repo_overview(workdir, policy.test_command),
            allow_remote=allow_remote,
            offline=policy_offline,
            glossary=policy.glossary or "",
            constraints=list(policy.notes),
            golden=list(policy.golden),
            **_optimization(
                optimize,
                settings,
                db=db,
                catalog=catalog,
                track=spec.track,
                config=config,
                egress=task_egress,
                summarizer=summarizer,
                relay=relay,
                relay_step=spec.relay_step,
                scrub=[(str(workdir), "."), (str(root), "<project>"), (str(Path.home()), "~")],
            ),
        )
        row.update("running")
        trajectory.emit("status", status="running")
        outcome = agent.run()
        if agent.optimize:
            _learn_from_handoffs(db, agent.handoffs, success=outcome.status == "done")
    except _Cancelled:
        trajectory.emit("cancelled", step=0)
        outcome = LoopOutcome("stopped", "cancelled", detail="cancelled before it started")
    except RuleError as exc:
        outcome = LoopOutcome("failed", "error", detail=f"policy rules: {exc}")
    except Exception as exc:  # noqa: BLE001 - every failure ends with a recorded status
        outcome = LoopOutcome("failed", "error", detail=f"{type(exc).__name__}: {exc}")
        trajectory.emit("error", message=str(exc)[:2000], kind=type(exc).__name__)
    finally:
        if toolbox is not None:
            toolbox.close()

    diff = ""
    changed: list[str] = []
    branch = None
    if worktree is not None:
        try:
            diff = worktree.diff()
            changed = list(worktree.changed_files())
            if changed:
                worktree.commit(f"stepswitch: {spec.instruction.strip()[:60]}")
                branch = f"stepswitch/{task_id}"
            if outcome.status == "done" and spec.apply and diff:
                worktree.apply_to(repo)
                trajectory.emit("applied", files=changed)
        except Exception as exc:  # noqa: BLE001 - the result is still recorded
            trajectory.emit("error", message=f"finishing the worktree: {exc}")
            outcome.detail = (outcome.detail + f"; worktree: {exc}").strip("; ")
        finally:
            worktree.release(keep=False)
    if diff:
        trajectory.write_file("diff.patch", diff)
    totals = ledger.totals()
    summary = outcome.summary or outcome.detail
    result = TaskResult(
        task_id=task_id,
        status=outcome.status,  # type: ignore[arg-type]
        stop_reason=outcome.stop_reason,  # type: ignore[arg-type]
        summary=summary,
        steps=agent.step if agent is not None else 0,
        endpoints=agent.endpoints if agent is not None else [],
        switches=agent.switches if agent is not None else 0,
        escalations=budget.escalations,
        changed_files=changed,
        diff=diff,
        verdict=outcome.verdict,
        tokens={
            k: int(totals[k])
            for k in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")
        },
        budget=budget.snapshot(),
        wall_seconds=round(budget.wall_seconds, 3),
        run_dir=str(trajectory.run_dir),
        worktree=None,
        branch=branch,
        optimize=agent.optimize if agent is not None else True,
        failovers=agent.failovers if agent is not None else 0,
        handoffs=len(agent.handoffs) if agent is not None else 0,
        compactions=agent.compactions if agent is not None else 0,
        context=dict(agent.stats) if agent is not None else {},
    )
    data = result.model_dump(mode="json", exclude={"diff"})
    trajectory.write_file("result.json", json.dumps(data, indent=2) + "\n")
    row.update(result.status, stop_reason=result.stop_reason, result=json.dumps(data))
    trajectory.emit(
        "task_end",
        status=result.status,
        stop_reason=result.stop_reason,
        detail=outcome.detail,
        summary=result.summary,
        budget=result.budget,
        ledger=totals,
    )
    say(f"{result.status}" + (f" ({result.stop_reason})" if result.stop_reason else ""))
    db.close()
    return result


def _relay(spec: TaskSpec, catalog: Catalog) -> tuple[str, str] | None:
    """The relay's two worker ids (`--relay first,second`), resolved in the catalog."""
    if not spec.relay:
        return None
    if len(spec.relay) != 2:
        raise ValueError("a relay names exactly two workers: first,second")
    ids = []
    for name in spec.relay:
        worker = catalog.find(name)
        if worker is None:
            known = ", ".join(sorted(catalog.workers)) or "none configured"
            raise ValueError(f"unknown relay worker {name!r} (workers: {known})")
        ids.append(worker.spec.id)
    if ids[0] == ids[1]:
        raise ValueError("a relay hands the task to another worker: give two different ones")
    return ids[0], ids[1]


def _optimization(
    optimize: bool,
    settings: ContextSettings,
    *,
    db: StateDB,
    catalog: Catalog,
    track: str,
    config: Config,
    egress: net.Egress,
    summarizer: Any,
    relay: tuple[str, str] | None,
    relay_step: int,
    scrub: list[tuple[str, str]],
) -> dict[str, Any]:
    """The AgentLoop arguments of the token and cache optimizations (ADR 0024)."""
    from stepswitch.context.compress import HttpCompressor
    from stepswitch.router import calibration
    from stepswitch.router.policy import BASE_BY_LEVEL

    out: dict[str, Any] = {
        "optimize": optimize,
        "settings": settings,
        "relay": relay,
        "relay_step": relay_step,
        "models_dir": config.models_dir(),
        "egress": egress,
        "scrub_paths": scrub,
    }
    if not optimize:
        return out
    table = calibration.load_priors(db)
    rates = {}
    for spec in catalog.specs():
        rate = table.rate(spec.model, track, BASE_BY_LEVEL.get(spec.level, 0.6))
        if rate is not None:
            rates[spec.id] = round(rate, 4)
    compression = settings.compression
    out.update(
        summarizer=summarizer,
        handoff_recovery=calibration.load_recovery(db).mean,
        base_rates=rates,
        compressor=HttpCompressor(compression.endpoint, egress=egress)
        if compression.enabled and compression.endpoint
        else None,
    )
    return out


def _learn_from_handoffs(db: StateDB, records: list[dict[str, Any]], *, success: bool) -> None:
    """Update the learned handoff recovery (SPEC §7.2) with this task's handoffs."""
    from stepswitch.router import calibration

    pairs = [
        (float(r["p_current"]), float(r["p_new"]))
        for r in records
        if r.get("reason") != "compaction"
        and r.get("p_current") is not None
        and r.get("p_new") is not None
    ]
    if pairs:
        calibration.record_handoff_outcomes(db, pairs, success)


def _default_gates() -> Callable[..., Any]:
    from stepswitch.verify.gates import run_gates

    return run_gates
