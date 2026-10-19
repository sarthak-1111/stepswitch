"""Proxy sessions (ADR 0004 item 5, ADR 0029): which conversation a request belongs to, which
worker that conversation is on, and when it may move.

A request belongs to a **task** and, within it, to a **conversation**:

* The task comes from `x-stepswitch-session` (adapters set it to their task ID), else from a
  harness's own run header (`x-claude-code-session-id`), else from a hash of the system prompt
  and the first user turn. Every request of a task is one step of one proxy task in the project
  state, so a harness run reads as one task in the ledger and the dashboard.
* The conversation separates the transcripts inside a run: a harness's agent header names it
  (`x-claude-code-agent-id`; subagents have their own), else the same hash. Each conversation
  keeps its own current worker.

Models switch only at a new user turn (`user_turn`): the last message is the user's own words
and carries no tool results. While a tool loop is open, the conversation's worker stays unless
the router has to exclude it (it failed, or the transcript no longer fits its context).
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from stepswitch.compat.ir import Message, Request

SESSION_HEADER = "x-stepswitch-session"
# Headers a supported harness sends on every request (docs/RESEARCH.md, Claude Code): one names
# the run, the other the agent (main conversation or subagent) within it.
HARNESS_RUN_HEADERS = ("x-claude-code-session-id",)
HARNESS_AGENT_HEADERS = ("x-claude-code-agent-id",)
MAX_CONVERSATIONS = 512
SESSION_TTL_SECONDS = 6 * 3600.0
MAX_STORED_RESPONSES = 256


def header(headers: Mapping[str, str], name: str) -> str | None:
    """A header's value, matched case-insensitively; blank values count as missing."""
    for key, value in headers.items():
        if key.lower() == name and str(value).strip():
            return str(value).strip()
    return None


def first_user_text(request: Request) -> str:
    for message in request.messages:
        if message.role == "user" and not message.tool_results():
            return message.text_content()
    return ""


def conversation_hash(fmt: str, request: Request) -> str:
    """Stable for one conversation: the format, the system prompt and the first user turn,
    which every later request of the conversation repeats."""
    data = "\x00".join([fmt, request.system_text(), first_user_text(request)])
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SessionKey:
    task: str
    conversation: str


def session_key(fmt: str, request: Request, headers: Mapping[str, str]) -> SessionKey:
    digest = conversation_hash(fmt, request)
    run = header(headers, SESSION_HEADER)
    if run is None:
        run = next((v for name in HARNESS_RUN_HEADERS if (v := header(headers, name))), None)
    if run is None:
        return SessionKey(task=digest, conversation=digest)
    agent = next((v for name in HARNESS_AGENT_HEADERS if (v := header(headers, name))), None)
    return SessionKey(task=run, conversation=f"{run}/{agent or digest}")


def user_turn(request: Request) -> bool:
    """The request starts a new user turn: models may switch here and nowhere else."""
    if not request.messages:
        return True
    last = request.messages[-1]
    return last.role == "user" and not last.tool_results()


def open_tool_loop(request: Request) -> bool:
    """The request continues a tool loop: it answers tool calls, or extends an assistant turn
    that made some."""
    if not request.messages:
        return False
    last = request.messages[-1]
    return (
        bool(last.tool_results())
        or last.role == "tool"
        or (last.role == "assistant" and bool(last.tool_calls()))
    )


def new_proxy_task_id(clock: Callable[[], float] = time.time) -> str:
    return f"p{time.strftime('%Y%m%d-%H%M%S', time.localtime(clock()))}-{secrets.token_hex(3)}"


@dataclass
class ProxyTask:
    """The task a harness run is recorded as."""

    key: str
    task_id: str
    created: float
    last_used: float
    instruction: str = ""
    steps: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def next_step(self) -> int:
        with self.lock:
            self.steps += 1
            return self.steps


@dataclass
class Conversation:
    """One transcript of a task and the worker it is on."""

    key: SessionKey
    task: ProxyTask
    created: float
    last_used: float
    current: str | None = None
    # Tokens of the transcript each worker's server has already processed (cache-aware
    # switching, SPEC §7.2) and workers that failed in this conversation.
    seen_tokens: dict[str, int] = field(default_factory=dict)
    failed: set[str] = field(default_factory=set)
    requests: int = 0
    switches: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class SessionStore:
    """Bounded, thread-safe store of tasks and conversations. Conversations idle for longer
    than the TTL, or beyond the size bound (least recently used first), are dropped; a task
    ends when its last conversation is dropped."""

    def __init__(
        self,
        *,
        max_conversations: int = MAX_CONVERSATIONS,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        new_task_id: Callable[[], str] | None = None,
    ) -> None:
        self.max_conversations = max_conversations
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.new_task_id = new_task_id or (lambda: new_proxy_task_id(clock))
        self._tasks: dict[str, ProxyTask] = {}
        self._conversations: OrderedDict[SessionKey, Conversation] = OrderedDict()
        self._lock = threading.Lock()
        self.ended: list[ProxyTask] = []

    def conversation(self, key: SessionKey, *, instruction: str = "") -> tuple[Conversation, bool]:
        """The conversation for `key` and whether its task is new."""
        now = self.clock()
        with self._lock:
            self._expire(now)
            found = self._conversations.get(key)
            if found is not None:
                found.last_used = found.task.last_used = now
                self._conversations.move_to_end(key)
                return found, False
            task = self._tasks.get(key.task)
            new_task = task is None
            if task is None:
                task = ProxyTask(key.task, self.new_task_id(), now, now, instruction[:2000])
                self._tasks[key.task] = task
            task.last_used = now
            conversation = Conversation(key, task, now, now)
            self._conversations[key] = conversation
            while len(self._conversations) > self.max_conversations:
                _, old = self._conversations.popitem(last=False)
                self._end_if_idle(old.task)
            return conversation, new_task

    def tasks(self) -> list[ProxyTask]:
        with self._lock:
            return list(self._tasks.values())

    def take_ended(self) -> list[ProxyTask]:
        """Tasks that ended since the last call (to record their final status)."""
        with self._lock:
            ended, self.ended = self.ended, []
            return ended

    def _expire(self, now: float) -> None:
        stale = [k for k, c in self._conversations.items() if now - c.last_used > self.ttl_seconds]
        for key in stale:
            self._end_if_idle(self._conversations.pop(key).task)

    def _end_if_idle(self, task: ProxyTask) -> None:
        if any(c.task is task for c in self._conversations.values()):
            return
        if self._tasks.pop(task.key, None) is not None:
            self.ended.append(task)


@dataclass
class StoredResponse:
    messages: list[Message]
    created: float


class ResponseStore:
    """Responses the proxy answered in the responses format with `store` on (the format's
    default), so that a later request can continue one through `previous_response_id`. The
    conversation lives here, in the proxy, never at the upstream (ADR 0003 item 2)."""

    def __init__(
        self,
        *,
        max_items: int = MAX_STORED_RESPONSES,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._items: OrderedDict[str, StoredResponse] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, response_id: str, messages: list[Message]) -> None:
        with self._lock:
            self._items[response_id] = StoredResponse(list(messages), self.clock())
            self._items.move_to_end(response_id)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def get(self, response_id: str) -> list[Message] | None:
        with self._lock:
            item = self._items.get(response_id)
            if item is None or self.clock() - item.created > self.ttl_seconds:
                self._items.pop(response_id, None)
                return None
            return list(item.messages)
