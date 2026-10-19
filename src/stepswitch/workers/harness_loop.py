"""Running a task in an external harness (SPEC §12, ADR 0029): `ssw run --harness NAME`.

The orchestrator runs a `HarnessLoop` in place of the built-in agent. For one candidate it:

1. serves a proxy of its own, bound to the task: a `ProxyService` over the task's catalog on a
   free local port with a fresh token, every request a step of the task (a decision, a step
   event and a ledger row in the task's own records), routed with the task's features, rung,
   `--worker` pin, model families (best-of-N) and learned priors;
2. starts the harness in the worktree (`HarnessAdapter.start`), records its events (text is
   coalesced) and the raw transcript, and cancels it when the task's budget runs out;
3. runs the gates when the harness ends; when they fail, starts the harness again with the
   failure report (continuing its session when the adapter can), up to `gate_attempts` times per
   rung, then escalates: the proxy routes the next attempt on the next rung (SPEC §7.3);
4. reviews the passing change with the built-in agent's cross-family review (SPEC §8.5) and
   sends the harness back with the reviewer's issues once, as the built-in agent does;
5. reports what the orchestrator reads of an agent: steps, switches, endpoints, failovers,
   compactions, tokens spent, work steps per worker and the outcome.

The built-in agent of the candidate (`helper`) takes the text steps (review, best-of-N's test
writing); it never runs its own loop. Step numbers are shared: the proxy's steps and the helper's
continue one sequence. Harness runs are not replayable: their tool calls run in the harness.
"""

from __future__ import annotations

import secrets
import shutil
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from stepswitch.core.task import TEXT_STEPS
from stepswitch.proxy.runtime import ProxyServer, proxy_settings
from stepswitch.proxy.service import ProxyService, ProxySettings
from stepswitch.proxy.sessions import ProxyTask
from stepswitch.workers.builtin_agent import REVIEW_ROUNDS, AgentLoop, LoopOutcome
from stepswitch.workers.harness import (
    HarnessAdapter,
    HarnessEndpoint,
    HarnessEvent,
    HarnessMissing,
    HarnessSettings,
    HarnessTask,
    RunResult,
    get_adapter,
    harness_settings,
    shell_command,
    tail,
)

# The proxy model names a harness's roles map to unless `[harness.<name>.slots]` says otherwise:
# the router decides everything except planning, and request-class headers refine `ssw:auto`.
DEFAULT_SLOTS: dict[str, str] = {
    "main": "ssw:auto",
    "plan": "ssw:plan",
    "fast": "ssw:auto",
    "subagent": "ssw:auto",
    "review": "ssw:review",
}
WATCHDOG_SECONDS = 0.5
HARNESS_MAX_STEPS = 1_000_000
# Characters of a gate report or review sent back to the harness.
REPORT_CHARS = 4000
TEXT_EVENT_CHARS = 4000


