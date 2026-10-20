# Leaderboard: mini-general

- Results: [`results.jsonl`](results.jsonl) (60 runs, 15 tasks, seeds [0, 1])
- Command: `ssw bench run --suite mini-general --workers router,harness:claude-code --seeds 2 --router-pool ws-ollama-local/gpt-oss:120b,ws-ollama-local/qwen2.5:32b --budget-seconds 900.0`
- Hardware: os Windows, arch x86_64, cpu_cores 32, ram_gb 1098.43, gpus 4, gpu_memory_gb [51.53, 48.31, 51.53, 51.53]
- Date: 2026-10-20T04:58:03+00:00
- Version: 0.1.0.dev0 (commit 5a33f890d261)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when every acceptance command exits with 0 on a fresh copy with the run's diff applied and the suite's protected paths restored.
- Router pool: `ws-ollama-local/gpt-oss:120b`, `ws-ollama-local/qwen2.5:32b`

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | Median uncached | Median hosted | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | router | 100% | 100% to 100% | 30 | 34.5 | 18,974 | 5,842 | 0 | 878.5 | 0% | 77% | 0 | - |
| 2 | harness:claude-code | 100% | 100% to 100% | 30 | 90.2 | 141,129 | 33,781 | 0 | 2,794.7 | 0% | 76% | 0 | - |

![Pass rate by contestant](pass-rate.svg)
