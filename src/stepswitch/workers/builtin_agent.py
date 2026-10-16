"""The built-in lean agent with step-level routing (SPEC §12, ARCHITECTURE §4.1, ADR 0015).

The agent is a state machine over step types. Before every model turn it predicts the type of
the next step, asks the router for a worker, and sends that worker the task state:

* text steps (plan, review, summarize) get a purpose-built prompt without tools;
* work steps (navigate, read, edit, run, triage) continue one shared IR transcript with the
  agent's tools. The transcript only grows, so each server's prefix cache stays valid, and any
  worker can take the next turn: reasoning is replayed only to its own origin (SPEC §4.5) and a
  worker without native tool calls sees the same history through the fallback protocol.

Calling `finish` runs the track's gates. A failure goes back to the worker as the tool result,
and after `gate_attempts` failures on one rung the escalation ladder moves up (SPEC §7.3).
Budgets are checked at every step boundary (SPEC §7.4). After the gates pass, a review step
may send the work back once, and a summary step writes the result.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from stepswitch.compat.client import Completion
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import (
    Message,
    Request,
    Sampling,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from stepswitch.core.budget import BudgetTracker
from stepswitch.core.task import TaskSpec
from stepswitch.ledger import Ledger, LedgerRow, estimate_tokens, gpu_seconds_of, usage_row_fields
from stepswitch.router.escalation import Ladder
from stepswitch.router.features import TaskFeatures
from stepswitch.router.router import Decision, RouterState, route
from stepswitch.router.rules import Rule
from stepswitch.trajectory.record import Trajectory
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.tools import CONTROL_TOOLS, TOOL_NAMES, ToolOutput

__all__ = ["CONTROL_TOOLS", "TOOL_NAMES", "AgentLoop", "LoopOutcome"]

# Consecutive work steps without progress (no new file read, search, edit or test outcome).
STUCK_STEPS = 8
# Consecutive text-only answers in work steps before the answer counts as `finish`.
TEXT_ONLY_LIMIT = 2
REVIEW_ROUNDS = 1
DIFF_PROMPT_CHARS = 6000
WORK_OUTPUT_TOKENS = {"cpu": 1024}
DEFAULT_WORK_OUTPUT_TOKENS = 2048
# Rough output sizes for the cost model's time estimates.
EXPECTED_OUTPUT_TOKENS = {"plan": 250, "review": 150, "summarize": 100}
DEFAULT_EXPECTED_OUTPUT = 200
NUDGE = "Continue the task: call a tool, or call the finish tool when the change is complete."
# A text-only answer that only says this is taken as a call to `finish`.
FINISH_WORDS = frozenset({"finish", "finished", "done", "complete", "task complete"})
# Added to a tool result that repeats the previous call's result for the same call: small
# models otherwise tend to run the same call again and again.
REPEAT_HINT = (
    "[You made the same call in your previous step and got the same result. Repeating it "
    "changes nothing: act on the result (for example, change the code with `edit`), try "
    "something else, or call `finish`.]"
)

WORK_PROMPT = """\
You are the coding agent of Stepswitch. You work in a git checkout of the user's repository
and change it to complete the task.

How to work:
- Find and read the relevant code with the tools before you change it.
- `read` shows each line as <line>:<hash>|<text>. `edit` refers to lines by those
  <line>:<hash> anchors; read a file again before editing it a second time.
- Keep the change small and focused on the task. Do not touch unrelated files.
- Run the tests with `test` before you finish.
- When the task is done, call `finish` with a one-sentence summary. The project's checks
  then run; if they fail you get the report and continue.
