"""Replay and fork recorded runs (SPEC §11, ADR 0027).

A run records what a replay needs: the task (`task_start`), the base commit of each worktree
(`worktree`), the workers, the summarizer and the context settings (`catalog`), the
classifier's features (`classify`), the learning router's samples (`learning`), each
candidate's plan and ladder (`best_of_n`, `candidate_start`, `ladder`), every routing decision
(`decision`), every model answer in full (`step` and `aux` events with `response_blob`), every
failed model call (`model_error`), every tool result (`tool`, with `output_blob` and `raw_blob`
when long) and every compression (`compress`).

`replay_run` runs the task again from that recording, as a new run:

* each worktree starts at its recorded base commit;
* the recorded features, samples, plans and ladders are used: there is no classifier call and
  no sampling, and a replay teaches the learning router nothing;
* every routing decision is the recorded one and every model call is answered from the
  recording, a recorded failure by raising it again (so failovers repeat): no model is asked;
* edits, commands and test runs run again for their effect on the files, but every tool
  result the transcript gets is the recorded one, so the transcript is the recorded one;
* the gates run for real. Time is real time plus the recorded time of what the recording
  answers, so time budgets run out where they ran out.

With `from_step=k` the replay forks at the routing decision for model call k (calls are
numbered per candidate, failed attempts included): from there on routing decides, pinned to
`worker` when one is given, models answer live and tools run. `compare` says whether a replay
reproduced the recording (the same diffs and the same gate results) and lists every point
where it departed from the recording.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from stepswitch.compat.client import Completion
from stepswitch.compat.errors import ErrorKind, ModelError
from stepswitch.compat.ir import ConversionReport, Request, Response
from stepswitch.trajectory.record import EVENTS_FILE, get_blob, read_events, runs_dir
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.tools import TOOL_ALIASES, ToolOutput

RESPONSE_BLOB = "response_blob"
RAW_BLOB = "raw_blob"
# Tools a replay runs again for their effect on the files (tests write files too); the
# transcript still gets their recorded results.
EFFECT_TOOLS = frozenset({"edit", "run", "test"})
# How much of a call's arguments a tool event keeps.
ARGUMENT_CHARS = 2000
# The comparison with the recording, in the replay's run directory.
REPORT_FILE = "replay.json"
_TUPLE_FIELDS = ("roles", "notes")
_ENVELOPE = ("seq", "ts", "type", "step", "candidate")


class ReplayError(Exception):
    """The run cannot be replayed (as asked)."""


class RunNotFound(ReplayError):
    """No recorded run by that name."""


@dataclass(frozen=True)
class ModelCall:
    """One recorded model call: its number, kind, worker and answer (or failure)."""

    seq: int
    step: int
    kind: str  # "step" (routed by a decision) or "aux" (a brief or a summary)
    worker: str
    seconds: float
    response: dict[str, Any] | None = None
    error: tuple[str, str] | None = None


@dataclass(frozen=True)
class ToolCall:
    """One recorded tool call and its result."""

    seq: int
    step: int
    name: str
    arguments: str
    tool: str  # the canonical tool name
    text: str
    is_error: bool
    data: dict[str, Any]
    changed_files: tuple[str, ...]
    raw: str | None
    seconds: float

    @property
    def effects(self) -> bool:
        """Whether a replay runs the call again, for its effect on the files."""
        return self.tool in EFFECT_TOOLS

    def output(self) -> ToolOutput:
        return ToolOutput(
            self.text,
            self.is_error,
            copy.deepcopy(self.data),
            list(self.changed_files),
            self.raw,
        )


@dataclass(frozen=True)
class Compression:
    seq: int
    output: str | None
    error: str | None


@dataclass
class Stream:
    """What one candidate recorded (None: the only one), in order."""

    ladder: dict[str, Any] | None = None
    decisions: dict[int, dict[str, Any]] = field(default_factory=dict)
    calls: list[ModelCall] = field(default_factory=list)
    tools: list[ToolCall] = field(default_factory=list)
    compressions: list[Compression] = field(default_factory=list)
    gates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def last_step(self) -> int:
        return max((call.step for call in self.calls if call.kind == "step"), default=0)


@dataclass
class Recording:
    """A recorded run, read back for replay (`load_recording`)."""

    task_id: str
    run_dir: Path
    spec: dict[str, Any]
    workers: list[dict[str, Any]]
    summarizer: str | None
    settings: dict[str, Any] | None
    classify: dict[str, Any]
    classify_seconds: float
    learning: dict[str, Any] | None
    plans: dict[int, dict[str, Any]]
    bases: dict[int | None, str | None]
    workdirs: dict[int | None, str]
    streams: dict[int | None, Stream]
    diffs: dict[str, str]
    result: dict[str, Any]

    def task_spec(self) -> Any:
        from stepswitch.core.task import TaskSpec

        return TaskSpec.model_validate(self.spec)

    def worker_specs(self) -> list[WorkerSpec]:
        known = {f.name for f in dataclasses.fields(WorkerSpec)}
        specs: list[WorkerSpec] = []
        for data in self.workers:
            values = {k: v for k, v in data.items() if k in known}
            for name in _TUPLE_FIELDS:
                if name in values:
                    values[name] = tuple(values[name] or ())
            specs.append(WorkerSpec(**values))
        return specs

    def context_settings(self) -> Any:
        from stepswitch.context.compress import CompressionSettings
        from stepswitch.context.settings import ContextSettings

        if not self.settings:
            return None
        data = dict(self.settings)
        compression = CompressionSettings(**dict(data.pop("compression", None) or {}))
        known = {f.name for f in dataclasses.fields(ContextSettings)}
        return ContextSettings(
            **{k: v for k, v in data.items() if k in known}, compression=compression
        )

    def features(self) -> Any:
        from stepswitch.router.features import TaskFeatures

        data = self.classify.get("features") or {}
        known = {f.name for f in dataclasses.fields(TaskFeatures)}
        return TaskFeatures(**{k: v for k, v in data.items() if k in known})

    def gate_results(self) -> dict[str, list[dict[str, Any]]]:
        return {
            _label(candidate): [_gate_outcome(event) for event in stream.gates]
            for candidate, stream in sorted(self.streams.items(), key=_order)
            if stream.gates
        }


def _label(candidate: int | None) -> str:
    return "task" if candidate is None else f"candidate {candidate}"


def _order(item: tuple[int | None, Any]) -> int:
    return -1 if item[0] is None else item[0]


def _gate_outcome(event: Mapping[str, Any]) -> dict[str, Any]:
    verdict = event.get("verdict") or {}
    return {
        "step": event.get("step"),
        "run": event.get("run"),
        "passed": verdict.get("passed"),
        "failed_gate": verdict.get("failed_gate"),
        "gates": [
            [str(r.get("name")), str(r.get("status"))]
            for r in verdict.get("results") or []
            if isinstance(r, Mapping)
        ],
    }


def _canonical_tool(name: str, data: Mapping[str, Any]) -> str:
    if isinstance(data.get("tool"), str):
        return str(data["tool"])
    key = (name or "").strip().lower()
    return TOOL_ALIASES.get(key, key)


def find_run(root: Path, run: str | Path) -> Path:
    """The run directory of `run`: a path, a task id, a unique prefix of one, or `last`."""
    path = Path(run)
    if (path / EVENTS_FILE).is_file():
        return path
    name = str(run)
    if isinstance(run, Path) or "/" in name or "\\" in name or name in ("", ".", ".."):
        raise RunNotFound(f"no recorded run at {run}")
    base = runs_dir(root)
    if str(run) == "last":
        found = sorted(p for p in base.glob("*") if (p / EVENTS_FILE).is_file())
        if not found:
            raise RunNotFound(f"no recorded runs under {base}")
        return found[-1]
    if (base / str(run) / EVENTS_FILE).is_file():
        return base / str(run)
    matches = sorted(p for p in base.glob(f"{run}*") if (p / EVENTS_FILE).is_file())
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(p.name for p in matches[:5])
        raise ReplayError(f"{run!r} matches several runs: {names}")
    raise RunNotFound(f"no recorded run {run!r} under {base}")


def _blob_text(root: Path, ref: str) -> str:
    try:
        return get_blob(root, ref).decode("utf-8")
    except OSError as exc:
        raise ReplayError(f"blob {ref[:12]} of the recording is missing: {exc}") from exc


def load_recording(run_dir: Path, root: Path) -> Recording:
    """Read a run directory and the blobs it refers to (under the project at `root`)."""
    run_dir = Path(run_dir)
    events = read_events(run_dir)
    start = next((e for e in events if e.get("type") == "task_start"), None)
    if start is None:
        raise RunNotFound(f"{run_dir} holds no recorded run")
    streams: dict[int | None, Stream] = {}
    last_ts: dict[int | None, float] = {}
    previous = float(start.get("ts") or 0.0)
    workers: list[dict[str, Any]] = []
    summarizer: str | None = None
    settings: dict[str, Any] | None = None
    have_catalog = False
    classify: dict[str, Any] = {}
    classify_seconds = 0.0
    learning: dict[str, Any] | None = None
    plans: dict[int, dict[str, Any]] = {}
    bases: dict[int | None, str | None] = {}
    workdirs: dict[int | None, str] = {}
    for event in events:
        kind = event.get("type")
        candidate = event.get("candidate")
        ts = float(event.get("ts") or 0.0)
        gap = max(0.0, ts - last_ts.get(candidate, ts))
        stream = streams.setdefault(candidate, Stream())
        if kind == "worktree":
            bases.setdefault(candidate, event.get("base"))
            if event.get("path"):
                workdirs.setdefault(candidate, str(event["path"]))
        elif kind == "catalog":
            have_catalog = True
            workers = list(event.get("workers") or [])
            summarizer = event.get("summarizer")
            settings = event.get("settings")
        elif kind == "classify":
            classify = {"features": event.get("features"), "details": event.get("details")}
            classify_seconds = max(0.0, ts - previous)
        elif kind == "learning":
            learning = {k: v for k, v in event.items() if k not in _ENVELOPE}
        elif kind == "best_of_n":
            for plan in event.get("plans") or []:
                plans.setdefault(int(plan["index"]), dict(plan))
        elif kind == "candidate_start" and isinstance(event.get("plan"), Mapping):
            plans[int(event["candidate"])] = dict(event["plan"])
        elif kind == "ladder":
            stream.ladder = {k: event.get(k) for k in ("levels", "start", "reason")}
        elif kind == "decision" and isinstance(event.get("step"), int):
            stream.decisions[int(event["step"])] = event
        elif kind in ("step", "aux"):
            ref = event.get(RESPONSE_BLOB)
            if not ref:
                raise ReplayError(
                    f"{run_dir.name} was recorded without full model answers, which replay"
                    " needs (runs recorded before Phase 7)"
                )
            stream.calls.append(
                ModelCall(
                    int(event["seq"]),
                    int(event.get("step") or 0),
                    "step" if kind == "step" else "aux",
                    str(event.get("worker")),
                    float(event.get("wall_seconds") or 0.0),
                    response=json.loads(_blob_text(root, str(ref))),
                )
            )
        elif kind == "model_error" and isinstance(event.get("step"), int):
            stream.calls.append(
                ModelCall(
                    int(event["seq"]),
                    int(event["step"]),
                    "aux" if event.get("purpose") else "step",
                    str(event.get("worker")),
                    gap,
                    error=(str(event.get("kind") or "server"), str(event.get("message") or "")),
                )
            )
        elif kind == "tool":
            ref = event.get("output_blob")
            raw = event.get(RAW_BLOB)
            data = dict(event.get("data") or {})
            stream.tools.append(
                ToolCall(
                    int(event["seq"]),
                    int(event.get("step") or 0),
                    str(event.get("name")),
                    str(event.get("arguments") or ""),
                    _canonical_tool(str(event.get("name")), data),
                    _blob_text(root, str(ref)) if ref else str(event.get("output") or ""),
                    bool(event.get("is_error")),
                    data,
                    tuple(event.get("changed_files") or ()),
                    _blob_text(root, str(raw)) if raw else None,
                    gap,
                )
            )
        elif kind == "compress":
            ref = event.get("output_blob")
            stream.compressions.append(
                Compression(
                    int(event["seq"]),
                    _blob_text(root, str(ref)) if ref else None,
                    str(event["error"]) if event.get("error") else None,
                )
            )
        elif kind == "gates":
            stream.gates.append(event)
        last_ts[candidate] = ts
        previous = ts
    if not have_catalog:
        raise ReplayError(f"{run_dir.name} was recorded without its workers (before Phase 7)")
    streams = {k: v for k, v in streams.items() if k is None or v.calls or v.ladder}
    result_path = run_dir / "result.json"
    return Recording(
        task_id=str(start.get("task_id") or run_dir.name),
        run_dir=run_dir,
        spec=dict(start.get("spec") or {}),
        workers=workers,
        summarizer=summarizer,
        settings=settings,
        classify=classify,
        classify_seconds=classify_seconds,
        learning=learning,
        plans=plans,
        bases=bases,
        workdirs=workdirs,
        streams=streams,
        diffs=_diffs(run_dir),
        result=json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {},
    )


def _diffs(run_dir: Path) -> dict[str, str]:
    """The run's patches by file name: the result's and each candidate's."""
    names = ["diff.patch", *sorted(p.name for p in run_dir.glob("candidate-*.patch"))]
    return {
        name: (run_dir / name).read_text(encoding="utf-8")
        for name in names
        if (run_dir / name).is_file()
    }


