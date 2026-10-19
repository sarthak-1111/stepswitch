# ADR 0026: The learning router (contextual Thompson sampling)

- Status: accepted
- Date: 2026-10-19
- Phase: 7

## Context

SPEC §10 asks for contextual Thompson sampling over workers. The context is the task's
features, the objective is `reward = success - λ·time - μ·tokens`, the cold start comes from
registry priors, probe and bench results, and the data stays local, with an opt-in export of
anonymized aggregates. A simulation harness with synthetic workers must show convergence
(C-015).

Constraints from earlier phases:

- The router already scores every candidate for every step: a task-level prior from
  `policy.prior` (registry, tier, probes, bench base rates), shifted per step type, against the
  expected time and tokens of the cost model and the cache-aware switch cost (ADR 0024).
- Benches compare contestants and must route the same way on every run.
- Tasks are few. A user runs tens of tasks a week, not thousands. The classifier's features
  are coarse (track, difficulty 1-5, task type, language, context size), and many contexts are
  seen once.

## Decision

1. **What is learned.** Only success is uncertain enough to sample.
   - The bandit keeps a Beta posterior of each worker's success per context key.
   - It also keeps running means of the worker's normalized time and tokens per task.
   - Time and tokens are divided by `time_scale_seconds` (600) and `token_scale` (200,000) and
     capped at 1.
   - `λ = μ = 0.1` by default, so a full-scale task costs at most 0.2 of a success.
   - Mixing costs into the Beta (rewards as fractional successes) learned slowly and blurred
     cheap failures with expensive successes. It was tried first and dropped.
2. **Five context levels with backoff.** The levels are every task, the track, the difficulty
   band (easy 1-2, medium 3, hard 4-5), the difficulty, and the full context (task type,
   language, context band).
   - Each level's counts are added to those of the level above, which are first scaled down to
     at most `backoff_cap` (15) pseudo-tasks.
   - A context with little data borrows from coarser ones; one with much data stands alone.
   - The cold start is the static prior for edit steps, worth `prior_strength` (8) pseudo-tasks.
     Until data arrives, the learning router routes as the Phase 2 to 6 router did.
3. **One sample per worker per task.**
   - The sample is drawn with a generator seeded by the task's seed and id
     (`task_rng(seed, task_id)`), and is recorded in the `learning` event.
   - The router uses the sample in place of the static task-level prior and keeps the step-type
     shift, so its cost-aware per-step score is unchanged otherwise (`RouterState.learned`).
   - Sampling per step would make a task switch workers on noise, and every switch costs cache
     and possibly a brief.
   - The sample is for choosing. Estimates of success, such as the `p_current` and `p_new` of a
     handoff record that calibrates the handoff recovery (ADR 0024), use the posterior mean
     with the same shift (`RouterState.means`, `Candidate.expected_success`). The chance of
     success after a handoff, `p_current + r * (p_new - p_current)`, is linear in the two
     success rates, so averaging it over their posteriors is the same as using the means. A
     sample can also rank a weaker worker above a stronger one, which drops the handoff from
     calibration at random.
4. **Credit by work share.**
   - A finished task adds its outcome (success means `done`) and its measured time and tokens
     to every level.
   - Each worker is credited in proportion to the work steps it took. Each best-of-N candidate
     is credited with its own status.
   - Cancelled tasks and tasks that ended in an error are not learned from: they say nothing
     about the workers.
5. **Modes.** `[router.learning] mode` (or `TaskSpec.learning`) is `on` (sample and learn),
   `frozen` (sample, learn nothing) or `off` (static priors). Bench runs default to `off`, so a
   contestant's routing does not drift during a bench and runs stay comparable. A replay never
   samples or learns (ADR 0027).
6. **Local by default.**
   - The statistics are a JSON document in the project's state database (`router_params`, key
     `bandit`).
   - `ssw router stats` shows them.
   - `ssw router stats --export PATH` writes aggregates per model and coarse level (`all`,
     `track`, `band`, `difficulty`). These carry no endpoint names, which may name machines,
     and no finest-level contexts, which could single out one task. Nothing is sent anywhere.
7. **The claim is about the simulation.**
   - C-015 is scoped to the harness, `router.simulate`. Each world has four synthetic workers,
     one per rung. Their success depends on track, difficulty, type and context size, plus
     per-worker and per-pair effects. Their costs grow with the rung, and their registry priors
     are noisy.
   - The learning router is compared with the router without learning (`static`), with the
     single best worker of the world (`fixed`, which knows the true rates) and with random
     routing, on the same tasks and outcome draws.
   - Thresholds: below random in every world, on average at most 0.25 of random's regret, below
     both fixed policies on average, and a last-quarter regret per task at most 0.65 of the
     first quarter's.

## Consequences

- Measured (30 worlds, 4,000 tasks each):

  | Policy | Mean cumulative regret |
  |---|---|
  | Learning router | 117.8 |
  | Best fixed worker | 167.0 |
  | Static priors | 194.3 |
  | Random | 670.1 |

  - Regret per task falls from 0.045 in the first quarter to 0.021 in the last.
  - The learning router beats random routing in all 30 worlds, the static router in 18 and the
    fixed worker in 15. In worlds where one worker is best almost everywhere, the fixed policy,
    which knows the truth from the start, does better.
  - The evidence lists every world.
- Real routing changes only once tasks are recorded. The bench keeps the learning router off, so
  bench results measure the static router plus whatever the bench calibrated.
- The harness, not real tasks, proves C-015. A claim about real tasks would need many more
  recorded tasks per context than a bench run gives.
