"""Routing rules as data (SPEC §7.1 item 1, ADR 0005).

Rules live in stepswitch.toml as `[[policy.rules]]`:

    [[policy.rules]]
    name = "plan-on-big-model"
    when = { step_type = ["plan", "review"], difficulty = ">=3" }
    to = { tier = ["t2", "t3"] }          # pin: only matching workers (first matching rule wins)
    exclude = { family = "gpt-oss" }     # remove matching workers (every matching rule applies)
    prefer = { role = "planner" }        # score bonus for matching workers

`when` matches task features plus `step_type` and `track`; a value is a scalar (equality), a
list (any of), or a comparison string (">=3", "<=2", ">1", "<4", "!=low") for numbers and
strings. Selectors match worker fields: id, endpoint, model, tier, family, harness, role
(any of the worker's roles), local, format. The matcher never evaluates code.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from stepswitch.workers.base import WorkerSpec

_COMPARISON = re.compile(r"^\s*(>=|<=|!=|==|>|<)\s*(.+?)\s*$")
_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "!=": operator.ne,
    "==": operator.eq,
}
SELECTOR_FIELDS = (
    "id",
    "endpoint",
    "model",
    "tier",
    "family",
    "harness",
    "role",
    "local",
    "format",
)


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    name: str
    when: Mapping[str, Any] = field(default_factory=dict)
    to: Mapping[str, Any] | None = None
    exclude: Mapping[str, Any] | None = None
    prefer: Mapping[str, Any] | None = None
    bonus: float = 0.1


def _coerce(value: str, like: Any) -> Any:
    if isinstance(like, bool):
        return value.lower() in {"true", "1", "yes"}
    if isinstance(like, int | float):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def value_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list | tuple | set | frozenset):
        return any(value_matches(item, actual) for item in expected)
    if isinstance(expected, str) and not isinstance(actual, str):
        if actual is None:
            return False
        found = _COMPARISON.match(expected)
        if found:
            op, raw = found.groups()
            try:
                return bool(_OPERATORS[op](actual, _coerce(raw, actual)))
            except TypeError:
                return False
        # "true" for a boolean feature, "3" for a number
        return bool(_coerce(expected, actual) == actual)
    if isinstance(expected, str) and isinstance(actual, str):
        found = _COMPARISON.match(expected)
        if found and found.group(1) == "!=":
            return actual != found.group(2)
        return expected == actual
    return bool(expected == actual)


def when_matches(when: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    return all(value_matches(expected, context.get(key)) for key, expected in when.items())


def selector_matches(selector: Mapping[str, Any], spec: WorkerSpec) -> bool:
    for key, expected in selector.items():
        if key == "role":
            wanted = expected if isinstance(expected, list | tuple) else [expected]
            if not set(wanted) & set(spec.roles):
                return False
        elif key in SELECTOR_FIELDS:
            if not value_matches(expected, getattr(spec, key)):
                return False
        else:
            raise RuleError(
                f"unknown worker field {key!r} (use one of {', '.join(SELECTOR_FIELDS)})"
            )
    return True


def parse_rules(raw: Sequence[Mapping[str, Any]]) -> list[Rule]:
    rules = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise RuleError(f"policy.rules[{index}] must be a table")
        unknown = set(item) - {"name", "when", "to", "exclude", "prefer", "bonus"}
        if unknown:
            raise RuleError(f"policy.rules[{index}]: unknown keys {sorted(unknown)}")
        if not any(item.get(k) for k in ("to", "exclude", "prefer")):
            raise RuleError(f"policy.rules[{index}] needs `to`, `exclude` or `prefer`")
        for key in ("to", "exclude", "prefer"):
            selector = item.get(key)
            if selector is not None:
                bad = set(selector) - set(SELECTOR_FIELDS)
                if bad:
                    raise RuleError(f"policy.rules[{index}].{key}: unknown fields {sorted(bad)}")
        rules.append(
            Rule(
                name=str(item.get("name") or f"rule-{index + 1}"),
                when=dict(item.get("when") or {}),
                to=dict(item["to"]) if item.get("to") else None,
                exclude=dict(item["exclude"]) if item.get("exclude") else None,
                prefer=dict(item["prefer"]) if item.get("prefer") else None,
                bonus=float(item.get("bonus", 0.1)),
            )
        )
    return rules


def rules_from_config(config: Any) -> list[Rule]:
    """`[[policy.rules]]` from a stepswitch Config (unknown sections are kept as extras)."""
    policy = getattr(config, "policy", None)
    if policy is None and hasattr(config, "model_extra"):
        policy = (config.model_extra or {}).get("policy")
    if isinstance(policy, Mapping):
        return parse_rules(list(policy.get("rules") or []))
    return []