class ReplayClock:
    """A replay's clock: real time plus the recorded time of what the recording answers (model
    calls, tool results), which the replay does not wait for."""

    def __init__(self, base: Callable[[], float] = time.monotonic) -> None:
        self.base = base
        self.offset = 0.0

    def __call__(self) -> float:
        return self.base() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += max(0.0, seconds)


def _decision(step: int, data: Mapping[str, Any]) -> Any:
    from stepswitch.router.router import Candidate, Decision

    names = {f.name for f in dataclasses.fields(Decision)} - {"step", "candidates"}
    known = {f.name for f in dataclasses.fields(Candidate)}
    candidates = [
        Candidate(**{k: v for k, v in item.items() if k in known})
        for item in data.get("candidates") or []
        if isinstance(item, Mapping)
    ]
    return Decision(step=step, candidates=candidates, **{k: data[k] for k in names if k in data})


@dataclass
class CandidateReplay:
    """One candidate's replay, which its agent loop consults: the recorded decisions, model
    answers, tool results and compressions until the fork, and where the replay departed from
    the recording."""

    candidate: int | None
    stream: Stream
    clock: ReplayClock
    from_step: int | None = None
    fork_worker: str | None = None
    forked: bool = False
    served: int = 0  # model calls answered from the recording
    live: int = 0  # model calls answered by a model (after the fork)
    executed: int = 0  # tool calls run again for their effects
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._calls: deque[ModelCall] = deque(self.stream.calls)
        self._tools: deque[ToolCall] = deque(self.stream.tools)
        self._compressions: deque[Compression] = deque(self.stream.compressions)

    def depart(self, note: str) -> None:
        prefix = "" if self.candidate is None else f"candidate {self.candidate}: "
        self.notes.append(prefix + note)

    def decision(self, step: int) -> Any:
        """The recorded decision for model call `step`; None routes it (after the fork)."""
        if self.forked:
            return None
        if self.from_step is not None and step >= self.from_step:
            self.forked = True
            return None
        data = self.stream.decisions.get(step)
        if data is None:
            self.depart(f"call {step} has no recorded routing decision")
            return None
        return _decision(step, data)

    def model(self, worker: Worker, kind: str) -> ReplayModel:
        return ReplayModel(self, worker, kind)

    def answer(self, worker_id: str, kind: str) -> ModelCall | None:
        """The recorded answer to the next model call; None after the fork (a live call)."""
        if self.forked:
            self.live += 1
            return None
        head = self._calls[0] if self._calls else None
        if head is None or head.kind != kind:
            has = "none" if head is None else f"a {head.kind} call on {head.worker}"
            self.depart(f"a {kind} call on {worker_id} where the recording has {has}")
            raise ModelError("server", "replay: the recording has no answer for this call")
        self._calls.popleft()
        if head.worker != worker_id:
            self.depart(f"call {head.step} went to {worker_id}; it was recorded on {head.worker}")
        self.served += 1
        self.clock.advance(head.seconds)
        return head

    def tool(self, step: int, call: Any) -> ToolCall | None:
        """The recorded result of this tool call; None runs it (after the fork, or when the
        replay departed from the recording)."""
        if self.forked:
            return None
        head = self._tools[0] if self._tools else None
        name = str(call.name)
        if head is None:
            self.depart(f"step {step}: {name}, which the recording does not have")
            return None
        if (head.step, head.name) != (step, name):
            self.depart(
                f"step {step}: {name} where the recording has {head.name} at step {head.step}"
            )
            return None
        self._tools.popleft()
        if head.arguments != str(call.arguments)[:ARGUMENT_CHARS]:
            self.depart(f"step {step}: {name} with other arguments than recorded")
            return None
        if not head.effects:
            self.clock.advance(head.seconds)
        return head

    def check(self, recorded: ToolCall, output: ToolOutput) -> None:
        """Compare a tool call run again with its recorded result."""
        self.executed += 1
        same = output.is_error == recorded.is_error and sorted(output.changed_files) == sorted(
            recorded.changed_files
        )
        if recorded.tool == "edit":
            same = same and output.text == recorded.text
        elif recorded.tool == "run":
            same = same and output.data.get("exit_code") == recorded.data.get("exit_code")
        elif recorded.tool == "test":
            same = same and output.data.get("ok") == recorded.data.get("ok")
        if not same:
            self.depart(f"step {recorded.step}: {recorded.tool} gave another result this time")

    def compressor(self, live: Any) -> ReplayCompressor:
        return ReplayCompressor(self, live)

    def compression(self) -> Compression | None:
        """The recorded result of the next compression; None after the fork."""
        if self.forked:
            return None
        if not self._compressions:
            self.depart("a compression the recording does not have")
            raise RuntimeError("replay: the recording has no compression here")
        return self._compressions.popleft()

    def left_over(self) -> list[str]:
        """What the recording has and this replay never asked for."""
        if self.forked:
            return []
        left = []
        if self._calls:
            left.append(f"{len(self._calls)} model calls")
        if self._tools:
            left.append(f"{len(self._tools)} tool calls")
        if self._compressions:
            left.append(f"{len(self._compressions)} compressions")
        return left


