"""Oh My Pi adapter (SPEC §12 item 5, ADR 0029): Pi's core, Oh My Pi's configuration.

Oh My Pi is a fork of Pi with its own package, runtime and config files, so this is a thin
adapter on Pi's (`stepswitch.workers.pi`):

* **Command.** `omp -p --mode json --no-session --model stepswitch/<slot> <prompt>`, with the
  approval mode of the run's permission (`write`: reads and edits run, commands that need
  approval fail closed without a UI; `yolo` under "all"), and with the `adapters/omp` extension as
  the only extension when present (`--trusted-extension <path>`, else `--no-extensions`): it adds
  the `before_subagent_spawn` hook that gives each subagent the slot of its role (the role map
  arrives in `STEPSWITCH_ROLE_MODELS`, so this module stays its only source). Oh My Pi has no
  virtual models, so the run selects the main slot and the proxy routes every request.
* **Isolation.** `PI_CODING_AGENT_DIR` (honoured for the default profile, which the run uses) is
  the run's own agent directory with a generated `models.yml` (the `stepswitch` provider at the
  proxy, its key read from `STEPSWITCH_PROXY_TOKEN`) and `config.yml` (`modelRoles`: every role
  mapped to a slot). Both are JSON, which is valid YAML.
* **Stream.** Pi's events, without usage on `message_update`; the answer is the last assistant
  `message_end`, and the process exits non-zero when its stop reason is `error` or `aborted`.
"""

from __future__ import annotations

import json
from pathlib import Path

from stepswitch.workers.harness import HarnessEndpoint, HarnessSettings, HarnessTask, model_id
from stepswitch.workers.pi import PROVIDER, PiAdapter

# Oh My Pi's model roles (docs/RESEARCH.md) and the slot roles they map to.
ROLE_SLOTS = {
    "default": "main",
    "smol": "fast",
    "slow": "plan",
    "plan": "plan",
    "commit": "fast",
    "tiny": "fast",
    "memory": "fast",
    "task": "subagent",
    "advisor": "review",
}


class OhMyPiAdapter(PiAdapter):
    name = "oh-my-pi"
    title = "Oh My Pi"
    binary = "omp"
    support = "native provider config and model roles, plus the adapters/omp extension"
    offline_env = {"PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"}
    extension_dir = "omp"

    def roles(self, endpoint: HarnessEndpoint) -> dict[str, str]:
        return {
            role: f"{PROVIDER}/{model_id(endpoint.slot(slot))}" for role, slot in ROLE_SLOTS.items()
        }

    def write_config(self, directory: Path, endpoint: HarnessEndpoint) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        provider = {**self.provider(endpoint), "apiKey": "STEPSWITCH_PROXY_TOKEN"}
        models = {"providers": {PROVIDER: provider}}
        (directory / "models.yml").write_text(json.dumps(models, indent=2) + "\n", encoding="utf-8")
        (directory / "config.yml").write_text(
            json.dumps({"modelRoles": self.roles(endpoint)}, indent=2) + "\n", encoding="utf-8"
        )

    def run_model(self, endpoint: HarnessEndpoint) -> str:
        return self.selector(endpoint)

    def environment(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> dict[str, str]:
        env = super().environment(task, workdir, endpoint, settings)
        env["STEPSWITCH_ROLE_MODELS"] = json.dumps(self.roles(endpoint), separators=(",", ":"))
        return env

    def command(
        self,
        task: HarnessTask,
        workdir: Path,
        endpoint: HarnessEndpoint,
        settings: HarnessSettings,
    ) -> list[str]:
        args = ["-p", "--mode", "json", "--no-session", "--model", self.run_model(endpoint)]
        args += ["--approval-mode", "yolo" if settings.permission == "all" else "write"]
        # `--trusted-extension` is an exact allowlist (ambient discovery off); it cannot be
        # combined with `-e`. Without Stepswitch's extension, no extension loads at all.
        extension = self.extension()
        if extension is not None:
            args += ["--trusted-extension", str(extension)]
        else:
            args.append("--no-extensions")
        return [*args, *settings.extra_args, task.prompt]
