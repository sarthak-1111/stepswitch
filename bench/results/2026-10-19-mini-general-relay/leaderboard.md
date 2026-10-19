# Leaderboard: mini-general

- Results: [`results.jsonl`](results.jsonl) (30 runs, 15 tasks, seeds [0, 1])
- Command: `ssw bench run --suite mini-general --workers relay --seeds 2 --relay ws-ollama-local/qwen2.5:32b,ws-ollama-local/gpt-oss:120b --relay-step 6 --budget-seconds 900.0`
- Hardware: os Windows, arch x86_64, cpu_cores 32, ram_gb 1098.43, gpus 4, gpu_memory_gb [51.53, 48.31, 51.53, 51.53]
- Date: 2026-10-19T00:06:00+00:00
- Version: 0.1.0.dev0 (commit 7cb9ba428a4c)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when every acceptance command exits with 0 on a fresh copy with the run's diff applied and the suite's protected paths restored.

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | relay | 70% | 47% to 90% | 30 | 58.1 | 16,544 | 1,913.7 | 0% | 70% | 8 | - |

![Pass rate by contestant](pass-rate.svg)
