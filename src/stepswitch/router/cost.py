"""Cost model (SPEC §7.1 item 4, §7.2; ADR 0005).

Phase 2 estimates time from default throughput per tier unless the worker has calibrated
figures (`ssw calibrate`). Switching to a worker whose server has not seen the current
transcript means it must prefill the uncached part again; that time is the switch cost.

    switch_cost(m)   = uncached_tokens(m) / prefill_tps(m) + handoff_cost
    expected_gain(m) = (p_success(m) - p_success(current)) * value_of_success_seconds
    switch iff expected_gain(m) - switch_cost(m) > margin            (`should_switch`)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from stepswitch.workers.base import WorkerSpec

# (prefill tokens/s, decode tokens/s) when a worker has no calibration (rough, conservative).
DEFAULT_THROUGHPUT: dict[str, tuple[float, float]] = {
    "cpu": (150.0, 12.0),
    "t1": (1500.0, 45.0),
    "t2": (2500.0, 35.0),
    "t3": (4000.0, 60.0),
    "remote": (3000.0, 60.0),
}
VALUE_OF_SUCCESS_SECONDS = 600.0
SWITCH_MARGIN_SECONDS = 30.0


@dataclass(frozen=True)
class Estimate:
    input_tokens: int
    output_tokens: int
    seconds: float
    switch_tokens: int = 0
    switch_seconds: float = 0.0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "tokens": self.tokens}


def throughput(spec: WorkerSpec) -> tuple[float, float]:
    default = DEFAULT_THROUGHPUT.get(spec.tier, DEFAULT_THROUGHPUT["t1"])
    return (spec.prefill_tps or default[0], spec.decode_tps or default[1])


def estimate(
    spec: WorkerSpec, *, input_tokens: int, output_tokens: int, uncached_tokens: int
) -> Estimate:
    """Time for one step on `spec`: uncached prefill plus decode. `uncached_tokens` is the part
    of the input the worker's server has not seen (all of it for a worker that never ran)."""
    prefill, decode = throughput(spec)
    uncached = max(0, min(uncached_tokens, input_tokens))
    seconds = uncached / prefill + output_tokens / decode
    return Estimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        seconds=round(seconds, 3),
        switch_tokens=uncached,
        switch_seconds=round(uncached / prefill, 3),
    )


def switch_cost(spec: WorkerSpec, uncached_tokens: int, handoff_seconds: float = 0.0) -> float:
    prefill, _ = throughput(spec)
    return max(0, uncached_tokens) / prefill + handoff_seconds


def expected_gain(p_new: float, p_current: float, value_of_success: float) -> float:
    return (p_new - p_current) * value_of_success


def should_switch(gain: float, cost: float, margin: float) -> bool:
    """SPEC §7.2: switch only when the expected gain outweighs the cost by more than a margin."""
    return gain - cost > margin
