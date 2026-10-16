# ADR 0008: Engine targets and platform scope

- Status: accepted
- Date: 2026-10-16
- Phase: 0 (llama.cpp and Ollama adapters in Phase 1; the rest in Phase 3 and Phase 9)

## Context

SPEC §4.2 lists engine targets, and SPEC §5.3 defines the engine interface. Phase 0 research
(docs/RESEARCH.md, "Engines", "Engine-side tool parsers", and the details for A01-A03) shows
that the targets differ widely in platform support, in what they expose, and in how they
manage their own memory. The primary development host is a Windows workstation.

## Decision

| Engine | Use | Platforms | How Stepswitch reaches it |
|---|---|---|---|
| llama.cpp server | primary engine: T0 on CPU, GGUF on GPU, MoE expert offload | native binaries on Windows, macOS, Linux | managed process; router mode (several models in one server) is the first hot-swap path; bound to loopback; the RPC backend is never enabled; releases pinned and bumped for security advisories |
| Ollama | supported when installed | Windows, macOS, Linux | treated as a self-managing server: we set `num_ctx` explicitly on every request and its keep-alive instead of evicting for it; pulls use `hf.co/...` names when the original source is required; models whose tags resolve to a remote service are rejected in local mode |
| vLLM, SGLang | GPU servers | Linux; on Windows through WSL2 or a container (WSL2 documented for vLLM only) | external or managed inside Linux; `ssw doctor` reports the driver branch, because current wheels need a CUDA 13 driver |
| MLX-LM | ARM macOS | macOS (Linux extras declared, unverified) | managed process; no constrained decoding, so the fallback protocol validates and repairs |
| transformers serve | universal slow fallback | all | separate process over HTTP, always with the fallback tool protocol; never imported into the core |
| KTransformers | slow lane and AMX-friendly MoE offload | Linux x86-64 only | as an SGLang profile with its `--kt-*` flags |
| AirLLM | slow lane | wherever its own dependencies install | out-of-process HTTP shim in its own environment (optional extra), since it has no server |
| ExLlamaV3 | optional | runs natively on Windows | only through its separate server process, pinned by commit and never vendored (that server is AGPL) |
| llama-swap | optional | native binaries | not required: the server manager plus llama.cpp router mode cover hot-swap; configs with remote `peers` are rejected in local mode |
| TensorRT-LLM | **dropped** as an adapter | Linux only; our GPU class is not on its supported list | still reachable as a plain chat-completions endpoint if a user runs it |
| bitnet.cpp | not targeted | fork-only model format, no releases | watch item |

Engine-interface changes to SPEC §5.3:

1. `metrics` and `prefix_cache_stats` are optional. Where an engine has no metric, cache
   statistics are derived from per-response usage fields (cached-token counts, timing fields)
   recorded in the ledger.
2. `slot_save` / `slot_restore` exist only for the llama.cpp server (verified). Other engines may
   offer an optional `kv_offload` capability instead.
3. The server manager owns memory accounting (inputs: `ssw doctor`, engine-reported loaded sizes).
   It does not double-manage engines that load and unload models themselves.
4. Hot-swap is per process for the GPU servers; their sleep modes are opt-in fast paths on
   loopback only.
5. Engine recipes in the registry carry the exact flags, parser names, plugins, forks and the
   minimum engine version per model (tool and reasoning parsers are named per engine, and the
   names differ between engines).

## Consequences

- On the Windows workstation, Phase 1-2 work runs on llama.cpp and Ollama natively. vLLM,
  SGLang and KTransformers become available later through WSL2 or containers and are reached over
  HTTP like remote endpoints.
- The compatibility matrix reports per-engine gaps (for example missing tool-call IDs or
  unsupported `tool_choice`) instead of per-format promises.
- Removing TensorRT-LLM shrinks the adapter list without reducing what users can connect.
