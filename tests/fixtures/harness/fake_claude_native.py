"""A stand-in for Claude Code running natively on its own login, for the bench's native runs.

It checks that it was started the way a native bench run must start it (ADR 0034):

* its flags include `--safe-mode`, `--setting-sources project,local`,
  `--no-session-persistence`, `--strict-mcp-config` and `--permission-prompts none`;
* its environment has no proxy, key or token variable and no `CLAUDE_CONFIG_DIR`, and has auto
  memory and non-essential traffic off;
* its prompt names the project's test command.

A failed check ends the run with an error result. Otherwise `FAKE_NATIVE_MODE` decides:

* `fix` (default): edit `calc.py` so add() adds, then report success with `modelUsage` for the
  model given with `--model` and a small auxiliary model;
* `limit`: report a usage-limit error (an infrastructure error);
* `crash`: exit without a final report.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

FORBIDDEN = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CONFIG_DIR",
)
AUXILIARY = "fake-auxiliary-model"


def emit(item: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(item) + "\n")
    sys.stdout.flush()


def after(args: list[str], flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args else None


def problems(args: list[str], prompt: str) -> list[str]:
    found = [
        f"missing {flag}"
        for flag in ("--safe-mode", "--no-session-persistence")
        if flag not in args
    ]
    found += [] if after(args, "--setting-sources") == "project,local" else ["user settings load"]
    found += [] if "--strict-mcp-config" in args else ["MCP servers load"]
    found += [] if after(args, "--permission-prompts") == "none" else ["permission prompts on"]
    found += [f"{name} is set" for name in FORBIDDEN if os.environ.get(name)]
    if os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") != "1":
        found.append("auto memory on")
    if os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") != "1":
        found.append("non-essential traffic on")
    if "Run the project's tests with this command" not in prompt:
        found.append("the prompt names no test command")
    return found


def usage(model: str) -> dict[str, Any]:
    return {
        model: {
            "inputTokens": 100,
            "outputTokens": 20,
            "cacheReadInputTokens": 1000,
            "cacheCreationInputTokens": 50,
            "costUSD": 0.01,
        },
        AUXILIARY: {
            "inputTokens": 10,
            "outputTokens": 2,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0,
            "costUSD": 0.002,
        },
    }


def main() -> int:
    args = sys.argv[1:]
    prompt = after(args, "-p") or ""
    model = after(args, "--model") or "unknown"
    session = after(args, "--session-id") or str(uuid.uuid4())
    mode = os.environ.get("FAKE_NATIVE_MODE", "fix")
    emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": session,
            "model": model,
            "tools": ["Edit", "Bash"],
            "mcp_servers": [],
            "permissionMode": after(args, "--permission-mode"),
            "apiKeySource": "none",
            "claude_code_version": "0.0.0-fake",
        }
    )
    found = problems(args, prompt)
    if found:
        emit(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "session_id": session,
                "is_error": True,
                "num_turns": 0,
                "errors": found,
            }
        )
        return 1
    if mode == "crash":
        return 3
    if mode == "limit":
        emit(
            {
                "type": "result",
                "subtype": "success",
                "session_id": session,
                "is_error": True,
                "api_error_status": 429,
                "num_turns": 1,
                "result": "You've hit your limit",
                "usage": {"input_tokens": 5, "output_tokens": 0},
                "modelUsage": {},
            }
        )
        return 1
    calc = Path.cwd() / "calc.py"
    calc.write_text(calc.read_text("utf-8").replace("return a - b", "return a + b"), "utf-8")
    emit(
        {
            "type": "assistant",
            "session_id": session,
            "parent_tool_use_id": None,
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Edit",
                        "input": {"file_path": "calc.py"},
                    }
                ],
            },
        }
    )
    emit(
        {
            "type": "user",
            "session_id": session,
            "parent_tool_use_id": None,
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            },
        }
    )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "session_id": session,
            "is_error": False,
            "num_turns": 2,
            "result": "Fixed add().",
            "usage": {
                "input_tokens": 110,
                "output_tokens": 22,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 50,
            },
            "modelUsage": usage(model),
            "total_cost_usd": 0.012,
            "permission_denials": [],
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
