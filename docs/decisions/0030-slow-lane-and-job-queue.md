# ADR 0030: The slow lane: AirLLM and KTransformers backends and an asynchronous job queue

- Status: accepted
- Date: 2026-10-19
- Phase: 9

## Context

SPEC §5 adds a slow-lane tier (SL) for models too large for the machine's memory, served by
layer streaming (AirLLM) or CPU-GPU hybrid MoE inference (KTransformers, llama.cpp expert
offload). Its uses are asynchronous second opinions, overnight review and extra candidates.
SPEC §25 says the slow lane is "asynchronous jobs only, never interactive", and RN9 asks for a
queue whose results arrive as review comments. ADR 0008 places AirLLM behind an out-of-process
HTTP shim and KTransformers as an SGLang profile.

Research checked on 2026-10-19 (docs/RESEARCH.md, "Verified during Phase 9") found:

- AirLLM has no server. Its model class is a plain Python class whose `generate` forwards to a
  transformers model. Its loader takes a local path or a hub ID, and with a hub ID it downloads
  the weights.
- KTransformers ships as `sglang-kt`, a build of SGLang that replaces the official package and
  adds `--kt-*` flags to `python -m sglang.launch_server`. It needs Linux x86-64. The AMX
  methods need a CPU with AMX tiles.
- Whether `sglang-kt` keeps SGLang's `--config` file for the API key is not documented. This
  is an assumption, isolated in the SGLang adapter's key handling.

## Decision

1. **The SL tier is pin-only.**
   - A worker is slow-lane when its tier is `sl`, or its endpoint's engine is `airllm` or
     `ktransformers`, or every engine of its managed model is one of those two.
   - Slow-lane workers are never routed to. A pin to one is refused unless the task runs as a
     job (`TaskSpec.slow_lane`), and the refusal names `ssw jobs submit task` and
     `ssw run --slow-review`.
   - The catalog notes them as "slow lane: asynchronous jobs only (`ssw jobs`)". Their
     escalation level is 2, after T2, only so that ordering stays total.
2. **AirLLM: a shim over the transformers wrapper.**
   - `stepswitch/serve/airllm_server.py` loads `transformers_server.py` by path and replaces
     its generator with one built on AirLLM's `AutoModel`. It serves the same
     chat-completions API, key handling and generation loop.
   - The shim imports only the standard library at module level and runs as
     `python -I airllm_server.py` in the engine's own environment, which needs `airllm`,
     `torch` and `transformers`. The core never imports any of them.
   - Only local model directories are served, because AirLLM downloads any other name. The
     adapter and the shim both refuse anything else.
   - macOS is refused. There AirLLM loads its MLX model class, whose `generate` returns text
     and takes no streamer, and MLX-LM already serves that platform.
   - Its options are `--compression none|4bit|8bit`, `--layer-shards-dir` and
     `--no-prefetching`. The adapter owns `--model`, `--host`, `--port`,
     `--served-model-name` and `--ctx-size` (default: the model's context, else 8192).
3. **KTransformers: an SGLang profile.**
   - The adapter is the SGLang adapter with its own recipe (`engines.ktransformers`), the
     `sglang-kt` distribution and the `kt-kernel` requirement.
   - A recipe must give `--kt-method` (AMXINT4, AMXINT8, RAWINT4, FP8, FP8_PERCHANNEL, BF16 or
     LLAMAFILE) and `--kt-weight-path`.
   - It runs on Linux x86-64 with AVX2 only, which is all LLAMAFILE needs.
   - The other methods' CPU features are checked in `/proc/cpuinfo` whenever that file is
     readable:
     - AMX tiles for AMXINT4 and AMXINT8;
     - AVX-512 F and BW for RAWINT4;
     - AVX-512 BF16 for BF16;
     - AVX-512 BF16 and VBMI for FP8.

     FP8_PERCHANNEL's needs are not documented, so it is not checked.
   - The fork has no `/ready`, so readiness is `/health`, with its per-call one-token generation
     turned off (`SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION=false`).
   - The API key goes into the `--config` file as for SGLang.
4. **A job queue in the project state.**
   - Jobs live in a `jobs` table. A job is `review` or `task`. Its status moves from `queued` to
     `running`, then to `done`, `failed` or `cancelled`.
   - Commands: `ssw jobs submit review --task <id>` needs a finished task with a diff.
     `ssw jobs submit task "<instruction>"` takes `--track` and `--acceptance`. Both take
     `--worker`. `ssw run --slow-review` queues a review when a task finishes with a diff.
   - The job's worker is the one it names, else the first slow-lane worker by ID.
   - **Runners.** `ssw jobs run` claims the oldest queued job in one conditional update, so
     several runners can share a queue. It runs jobs until the queue is empty or `--max-jobs`
     have run, then exits. Scheduling is left to the operating system (cron, a service, a
     terminal left open).
   - **Stale jobs.** A running job whose runner process has stopped (checked by process ID and
     start time) is queued again, and fails after three attempts.
   - **Timeouts.** Model calls get a long timeout, `[jobs] timeout_seconds`, by default six
     hours.
   - **Cancelling.** `ssw jobs cancel` ends a queued job at once. A running review keeps its
     model call but drops the answer. A running task is asked to stop like any other task. Its
     ID goes on the job before the task starts, so a cancel cannot miss it.
   - **Reading jobs.** `ssw jobs list` and `ssw jobs show`, and the read-only
     `GET /api/jobs` and `GET /api/jobs/{id}`.
5. **Review results are review comments on the original task.**
   - A review job asks the worker for the cross-family review format of SPEC §8.5 (ADR 0025).
     Its input is the task's instruction, acceptance commands and diff.
   - The task's trajectory gets one `slow_review` event with the verdict and the reviewer's
     family, then one `review_comment` event per issue. Event numbers continue where the task
     stopped (`Trajectory.resume`), so the dashboard and `ssw show` list the comments with the
     task.
   - An answer that does not parse becomes a review that is not approved, with one issue that
     says so. The raw text is kept.
6. **Task jobs are whole tasks.** A task job runs `run_task` with the worker pinned and
   `slow_lane` set. The result is an ordinary task whose ID the job records. That task is the
   SL row's "extra candidate". Slow-lane workers do not join interactive best-of-N pools,
   because those wait for every candidate.

## Consequences

- **Nothing interactive waits on the slow lane.** The orchestrator and the tests check this.
- **No inference engine in the core package.** The shim and the profile reach models over
  HTTP, like every other engine (CLAUDE.md, Stack).
- **Not verified on hardware.** The development machine has neither AirLLM nor a Linux AMX
  host. The adapters are tested with fake loaders, fake environments and fake CPU flags. A
  live run is still to come.
- **The runner exits when the queue is empty.** Unattended use needs an operating-system
  scheduler. A resident runner can come later behind the same `claim` and `finish` functions.