class ReplayModel:
    """A worker's model in a replay: the recording answers until the fork, then the worker's
    own model."""

    def __init__(self, replay: CandidateReplay, worker: Worker, kind: str) -> None:
        self.replay = replay
        self.worker = worker
        self.kind = kind

    def complete(self, request: Request) -> Completion:
        call = self.replay.answer(self.worker.spec.id, self.kind)
        if call is None:
            return self.worker.get_model().complete(request)
        if call.error is not None:
            raise ModelError(cast(ErrorKind, call.error[0]), call.error[1])
        response = Response.model_validate(call.response)
        return Completion(response, ConversionReport(target="replay"), call.seconds)


class ReplayCompressor:
    """The compressor in a replay: recorded results until the fork, then `live`."""

    def __init__(self, replay: CandidateReplay, live: Any) -> None:
        self.replay = replay
        self.live = live

    def compress(self, text: str, rate: float) -> str:
        recorded = self.replay.compression()
        if recorded is None:
            return str(self.live.compress(text, rate))
        if recorded.error is not None:
            raise RuntimeError(recorded.error)
        return recorded.output or ""


@dataclass
class Replay:
    """A replay of a whole run, which the orchestrator follows (`run_task(replay=...)`)."""

    recording: Recording
    from_step: int | None = None
    worker: str | None = None
    clock: ReplayClock = field(default_factory=ReplayClock)
    candidates: dict[int | None, CandidateReplay] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.worker is not None and self.from_step is None:
            self.from_step = 1
        if self.from_step is None:
            return
        if any(candidate is not None for candidate in self.recording.streams):
            raise ReplayError("a best-of-N run can be replayed whole, not forked")
        last = self.recording.streams.get(None, Stream()).last_step
        if not 1 <= self.from_step <= last:
            raise ReplayError(f"--from-step must be between 1 and {last}, the run's model calls")

    @property
    def fork(self) -> bool:
        return self.from_step is not None

    def header(self) -> dict[str, Any]:
        """Fields of the replay's `task_start` event."""
        return {
            "replay_of": self.recording.task_id,
            "from_step": self.from_step,
            "fork_worker": self.worker,
        }

    def catalog(self, live: Any) -> Any:
        """The recorded workers, with the live models of those configured (used after the
        fork), and the fork's worker."""
        from stepswitch.workers.catalog import Catalog

        workers = []
        for spec in self.recording.worker_specs():
            source = live.workers.get(spec.id)
            workers.append(Worker(spec, factory=source.get_model if source is not None else None))
        catalog = Catalog(workers, notes=list(live.notes))
        if self.worker is not None:
            found = catalog.find(self.worker)
            if found is None:
                extra = live.find(self.worker)
                if extra is None:
                    known = ", ".join(sorted(set(catalog.workers) | set(live.workers)))
                    raise ReplayError(f"unknown worker {self.worker!r} (workers: {known})")
                catalog = Catalog([*catalog.workers.values(), extra], notes=list(live.notes))
                found = extra
            self.worker = found.spec.id
        return catalog

    def summarizer(self, catalog: Any) -> Worker | None:
        name = self.recording.summarizer
        return catalog.workers.get(name) if name else None

    def base(self, candidate: int | None) -> str | None:
        bases = self.recording.bases
        return bases.get(candidate) or bases.get(None)

    def workdir(self, candidate: int | None) -> str | None:
        workdirs = self.recording.workdirs
        return workdirs.get(candidate) or workdirs.get(None)

    def ladder(self, candidate: int | None) -> dict[str, Any] | None:
        stream = self.recording.streams.get(candidate)
        return stream.ladder if stream is not None else None

    def learned(self) -> dict[str, float]:
        learning = self.recording.learning or {}
        return {str(k): float(v) for k, v in (learning.get("samples") or {}).items()}

    def plans(self, count: int, fallback: Callable[[int], list[Any]]) -> list[Any]:
        """The recorded plans of the first `count` best-of-N candidates."""
        from stepswitch.verify.best_of_n import Plan

        recorded = self.recording.plans
        if not all(index in recorded for index in range(count)):
            self.notes.append(f"the recording has no plans for {count} candidates")
            return fallback(count)
        return [
            Plan(
                index,
                str(recorded[index]["family"]),
                tuple(recorded[index].get("workers") or ()),
                int(recorded[index].get("seed") or 0),
                str(recorded[index].get("note") or ""),
            )
            for index in range(count)
        ]

    def for_candidate(self, index: int | None) -> CandidateReplay:
        if index not in self.candidates:
            stream = self.recording.streams.get(index)
            if stream is None:
                self.notes.append(f"{_label(index)} is not in the recording")
                stream = Stream()
            self.candidates[index] = CandidateReplay(
                index, stream, self.clock, self.from_step, self.worker
            )
        return self.candidates[index]

    def departures(self) -> list[str]:
        notes = list(self.notes)
        for index, replay in sorted(self.candidates.items(), key=_order):
            notes += replay.notes
            left = replay.left_over()
            if left:
                notes.append(f"{_label(index)}: the replay never used " + ", ".join(left))
        return notes


