"""`route()`: pick a worker for one step (SPEC §7.1, ARCHITECTURE §4, ADR 0005, ADR 0015).

A pure function: task features, step type, router state and the candidate workers in; a
`Decision` out, recording every candidate with its prior, estimate, score or exclusion reason,
the chosen worker and a one-line reason. Decisions are deterministic for fixed inputs (ties
break by worker id); the seed is recorded for the learned policy of later phases.

Pipeline:

1. Hard rules, never overridden: workers that failed in this task, offline mode (local
   endpoints only), remote endpoints unless allowed, a transcript longer than the worker's
   context, T0 never edits code in the science track (working rule 10), `--worker` pins, and
   `exclude` selectors of matching `[[policy.rules]]`.
2. Explicit routing: the first matching rule with a `to` selector that leaves at least one
   worker pins the step to the workers it selects.
3. The escalation rung (SPEC §7.3): without a pinning rule, a step may use workers up to the
   current rung, and heavy steps (all but navigate, summarize and write-text) use workers at
   the rung itself, so after an escalation the rung that failed no longer does the work. If
   nothing is left, the nearest rung that has an eligible worker is used and the decision says
   so.
4. Priors (`policy.prior`, with measured base rates where `ssw bench calibrate` has them) plus
   `prefer` bonuses, and the cost model: score = (prior + bonus) * value_of_success - estimated
   seconds.
5. Cache-aware switching (SPEC §7.2, `cost.switch_terms`): the previous step's worker keeps
   the step unless another worker's expected gain minus its switch cost (re-reading what its
   server has not cached, or reading a HANDOFF brief above the threshold) exceeds the margin;
   the worker with the largest margin wins. The previous worker leaves when it is no longer
   eligible, and stays while a tool-call loop is open or the next turn depends on state only
   its endpoint holds.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from stepswitch.core.task import CODE_STEPS
from stepswitch.router.cost import (
    DEFAULT_HANDOFF_RECOVERY,
    HANDOFF_BRIEF_TOKENS,
    HANDOFF_THRESHOLD_TOKENS,
    SWITCH_MARGIN_SECONDS,
    VALUE_OF_SUCCESS_SECONDS,
    Estimate,
    SwitchTerms,
    estimate,
    should_switch,
    switch_terms,
)
from stepswitch.router.escalation import RUNG_NAMES
from stepswitch.router.features import TaskFeatures
from stepswitch.router.policy import LIGHT_STEPS, prior
from stepswitch.router.rules import Rule, selector_matches, when_matches
from stepswitch.workers.base import WorkerSpec


@dataclass
class RouterState:
    step: int
    current: str | None = None
    # The escalation rung (0 = T0, 1 = T1/T2, 2 = T3, 3 = remote).
    level: int = 0
    transcript_tokens: int = 0
    output_tokens: int = 512
    # Tokens of the current transcript that each worker's server has already processed.
    seen_tokens: dict[str, int] = field(default_factory=dict)
    # The next turn depends on state only the current endpoint holds (signed reasoning,
    # stored responses): stay unless the current worker is excluded.
    pinned: bool = False
    # The current worker's last reply has tool calls whose results are not all in the
    # transcript yet (SPEC §7.2): stay unless the current worker is excluded.
    open_tool_calls: bool = False
    # Cache-aware switching (SPEC §7.2): the size of the stable prefix (system prompt and
    # tools), the expected brief size, the time to write a brief, the share of the success gap
    # a brief recovers, and the uncached tokens above which a switch sends a brief.
    prefix_tokens: int = 0
    brief_tokens: int = HANDOFF_BRIEF_TOKENS
    handoff_seconds: float = 0.0
    handoff_recovery: float = DEFAULT_HANDOFF_RECOVERY
    handoff_threshold: int = HANDOFF_THRESHOLD_TOKENS
    # False in the unoptimized baseline (`ssw bench run --baseline`): switches always send the
    # transcript.
    handoff_allowed: bool = True
    track: str = "general"
    offline: bool = False
    allow_remote: bool = False
    worker_pin: str | None = None
    failed: frozenset[str] = frozenset()
    # Prompt sizes (estimated tokens) at which a worker's server refused the context.
    context_caps: dict[str, int] = field(default_factory=dict)
    # Measured base success rates per worker id (`ssw bench calibrate`), replacing the tier's.
    base_rates: dict[str, float] = field(default_factory=dict)
    # Best-of-N (SPEC §8.3, ADR 0025): a candidate's steps go only to workers of its model
    # families, except reviews, which prefer workers outside `avoid_families` (the families
    # that wrote the change; SPEC §8.5).
    families: frozenset[str] | None = None
    avoid_families: frozenset[str] = frozenset()
    # The learning router (SPEC §10, ADR 0026): one sampled task-level success per worker id,
    # drawn at the start of the task, which replaces the static task-level prior.
    learned: dict[str, float] = field(default_factory=dict)


@dataclass
class Candidate:
    worker: str
    endpoint: str
    model: str
    tier: str
    level: int
    prior: float | None = None
    bonus: float = 0.0
    estimate: dict[str, Any] | None = None
    score: float | None = None
    excluded: str | None = None
    # True when the escalation rung would exclude this worker but nothing else was eligible.
    relaxed: bool = False
    # The learning router's sampled task-level success, when it set the prior.
    learned: float | None = None
    # SPEC §7.2 terms for switching from the current worker to this one (`SwitchTerms`).
    switch: dict[str, Any] | None = None


@dataclass
class Decision:
    step: int
    step_type: str
    chosen: str | None
    previous: str | None
    switched: bool
    reason: str
    rule: str | None
    candidates: list[Candidate]
    level: int
    seed: int
    expected_gain: float | None = None
    switch_cost: dict[str, float] | None = None
    estimate: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    # The chosen worker continues from a HANDOFF brief instead of the transcript (SPEC §7.2).
    handoff: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def rung_name(level: int) -> str:
    return RUNG_NAMES.get(level, str(level))


def hard_exclusion(spec: WorkerSpec, step_type: str, state: RouterState) -> str | None:
    if spec.id in state.failed:
        return "failed earlier in this task"
    if state.offline and not spec.local:
        return "offline mode allows local endpoints only"
    if state.worker_pin is not None and spec.id != state.worker_pin:
        return f"--worker pins {state.worker_pin}"
    if spec.pin_only and spec.id != state.worker_pin:
        return "used only when pinned with --worker"
    if not spec.local and not state.allow_remote and spec.id != state.worker_pin:
        return "remote endpoints are opt-in (top of the escalation ladder)"
    if spec.context and state.transcript_tokens + state.output_tokens > spec.context:
        return f"context {spec.context} is too small for ~{state.transcript_tokens} tokens"
    cap = state.context_caps.get(spec.id)
    if cap is not None and state.transcript_tokens >= cap:
        return f"its server refused a prompt of ~{cap} tokens as too long"
    if state.track == "science" and spec.level == 0 and step_type in CODE_STEPS:
        return "T0 models never edit code in the science track"
    if (
        state.families is not None
        and step_type != "review"
        and spec.family_key not in state.families
    ):
        return "another best-of-N candidate's model family"
    return None


def rung_exclusion(spec: WorkerSpec, step_type: str, level: int) -> str | None:
    if spec.level > level:
        return f"above the current rung ({rung_name(level)})"
    if spec.level < level and step_type not in LIGHT_STEPS:
        return f"below the current rung ({rung_name(level)}) for {step_type} steps"
    return None


def _prefer_other_families(
    workers: Sequence[WorkerSpec],
    candidates: dict[str, Candidate],
    rung_excluded: set[str],
    state: RouterState,
    notes: list[str],
    exclude: Callable[[str, str], None],
) -> None:
    """A review goes to a model family that did not write the change (SPEC §8.5), on another
    rung when this one has none; with no other family at all, the author's family reviews."""
    avoid = state.avoid_families
    eligible = [s for s in workers if candidates[s.id].excluded is None]
    if not any(s.family_key not in avoid for s in eligible):
        others = [s for s in workers if s.id in rung_excluded and s.family_key not in avoid]
        if not others:
            notes.append("no worker of another model family for the review")
            return
        above = sorted({s.level for s in others if s.level > state.level})
        below = sorted({s.level for s in others if s.level < state.level}, reverse=True)
        target = above[0] if above else below[0]
        notes.append(f"cross-family review on rung {rung_name(target)}")
        for spec in others:
            if spec.level == target:
                candidates[spec.id].excluded = None
                candidates[spec.id].relaxed = True
    for spec in eligible:
        if spec.family_key in avoid:
            exclude(spec.id, "same model family as the change's author (cross-family review)")


