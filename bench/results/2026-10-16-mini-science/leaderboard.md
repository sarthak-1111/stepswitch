# Leaderboard: mini-science

- Results: [`results.jsonl`](results.jsonl) (36 runs, 6 tasks, seeds [0, 1])
- Command: `ssw bench run --suite mini-science --workers router,ws-ollama-local/qwen2.5:32b,ws-ollama-local/gpt-oss:120b --seeds 2 --router-pool ws-ollama-local/qwen2.5:32b,ws-ollama-local/gpt-oss:120b --budget-seconds 900.0`
- Hardware: os Windows, arch x86_64, cpu_cores 32, ram_gb 1098.43, gpus 4, gpu_memory_gb [51.53, 48.31, 51.53, 51.53]
- Date: 2026-10-16T10:33:02+00:00
- Version: 0.1.0.dev0 (commit 4766b7969e9a)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when every acceptance command exits with 0 on a fresh copy with the run's diff applied and the suite's protected paths restored.
- Router pool: `ws-ollama-local/gpt-oss:120b`, `ws-ollama-local/qwen2.5:32b`

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | ws-ollama-local/gpt-oss:120b | 100% | 100% to 100% | 12 | 36.0 | 29,774 | 423.6 | 0% | 78% | 0 | - |
| 2 | router | 92% | 75% to 100% | 12 | 45.9 | 40,358 | 534.0 | 0% | 76% | 0 | - |
| 3 | ws-ollama-local/qwen2.5:32b | 50% | 17% to 83% | 12 | 88.0 | 36,447 | 1,386.5 | 0% | 88% | 3 | - |

![Pass rate by contestant](pass-rate.svg)
