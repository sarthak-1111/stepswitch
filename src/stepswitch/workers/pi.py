"""Pi adapter (SPEC §12 item 4, ADR 0029), and the core Oh My Pi's adapter shares.

* **Command.** `pi --mode json --no-session --model stepswitch/<model> --no-extensions
  [-e <extension>] <prompt>`:
  JSON lines on stdout (records split on LF only), the prompt as the last argument.
* **Isolation.** `PI_CODING_AGENT_DIR` is the run's own agent directory with a generated
  `models.json`: one provider, `stepswitch`, at the proxy in the chat-completions format, its key
  read from `STEPSWITCH_PROXY_TOKEN`, with one model per slot (the largest context window) and
  one per worker (its own window). The user's agent directory (settings, credentials, sessions)
  is never read.
* **Extension.** With `adapters/pi` present (a source checkout), `-e` loads it as the only
  extension: it adds the virtual model `stepswitch/routed`, whose `route()` asks the proxy which
  worker takes each request (`POST /stepswitch/v1/route`) and resolves to that worker's model, so
  Pi's context limits and compaction follow the model that answers. The run selects it; without
  the extension every request names the main slot and the proxy routes it.
* **Egress.** `PI_SKIP_VERSION_CHECK=1` and `PI_TELEMETRY=0` on every run, `PI_OFFLINE=1` and
  `--offline` in offline mode.
* **Stream.** A `session` header, then agent, turn, message and tool events. The answer and its
  stop reason are on the last assistant `message_end`; JSON mode exits 0 even when the answer
  failed, so the stop reason decides the run's status.
"""

from __future__ import annotations

import json
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
    model_id,
    tail,
)

PROVIDER = "stepswitch"
# The extension's virtual model (`adapters/pi/index.ts`); no physical model may share its ID.
VIRTUAL_MODEL = "routed"
# The chat-completions format's `api` value in Pi's model configuration (docs/RESEARCH.md).
CHAT_API = "openai-completions"
DEFAULT_MAX_TOKENS = 16384
# Roles every generated provider offers as models: the proxy's slots.
SLOT_ROLES = ("main", "plan", "fast", "subagent", "review")
# The assistant message's stop reasons and what they mean for the run.
STOPS = {
    "stop": ("done", None),
    "length": ("failed", "model_error"),
    "error": ("failed", "model_error"),
    "aborted": ("stopped", "cancelled"),
}
REPO = Path(__file__).resolve().parents[3]
# The entry module of each extension package under `adapters/` (plain ES modules, ADR 0029).
EXTENSION_FILE = "index.js"


