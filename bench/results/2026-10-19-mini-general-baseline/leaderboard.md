# Leaderboard: mini-general

- Results: [`results.jsonl`](results.jsonl) (30 runs, 15 tasks, seeds [0, 1])
- Command: `ssw bench run --suite mini-general --workers router --seeds 2 --router-pool ws-ollama-local/gpt-oss:120b,ws-ollama-local/qwen2.5:32b --baseline --budget-seconds 900.0`
- Hardware: os Windows, arch x86_64, cpu_cores 32, ram_gb 1098.43, gpus 4, gpu_memory_gb [51.53, 48.31, 51.53, 51.53]
- Date: 2026-10-18T22:36:10+00:00
- Version: 0.1.0.dev0 (commit eee786076888)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when every acceptance command exits with 0 on a fresh copy with the run's diff applied and the suite's protected paths restored.
- Router pool: `ws-ollama-local/gpt-oss:120b`, `ws-ollama-local/qwen2.5:32b`

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | router | 93% | 83% to 100% | 30 | 37.9 | 33,270 | 1,243.4 | 0% | 85% | 1 | - |

![Pass rate by contestant](pass-rate.svg)
