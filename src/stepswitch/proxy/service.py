"""The proxy's request pipeline (ADR 0004 item 3, ADR 0029).

decode the caller's request → find its conversation → route → call the chosen worker in that
worker's own format → answer in the caller's format → record the step.

* **Routing.** Every request is one router decision (`stepswitch.router.route`) for the step
  type its slot hint names (`stepswitch.proxy.slots`). Unless the request starts a new user
  turn, the router state says a tool loop is open, so the conversation's worker stays unless
  the router has to exclude it.
* **Rungs.** A harness run has no gates the proxy could see, so nothing would ever escalate it.
  Heavy steps therefore start on the first rung at or above `ProxySettings.min_level` (T1/T2 by
  default) when the catalog has one; light steps may still use the rungs below.
* **Failures.** A worker that fails before the first byte reaches the caller is marked failed
  for the conversation and the request is routed again, at most `max_attempts` times; after the
  first byte, an error ends the stream in the caller's error shape.
* **Accounting.** Each attempt is a step of the proxy task: a decision and a step event in the
  trajectory, a ledger row with the server's usage, and the task row the dashboard lists.
* **Credentials.** Inbound credential headers never reach codecs, records or upstreams;
  upstream requests carry only the worker endpoint's own credentials.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from stepswitch.compat.codecs import get_codec
from stepswitch.compat.codecs.base import Codec, CodecOptions
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import (
    Delta,
    ErrorDelta,
    Message,
    Request,
    Response,
    StartDelta,
    ToolSpec,
)
from stepswitch.compat.stream import StreamAssembler, response_to_deltas
from stepswitch.core.task import TaskSpec
from stepswitch.ledger import Ledger, LedgerRow, estimate_tokens, gpu_seconds_of, usage_row_fields
from stepswitch.proxy.sessions import (
    SESSION_HEADER,
    Conversation,
    ProxyTask,
    ResponseStore,
    SessionKey,
    SessionStore,
    first_user_text,
    header,
    session_key,
    user_turn,
)
from stepswitch.proxy.slots import Slot, resolve_slot
from stepswitch.router.escalation import Ladder, start_level
from stepswitch.router.features import TaskFeatures
from stepswitch.router.router import Decision, RouterState, route
from stepswitch.router.rules import Rule
from stepswitch.state import StateDB
from stepswitch.trajectory.record import Trajectory
from stepswitch.workers.base import Worker
from stepswitch.workers.catalog import Catalog

# Inbound headers that carry credentials or cookies.
CREDENTIAL_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "x-api-key", "x-goog-api-key", "cookie"}
)
# Errors after which another worker may take the request (before the first byte only).
FAILOVER_KINDS = frozenset(
    {"server", "timeout", "connection", "overloaded", "rate_limit", "too_large", "quota"}
)
RESPONSES_EXTENSION = "responses"
# Output tokens the router reserves for a reply when the caller asks for more.
OUTPUT_RESERVE = 4096
# The proxy task of the request being served, for the egress audit log (`TaskEgress`).
CURRENT_TASK: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "stepswitch_proxy_task", default=None
)


class TaskEgress:
    """The egress layer, with the proxy task of the current request as the audit task ID. The
    catalog's model clients are built once; this makes their connections land on the right
    task in the audit log."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def client(self, purpose: str, *, task_id: str | None = None, **kwargs: Any) -> Any:
        return self.inner.client(purpose, task_id=task_id or CURRENT_TASK.get(), **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@dataclass
class ProxySettings:
    offline: bool = False
    allow_remote: bool = False
    # Caller model names mapped to slots or workers (`[proxy.aliases]`).
    aliases: dict[str, str] = field(default_factory=dict)
    min_level: int = 1
    max_attempts: int = 3
    # Keep-alive interval of streams that wait for the first upstream byte (the messages
    # format's `ping` events; a comment line in other SSE formats).
    heartbeat_seconds: float = 5.0


@dataclass
class Call:
    """One routed request: what the caller sent, where it went and what it cost."""

    fmt: str
    codec: Codec
    request: Request
    conversation: Conversation
    slot: Slot
    step: int
    decision: Decision
    worker: Worker
    upstream: Request
    started: float
    # Responses format: the ID the caller sees, and whether the answer is stored for
    # `previous_response_id`.
    response_id: str | None = None
    store: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def task_id(self) -> str:
        return self.conversation.task.task_id

    @property
    def stream(self) -> bool:
        return self.request.stream


class NoWorker(ModelError):
    def __init__(self, reason: str) -> None:
        super().__init__("overloaded", f"no worker can take this request: {reason}", status=503)


def _held(decision: Decision, current: str) -> Decision:
    """A decision routed with the conversation's worker pinned, labelled for what it is."""
    for candidate in decision.candidates:
        if candidate.worker != current and (candidate.excluded or "").startswith("--worker pins"):
            candidate.excluded = f"the turn is still open on {current}"
    decision.rule = "open turn"
    decision.reason = (
        f"stay on {current} for {decision.step_type}: the turn is still open"
        " (tool results or a continued answer); models switch at a new user turn"
    )
    return decision


def strip_credentials(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in CREDENTIAL_HEADERS}


def request_chars(request: Request) -> int:
    size = len(request.system_text()) + len(request.prompt or "")
    size += sum(len(json.dumps(m.model_dump(exclude_none=True))) for m in request.messages)
    size += sum(len(json.dumps(t.model_dump(exclude_none=True))) for t in request.tools)
    return size


class ProxyService:
    def __init__(
        self,
        root: Path,
        catalog: Catalog,
        *,
        db: StateDB | None = None,
        settings: ProxySettings | None = None,
        rules: Sequence[Rule] = (),
        clock: Callable[[], float] = time.time,
        sessions: SessionStore | None = None,
        responses: ResponseStore | None = None,
    ) -> None:
        self.root = root
        self.catalog = catalog
        self.db = db
        self.settings = settings or ProxySettings()
        self.rules = list(rules)
        self.clock = clock
        self.sessions = sessions or SessionStore(clock=clock)
        self.responses = responses or ResponseStore(clock=clock)
        self._trajectories: dict[str, Trajectory] = {}
        self._ledgers: dict[str, Ledger] = {}
        self._lock = threading.Lock()
        # The session every request belongs to, when the proxy serves one attached task.
        self.bound: str | None = None

    # ------------------------------------------------------------------------------------
    # Attached tasks (a harness run of `ssw run --harness`)
    # ------------------------------------------------------------------------------------

    def attach(
        self,
        session: str,
        *,
        task_id: str,
        trajectory: Trajectory,
        ledger: Ledger,
        instruction: str = "",
        bind_all: bool = True,
        **fields: Any,
    ) -> ProxyTask:
        """Make the requests of `session` steps of an existing task: decisions, step events
        and ledger rows go to the caller's trajectory and ledger, under the caller's task ID.
        With `bind_all`, every request this proxy serves belongs to that session. `fields` set
        the task's routing context (`ProxyTask`)."""
        task = self.sessions.bind(session, task_id, instruction=instruction, **fields)
        with self._lock:
            self._trajectories[task_id] = trajectory
            self._ledgers[task_id] = ledger
        if bind_all:
            self.bound = session
        return task

    def detach(self, session: str) -> ProxyTask | None:
        task = self.sessions.release(session)
        if task is not None:
            with self._lock:
                self._trajectories.pop(task.task_id, None)
                self._ledgers.pop(task.task_id, None)
        if self.bound == session:
            self.bound = None
        return task

    # ------------------------------------------------------------------------------------
    # Decoding and routing
    # ------------------------------------------------------------------------------------

    def decode(
        self, fmt: str, body: Any, *, path: str = "", headers: Mapping[str, str] | None = None
    ) -> tuple[Codec, Request]:
        # Streams open at once, so that keep-alives can follow (the messages codec's option).
        codec = get_codec(fmt, CodecOptions(extra={"eager_start": True}))
        try:
            request = codec.decode_request(
                body, path=path, headers=strip_credentials(headers or {})
            )
        except ModelError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise ModelError("bad_request", str(exc) or "invalid request", status=400) from exc
        return codec, request

    def _continue_stored(self, request: Request) -> tuple[Request, bool]:
        """Resolve `previous_response_id` from the proxy's response store (ADR 0004 item 5);
        also whether this answer is to be stored (`store`, on unless the caller says no)."""
        extension = dict(request.extensions.get(RESPONSES_EXTENSION) or {})
        previous = extension.pop("previous_response_id", None)
        store = extension.pop("store", None) is not False
        extensions = {**request.extensions, RESPONSES_EXTENSION: extension}
        if not extension:
            extensions.pop(RESPONSES_EXTENSION)
        if previous is None:
            return request.model_copy(update={"extensions": extensions}), store
        history = self.responses.get(str(previous))
        if history is None:
            raise ModelError(
                "not_found", f"no stored response {previous!r} in this proxy", status=404
            )
        messages = [*history, *request.messages]
        return request.model_copy(update={"messages": messages, "extensions": extensions}), store

    def prepare(
        self,
        fmt: str,
        body: Any,
        *,
        path: str = "",
        headers: Mapping[str, str] | None = None,
    ) -> Call:
        """Decode and route a request; raises ModelError when it cannot be served."""
        headers = dict(headers or {})
        codec, request = self.decode(fmt, body, path=path, headers=headers)
        store = False
        if fmt == "responses":
            request, store = self._continue_stored(request)
        key = session_key(fmt, request, headers, bound=self.bound)
        conversation, new_task = self.sessions.conversation(
            key, instruction=first_user_text(request)
        )
        if new_task:
            self._start_task(conversation.task, fmt)
        limit = conversation.task.limit() if conversation.task.limit is not None else None
        if limit is not None:
            raise ModelError("quota", f"the task takes no more steps: {limit}", status=429)
        slot = resolve_slot(request, headers, self.catalog, self.settings.aliases)
        call: Call | None = None
        # A decision made ahead for this worker (`route_only`) is this request's step.
        reserved = None
        if slot.pin is not None:
            with conversation.task.lock:
                reserved = conversation.task.reserved.pop(slot.pin, None)
        decision = reserved or self._route(conversation, request, slot)
        if decision.chosen is None:
            self._record_decision(conversation, decision, slot, fmt)
            raise NoWorker(decision.reason)
        worker = self.catalog.get(decision.chosen)
        call = Call(
            fmt=fmt,
            codec=codec,
            request=request,
            conversation=conversation,
            slot=slot,
            step=decision.step,
            decision=decision,
            worker=worker,
            upstream=self._upstream_request(request, worker),
            started=self.clock(),
        )
        if fmt == "responses":
            call.response_id = f"resp_{uuid.uuid4().hex}"
            call.store = store
        if reserved is None:
            self._record_decision(conversation, decision, slot, fmt)
        return call

    def route_only(self, body: Mapping[str, Any], headers: Mapping[str, str]) -> dict[str, Any]:
        """Which worker takes a harness's next request, decided before the request exists
        (`POST /stepswitch/v1/route`: the Pi extension's virtual model, SPEC §12 item 4).

        `body`: `model` (a slot name, default `ssw:auto`), `reason` (`user` starts a new user
        turn; `continuation`, `retry` and `direct` do not), `previous` (the worker of the
        previous request), `failed` (a worker that just failed), `transcript_tokens` and
        `tools` (whether tools are offered). The decision is recorded and kept for the task:
        its next request that names the chosen worker is that step."""
        name = str(body.get("model") or "ssw:auto")
        reason = str(body.get("reason") or "user")
        run = self.bound or header(headers, SESSION_HEADER) or "route"
        key = SessionKey(task=run, conversation=f"{run}/route")
        conversation, new_task = self.sessions.conversation(key)
        if new_task:
            self._start_task(conversation.task, "route")
        limit = conversation.task.limit() if conversation.task.limit is not None else None
        if limit is not None:
            raise ModelError("quota", f"the task takes no more steps: {limit}", status=429)
        tools = [ToolSpec(name="tool")] if body.get("tools", True) else []
        probe = Request(model=name, tools=tools)
        slot = resolve_slot(probe, headers, self.catalog, self.settings.aliases)
        with conversation.lock:
            previous = body.get("previous")
            conversation.current = str(previous) if previous in self.catalog else None
            if body.get("failed"):
                conversation.failed.add(str(body["failed"]))
        decision = self._decide(
            conversation,
            slot,
            tokens=max(0, int(body.get("transcript_tokens") or 0)),
            open_turn=reason != "user",
            output_tokens=OUTPUT_RESERVE,
        )
        self._record_decision(conversation, decision, slot, "route")
        if decision.chosen is None:
            raise NoWorker(decision.reason)
        with conversation.task.lock:
            conversation.task.reserved[decision.chosen] = decision
        spec = self.catalog.get(decision.chosen).spec
        return {
            "worker": spec.id,
            "context": spec.context,
            "step": decision.step,
            "step_type": decision.step_type,
            "reason": decision.reason,
        }

    def _level(self, step_type: str) -> int:
        ladder = Ladder.for_levels(
            self.catalog.levels(
                offline=self.settings.offline, allow_remote=self.settings.allow_remote
            )
        )
        level, _ = start_level(TaskFeatures(), ladder)
        above = [lvl for lvl in ladder.levels if lvl >= self.settings.min_level]
        if above:
            level = max(level, above[0])
        return level

    def _route(self, conversation: Conversation, request: Request, slot: Slot) -> Decision:
        return self._decide(
            conversation,
            slot,
            tokens=estimate_tokens(request_chars(request)),
            open_turn=not user_turn(request),
            output_tokens=min(request.max_output_tokens or 1024, OUTPUT_RESERVE),
        )

    def _decide(
        self,
        conversation: Conversation,
        slot: Slot,
        *,
        tokens: int,
        open_turn: bool,
        output_tokens: int,
    ) -> Decision:
        """One decision. Inside a turn (tool results, a continued assistant turn) the
        conversation stays on its worker even where a rule or the rung would send this step
        type elsewhere; it moves only when that worker cannot take the request at all (it
        failed, or the transcript no longer fits its context). A model name that pins a
        worker is the caller's own choice and always holds."""
        task = conversation.task
        step = task.next_step()
        workers = [w.spec for w in self.catalog]
        features = task.features or TaskFeatures(offline=self.settings.offline)
        pin = slot.pin or task.pin
        with conversation.lock:
            current = conversation.current
            state = RouterState(
                step=step,
                current=current,
                level=task.level if task.level is not None else self._level(slot.step_type),
                transcript_tokens=tokens,
                output_tokens=output_tokens,
                seen_tokens=dict(conversation.seen_tokens),
                open_tool_calls=open_turn,
                # The harness keeps its transcript; the proxy never replaces it with a brief.
                handoff_allowed=False,
                track=getattr(features, "track", "general"),
                offline=self.settings.offline,
                allow_remote=self.settings.allow_remote,
                worker_pin=pin,
                failed=frozenset(conversation.failed),
                families=task.families,
                learned=dict(task.learned),
            )
        decision: Decision | None = None
        if state.open_tool_calls and pin is None and current and current in self.catalog:
            held = route(
                features, slot.step_type, replace(state, worker_pin=current), workers, seed=step
            )
            if held.chosen == current:
                decision = _held(held, current)
        if decision is None:
            decision = route(features, slot.step_type, state, workers, self.rules, seed=step)
            if state.open_tool_calls and current is not None and decision.chosen != current:
                decision.notes.append(f"the turn moved: {current} cannot take this request")
        if slot.note:
            decision.notes.append(slot.note)
        return decision

    def _upstream_request(self, request: Request, worker: Worker) -> Request:
        """The request as the worker gets it: its own model name, and an output cap that
        fits its context."""
        update: dict[str, Any] = {"model": worker.spec.model}
        context = worker.spec.context
        if context and request.max_output_tokens:
            room = context - estimate_tokens(request_chars(request))
            update["max_output_tokens"] = max(256, min(request.max_output_tokens, room))
        return request.model_copy(update=update)

    # ------------------------------------------------------------------------------------
    # Upstream calls
    # ------------------------------------------------------------------------------------

    def complete(self, call: Call) -> Response:
        """A whole answer, failing over to another worker while nothing was sent yet."""
        while True:
            started = self.clock()
            token = CURRENT_TASK.set(call.task_id)
            try:
                completion = call.worker.get_model().complete(call.upstream)
            except ModelError as exc:
                self._record_step(call, None, exc, self.clock() - started)
                if not self._fail_over(call, exc):
                    raise
                continue
            finally:
                CURRENT_TASK.reset(token)
            response = self._with_response_id(call, completion.response)
            self._record_step(call, response, None, self.clock() - started, completion)
            self._store(call, response)
            return response

    def open_stream(self, call: Call) -> Iterator[Delta]:
        """The answer as deltas. Blocks until the first delta (failing over to another worker
        while nothing was sent); the returned iterator records the step when it ends."""
        while True:
            started = self.clock()
            token = CURRENT_TASK.set(call.task_id)
            try:
                source = self._deltas(call)
                first = next(source, None)
            except ModelError as exc:
                self._record_step(call, None, exc, self.clock() - started)
                if not self._fail_over(call, exc):
                    raise
                continue
            finally:
                CURRENT_TASK.reset(token)
            return self._recorded(call, first, source, started)

    def _deltas(self, call: Call) -> Iterator[Delta]:
        model = call.worker.get_model()
        streamer = getattr(model, "stream", None)
        if callable(streamer):
            yield from streamer(call.upstream)
            return
        completion = model.complete(call.upstream)
        yield from response_to_deltas(completion.response)

    def _recorded(
        self, call: Call, first: Delta | None, source: Iterator[Delta], started: float
    ) -> Iterator[Delta]:
        context = contextvars.copy_context()
        assembler = StreamAssembler(origin=None)
        error: ModelError | None = None
        done = False
        try:
            pending = [first] if first is not None else []
            while True:
                if pending:
                    delta = pending.pop()
                else:
                    try:
                        delta = context.run(next, source)
                    except StopIteration:
                        break
                    except ModelError as exc:
                        error = exc
                        delta = ErrorDelta(kind=exc.kind, message=exc.message, status=exc.status)
                if isinstance(delta, StartDelta) and call.response_id:
                    delta = delta.model_copy(update={"id": call.response_id})
                assembler.feed(delta)
                yield delta
                if error is not None:
                    break
            done = error is None
        finally:
            response = assembler.result()
            response = self._with_response_id(call, response)
            if error is None and assembler.error is not None:
                error = ModelError("server", assembler.error.message, status=assembler.error.status)
            if not done and error is None:
                error = ModelError("connection", "the caller closed the stream")
            self._record_step(call, response, error, self.clock() - started)
            if error is None:
                self._store(call, response)

    def _fail_over(self, call: Call, error: ModelError) -> bool:
        """Route the request again without the failed worker; False when it cannot move."""
        call.attempts.append({"worker": call.worker.spec.id, "error": error.kind})
        if error.kind not in FAILOVER_KINDS or len(call.attempts) >= self.settings.max_attempts:
            return False
        if call.slot.pin is not None or call.conversation.task.pin is not None:
            return False
        with call.conversation.lock:
            call.conversation.failed.add(call.worker.spec.id)
        decision = self._route(call.conversation, call.request, call.slot)
        decision.notes.append(f"failover: {call.worker.spec.id} failed ({error.kind})")
        self._record_decision(call.conversation, decision, call.slot, call.fmt)
        if decision.chosen is None:
            return False
        call.decision = decision
        call.step = decision.step
        call.worker = self.catalog.get(decision.chosen)
        call.upstream = self._upstream_request(call.request, call.worker)
        with call.conversation.task.lock:
            call.conversation.task.failovers += 1
        return True

    def _with_response_id(self, call: Call, response: Response) -> Response:
        if call.response_id is None:
            return response
        return response.model_copy(update={"id": call.response_id})

    def _store(self, call: Call, response: Response) -> None:
        if call.response_id is not None and call.store:
            self.responses.put(call.response_id, [*call.request.messages, response.message])

    # ------------------------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------------------------

    def _trajectory(self, task: ProxyTask) -> Trajectory:
        with self._lock:
            found = self._trajectories.get(task.task_id)
            if found is None:
                found = Trajectory(self.root, task.task_id, db=self.db, clock=self.clock)
                self._trajectories[task.task_id] = found
                self._ledgers[task.task_id] = Ledger(self.db, task.task_id, clock=self.clock)
            return found

    def _start_task(self, task: ProxyTask, fmt: str) -> None:
        spec = TaskSpec(
            instruction=task.instruction or "(harness run through ssw proxy)",
            repo=self.root,
            offline=self.settings.offline,
            allow_remote=self.settings.allow_remote,
            review=False,
        )
        now = self.clock()
        if self.db is not None:
            self.db.execute(
                "INSERT INTO tasks (id, created, updated, status, stop_reason, track, instruction,"
                " spec, result) VALUES (?, ?, ?, 'running', NULL, ?, ?, ?, NULL)"
                " ON CONFLICT(id) DO NOTHING",
                (task.task_id, now, now, spec.track, spec.instruction, spec.model_dump_json()),
            )
        self._trajectory(task).emit(
            "task_start",
            task_id=task.task_id,
            spec=json.loads(spec.model_dump_json()),
            proxy={"format": fmt, "session": task.key},
        )

    def _touch(self, task: ProxyTask) -> None:
        if self.db is not None:
            self.db.execute(
                "UPDATE tasks SET updated = ? WHERE id = ? AND status = 'running'",
                (self.clock(), task.task_id),
            )

    def _record_decision(
        self, conversation: Conversation, decision: Decision, slot: Slot, fmt: str
    ) -> None:
        record = decision.as_dict()
        trajectory = self._trajectory(conversation.task)
        extra = {
            "slot": slot.as_dict(),
            "format": fmt,
            "conversation": conversation.key.conversation,
        }
        fields = {k: v for k, v in record.items() if k != "step"}
        trajectory.emit("decision", step=decision.step, **fields, proxy=extra)
        if self.db is not None:
            self.db.execute(
                "INSERT INTO decisions (task_id, step, ts, step_type, chosen, switched, reason,"
                " data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation.task.task_id,
                    decision.step,
                    self.clock(),
                    decision.step_type,
                    decision.chosen,
                    int(decision.switched),
                    decision.reason,
                    json.dumps({**record, "proxy": extra}, default=str),
                ),
            )
        self._touch(conversation.task)

    def _record_step(
        self,
        call: Call,
        response: Response | None,
        error: ModelError | None,
        wall: float,
        completion: Any = None,
    ) -> None:
        spec = call.worker.spec
        conversation = call.conversation
        trajectory = self._trajectory(conversation.task)
        ledger = self._ledgers[conversation.task.task_id]
        message = response.message if response is not None else Message(role="assistant")
        reply_chars = len(json.dumps(message.model_dump(exclude_none=True)))
        fields = usage_row_fields(
            response.usage if response is not None else None,
            request_chars=request_chars(call.upstream),
            response_chars=reply_chars if response is not None else 0,
        )
        gpu = gpu_seconds_of(response, wall, gpu=spec.gpu) if response is not None else 0.0
        losses = [loss.model_dump() for loss in completion.report.losses] if completion else []
        ledger.record(
            LedgerRow(
                task_id=call.task_id,
                step=call.step,
                step_type=call.slot.step_type,
                endpoint=spec.endpoint,
                model=spec.model,
                wall_seconds=round(wall, 6),
                gpu_seconds=round(gpu, 6),
                data={
                    "worker": spec.id,
                    "proxy": True,
                    "format": call.fmt,
                    "finish_reason": response.finish_reason if response is not None else None,
                    "error": error.kind if error is not None else None,
                    "losses": losses,
                },
                **fields,
            )
        )
        switched = False
        with conversation.lock:
            conversation.requests += 1
            if error is None:
                if conversation.current not in (None, spec.id):
                    conversation.switches += 1
                    switched = True
                conversation.current = spec.id
                conversation.seen_tokens[spec.id] = estimate_tokens(
                    request_chars(call.upstream) + reply_chars
                )
        calls = [c.name for c in message.tool_calls()]
        event: dict[str, Any] = {
            "step_type": call.slot.step_type,
            "worker": spec.id,
            "endpoint": spec.endpoint,
            "model": spec.model,
            "tool_calls": calls,
            "text": message.text_content()[:400],
            "finish_reason": response.finish_reason if response is not None else "error",
            "usage": dict(fields),
            "wall_seconds": round(wall, 3),
            "gpu_seconds": round(gpu, 3),
            "proxy": {
                "format": call.fmt,
                "conversation": conversation.key.conversation,
                "stream": call.stream,
            },
        }
        if response is not None:
            event["response_blob"] = trajectory.blob(response.model_dump_json())
        if error is not None:
            event["error"] = {"kind": error.kind, "message": error.message[:400]}
        trajectory.emit("step", step=call.step, **event)
        self._touch(conversation.task)
        if conversation.task.on_step is not None:
            conversation.task.on_step(
                {
                    "step": call.step,
                    "step_type": call.slot.step_type,
                    "request_class": call.slot.request_class,
                    "worker": spec.id,
                    "endpoint": spec.endpoint,
                    "family": spec.family_key,
                    "usage": dict(fields),
                    "wall_seconds": wall,
                    "gpu_seconds": gpu,
                    "error": error.kind if error is not None else None,
                    "switched": switched,
                    "conversation": conversation.key.conversation,
                }
            )

    def end_tasks(self, tasks: Sequence[ProxyTask] | None = None, *, reason: str) -> list[str]:
        """Record proxy tasks as stopped (their sessions ended, or the proxy is shutting down).
        Attached tasks are their callers' to end."""
        ended = list(tasks) if tasks is not None else self.sessions.tasks()
        ended = [task for task in ended if not task.attached]
        for task in ended:
            trajectory = self._trajectory(task)
            trajectory.emit("task_end", status="stopped", stop_reason=reason, steps=task.steps)
            if self.db is not None:
                self.db.execute(
                    "UPDATE tasks SET updated = ?, status = 'stopped', stop_reason = ?"
                    " WHERE id = ? AND status = 'running'",
                    (self.clock(), reason, task.task_id),
                )
        return [task.task_id for task in ended]

    def sweep(self) -> list[str]:
        """End the tasks whose conversations all expired."""
        return self.end_tasks(self.sessions.take_ended(), reason="idle")
