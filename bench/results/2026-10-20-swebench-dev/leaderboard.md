# Leaderboard: swebench-dev

- Results: [`results.jsonl`](results.jsonl) (12 runs, 6 tasks, seeds [0])
- Command: `ssw bench run --suite swebench-dev --workers router,mini-swe-agent:ws-ollama-local/gpt-oss:120b --seeds 1 --router-pool ws-ollama-local/gpt-oss:120b --max-steps 100 --budget-seconds 3600.0`
- Hardware: not recorded
- Date: 2026-10-20T13:55:37+00:00
- Version: 0.1.0.dev0 (commit 7359b1589030)
- Pass-rate intervals: 95%, cluster bootstrap over tasks (10,000 resamples, seed 0); Wilson score interval with one task.
- Scoring: a run passes when the official harness (swebench 5.0.2) resolves the instance with the run's patch: every FAIL_TO_PASS test passes and every PASS_TO_PASS test still passes.
- Router pool: `ws-ollama-local/gpt-oss:120b`

| Rank | Contestant | Pass rate | 95% interval | Runs | Median wall (s) | Median tokens | Median uncached | Median hosted | GPU-s | Escalations | Cache hits | Changed tests | Reference cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | router | 33% | 0% to 67% | 6 | 536.4 | 875,056 | 133,130 | 0 | 0.0 | 0% | 87% | 1 | - |
| 2 | mini-swe-agent:ws-ollama-local/gpt-oss:120b | 17% | 0% to 50% | 6 | 2,174.2 | 2,252,338 | 54,852 | 0 | 0.0 | 0% | 99% | 0 | - |

![Pass rate by contestant](pass-rate.svg)
