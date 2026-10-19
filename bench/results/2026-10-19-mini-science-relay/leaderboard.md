# Leaderboard: mini-science

- Results: [`results.jsonl`](results.jsonl) (12 runs, 6 tasks, seeds [0, 1])
- Command: `ssw bench run --suite mini-science --workers relay --seeds 2 --relay ws-ollama-local/qwen2.5:32b,ws-ollama-local/gpt-oss:120b --relay-step 6 --budget-seconds 900.0`
- Hardware: os Windows, arch x86_64, cpu_cores 32, ram_gb 1098.43, gpus 4, gpu_memory_gb [51.53, 48.31, 51.53, 51.53]
- Date: 2026-10-19T00:20:09+00:00
- Version: 0.1.0.dev0 (commit 7cb9ba428a4c)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when every acceptance command exits with 0 on a fresh copy with the run's diff applied and the suite's protected paths restored.

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | relay | 100% | 100% to 100% | 12 | 66.2 | 35,564 | 525.3 | 0% | 70% | 0 | - |

![Pass rate by contestant](pass-rate.svg)
