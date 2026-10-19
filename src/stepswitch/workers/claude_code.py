"""Claude Code adapter (SPEC §12 item 3, ADR 0029): headless runs pointed at `ssw proxy`.

Works through `ssw proxy`; not supported upstream: the harness's documentation says routing it to
other models through a gateway is not supported (docs/RESEARCH.md, Claude Code).

* **Command.** `claude -p <prompt> --output-format stream-json --verbose`, an explicit
  permission mode (a `-p` run against a third-party endpoint otherwise starts in `auto`),
  `--permission-prompts none` (whatever would ask is denied, so a run never waits),
  `--strict-mcp-config` (no MCP servers), the web tools disallowed, and `--session-id` (a fresh
  UUID) or `--resume` (the session of the previous attempt).
* **Permissions.** "edits" is `acceptEdits` plus the project's test command as the one allowed
  Bash command prefix (`Bash(<command> *)`, which also matches the bare command); "all" is
  `bypassPermissions`, for sandboxed runs only. The Bash tool is a POSIX shell on every
  platform, so the rule spells the command as the prompt shows it, with Windows paths in
  forward slashes (`shell_command`); a rule with backslashes never matched on Windows.
* **Isolation.** `CLAUDE_CONFIG_DIR` is the run's own directory, so the user's settings,
  credentials, history and `~/.claude.json` are never read or written. The environment is
  `minimal_env`, so the user's own keys never reach the harness; `ANTHROPIC_AUTH_TOKEN` carries
  the run's proxy token (sent as a bearer token).
* **Routing.** `ANTHROPIC_BASE_URL` is the proxy; the main model and the alias pins carry slot
  names (`HarnessEndpoint.slots`); `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` adds the request-class
  headers the proxy routes by (main, subagent, compaction, auxiliary); `ANTHROPIC_CUSTOM_HEADERS`
  adds the session header that binds every request to the task; `CLAUDE_CODE_MAX_CONTEXT_TOKENS`
  is the endpoint's context (unknown model names are otherwise assumed to have 200K).
* **Stream.** One JSON object per line: `system`/`init`, then `assistant` and `user` messages in
  the messages format, and a last `result` (subtype `success`, or `error_max_turns`,
  `error_during_execution`, `error_max_budget_usd`, ... with `errors` and no `result` text).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from stepswitch.workers.harness import (
    HarnessAdapter,
    HarnessEndpoint,
    HarnessEvent,
    HarnessSettings,
    HarnessTask,
    ParseState,
    RunResult,
    RunStatus,
    allowed_test_prefixes,
    proxy_headers,
    tail,
)

# Tools that reach the web; never offered, so a run's egress stays with the proxy.
WEB_TOOLS = ("WebFetch", "WebSearch")
# Result subtypes and what they mean for the run.
RESULT_STOPS: dict[str, tuple[RunStatus, str]] = {
    "error_max_turns": ("stopped", "max_steps"),
    "error_max_budget_usd": ("stopped", "budget_tokens"),
}


class ClaudeCodeAdapter(HarnessAdapter):
    name = "claude-code"
    title = "Claude Code"
    binary = "claude"
    support = "works through ssw proxy; not supported upstream"
    # Non-essential traffic covers auto-update, telemetry, error reporting, feature flags and
    # availability checks; the official plugin marketplace install has its own switch.
    offline_env = {
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
    }
    resumable = True
    # A stdin-only prompt is not documented for `-p`; the prompt is an argument of the native
    # executable (never a batch shim, which would reparse it).
    prompt_on_stdin = False

    def config_dir(self, settings: HarnessSettings) -> Path | None:
        return settings.state_dir / "claude-config" if settings.state_dir is not None else None

    def command(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> list[str]:
        # The prompt follows `-p` directly: the variadic tool lists below would take a prompt
        # placed after them, and a leading "-" would read as an option.
        prompt = task.prompt if not task.prompt.startswith("-") else "Task: " + task.prompt
        args = ["-p", prompt, "--output-format", "stream-json", "--verbose"]
        args += ["--model", endpoint.slot("main")]
        if settings.permission == "all":
            args += ["--permission-mode", "bypassPermissions"]
        else:
            args += ["--permission-mode", "acceptEdits"]
            prefixes = allowed_test_prefixes(settings)
            if prefixes:
                args += ["--allowedTools", *(f"Bash({prefix} *)" for prefix in prefixes)]
        args += ["--permission-prompts", "none", "--strict-mcp-config"]
        args += ["--disallowedTools", *WEB_TOOLS]
        if task.resume:
            args += ["--resume", task.resume]
        else:
            args += ["--session-id", str(uuid.uuid4())]
        if settings.max_turns:
            args += ["--max-turns", str(settings.max_turns)]
        return [*args, *settings.extra_args]

    def environment(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> dict[str, str]:
        headers = "\n".join(f"{k}: {v}" for k, v in proxy_headers(endpoint).items())
        env = {
            "ANTHROPIC_BASE_URL": endpoint.base_url,
            "ANTHROPIC_AUTH_TOKEN": endpoint.token,
            "ANTHROPIC_CUSTOM_HEADERS": headers,
            "ANTHROPIC_MODEL": endpoint.slot("main"),
            "ANTHROPIC_DEFAULT_OPUS_MODEL": endpoint.slot("plan"),
            "ANTHROPIC_DEFAULT_SONNET_MODEL": endpoint.slot("main"),
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": endpoint.slot("fast"),
            "CLAUDE_CODE_SUBAGENT_MODEL": endpoint.slot("subagent"),
            "CLAUDE_CODE_GATEWAY_HINT_HEADERS": "1",
        }
        if endpoint.context:
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(endpoint.context)
        directory = self.config_dir(settings)
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
            env["CLAUDE_CONFIG_DIR"] = str(directory)
        return env

    # -- the stream ----------------------------------------------------------------------------

    def parse(self, line: str, state: ParseState) -> list[HarnessEvent]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            return [HarnessEvent("log", {"line": line[:500]})]
        if not isinstance(item, dict):
            return [HarnessEvent("log", {"line": line[:500]})]
        kind = item.get("type")
        state.session_id = item.get("session_id") or state.session_id
        if kind == "system":
            return self._system(item, state)
        if kind == "assistant":
            return self._assistant(item, state)
        if kind == "user":
            return self._user(item)
        if kind == "result":
            state.final = item
            return [HarnessEvent("result", _result_fields(item))]
        return [HarnessEvent("log", {"type": kind})]

    def _system(self, item: dict[str, Any], state: ParseState) -> list[HarnessEvent]:
        subtype = item.get("subtype")
        if subtype == "init":
            state.model = item.get("model")
            state.version = item.get("claude_code_version") or state.version
            return [
                HarnessEvent(
                    "start",
                    {
                        "session_id": item.get("session_id"),
                        "model": item.get("model"),
                        "version": state.version,
                        "permission_mode": item.get("permissionMode"),
                        "tools": len(item.get("tools") or []),
                        "mcp_servers": len(item.get("mcp_servers") or []),
                    },
                )
            ]
        if subtype == "compact_boundary":
            meta = item.get("compact_metadata") or {}
            return [HarnessEvent("compaction", {"trigger": meta.get("trigger")})]
        return [HarnessEvent("log", {"type": "system", "subtype": subtype})]

    def _assistant(self, item: dict[str, Any], state: ParseState) -> list[HarnessEvent]:
        message = item.get("message") or {}
        subagent = item.get("parent_tool_use_id") is not None
        events: list[HarnessEvent] = []
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and block.get("text"):
                if not subagent:
                    state.texts.append(str(block["text"]))
                events.append(
                    HarnessEvent("text", {"text": str(block["text"])[:2000], "subagent": subagent})
                )
            elif kind == "tool_use":
                state.tool_calls += 1
                arguments = json.dumps(block.get("input") or {}, ensure_ascii=False)
                events.append(
                    HarnessEvent(
                        "tool_call",
                        {
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "arguments": arguments[:1000],
                            "subagent": subagent,
                        },
                    )
                )
            elif kind in {"thinking", "redacted_thinking"}:
                text = str(block.get("thinking") or "")
                events.append(HarnessEvent("thinking", {"chars": len(text), "subagent": subagent}))
        return events

    def _user(self, item: dict[str, Any]) -> list[HarnessEvent]:
        message = item.get("message") or {}
        content = message.get("content")
        events: list[HarnessEvent] = []
        if not isinstance(content, list):
            return events
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                events.append(
                    HarnessEvent(
                        "tool_result",
                        {
                            "id": block.get("tool_use_id"),
                            "error": bool(block.get("is_error")),
                            "text": _result_text(block.get("content"))[:1000],
                            "subagent": item.get("parent_tool_use_id") is not None,
                        },
                    )
                )
        return events

    def finish(
        self, state: ParseState, exit_code: int | None, stderr: str, *, cancelled: bool
    ) -> RunResult:
        final = state.final
        if final is None:
            if cancelled:
                return RunResult(
                    "stopped",
                    "cancelled",
                    session_id=state.session_id,
                    tool_calls=state.tool_calls,
                    exit_code=exit_code,
                    detail="cancelled",
                    version=state.version,
                )
            return RunResult(
                "failed",
                "error",
                summary="\n\n".join(state.texts)[-2000:],
                session_id=state.session_id,
                tool_calls=state.tool_calls,
                exit_code=exit_code,
                detail=f"exit {exit_code} without a result; stderr: {tail(stderr)}",
                version=state.version,
            )
        usage = final.get("usage") or {}
        counts = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cached_input_tokens": usage.get("cache_read_input_tokens"),
            "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        }
        result = RunResult(
            "done",
            session_id=final.get("session_id") or state.session_id,
            turns=int(final.get("num_turns") or 0),
            tool_calls=state.tool_calls,
            usage={k: int(v) for k, v in counts.items() if isinstance(v, int)},
            exit_code=exit_code,
            version=state.version,
        )
        subtype = str(final.get("subtype") or "")
        errors = [str(e) for e in final.get("errors") or []]
        text = str(final.get("result") or "")
        if subtype == "success" and not final.get("is_error"):
            result.summary = text or "\n\n".join(state.texts)[-2000:]
            denied = final.get("permission_denials") or []
            if denied:
                names = sorted({str(d.get("tool_name")) for d in denied if isinstance(d, dict)})
                result.detail = f"{len(denied)} tool call(s) denied: {', '.join(names)}"
            return result
        if subtype in RESULT_STOPS:
            result.status, result.stop_reason = RESULT_STOPS[subtype]
        elif cancelled:
            result.status, result.stop_reason = "stopped", "cancelled"
        else:
            result.status, result.stop_reason = "failed", "error"
        result.summary = text
        result.detail = "; ".join(errors) or text or subtype or f"exit {exit_code}"
        return result


def _result_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "subtype": item.get("subtype"),
        "is_error": bool(item.get("is_error")),
        "turns": item.get("num_turns"),
        "duration_ms": item.get("duration_ms"),
        "duration_api_ms": item.get("duration_api_ms"),
        "denials": len(item.get("permission_denials") or []),
        "text": str(item.get("result") or "")[:2000],
        "errors": [str(e)[:500] for e in item.get("errors") or []],
    }


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""
