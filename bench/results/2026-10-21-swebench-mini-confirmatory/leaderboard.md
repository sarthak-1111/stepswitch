# Leaderboard: swebench-verified-mini

- Results: [`results.jsonl`](results.jsonl) (100 runs, 50 tasks, seeds [0])
- Command: `ssw bench run --suite swebench-verified-mini --workers router,mini-swe-agent:ws-ollama-local/gpt-oss:120b --seeds 1 --router-pool ws-ollama-local/gpt-oss:120b --max-steps 100 --budget-seconds 3600.0`
- Hardware: not recorded
- Date: 2026-10-23T05:02:56+00:00
- Version: 0.1.0.dev0 (commit 98bc3951e16b)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when the official harness (swebench 5.0.2) resolves the instance with the run's patch: every FAIL_TO_PASS test passes and every PASS_TO_PASS test still passes.
- Router pool: `ws-ollama-local/gpt-oss:120b`

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | Median uncached | Median hosted | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | router | 50% | 36% to 64% | 50 | 823.3 | 1,848,212 | 144,569 | 0 | 0.0 | 0% | 92% | 6 | - |
| 2 | mini-swe-agent:ws-ollama-local/gpt-oss:120b | 14% | 6% to 24% | 50 | 2,264.8 | 729,598 | 30,322 | 0 | 0.0 | 0% | 99% | 0 | - |

![Pass rate by contestant](pass-rate.svg)