@dataclass
class Replayed:
    result: Any  # the replay's TaskResult
    comparison: dict[str, Any]


def compare(recording: Recording, run_dir: Path, replay: Replay) -> dict[str, Any]:
    """How the replay in `run_dir` compares with the recording."""
    events = read_events(run_dir)
    gates: dict[int | None, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("type") == "gates":
            gates.setdefault(event.get("candidate"), []).append(_gate_outcome(event))
    gates_now = {_label(k): v for k, v in sorted(gates.items(), key=_order)}
    gates_then = recording.gate_results()
    diffs_now = _diffs(run_dir)
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
    names = sorted(set(recording.diffs) | set(diffs_now))
    return {
        "replay_of": recording.task_id,
        "task_id": run_dir.name,
        "fork": {"from_step": replay.from_step, "worker": replay.worker} if replay.fork else None,
        "identical_diffs": diffs_now == recording.diffs,
        "identical_gate_results": gates_now == gates_then,
        "diffs": {
            name: {
                "identical": diffs_now.get(name) == recording.diffs.get(name),
                "recorded_lines": len(recording.diffs.get(name, "").splitlines()),
                "replayed_lines": len(diffs_now.get(name, "").splitlines()),
            }
            for name in names
        },
        "gate_results": {"recorded": gates_then, "replayed": gates_now},
        "status": {"recorded": recording.result.get("status"), "replayed": result.get("status")},
        "model_calls": {
            "recorded": sum(len(s.calls) for s in recording.streams.values()),
            "from_recording": sum(c.served for c in replay.candidates.values()),
            "live": sum(c.live for c in replay.candidates.values()),
        },
        "tools_run_again": sum(c.executed for c in replay.candidates.values()),
        "departures": replay.departures(),
    }


def prepare(
    run: str | Path, root: Path, *, from_step: int | None = None, worker: str | None = None
) -> Replay:
    """Load the recorded run `run` of the project at `root` and check that it can be replayed
    (or forked) as asked; raises `ReplayError` (`RunNotFound`) otherwise."""
    root = Path(root).resolve()
    return Replay(load_recording(find_run(root, run), root), from_step, worker)


def replay_run(
    run: str | Path,
    root: Path,
    *,
    from_step: int | None = None,
    worker: str | None = None,
    config: Any = None,
    catalog: Any = None,
    egress: Any = None,
    echo: Callable[[str], None] | None = None,
    gates: Callable[..., Any] | None = None,
    task_id: str | None = None,
) -> Replayed:
    """Replay the recorded run `run` (a run directory or task id) of the project at `root`, or
    fork it at model call `from_step` (on `worker`), as the run `task_id` (new by default).
    Never applies the result. The comparison is also written to the run's `replay.json`."""
    from stepswitch.core.orchestrator import run_task

    root = Path(root).resolve()
    replay = prepare(run, root, from_step=from_step, worker=worker)
    recording = replay.recording
    if worker is not None:
        # Resolve the fork's worker now: an unknown one is the caller's mistake, not a run's.
        if catalog is None:
            from stepswitch import config as config_module
            from stepswitch.workers.catalog import Catalog

            config = config or config_module.load(root)
            catalog = Catalog.from_config(config, root, egress=egress)
        replay.catalog(catalog)
    spec = recording.task_spec()
    repo = spec.repo if Path(spec.repo).exists() else root
    spec = spec.model_copy(update={"apply": False, "repo": repo})
    result = run_task(
        spec,
        root=root,
        config=config,
        catalog=catalog,
        egress=egress,
        echo=echo,
        gates=gates,
        task_id=task_id,
        replay=replay,
    )
    run_dir = Path(str(result.run_dir))
    comparison = compare(recording, run_dir, replay)
    (run_dir / REPORT_FILE).write_text(
        json.dumps(comparison, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return Replayed(result, comparison)
