"""Success priors per (worker, step type, task features) (SPEC §7.1 item 3).

Phase 2 priors come from the worker's tier and registry or config roles; probe results lower
them for capabilities a worker failed. Phase 7 replaces this with a learned policy (SPEC §10).
The numbers are deliberately simple and explainable: every decision records them.
"""

from __future__ import annotations

from stepswitch.router.features import TaskFeatures
from stepswitch.workers.base import WorkerSpec

# Base success probability per ladder rung for a medium task (difficulty 2-3).
BASE_BY_LEVEL: dict[int, float] = {0: 0.45, 1: 0.65, 2: 0.80, 3: 0.85}
# Roles that suit each step type (registry roles: router, classifier, summarizer, log_triage,
# commit_message, handoff_writer, embeddings, search_ranking, fim; config roles: planner,
# editor, reviewer, coder).
ROLES_FOR_STEP: dict[str, tuple[str, ...]] = {
    "plan": ("planner",),
    "navigate": ("router", "search_ranking", "classifier", "planner"),
    "read": ("router", "summarizer", "editor"),
    "edit": ("editor", "coder"),
    "run": ("editor", "coder"),
    "triage": ("log_triage", "planner", "editor"),
    "review": ("reviewer",),
    "summarize": ("summarizer", "handoff_writer", "commit_message"),
    "write-text": ("summarizer", "commit_message"),
}
# Steps where a small model does almost as well as a large one.
LIGHT_STEPS = frozenset({"navigate", "summarize", "write-text"})


def prior(spec: WorkerSpec, step_type: str, features: TaskFeatures) -> float:
    p = BASE_BY_LEVEL.get(spec.level, 0.6)
    if step_type in LIGHT_STEPS:
        p += 0.15 * (1.0 - spec.level / 3.0)
    if set(ROLES_FOR_STEP.get(step_type, ())) & set(spec.roles):
        p += 0.05
    # Harder tasks hurt small models more than large ones.
    p -= 0.06 * (features.difficulty - 2) * (1.0 - spec.level / 3.0)
    if features.risk == "high":
        p -= 0.05 * (1.0 - spec.level / 3.0)
    if spec.tool_protocol == "fallback" and step_type not in LIGHT_STEPS:
        p -= 0.05
    return round(min(0.98, max(0.05, p)), 4)
