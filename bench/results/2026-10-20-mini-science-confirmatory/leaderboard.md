# Leaderboard: mini-science

- Results: [`results.jsonl`](results.jsonl) (24 runs, 6 tasks, seeds [0, 1])
- Command: `ssw bench run --suite mini-science --workers router,harness:claude-code --seeds 2 --router-pool ws-ollama-local/gpt-oss:120b,ws-ollama-local/qwen2.5:32b --budget-seconds 900.0`
- Hardware: os Windows, arch x86_64, cpu_cores 32, ram_gb 1098.43, gpus 4, gpu_memory_gb [51.53, 48.31, 51.53, 51.53]
- Date: 2026-10-20T05:45:33+00:00
- Version: 0.1.0.dev0 (commit 5a33f890d261)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when every acceptance command exits with 0 on a fresh copy with the run's diff applied and the suite's protected paths restored.
- Router pool: `ws-ollama-local/gpt-oss:120b`, `ws-ollama-local/qwen2.5:32b`

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | Median uncached | Median hosted | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | harness:claude-code | 100% | 100% to 100% | 12 | 106.7 | 105,494 | 30,836 | 0 | 1,912.6 | 0% | 66% | 0 | - |
| 2 | router | 92% | 75% to 100% | 12 | 31.7 | 20,416 | 5,942 | 0 | 503.3 | 0% | 75% | 0 | - |

![Pass rate by contestant](pass-rate.svg)
