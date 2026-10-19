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

import contextlib
import dataclasses
import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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


PROBE_PROMPT = """\
You write tests that pin down what a coding task asks for.
Step: edit
Write one pytest file with at most 8 short test functions for the task below. Test only
behaviour the task states, with exact expected values, through the names in the code outline.
Import from the project's modules. Use no fixtures, files, network or randomness. Answer with
one ```python code block."""

TARGETED_PROMPT = """\
You settle a disagreement between solutions of a coding task.
Step: edit
The solutions pass the project's tests but disagree on the tests below. Write one pytest file
with at most 6 test functions that check what the task asks for in exactly these cases, with
exact expected values. Answer with one ```python code block."""

OUTLINE_LINES = 40
SOURCE_CHARS = 3000
_DEF_LINE = re.compile(r"^\+\s*((?:async\s+)?def\s+\w+\s*\(.*|class\s+\w+.*)$")


@dataclass
class _Shared:
    """What every candidate of one task shares."""

    spec: TaskSpec
    task_id: str
    root: Path
    repo: Path
    config: Config
    catalog: Catalog
    trajectory: Trajectory
    ledger: Ledger
    budget: BudgetTracker
    features: TaskFeatures
    rules: list[Rule]
    policy: Any
    offline: bool
    egress: net.Egress
    allow_remote: bool
    pinned: Any
    gates: Callable[..., Any]
    clock: Callable[[], float]
    say: Callable[[str], None]
    db: StateDB
    state_dir: Path
    opt: dict[str, Any]
    # Best-of-N: `planner(n)` plans n candidates (the first ones never change).
    planner: Callable[[int], list[Any]] | None = None
    # The learning router's samples for this task, and the posterior means they were drawn
    # from (SPEC §10, ADR 0026).
    learned: dict[str, float] = field(default_factory=dict)
    means: dict[str, float] = field(default_factory=dict)
    # A replay of a recorded run (SPEC §11, ADR 0027): `trajectory.replay.Replay`.
    replay: Any = None


