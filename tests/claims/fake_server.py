"""Scripted model servers for claim tests.

`ScriptedServer` is a real HTTP server on 127.0.0.1 that speaks the chat-completions format and
behaves like a model that knows how to fix the calc-bug toy repository. It decides from the
conversation itself, so the same script works whichever server the router sends a step to:

* without tools: a short plan, an approval (when the system prompt says `Step: review`) or a
  summary;
* with tools: grep for `def add`, read calc.py, replace the buggy line using the anchor shown in
  the read result, run the tests, then finish.

Every request is recorded with the step type named in the system prompt.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

STEP_RE = re.compile(r"^Step: ([a-z-]+)\s*$", re.MULTILINE)
ANCHORED_LINE_RE = re.compile(r"^(\d+):([0-9A-Za-z]+)\|(.*)$", re.MULTILINE)


def _system_text(messages: list[dict[str, Any]]) -> str:
    parts = []
    for message in messages:
        if message.get("role") in ("system", "developer"):
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                parts.extend(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return "\n".join(parts)


def _calls_and_results(messages: list[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
    """Tool names called so far, and the latest result text per tool name."""
    names: list[str] = []
    by_id: dict[str, str] = {}
    results: dict[str, str] = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                name = call["function"]["name"]
                names.append(name)
                by_id[call.get("id", "")] = name
        elif message.get("role") == "tool":
            name = by_id.get(message.get("tool_call_id", ""), "")
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
            results[name] = str(content or "")
    return names, results


def calc_fixer(body: dict[str, Any]) -> tuple[str | None, tuple[str, dict[str, Any]] | None]:
    """(text, tool call) for one request."""
    messages = body.get("messages", [])
    step = STEP_RE.search(_system_text(messages))
    step_type = step.group(1) if step else ""
    if not body.get("tools"):
        if step_type == "plan":
            return "Plan: find add() in calc.py, fix its operator, then run the tests.", None
        if step_type == "review":
            return "APPROVE. The change makes add() return the sum and the tests pass.", None
        return "Fixed add() in calc.py: it returned a - b instead of a + b. Tests pass.", None
    called, results = _calls_and_results(messages)
    if "grep" not in called:
        return None, ("grep", {"pattern": "def add"})
    if "read" not in called:
        return None, ("read", {"path": "calc.py"})
    if "edit" not in called:
        anchor = None
        for match in ANCHORED_LINE_RE.finditer(results.get("read", "")):
            if match.group(3).strip() == "return a - b":
                anchor = f"{match.group(1)}:{match.group(2)}"
                break
        if anchor is None:
            return "I could not find the buggy line.", None
        edit = {"op": "replace", "start": anchor, "text": "    return a + b"}
        return None, ("edit", {"path": "calc.py", "edits": [edit]})
    if "test" not in called:
        return None, ("test", {})
    return None, ("finish", {"summary": "Fixed add() in calc.py; the tests pass."})


class ScriptedServer:
    """A chat-completions server on a free loopback port, running in a background thread."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> ScriptedServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def _reply(self, body: dict[str, Any]) -> dict[str, Any]:
        text, call = calc_fixer(body)
        step = STEP_RE.search(_system_text(body.get("messages", [])))
        with self._lock:
            index = len(self.requests)
            self.requests.append(
                {
                    "step_type": step.group(1) if step else None,
                    "tools": [t["function"]["name"] for t in body.get("tools") or []],
                    "reply": call[0] if call else "text",
                }
            )
        message: dict[str, Any] = {"role": "assistant", "content": text}
        finish = "stop"
        if call is not None:
            message["tool_calls"] = [
                {
                    "id": f"call_{self.model}_{index}",
                    "type": "function",
                    "function": {"name": call[0], "arguments": json.dumps(call[1])},
                }
            ]
            finish = "tool_calls"
        prompt = sum(len(json.dumps(m)) for m in body.get("messages", [])) // 4
        completion = len(json.dumps(message)) // 4
        return {
            "id": f"chatcmpl-{self.model}-{index}",
            "object": "chat.completion",
            "created": 0,
            "model": self.model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        }

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                if self.path.rstrip("/").endswith("/models"):
                    self._send(200, {"object": "list", "data": [{"id": server.model}]})
                else:
                    self._send(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not self.path.endswith("/chat/completions"):
                    self._send(404, {"error": {"message": "not found"}})
                    return
                if body.get("stream"):
                    self._send(400, {"error": {"message": "streaming not scripted"}})
                    return
                self._send(200, server._reply(body))

        return Handler
