"""A stand-in for Claude Code's headless mode, for the harness-loop integration tests.

It talks to the proxy the way the harness does: the messages format on
`$ANTHROPIC_BASE_URL/v1/messages`, `Authorization: Bearer $ANTHROPIC_AUTH_TOKEN` and the
harness's session, agent and request-class headers. It offers one `Edit` tool, applies the edits
it is given in its working directory and prints the stream-json lines of a run, ending with a
`result`. The prompt is the argument after `-p`. Sessions are kept in `$CLAUDE_CONFIG_DIR`, so
`--resume` continues one. It refuses to run when the user's own key reached its environment or
no config directory was set.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path
from typing import Any

EDIT_TOOL = {
    "name": "Edit",
    "description": "Replace old_string with new_string in a file.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
}


def emit(item: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(item) + "\n")
    sys.stdout.flush()


def after(args: list[str], flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args else None


def fail(session: str, message: str) -> int:
    emit(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "session_id": session,
            "is_error": True,
            "num_turns": 0,
            "errors": [message],
        }
    )
    return 1


def post(body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "authorization": "Bearer " + os.environ["ANTHROPIC_AUTH_TOKEN"],
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            **headers,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def edit(arguments: dict[str, Any]) -> tuple[bool, str]:
    path = Path(arguments["file_path"])
    if not path.is_absolute():
        path = Path.cwd() / path
    text = path.read_text(encoding="utf-8")
    if arguments["old_string"] not in text:
        return False, "old_string not found in the file"
    path.write_text(text.replace(arguments["old_string"], arguments["new_string"], 1), "utf-8")
    return True, f"The file {path.name} has been updated."


def main() -> int:
    args = sys.argv[1:]
    prompt = after(args, "-p") or ""
    session = after(args, "--resume") or after(args, "--session-id") or str(uuid.uuid4())
    if os.environ.get("ANTHROPIC_API_KEY"):
        return fail(session, "the user's own key reached the harness")
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if not config_dir or not Path(config_dir).is_dir():
        return fail(session, "no CLAUDE_CONFIG_DIR")
    store = Path(config_dir) / "sessions" / f"{session}.json"
    messages = json.loads(store.read_text("utf-8")) if store.is_file() else []
    messages.append({"role": "user", "content": prompt})
    model = os.environ["ANTHROPIC_MODEL"]
    emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": session,
            "cwd": str(Path.cwd()),
            "model": model,
            "tools": ["Edit"],
            "mcp_servers": [],
            "permissionMode": after(args, "--permission-mode"),
            "claude_code_version": "0.0.0-fake",
        }
    )
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    max_turns = int(after(args, "--max-turns") or 50)
    headers = {
        "x-claude-code-session-id": session,
        "x-claude-code-agent-id": "main",
        "x-claude-code-request-class": "main",
    }
    turns = 0
    while turns < max_turns:
        turns += 1
        body = {
            "model": model,
            "max_tokens": 1024,
            "system": "You are a coding agent.",
            "messages": messages,
            "tools": [EDIT_TOOL],
        }
        reply = post(body, headers)
        for key in ("input_tokens", "output_tokens"):
            usage[key] += int((reply.get("usage") or {}).get(key) or 0)
        emit(
            {
                "type": "assistant",
                "session_id": session,
                "parent_tool_use_id": None,
                "message": reply,
            }
        )
        messages.append({"role": "assistant", "content": reply["content"]})
        calls = [block for block in reply["content"] if block.get("type") == "tool_use"]
        if not calls:
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_text(json.dumps(messages), "utf-8")
            text = "".join(b.get("text", "") for b in reply["content"] if b.get("type") == "text")
            emit(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": session,
                    "is_error": False,
                    "num_turns": turns,
                    "result": text,
                    "usage": usage,
                    "permission_denials": [],
                }
            )
            return 0
        results = []
        for call in calls:
            ok, output = edit(call["input"])
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": output,
                    "is_error": not ok,
                }
            )
        emit(
            {
                "type": "user",
                "session_id": session,
                "parent_tool_use_id": None,
                "message": {"role": "user", "content": results},
            }
        )
        messages.append({"role": "user", "content": results})
    emit(
        {
            "type": "result",
            "subtype": "error_max_turns",
            "session_id": session,
            "is_error": True,
            "num_turns": turns,
            "usage": usage,
            "errors": [f"Reached maximum number of turns ({max_turns})"],
        }
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
