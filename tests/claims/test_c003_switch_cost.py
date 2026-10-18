"""C-003: only switches models when the expected gain outweighs the cost of re-reading context.

Proof (SPEC §18.5): a property test over randomized scenarios: the router switches iff
gain - cost > margin, and stays otherwise. Scope: the router's switch decision under the
cache-aware cost model of SPEC §7.2.

The scenarios are drawn from a seeded generator, so every run checks the same ones: two to five
workers with random tiers, throughputs, contexts and roles, a random step type and task, a random
transcript with random cached prefixes per server, random handoff settings, sometimes a `prefer`
rule, a random margin and value of success. `route()` decides, and an oracle written from the
SPEC formulas (not from the router's code) decides again from the same inputs. The oracle takes
from the decision only what the switch rule takes as input: which workers are eligible and each
worker's prior plus rule bonus.

For the current worker c and another eligible worker m, with transcript length T, cached prefix
S(w) on each worker's server, expected output O, prefill and decode throughput P and D, stable
prefix X, brief size B, brief writing time H, handoff recovery r and value of success V:

    uncached(w)     = T - S(w)
    mode            = brief if uncached(m) > threshold, else transcript
    re-read(m)      = uncached(m)                      transcript
                    = X - min(S(m), X) + B             brief
    switch_cost(m)  = re-read(m) / P(m)  (+ H for a brief)
    p'(m)           = p(m)                             transcript
                    = p(c) + r * (p(m) - p(c))         brief, when p(m) > p(c)
    expected_gain(m) = (p'(m) - p(c)) * V + O / D(c) + uncached(c) / P(c) - O / D(m)

The router must switch iff the largest expected_gain(m) - switch_cost(m) exceeds the margin, and
then to that worker. Decisions where the switch rule does not apply are counted separately: the
current worker excluded (a forced switch), or held because the next turn needs state only its
endpoint holds or a tool-call loop is open (no switch).
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from stepswitch.core.task import STEP_TYPES
from stepswitch.router.cost import DEFAULT_THROUGHPUT, HANDOFF_THRESHOLD_TOKENS
from stepswitch.router.features import TaskFeatures
from stepswitch.router.router import Decision, RouterState, route
from stepswitch.router.rules import Rule
from stepswitch.workers.base import WorkerSpec
from tests.claims import evidence

pytestmark = [pytest.mark.claim("C-003")]

SEED = 20261019
SCENARIOS = 4000
TIERS = ("cpu", "t1", "t2", "t3")
ROLES = ("router", "summarizer", "planner", "editor", "coder", "reviewer")
TOLERANCE = 1e-6  # |value - margin| below this counts either way (float rounding)


def _worker(rng: random.Random, index: int) -> WorkerSpec:
    return WorkerSpec(
        id=f"w{index}",
        endpoint=f"e{index}",
        model=f"m{index}",
        tier=rng.choice(TIERS),
        roles=tuple(rng.sample(ROLES, rng.randint(0, 2))),
        tool_protocol=rng.choice(("native", "native", "fallback")),
        context=rng.choice((None, None, None, 8192, 32768, 131072)),
        prefill_tps=rng.choice((None, round(rng.uniform(40, 9000), 1))),
        decode_tps=rng.choice((None, round(rng.uniform(4, 160), 1))),
    )


def scenario(rng: random.Random) -> dict[str, Any]:
    workers = [_worker(rng, i) for i in range(rng.randint(2, 5))]
    transcript = rng.choice(
        (
            rng.randint(0, 4000),
            rng.randint(0, 40000),
            rng.randint(0, 150000),
            rng.randint(0, 150000),
        )
    )
    seen = {}
    for spec in workers:
        roll = rng.random()
        if roll < 0.3:
            seen[spec.id] = 0
        elif roll < 0.6:
            seen[spec.id] = rng.randint(0, transcript)
        else:
            seen[spec.id] = max(0, transcript - rng.randint(0, 3000))
    current = rng.choice(workers)
    # Mostly the current worker's own rung, so that the switch rule decides most scenarios.
    level = current.level if rng.random() < 0.7 else rng.choice((0, 1, 2, 3))
    state = RouterState(
        step=rng.randint(2, 40),
        current=current.id,
        level=level,
        transcript_tokens=transcript,
        output_tokens=rng.randint(50, 2500),
        seen_tokens=seen,
        pinned=rng.random() < 0.05,
        open_tool_calls=rng.random() < 0.05,
        prefix_tokens=rng.randint(300, 5000),
        brief_tokens=rng.randint(400, 1500),
        handoff_seconds=round(rng.uniform(0, 120), 3),
        handoff_recovery=round(rng.uniform(0, 1), 3),
        handoff_threshold=rng.choice(
            (HANDOFF_THRESHOLD_TOKENS, rng.randint(500, 60000), rng.randint(500, 8000))
        ),
        allow_remote=True,
    )
    rules: list[Rule] = []
    if rng.random() < 0.3:
        rules.append(
            Rule(
                name="prefer-a-tier",
                prefer={"tier": rng.choice(TIERS)},
                bonus=round(rng.uniform(0, 0.3), 3),
            )
        )
    return {
        "workers": workers,
        "features": TaskFeatures(
            difficulty=rng.randint(1, 5),
            risk=rng.choice(("low", "med", "high")),
            task_type=rng.choice(("bugfix", "feature", "refactor")),
        ),
        "step_type": rng.choice(STEP_TYPES),
        "state": state,
        "rules": rules,
        "margin": round(rng.uniform(0, 60), 3),
        "value": round(rng.uniform(100, 1200), 3),
    }


def throughput(spec: WorkerSpec) -> tuple[float, float]:
    prefill, decode = DEFAULT_THROUGHPUT[spec.tier]
    return spec.prefill_tps or prefill, spec.decode_tps or decode


def oracle(case: dict[str, Any], decision: Decision) -> dict[str, Any]:
    """The SPEC §7.2 decision for `case`, given eligibility and priors from `decision`."""
    state: RouterState = case["state"]
    specs = {spec.id: spec for spec in case["workers"]}
    candidates = {c.worker: c for c in decision.candidates}
    current = state.current
    assert current is not None
    if candidates[current].excluded is not None:
        return {"category": "forced"}
    if state.pinned or state.open_tool_calls:
        return {"category": "held"}

    def p(worker: str) -> float:
        cand = candidates[worker]
        assert cand.prior is not None
        return cand.prior + cand.bonus

    total = state.transcript_tokens
    out = state.output_tokens
    cached = {w: min(state.seen_tokens.get(w, 0), total) for w in specs}
    p_cur = p(current)
    prefill_cur, decode_cur = throughput(specs[current])
    stay_seconds = out / decode_cur + (total - cached[current]) / prefill_cur
    options = []
    for worker, cand in sorted(candidates.items()):
        if worker == current or cand.excluded is not None:
            continue
        prefill, decode = throughput(specs[worker])
        uncached = total - cached[worker]
        if uncached > state.handoff_threshold:
            prefix = min(state.prefix_tokens, total)
            reread = prefix - min(cached[worker], prefix) + state.brief_tokens
            cost = reread / prefill + state.handoff_seconds
            p_new = p(worker)
            if p_new > p_cur:
                p_new = p_cur + state.handoff_recovery * (p_new - p_cur)
            mode = "brief"
        else:
            reread = uncached
            cost = reread / prefill
            p_new = p(worker)
            mode = "transcript"
        gain = (p_new - p_cur) * case["value"] + stay_seconds - out / decode
        options.append((gain - cost, worker, gain, cost, mode, reread))
    if not options:
        return {"category": "cost", "switch": False, "boundary": False}
    # Highest value first; ties go to the smaller worker id.
    value, worker, gain, cost, mode, reread = min(options, key=lambda o: (-o[0], o[1]))
    return {
        "category": "cost",
        "switch": value > case["margin"],
        "boundary": abs(value - case["margin"]) < TOLERANCE,
        "worker": worker,
        "gain": gain,
        "cost": cost,
        "mode": mode,
        "reread": reread,
    }


def check(case: dict[str, Any], decision: Decision, expected: dict[str, Any]) -> str | None:
    """None when the decision agrees with the oracle, else what went wrong."""
    current = case["state"].current
    if expected["category"] == "forced":
        if decision.chosen is not None and decision.chosen == current:
            return "stayed on an excluded worker"
        return None
    if expected["category"] == "held":
        return None if decision.chosen == current and not decision.switched else "switched"
    if expected["boundary"]:
        return None
    if decision.switched != expected["switch"]:
        return f"switched={decision.switched}, expected {expected['switch']}"
    if not expected["switch"]:
        return None if decision.chosen == current else f"chose {decision.chosen} without a switch"
    if decision.chosen != expected["worker"]:
        return f"switched to {decision.chosen}, expected {expected['worker']}"
    if decision.expected_gain is None or abs(decision.expected_gain - expected["gain"]) > 0.01:
        return f"expected_gain {decision.expected_gain} != {expected['gain']:.3f}"
    cost = (decision.switch_cost or {}).get("seconds")
    if cost is None or abs(cost - expected["cost"]) > 0.01:
        return f"switch cost {cost} != {expected['cost']:.3f}"
    if decision.handoff != (expected["mode"] == "brief"):
        return f"handoff={decision.handoff}, expected mode {expected['mode']}"
    return None


def test_switch_iff_gain_exceeds_cost_plus_margin() -> None:
    limits = evidence.thresholds("C-003")
    rng = random.Random(SEED)
    counts = {"forced": 0, "held": 0, "cost": 0, "switch": 0, "stay": 0, "brief": 0}
    violations: list[dict[str, Any]] = []
    for index in range(SCENARIOS):
        case = scenario(rng)
        decision = route(
            case["features"],
            case["step_type"],
            case["state"],
            case["workers"],
            case["rules"],
            seed=index,
            margin=case["margin"],
            value_of_success=case["value"],
        )
        expected = oracle(case, decision)
        counts[expected["category"]] += 1
        if expected["category"] == "cost":
            counts["switch" if expected["switch"] else "stay"] += 1
            if expected["switch"] and expected["mode"] == "brief":
                counts["brief"] += 1
        problem = check(case, decision, expected)
        if problem is not None:
            violations.append({"scenario": index, "problem": problem, "reason": decision.reason})
    evidence.write(
        "C-003",
        "property",
        {
            "seed": SEED,
            "scenarios": SCENARIOS,
            "cost_model_decisions": counts["cost"],
            "switches": counts["switch"],
            "stays": counts["stay"],
            "brief_handoffs": counts["brief"],
            "forced_switches": counts["forced"],
            "held": counts["held"],
            "violations": len(violations),
            "first_violations": violations[:10],
        },
    )
    assert counts["cost"] >= limits["min_examples"], counts
    # Both outcomes and both switch modes must occur, or the property says little.
    assert counts["switch"] >= counts["cost"] // 10, counts
    assert counts["stay"] >= counts["cost"] // 10, counts
    assert counts["brief"] >= 20, counts
    assert len(violations) <= limits["violations"], violations[:10]
