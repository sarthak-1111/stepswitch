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

Token and cache optimization (SPEC §13, ADR 0024) is on unless the task runs the Phase 2
baseline (`optimize` false: `ssw run --no-optimize`, `ssw bench run --baseline`):

* TK1: the system text is the stable prefix (role prompt and track notes, repository map,
  glossary); everything volatile is appended as messages.
* TK3: a read of an unchanged range whose earlier result is still visible returns a stub.
* TK4: a tool output longer than `distill_tokens` is distilled (key lines verbatim, a T0
  summary when a summarizer is available); the raw output stays readable as `@output/<n>`.
* TK6: superseded results are stubbed in batches, and at 60% of the transcript budget the
  older turns are replaced by a HANDOFF brief, keeping the latest exchange (a rolling summary).
* TK7 and SPEC §7.2: a switch or failover whose worker would re-read more uncached tokens than
  the threshold, and the bench's relay, hand the task over with a HANDOFF brief (SPEC §9):
  written by the T0 summarizer from the work log, fitted to 1,500 tokens of the receiving
  model's tokenizer, recorded in `handoffs` and the run directory's `handoffs.jsonl`.
* TK8: the review step answers one JSON object under a JSON Schema constraint (text that is not
  valid JSON is read the Phase 2 way).
* TK10: output caps and reasoning effort per step type; effort goes only to reasoning workers.
* TK11: optional prose compression of documentation reads.
* Cached tokens per server come from the prefix registry (`router.cache`), calibrated by the
  cached-token counts the servers report.

Every model call goes through `stepswitch.compat` (working rule 2); briefs and summaries are
written by a local worker and contain repository-relative paths only.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import re
import sys
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
    ResponseFormat,
    Sampling,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
)
from stepswitch.context import dedup, distill, handoff, prune
from stepswitch.context import tokens as token_counts
from stepswitch.context.compress import maybe_compress
from stepswitch.context.prefix import StablePrefix
from stepswitch.context.settings import ContextSettings
from stepswitch.core.budget import BudgetTracker
from stepswitch.core.task import TaskSpec
from stepswitch.ledger import Ledger, LedgerRow, estimate_tokens, gpu_seconds_of, usage_row_fields
from stepswitch.router import cost
from stepswitch.router.budgets import RoleBudget, role_budget
from stepswitch.router.cache import Chain, PrefixRegistry, chain_of
from stepswitch.router.escalation import Ladder
from stepswitch.router.features import TaskFeatures
from stepswitch.router.router import Decision, RouterState, route
from stepswitch.router.rules import Rule
from stepswitch.trajectory.record import Trajectory
from stepswitch.verify import review as review_module
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.tools import CONTROL_TOOLS, TOOL_ALIASES, TOOL_NAMES, ToolOutput

__all__ = ["CONTROL_TOOLS", "TOOL_NAMES", "AgentLoop", "LoopOutcome"]

# Consecutive work steps without progress (no new file read, search, edit or test outcome).
STUCK_STEPS = 8
# Consecutive text-only answers in work steps before the answer counts as `finish`.
TEXT_ONLY_LIMIT = 2
REVIEW_ROUNDS = 1
DIFF_PROMPT_CHARS = 6000
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
# The work step of the bench's relay (`ssw bench run --relay`) at which the task is handed
# from the first worker to the second with a HANDOFF brief.
RELAY_STEP = 6
# The size of a brief-writing call, for the router's estimate of the time a handoff takes.
WRITER_INPUT_TOKENS = 2500
WRITER_OUTPUT_TOKENS = 400
DIGEST_CHARS = 8000
WORK_LOG_ITEMS = 40
PATH_KEYS = ("path", "file", "filename", "file_path", "filepath")

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

# Optimized runs: every work step re-sends the plan, so it is kept to one line per step.
PLAN_PROMPT_SHORT = """\
You plan the work of a coding agent.
Step: plan
Write a short numbered plan for the task below: at most five steps, one line each. Name the
files to inspect or change when you can tell. Do not write code."""
PLAN_CHARS = 1200

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

HANDOFF_INTRO = """\
You take over this coding task from another worker. Its state is in the HANDOFF brief below.
Continue from it: read the files you need before you edit them (the brief shows no line
anchors), run the tests, and call finish when the task is done."""

COMPACT_INTRO = """\
The earlier part of this task's transcript was replaced by the HANDOFF brief below to keep
the context small; the latest exchange follows it. Read a file again before you edit it if
its anchors are not shown."""


def _canonical_arguments(arguments: str) -> str:
    """Arguments as sorted JSON, so the same call with reordered keys compares equal."""
    try:
        return json.dumps(json.loads(arguments), sort_keys=True)
    except ValueError:
        return arguments.strip()


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _endpoint_of(model: Any) -> Any:
    """The compat `Endpoint` behind a step model (a client, or a protocol wrapper of one)."""
    for _ in range(3):
        endpoint = getattr(model, "endpoint", None)
        if endpoint is not None:
            return endpoint
        model = getattr(model, "client", None)
        if model is None:
            return None
    return None


