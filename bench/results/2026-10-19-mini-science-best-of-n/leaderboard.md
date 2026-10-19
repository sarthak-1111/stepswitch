# Leaderboard: mini-science

- Results: [`results.jsonl`](results.jsonl) (36 runs, 6 tasks, seeds [0, 1])
- Command: `ssw bench run --suite mini-science --workers ws-ollama-local/gpt-oss:120b,ws-ollama-local/qwen2.5:32b,best-of-n --seeds 2 --router-pool ws-ollama-local/gpt-oss:120b,ws-ollama-local/qwen2.5:32b --best-of 2 --budget-seconds 900.0`
- Hardware: os Windows, arch x86_64, cpu_cores 32, ram_gb 1098.43, gpus 4, gpu_memory_gb [51.53, 48.31, 51.53, 51.53]
- Date: 2026-10-19T04:02:59+00:00
- Version: 0.1.0.dev0 (commit a27cce9d9d8c)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when every acceptance command exits with 0 on a fresh copy with the run's diff applied and the suite's protected paths restored.
- Router pool: `ws-ollama-local/gpt-oss:120b`, `ws-ollama-local/qwen2.5:32b`

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | ws-ollama-local/gpt-oss:120b | 100% | 100% to 100% | 12 | 105.1 | 30,886 | 1,157.1 | 0% | 79% | 0 | - |
| 2 | best-of-n | 100% | 100% to 100% | 12 | 201.9 | 58,442 | 2,259.9 | 0% | 83% | 1 | - |
| 3 | ws-ollama-local/qwen2.5:32b | 58% | 17% to 92% | 12 | 104.4 | 28,999 | 1,861.8 | 0% | 89% | 2 | - |

![Pass rate by contestant](pass-rate.svg)
