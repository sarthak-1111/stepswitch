"""Workers (SPEC §2, §12): a (harness, endpoint, settings) triple that can execute steps.

The built-in agent drives every step itself and asks the router for a worker per step, so a
worker here is what answers one model turn: anything with `complete(request) -> Completion`
over the internal representation. Endpoint workers wrap a `ModelClient` (or the fallback tool
protocol around one); fake workers answer from a script (`stepswitch.workers.fake`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from stepswitch.compat.client import Completion
from stepswitch.compat.ir import Request

ToolProtocol = Literal["native", "fallback"]

# Rungs of the escalation ladder (SPEC §7.3): T0 -> T1/T2 -> T3 -> remote (opt-in).
LEVEL_BY_TIER: dict[str, int] = {"cpu": 0, "t1": 1, "t2": 1, "t3": 2, "remote": 3}
TIER_NAMES: tuple[str, ...] = tuple(LEVEL_BY_TIER)


@runtime_checkable
class StepModel(Protocol):
    """Answers one model turn."""

    def complete(self, request: Request) -> Completion: ...


@dataclass(frozen=True)
class WorkerSpec:
    """What the router knows about a worker. Pure data: no connections."""

    id: str
    harness: str = "builtin"
    endpoint: str = ""
    model: str = ""
    format: str = "chat-completions"
    tier: str = "cpu"
    family: str | None = None
    local: bool = True
    roles: tuple[str, ...] = ()
    tool_protocol: ToolProtocol = "native"
    # The catalog syntax Stepswitch parses native tool calls with, when the server does not
    # (ADR 0022).
    tool_parser: str | None = None
    context: int | None = None
    max_output_tokens: int | None = None
    prefill_tps: float | None = None
    decode_tps: float | None = None
    gpu: bool = False
    registry_id: str | None = None
    notes: tuple[str, ...] = ()
    # The model reasons before answering, so a reasoning effort applies to it (TK10).
    reasoning: bool = False
    # The registry's tokenizer name for HANDOFF counts (SPEC §9), when it has one.
    tokenizer: str | None = None
    # Reported / predicted cached prompt tokens measured by `ssw calibrate --cache`.
    cache_ratio: float | None = None
    # Models that are not meant to drive agent turns (completion-only models) are used only
    # when `--worker` pins them.
    pin_only: bool = False

    @property
    def level(self) -> int:
        return LEVEL_BY_TIER.get(self.tier, 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "harness": self.harness,
            "endpoint": self.endpoint,
            "model": self.model,
            "format": self.format,
            "tier": self.tier,
            "level": self.level,
            "family": self.family,
            "local": self.local,
            "roles": list(self.roles),
            "tool_protocol": self.tool_protocol,
            "tool_parser": self.tool_parser,
            "context": self.context,
            "gpu": self.gpu,
            "pin_only": self.pin_only,
            "reasoning": self.reasoning,
            "tokenizer": self.tokenizer,
            "cache_ratio": self.cache_ratio,
        }


@dataclass
class Worker:
    """A worker spec plus the model that answers for it, created on first use."""

    spec: WorkerSpec
    model: StepModel | None = None
    factory: Callable[[], StepModel] | None = field(default=None, repr=False)

    def get_model(self) -> StepModel:
        if self.model is None:
            if self.factory is None:
                raise RuntimeError(f"worker {self.spec.id} has no model")
            self.model = self.factory()
        return self.model
