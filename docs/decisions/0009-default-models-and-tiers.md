# ADR 0009: Default models per tier

- Status: accepted
- Date: 2026-10-16
- Phase: 0 (registry entries in Phase 1)

## Context

SPEC §5 lists candidate models per hardware tier "as of October 2026 (verify)". Phase 0 checked
every candidate against the model hub and the Ollama library (docs/RESEARCH.md, "Default model
candidates"). Most exist. A few are renamed, outdated, unnamed or not runnable locally, and some
carry licenses with commercial conditions. The T0 cap of 8 GB was defined as a download size,
but resident memory is what decides whether a CPU-only 8 GB machine can run the set.

## Decision

**T0 default set** (`ssw models pull --tier cpu`, llama.cpp preferred; Ollama tags as alternates):

| Role | Model | Quant | Disk GB |
|---|---|---|---|
| router / classifier and summarizer (one server, two slots) | Qwen3-4B-Instruct-2507 | Q4_K_M | 2.50 |
| embeddings | Qwen3-Embedding-0.6B | Q8_0 | 0.64 |
| fill-in-the-middle code model (optional, lazy start) | Qwen2.5-Coder-1.5B (base) | Q8_0 | 1.65 |
| reranker (optional, off by default, llama.cpp only) | Qwen3-Reranker-0.6B | Q8_0 | 0.64 |

The default set is 4.78 GB on disk. The footprint cap stays a disk cap (default 8 GB, `--max-gb`),
and a **RAM budget** is added: per-role context sizes (router 4K, embeddings 2K, FIM 4K, about
5.7 GB resident), lazy start and LRU eviction, so the set fits 8 GB machines. Alternates for T0:
Qwen3.5 0.8B/2B/4B (vision-language models; thinking switched off for router roles), and
Gemma 4 E2B/E4B as a second family, sized by total rather than effective parameters. Phi-4-mini
is dropped from the defaults as outdated; users can still add it.

**T1-T3 candidates** (registry priors; any other model can be added with `ssw models add`):

| Tier | Models |
|---|---|
| T1 (one GPU) | Qwen3.8-27B at 4-bit (needs 24 GB; 12-16 GB cards need 3-bit or partial offload), Devstral-Small-2-24B-Instruct-2512, Qwen3.6-35B-A3B with expert offload, gpt-oss-20b, Gemma 4 26B-A4B |
| T2 (96-192 GB) | Qwen3.8-Flash-Next (license flag, see below), gpt-oss-120b, GLM-5.3-Flash (IQ4_XS fits 192 GB of VRAM; Q4_K_XL needs expert offload), MiMo-V2.6-Flash-MOPD, Mistral-Small-4-119B |
| T3 (multi-GPU or slow lane) | GLM-5.3, Kimi K3, MiMo-V2.6-Pro-MOPD (renamed from "MiMo-V2.6-Pro"), DeepSeek V4.1 Flash (vLLM and SGLang recipes only: no GGUF path yet, since llama.cpp support is an open pull request; excluded from GGUF pulls) |
| SL | requirements per engine (ADR 0008), replacing "any GPU >= 4 GB" |

**Licenses are data.** Registry entries carry the license ID, any usage conditions and a
`commercial_use` field (`permitted | conditional | prohibited`). Models whose licenses restrict
commercial coding-assistant use, or which carry revenue or user-count conditions, are flagged and
never pulled without an explicit confirmation; policy can prefer permissively licensed models.

**Workstation profile** (4 x 48 GB Ampere, large system RAM): editor Qwen3.8-27B (Q8_0, one GPU),
planner GLM-5.3-Flash, reviewer gpt-oss-120b (already served locally), giving three different
families for cross-family review. The slow lane can run T3 MoE models with experts in system RAM.

## Consequences

- The default T0 set is a single family; cross-family checks at T0 use the Gemma 4 alternates.
  The CI model set for the compatibility matrix (C-005, at least four families) is chosen
  separately in Phase 3.
- Model IDs, sizes and licenses are re-verified whenever the registry is updated, with the date
  recorded per entry.
