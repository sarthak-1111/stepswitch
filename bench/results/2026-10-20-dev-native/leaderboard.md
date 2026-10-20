# Leaderboard: dev

- Results: [`results.jsonl`](results.jsonl) (24 runs, 6 tasks, seeds [0, 1])
- Command: `ssw bench run --suite dev --workers native:claude-code:claude-sonnet-5-5,native:claude-code:claude-opus-5-5 --seeds 2 --budget-seconds 900.0`
- Hardware: os Windows, arch x86_64, cpu_cores 32, ram_gb 1098.43, gpus 4, gpu_memory_gb [51.53, 48.31, 51.53, 51.53]
- Date: 2026-10-20T03:14:27+00:00
- Version: 0.1.0.dev0 (commit f58577271a8a)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when every acceptance command exits with 0 on a fresh copy with the run's diff applied and the suite's protected paths restored.

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | Median uncached | Median hosted | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | native:claude-code:claude-sonnet-5-5 | 100% | 100% to 100% | 12 | 12.7 | 72,764 | 7,616 | 72,764 | 0.0 | 0% | 90% | 0 | - |
| 2 | native:claude-code:claude-opus-5-5 | 100% | 100% to 100% | 12 | 22.8 | 96,378 | 9,925 | 96,378 | 0.0 | 0% | 91% | 0 | - |

![Pass rate by contestant](pass-rate.svg)
