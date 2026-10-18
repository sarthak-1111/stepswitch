"""Context settings of the built-in agent (SPEC §13, ADR 0024): `[context]` in the config.

    [context]
    optimize = true                  # false: the Phase 2 baseline (`ssw run --no-optimize`)
    window_tokens = 32768            # the work transcript's budget (capped by the worker's)
    compact_at = 0.6                 # rolling HANDOFF summary at this share of it (TK6)
    prune_min_tokens = 800           # superseded results are stubbed in batches this large
    keep_recent = 2                  # the newest results of each kind are never stubbed
    handoff_threshold_tokens = 24000 # uncached tokens above which a switch sends a brief
    distill_tokens = 2000            # longer tool outputs are distilled (TK4)
    dedup_min_tokens = 500           # shorter reads are never answered with a stub (TK3)

    [context.compression]            # TK11, off by default (`context.compress`)
    enabled = false

Values that are missing or out of range keep their defaults.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from stepswitch.context import dedup
from stepswitch.context.compress import CompressionSettings
from stepswitch.context.distill import DISTILL_TOKENS
from stepswitch.context.prune import COMPACT_AT, KEEP_RECENT, PRUNE_MIN_TOKENS
from stepswitch.router.cost import HANDOFF_THRESHOLD_TOKENS

WINDOW_TOKENS = 32_768
# A brief plus the latest exchange must fit after compaction.
MIN_WINDOW_TOKENS = 4_096


def _int(value: Any, default: int, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return default
    return value


@dataclass(frozen=True)
class ContextSettings:
    optimize: bool = True
    window_tokens: int = WINDOW_TOKENS
    compact_at: float = COMPACT_AT
    prune_min_tokens: int = PRUNE_MIN_TOKENS
    keep_recent: int = KEEP_RECENT
    handoff_threshold: int = HANDOFF_THRESHOLD_TOKENS
    distill_tokens: int = DISTILL_TOKENS
    dedup_min_tokens: int = dedup.MIN_TOKENS
    compression: CompressionSettings = field(default_factory=CompressionSettings)

    @classmethod
    def from_config(cls, config: Any) -> ContextSettings:
        section = (getattr(config, "model_extra", None) or {}).get("context")
        if not isinstance(section, Mapping):
            return cls(compression=CompressionSettings.from_config(config))
        compact_at = section.get("compact_at", COMPACT_AT)
        if not isinstance(compact_at, int | float) or not 0.1 <= float(compact_at) <= 0.95:
            compact_at = COMPACT_AT
        optimize = section.get("optimize", True)
        return cls(
            optimize=optimize if isinstance(optimize, bool) else True,
            window_tokens=_int(section.get("window_tokens"), WINDOW_TOKENS, MIN_WINDOW_TOKENS),
            compact_at=float(compact_at),
            prune_min_tokens=_int(section.get("prune_min_tokens"), PRUNE_MIN_TOKENS, 0),
            keep_recent=_int(section.get("keep_recent"), KEEP_RECENT, 0),
            handoff_threshold=_int(
                section.get("handoff_threshold_tokens"), HANDOFF_THRESHOLD_TOKENS, 0
            ),
            distill_tokens=_int(section.get("distill_tokens"), DISTILL_TOKENS, 256),
            dedup_min_tokens=_int(section.get("dedup_min_tokens"), dedup.MIN_TOKENS, 0),
            compression=CompressionSettings.from_config(config),
        )

    def window_for(self, context: int | None) -> int:
        """The transcript budget on a worker with `context` tokens (None: unknown)."""
        return min(self.window_tokens, context) if context else self.window_tokens
