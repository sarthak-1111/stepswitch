# ADR 0033: The quickstart, the installers, and how C-017 is measured

- Status: accepted
- Date: 2026-10-19
- Phase: 10

## Context

SPEC §24 asks for a CPU-only quickstart (`uv tool install stepswitch && ssw init && ssw run`)
and for `install.sh` and `install.ps1` (hard rule 9). Claim C-017, "Goes from install to first
completed task in under 5 minutes on a CPU-only machine", is proven by a "timed scripted
quickstart in a clean container (model download time reported separately)".

Research checked on 2026-10-19 (docs/RESEARCH.md, "Verified during Phase 10") found:

- uv's pinned `install.sh` (0.13.1) embeds a SHA-256 per artifact, but skips the check when
  `sha256sum` is missing. Its `install.ps1` checks nothing. `UV_NO_MODIFY_PATH=1` stops either
  one from editing shell profiles.
- The llama.cpp CPU build for Linux needs `libgomp.so.1`, which the slim Python images lack.
- llama.cpp b11669 counts the physical cores of the whole machine. It enumerates the
  thread-sibling sets of every CPU in sysfs and ignores both the CPU affinity mask and the
  cgroup CPU quota.

The first timed runs of the scripted quickstart exceeded the threshold, for reasons outside
the CPU's speed:

- **Threads.** In a container limited to 8 of 32 CPUs, llama.cpp started a thread per
  physical core of the host, twice as many as the CPUs it could run on.
- **The project's environment.** A task runs in a git worktree, which holds no `.venv`. The
  gates and the agent's `test` tool fell back to the interpreter running Stepswitch. Under
  `uv tool install` that interpreter has none of the project's tools, so the tests were
  skipped while the model kept retrying them.
- **Test selectors.** The model named `test_calc.py` instead of `tests/test_calc.py`, and
  repeated the failing call for several steps.

## Decision

1. **The installers.**
   - Both pin uv (0.13.1) and check what they download before running it. `install.sh` checks
     the pinned uv installer's SHA-256 and runs it with `UV_NO_MODIFY_PATH=1`. `install.ps1`
     downloads uv's release archive for the machine's architecture, checks its SHA-256 and
     unpacks it.
   - Both then run `uv tool install stepswitch==<version>` (`STEPSWITCH_PACKAGE` overrides
     what is installed, for a wheel), run `ssw prefetch`, and print the bin directory if it is
     not on PATH. Neither needs administrator rights or edits a shell profile.
   - `scripts/release_check.py` makes a release tag, the package version and both installer
     pins agree.
2. **`ssw models pull` installs the engine its GGUF files need.** It installs the pinned
   llama.cpp build with consent (`--yes`; `--no-engine-install` skips it) and reports how long
   that took (`engine.seconds` in its JSON).
3. **How C-017 is measured** (`tests/claims/test_c017_quickstart.py`):
   - **The container.** `python:3.12-slim`, run with `--memory=8g --memory-swap=8g` and
     `--cpuset-cpus=0-7`, with no GPU devices. The test checks that it is Linux x86-64 and
     that the process may use eight CPUs.
   - **Before the clock.** The README's prerequisites come from apt: git, curl,
     ca-certificates and libgomp1. The toy project is set up the way a user's project already
     is: the calc-bug repository, committed to git, with its own `.venv` holding pytest.
   - **Timed.** Four steps count:
     - `sh install.sh`, with `STEPSWITCH_PACKAGE` set to a wheel of these sources; its
       dependencies come from the package index;
     - `ssw init`;
     - the engine install inside `ssw models pull --tier cpu`;
     - `ssw run`.
   - **Excluded.** The model download. The tier's model files, which the registry pins by
     SHA-256, are mounted read-only from the host's verified store. The evidence records
     their total size, so a reader can work out the download time at their own bandwidth.
   - **To pass.** The task ends `done`, with the project's tests run in the gates and
     passing, and the total stays under the threshold.
   - **Elsewhere.** The claim is not applicable without Docker running Linux x86-64
     containers, and pending until the tier's models are installed.
4. **Fixes found by the measurement.**
   - The llama.cpp adapter passes `--threads` for the physical cores the process may use when
     an affinity mask or a cgroup quota limits it, unless the recipe or the extra arguments
     set the count.
   - A task worktree uses the environment of the checkout it was made from: its `.venv`, and
     interpreter paths configured relative to the repository. When that interpreter lives
     outside the worktree, the worktree's `src/` goes first on PYTHONPATH, so an editable
     install of the checkout does not shadow the task's changes. The agent's commands get
     the same environment as the gates.
   - The `test` tool resolves a test file named without its directory to the one file of the
     repository with that name, and its output says so.
   - Each step line shows the task's running token count, for the terminal and the demo.

## Consequences

- The time depends on the CPU. The evidence records the step times, the CPU count, the image
  digest and the tier's models. It does not record the CPU model, whose name carries a company
  name. A slower machine with the same CPU count may take longer.
- The Stepswitch package comes from a wheel of the sources until the first release is on the
  package index. Its dependencies already come from the index, as they will for users.
- The model download is the slowest part of a real first run. It is kept out of the claim, as
  the proof says, and reported by size instead of by a time that depends on the network.