def path_pattern(path: str) -> re.Pattern[str]:
    """`path` with either separator, case-insensitive on Windows."""
    text = path.rstrip("\\/")
    lead = r"[\\/]" if text[:1] in "\\/" else ""
    parts = [re.escape(part) for part in re.split(r"[\\/]+", text) if part]
    flags = re.IGNORECASE if sys.platform == "win32" else 0
    return re.compile(lead + r"[\\/]+".join(parts), flags)


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
    # Phase 5 (ADR 0024). `optimize` false is the Phase 2 baseline.
    optimize: bool = True
    settings: ContextSettings = field(default_factory=ContextSettings)
    glossary: str = ""
    constraints: Sequence[str] = ()
    golden: Sequence[str] = ()
    # The T0 worker that writes briefs and output summaries; None writes briefs from facts.
    summarizer: Worker | None = None
    models_dir: Path | None = None
    egress: Any = None
    compressor: Any = None
    # The bench's relay: (first worker, second worker), handed over at `relay_step`.
    relay: tuple[str, str] | None = None
    relay_step: int = RELAY_STEP
    handoff_recovery: float = cost.DEFAULT_HANDOFF_RECOVERY
    base_rates: dict[str, float] = field(default_factory=dict)
    # Absolute paths replaced in briefs and digests: (path, replacement), longest first.
    scrub_paths: Sequence[tuple[str, str]] = ()
    # Best-of-N (SPEC §8.3, ADR 0025): the candidate's index and model families. Its steps go
    # only to workers of those families; its review goes to another family (SPEC §8.5).
    candidate: int | None = None
    families: frozenset[str] | None = None
    # The learning router's sampled task-level success per worker (SPEC §10, ADR 0026).
    learned: dict[str, float] = field(default_factory=dict)

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
    review_result: review_module.Review | None = None
    # Families of the workers that took work steps: the change's authors.
    work_families: set[str] = field(default_factory=set)
    # Work steps per worker id, and the tokens and model time this agent spent (all calls):
    # what the learning router credits a task's outcome to.
    work_steps: dict[str, int] = field(default_factory=dict)
    spent_tokens: int = 0
    spent_seconds: float = 0.0
    context_caps: dict[str, int] = field(default_factory=dict)
    overflowed: bool = False
    last_call: tuple[str, str, str] | None = None  # name, arguments, result of the last call
    last_worker: str | None = None
    failovers: int = 0
    compactions: int = 0
    relayed: bool = False
    registry: PrefixRegistry = field(default_factory=PrefixRegistry)
    records: list[prune.ResultRecord] = field(default_factory=list)
    read_cache: dedup.ReadCache = field(default_factory=dedup.ReadCache)
    outputs: distill.OutputStore = field(default_factory=distill.OutputStore)
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    work_log: list[str] = field(default_factory=list)
    file_notes: dict[str, handoff.FileNote] = field(default_factory=dict)
    failing: list[handoff.Failure] = field(default_factory=list)
    # The project's tests passed after the latest edit that changed a file (and there was one).
    tested_after_edit: bool = False
    edits_made: bool = False
    stats: dict[str, int] = field(
        default_factory=lambda: {
            "pruned": 0,
            "pruned_tokens": 0,
            "dedup": 0,
            "distilled": 0,
            "compressed": 0,
        }
    )

    def __post_init__(self) -> None:
        for spec in self.catalog.specs():
            self.registry.seed(spec.id, spec.cache_ratio)
        self._scrubbers = [
            (path_pattern(path), replacement)
            for path, replacement in sorted(self.scrub_paths, key=lambda p: -len(p[0]))
            if path.strip("\\/")
        ]

    # ------------------------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------------------------

    def _system_prompt(self) -> str:
        glossary = f"Domain glossary:\n{self.glossary}" if self.glossary else ""
        if not self.optimize:
            # Phase 2: role prompt, track notes with the glossary, repository overview.
            notes = "\n\n".join(part for part in (self.track_notes, glossary) if part)
            return "\n\n".join(part for part in (WORK_PROMPT, notes, self.overview) if part)
        system = "\n\n".join(part for part in (WORK_PROMPT, self.track_notes) if part)
        return StablePrefix(system, repo_map=self.overview, glossary=glossary).text()

    def _budget(self, step_type: str, spec: WorkerSpec | None) -> RoleBudget:
        tier = spec.tier if spec is not None else "t1"
        return role_budget(step_type, tier, self.config, optimize=self.optimize)

    def _effort(self, budget: RoleBudget, spec: WorkerSpec | None) -> Any:
        """The step's reasoning effort, for workers that reason (TK10)."""
        if not self.optimize or spec is None or not spec.reasoning:
            return None
        return budget.effort

    def _work_request(self, step_type: str, spec: WorkerSpec | None) -> Request:
        budget = self._budget(step_type, spec)
        return Request(
            model=spec.model if spec is not None else "",
            system=[TextPart(text=self._system_prompt())],
            messages=list(self.messages),
            tools=self.toolbox.specs(),
            max_output_tokens=budget.max_output_tokens,
            effort=self._effort(budget, spec),
            sampling=Sampling(temperature=0.2, seed=self.spec.seed),
        )

    @staticmethod
    def _chars(request: Request) -> int:
        size = len(request.system_text())
        size += sum(len(json.dumps(m.model_dump(exclude_none=True))) for m in request.messages)
        size += sum(len(json.dumps(t.model_dump(exclude_none=True))) for t in request.tools)
        return size

    def _prompt_tokens(self, request: Request) -> int:
        return estimate_tokens(self._chars(request))

    # ------------------------------------------------------------------------------------
    # Routing and model calls
    # ------------------------------------------------------------------------------------

    def _relay_pin(self, work: bool) -> tuple[str | None, int]:
        """The worker this step is pinned to and the handoff threshold for it."""
        threshold = self.settings.handoff_threshold
        if self.relay is None:
            return self.spec.worker, threshold
        first, second = self.relay
        if self.relayed:
            return second, threshold
        if work and self.step + 1 >= self.relay_step:
            return second, 0  # the relay always hands over with a brief
        return first, threshold

    def _handoff_seconds(self) -> float:
        if self.summarizer is None or not self.optimize:
            return 0.0
        return cost.estimate(
            self.summarizer.spec,
            input_tokens=WRITER_INPUT_TOKENS,
            output_tokens=WRITER_OUTPUT_TOKENS,
            uncached_tokens=WRITER_INPUT_TOKENS,
        ).seconds

    def _seen(self, probe: Request, prompt_tokens: int) -> tuple[dict[str, int], int]:
        """Tokens of the prompt each server holds, and the stable prefix's share (estimated
        tokens), from the prefix registry."""
        chain = chain_of(probe)
        if not chain.tokens:
            return {}, 0
        seen: dict[str, int] = {}
        for spec in self.catalog.specs():
            cached = self.registry.cached_tokens(spec.id, chain)
            if cached:
                seen[spec.id] = min(prompt_tokens, round(prompt_tokens * cached / chain.tokens))
        return seen, round(prompt_tokens * chain.prefix_tokens / chain.tokens)

    def _decide(self, step_type: str, probe: Request, *, work: bool) -> Decision:
        prompt_tokens = self._prompt_tokens(probe)
        seen: dict[str, int] = {}
        prefix_tokens = 0
        if work and self.optimize:
            seen, prefix_tokens = self._seen(probe, prompt_tokens)
        elif work:
            seen = dict(self.seen_tokens)
        pin, threshold = self._relay_pin(work)
        state = RouterState(
            step=self.step + 1,
            current=self.current,
            level=self.ladder.level,
            transcript_tokens=prompt_tokens,
            output_tokens=EXPECTED_OUTPUT_TOKENS.get(step_type, DEFAULT_EXPECTED_OUTPUT),
            seen_tokens=seen,
            prefix_tokens=prefix_tokens,
            handoff_seconds=self._handoff_seconds(),
            handoff_recovery=self.handoff_recovery,
            handoff_threshold=threshold,
            handoff_allowed=self.optimize and work,
            track=self.spec.track,
            offline=self.offline,
            allow_remote=self.allow_remote,
            worker_pin=pin,
            failed=frozenset(self.failed),
            context_caps=dict(self.context_caps),
            base_rates=dict(self.base_rates) if self.optimize else {},
            families=self.families,
            avoid_families=(
                frozenset(self._author_families()) if step_type == "review" else frozenset()
            ),
            learned=dict(self.learned),
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
                handoff=decision.handoff,
            )
            self.echo(f"  switch: {decision.reason}")
        return decision

    def _call(
        self, step_type: str, request_for: Callable[[WorkerSpec | None], Request], *, work: bool
    ) -> tuple[Completion, str] | None:
        """Route one step and run it, retrying on another worker after a model error."""
        while True:
            probe = request_for(None)
            decision = self._decide(step_type, probe, work=work)
            if decision.chosen is None:
                self.last_error = self.last_error or decision.reason
                return None
            worker = self.catalog.get(decision.chosen)
            spec = worker.spec
            relay_now = (
                work and self.relay is not None and not self.relayed and spec.id == self.relay[1]
            )
            if relay_now:
                self.relayed = True
            if decision.handoff and work and self.optimize:
                reason = "relay" if relay_now else "switch" if decision.switched else "failover"
                self._handoff(decision, spec, reason, self._prompt_tokens(probe))
            request = request_for(spec)
            prompt_tokens = self._prompt_tokens(request)
            chain = chain_of(request) if self.optimize else None
            predicted = self.registry.predicted(spec.id, chain) if chain is not None else 0
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
                self.failovers += 1
                self.registry.forget(spec.id)
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
            if chain is not None:
                self._observe_cache(spec.id, chain, predicted, completion.response.usage)
                if not work:
                    self.registry.record(spec.id, chain)
            self.current = spec.id
            self.last_worker = spec.id
            if work:
                self.work_families.add(spec.family_key)
                self.work_steps[spec.id] = self.work_steps.get(spec.id, 0) + 1
            return completion, spec.id

    def _observe_cache(
        self, worker_id: str, chain: Chain, predicted: int, usage: Usage | None
    ) -> None:
        """Calibrate the registry: the cached share the server reported vs the predicted one."""
        if usage is None or not usage.input_tokens or not chain.tokens:
            return
        expected = round(usage.input_tokens * predicted / chain.tokens)
        self.registry.observe(worker_id, expected, usage.cached_input_tokens)

    def _remember_prefix(self, worker_id: str) -> None:
        """`worker_id`'s server now holds the transcript up to its reply."""
        if self.optimize:
            self.registry.record(worker_id, chain_of(self._work_request("navigate", None)))

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
        self.spent_tokens += fields["input_tokens"] + fields["output_tokens"]
        self.spent_seconds += wall
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

    def _account_aux(
        self,
        purpose: str,
        spec: WorkerSpec,
        completion: Completion | None,
        wall: float,
        request_chars: int,
    ) -> None:
        """Record a call that serves the agent rather than taking a step: a brief or summary."""
        if completion is None:
            return
        response = completion.response
        fields = usage_row_fields(
            response.usage,
            request_chars=request_chars,
            response_chars=len(response.message.text_content()),
        )
        gpu = gpu_seconds_of(response, wall, gpu=spec.gpu)
        self.ledger.record(
            LedgerRow(
                task_id=self.task_id,
                step=self.step,
                step_type="summarize",
                endpoint=spec.endpoint,
                model=spec.model,
                wall_seconds=round(wall, 6),
                gpu_seconds=round(gpu, 6),
                data={
                    "worker": spec.id,
                    "purpose": purpose,
                    "attempts": completion.attempts,
                    "finish_reason": response.finish_reason,
                },
                **fields,
            )
        )
        self.budget.charge(tokens=fields["input_tokens"] + fields["output_tokens"], gpu_seconds=gpu)
        self.spent_tokens += fields["input_tokens"] + fields["output_tokens"]
        self.spent_seconds += wall
        if spec.endpoint not in self.endpoints:
            self.endpoints.append(spec.endpoint)
        # The summarizer's server now holds this prompt, not a work transcript.
        self.registry.forget(spec.id)
        self.trajectory.emit(
            "aux",
            step=self.step,
            purpose=purpose,
            worker=spec.id,
            endpoint=spec.endpoint,
            model=spec.model,
            usage=dict(fields),
            wall_seconds=round(wall, 3),
        )

    # ------------------------------------------------------------------------------------
    # Text steps
    # ------------------------------------------------------------------------------------

    def _text_step(
        self,
        step_type: str,
        prompt: str,
        user: str,
        response_format: ResponseFormat | None = None,
    ) -> str | None:
        def request_for(spec: WorkerSpec | None) -> Request:
            budget = self._budget(step_type, spec)
            return Request(
                model=spec.model if spec is not None else "",
                system=[TextPart(text=prompt)],
                messages=[Message.text("user", user)],
                max_output_tokens=budget.max_output_tokens,
                effort=self._effort(budget, spec),
                sampling=Sampling(temperature=0.2, seed=self.spec.seed),
                response_format=response_format,
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
    # HANDOFF briefs (TK7) and the rolling summary (TK6)
    # ------------------------------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        """`text` with the machine's absolute paths replaced (briefs reach other models and
        bench records; working rule 5)."""
        for pattern, replacement in self._scrubbers:
            text = pattern.sub(replacement, text)
        return text

    def _facts(self, from_worker: str) -> handoff.HandoffFacts:
        remaining = self.budget.remaining()
        tokens_left = remaining.get("tokens")
        seconds_left = remaining.get("wall_seconds")
        decisions = (
            [{"what": "Follow the plan", "why": self._scrub(self.plan)}] if self.plan else []
        )
        commands = [cmd.replace("{python}", "python") for cmd in self.spec.acceptance]
        if not commands and getattr(self.toolbox, "test_command", None):
            commands = ["the `test` tool runs the project's tests"]
        return handoff.HandoffFacts(
            task_id=self.task_id,
            goal=self._scrub(self.spec.instruction.strip()),
            track=self.spec.track,
            constraints=[self._scrub(item) for item in self.constraints],
            commands=commands,
            golden=list(self.golden),
            done=list(self.work_log),
            decisions=decisions,
            files=list(reversed(self.file_notes.values())),
            failing=list(self.failing),
            tokens_left=int(tokens_left) if tokens_left is not None else None,
            seconds_left=float(seconds_left) if seconds_left is not None else None,
            from_worker=from_worker,
            step=self.step,
        )

    def _digest(self) -> str:
        """What the summarizer reads to write the brief's state: the work log and notes."""
        head = [f"Task: {self.spec.instruction.strip()[:3000]}"]
        if self.plan:
            head.append(f"Plan:\n{self.plan[:1500]}")
        said = [
            m.text_content().strip()
            for m in self.messages
            if m.role == "assistant" and m.text_content().strip()
        ]
        tail = []
        if said:
            tail.append(
                "What the worker wrote (latest last):\n"
                + "\n".join(f"- {s[:300]}" for s in said[-6:])
            )
        if self.failing:
            tail.append(
                "Failing checks:\n"
                + "\n\n".join(f"{f.check}:\n{f.excerpt[-1500:]}" for f in self.failing[-2:])
            )
        log = self.work_log[-WORK_LOG_ITEMS:]
        while True:
            body = ["Work log:\n" + "\n".join(f"- {item}" for item in log)] if log else []
            text = self._scrub("\n\n".join(head + body + tail))
            if len(text) <= DIGEST_CHARS or not log:
                return text[:DIGEST_CHARS]
            log = log[len(log) // 4 + 1 :]

    def _receiver(self, spec: WorkerSpec) -> token_counts.Receiver:
        try:
            endpoint = _endpoint_of(self.catalog.get(spec.id).get_model())
        except Exception:  # noqa: BLE001 - a receiver without endpoint facts still has a name
            endpoint = None
        return token_counts.Receiver(
            model=spec.model,
            registry_id=spec.registry_id,
            tokenizer=spec.tokenizer,
            engine=getattr(endpoint, "engine", None),
            base_url=getattr(endpoint, "base_url", None),
            headers=dict(getattr(endpoint, "headers", None) or {}),
        )

    def _ask_writer(self) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """The brief's state from the T0 summarizer (TK8: a JSON-Schema-constrained answer)."""
        assert self.summarizer is not None
        digest = self._digest()
        started = self.clock()
        try:
            state, details = handoff.write_state(
                self.summarizer.get_model(), digest, seed=self.spec.seed
            )
        except Exception as exc:  # noqa: BLE001 - the facts still make a brief
            state, details = None, {"writer": "fallback", "reason": f"{type(exc).__name__}: {exc}"}
        wall = max(0.0, self.clock() - started)
        completion = details.pop("completion", None)
        self._account_aux(
            "handoff",
            self.summarizer.spec,
            completion,
            wall,
            len(digest) + len(handoff.WRITER_PROMPT),
        )
        details["worker"] = self.summarizer.spec.id
        if "reason" in details:
            details["reason"] = self._scrub(str(details["reason"]))[:300]
        return state, details

    def _write_brief(
        self, receiver: WorkerSpec, from_worker: str
    ) -> tuple[handoff.Fitted, dict[str, Any], token_counts.Receiver] | None:
        facts = self._facts(from_worker)
        writer: dict[str, Any] = {"writer": "facts"}
        if self.summarizer is not None:
            state, writer = self._ask_writer()
            if state is not None:
                facts.done = [self._scrub(str(s)) for s in state.get("done") or []] or facts.done
                facts.decisions = facts.decisions[:1] + [
                    {"what": self._scrub(str(d["what"])), "why": self._scrub(str(d["why"]))}
                    for d in state.get("decisions") or []
                ]
                facts.open_questions = [
                    self._scrub(str(q)) for q in state.get("open_questions") or []
                ]
        target = self._receiver(receiver)
        counter = token_counts.resolve(target, models_dir=self.models_dir, egress=self.egress)
        try:
            fitted = handoff.fit(handoff.build(facts), counter, limit=handoff.MAX_TOKENS)
        except handoff.HandoffError as exc:
            self.trajectory.emit("handoff_error", step=self.step, message=str(exc)[:500])
            return None
        return fitted, writer, target

    def _record_brief(
        self,
        fitted: handoff.Fitted,
        writer: dict[str, Any],
        target: token_counts.Receiver,
        *,
        reason: str,
        to_worker: str,
        from_worker: str,
        transcript_tokens: int,
        decision: Decision | None = None,
    ) -> None:
        priors = {c.worker: c.prior for c in decision.candidates} if decision is not None else {}
        record: dict[str, Any] = {
            "task_id": self.task_id,
            "step": self.step,
            "reason": reason,
            "from_worker": from_worker,
            "to_worker": to_worker,
            "receiver": target.as_dict(),
            "tokenizer": fitted.count.tokenizer,
            "exact": fitted.count.exact,
            "tokens": fitted.count.tokens,
            "limit": handoff.MAX_TOKENS,
            "valid": fitted.valid,
            "errors": fitted.errors[:5],
            "trims": fitted.trims,
            "writer": writer,
            "transcript_tokens": transcript_tokens,
            "p_current": priors.get(from_worker),
            "p_new": priors.get(to_worker),
            "brief": fitted.brief,
            "markdown": fitted.markdown,
        }
        if self.candidate is not None:
            record["candidate"] = self.candidate
        self.handoffs.append(record)
        self.trajectory.emit(
            "handoff",
            step=self.step,
            **{k: v for k, v in record.items() if k not in {"step", "brief", "markdown"}},
        )
        self.trajectory.write_file(
            self.handoffs_file,
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in self.handoffs),
        )
        self.echo(
            f"  handoff ({reason}): {fitted.count.tokens} tokens"
            f" ({fitted.count.tokenizer}) -> {to_worker}"
        )

    def _reset_results(self) -> None:
        self.records = []
        self.read_cache.reset()
        self.last_call = None

    def _handoff(
        self, decision: Decision, spec: WorkerSpec, reason: str, transcript_tokens: int
    ) -> bool:
        """Replace the transcript with a HANDOFF brief for `spec` (SPEC §7.2, §9)."""
        from_worker = decision.previous or self.last_worker or "unknown"
        written = self._write_brief(spec, from_worker)
        if written is None:
            return False
        fitted, writer, target = written
        self.messages = [Message.text("user", f"{HANDOFF_INTRO}\n\n{fitted.markdown}")]
        self._reset_results()
        self._record_brief(
            fitted,
            writer,
            target,
            reason=reason,
            to_worker=spec.id,
            from_worker=from_worker,
            transcript_tokens=transcript_tokens,
            decision=decision,
        )
        return True

    def _maybe_compact(self) -> None:
        """At `compact_at` of the transcript budget, replace the older turns by a brief and keep
        the latest exchange (TK6)."""
        if self.current is None:
            return
        spec = self.catalog.get(self.current).spec
        tokens = self._prompt_tokens(self._work_request("navigate", None))
        window = self.settings.window_for(spec.context)
        if not prune.should_compact(tokens, window, at=self.settings.compact_at):
            return
        tail = max(
            (i for i, message in enumerate(self.messages) if message.role == "assistant"),
            default=None,
        )
        if tail is None or tail < 2:
            return
        written = self._write_brief(spec, spec.id)
        if written is None:
            return
        fitted, writer, target = written
        kept = self.messages[tail:]
        self.messages = [Message.text("user", f"{COMPACT_INTRO}\n\n{fitted.markdown}"), *kept]
        shift = tail - 1
        self.records = [
            dataclasses.replace(record, index=record.index - shift)
            for record in self.records
            if record.index >= tail
        ]
        self.read_cache.reset()
        self.compactions += 1
        self._record_brief(
            fitted,
            writer,
            target,
            reason="compaction",
            to_worker=spec.id,
            from_worker=spec.id,
            transcript_tokens=tokens,
        )

    def _prune(self) -> None:
        """Stub superseded tool results, in batches worth a cache miss (TK6)."""
        found = prune.plan(
            self.records,
            min_tokens=self.settings.prune_min_tokens,
            keep_recent=self.settings.keep_recent,
        )
        if not found:
            return
        saved = 0
        for record, text in found:
            message = self.messages[record.index]
            parts = [
                part.model_copy(update={"content": [TextPart(text=text)]})
                if isinstance(part, ToolResultPart)
                else part
                for part in message.content
            ]
            self.messages[record.index] = message.model_copy(update={"content": parts})
            record.pruned = True
            saved += max(0, record.tokens - estimate_tokens(len(text)))
        self.stats["pruned"] += len(found)
        self.stats["pruned_tokens"] += saved
        self.trajectory.emit(
            "prune",
            step=self.step,
            results=len(found),
            tokens=saved,
            indices=[record.index for record, _ in found],
        )

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

    def _stored_output(self, call: ToolCallPart) -> ToolOutput | None:
        """A `read` of `@output/<n>`, answered from the raw outputs kept by TK4."""
        name = call.name.strip().lower()
        if TOOL_ALIASES.get(name, name) != "read":
            return None
        try:
            args = json.loads(call.arguments) if call.arguments.strip() else {}
        except ValueError:
            return None
        if not isinstance(args, dict):
            return None
        path = next((str(args[k]) for k in PATH_KEYS if isinstance(args.get(k), str)), "")
        number = self.outputs.parse(path.strip())
        if number is None:
            return None
        limit = int(getattr(self.toolbox, "max_output_chars", 6000))
        try:
            text = self.outputs.show(
                number, _int_or_none(args.get("start")), _int_or_none(args.get("end")), limit
            )
        except KeyError as exc:
            return ToolOutput(str(exc.args[0]), is_error=True, data={"tool": "read"})
        return ToolOutput(text, data={"tool": "read", "output": number})

    def _execute(self, call: ToolCallPart) -> tuple[ToolOutput, bool]:
        """Run one call; also say whether it repeats the previous call and its result."""
        output = self._stored_output(call) if self.optimize else None
        if output is None:
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

    def _visible(self, index: int) -> bool:
        return any(r.index == index and not r.pruned for r in self.records)

    def _summarize_output(self, text: str) -> str | None:
        if self.summarizer is None:
            return None
        started = self.clock()
        try:
            summary, completion = distill.summarize(self.summarizer.get_model(), text)
        except Exception:  # noqa: BLE001 - the key lines still stand without a summary
            return None
        self._account_aux(
            "distill",
            self.summarizer.spec,
            completion,
            max(0.0, self.clock() - started),
            len(distill.summary_input(text)),
        )
        return summary

    def _present(self, call: ToolCallPart, output: ToolOutput) -> tuple[str, str | None]:
        """The text the transcript gets for `output`, and which technique shaped it."""
        if not self.optimize:
            return output.text, None
        tool = str(output.data.get("tool") or call.name)
        if tool == "read" and not output.is_error and "output" not in output.data:
            large = estimate_tokens(len(output.text)) >= self.settings.dedup_min_tokens
            earlier = self.read_cache.lookup(output.data, self._visible) if large else None
            if earlier is not None:
                self.read_cache.mark_stubbed(output.data)
                self.stats["dedup"] += 1
                return dedup.stub(output.data, earlier), "dedup"
            compression = self.settings.compression
            if compression.enabled and self.compressor is not None:
                path = str(output.data.get("path") or "")
                text, info = maybe_compress(output.text, path, compression, self.compressor)
                if info is not None and info.get("compressed"):
                    self.stats["compressed"] += 1
                    return text, "compressed"
            return output.text, None
        raw = output.raw
        if (
            tool in distill.DISTILL_TOOLS
            and raw is not None
            and distill.too_long(raw, self.settings.distill_tokens)
        ):
            number = self.outputs.put(raw)
            text = distill.distill(
                raw, tool=tool, number=number, summary=self._summarize_output(raw)
            )
            self.stats["distilled"] += 1
            return text, "distilled"
        return output.text, None

    def _add_result(
        self, call: ToolCallPart, output: ToolOutput, text: str, index: int, shaped: str | None
    ) -> None:
        """Track a tool result for pruning (TK6) and read dedup (TK3)."""
        if not self.optimize:
            return
        data = output.data
        tool = str(data.get("tool") or call.name)
        record = prune.ResultRecord(
            index=index,
            step=self.step,
            # A dedup stub points to an earlier read; it must never supersede that read.
            tool="dedup" if shaped == "dedup" else tool,
            arguments=_canonical_arguments(call.arguments),
            tokens=estimate_tokens(len(text)),
            path=str(data["path"]) if isinstance(data.get("path"), str) else None,
            start=_int_or_none(data.get("start")),
            end=_int_or_none(data.get("end")),
            total_lines=_int_or_none(data.get("total_lines")),
            is_error=output.is_error,
        )
        self.records.append(record)
        if tool == "read" and shaped is None and not output.is_error:
            self.read_cache.remember(data, index, self.step)
        if shaped is not None:
            self.trajectory.emit(
                "context",
                step=self.step,
                kind=shaped,
                tool=tool,
                chars=len(output.raw or output.text),
                kept=len(text),
            )

    def _note_file(self, path: str, what: str) -> None:
        note = self.file_notes.pop(path, None) or handoff.FileNote(path)
        if what not in note.why.split(", "):
            note.why = ", ".join(part for part in (note.why, what) if part)
        self.file_notes[path] = note  # most recent last

    def _log_tool(self, call: ToolCallPart, output: ToolOutput) -> None:
        """One line of the work log, and the files and failures a brief carries (SPEC §9)."""
        data = output.data
        tool = str(data.get("tool") or call.name)
        path = data.get("path") if isinstance(data.get("path"), str) else None
        first = output.text.strip().splitlines()[0][:200] if output.text.strip() else ""
        if output.is_error:
            entry = f"{tool} failed: {first}"
        elif tool == "read" and "output" in data:
            entry = f"read the stored output @output/{data['output']}"
        elif tool == "read" and data.get("directory"):
            entry = f"listed {path}"
        elif tool == "read":
            entry = (
                f"read {path} lines {data.get('start')}-{data.get('end')}"
                f" of {data.get('total_lines')}"
            )
            self._note_file(str(path), "read")
        elif tool == "edit":
            if data.get("changed"):
                entry = f"edited {path} (+{data.get('added', 0)} -{data.get('removed', 0)})"
                self._note_file(str(path), "edited")
                self.tested_after_edit = False
                self.edits_made = True
            else:
                entry = f"edit of {path} changed nothing"
        elif tool == "test":
            counts = data.get("counts") or {}
            shown = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
            entry = ("tests passed" if data.get("ok") else "tests failed") + (
                f" ({shown})" if shown else ""
            )
            failing = [str(item) for item in data.get("failing") or []][:5]
            if failing:
                entry += ": " + ", ".join(failing)
        elif tool == "run":
            entry = f"ran `{str(data.get('command'))[:120]}`: exit {data.get('exit_code')}"
        else:
            entry = f"{tool} {_canonical_arguments(call.arguments)[:160]}"
        self.work_log.append(f"step {self.step}: {self._scrub(entry)}")
        excerpt = self._scrub(output.text[-handoff.EXCERPT_CHARS :])
        if tool == "test":
            if data.get("ok"):
                self.failing = []
                self.tested_after_edit = self.edits_made
            else:
                self.tested_after_edit = False
                self.failing.append(handoff.Failure("tests", excerpt))
        elif tool == "run" and isinstance(data.get("exit_code"), int) and data["exit_code"] != 0:
            command = self._scrub(str(data.get("command")))[:80]
            self.failing.append(handoff.Failure(f"run {command}", excerpt))

    def _log_gates(self, verdict: Any) -> None:
        if verdict.passed:
            self.work_log.append(f"step {self.step}: verification passed")
            self.failing = []
            return
        gate = verdict.failed_gate or "a"
        self.work_log.append(f"step {self.step}: verification failed at the {gate} gate")
        report = self._scrub(str(verdict.report or ""))
        self.failing.append(handoff.Failure(f"{gate} gate", report[-handoff.EXCERPT_CHARS :]))

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
        self._log_gates(verdict)
        return verdict

    # ------------------------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------------------------

    def _limit(self) -> LoopOutcome | None:
        reason = self.budget.exceeded()
        if reason == "cancelled":
            self.trajectory.emit("cancelled", step=self.step)
            return LoopOutcome("stopped", reason, detail="cancelled on request")
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
        prompt = PLAN_PROMPT_SHORT if self.optimize else PLAN_PROMPT
        plan = self._text_step("plan", prompt, f"Task: {task_text}\n\n{self.overview}")
        if plan:
            self.plan = plan
            if self.optimize and len(plan) > PLAN_CHARS:
                self.plan = plan[:PLAN_CHARS].rstrip() + "\n[plan cut]"
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
            if self.optimize:
                self._prune()
                self._maybe_compact()
            result = self._call(
                step_type, functools.partial(self._work_request, step_type), work=True
            )
            if result is None:
                return self._limit() or self._no_worker()
            completion, worker_id = result
            message = completion.response.message
            self.messages.append(message.model_copy(update={"role": "assistant"}))
            self._remember_prefix(worker_id)
            calls = message.tool_calls()
            finish: ToolCallPart | None = None
            if not calls:
                text_only += 1
                said = message.text_content().strip().strip(".!`*\"' ").lower()
                # Optimized: a text answer right after the tests passed on a change is taken
                # as `finish`. A nudge would be a new user turn, which costs a step and, with
                # some chat templates, the server's prefix cache (ADR 0024).
                settled = self.optimize and self.tested_after_edit
                if text_only < TEXT_ONLY_LIMIT and said not in FINISH_WORDS and not settled:
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
                    shown, shaped = self._present(call, output)
                    text = f"{shown}\n\n{REPEAT_HINT}" if repeated else shown
                    index = len(self.messages)
                    self.messages.append(self._tool_message(call, text, is_error=output.is_error))
                    self._add_result(call, output, text, index, shaped)
                    self._log_tool(call, output)
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
                    self.work_log.append(f"step {self.step}: the review found issues")
                    step_type = "edit"
                    continue
            summary = self._summary(verdict)
            return LoopOutcome(
                "done", None, summary=summary, verdict=verdict.to_dict(), review=self.last_review
            )

    @property
    def handoffs_file(self) -> str:
        return "handoffs.jsonl" if self.candidate is None else f"handoffs-c{self.candidate}.jsonl"

    def _author_families(self) -> set[str]:
        if self.work_families:
            return set(self.work_families)
        if self.families:
            return set(self.families)
        if self.current is not None and self.current in self.catalog.workers:
            return {self.catalog.get(self.current).spec.family_key}
        return set()

    def _review(self) -> dict[str, Any] | None:
        """Review the change with a worker of another model family when there is one
        (SPEC §8.5): the schema-constrained answer when optimized, Phase 2's text otherwise."""
        self.review_result = None
        limit = self._limit()
        if limit is not None:
            return None
        authors = self._author_families()
        constrained = self.optimize
        if constrained:
            text = self._text_step(
                "review",
                review_module.REVIEW_PROMPT,
                review_module.request_text(
                    self.spec.instruction, self.spec.acceptance, self._diff_text()
                ),
                ResponseFormat(
                    type="json_schema",
                    name="review",
                    json_schema=review_module.REVIEW_SCHEMA,
                    strict=True,
                ),
            )
        else:
            text = self._text_step(
                "review",
                REVIEW_PROMPT,
                f"Task: {self.spec.instruction.strip()}\n\nDiff:\n{self._diff_text()}",
            )
        if text is None:
            return None
        parsed = review_module.parse(text) if constrained else None
        if parsed is not None:
            reviewer = self.last_worker
            parsed.reviewer = reviewer
            if reviewer is not None and reviewer in self.catalog.workers:
                parsed.reviewer_family = self.catalog.get(reviewer).spec.family_key
            parsed.author_families = tuple(sorted(authors))
            self.review_result = parsed
            review = parsed.as_dict()
        else:
            head = text.lstrip("*# ").upper()
            review = {
                "approved": not head.startswith("ISSUES"),
                "text": text[:2000],
                "parsed": head.startswith(("APPROVE", "ISSUES")),
                "reviewer": self.last_worker,
            }
        self.last_review = review
        self.trajectory.emit("review", step=self.step, **review)
        return review

    def review_change(self) -> review_module.Review | None:
        """Review this candidate's finished change (best-of-N selection and SPEC §8.5)."""
        self._review()
        if self.review_result is None and self.last_review is not None:
            approved = bool(self.last_review.get("approved"))
            text = str(self.last_review.get("text") or "")
            fallback = review_module.parse(text) or review_module.Review(approved, [], False, text)
            fallback.reviewer = self.last_review.get("reviewer")
            fallback.author_families = tuple(sorted(self._author_families()))
            self.review_result = fallback
        return self.review_result

    def write_tests(self, prompt: str, user: str) -> str | None:
        """A text step that writes tests, open to every model family (probe and targeted
        tests, SPEC §8.4). Routed as an edit step, so T0 never writes them for science."""
        saved, self.families = self.families, None
        try:
            return self._text_step("edit", prompt, user)
        finally:
            self.families = saved

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
        )
        return text or fallback
