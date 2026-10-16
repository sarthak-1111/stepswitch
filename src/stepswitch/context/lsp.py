"""A thin Language Server Protocol client for language servers the user installed.

Operations: go to definition, find references, hover and diagnostics for a file position.
One server process runs per language and per project root (the nearest directory with one of
the server's root markers), started on first use and shut down with `shutdown` and `exit` on
close. Every request has a timeout; a server that does not answer in time is reported, not
waited for.

Launch commands come only from configuration: `[lsp.<language>] command = [...]` in the merged
config, or registry files `registry/lsp/*.toml` holding `language`, `extensions`, `command`
and `root_markers`. Servers are never downloaded; a missing program is a clear error.

The protocol is spoken directly (JSON-RPC 2.0 with Content-Length framing over stdio, LSP 3.17
messages as plain dicts) instead of through pygls: its client is asyncio-based (a synchronous
tool call would need an event-loop thread), does not drain the server's stderr pipe and waits
without a timeout when stopping. A small threaded client avoids all three and adds no
dependencies.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import tomllib
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any
from urllib.parse import unquote, urlparse

from stepswitch import paths
from stepswitch.context import proc

DEFAULT_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py", ".pyi"),
    "typescript": (".ts", ".tsx", ".mts", ".cts"),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "go": (".go",),
    "rust": (".rs",),
    "java": (".java",),
    "c": (".c", ".h"),
    "cpp": (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
    "ruby": (".rb",),
}
# LSP languageId values that differ from the language name.
LANGUAGE_IDS = {".tsx": "typescriptreact", ".jsx": "javascriptreact"}
# Toolchain locations (paths, never secrets) that language servers commonly need.
SERVER_PASS_THROUGH = (
    "GOPATH",
    "GOROOT",
    "GOFLAGS",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "JAVA_HOME",
    "NODE_PATH",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONPATH",
)
SEVERITIES = {1: "error", 2: "warning", 3: "info", 4: "hint"}
ACTIONS = ("defs", "refs", "hover", "diagnostics")


class LspError(RuntimeError):
    """A language-server problem, phrased for the model."""


class LspNotConfigured(LspError):
    """No server is configured for a file type."""


class LspTimeout(LspError):
    pass


@dataclass(frozen=True)
class ServerSpec:
    language: str
    command: tuple[str, ...]
    extensions: tuple[str, ...]
    root_markers: tuple[str, ...] = ()
    initialization_options: Any = None
    settings: Any = None
    env: tuple[tuple[str, str], ...] = ()
    source: str = "config"


@dataclass(frozen=True)
class Location:
    path: str  # POSIX relative to the root when inside it, else absolute
    line: int  # 1-based
    column: int  # 1-based, in characters
    end_line: int
    end_column: int
    inside: bool = True


@dataclass(frozen=True)
class Diagnostic:
    line: int
    column: int
    end_line: int
    end_column: int
    severity: str
    message: str
    source: str | None = None
    code: str | None = None


# --------------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------------


def _command(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(shlex.split(value, posix=sys.platform != "win32"))
    if isinstance(value, list | tuple) and all(isinstance(v, str) for v in value):
        return tuple(value)
    return ()


def _spec(
    language: str, data: Mapping[str, Any], source: str, base: ServerSpec | None
) -> ServerSpec:
    command = _command(data.get("command")) or (base.command if base else ())
    extensions = data.get("extensions")
    if isinstance(extensions, list) and extensions:
        exts = tuple(e if e.startswith(".") else f".{e}" for e in map(str, extensions))
    else:
        exts = base.extensions if base else DEFAULT_EXTENSIONS.get(language, ())
    markers = data.get("root_markers")
    env = data.get("env")
    return ServerSpec(
        language=language,
        command=command,
        extensions=tuple(e.lower() for e in exts),
        root_markers=(
            tuple(map(str, markers))
            if isinstance(markers, list)
            else (base.root_markers if base else ())
        ),
        initialization_options=data.get(
            "initialization_options", base.initialization_options if base else None
        ),
        settings=data.get("settings", base.settings if base else None),
        env=(
            tuple((str(k), str(v)) for k, v in env.items())
            if isinstance(env, Mapping)
            else (base.env if base else ())
        ),
        source=source,
    )


def load_specs(
    config: Any = None, registry_dirs: Iterable[Path] | None = None
) -> dict[str, ServerSpec]:
    """Server specs by language: registry files first, then `[lsp.<language>]` from config."""
    specs: dict[str, ServerSpec] = {}
    dirs = paths.registry_dirs() if registry_dirs is None else list(registry_dirs)
    for directory in dirs:
        for file in sorted((directory / "lsp").glob("*.toml")):
            try:
                with file.open("rb") as handle:
                    data = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError):
                continue
            language = str(data.get("language") or file.stem)
            specs[language] = _spec(language, data, f"registry:{file.name}", specs.get(language))
    section: Any = None
    if config is not None:
        extra = getattr(config, "model_extra", None) or {}
        section = extra.get("lsp") if isinstance(extra, Mapping) else None
        if section is None and isinstance(config, Mapping):
            section = config.get("lsp")
    if isinstance(section, Mapping):
        for language, data in section.items():
            if isinstance(data, Mapping):
                specs[str(language)] = _spec(
                    str(language), data, "config", specs.get(str(language))
                )
    return {language: spec for language, spec in specs.items() if spec.command}


# --------------------------------------------------------------------------------------------
# Positions and URIs
# --------------------------------------------------------------------------------------------


def to_lsp_character(text: str, column: int, encoding: str = "utf-16") -> int:
    """A 1-based character column in `text` as an LSP character offset."""
    prefix = text[: max(0, column - 1)]
    if encoding == "utf-8":
        return len(prefix.encode("utf-8", errors="surrogatepass"))
    if encoding == "utf-32":
        return len(prefix)
    return len(prefix.encode("utf-16-le", errors="surrogatepass")) // 2


def from_lsp_character(text: str, character: int, encoding: str = "utf-16") -> int:
    """An LSP character offset in `text` as a 1-based character column."""
    if encoding == "utf-32":
        return min(character, len(text)) + 1
    unit = 1 if encoding == "utf-8" else 2
    codec = "utf-8" if encoding == "utf-8" else "utf-16-le"
    data = text.encode(codec, errors="surrogatepass")[: character * unit]
    return len(data.decode(codec, errors="ignore")) + 1


def path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()


def uri_to_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    raw = unquote(parsed.path)
    if sys.platform == "win32":
        if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        if parsed.netloc:
            raw = f"//{parsed.netloc}{raw}"
    elif parsed.netloc and parsed.netloc != "localhost":
        raw = f"//{parsed.netloc}{raw}"
    return Path(raw)


def _key(path: Path) -> str:
    text = str(path.resolve())
    return text.lower() if sys.platform == "win32" else text


# --------------------------------------------------------------------------------------------
# JSON-RPC over stdio
# --------------------------------------------------------------------------------------------


class _Pending:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None
        self.error: dict[str, Any] | None = None


class JsonRpcConnection:
    """JSON-RPC 2.0 with LSP base-protocol framing over a child process's stdin and stdout."""

    def __init__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None,
        on_notification: Callable[[str, Any], None],
        on_request: Callable[[str, Any], Any],
    ) -> None:
        self._on_notification = on_notification
        self._on_request = on_request
        self._proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=None if env is None else dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **proc.popen_kwargs(),
        )
        self._write_lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._ids = itertools.count(1)
        self.stderr_tail: deque[str] = deque(maxlen=40)
        self.closed = threading.Event()
        assert self._proc.stdout is not None and self._proc.stderr is not None
        threading.Thread(target=self._read_loop, args=(self._proc.stdout,), daemon=True).start()
        threading.Thread(target=self._drain, args=(self._proc.stderr,), daemon=True).start()

    @property
    def pid(self) -> int:
        return self._proc.pid

    def alive(self) -> bool:
        return self._proc.poll() is None and not self.closed.is_set()

    def _send(self, message: dict[str, Any]) -> None:
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        stdin = self._proc.stdin
        if stdin is None:
            raise LspError("the language server's input is closed")
        with self._write_lock:
            try:
                stdin.write(frame)
                stdin.flush()
            except (OSError, ValueError) as exc:
                raise LspError(f"the language server stopped accepting input: {exc}") from exc

    def notify(self, method: str, params: Any = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: Any, timeout: float) -> Any:
        request_id = next(self._ids)
        pending = _Pending()
        with self._pending_lock:
            self._pending[request_id] = pending
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:  # JSON-RPC params are an object or array, or left out
            message["params"] = params
        try:
            self._send(message)
        except LspError:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise
        if not pending.event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            with contextlib.suppress(LspError):
                self.notify("$/cancelRequest", {"id": request_id})
            raise LspTimeout(f"the language server did not answer {method} within {timeout:g} s")
        if pending.error is not None:
            code = pending.error.get("code")
            detail = f"{method} failed ({code}): {pending.error.get('message', '')}"
            tail = " | ".join(list(self.stderr_tail)[-3:])
            if code == -32099 and tail:
                detail += f"; its last output: {tail}"
            raise LspError(detail)
        return pending.result

    def _read_loop(self, stream: IO[bytes]) -> None:
        try:
            while True:
                length = None
                while True:
                    header = stream.readline()
                    if not header:
                        return
                    text = header.decode("ascii", errors="replace").strip()
                    if not text:
                        if length is not None:
                            break
                        continue
                    name, _, value = text.partition(":")
                    if name.strip().lower() == "content-length":
                        length = int(value.strip())
                body = b""
                while len(body) < length:
                    chunk = stream.read(length - len(body))
                    if not chunk:
                        return
                    body += chunk
                try:
                    message = json.loads(body.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        except (OSError, ValueError):
            return
        finally:
            self.closed.set()
            with self._pending_lock:
                waiting = list(self._pending.values())
                self._pending.clear()
            for pending in waiting:
                pending.error = {"code": -32099, "message": "the language server exited"}
                pending.event.set()

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if isinstance(method, str):
            if "id" in message:  # a request from the server
                reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
                try:
                    reply["result"] = self._on_request(method, message.get("params"))
                except KeyError:
                    reply["error"] = {"code": -32601, "message": f"method not found: {method}"}
                except Exception as exc:  # noqa: BLE001 - never let a handler stop the reader
                    reply["error"] = {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}
                with contextlib.suppress(LspError):
                    self._send(reply)
            else:
                with contextlib.suppress(Exception):
                    self._on_notification(method, message.get("params"))
            return
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        with self._pending_lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        error = message.get("error")
        if isinstance(error, dict):
            pending.error = error
        else:
            pending.result = message.get("result")
        pending.event.set()

    def _drain(self, stream: IO[bytes]) -> None:
        with contextlib.suppress(OSError, ValueError):
            for line in stream:
                self.stderr_tail.append(line.decode("utf-8", errors="replace").rstrip())

    def close(self, timeout: float = 3.0) -> None:
        with contextlib.suppress(OSError, ValueError):
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill_tree(self._proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._proc.wait(timeout=5)
        self.closed.set()


# --------------------------------------------------------------------------------------------
# One server
# --------------------------------------------------------------------------------------------

CLIENT_CAPABILITIES: dict[str, Any] = {
    "general": {"positionEncodings": ["utf-16"]},
    "textDocument": {
        "synchronization": {"dynamicRegistration": False, "didSave": False},
        "definition": {"dynamicRegistration": False, "linkSupport": True},
        "references": {"dynamicRegistration": False},
        "hover": {"dynamicRegistration": False, "contentFormat": ["plaintext", "markdown"]},
        "publishDiagnostics": {"relatedInformation": False, "versionSupport": True},
        "diagnostic": {"dynamicRegistration": False},
    },
    "workspace": {"configuration": True, "workspaceFolders": True},
    "window": {"workDoneProgress": False},
}


@dataclass
class _OpenDocument:
    version: int
    text: str
    synced_at: int  # publish sequence number when the server got this text


@dataclass
class _Published:
    sequence: int
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


class LspClient:
    """One language server for one project root."""

    def __init__(
        self,
        spec: ServerSpec,
        root: Path,
        *,
        display_root: Path | None = None,
        request_timeout: float = 10.0,
        start_timeout: float = 30.0,
        diagnostics_wait: float = 2.0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.spec = spec
        self.root = root.resolve()
        self.display_root = (display_root or root).resolve()
        self.request_timeout = request_timeout
        self.start_timeout = start_timeout
        self.diagnostics_wait = diagnostics_wait
        self._env = env
        self._conn: JsonRpcConnection | None = None
        self.capabilities: dict[str, Any] = {}
        self.encoding = "utf-16"
        self._open: dict[str, _OpenDocument] = {}
        self._published: dict[str, _Published] = {}
        self._sequence = 0
        self._cond = threading.Condition()
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------------------------

    def _argv(self) -> list[str]:
        program, *rest = self.spec.command
        env_path = (self._env or os.environ).get("PATH", "")
        if Path(program).is_absolute():
            resolved = program if Path(program).exists() else None
        elif Path(program).name != program:
            candidate = (self.root / program).resolve()
            resolved = str(candidate) if candidate.exists() else None
        else:
            resolved = proc.which(program, env_path)
        if resolved is None:
            raise LspError(
                f"the {self.spec.language} language server `{program}` is not installed (not "
                f"found on PATH). Install it, or change `[lsp.{self.spec.language}] command`. "
                "Stepswitch never downloads language servers."
            )
        return [resolved, *rest]

    def start(self) -> None:
        if self._conn is not None and self._conn.alive():
            return
        argv = self._argv()
        try:
            self._conn = JsonRpcConnection(
                argv,
                cwd=self.root,
                env=self._env,
                on_notification=self._notification,
                on_request=self._server_request,
            )
        except OSError as exc:
            raise LspError(f"could not start `{argv[0]}`: {exc}") from exc
        params: dict[str, Any] = {
            "processId": os.getpid(),
            "clientInfo": {"name": "stepswitch"},
            "rootUri": path_to_uri(self.root),
            "rootPath": str(self.root),
            "workspaceFolders": [{"uri": path_to_uri(self.root), "name": self.root.name}],
            "capabilities": CLIENT_CAPABILITIES,
            "trace": "off",
        }
        if self.spec.initialization_options is not None:
            params["initializationOptions"] = self.spec.initialization_options
        try:
            result = self._conn.request("initialize", params, self.start_timeout)
        except LspError as exc:
            tail = " | ".join(list(self._conn.stderr_tail)[-5:])
            self.close()
            raise LspError(f"{exc}{'; server said: ' + tail if tail else ''}") from exc
        capabilities = (result or {}).get("capabilities") if isinstance(result, dict) else None
        self.capabilities = capabilities if isinstance(capabilities, dict) else {}
        encoding = self.capabilities.get("positionEncoding")
        self.encoding = encoding if encoding in {"utf-8", "utf-16", "utf-32"} else "utf-16"
        self._conn.notify("initialized", {})

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        if conn.alive():
            with contextlib.suppress(LspError):
                conn.request("shutdown", None, 3.0)
            with contextlib.suppress(LspError):
                conn.notify("exit")
        conn.close()
        self._open.clear()

    def _connection(self) -> JsonRpcConnection:
        if self._conn is None or not self._conn.alive():
            self.start()
        assert self._conn is not None
        return self._conn

    # -- messages from the server ----------------------------------------------------------------

    def _notification(self, method: str, params: Any) -> None:
        if method != "textDocument/publishDiagnostics" or not isinstance(params, dict):
            return
        path = uri_to_path(str(params.get("uri", "")))
        if path is None:
            return
        diagnostics = params.get("diagnostics")
        with self._cond:
            self._sequence += 1
            self._published[_key(path)] = _Published(
                self._sequence, list(diagnostics) if isinstance(diagnostics, list) else []
            )
            self._cond.notify_all()

    def _server_request(self, method: str, params: Any) -> Any:
        if method == "workspace/configuration":
            items = params.get("items", []) if isinstance(params, dict) else []
            return [self._setting(item.get("section")) for item in items if isinstance(item, dict)]
        if method == "workspace/workspaceFolders":
            return [{"uri": path_to_uri(self.root), "name": self.root.name}]
        if method in {
            "client/registerCapability",
            "client/unregisterCapability",
            "window/workDoneProgress/create",
            "window/showMessageRequest",
            "workspace/diagnostic/refresh",
            "workspace/semanticTokens/refresh",
            "workspace/inlayHint/refresh",
            "workspace/codeLens/refresh",
        }:
            return None
        if method == "workspace/applyEdit":
            return {"applied": False, "failureReason": "this client applies no edits"}
        raise KeyError(method)

    def _setting(self, section: Any) -> Any:
        node = self.spec.settings
        if not isinstance(section, str) or not section:
            return node
        for part in section.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return None
            node = node[part]
        return node

    # -- documents ---------------------------------------------------------------------------

    def _sync(self, path: Path) -> tuple[str, str]:
        """Open or update `path` in the server; returns (uri, text)."""
        conn = self._connection()
        text = path.read_text(encoding="utf-8", errors="replace")
        uri = path_to_uri(path)
        key = _key(path)
        current = self._open.get(key)
        with self._cond:
            sequence = self._sequence
        if current is None:
            language_id = LANGUAGE_IDS.get(path.suffix.lower(), self.spec.language)
            conn.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": 1,
                        "text": text,
                    }
                },
            )
            self._open[key] = _OpenDocument(1, text, sequence)
        elif current.text != text:
            current.version += 1
            current.text = text
            current.synced_at = sequence
            conn.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": current.version},
                    "contentChanges": [{"text": text}],
                },
            )
        return uri, text

    def _position(self, text: str, line: int, column: int) -> dict[str, int]:
        lines = text.split("\n")
        if line < 1 or line > len(lines):
            raise LspError(f"line {line} is outside the file (it has {len(lines)} lines)")
        row = lines[line - 1].rstrip("\r")
        return {"line": line - 1, "character": to_lsp_character(row, column, self.encoding)}

    def _location(self, uri: str, rng: Any, cache: dict[str, list[str]]) -> Location | None:
        path = uri_to_path(uri)
        if path is None or not isinstance(rng, dict):
            return None
        start, end = rng.get("start") or {}, rng.get("end") or {}
        key = str(path)
        if key not in cache:
            try:
                cache[key] = path.read_text(encoding="utf-8", errors="replace").split("\n")
            except OSError:
                cache[key] = []
        rows = cache[key]

        def column(position: dict[str, Any]) -> int:
            line, character = int(position.get("line", 0)), int(position.get("character", 0))
            if 0 <= line < len(rows):
                return from_lsp_character(rows[line].rstrip("\r"), character, self.encoding)
            return character + 1

        resolved = path.resolve()
        inside = resolved.is_relative_to(self.display_root)
        shown = resolved.relative_to(self.display_root).as_posix() if inside else str(resolved)
        return Location(
            shown,
            int(start.get("line", 0)) + 1,
            column(start),
            int(end.get("line", start.get("line", 0))) + 1,
            column(end) if end else column(start),
            inside,
        )

    def _locations(self, result: Any) -> list[Location]:
        items = result if isinstance(result, list) else [result] if result else []
        cache: dict[str, list[str]] = {}
        found: list[Location] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if "targetUri" in item:
                rng = item.get("targetSelectionRange") or item.get("targetRange")
                location = self._location(str(item["targetUri"]), rng, cache)
            else:
                location = self._location(str(item.get("uri", "")), item.get("range"), cache)
            if location is not None and location not in found:
                found.append(location)
        return found

    # -- operations --------------------------------------------------------------------------

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        with self._lock:
            return self._connection().request(method, params, self.request_timeout)

    def definition(self, path: Path, line: int, column: int) -> list[Location]:
        uri, text = self._sync(path)
        position = self._position(text, line, column)
        params = {"textDocument": {"uri": uri}, "position": position}
        return self._locations(self._request("textDocument/definition", params))

    def references(self, path: Path, line: int, column: int) -> list[Location]:
        uri, text = self._sync(path)
        params = {
            "textDocument": {"uri": uri},
            "position": self._position(text, line, column),
            "context": {"includeDeclaration": True},
        }
        return self._locations(self._request("textDocument/references", params))

    def hover(self, path: Path, line: int, column: int) -> str:
        uri, text = self._sync(path)
        params = {"textDocument": {"uri": uri}, "position": self._position(text, line, column)}
        result = self._request("textDocument/hover", params)
        return hover_text(result.get("contents") if isinstance(result, dict) else None)

    def diagnostics(self, path: Path, wait: float | None = None) -> list[Diagnostic]:
        uri, text = self._sync(path)
        rows = text.split("\n")
        if self.capabilities.get("diagnosticProvider"):
            with contextlib.suppress(LspError):
                report = self._request("textDocument/diagnostic", {"textDocument": {"uri": uri}})
                if isinstance(report, dict) and isinstance(report.get("items"), list):
                    return [_diagnostic(d, rows, self.encoding) for d in report["items"]]
        key = _key(path)
        synced_at = self._open[key].synced_at
        deadline = time.monotonic() + (self.diagnostics_wait if wait is None else wait)
        with self._cond:
            while True:  # wait for a publish that follows our last sync
                published = self._published.get(key)
                if published is not None and published.sequence > synced_at:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            settle = min(0.3, max(0.0, deadline - time.monotonic()))
            if settle and self._published.get(key) is not None:
                self._cond.wait(settle)  # servers often publish twice: quick, then complete
            published = self._published.get(key)
        if published is None:
            return []
        return [_diagnostic(d, rows, self.encoding) for d in published.diagnostics]


