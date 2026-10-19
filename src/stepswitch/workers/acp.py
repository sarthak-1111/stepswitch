"""Agent Client Protocol client and the generic ACP adapter (SPEC §12 item 7, ADR 0029).

ACP v1 (`protocolVersion` 1; schema 1.26, docs/RESEARCH.md): JSON-RPC 2.0 over the agent's
stdio, one UTF-8 message per line. Stepswitch implements the client side itself (the Python SDK
requires Python < 3.15) with the stable methods only:

1. `initialize` with `protocolVersion` 1 and the capabilities Stepswitch implements:
   `fs.readTextFile` and `fs.writeTextFile` (inside the worktree only), no terminals. An agent
   that answers with another protocol version is refused (fail closed).
2. `session/load` when the task resumes a session and the agent has `loadSession` (the replayed
   history is not reported again), else `session/new` with the worktree as `cwd`, no MCP servers.
3. When the session offers a `model` config option with the endpoint's main slot model among its
   values, `session/set_config_option` selects it; otherwise the agent's model configuration
   already points at the proxy.
4. `session/prompt` with the task text. `session/update` notifications become events until the
   prompt answers with a stop reason.
5. Requests from the agent: `session/request_permission` is answered by the run's permission
   policy (`decide`); `fs/read_text_file` and `fs/write_text_file` work inside the worktree only;
   any other method is "method not found".
6. `cancel()` sends `session/cancel`, answers permission requests that arrive afterwards with
   `cancelled`, waits for the prompt's `cancelled` stop reason, then closes stdin and ends the
   agent's process tree.

Generic agents read the proxy from `STEPSWITCH_PROXY_URL`, `STEPSWITCH_PROXY_TOKEN` and
`STEPSWITCH_SESSION`; the OpenCode and Kimi Code adapters configure their agents themselves.
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import IO, Any

from stepswitch import __version__
from stepswitch.context.proc import kill_tree, popen_kwargs
from stepswitch.workers.harness import (
    CANCEL_GRACE_SECONDS,
    STDERR_TAIL,
    HarnessAdapter,
    HarnessEndpoint,
    HarnessEvent,
    HarnessMissing,
    HarnessSettings,
    HarnessTask,
    RunHandle,
    RunResult,
    allowed_test_prefixes,
    path_inside,
    tail,
)

PROTOCOL_VERSION = 1
PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Seconds for the setup requests (initialize, session/new, ...); the prompt has no limit (the
# run's budget watchdog cancels it).
SETUP_TIMEOUT_SECONDS = 120.0
STOP_REASONS = {
    "end_turn": ("done", None),
    "max_turn_requests": ("stopped", "max_steps"),
    "max_tokens": ("failed", "model_error"),
    "refusal": ("failed", "model_error"),
    "cancelled": ("stopped", "cancelled"),
}
# Tool kinds that only look at files, and those that change them (ACP `ToolKind`).
READ_KINDS = frozenset({"read", "search", "think"})
EDIT_KINDS = frozenset({"edit", "delete", "move"})


class AcpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def as_error(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


class JsonRpcPeer:
    """JSON-RPC 2.0 over a byte stream pair, one message per line.

    Notifications are handled on the reader thread, in order; requests from the other side run
    on their own threads, so that a slow answer never stops the reader. `transcript`, when
    given, receives every message as `{"dir": "send" | "recv", "msg": ...}`, one per line.
    """

    def __init__(
        self,
        reader: IO[bytes],
        writer: IO[bytes],
        *,
        on_request: Callable[[str, Any], Any],
        on_notification: Callable[[str, Any], None],
        transcript: IO[str] | None = None,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.on_request = on_request
        self.on_notification = on_notification
        self.transcript = transcript
        self._next_id = 0
        self._pending: dict[int | str, Future[Any]] = {}
        self._write_lock = threading.Lock()
        self._lock = threading.Lock()
        self.closed = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True, name="acp-reader")

    def start(self) -> None:
        self._thread.start()

    def _record(self, direction: str, message: Any) -> None:
        if self.transcript is not None:
            with self._lock:
                self.transcript.write(
                    json.dumps({"dir": direction, "msg": message}, ensure_ascii=False) + "\n"
                )
                self.transcript.flush()

    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            if self.closed.is_set():
                raise AcpError(INTERNAL_ERROR, "the agent's connection is closed")
            self._record("send", message)
            try:
                self.writer.write(line.encode("utf-8") + b"\n")
                self.writer.flush()
            except (OSError, ValueError) as exc:
                raise AcpError(INTERNAL_ERROR, f"writing to the agent failed: {exc}") from exc

    def request(self, method: str, params: Any = None, *, timeout: float | None = None) -> Any:
        future: Future[Any] = Future()
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            self._pending[request_id] = future
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            self.send(message)
            return future.result(timeout=timeout)
        except FutureTimeout as exc:
            raise AcpError(INTERNAL_ERROR, f"{method}: no answer in {timeout:g} s") from exc
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: Any = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)

    def _respond(self, request_id: Any, method: str, params: Any) -> None:
        try:
            result = self.on_request(method, params)
            reply: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except AcpError as exc:
            reply = {"jsonrpc": "2.0", "id": request_id, "error": exc.as_error()}
        except Exception as exc:  # noqa: BLE001 - the agent gets an error, the run goes on
            error = AcpError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
            reply = {"jsonrpc": "2.0", "id": request_id, "error": error.as_error()}
        with contextlib.suppress(AcpError):
            self.send(reply)

    def _read(self) -> None:
        try:
            for raw in iter(self.reader.readline, b""):
                line = raw.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    self._record("recv", {"unparsed": line[:500].decode("utf-8", "replace")})
                    continue
                self._record("recv", message)
                if not isinstance(message, dict):
                    continue
                self._dispatch(message)
        except (OSError, ValueError):
            pass
        finally:
            self.closed.set()
            with self._lock:
                pending = list(self._pending.values())
            for future in pending:
                if not future.done():
                    future.set_exception(AcpError(INTERNAL_ERROR, "the agent ended the session"))

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if isinstance(method, str):
            params = message.get("params")
            if "id" in message:
                threading.Thread(
                    target=self._respond,
                    args=(message["id"], method, params),
                    daemon=True,
                    name=f"acp-{method}",
                ).start()
            else:
                with contextlib.suppress(Exception):
                    self.on_notification(method, params)
            return
        request_id = message.get("id")
        with self._lock:
            future = self._pending.get(request_id) if request_id is not None else None
        if future is None or future.done():
            return
        if "error" in message:
            error = message.get("error") or {}
            future.set_exception(
                AcpError(
                    int(error.get("code", INTERNAL_ERROR)),
                    str(error.get("message", "error")),
                    error.get("data"),
                )
            )
        else:
            future.set_result(message.get("result"))

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)


_END = object()


def choose_option(options: Sequence[Any], allow: bool) -> str | None:
    """The permission option for the decision: once if offered, else always."""
    wanted = ("allow_once", "allow_always") if allow else ("reject_once", "reject_always")
    for kind in wanted:
        for option in options:
            if isinstance(option, dict) and option.get("kind") == kind and option.get("optionId"):
                return str(option["optionId"])
    return None


def decide(
    tool_call: Mapping[str, Any], workdir: Path, settings: HarnessSettings
) -> tuple[bool, str]:
    """The run's permission policy: (allow, why)."""
    kind = str(tool_call.get("kind") or "other")
    if settings.permission == "all":
        return True, "the run allows every tool"
    locations = [
        Path(str(loc.get("path")))
        for loc in tool_call.get("locations") or []
        if isinstance(loc, dict) and loc.get("path")
    ]
    outside = [str(p) for p in locations if not (p.is_absolute() and path_inside(p, workdir))]
    if kind in READ_KINDS | EDIT_KINDS:
        if outside:
            return False, f"{kind} outside the worktree: {', '.join(outside[:3])}"
        return True, f"{kind} inside the worktree"
    if kind == "switch_mode":
        return True, "mode switch"
    if kind == "execute":
        prefixes = allowed_test_prefixes(settings)
        raw = tool_call.get("rawInput") or {}
        command = raw.get("command") if isinstance(raw, dict) else None
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        command = str(command or tool_call.get("title") or "")
        if any(command.strip().startswith(prefix) for prefix in prefixes):
            return True, "the test command"
        return False, "commands other than the test command need the 'all' permission"
    if kind == "fetch":
        return False, "web access: the run's egress stays with the proxy"
    return False, f"tool kind {kind!r} needs the 'all' permission"