- Paths are relative to the repository root."""

PLAN_PROMPT = """\
You plan the work of a coding agent.
Step: plan
Write a short numbered plan (at most five steps) for the task below. Name the files to
inspect or change when you can tell. Do not write code."""

REVIEW_PROMPT = """\
You review a change that a coding agent made.
Step: review
Check the diff against the task. If the change does what the task asks and nothing harmful,
reply with APPROVE on the first line. Otherwise reply with ISSUES on the first line, followed
by a short list of concrete problems."""

SUMMARY_PROMPT = """\
You summarize a finished coding task.
Step: summarize
In one or two sentences, say what was changed and how it was verified."""


def _canonical_arguments(arguments: str) -> str:
    """Arguments as sorted JSON, so the same call with reordered keys compares equal."""
    try:
        return json.dumps(json.loads(arguments), sort_keys=True)
    except ValueError:
        return arguments.strip()


class StepModelLike(Protocol):
    def complete(self, request: Request) -> Completion: ...


class ToolboxLike(Protocol):
    def specs(self, names: Sequence[str] | None = None) -> list[ToolSpec]: ...

    def execute(self, name: str, arguments: Mapping[str, Any] | str) -> ToolOutput: ...


GateRunner = Callable[..., Any]


@dataclass
class LoopOutcome:
    status: str
    stop_reason: str | None
    summary: str = ""
    verdict: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    detail: str = ""


@dataclass
class AgentLoop:
    spec: TaskSpec
    task_id: str
    workdir: Path
    base: str | None
    catalog: Catalog
    toolbox: ToolboxLike
    trajectory: Trajectory
    ledger: Ledger
    budget: BudgetTracker
    features: TaskFeatures
    ladder: Ladder
    gates: GateRunner
    rules: Sequence[Rule] = ()
    config: Any = None
    clock: Callable[[], float] = time.monotonic
    echo: Callable[[str], None] = lambda _msg: None
    diff: Callable[[], str] = lambda: ""
    track_notes: str = ""
    overview: str = ""
    allow_remote: bool = False
    offline: bool = False

    step: int = 0
    current: str | None = None
    messages: list[Message] = field(default_factory=list)
    seen_tokens: dict[str, int] = field(default_factory=dict)
    failed: set[str] = field(default_factory=set)
    endpoints: list[str] = field(default_factory=list)
    switches: int = 0
    gate_runs: int = 0
    plan: str = ""
    finish_summary: str = ""
    last_error: str = ""
    last_review: dict[str, Any] | None = None
    context_caps: dict[str, int] = field(default_factory=dict)
    overflowed: bool = False
    last_call: tuple[str, str, str] | None = None  # name, arguments, result of the last call

    # ------------------------------------------------------------------------------------
    # Routing and model calls
    # ------------------------------------------------------------------------------------

    def _system_prompt(self) -> str:
        parts = [WORK_PROMPT]
        if self.track_notes:
            parts.append(self.track_notes)
        if self.overview:
            parts.append(self.overview)
        return "\n\n".join(parts)

    def _work_request(self, model: str, max_output: int) -> Request:
        return Request(
            model=model,
            system=[TextPart(text=self._system_prompt())],
            messages=list(self.messages),
            tools=self.toolbox.specs(),
            max_output_tokens=max_output,
            sampling=Sampling(temperature=0.2, seed=self.spec.seed),
        )

    @staticmethod
    def _chars(request: Request) -> int:
        size = len(request.system_text())
        size += sum(len(json.dumps(m.model_dump(exclude_none=True))) for m in request.messages)
        size += sum(len(json.dumps(t.model_dump(exclude_none=True))) for t in request.tools)
        return size

    def _decide(self, step_type: str, prompt_tokens: int, *, work: bool) -> Decision:
        state = RouterState(
            step=self.step + 1,
            current=self.current,
            level=self.ladder.level,
            transcript_tokens=prompt_tokens,
            output_tokens=EXPECTED_OUTPUT_TOKENS.get(step_type, DEFAULT_EXPECTED_OUTPUT),
            seen_tokens=dict(self.seen_tokens) if work else {},
            track=self.spec.track,
            offline=self.offline,
            allow_remote=self.allow_remote,
            worker_pin=self.spec.worker,
            failed=frozenset(self.failed),
            context_caps=dict(self.context_caps),
        )
        decision = route(
            self.features,
            step_type,
            state,
            self.catalog.specs(),
            self.rules,
            seed=self.spec.seed,
        )
        record = decision.as_dict()
        step = record.pop("step")
        self.trajectory.emit("decision", step=step, **record)
        if self.trajectory.db is not None:
            self.trajectory.db.execute(
                "INSERT INTO decisions (task_id, step, ts, step_type, chosen, switched, reason,"
                " data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.task_id,
                    decision.step,
                    time.time(),
                    step_type,
                    decision.chosen,
                    int(decision.switched),
                    decision.reason,
                    json.dumps(record, default=str),
                ),
            )
        if decision.chosen is not None and decision.switched and decision.previous is not None:
            old = self.catalog.get(decision.previous).spec
            new = self.catalog.get(decision.chosen).spec
            self.switches += 1
            self.trajectory.emit(
                "switch",
                step=decision.step,
                step_type=step_type,
                **{"from": old.id, "to": new.id},
                from_endpoint=old.endpoint,
                to_endpoint=new.endpoint,
                reason=decision.reason,
                cost=decision.switch_cost or {"seconds": 0.0, "tokens": 0.0},
                expected_gain=decision.expected_gain,
            )
            self.echo(f"  switch: {decision.reason}")
        return decision

    def _call(
        self, step_type: str, request_for: Callable[[str, int], Request], *, work: bool
    ) -> tuple[Completion, str] | None:
        """Route one step and run it, retrying on another worker after a model error."""
        while True:
            probe = request_for("", self._output_budget(None))
            decision = self._decide(step_type, estimate_tokens(self._chars(probe)), work=work)
            if decision.chosen is None:
                self.last_error = self.last_error or decision.reason
                return None
            worker = self.catalog.get(decision.chosen)
            spec = worker.spec
            request = request_for(spec.model, self._output_budget(spec.tier))
            prompt_tokens = estimate_tokens(self._chars(request))
            self.step += 1
            started = self.clock()
            try:
                completion = worker.get_model().complete(request)
            except Exception as exc:  # noqa: BLE001 - any failure takes this worker out
                kind = exc.kind if isinstance(exc, ModelError) else type(exc).__name__
                if kind == "too_large":
                    # Only prompts of this size are out of reach for this worker.
                    known = self.context_caps.get(spec.id, prompt_tokens)
                    self.context_caps[spec.id] = min(known, prompt_tokens)
                    self.overflowed = True
                else:
                    self.failed.add(spec.id)
                self.last_error = f"{spec.id}: {exc}"
                self.trajectory.emit(
                    "model_error",
                    step=self.step,
                    step_type=step_type,
                    worker=spec.id,
                    endpoint=spec.endpoint,
                    kind=kind,
                    message=str(exc)[:500],
                )
                self.echo(f"  model error on {spec.id}: {exc}")
                self.current = None if self.current == spec.id else self.current
                if self.budget.exceeded():
                    return None
                continue
            wall = max(0.0, self.clock() - started)
            self._account(step_type, spec, request, completion, wall, prompt_tokens, work=work)
            self.current = spec.id
            return completion, spec.id

    def _output_budget(self, tier: str | None) -> int:
        if tier is None:
            return DEFAULT_WORK_OUTPUT_TOKENS
        return WORK_OUTPUT_TOKENS.get(tier, DEFAULT_WORK_OUTPUT_TOKENS)

    def _account(
        self,
        step_type: str,
        spec: Any,
        request: Request,
        completion: Completion,
        wall: float,
        prompt_tokens: int,
        *,
        work: bool,
    ) -> None:
        response = completion.response
        message = response.message
        reply_chars = len(json.dumps(message.model_dump(exclude_none=True)))
        fields = usage_row_fields(
            response.usage, request_chars=self._chars(request), response_chars=reply_chars
        )
        gpu = gpu_seconds_of(response, wall, gpu=spec.gpu)
        self.ledger.record(
            LedgerRow(
                task_id=self.task_id,
                step=self.step,
                step_type=step_type,
                endpoint=spec.endpoint,
                model=spec.model,
                wall_seconds=round(wall, 6),
                gpu_seconds=round(gpu, 6),
                data={
                    "worker": spec.id,
                    "attempts": completion.attempts,
                    "finish_reason": response.finish_reason,
                    "losses": [loss.model_dump() for loss in completion.report.losses],
                },
                **fields,
            )
        )
        self.budget.charge(tokens=fields["input_tokens"] + fields["output_tokens"], gpu_seconds=gpu)
        if work:
            self.seen_tokens[spec.id] = prompt_tokens + estimate_tokens(reply_chars)
        if spec.endpoint not in self.endpoints:
            self.endpoints.append(spec.endpoint)
        calls = [c.name for c in message.tool_calls()]
        self.trajectory.emit(
            "step",
            step=self.step,
            step_type=step_type,
            worker=spec.id,
            endpoint=spec.endpoint,
            model=spec.model,
            tool_calls=calls,
            text=message.text_content()[:400],
            finish_reason=response.finish_reason,
            usage={k: v for k, v in fields.items()},
            wall_seconds=round(wall, 3),
            gpu_seconds=round(gpu, 3),
        )
        self.echo(
            f"[{self.step}] {step_type:9} {spec.id}" + (f" -> {', '.join(calls)}" if calls else "")
        )

    # ------------------------------------------------------------------------------------
    # Text steps
    # ------------------------------------------------------------------------------------

    def _text_step(self, step_type: str, prompt: str, user: str, max_output: int) -> str | None:
        def request_for(model: str, _budget: int) -> Request:
            return Request(
                model=model,
                system=[TextPart(text=prompt)],
                messages=[Message.text("user", user)],
                max_output_tokens=max_output,
                sampling=Sampling(temperature=0.2, seed=self.spec.seed),
            )

        self.overflowed = False
        result = self._call(step_type, request_for, work=False)
        if result is None and self.overflowed and len(user) > 2000 and not self._limit():
            # Every worker that could take the step refused the prompt as too long: retry
            # once with the second half of the prompt (usually most of the diff) cut.
            user = user[: len(user) // 2] + "\n[cut to fit the model's context]"
            result = self._call(step_type, request_for, work=False)
        if result is None:
            return None
        completion, _worker = result
        return completion.response.message.text_content().strip()

    def _diff_text(self) -> str:
        diff = self.diff()
        if len(diff) > DIFF_PROMPT_CHARS:
            diff = diff[:DIFF_PROMPT_CHARS] + f"\n[diff cut at {DIFF_PROMPT_CHARS} characters]"
        return diff or "(no changes)"

    # ------------------------------------------------------------------------------------
    # Tools and gates
    # ------------------------------------------------------------------------------------

    def _tool_message(self, call: ToolCallPart, text: str, *, is_error: bool) -> Message:
        return Message(
            role="tool",
            content=[
                ToolResultPart(
                    call_id=call.id,
                    name=call.name,
                    content=[TextPart(text=text)],
                    is_error=is_error,
                )
            ],
        )

    def _execute(self, call: ToolCallPart) -> tuple[ToolOutput, bool]:
        """Run one call; also say whether it repeats the previous call and its result."""
        output = self.toolbox.execute(call.name, call.arguments)
        payload = output.text
        key = (call.name, _canonical_arguments(call.arguments), payload)
        repeated = key == self.last_call
        self.last_call = key
        ref = self.trajectory.blob(payload) if len(payload) > 2000 else None
        self.trajectory.emit(
            "tool",
            step=self.step,
            name=call.name,
            call_id=call.id,
            arguments=call.arguments[:2000],
            is_error=output.is_error,
            output=payload[:2000] if ref is None else payload[:500],
            output_blob=ref,
            changed_files=list(output.changed_files),
            data=output.data,
            repeated=repeated,
        )
        return output, repeated

    def _run_gates(self) -> Any:
        self.gate_runs += 1
        artifacts = self.trajectory.run_dir / "artifacts" / f"gates-{self.gate_runs}"
        verdict = self.gates(
            self.workdir,
            track=self.spec.track,
            base=self.base,
            config=self.config,
            allow_protected=list(self.spec.allow_protected),
            acceptance=list(self.spec.acceptance),
            artifacts=artifacts,
        )
        data = verdict.to_dict()
        self.trajectory.emit("gates", step=self.step, run=self.gate_runs, verdict=data)
        status = "passed" if verdict.passed else f"failed at {verdict.failed_gate}"
        self.echo(f"  gates: {status}")
        return verdict

    # ------------------------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------------------------

    def _limit(self) -> LoopOutcome | None:
        reason = self.budget.exceeded()
        if reason is not None:
            self.trajectory.emit("budget", step=self.step, reason=reason, **self.budget.snapshot())
            return LoopOutcome("stopped", reason, detail=f"{reason.removeprefix('budget_')} budget")
        if self.step >= self.spec.max_steps:
            return LoopOutcome("stopped", "max_steps", detail=f"{self.spec.max_steps} steps")
        return None

    def _no_worker(self) -> LoopOutcome:
        if self.failed:
            return LoopOutcome("failed", "model_error", detail=self.last_error)
        return LoopOutcome("failed", "no_worker", detail=self.last_error)

    def _escalate(self, why: str) -> LoopOutcome | None:
        """Move up the ladder, or say why the run ends here."""
        if not self.ladder.can_escalate():
            return LoopOutcome("failed", "gates_failed", detail=f"{why}; no higher rung")
        if not self.budget.can_escalate():
            return LoopOutcome("stopped", "budget_escalations", detail=why)
        move = self.ladder.escalate(why)
        self.budget.escalate()
        self.trajectory.emit("escalation", step=self.step, **(move or {}))
        self.echo(f"  escalate: {why}")
        return None

    @staticmethod
    def _failed_test(output: ToolOutput) -> bool:
        data = output.data or {}
        code = data.get("exit_code", data.get("returncode"))
        failures = data.get("failed") or data.get("failures") or data.get("errors")
        return bool(output.is_error or (isinstance(code, int) and code != 0) or failures)

    def _next_step(self, calls: Sequence[ToolCallPart], outputs: Sequence[ToolOutput]) -> str:
        last, output = calls[-1].name, outputs[-1]
        if last in {"grep", "repo_map", "search", "lsp"}:
            return "read"
        if last == "read":
            return "read" if output.is_error else "edit"
        if last == "edit":
            return "edit" if output.is_error else "run"
        if last in {"run", "test"}:
            return "triage" if self._failed_test(output) else "run"
        return "navigate"

    def _progress_key(self, call: ToolCallPart, output: ToolOutput) -> str | None:
        """A fact that counts as progress the first time it is seen."""
        if output.is_error and call.name != "test":
            return None
        if call.name == "edit" and output.changed_files:
            return f"edit:{output.text[:2000]}"
        if call.name in {"test", "run"}:
            return f"{call.name}:{output.text[-1500:]}"
        return f"{call.name}:{call.arguments}"

    def run(self) -> LoopOutcome:
        if self.spec.track not in {"general", "science"}:
            return LoopOutcome("failed", "error", detail=f"unknown track {self.spec.track}")
        task_text = self.spec.instruction.strip()
        if self.spec.acceptance:
            task_text += "\n\nAcceptance checks:\n" + "\n".join(
                f"- {cmd}" for cmd in self.spec.acceptance
            )
        limit = self._limit()
        if limit is not None:
            return limit
        plan = self._text_step("plan", PLAN_PROMPT, f"Task: {task_text}\n\n{self.overview}", 400)
        if plan:
            self.plan = plan
        first = f"Task: {task_text}"
        if self.plan:
            first += f"\n\nPlan:\n{self.plan}"
        self.messages = [Message.text("user", first)]

        step_type = "navigate"
        text_only = 0
        failures_on_rung = 0
        review_rounds = 0
        idle = 0
        seen_progress: set[str] = set()
        while True:
            limit = self._limit()
            if limit is not None:
                return limit
            result = self._call(
                step_type,
                lambda model, budget: self._work_request(model, budget),
                work=True,
            )
            if result is None:
                return self._limit() or self._no_worker()
            completion, _worker = result
            message = completion.response.message
            self.messages.append(message.model_copy(update={"role": "assistant"}))
            calls = message.tool_calls()
            finish: ToolCallPart | None = None
            if not calls:
                text_only += 1
                said = message.text_content().strip().strip(".!`*\"' ").lower()
                if text_only < TEXT_ONLY_LIMIT and said not in FINISH_WORDS:
                    self.messages.append(Message.text("user", NUDGE))
                    continue
                self.finish_summary = message.text_content().strip()[:500]
            else:
                text_only = 0
                outputs: list[ToolOutput] = []
                work_calls: list[ToolCallPart] = []
                for call in calls:
                    if call.name in CONTROL_TOOLS:
                        if finish is None:
                            finish = call
                        else:
                            self.messages.append(
                                self._tool_message(
                                    call, "Ignored: finish was already called.", is_error=True
                                )
                            )
                        continue
                    output, repeated = self._execute(call)
                    outputs.append(output)
                    work_calls.append(call)
                    text = f"{output.text}\n\n{REPEAT_HINT}" if repeated else output.text
                    self.messages.append(self._tool_message(call, text, is_error=output.is_error))
                    key = self._progress_key(call, output)
                    if key is not None and key not in seen_progress:
                        seen_progress.add(key)
                        idle = -1
                if finish is not None:
                    try:
                        args = finish.parsed_arguments()
                    except ValueError:
                        args = {}
                    self.finish_summary = str(args.get("summary") or "").strip()[:500]
                elif work_calls:
                    idle += 1
                    step_type = self._next_step(work_calls, outputs)
                    if idle >= STUCK_STEPS:
                        stuck = self._escalate(f"no progress in {STUCK_STEPS} steps")
                        if stuck is not None:
                            return LoopOutcome("failed", "stuck", detail=stuck.detail)
                        idle = 0
                    continue
            # `finish` was called, or the worker kept answering without tools: verify.
            verdict = self._run_gates()
            if finish is not None:
                text = (
                    "Verification passed."
                    if verdict.passed
                    else f"Not finished: verification failed.\n{verdict.report}\n"
                    "Fix the problem, then call finish again."
                )
                self.messages.append(self._tool_message(finish, text, is_error=not verdict.passed))
            elif not verdict.passed:
                self.messages.append(
                    Message.text(
                        "user",
                        f"Verification failed.\n{verdict.report}\n"
                        "Fix the problem with the tools, then call finish.",
                    )
                )
            text_only = 0
            if not verdict.passed:
                failures_on_rung += 1
                step_type = "triage"
                limit = self._limit()
                if limit is not None:
                    return limit
                if failures_on_rung >= self.spec.gate_attempts:
                    gate = verdict.failed_gate or "a"
                    ended = self._escalate(
                        f"{gate} gate failed {failures_on_rung} times on rung {self.ladder.level}"
                    )
                    if ended is not None:
                        ended.verdict = verdict.to_dict()
                        return ended
                    failures_on_rung = 0
                continue
            if self.spec.review and review_rounds < REVIEW_ROUNDS:
                review_rounds += 1
                review = self._review()
                if review is not None and not review["approved"]:
                    self.messages.append(
                        Message.text(
                            "user",
                            "A reviewer found issues:\n"
                            + review["text"]
                            + "\nAddress them, then call finish again.",
                        )
                    )
                    step_type = "edit"
                    continue
            summary = self._summary(verdict)
            return LoopOutcome(
                "done", None, summary=summary, verdict=verdict.to_dict(), review=self.last_review
            )

    def _review(self) -> dict[str, Any] | None:
        limit = self._limit()
        if limit is not None:
            return None
        text = self._text_step(
            "review",
            REVIEW_PROMPT,
            f"Task: {self.spec.instruction.strip()}\n\nDiff:\n{self._diff_text()}",
            400,
        )
        if text is None:
            return None
        head = text.lstrip("*# ").upper()
        approved = not head.startswith("ISSUES")
        review = {
            "approved": approved,
            "text": text[:2000],
            "parsed": head.startswith(("APPROVE", "ISSUES")),
        }
        self.last_review = review
        self.trajectory.emit("review", step=self.step, **review)
        return review

    def _summary(self, verdict: Any) -> str:
        fallback = self.finish_summary or "Task completed; verification passed."
        if self._limit() is not None:
            return fallback
        gates = ", ".join(
            f"{r.name}: {r.status}"
            for r in getattr(verdict, "results", [])
            if r.status != "skipped"
        )
        text = self._text_step(
            "summarize",
            SUMMARY_PROMPT,
            f"Task: {self.spec.instruction.strip()}\n\nChecks: {gates or 'none'}\n\n"
            f"Diff:\n{self._diff_text()}",
            200,
        )
        return text or fallback