def hover_text(contents: Any) -> str:
    """Plain text from MarkupContent, a MarkedString or a list of MarkedStrings."""
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents.strip()
    if isinstance(contents, list):
        return "\n\n".join(t for t in (hover_text(c) for c in contents) if t)
    if isinstance(contents, dict):
        value = contents.get("value")
        return str(value).strip() if value is not None else ""
    return str(contents)


def _diagnostic(raw: Any, rows: list[str], encoding: str) -> Diagnostic:
    data = raw if isinstance(raw, dict) else {}
    rng = data.get("range") or {}
    start, end = rng.get("start") or {}, rng.get("end") or {}

    def column(position: dict[str, Any]) -> int:
        line, character = int(position.get("line", 0)), int(position.get("character", 0))
        if 0 <= line < len(rows):
            return from_lsp_character(rows[line].rstrip("\r"), character, encoding)
        return character + 1

    code = data.get("code")
    return Diagnostic(
        int(start.get("line", 0)) + 1,
        column(start),
        int(end.get("line", start.get("line", 0))) + 1,
        column(end) if end else column(start),
        SEVERITIES.get(int(data.get("severity") or 1), "error"),
        str(data.get("message", "")).strip(),
        str(data["source"]) if data.get("source") else None,
        str(code) if code is not None else None,
    )


