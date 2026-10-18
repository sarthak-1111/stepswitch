"""Cost model (SPEC §7.1 item 4, §7.2; ADR 0005, ADR 0024).

Time comes from calibrated throughput (`ssw calibrate`) when a worker has it, otherwise from
default figures per tier. A model cannot reuse another model's prefix cache, so a worker whose
server does not hold the current transcript must prefill the uncached part again; that time is
the switch cost. Above a threshold of uncached tokens a switch sends a HANDOFF brief (SPEC §9)
instead of the transcript: the new worker then reads the stable prefix and the brief, the brief
takes time to write, and the new worker's chance of success is lower than a fresh start
(`handoff_success`).

For the current worker c and another worker m, with transcript length T, the tokens S(w) of the
transcript that each worker's server already holds (the prefix registry, `router.cache`),
expected output O, prefill and decode throughput P and D, stable prefix X, brief size B, brief
writing time H, handoff recovery r and value of success V:

    uncached(w)      = T - S(w)
    mode             = brief if uncached(m) > threshold else transcript
    re-read(m)       = uncached(m)                          (transcript)
                     = X - min(S(m), X) + B                 (brief)
    switch_cost(m)   = re-read(m) / P(m)  (+ H for a brief)
    p'(m)            = p(m)                                 (transcript)
                     = handoff_success(p(m), p(c), r)       (brief)
    expected_gain(m) = (p'(m) - p(c)) * V + O / D(c) + uncached(c) / P(c) - O / D(m)
    switch iff expected_gain(m) - switch_cost(m) > margin   (`should_switch`)

The gain counts what staying costs too: the current worker decodes at its own speed and must
also read whatever its server has not seen (new tool results). p includes rule bonuses.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

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
# SPEC §7.2: above this many uncached tokens a switch sends a HANDOFF brief.
HANDOFF_THRESHOLD_TOKENS = 24_000
# SPEC §9: a brief's target size, used as its size before it is written.
HANDOFF_BRIEF_TOKENS = 1_500
# Share of the success gap to a better worker that a brief handoff recovers, before any outcomes
# were seen: published results recover less than half of it (ADR 0005). Learned per project by
# `router.calibration.HandoffRecovery`.
DEFAULT_HANDOFF_RECOVERY = 0.45

SwitchMode = Literal["transcript", "brief"]


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


@dataclass(frozen=True)
class SwitchTerms:
    """The terms of SPEC §7.2 for switching from the current worker to `worker`."""

    worker: str
    mode: SwitchMode
    reread_tokens: int
    cost_seconds: float
    gain_seconds: float
    p_success: float

    @property
    def value(self) -> float:
        return self.gain_seconds - self.cost_seconds

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reread_tokens": self.reread_tokens,
            "cost_seconds": round(self.cost_seconds, 3),
            "gain_seconds": round(self.gain_seconds, 3),
            "p_success": round(self.p_success, 4),
        }


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


def handoff_success(p_new: float, p_current: float, recovery: float) -> float:
    """p_success of a worker that continues from a brief: it recovers `recovery` of the gap to a
    better worker (ADR 0005); a worker that is not better keeps its own rate."""
    if p_new <= p_current:
        return p_new
    return p_current + max(0.0, min(1.0, recovery)) * (p_new - p_current)


def switch_terms(
    current: WorkerSpec,
    p_current: float,
    new: WorkerSpec,
    p_new: float,
    *,
    transcript_tokens: int,
    output_tokens: int,
    cached_current: int,
    cached_new: int,
    value_of_success: float,
    prefix_tokens: int = 0,
    brief_tokens: int = HANDOFF_BRIEF_TOKENS,
    handoff_seconds: float = 0.0,
    recovery: float = DEFAULT_HANDOFF_RECOVERY,
    threshold: int = HANDOFF_THRESHOLD_TOKENS,
    handoff_allowed: bool = True,
) -> SwitchTerms:
    """The module docstring's formulas for one candidate."""
    total = max(0, transcript_tokens)
    held_current = min(max(0, cached_current), total)
    held_new = min(max(0, cached_new), total)
    prefill_current, decode_current = throughput(current)
    prefill_new, decode_new = throughput(new)
    stay_seconds = output_tokens / decode_current + (total - held_current) / prefill_current
    uncached_new = total - held_new
    mode: SwitchMode
    if handoff_allowed and uncached_new > threshold:
        prefix = min(max(0, prefix_tokens), total)
        reread = prefix - min(held_new, prefix) + max(0, brief_tokens)
        cost = reread / prefill_new + max(0.0, handoff_seconds)
        p_used = handoff_success(p_new, p_current, recovery)
        mode = "brief"
    else:
        reread = uncached_new
        cost = reread / prefill_new
        p_used = p_new
        mode = "transcript"
    gain = (p_used - p_current) * value_of_success + stay_seconds - output_tokens / decode_new
    return SwitchTerms(new.id, mode, reread, cost, gain, p_used)