@dataclass
class HarnessLoop:
    """One candidate's harness run (see the module docstring)."""

    helper: AgentLoop
    adapter: HarnessAdapter
    settings: HarnessSettings
    root: Path
    db: Any
    proxy: ProxySettings
    rules: Sequence[Any] = ()
    slots: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_SLOTS))
    pin: str | None = None
    keep_state: bool = False

    step: int = 0
    switches: int = 0
    failovers: int = 0
    compactions: int = 0
    endpoints: list[str] = field(default_factory=list)
    work_steps: dict[str, int] = field(default_factory=dict)
    work_families: set[str] = field(default_factory=set)
    spent_tokens: int = 0
    spent_seconds: float = 0.0
    results: list[RunResult] = field(default_factory=list)
    # Token optimization (SPEC §13) does not apply: the harness keeps its own transcript.
    optimize: bool = False
    limit_reason: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _task: ProxyTask | None = field(default=None, repr=False)

    # -- what the orchestrator reads of an agent ---------------------------------------------

    @property
    def spec(self) -> Any:
        return self.helper.spec

    @property
    def handoffs(self) -> list[dict[str, Any]]:
        return self.helper.handoffs

    @property
    def stats(self) -> dict[str, int]:
        return self.helper.stats

    @property
    def last_review(self) -> dict[str, Any] | None:
        return self.helper.last_review

    @property
    def trajectory(self) -> Any:
        return self.helper.trajectory

    @property
    def budget(self) -> Any:
        return self.helper.budget

    def review_change(self) -> Any:
        """Best-of-N's review of this candidate's change (by another model family)."""
        with self._helper_steps():
            return self.helper.review_change()

    def write_tests(self, prompt: str, user: str) -> str | None:
        with self._helper_steps():
            return self.helper.write_tests(prompt, user)

    # -- the run ---------------------------------------------------------------------------

    def run(self) -> LoopOutcome:
        spec = self.helper.spec
        if spec.track not in {"general", "science"}:
            return LoopOutcome("failed", "error", detail=f"unknown track {spec.track}")
        if self.adapter.executable(self.settings) is None:
            return LoopOutcome("failed", "error", detail=str(HarnessMissing(self.adapter)))
        service = ProxyService(
            self.root, self.helper.catalog, db=self.db, settings=self.proxy, rules=self.rules
        )
        session = self.helper.task_id
        if self.helper.candidate is not None:
            session += f"-c{self.helper.candidate}"
        self._task = service.attach(
            session,
            task_id=self.helper.task_id,
            trajectory=self.helper.trajectory,
            ledger=self.helper.ledger,
            instruction=spec.instruction,
            features=self.helper.features,
            level=self.helper.ladder.level,
            pin=self.pin,
            families=self.helper.families,
            learned=dict(self.helper.learned),
            on_step=self._on_step,
            limit=self.helper.budget.exceeded,
        )
        token = "ssw-" + secrets.token_urlsafe(32)
        server = ProxyServer(service, token=token, port=0)
        try:
            base_url = server.start()
            workers = self._workers()
            windows = [window for window in workers.values() if window]
            endpoint = HarnessEndpoint(
                base_url=base_url,
                token=token,
                session=session,
                slots={**DEFAULT_SLOTS, **self.slots},
                context=max(windows) if windows else None,
                workers=workers,
            )
            self.helper.trajectory.emit(
                "harness",
                step=self.step,
                harness=self.adapter.name,
                title=self.adapter.title,
                support=self.adapter.support,
                proxy=base_url,
                slots=dict(endpoint.slots),
                context=endpoint.context,
                permission=self.settings.permission,
            )
            return self._attempts(endpoint)
        finally:
            server.stop(reason="harness run ended")
            service.detach(session)
            if self.settings.state_dir is not None and not self.keep_state:
                shutil.rmtree(self.settings.state_dir, ignore_errors=True)

    def _workers(self) -> dict[str, int | None]:
        """The workers the proxy may route to and their context windows. The harness assumes
        the largest window: a transcript that outgrows a smaller worker's moves to one it fits."""
        return {
            w.spec.id: w.spec.context
            for w in self.helper.catalog
            if not w.spec.pin_only
            and (self.helper.families is None or w.spec.family_key in self.helper.families)
        }

    def _attempts(self, endpoint: HarnessEndpoint) -> LoopOutcome:
        spec = self.helper.spec
        prompt = self._first_prompt()
        resume: str | None = None
        failures_on_rung = 0
        review_rounds = 0
        attempt = 0
        while True:
            reason = self.helper.budget.exceeded()
            if reason is not None:
                return self._stopped(reason)
            attempt += 1
            task = HarnessTask(
                prompt=prompt,
                instruction=spec.instruction,
                resume=resume if self.adapter.resumable else None,
                attempt=attempt,
            )
            result = self._run_once(task, endpoint)
            if self.limit_reason is None:
                # The harness may have ended on the refused requests before the watchdog woke.
                self.limit_reason = self.helper.budget.exceeded()
            if self.limit_reason is not None:
                return self._stopped(self.limit_reason, result)
            if result.status == "failed":
                stop = result.stop_reason if result.stop_reason == "model_error" else "error"
                return LoopOutcome(
                    "failed",
                    stop,
                    summary=result.summary,
                    detail=f"{self.adapter.title}: {result.detail or 'the run failed'}",
                )
            if result.status == "stopped" and result.stop_reason == "cancelled":
                return LoopOutcome("stopped", "cancelled", detail="the harness was cancelled")
            resume = result.session_id or resume
            verdict = self._gates()
            if verdict.passed:
                if spec.review and review_rounds < REVIEW_ROUNDS:
                    review_rounds += 1
                    with self._helper_steps():
                        self.helper.work_families = set(self.work_families)
                        review = self.helper._review()
                    if review is not None and not review["approved"]:
                        prompt = self._review_prompt(review)
                        continue
                summary = result.summary.strip() or "Task completed; verification passed."
                return LoopOutcome(
                    "done",
                    None,
                    summary=summary[:4000],
                    verdict=verdict.to_dict(),
                    review=self.helper.last_review,
                )
            failures_on_rung += 1
            if failures_on_rung >= spec.gate_attempts:
                gate = verdict.failed_gate or "a"
                rung = self.helper.ladder.level
                why = f"{gate} gate failed {failures_on_rung} times on rung {rung}"
                with self._helper_steps():
                    ended = self.helper._escalate(why)
                if ended is not None:
                    ended.verdict = verdict.to_dict()
                    return ended
                if self._task is not None:
                    self._task.level = self.helper.ladder.level
                failures_on_rung = 0
            prompt = self._retry_prompt(verdict)

    def _stopped(self, reason: str, result: RunResult | None = None) -> LoopOutcome:
        if reason == "cancelled":
            self.helper.trajectory.emit("cancelled", step=self.step)
            return LoopOutcome("stopped", reason, detail="cancelled on request")
        self.helper.trajectory.emit(
            "budget", step=self.step, reason=reason, **self.helper.budget.snapshot()
        )
        summary = result.summary if result is not None else ""
        return LoopOutcome(
            "stopped", reason, summary=summary, detail=f"{reason.removeprefix('budget_')} budget"
        )

    def _run_once(self, task: HarnessTask, endpoint: HarnessEndpoint) -> RunResult:
        run_dir = self.helper.trajectory.run_dir / "harness"
        suffix = "" if self.helper.candidate is None else f"-c{self.helper.candidate}"
        settings = replace(
            self.settings,
            transcript=run_dir / f"attempt-{task.attempt}{suffix}.jsonl",
            log=run_dir / f"attempt-{task.attempt}{suffix}.stderr.log",
        )
        self.helper.trajectory.emit(
            "harness_start",
            step=self.step,
            attempt=task.attempt,
            harness=self.adapter.name,
            resume=task.resume is not None,
            prompt=task.prompt[:2000],
        )
        self.helper.echo(f"  {self.adapter.title}: attempt {task.attempt}")
        handle = self.adapter.start(task, self.helper.workdir, endpoint, settings)
        done = threading.Event()

        def watchdog() -> None:
            while not done.wait(WATCHDOG_SECONDS):
                reason = self.helper.budget.exceeded()
                if reason is not None:
                    self.limit_reason = reason
                    handle.cancel()
                    return

        threading.Thread(target=watchdog, daemon=True, name="harness-watchdog").start()
        pending: HarnessEvent | None = None
        try:
            for event in handle.events():
                if (
                    event.kind == "text"
                    and pending is not None
                    and pending.data.get("subagent") == event.data.get("subagent")
                    and len(str(pending.data.get("text", ""))) < TEXT_EVENT_CHARS
                ):
                    pending.data["text"] = str(pending.data.get("text", "")) + str(
                        event.data.get("text", "")
                    )
                    continue
                if pending is not None:
                    self._record(pending, task.attempt)
                    pending = None
                if event.kind == "text":
                    pending = HarnessEvent("text", dict(event.data))
                    continue
                self._record(event, task.attempt)
            if pending is not None:
                self._record(pending, task.attempt)
            result = handle.result()
        finally:
            done.set()
        self.results.append(result)
        self.helper.trajectory.emit(
            "harness_end", step=self.step, attempt=task.attempt, **result.as_dict()
        )
        status = result.status + (f" ({result.stop_reason})" if result.stop_reason else "")
        self.helper.echo(f"  {self.adapter.title}: {status}")
        return result

    def _record(self, event: HarnessEvent, attempt: int) -> None:
        data = dict(event.data)
        if "text" in data:
            data["text"] = self.helper._scrub(str(data["text"]))[:TEXT_EVENT_CHARS]
        self.helper.trajectory.emit(
            "harness_event", step=self.step, attempt=attempt, event=event.kind, **data
        )
        if event.kind == "tool_call":
            self.helper.echo(f"    {data.get('name')}")
        elif event.kind == "compaction":
            with self._lock:
                self.compactions += 1

    def _gates(self) -> Any:
        with self._helper_steps():
            return self.helper._run_gates()

    def _on_step(self, info: dict[str, Any]) -> None:
        """A proxy step of this task (called on the proxy's threads)."""
        usage = info.get("usage") or {}
        tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        with self._lock:
            self.step = max(self.step, int(info["step"]))
            self.helper.budget.charge(tokens=tokens, gpu_seconds=float(info["gpu_seconds"]))
            self.spent_tokens += tokens
            self.spent_seconds += float(info["wall_seconds"])
            endpoint = str(info["endpoint"])
            if endpoint not in self.endpoints:
                self.endpoints.append(endpoint)
            if info.get("switched"):
                self.switches += 1
            if info.get("request_class") == "compaction":
                self.compactions += 1
            if info.get("error") is None and info["step_type"] not in TEXT_STEPS:
                worker = str(info["worker"])
                self.work_steps[worker] = self.work_steps.get(worker, 0) + 1
                self.work_families.add(str(info["family"]))
            if self._task is not None:
                self.failovers = self._task.failovers

    class _Steps:
        def __init__(self, loop: HarnessLoop) -> None:
            self.loop = loop

        def __enter__(self) -> None:
            loop = self.loop
            loop.helper.step = max(loop.helper.step, loop.step)

        def __exit__(self, *exc: object) -> None:
            loop = self.loop
            with loop._lock:
                loop.step = max(loop.step, loop.helper.step)
                for endpoint in loop.helper.endpoints:
                    if endpoint not in loop.endpoints:
                        loop.endpoints.append(endpoint)
            if loop._task is not None:
                loop._task.advance_to(loop.step)

    def _helper_steps(self) -> HarnessLoop._Steps:
        """The helper's steps continue the proxy's numbering, and the proxy's continue the
        helper's."""
        return HarnessLoop._Steps(self)

    # -- prompts -----------------------------------------------------------------------------

    def _checks(self) -> str:
        lines = []
        spec = self.helper.spec
        if spec.acceptance:
            lines.append("Acceptance checks:\n" + "\n".join(f"- {cmd}" for cmd in spec.acceptance))
        test = self.settings.test_command
        if test:
            lines.append(
                "When you are done, Stepswitch checks the change by running the project's"
                f" tests: `{shell_command(test)}`."
            )
        return "\n\n".join(lines)

    def _first_prompt(self) -> str:
        parts = [self.helper.spec.instruction.strip()]
        checks = self._checks()
        if checks:
            parts.append(checks)
        if self.helper.track_notes:
            parts.append(self.helper.track_notes)
        parts.append(
            "Work only inside the current directory. Do not commit: Stepswitch reviews and"
            " commits the change."
        )
        return "\n\n".join(parts)

    def _retry_prompt(self, verdict: Any) -> str:
        gate = verdict.failed_gate or "a"
        report = self.helper._scrub(str(verdict.report or ""))
        return "\n\n".join(
            [
                f"Task: {self.helper.spec.instruction.strip()}",
                f"Your previous attempt is in this working tree, but Stepswitch's checks failed"
                f" at the {gate} gate:\n{tail(report, REPORT_CHARS)}",
                self._checks(),
                "Fix the problem. Work only inside the current directory and do not commit.",
            ]
        ).strip()

    def _review_prompt(self, review: Mapping[str, Any]) -> str:
        text = self.helper._scrub(str(review.get("text") or ""))
        return "\n\n".join(
            [
                f"Task: {self.helper.spec.instruction.strip()}",
                "A reviewer found issues with the change in this working tree:\n"
                + tail(text, REPORT_CHARS),
                "Address them. Work only inside the current directory and do not commit.",
            ]
        )

    # -- construction --------------------------------------------------------------------------

    @classmethod
    def wrap(
        cls,
        helper: AgentLoop,
        *,
        name: str,
        root: Path,
        db: Any,
        config: Any,
        state_dir: Path,
        rules: Sequence[Any],
        test_command: Sequence[str] | None,
        offline: bool,
        allow_remote: bool,
        pin: str | None,
    ) -> HarnessLoop:
        """The harness run of a candidate whose built-in agent is `helper`."""
        adapter = get_adapter(name)
        # `max_steps` caps the built-in agent's own loop; a harness run is bounded by the
        # task's budgets and `[harness.<name>] max_turns`, so the helper's text steps (which
        # share the run's step numbers) are never refused for the harness's step count.
        helper.spec = helper.spec.model_copy(update={"max_steps": HARNESS_MAX_STEPS})
        settings, slots, keep = harness_settings(config, name)
        suffix = "" if helper.candidate is None else f"-c{helper.candidate}"
        settings = replace(
            settings,
            offline=offline,
            test_command=list(test_command) if test_command else None,
            state_dir=state_dir / "harness" / f"{helper.task_id}{suffix}",
        )
        return cls(
            helper=helper,
            adapter=adapter,
            settings=settings,
            root=root,
            db=db,
            proxy=proxy_settings(config, offline=offline, allow_remote=allow_remote),
            rules=list(rules),
            slots={**DEFAULT_SLOTS, **slots},
            pin=pin,
            keep_state=keep,
        )