@dataclass
class _Run:
    """One candidate: a worktree, the agent that worked in it and what came of it."""

    index: int | None
    plan: Any
    worktree: Any
    agent: Any = None
    outcome: Any = None
    review: Any = None
    tests_passed: int | None = None
    diff: str = ""
    changed: list[str] = field(default_factory=list)
    toolbox: Any = None

    @property
    def status(self) -> str:
        return str(self.outcome.status) if self.outcome is not None else "failed"

    def as_dict(self) -> dict[str, Any]:
        from stepswitch.verify import best_of_n

        outcome = self.outcome
        return {
            "index": self.index,
            "family": self.plan.family if self.plan is not None else None,
            "workers": list(self.plan.workers) if self.plan is not None else [],
            "seed": self.plan.seed if self.plan is not None else None,
            "note": self.plan.note if self.plan is not None else "",
            "status": self.status,
            "stop_reason": outcome.stop_reason if outcome is not None else None,
            "detail": (outcome.detail if outcome is not None else "")[:500],
            "steps": self.agent.step if self.agent is not None else 0,
            "endpoints": list(self.agent.endpoints) if self.agent is not None else [],
            "changed_files": list(self.changed),
            "changed_lines": best_of_n.changed_lines(self.diff),
            "branch": self.worktree.branch if self.changed else None,
            "review": self.review.as_dict() if self.review is not None else None,
            "tests_passed": self.tests_passed,
            "handoffs": len(self.agent.handoffs) if self.agent is not None else 0,
        }


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
    replay: Any = None,
) -> TaskResult:
    """Run one task to a final status and return its result. With `replay` (a
    `trajectory.replay.Replay`), run it again from its recording (SPEC §11)."""
    from stepswitch import config as config_module
    from stepswitch.context.settings import ContextSettings
    from stepswitch.core.track import track_policy
    from stepswitch.verify import best_of_n
    from stepswitch.workers.builtin_agent import LoopOutcome
    from stepswitch.workers.catalog import Catalog
    from stepswitch.worktree import WorktreePool

    clock = replay.clock if replay is not None else clock or time.monotonic
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
    trajectory.emit(
        "task_start",
        task_id=task_id,
        spec=json.loads(spec.model_dump_json()),
        **(replay.header() if replay is not None else {}),
    )
    say(f"Task {task_id}")

    pool = WorktreePool(repo, state_dir)
    runs: list[_Run] = []
    winner = 0
    count = 1
    report: Any = None
    failure: LoopOutcome | None = None
    optimize = True
    learning_settings: Any = None
    features_seen: Any = None
    try:
        if db.cancel_requested(task_id):
            raise _Cancelled
        first = _acquire(pool, task_id, spec, replay, None)
        runs.append(_Run(None, None, first))
        workdir = Path(first.path)
        trajectory.emit("worktree", path=str(workdir), base=first.base_commit)
        row.update("routing")
        trajectory.emit("status", status="routing")
        rules: list[Rule] = rules_from_config(config)
        if catalog is None:
            catalog = Catalog.from_config(config, root, egress=egress, db=db, task_id=task_id)
        settings = ContextSettings.from_config(config)
        if replay is not None:
            catalog = replay.catalog(catalog)
            settings = replay.recording.context_settings() or settings
        optimize = settings.optimize if spec.optimize is None else spec.optimize
        # The writer of briefs may be outside the routing pool: it never takes a step.
        summarizer = None
        if optimize:
            summarizer = replay.summarizer(catalog) if replay is not None else catalog.summarizer()
        trajectory.emit(
            "catalog",
            workers=[dataclasses.asdict(s) for s in catalog.specs()],
            summarizer=summarizer.spec.id if summarizer is not None else None,
            settings=dataclasses.asdict(settings),
        )
        if spec.workers:
            catalog = catalog.subset(spec.workers)
            trajectory.emit("pool", workers=[w.spec.id for w in catalog.workers.values()])
        relay = _relay(spec, catalog)
        for note in catalog.notes:
            say(f"  note: {note}")
        if replay is not None:
            features = _recorded_features(
                replay, catalog=catalog, ledger=ledger, budget=budget, trajectory=trajectory
            )
        else:
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
        features_seen = features
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
        shared = _Shared(
            spec=spec,
            task_id=task_id,
            root=root,
            repo=repo,
            config=config,
            catalog=catalog,
            trajectory=trajectory,
            ledger=ledger,
            budget=budget,
            features=features,
            rules=rules,
            policy=policy,
            offline=policy_offline,
            egress=task_egress,
            allow_remote=allow_remote,
            pinned=pinned,
            gates=gates or _default_gates(),
            clock=clock,
            say=say,
            db=db,
            state_dir=state_dir,
            opt=_optimization(
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
                scrub=[],
            ),
            replay=replay,
        )
        if replay is not None:
            # A replay samples nothing and teaches the learning router nothing.
            shared.learned, shared.means = replay.learned(), replay.means()
            if replay.recording.learning is not None:
                trajectory.emit("learning", **replay.recording.learning)
        else:
            learning_settings, shared.learned, shared.means = _learning_samples(shared)
        count = (
            1 if relay is not None else best_of_n.resolve_count(spec.best_of, features.difficulty)
        )
        row.update("running")
        trajectory.emit("status", status="running")
        if count == 1:
            _run_candidate(shared, runs[0])
        else:
            winner, report = _best_of_n(shared, pool, runs, count)
    except _Cancelled:
        trajectory.emit("cancelled", step=0)
        failure = LoopOutcome("stopped", "cancelled", detail="cancelled before it started")
    except RuleError as exc:
        failure = LoopOutcome("failed", "error", detail=f"policy rules: {exc}")
    except Exception as exc:  # noqa: BLE001 - every failure ends with a recorded status
        failure = LoopOutcome("failed", "error", detail=f"{type(exc).__name__}: {exc}")
        trajectory.emit("error", message=str(exc)[:2000], kind=type(exc).__name__)
    finally:
        for run in runs:
            if run.toolbox is not None:
                with contextlib.suppress(Exception):
                    run.toolbox.close()

    chosen = runs[winner] if runs else None
    outcome = failure or (chosen.outcome if chosen is not None else None)
    if outcome is None:
        outcome = LoopOutcome("failed", "error", detail="no candidate ran")
    uncertain = bool(report is not None and report.uncertain)
    human = report is not None and report.detected and spec.on_disagreement == "human"
    if human and outcome.status == "done":
        outcome = dataclasses.replace(
            outcome, status="needs-human", detail="passing candidates disagree"
        )
    for position, run in enumerate(runs):
        is_winner = position == winner
        try:
            run.diff = run.worktree.diff()
            run.changed = list(run.worktree.changed_files())
            if run.changed:
                label = "" if run.index is None or is_winner else f" (candidate {run.index})"
                run.worktree.task_id = task_id if is_winner else f"{task_id}-c{run.index}"
                run.worktree.commit(f"stepswitch: {spec.instruction.strip()[:60]}{label}")
            if is_winner and outcome.status == "done" and spec.apply and run.diff:
                if uncertain:
                    trajectory.emit("not_applied", reason="passing candidates disagree")
                else:
                    run.worktree.apply_to(repo)
                    trajectory.emit("applied", files=run.changed)
        except Exception as exc:  # noqa: BLE001 - the result is still recorded
            trajectory.emit("error", message=f"finishing the worktree: {exc}")
            if is_winner:
                outcome.detail = (outcome.detail + f"; worktree: {exc}").strip("; ")
        finally:
            run.worktree.release(keep=False)
        if run.index is not None and run.diff:
            trajectory.write_file(f"candidate-{run.index}.patch", run.diff)
    diff = chosen.diff if chosen is not None else ""
    if diff:
        trajectory.write_file("diff.patch", diff)
    if learning_settings is not None and learning_settings.learns:
        _learn_from_outcomes(db, trajectory, learning_settings, features_seen, runs, outcome)
    agents = [run.agent for run in runs if run.agent is not None]
    records = [record for agent in agents for record in agent.handoffs]
    if count > 1 and records:
        trajectory.write_file(
            "handoffs.jsonl",
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        )
    context: dict[str, int] = {}
    for agent in agents:
        for key, value in agent.stats.items():
            context[key] = context.get(key, 0) + int(value)
    endpoints: list[str] = []
    for agent in agents:
        endpoints += [e for e in agent.endpoints if e not in endpoints]
    totals = ledger.totals()
    summary = outcome.summary or outcome.detail
    review = None
    if chosen is not None and chosen.review is not None:
        review = chosen.review.as_dict()
    elif chosen is not None and chosen.agent is not None:
        review = chosen.agent.last_review
    result = TaskResult(
        task_id=task_id,
        status=outcome.status,  # type: ignore[arg-type]
        stop_reason=outcome.stop_reason,  # type: ignore[arg-type]
        summary=summary,
        steps=sum(agent.step for agent in agents),
        endpoints=endpoints,
        switches=sum(agent.switches for agent in agents),
        escalations=budget.escalations,
        changed_files=chosen.changed if chosen is not None else [],
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
        branch=chosen.worktree.branch if chosen is not None and chosen.changed else None,
        optimize=agents[0].optimize if agents else optimize,
        failovers=sum(agent.failovers for agent in agents),
        handoffs=len(records),
        compactions=sum(agent.compactions for agent in agents),
        context=context,
        best_of=count,
        candidates=[run.as_dict() for run in runs if run.index is not None],
        winner=chosen.index if chosen is not None and count > 1 else None,
        disagreement=report.as_dict() if report is not None else None,
        uncertain=uncertain,
        review=review,
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


def _run_candidate(shared: _Shared, run: _Run) -> None:
    """Run the built-in agent for one candidate (or the only one) in its worktree."""
    from stepswitch.workers.builtin_agent import AgentLoop, LoopOutcome
    from stepswitch.workers.tools import Toolbox

    spec = shared.spec
    plan = run.plan
    trajectory = (
        shared.trajectory if run.index is None else shared.trajectory.tagged(candidate=run.index)
    )
    workdir = Path(run.worktree.path)
    if run.index:
        trajectory.emit("worktree", path=str(workdir), base=run.worktree.base_commit)
    source = shared.catalog.subset(list(plan.workers)) if plan is not None else shared.catalog
    recorded = shared.replay.ladder(run.index) if shared.replay is not None else None
    why: str | None
    if recorded is not None and recorded.get("levels"):
        ladder = Ladder.for_levels([int(level) for level in recorded["levels"]])
        ladder.level, why = int(recorded["start"]), str(recorded.get("reason") or "")
    else:
        if shared.pinned is not None:
            ladder = Ladder.for_levels([shared.pinned.spec.level])
        else:
            ladder = Ladder.for_levels(
                source.levels(offline=shared.offline, allow_remote=shared.allow_remote)
            )
        ladder.level, why = start_level(shared.features, ladder, track=spec.track)
    trajectory.emit(
        "ladder",
        levels=ladder.levels,
        start=ladder.level,
        rung=RUNG_NAMES.get(ladder.level, str(ladder.level)),
        reason=why,
        workers=[s.as_dict() for s in source.specs()],
    )
    task_spec = (
        spec if plan is None else spec.model_copy(update={"seed": plan.seed, "review": False})
    )
    toolbox = Toolbox(
        workdir,
        test_command=shared.policy.test_command,
        config=shared.config,
        offline=shared.offline,
        cache_dir=shared.state_dir / "cache" / "context",
    )
    run.toolbox = toolbox
    scrub = [(str(workdir), "."), (str(shared.root), "<project>"), (str(Path.home()), "~")]
    replay = shared.replay.for_candidate(run.index) if shared.replay is not None else None
    recorded_workdir = shared.replay.workdir(run.index) if shared.replay is not None else None
    if recorded_workdir:
        # Recorded tool results name the recorded worktree.
        scrub.insert(0, (recorded_workdir, "."))
    try:
        run.agent = AgentLoop(
            spec=task_spec,
            task_id=shared.task_id,
            workdir=workdir,
            base=run.worktree.base_commit,
            catalog=shared.catalog,
            toolbox=toolbox,
            trajectory=trajectory,
            ledger=shared.ledger,
            budget=shared.budget,
            features=shared.features,
            ladder=ladder,
            gates=shared.gates,
            rules=shared.rules,
            config=shared.config,
            clock=shared.clock,
            echo=shared.say,
            diff=run.worktree.diff,
            track_notes="\n\n".join(shared.policy.notes),
            overview=repo_overview(workdir, shared.policy.test_command),
            allow_remote=shared.allow_remote,
            offline=shared.offline,
            glossary=shared.policy.glossary or "",
            constraints=list(shared.policy.notes),
            golden=list(shared.policy.golden),
            candidate=run.index,
            families=frozenset({plan.family}) if plan is not None else None,
            learned=dict(shared.learned),
            means=dict(shared.means),
            replay=replay,
            **{**shared.opt, "scrub_paths": scrub},
        )
        run.outcome = run.agent.run()
        if run.agent.optimize and replay is None:
            _learn_from_handoffs(
                shared.db, run.agent.handoffs, success=run.outcome.status == "done"
            )
    except Exception as exc:
        if plan is None:
            raise
        run.outcome = LoopOutcome("failed", "error", detail=f"{type(exc).__name__}: {exc}")
        trajectory.emit("error", message=str(exc)[:2000], kind=type(exc).__name__)
    finally:
        toolbox.close()
        run.toolbox = None


def _best_of_n(shared: _Shared, pool: Any, runs: list[_Run], count: int) -> tuple[int, Any]:
    """Run `count` candidates, compare and review the passing ones, select the winner.

    `runs` holds the first candidate's run (its worktree is already acquired); every candidate
    is appended to it as it starts, so the caller can release all worktrees whatever happens.
    Returns the winner's position in `runs` and the disagreement report (None when fewer than
    two candidates passed).
    """
    from stepswitch.router.policy import prior
    from stepswitch.router.router import RouterState, hard_exclusion
    from stepswitch.verify import best_of_n

    spec = shared.spec
    state = RouterState(
        step=0,
        track=spec.track,
        offline=shared.offline,
        allow_remote=shared.allow_remote,
        worker_pin=shared.pinned.spec.id if shared.pinned is not None else None,
    )
    eligible = [s for s in shared.catalog.specs() if hard_exclusion(s, "edit", state) is None]
    base_rates = shared.opt.get("base_rates") or {}

    def rank(worker: Any) -> float:
        return prior(worker, "edit", shared.features, base=base_rates.get(worker.id))

    def live_plans(n: int) -> list[Any]:
        return best_of_n.plan_candidates(eligible, n, rank=rank, seed=spec.seed)

    def planner(n: int) -> list[Any]:
        if shared.replay is None:
            return live_plans(n)
        recorded: list[Any] = shared.replay.plans(n, live_plans)
        return recorded

    shared.planner = planner
    plans = planner(count)
    if not plans:
        raise ValueError("no worker can take this task's edit steps")
    shared.trajectory.emit(
        "best_of_n",
        count=count,
        difficulty=shared.features.difficulty,
        plans=[p.as_dict() for p in plans],
        policy=spec.on_disagreement,
    )
    shared.say(f"  best-of-{count}: " + ", ".join(p.family for p in plans))
    head = runs[0]
    for plan in plans:
        reason = shared.budget.exceeded()
        if plan.index > 0 and reason is not None:
            shared.trajectory.emit("candidate_skipped", candidate=plan.index, reason=reason)
            break
        _start(shared, pool, runs, plan, head if plan.index == 0 else None)
    report = _compare(shared, pool, runs)
    passing = [run for run in runs if run.status == "done"]
    for run in passing if len(passing) >= 2 else passing[:1]:
        if run.agent is not None:
            run.review = run.agent.review_change()
    finished = [
        best_of_n.Finished(
            run.index or 0,
            run.plan.family,
            run.status,
            best_of_n.changed_lines(run.worktree.diff()),
            run.review,
            run.tests_passed,
        )
        for run in runs
    ]
    kept = report.kept if report is not None and report.resolved and report.kept else None
    chosen = best_of_n.select(finished, among=kept)
    why = best_of_n.explain(chosen, finished)
    shared.trajectory.emit(
        "selection",
        winner=chosen,
        reason=why,
        candidates=[
            {
                "candidate": f.index,
                "family": f.family,
                "status": f.status,
                "changed_lines": f.changed_lines,
                "tests_passed": f.tests_passed,
                "review_score": f.review.score if f.review is not None else None,
                "blocking": f.review.blocking if f.review is not None else None,
            }
            for f in finished
        ],
    )
    shared.say(f"  selected: {why}")
    position = next((i for i, run in enumerate(runs) if run.index == chosen), 0)
    return position, report


def _start(shared: _Shared, pool: Any, runs: list[_Run], plan: Any, run: _Run | None) -> _Run:
    """Run the candidate of `plan`: in `run` (the first, whose worktree the task acquired) or
    in a new worktree, appended to `runs` before it starts."""
    if run is None:
        worktree = _acquire(
            pool, f"{shared.task_id}-c{plan.index}", shared.spec, shared.replay, plan.index
        )
        run = _Run(plan.index, plan, worktree)
        runs.append(run)
    else:
        run.index, run.plan = plan.index, plan
    shared.trajectory.emit("candidate_start", candidate=plan.index, plan=plan.as_dict())
    shared.say(f"  candidate {plan.index} ({plan.family})")
    _run_candidate(shared, run)
    shared.trajectory.emit(
        "candidate_end",
        candidate=plan.index,
        family=plan.family,
        status=run.status,
        stop_reason=run.outcome.stop_reason if run.outcome is not None else None,
    )
    return run


def _outline(diffs: list[str]) -> str:
    """The definitions the candidates added or changed, for probe tests: `file: def f(x)`."""
    from stepswitch.verify import impact

    lines: list[str] = []
    for diff in diffs:
        current = ""
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                current = line[6:]
                continue
            match = _DEF_LINE.match(line)
            if match and current and not impact.is_test_file(current):
                entry = f"{current}: {match.group(1).strip().rstrip(':')}"
                if entry not in lines:
                    lines.append(entry)
    return "\n".join(lines[:OUTLINE_LINES]) or "(no new definitions)"


def _compare(shared: _Shared, pool: Any, runs: list[_Run]) -> Any:
    """The disagreement check on the passing candidates and the policy's next move (SPEC
    §8.4); None when fewer than two candidates passed."""
    from stepswitch.verify import commands, disagreement, science

    spec = shared.spec
    passing = [run for run in runs if run.status == "done"]
    if len(passing) < 2:
        return None
    python = commands.project_python(Path(passing[0].worktree.path), shared.config, spec.track)
    env = commands.project_env(python)
    science_config = None
    if spec.track == "science":
        try:
            science_config = science.load_science(Path(passing[0].worktree.path))
        except science.ScienceConfigError as exc:
            shared.trajectory.emit("disagreement_note", message=f"science.toml: {exc}")

    def files_for(group: list[_Run]) -> list[Any]:
        found = []
        for run in group:
            found += disagreement.candidate_tests(
                run.index or 0, Path(run.worktree.path), run.worktree.changed_files()
            )
        return found

    files = files_for(passing)
    if spec.probe_tests and passing[0].agent is not None:
        user = (
            f"Task: {spec.instruction.strip()}\n\nCode outline (names the solutions define):\n"
            + _outline([run.worktree.diff() for run in passing])
        )
        text = passing[0].agent.write_tests(PROBE_PROMPT, user)
        code = disagreement.extract_test_file(text or "")
        shared.trajectory.emit("probe_tests", written=code is not None, chars=len(code or ""))
        if code is not None:
            files.append(disagreement.probe_file(code))

    def analyze(group: list[_Run], tests: list[Any]) -> Any:
        return disagreement.analyze(
            [disagreement.Subject(run.index or 0, Path(run.worktree.path)) for run in group],
            tests,
            python=python,
            env=env,
            science_config=science_config,
            policy=spec.on_disagreement,
        )

    report = analyze(passing, files)
    if report.detected and spec.on_disagreement == "tests":
        _settle_by_tests(shared, report, passing, files, python=python, env=env)
    elif report.detected and spec.on_disagreement == "escalate" and shared.planner is not None:
        reason = shared.budget.exceeded()
        if reason is not None:
            report.notes.append(f"no candidate to escalate to: {reason}")
        else:
            plan = shared.planner(len(runs) + 1)[-1]
            extra = _start(shared, pool, runs, plan, None)
            if extra.status == "done":
                passing.append(extra)
                probes = [f for f in files if f.author is None]
                report = analyze(passing, files_for(passing) + probes)
            kept = disagreement.majority(report.clusters)
            if report.detected and kept is not None:
                report.resolved = True
                report.kept = kept
                report.resolution = f"the majority of {len(passing)} passing candidates"
            elif report.detected:
                report.notes.append("one more candidate gave no majority")
    for run in passing:
        run.tests_passed = report.passed.get(run.index or 0)
    shared.trajectory.emit("disagreement", **report.as_dict())
    if report.detected:
        shared.say(
            "  passing candidates disagree"
            + (f"; {report.resolution}" if report.resolved else f" ({spec.on_disagreement})")
        )
    return report


def _settle_by_tests(
    shared: _Shared,
    report: Any,
    passing: list[_Run],
    files: list[Any],
    *,
    python: str,
    env: dict[str, str],
) -> None:
    """The `tests` policy: targeted tests on the split behaviour decide between clusters."""
    from stepswitch.verify import disagreement

    splits = [s for s in report.splits if s.kind == "test"][:5]
    if not splits or passing[0].agent is None:
        report.notes.append("targeted tests need tests that split the candidates")
        return
    shown = [
        f"- {s.name}: " + ", ".join(f"candidate {k} {v}" for k, v in sorted(s.outcomes.items()))
        for s in splits
    ]
    tags = {disagreement.tag_of(s.name) for s in splits}
    sources = "\n\n".join(
        f"# {f.rel} (from {f.tag})\n{f.text[:SOURCE_CHARS]}"
        for f in files
        if f.is_test and f.tag in tags
    )
    user = (
        f"Task: {shared.spec.instruction.strip()}\n\nDisputed tests:\n"
        + "\n".join(shown)
        + f"\n\nTheir sources:\n{sources}"
    )
    text = passing[0].agent.write_tests(TARGETED_PROMPT, user)
    code = disagreement.extract_test_file(text or "")
    shared.trajectory.emit("targeted_tests", written=code is not None, chars=len(code or ""))
    if code is None:
        report.notes.append("no targeted tests were written")
        return
    targeted = disagreement.TestFile("test_targeted.py", code, None)
    subjects = [disagreement.Subject(run.index or 0, Path(run.worktree.path)) for run in passing]
    results = {
        s.index: disagreement.run_tests(s, [targeted], python=python, env=env) for s in subjects
    }
    kept = disagreement.resolve_by_tests(report, subjects, results)
    if kept is None:
        report.notes.append("the targeted tests did not single out one cluster")
        return
    report.resolved = True
    report.kept = kept
    report.resolution = f"targeted tests kept candidates {', '.join(map(str, kept))}"


def _acquire(pool: Any, name: str, spec: TaskSpec, replay: Any, candidate: int | None) -> Any:
    """A worktree for the task or a candidate: in a replay, at the recorded base commit."""
    if replay is not None and pool.mode() == "git":
        base = replay.base(candidate)
        if base:
            try:
                return pool.acquire(name, base=base)
            except Exception as exc:  # noqa: BLE001 - say which commit is missing
                raise ValueError(f"the recorded base commit {base[:12]} is gone: {exc}") from exc
    return pool.acquire(name, include_uncommitted=spec.include_uncommitted and replay is None)


def _recorded_features(
    replay: Any, *, catalog: Catalog, ledger: Ledger, budget: BudgetTracker, trajectory: Trajectory
) -> TaskFeatures:
    """A replay's task features: the recorded classification, charged as it was."""
    from stepswitch.compat.ir import Usage

    recording = replay.recording
    features: TaskFeatures = recording.features()
    details = dict(recording.classify.get("details") or {})
    replay.clock.advance(recording.classify_seconds)
    if details.get("classifier") == "model":
        usage = details.get("usage") or {}
        fields = usage_row_fields(Usage(**usage) if usage else None)
        worker_id = features.source.removeprefix("model:")
        worker = catalog.workers.get(worker_id)
        ledger.record(
            LedgerRow(
                task_id=ledger.task_id,
                step=0,
                step_type="classify",
                endpoint=worker.spec.endpoint if worker is not None else "",
                model=worker.spec.model if worker is not None else worker_id,
                wall_seconds=round(recording.classify_seconds, 6),
                gpu_seconds=0.0,
                data={"worker": worker_id, "replayed": True},
                **fields,
            )
        )
        budget.charge(tokens=fields["input_tokens"] + fields["output_tokens"])
    trajectory.emit("classify", step=0, features=features.as_dict(), details=details)
    return features


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


def _learning_samples(shared: _Shared) -> tuple[Any, dict[str, float], dict[str, float]]:
    """The learning router's settings, its samples for this task and the posterior means they
    were drawn from (both empty unless it samples).

    Each worker's cold start is the static edit prior, with the measured base rates when the
    task is optimized; one Beta sample per worker comes from the task's seed (ADR 0026).
    """
    from stepswitch.router import calibration, learning
    from stepswitch.router.policy import prior

    settings = learning.LearningSettings.from_config(shared.config)
    if shared.spec.learning is not None:
        settings = dataclasses.replace(settings, mode=shared.spec.learning)
    if not settings.samples:
        return settings, {}, {}
    bandit = learning.Bandit.from_dict(calibration.load_param(shared.db, learning.BANDIT_KEY))
    base_rates = shared.opt.get("base_rates") or {}
    priors = {
        spec.id: prior(spec, "edit", shared.features, base=base_rates.get(spec.id))
        for spec in shared.catalog.specs()
    }
    keys = learning.context_keys(shared.features)
    rng = learning.task_rng(shared.spec.seed, shared.task_id)
    samples = bandit.sample(priors, keys, settings, rng)
    means = {
        worker: round(bandit.belief(worker, keys, p, settings).success, 4)
        for worker, p in sorted(priors.items())
    }
    shared.trajectory.emit(
        "learning",
        mode=settings.mode,
        context=list(keys),
        tasks=bandit.tasks,
        samples=samples,
        means=means,
    )
    return settings, samples, means


def _learn_from_outcomes(
    db: StateDB,
    trajectory: Trajectory,
    settings: Any,
    features: Any,
    runs: list[_Run],
    outcome: Any,
) -> None:
    """Credit the task's outcome to the workers that did its work (each candidate its own).

    Cancelled tasks and tasks that ended in an error say nothing about the workers."""
    from stepswitch.router import calibration, learning

    if features is None or outcome.stop_reason in ("cancelled", "error"):
        return
    bandit = learning.Bandit.from_dict(calibration.load_param(db, learning.BANDIT_KEY))
    keys = learning.context_keys(features)
    updates = []
    for run in runs:
        agent = run.agent
        shares = learning.work_shares(agent.work_steps) if agent is not None else {}
        if not shares:
            continue
        status = outcome.status if run.index is None else run.status
        success = status == "done"
        bandit.update(shares, keys, success, agent.spent_seconds, agent.spent_tokens, settings)
        updates.append(
            {
                "candidate": run.index,
                "workers": shares,
                "success": success,
                "seconds": round(agent.spent_seconds, 3),
                "tokens": agent.spent_tokens,
                "reward": learning.reward(
                    success, agent.spent_seconds, agent.spent_tokens, settings
                ),
            }
        )
    if updates:
        calibration.save_param(db, learning.BANDIT_KEY, bandit.as_dict())
        trajectory.emit("learning_update", context=list(keys), updates=updates)


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
