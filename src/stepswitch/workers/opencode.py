"""OpenCode adapter (SPEC §12 item 6, ADR 0029): an ACP agent (`opencode acp`).

* **Config.** `OPENCODE_CONFIG_CONTENT` (inline JSON, which overrides the global and project
  config files without touching them) defines the `stepswitch` provider at the proxy in the
  chat-completions format (its key from `STEPSWITCH_PROXY_TOKEN`), one model per slot with the
  endpoint's context window, `model` and `small_model`, the `plan` agent's model, the permission
  policy (edits allowed; shell commands asked, so the ACP client decides, except the test command;
  web access and paths outside the worktree denied), sharing off and update checks off. Unknown
  keys are ignored by OpenCode, not rejected.
* **Model.** The session's `model` config option is set to the main slot's model when offered.
* **Egress.** Auto-update, the model-list fetch, default plugins and LSP downloads are off, and
  OpenCode does not read another harness's config directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stepswitch.workers.acp import AcpAdapter
from stepswitch.workers.harness import (
    HarnessEndpoint,
    HarnessSettings,
    HarnessTask,
    allowed_test_prefixes,
    model_id,
)

PROVIDER = "stepswitch"
# The chat-completions provider package of OpenCode's provider config (docs/RESEARCH.md).
CHAT_PACKAGE = "@ai-sdk/openai-compatible"
DEFAULT_OUTPUT = 16384
SLOT_ROLES = ("main", "plan", "fast", "subagent", "review")


class OpenCodeAdapter(AcpAdapter):
    name = "opencode"
    title = "OpenCode"
    binary = "opencode"
    support = "ACP agent (`opencode acp`), configured through OPENCODE_CONFIG_CONTENT"
    offline_env = {
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    }
    acp_args = ("acp",)

    def model_value(self, endpoint: HarnessEndpoint) -> str:
        return f"{PROVIDER}/{model_id(endpoint.slot('main'))}"

    def config(self, endpoint: HarnessEndpoint, settings: HarnessSettings) -> dict[str, Any]:
        context = endpoint.context
        models: dict[str, Any] = {}
        for role in SLOT_ROLES:
            slot = model_id(endpoint.slot(role))
            limit = {"output": min(DEFAULT_OUTPUT, context // 4) if context else DEFAULT_OUTPUT}
            if context:
                limit["context"] = context
            models.setdefault(
                slot, {"name": f"Stepswitch {slot.removeprefix('ssw-')}", "limit": limit}
            )
        bash: str | dict[str, str] = "allow"
        if settings.permission != "all":
            # In a pattern map the last matching rule wins: ask for everything, run the tests.
            bash = {"*": "ask"}
            for prefix in allowed_test_prefixes(settings):
                bash.update({prefix: "allow", f"{prefix} *": "allow"})
        return {
            "provider": {
                PROVIDER: {
                    "npm": CHAT_PACKAGE,
                    "name": "Stepswitch",
                    "options": {
                        "baseURL": endpoint.base_url + "/v1",
                        "apiKey": "{env:STEPSWITCH_PROXY_TOKEN}",
                    },
                    "models": models,
                }
            },
            "model": f"{PROVIDER}/{model_id(endpoint.slot('main'))}",
            "small_model": f"{PROVIDER}/{model_id(endpoint.slot('fast'))}",
            "agent": {"plan": {"model": f"{PROVIDER}/{model_id(endpoint.slot('plan'))}"}},
            "permission": {
                "edit": "allow",
                "bash": bash,
                "webfetch": "deny",
                "websearch": "deny",
                "external_directory": "deny",
            },
            "share": "disabled",
            "autoupdate": False,
        }

    def environment(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> dict[str, str]:
        env = super().environment(task, workdir, endpoint, settings)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(self.config(endpoint, settings))
        return env
