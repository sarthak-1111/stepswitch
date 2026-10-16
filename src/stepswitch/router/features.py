"""Task features: what the router knows about a task (SPEC §7.1 item 2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

TASK_TYPES = ("bugfix", "feature", "refactor", "test", "docs", "analysis", "other")
RISKS = ("low", "med", "high")

# JSON Schema of the classifier's answer (SPEC §7.1). The step type comes from the agent's state
# machine, so the classifier is asked about the task, not the step.
CLASSIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_type": {"type": "string", "enum": list(TASK_TYPES)},
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
        "domain": {"type": "string"},
        "risk": {"type": "string", "enum": list(RISKS)},
        "est_context_tokens": {"type": "integer", "minimum": 0},
        "needs_new_code": {"type": "boolean"},
        "frontend": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "task_type",
        "difficulty",
        "domain",
        "risk",
        "est_context_tokens",
        "needs_new_code",
        "frontend",
        "confidence",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class TaskFeatures:
    task_type: str = "other"
    difficulty: int = 2
    domain: str = "general"
    risk: str = "low"
    est_context_tokens: int = 2000
    needs_new_code: bool = False
    frontend: bool = False
    confidence: float = 0.5
    # Where the features came from: "heuristic", "model:<worker id>" or "cache".
    source: str = "heuristic"
    language: str | None = None
    track: str = "general"
    offline: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def context(self, step_type: str) -> dict[str, Any]:
        """Fields that routing rules can match on."""
        return {**self.as_dict(), "step_type": step_type}
