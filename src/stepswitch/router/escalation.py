"""Escalation ladder (SPEC §7.3, ADR 0005): T0 -> T1/T2 -> T3 -> remote (opt-in).

The ladder is a minimum rung: routing only considers workers at or above it. Triggers are gate
failures after K attempts, disagreement (Phase 6), low classifier confidence and high risk. The
remote rung is used only when remote workers are allowed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from stepswitch.router.features import TaskFeatures

CONFIDENCE_THRESHOLD = 0.35
RUNG_NAMES = {0: "T0", 1: "T1/T2", 2: "T3", 3: "remote"}


@dataclass
class Ladder:
    levels: list[int]
    level: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def for_levels(cls, available: Iterable[int]) -> Ladder:
        levels = sorted(set(available))
        return cls(levels=levels, level=levels[0] if levels else 0)

    def next_level(self) -> int | None:
        higher = [lvl for lvl in self.levels if lvl > self.level]
        return higher[0] if higher else None

    def can_escalate(self) -> bool:
        return self.next_level() is not None

    def escalate(self, reason: str) -> dict[str, Any] | None:
        target = self.next_level()
        if target is None:
            return None
        move = {
            "from_level": self.level,
            "to_level": target,
            "from": RUNG_NAMES.get(self.level, str(self.level)),
            "to": RUNG_NAMES.get(target, str(target)),
            "reason": reason,
        }
        self.level = target
        self.history.append(move)
        return move


def start_level(
    features: TaskFeatures, ladder: Ladder, *, track: str = "general"
) -> tuple[int, str | None]:
    """The rung a task starts on, and why it is not the lowest one."""
    if not ladder.levels:
        return 0, None
    lowest, reasons = ladder.levels[0], []
    if track == "science" and lowest == 0 and len(ladder.levels) > 1:
        lowest = ladder.levels[1]
        reasons.append("science track: T0 models never edit code")
    above = [lvl for lvl in ladder.levels if lvl > lowest]
    if above and features.risk == "high":
        return above[0], "; ".join([*reasons, "high-risk task"])
    model_answer = features.source.startswith("model:") or features.source == "cache"
    if above and model_answer and features.confidence < CONFIDENCE_THRESHOLD:
        reasons.append(f"classifier confidence {features.confidence:.2f} < {CONFIDENCE_THRESHOLD}")
        return above[0], "; ".join(reasons)
    return lowest, "; ".join(reasons) or None
