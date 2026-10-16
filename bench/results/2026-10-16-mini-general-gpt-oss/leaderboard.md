# Leaderboard: mini-general

- Results: [`results.jsonl`](results.jsonl) (30 runs, 15 tasks, seeds [0, 1])
- Command: `ssw bench run --suite mini-general --workers ws-ollama-local/gpt-oss:120b --seeds 2 --budget-seconds 900.0`
- Hardware: os Windows, arch x86_64, cpu_cores 32, ram_gb 1098.43, gpus 4, gpu_memory_gb [51.53, 48.31, 51.53, 51.53]
- Date: 2026-10-16T09:33:09+00:00
- Version: 0.1.0.dev0 (commit cd9ffff3f77c)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when every acceptance command exits with 0 on a fresh copy with the run's diff applied and the suite's protected paths restored.

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | ws-ollama-local/gpt-oss:120b | 97% | 90% to 100% | 30 | 57.8 | 36,202 | 1,511.3 | 0% | 80% | 0 | - |

![Pass rate by contestant](pass-rate.svg)
