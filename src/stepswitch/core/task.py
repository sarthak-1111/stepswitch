"""Tasks and steps (SPEC §2) and the task lifecycle (SPEC §17)."""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

StepType = Literal[
    "plan", "navigate", "read", "edit", "run", "triage", "review", "summarize", "write-text"
]
STEP_TYPES: tuple[str, ...] = get_args(StepType)
Track = Literal["general", "science"]
TaskStatus = Literal[
    "queued",
    "routing",
    "running",
    "verifying",
    "escalated",
    "done",
    "failed",
    "needs-human",
    "stopped",
]
FINAL_STATUSES: frozenset[str] = frozenset({"done", "failed", "needs-human", "stopped"})
StopReason = Literal[
    "budget_tokens",
    "budget_wall_seconds",
    "budget_gpu_seconds",
    "budget_escalations",
    "max_steps",
    "stuck",
    "cancelled",
    "no_worker",
    "gates_failed",
    "model_error",
    "error",
]
# Steps whose model may write or edit code; tiny (T0) models never get them in the science
# track (working rule 10).
CODE_STEPS: frozenset[str] = frozenset({"navigate", "read", "edit", "run", "triage"})
TEXT_STEPS: frozenset[str] = frozenset({"plan", "review", "summarize", "write-text"})


class Budgets(BaseModel):
    """Per-task limits (SPEC §7.4). None means no limit."""

    model_config = ConfigDict(extra="forbid")

    tokens: int | None = None
    wall_seconds: float | None = None
    gpu_seconds: float | None = None
    escalations: int | None = None


class TaskSpec(BaseModel):
    """What the user asked for: everything needed to start (and replay) a task."""

    model_config = ConfigDict(extra="forbid")

    instruction: str
    repo: Path
    track: Track = "general"
    acceptance: list[str] = Field(default_factory=list)
    budgets: Budgets = Field(default_factory=Budgets)
    # Best-of-N across model families (SPEC §8.3, ADR 0025): 1 runs one candidate, N >= 2 runs
    # N, and 0 picks N from the classifier's difficulty.
    best_of: int = Field(default=1, ge=0, le=5)
    # What happens when passing candidates disagree (SPEC §8.4): flag, tests, escalate, human.
    on_disagreement: Literal["flag", "tests", "escalate", "human"] = "flag"
    # With two or more passing candidates, ask a model for probe tests of the task as well.
    probe_tests: bool = True
    offline: bool = False
    # Run the task in an external harness through `ssw proxy` (SPEC §12, ADR 0029):
    # `claude-code`, `pi`, `oh-my-pi`, `opencode`, `kimi-code` or `acp`. None runs the built-in
    # agent.
    harness: str | None = None
    # Run the harness without the user's or the enclosing project's customizations
    # (instruction files, skills, plugins, hooks, memory). The bench sets it (ADR 0034).
    isolated: bool = False
    worker: str | None = None
    # Route only among these workers (ids or names); empty means every configured worker.
    workers: list[str] = Field(default_factory=list)
    seed: int = 0
    max_steps: int = 40
    allow_protected: list[str] = Field(default_factory=list)
    # Gate failures tolerated at one escalation level before moving up (SPEC §7.3, K).
    gate_attempts: int = 2
    # Run a review step after the gates pass.
    review: bool = True
    # Apply the resulting diff to the repository's working tree when the task is done.
    apply: bool = False
    # Start the worktree from the current working tree instead of the base commit.
    include_uncommitted: bool = False
    # Allow routing to remote endpoints (the top of the escalation ladder is opt-in).
    allow_remote: bool = False
    # Token and cache optimization (SPEC §13, ADR 0024); False runs the Phase 2 baseline.
    # None follows `[context] optimize` in the config (on by default).
    optimize: bool | None = None
    # The bench's relay: the first worker hands the task to the second with a HANDOFF brief
    # at work step `relay_step` (`ssw bench run --relay`).
    relay: list[str] = Field(default_factory=list)
    relay_step: int = 6
    # The learning router (SPEC §10, ADR 0026): on, frozen or off; None follows
    # `[router.learning]` (on by default). Bench runs set off, so their routing is reproducible.
    learning: Literal["on", "frozen", "off"] | None = None
    # The task runs as a slow-lane job (`ssw jobs`, ADR 0030), so its pinned worker may be a
    # slow-lane worker; an interactive task refuses one.
    slow_lane: bool = False


def new_task_id() -> str:
    return f"t{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


class TaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    stop_reason: StopReason | None = None
    summary: str = ""
    steps: int = 0
    endpoints: list[str] = Field(default_factory=list)
    switches: int = 0
    escalations: int = 0
    changed_files: list[str] = Field(default_factory=list)
    diff: str = ""
    verdict: dict[str, Any] | None = None
    tokens: dict[str, int] = Field(default_factory=dict)
    # Budget usage when the task ended: tokens, wall_seconds, gpu_seconds, escalations.
    budget: dict[str, float] = Field(default_factory=dict)
    wall_seconds: float = 0.0
    run_dir: str | None = None
    worktree: str | None = None
    branch: str | None = None
    # Phase 5: whether the token optimizations ran, model errors that moved a step to another
    # worker, HANDOFF briefs written (switches, failovers, relays and compactions) and what the
    # context techniques did (`AgentLoop.stats`).
    optimize: bool = True
    failovers: int = 0
    handoffs: int = 0
    compactions: int = 0
    context: dict[str, int] = Field(default_factory=dict)
    # Phase 6 (SPEC §8.3 to §8.5): how many candidates ran, each one's outcome, the winner's
    # index, the disagreement report, whether the task is uncertain, and the winner's review.
    best_of: int = 1
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    winner: int | None = None
    disagreement: dict[str, Any] | None = None
    uncertain: bool = False
    review: dict[str, Any] | None = None
    # Phase 11 (ADR 0034): tokens per worker (`endpoint/model`) from the ledger: calls, input
    # (cached included), cached_input, output, and whether the worker is local (its endpoint
    # is local and does not forward to a hosted service, SPEC §15).
    usage_by_worker: dict[str, dict[str, Any]] = Field(default_factory=dict)