def _content_text(content: Any) -> str:
    """Text of an ACP content block or of a tool call's content list."""
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text") or "")
        if content.get("type") == "content":
            return _content_text(content.get("content"))
        if content.get("type") == "diff":
            return f"diff {content.get('path')}"
        return ""
    if isinstance(content, list):
        return "\n".join(t for t in (_content_text(c) for c in content) if t)
    return ""


class AcpRun(RunHandle):
    """One prompt turn of an ACP agent, from spawning it to ending its process."""

    def __init__(
        self,
        adapter: AcpAdapter,
        argv: Sequence[str],
        env: Mapping[str, str],
        workdir: Path,
        task: HarnessTask,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> None:
        self.adapter = adapter
        self.argv = list(argv)
        self.workdir = workdir.resolve()
        self.task = task
        self.endpoint = endpoint
        self.settings = settings
        self._queue: queue.Queue[Any] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=STDERR_TAIL)
        self._cancelled = threading.Event()
        self._result: RunResult | None = None
        self._result_lock = threading.Lock()
        self._loading = False
        self.session_id: str | None = None
        self.agent: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.stop_reason: str | None = None
        self.error: str | None = None
        # The agent's messages, each a list of text chunks: a new message starts at a new
        # `messageId` or after a tool call.
        self.messages: list[list[str]] = []
        self._message_id: Any = None
        self._new_message = True
        self.config_options: list[Any] = []
        self.tool_calls = 0
        self.usage: dict[str, int] = {}
        self._transcript: IO[str] | None = None
        if settings.transcript is not None:
            settings.transcript.parent.mkdir(parents=True, exist_ok=True)
            self._transcript = settings.transcript.open("w", encoding="utf-8", newline="\n")
        self.proc = subprocess.Popen(
            self.argv,
            cwd=str(self.workdir),
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **popen_kwargs(),
        )
        assert self.proc.stdout is not None and self.proc.stdin is not None
        self.peer = JsonRpcPeer(
            self.proc.stdout,
            self.proc.stdin,
            on_request=self._on_request,
            on_notification=self._on_notification,
            transcript=self._transcript,
        )
        self.peer.start()
        threading.Thread(target=self._read_stderr, daemon=True, name="acp-stderr").start()
        self._driver = threading.Thread(target=self._drive, daemon=True, name="acp-driver")
        self._driver.start()

    # -- the protocol --------------------------------------------------------------------------

    def _emit(self, event_kind: str, /, **data: Any) -> None:
        self._queue.put(HarnessEvent(event_kind, data))

    def _drive(self) -> None:
        try:
            self._initialize()
            self._open_session()
            if self._cancelled.is_set():
                self.stop_reason = "cancelled"
                return
            self._select_model()
            result = self.peer.request(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "prompt": [{"type": "text", "text": self.task.prompt}],
                },
            )
            result = result if isinstance(result, dict) else {}
            self.stop_reason = str(result.get("stopReason") or "end_turn")
            usage = result.get("usage")
            if isinstance(usage, dict):
                for key, name in (
                    ("inputTokens", "input_tokens"),
                    ("outputTokens", "output_tokens"),
                    ("cachedReadTokens", "cached_input_tokens"),
                    ("thoughtTokens", "reasoning_tokens"),
                ):
                    if isinstance(usage.get(key), int):
                        self.usage[name] = int(usage[key])
            self._emit("result", stop_reason=self.stop_reason, usage=dict(self.usage))
        except AcpError as exc:
            self.error = f"{exc.message} (code {exc.code})"
            self._emit("error", message=exc.message[:500], code=exc.code)
        except Exception as exc:  # noqa: BLE001 - every failure ends the run with a result
            self.error = f"{type(exc).__name__}: {exc}"
            self._emit("error", message=self.error[:500])
        finally:
            self._shut_down()
            self._queue.put(_END)

    def _initialize(self) -> None:
        answer = self.peer.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": False,
                },
                "clientInfo": {"name": "stepswitch", "title": "Stepswitch", "version": __version__},
            },
            timeout=SETUP_TIMEOUT_SECONDS,
        )
        answer = answer if isinstance(answer, dict) else {}
        version = answer.get("protocolVersion")
        if version != PROTOCOL_VERSION:
            raise AcpError(
                INTERNAL_ERROR, f"the agent speaks ACP protocol version {version!r}, not 1"
            )
        self.capabilities = dict(answer.get("agentCapabilities") or {})
        self.agent = dict(answer.get("agentInfo") or {})
        self._emit(
            "start",
            agent=self.agent.get("name"),
            version=self.agent.get("version"),
            load_session=bool(self.capabilities.get("loadSession")),
            auth_methods=len(answer.get("authMethods") or []),
        )

    def _open_session(self) -> None:
        cwd = str(self.workdir)
        if self.task.resume and self.capabilities.get("loadSession"):
            self._loading = True
            try:
                self.peer.request(
                    "session/load",
                    {"sessionId": self.task.resume, "cwd": cwd, "mcpServers": []},
                    timeout=SETUP_TIMEOUT_SECONDS,
                )
                self.session_id = self.task.resume
                self._emit("log", note="resumed the previous session", session_id=self.session_id)
                return
            except AcpError as exc:
                self._emit("log", note=f"session/load failed ({exc.message}): a new session")
            finally:
                self._loading = False
        answer = self.peer.request(
            "session/new", {"cwd": cwd, "mcpServers": []}, timeout=SETUP_TIMEOUT_SECONDS
        )
        answer = answer if isinstance(answer, dict) else {}
        if not answer.get("sessionId"):
            raise AcpError(INTERNAL_ERROR, "session/new returned no sessionId")
        self.session_id = str(answer["sessionId"])
        self.config_options = list(answer.get("configOptions") or [])
        self._emit("log", note="session opened", session_id=self.session_id)

    def _select_model(self) -> None:
        wanted = self.adapter.model_value(self.endpoint)
        for option in self.config_options:
            if not isinstance(option, dict) or option.get("category") != "model":
                continue
            values = []
            for item in option.get("options") or []:
                if isinstance(item, dict) and "value" in item:
                    values.append(item["value"])
                elif isinstance(item, dict):  # a group of options
                    values += [
                        o.get("value") for o in item.get("options") or [] if isinstance(o, dict)
                    ]
            if wanted in values and option.get("currentValue") != wanted:
                self.peer.request(
                    "session/set_config_option",
                    {"sessionId": self.session_id, "configId": option.get("id"), "value": wanted},
                    timeout=SETUP_TIMEOUT_SECONDS,
                )
                self._emit("log", note=f"model option set to {wanted}")
            return

    def _on_notification(self, method: str, params: Any) -> None:
        if method != "session/update" or not isinstance(params, dict) or self._loading:
            return
        update = params.get("update")
        if not isinstance(update, dict):
            return
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = _content_text(update.get("content"))
            message_id = update.get("messageId")
            if message_id is not None and message_id != self._message_id:
                self._new_message = True
                self._message_id = message_id
            if text:
                if self._new_message or not self.messages:
                    self.messages.append([])
                    self._new_message = False
                self.messages[-1].append(text)
                self._emit("text", text=text[:2000])
        elif kind == "agent_thought_chunk":
            self._emit("thinking", chars=len(_content_text(update.get("content"))))
        elif kind == "tool_call":
            self.tool_calls += 1
            self._new_message = True
            raw = update.get("rawInput")
            self._emit(
                "tool_call",
                id=update.get("toolCallId"),
                name=update.get("title") or update.get("kind"),
                kind=update.get("kind"),
                arguments=json.dumps(raw, ensure_ascii=False)[:1000] if raw is not None else "",
            )
        elif kind == "tool_call_update":
            status = update.get("status")
            if status in {"completed", "failed"}:
                self._emit(
                    "tool_result",
                    id=update.get("toolCallId"),
                    error=status == "failed",
                    text=_content_text(update.get("content"))[:1000],
                )
        elif kind == "plan":
            entries = [e for e in update.get("entries") or [] if isinstance(e, dict)]
            self._emit(
                "plan",
                entries=[
                    {"content": str(e.get("content"))[:200], "status": e.get("status")}
                    for e in entries[:20]
                ],
            )
        elif kind == "usage_update":
            self._emit("usage", used=update.get("used"), size=update.get("size"))
        elif kind in {"compaction_update", "compaction_summary_chunk"}:
            self._emit("compaction", update=kind)
        else:
            self._emit("log", update=kind)

    def _on_request(self, method: str, params: Any) -> Any:
        params = params if isinstance(params, dict) else {}
        if method == "session/request_permission":
            return self._permission(params)
        if method == "fs/read_text_file":
            return self._read_file(params)
        if method == "fs/write_text_file":
            return self._write_file(params)
        raise AcpError(METHOD_NOT_FOUND, f"method not found: {method}")

    def _permission(self, params: dict[str, Any]) -> dict[str, Any]:
        found = params.get("toolCall")
        tool_call: dict[str, Any] = found if isinstance(found, dict) else {}
        options = params.get("options") or []
        if self._cancelled.is_set():
            self._emit("permission", tool=tool_call.get("title"), allowed=False, why="cancelled")
            return {"outcome": {"outcome": "cancelled"}}
        allowed, why = decide(tool_call, self.workdir, self.settings)
        option = choose_option(options, allowed)
        self._emit(
            "permission",
            tool=tool_call.get("title") or tool_call.get("kind"),
            kind=tool_call.get("kind"),
            allowed=allowed,
            why=why,
        )
        if option is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": option}}

    def _inside(self, params: dict[str, Any]) -> Path:
        raw = params.get("path")
        path = Path(str(raw)) if raw else None
        if path is None or not path.is_absolute() or not path_inside(path, self.workdir):
            raise AcpError(INVALID_PARAMS, f"{raw!r} is not an absolute path inside the worktree")
        return path

    def _read_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._inside(params)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise AcpError(INTERNAL_ERROR, f"cannot read {path.name}: {exc}") from exc
        line, limit = params.get("line"), params.get("limit")
        if isinstance(line, int) or isinstance(limit, int):
            lines = text.splitlines(keepends=True)
            start = max(int(line) - 1, 0) if isinstance(line, int) else 0
            end = start + int(limit) if isinstance(limit, int) else len(lines)
            text = "".join(lines[start:end])
        return {"content": text}

    def _write_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._inside(params)
        content = params.get("content")
        if not isinstance(content, str):
            raise AcpError(INVALID_PARAMS, "content must be a string")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        return {}

    # -- process ---------------------------------------------------------------------------

    def _read_stderr(self) -> None:
        log: IO[str] | None = None
        try:
            if self.settings.log is not None:
                self.settings.log.parent.mkdir(parents=True, exist_ok=True)
                log = self.settings.log.open("a", encoding="utf-8", newline="\n")
            assert self.proc.stderr is not None
            for raw in iter(self.proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                self._stderr.append(line)
                if log is not None:
                    log.write(line + "\n")
                    log.flush()
        except (OSError, ValueError):
            pass
        finally:
            if log is not None:
                log.close()

    def _shut_down(self) -> None:
        # The client ends a session by closing the agent's stdin and ending its process.
        with contextlib.suppress(OSError, ValueError):
            assert self.proc.stdin is not None
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=CANCEL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            kill_tree(self.proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=CANCEL_GRACE_SECONDS)
        self.peer.join(CANCEL_GRACE_SECONDS)
        if self._transcript is not None:
            with contextlib.suppress(OSError, ValueError):
                self._transcript.close()

    # -- RunHandle -------------------------------------------------------------------------

    def events(self) -> Iterator[HarnessEvent]:
        while True:
            item = self._queue.get()
            if item is _END:
                return
            yield item

    def cancel(self) -> None:
        if self._cancelled.is_set():
            return
        self._cancelled.set()
        if self.session_id is not None and not self.peer.closed.is_set():
            with contextlib.suppress(AcpError):
                self.peer.notify("session/cancel", {"sessionId": self.session_id})
        threading.Thread(target=self._kill_later, daemon=True).start()

    def _kill_later(self) -> None:
        self._driver.join(CANCEL_GRACE_SECONDS)
        if self._driver.is_alive():
            kill_tree(self.proc)

    def result(self, timeout: float | None = None) -> RunResult:
        with self._result_lock:
            if self._result is not None:
                return self._result
            self._driver.join(timeout)
            if self._driver.is_alive():
                kill_tree(self.proc)
                self._driver.join(CANCEL_GRACE_SECONDS * 2)
            self._result = self._summarize()
            return self._result

    def _summarize(self) -> RunResult:
        version = str(self.agent.get("version")) if self.agent.get("version") else None
        common: dict[str, Any] = {
            "session_id": self.session_id,
            "tool_calls": self.tool_calls,
            "usage": dict(self.usage),
            "exit_code": self.proc.poll(),
            "version": version,
        }
        summary = "".join(self.messages[-1])[-4000:] if self.messages else ""
        if self.error is not None and self.stop_reason is None:
            if self._cancelled.is_set():
                return RunResult(
                    "stopped", "cancelled", summary=summary, detail=self.error, **common
                )
            stderr = "\n".join(self._stderr)
            detail = self.error + (f"; stderr: {tail(stderr)}" if stderr else "")
            return RunResult("failed", "error", summary=summary, detail=detail, **common)
        reason = self.stop_reason or "end_turn"
        status, stop = STOP_REASONS.get(reason, ("failed", "error"))
        detail = "" if status == "done" else f"stop reason {reason}"
        return RunResult(status, stop, summary=summary, detail=detail, turns=1, **common)  # type: ignore[arg-type]


class AcpAdapter(HarnessAdapter):
    """Any agent that speaks ACP v1 over stdio: `[harness.acp] command = [...]`."""

    name = "acp"
    title = "ACP agent"
    binary = ""
    support = "any agent that speaks ACP v1 over stdio (configure its command)"
    # The common opt-out convention; agent-specific switches go in `[harness.acp] env`.
    offline_env = {"DO_NOT_TRACK": "1"}
    resumable = True
    prompt_on_stdin = False
    # Arguments that start the agent's ACP mode.
    acp_args: tuple[str, ...] = ()

    def executable(self, settings: HarnessSettings) -> list[str] | None:
        if not settings.command and not self.binary:
            return None
        return super().executable(settings)

    def model_value(self, endpoint: HarnessEndpoint) -> str:
        """The value a session's `model` config option takes for the endpoint's main slot."""
        return endpoint.slot("main")

    def command(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> list[str]:
        return [*self.acp_args, *settings.extra_args]

    def environment(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> dict[str, str]:
        env = {
            "STEPSWITCH_PROXY_URL": endpoint.base_url,
            "STEPSWITCH_PROXY_TOKEN": endpoint.token,
            "STEPSWITCH_SESSION": endpoint.session,
            "STEPSWITCH_MODEL": endpoint.slot("main"),
        }
        if endpoint.context:
            env["STEPSWITCH_CONTEXT_TOKENS"] = str(endpoint.context)
        return env

    def start(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> RunHandle:
        command = self.executable(settings)
        if command is None:
            raise HarnessMissing(self)
        argv = [*command, *self.command(task, workdir, endpoint, settings)]
        env = self.full_environment(task, workdir, endpoint, settings)
        return AcpRun(self, argv, env, workdir, task, endpoint, settings)