# --------------------------------------------------------------------------------------------
# Servers for a repository
# --------------------------------------------------------------------------------------------


class LspManager:
    """Starts one client per (language, project root) on first use; closes them all."""

    def __init__(
        self,
        root: Path,
        *,
        config: Any = None,
        registry_dirs: Iterable[Path] | None = None,
        specs: Mapping[str, ServerSpec] | None = None,
        request_timeout: float = 10.0,
        start_timeout: float = 30.0,
        diagnostics_wait: float = 2.0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.specs = dict(specs) if specs is not None else load_specs(config, registry_dirs)
        self.request_timeout = request_timeout
        self.start_timeout = start_timeout
        self.diagnostics_wait = diagnostics_wait
        self._env = env
        self._clients: dict[tuple[str, Path], LspClient] = {}
        self._lock = threading.Lock()

    def spec_for(self, path: Path) -> ServerSpec | None:
        suffix = path.suffix.lower()
        for spec in self.specs.values():
            if suffix in spec.extensions:
                return spec
        return None

    def _server_root(self, spec: ServerSpec, path: Path) -> Path:
        if spec.root_markers:
            for directory in [path.parent, *path.parent.parents]:
                if not directory.is_relative_to(self.root):
                    break
                if any((directory / marker).exists() for marker in spec.root_markers):
                    return directory
        return self.root

    def client_for(self, path: Path) -> LspClient:
        spec = self.spec_for(path)
        if spec is None:
            configured = ", ".join(sorted(self.specs)) or "none"
            raise LspNotConfigured(
                f"no language server is configured for {path.suffix or 'this file type'} files "
                f"(configured: {configured}). Add `[lsp.<language>] command = [...]` to the "
                "config, or use grep and repo_map instead."
            )
        server_root = self._server_root(spec, path)
        with self._lock:
            client = self._clients.get((spec.language, server_root))
            if client is None:
                env = dict(self._env) if self._env is not None else self._default_env()
                env.update(dict(spec.env))
                client = LspClient(
                    spec,
                    server_root,
                    display_root=self.root,
                    request_timeout=self.request_timeout,
                    start_timeout=self.start_timeout,
                    diagnostics_wait=self.diagnostics_wait,
                    env=env,
                )
                self._clients[(spec.language, server_root)] = client
        return client

    @staticmethod
    def _default_env() -> dict[str, str]:
        extra = {name: os.environ[name] for name in SERVER_PASS_THROUGH if name in os.environ}
        return proc.minimal_env(extra)

    def definition(self, path: Path, line: int, column: int) -> list[Location]:
        return self.client_for(path).definition(path, line, column)

    def references(self, path: Path, line: int, column: int) -> list[Location]:
        return self.client_for(path).references(path, line, column)

    def hover(self, path: Path, line: int, column: int) -> str:
        return self.client_for(path).hover(path, line, column)

    def diagnostics(self, path: Path, wait: float | None = None) -> list[Diagnostic]:
        return self.client_for(path).diagnostics(path, wait)

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()