def _model(name: str, context: int | None) -> dict[str, Any]:
    return {
        "id": name,
        "name": f"Stepswitch {name.removeprefix('ssw-')}",
        **({"contextWindow": context} if context else {}),
        "maxTokens": min(DEFAULT_MAX_TOKENS, context // 4) if context else DEFAULT_MAX_TOKENS,
    }


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


class PiAdapter(HarnessAdapter):
    name = "pi"
    title = "Pi"
    binary = "pi"
    support = "native provider config, plus the adapters/pi extension when present"
    offline_env = {"PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"}
    prompt_on_stdin = False
    # The variable that relocates the harness's agent directory, and its config file.
    agent_dir_env = "PI_CODING_AGENT_DIR"
    extension_dir = "pi"

    # -- configuration -----------------------------------------------------------------------

    def agent_dir(self, settings: HarnessSettings) -> Path | None:
        if settings.state_dir is None:
            return None
        return settings.state_dir / f"{self.name}-agent"

    def models(self, endpoint: HarnessEndpoint) -> list[dict[str, Any]]:
        """The slots (which the proxy routes), then every worker (which the routed virtual model
        resolves to; the proxy keeps the decision `route()` made for it)."""
        windows: dict[str, int | None] = {}
        for role in SLOT_ROLES:
            windows.setdefault(model_id(endpoint.slot(role)), endpoint.context)
        for worker, window in endpoint.workers.items():
            if worker != VIRTUAL_MODEL:
                windows.setdefault(worker, window or endpoint.context)
        return [_model(name, window) for name, window in windows.items()]

    def provider(self, endpoint: HarnessEndpoint) -> dict[str, Any]:
        return {
            "baseUrl": endpoint.base_url + "/v1",
            "api": CHAT_API,
            "apiKey": "$STEPSWITCH_PROXY_TOKEN",
            "models": self.models(endpoint),
        }

    def write_config(self, directory: Path, endpoint: HarnessEndpoint) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        config = {"providers": {PROVIDER: self.provider(endpoint)}}
        (directory / "models.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )

    def extension(self) -> Path | None:
        """The TypeScript extension of a source checkout, when present."""
        found = REPO / "adapters" / self.extension_dir / EXTENSION_FILE
        return found if found.is_file() else None

    def selector(self, endpoint: HarnessEndpoint, role: str = "main") -> str:
        return f"{PROVIDER}/{model_id(endpoint.slot(role))}"

    def run_model(self, endpoint: HarnessEndpoint) -> str:
        """The model the run selects: the routed virtual model when the extension loads."""
        if self.extension() is not None:
            return f"{PROVIDER}/{VIRTUAL_MODEL}"
        return self.selector(endpoint)

    def command(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> list[str]:
        args = ["--mode", "json", "--no-session", "--model", self.run_model(endpoint)]
        if settings.offline:
            args.append("--offline")
        # No ambient extensions (a repository's own would run code); explicit `-e` paths still
        # load, so only Stepswitch's does.
        args.append("--no-extensions")
        extension = self.extension()
        if extension is not None:
            args += ["-e", str(extension)]
        return [*args, *settings.extra_args, task.prompt]

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
        }
        if endpoint.context:
            env["STEPSWITCH_CONTEXT_TOKENS"] = str(endpoint.context)
        if settings.offline:
            env["PI_OFFLINE"] = "1"
        directory = self.agent_dir(settings)
        if directory is not None:
            self.write_config(directory, endpoint)
            env[self.agent_dir_env] = str(directory)
        return env

    # -- the stream ----------------------------------------------------------------------------

    def parse(self, line: str, state: ParseState) -> list[HarnessEvent]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            return [HarnessEvent("log", {"line": line[:500]})]
        if not isinstance(item, dict):
            return []
        kind = item.get("type")
        if kind == "session":
            state.session_id = str(item.get("id") or "") or None
            return [
                HarnessEvent(
                    "start", {"session_id": state.session_id, "format": item.get("version")}
                )
            ]
        if kind == "message_end":
            return self._message_end(item.get("message") or {}, state)
        if kind == "tool_execution_start":
            state.tool_calls += 1
            arguments = json.dumps(item.get("args") or {}, ensure_ascii=False)
            return [
                HarnessEvent(
                    "tool_call",
                    {
                        "id": item.get("toolCallId"),
                        "name": item.get("toolName"),
                        "arguments": arguments[:1000],
                    },
                )
            ]
        if kind == "tool_execution_end":
            result = item.get("result") or {}
            text = _text(result.get("content") if isinstance(result, dict) else result)
            return [
                HarnessEvent(
                    "tool_result",
                    {
                        "id": item.get("toolCallId"),
                        "error": bool(item.get("isError")),
                        "text": text[:1000],
                    },
                )
            ]
        if kind in {"compaction_start", "auto_compaction_start"}:
            return [HarnessEvent("compaction", {"type": kind})]
        if kind == "agent_settled":
            state.extra["aborted"] = bool(item.get("aborted"))
            return []
        if kind in {"auto_retry_start", "notice", "model_changed", "error"}:
            return [HarnessEvent("log", {"type": kind, "reason": item.get("reason")})]
        return []

    def _message_end(self, message: dict[str, Any], state: ParseState) -> list[HarnessEvent]:
        if message.get("role") != "assistant":
            return []
        state.turns += 1
        state.model = message.get("model") or state.model
        usage = message.get("usage") or {}
        state.add_usage(
            input_tokens=usage.get("input"),
            output_tokens=usage.get("output"),
            cached_input_tokens=usage.get("cacheRead"),
            cache_write_tokens=usage.get("cacheWrite"),
        )
        state.extra["stop_reason"] = message.get("stopReason")
        state.extra["error"] = message.get("errorMessage")
        text = _text(message.get("content"))
        state.extra["last_text"] = text
        events = []
        if text:
            state.texts.append(text)
            events.append(HarnessEvent("text", {"text": text[:2000]}))
        return events

    def finish(
        self, state: ParseState, exit_code: int | None, stderr: str, *, cancelled: bool
    ) -> RunResult:
        common: dict[str, Any] = {
            "session_id": state.session_id,
            "turns": state.turns,
            "tool_calls": state.tool_calls,
            "usage": dict(state.usage),
            "exit_code": exit_code,
            "version": state.version,
        }
        stop = state.extra.get("stop_reason")
        summary = str(state.extra.get("last_text") or "")
        if cancelled:
            return RunResult("stopped", "cancelled", summary=summary, detail="cancelled", **common)
        if stop is None:
            detail = f"exit {exit_code} without an answer; stderr: {tail(stderr)}"
            return RunResult("failed", "error", detail=detail, **common)
        status, reason = STOPS.get(str(stop), ("failed", "error"))
        if status == "done" and exit_code not in (0, None):
            status, reason = "failed", "error"
        detail = "" if status == "done" else f"stop reason {stop}"
        if state.extra.get("error"):
            detail = f"{detail}: {state.extra['error']}"
        return RunResult(status, reason, summary=summary, detail=detail, **common)  # type: ignore[arg-type]