def route(
    features: TaskFeatures,
    step_type: str,
    state: RouterState,
    workers: Sequence[WorkerSpec],
    rules: Sequence[Rule] = (),
    *,
    seed: int = 0,
    margin: float = SWITCH_MARGIN_SECONDS,
    value_of_success: float = VALUE_OF_SUCCESS_SECONDS,
) -> Decision:
    candidates = {
        spec.id: Candidate(spec.id, spec.endpoint, spec.model, spec.tier, spec.level)
        for spec in workers
    }
    notes: list[str] = []

    def exclude(worker_id: str, why: str) -> None:
        if candidates[worker_id].excluded is None:
            candidates[worker_id].excluded = why

    for spec in workers:
        why = hard_exclusion(spec, step_type, state)
        if why:
            exclude(spec.id, why)
    if state.worker_pin is not None and state.worker_pin not in candidates:
        notes.append(f"pinned worker {state.worker_pin} is not in the catalog")

    # User rules: excludes and bonuses from every matching rule, the first pinning `to` wins.
    rule_name: str | None = "--worker" if state.worker_pin is not None else None
    pinned_set: set[str] | None = None
    context = {**features.context(step_type), "track": state.track}
    if state.worker_pin is None:
        matching = [rule for rule in rules if when_matches(rule.when, context)]
        for rule in matching:
            for spec in workers:
                if rule.exclude and selector_matches(rule.exclude, spec):
                    exclude(spec.id, f"rule {rule.name} excludes it")
                if rule.prefer and selector_matches(rule.prefer, spec):
                    candidates[spec.id].bonus += rule.bonus
        for rule in matching:
            if not rule.to:
                continue
            selected = {
                s.id
                for s in workers
                if candidates[s.id].excluded is None and selector_matches(rule.to, s)
            }
            if selected:
                rule_name, pinned_set = rule.name, selected
                break
            notes.append(f"rule {rule.name} skipped: no eligible worker matches it")
    if pinned_set is not None:
        for spec in workers:
            if spec.id not in pinned_set:
                exclude(spec.id, f"rule {rule_name} routes {step_type} steps elsewhere")
    rung_excluded: set[str] = set()
    if pinned_set is None and state.worker_pin is None:
        hard_ok = [s for s in workers if candidates[s.id].excluded is None]
        in_rung = [s for s in hard_ok if rung_exclusion(s, step_type, state.level) is None]
        if hard_ok and not in_rung:
            above = sorted({s.level for s in hard_ok if s.level > state.level})
            below = sorted({s.level for s in hard_ok if s.level < state.level}, reverse=True)
            target = above[0] if above else below[0]
            notes.append(
                f"no eligible worker on rung {rung_name(state.level)} for {step_type};"
                f" using rung {rung_name(target)}"
            )
            for spec in hard_ok:
                if spec.level == target:
                    candidates[spec.id].relaxed = True
        for spec in hard_ok:
            if not candidates[spec.id].relaxed:
                why = rung_exclusion(spec, step_type, state.level)
                if why:
                    exclude(spec.id, why)
                    rung_excluded.add(spec.id)
    if (
        step_type == "review"
        and state.avoid_families
        and pinned_set is None
        and state.worker_pin is None
    ):
        _prefer_other_families(workers, candidates, rung_excluded, state, notes, exclude)

    estimates: dict[str, Estimate] = {}
    for spec in workers:
        seen = state.seen_tokens.get(spec.id, 0)
        est = estimate(
            spec,
            input_tokens=state.transcript_tokens,
            output_tokens=state.output_tokens,
            uncached_tokens=max(0, state.transcript_tokens - seen),
        )
        estimates[spec.id] = est
        cand = candidates[spec.id]
        base = state.base_rates.get(spec.id)
        cand.prior = prior(spec, step_type, features, base=base)
        sampled = state.learned.get(spec.id)
        if sampled is not None:
            # The sample stands for the task-level (edit) prior; the step type's adjustment
            # relative to it stays.
            shift = cand.prior - prior(spec, "edit", features, base=base)
            cand.learned = sampled
            cand.prior = round(min(0.98, max(0.02, sampled + shift)), 4)
        cand.estimate = est.as_dict()
        cand.score = round((cand.prior + cand.bonus) * value_of_success - est.seconds, 3)

    eligible = sorted(
        (c for c in candidates.values() if c.excluded is None),
        key=lambda c: (-(c.score or 0.0), c.worker),
    )
    previous = state.current
    if not eligible:
        reasons = "; ".join(f"{c.worker}: {c.excluded}" for c in candidates.values())
        return Decision(
            step=state.step,
            step_type=step_type,
            chosen=None,
            previous=previous,
            switched=False,
            reason=f"no eligible worker for {step_type} ({reasons or 'no workers configured'})",
            rule=rule_name,
            candidates=list(candidates.values()),
            level=state.level,
            seed=seed,
            notes=notes,
        )

    best = eligible[0]
    current = candidates.get(previous) if previous else None
    specs = {spec.id: spec for spec in workers}
    chosen = best
    gain: float | None = None
    cost = {"seconds": 0.0, "tokens": 0.0}
    handoff = False
    best_est = estimates[best.worker]
    p_text = f"p={best.prior:.2f}" + (f"+{best.bonus:.2f}" if best.bonus else "")
    why_best = f"rule {rule_name}" if rule_name else "best expected value"

    def terms_from(origin: Candidate, target: Candidate) -> SwitchTerms:
        return switch_terms(
            specs[origin.worker],
            (origin.prior or 0.0) + origin.bonus,
            specs[target.worker],
            (target.prior or 0.0) + target.bonus,
            transcript_tokens=state.transcript_tokens,
            output_tokens=state.output_tokens,
            cached_current=state.seen_tokens.get(origin.worker, 0),
            cached_new=state.seen_tokens.get(target.worker, 0),
            value_of_success=value_of_success,
            prefix_tokens=state.prefix_tokens,
            brief_tokens=state.brief_tokens,
            handoff_seconds=state.handoff_seconds,
            recovery=state.handoff_recovery,
            threshold=state.handoff_threshold,
            handoff_allowed=state.handoff_allowed,
        )

    if current is None or previous is None:
        # The first step, or the step after a model error took the previous worker out.
        uncached = state.transcript_tokens - min(
            state.seen_tokens.get(best.worker, 0), state.transcript_tokens
        )
        handoff = state.handoff_allowed and state.step > 1 and uncached > state.handoff_threshold
        reason = f"{best.worker} for {step_type}: {why_best} ({p_text}, ~{best_est.seconds:.1f} s)"
        if handoff:
            reason += f"; ~{uncached} uncached tokens, so it starts from a HANDOFF brief"
    else:
        options = []
        for cand in eligible:
            if cand.worker == previous:
                continue
            terms = terms_from(current, cand)
            cand.switch = terms.as_dict()
            options.append(terms)
        # The largest margin wins; ties go to the smaller worker id.
        top = min(options, key=lambda o: (-o.value, o.worker)) if options else None
        if top is not None:
            cost = {"seconds": round(top.cost_seconds, 3), "tokens": float(top.reread_tokens)}
            gain = round(top.gain_seconds, 3)
        reread = (
            f"reading a HANDOFF brief (~{int(cost['tokens'])} tokens) costs"
            if top is not None and top.mode == "brief"
            else f"re-reading ~{int(cost['tokens'])} uncached tokens costs"
        )
        if current.excluded is not None:
            if top is not None:
                chosen = candidates[top.worker]
                handoff = top.mode == "brief"
            reason = (
                f"switch {previous} -> {chosen.worker} for {step_type}: {current.excluded}"
                f" ({why_best}); {reread} ~{cost['seconds']:.1f} s"
            )
        elif state.pinned or state.open_tool_calls:
            chosen = current
            why = (
                "a tool-call loop is open"
                if state.open_tool_calls
                else "the next turn needs state only its endpoint holds"
            )
            reason = f"stay on {previous}: {why}"
        elif top is None:
            chosen = current
            reason = f"stay on {previous} for {step_type}: no other eligible worker"
        elif should_switch(top.gain_seconds, top.cost_seconds, margin):
            chosen = candidates[top.worker]
            handoff = top.mode == "brief"
            reason = (
                f"switch {previous} -> {chosen.worker} for {step_type}: gain {gain:.1f} s"
                f" - cost {cost['seconds']:.1f} s > margin {margin:.0f} s; {reread}"
                f" ~{cost['seconds']:.1f} s"
            )
        else:
            chosen = current
            reason = (
                f"stay on {previous} for {step_type}: switching to {top.worker} gains"
                f" {gain:.1f} s - cost {cost['seconds']:.1f} s <= margin {margin:.0f} s"
            )
    switched = previous is not None and chosen.worker != previous
    return Decision(
        step=state.step,
        step_type=step_type,
        chosen=chosen.worker,
        previous=previous,
        switched=switched,
        reason=reason,
        rule=rule_name,
        candidates=list(candidates.values()),
        level=state.level,
        seed=seed,
        expected_gain=gain if switched else None,
        switch_cost=cost if switched else None,
        estimate=estimates[chosen.worker].as_dict(),
        notes=notes,
        handoff=handoff,
    )
