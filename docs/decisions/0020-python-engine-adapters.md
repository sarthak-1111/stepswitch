# ADR 0020: Adapters for the Python engine servers

- Status: accepted
- Date: 2026-10-16
- Phase: 3

## Context

SPEC §23 asks Phase 3 for the remaining engine adapters: vLLM, SGLang, MLX-LM and transformers.
ADR 0008 fixed their platforms and listed "transformers serve" as the universal slow fallback.
The pinned engine sources (docs/RESEARCH.md, "Python engine servers") show that each server
handles keys, telemetry, readiness and model downloads differently, and that the development
machine (Windows) can run none of the first three natively.

## Decision

1. **One base, four adapters.** `stepswitch.serve.pyserver.PythonServerEngine` starts an engine
   server as a child process on loopback, from the interpreter or launcher that the machine
   configuration names (`[engines.<name>] python`, `command`, `env`). The adapters add each
   engine's command line, key transport, readiness check and metrics.
2. **No telemetry, no downloads.** Every child gets `HF_HUB_OFFLINE=1`,
   `HF_HUB_DISABLE_TELEMETRY=1` and `DO_NOT_TRACK=1`; the vLLM adapter also sets its own
   usage-statistics variables, and the alternative model hubs stay off. Configuration cannot
   override these. Models must already be local: a recipe `name` (a path or a cached id) or
   `<models>/<org>--<name>/` with a `config.json`. Remote model URIs are refused.
3. **Keys stay off the command line.** vLLM reads its key from the environment; SGLang from a
   private YAML file passed with `--config` and removed on stop; the transformers wrapper from
   the environment. MLX-LM has no authentication; its adapter marks the server as
   unauthenticated and always requests the model name `default_model`, because any other name
   makes the server load that model. Because the server fetches image URLs itself, image URLs
   must not reach it; enforcing that in the compatibility layer is an open issue.
4. **transformers through a small wrapper.** Instead of the transformers library's own serve
   command, the adapter runs `stepswitch.serve.transformers_server`: a standard-library HTTP
   server that imports torch and transformers only inside the engine's environment, renders
   the chat template, reports usage from token counts, and refuses tools and response-format
   constraints with HTTP 400. Agents reach it through the fallback tool protocol, as ADR 0008
   intended. The wrapper keeps the core free of inference imports and gives one stable surface
   across transformers releases.
5. **Platforms.** vLLM and SGLang are Linux-only, MLX-LM is arm64 macOS only. On other platforms
   the adapters report why they are unavailable and suggest adding the server as an endpoint
   (WSL2 or a container), as ADR 0008 says.

## Consequences

- `ssw models serve --engine vllm|sglang|mlx|transformers` works where the engine is installed
  and the model is local. Downloading safetensors models (`ssw models pull` for these engines)
  and registry recipes for them come later.
- The real engine binaries have not run on the development machine (no Linux, macOS or torch
  there). Their tests use stand-in servers that follow the pinned sources. The compatibility
  matrix lists only engine and model pairs that a probe actually ran on.
- Two engine-side risks remain and are documented: vLLM leaves `/invocations` open even with a
  key, and SGLang writes its key into its startup log and `/server_info`.
