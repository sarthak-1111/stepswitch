"""Model names as slot hints (ADR 0004 item 4, ADR 0029).

A harness names a model on every request. The proxy reads the name as a hint:

* `ssw:auto` lets the router decide everything, `ssw:plan`, `ssw:edit`, `ssw:fast`,
  `ssw:review` and `ssw:summarize` name the step type the router routes as. Each also has a
  dash form (`ssw-auto`, ...) for harnesses whose model selectors reserve ':'.
* A worker ID, `<endpoint>/<model>`, model name or registry ID pins the request to that worker.
* `[proxy.aliases]` maps other names (a harness's own model names) to either of the above.
* Any other name is routed as `ssw:auto`, and the decision says so.

With `ssw:auto`, a harness's request-class header refines the step type: a compaction request
is a summary, an auxiliary one (titles, short side questions) is short text, and the rest are
edit steps when the request offers tools and text steps when it does not.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from stepswitch.compat.ir import Request
from stepswitch.proxy.sessions import header
from stepswitch.workers.catalog import Catalog

AUTO = "ssw:auto"
SLOTS: dict[str, str | None] = {
    AUTO: None,
    "ssw:plan": "plan",
    "ssw:edit": "edit",
    "ssw:fast": "navigate",
    "ssw:review": "review",
    "ssw:summarize": "summarize",
}
# The same slots for harnesses whose model selectors reserve ':' (`provider/model:thinking`).
DASH_SLOTS: dict[str, str] = {name.replace(":", "-"): name for name in SLOTS}
# The request-class hint of a supported harness (docs/RESEARCH.md, Claude Code) and the step
# types its classes stand for; classes not listed keep the inferred step type.
CLASS_HEADERS = ("x-claude-code-request-class",)
CLASS_STEPS = {"compaction": "summarize", "auxiliary": "write-text"}


@dataclass(frozen=True)
class Slot:
    name: str
    step_type: str
    pin: str | None = None
    request_class: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "step_type": self.step_type,
            "pin": self.pin,
            "request_class": self.request_class,
            "note": self.note,
        }


def request_class(headers: Mapping[str, str]) -> str | None:
    return next((v for name in CLASS_HEADERS if (v := header(headers, name))), None)


def inferred_step(request: Request, klass: str | None) -> str:
    if klass in CLASS_STEPS:
        return CLASS_STEPS[klass]
    return "edit" if request.tools else "write-text"


def resolve_slot(
    request: Request,
    headers: Mapping[str, str],
    catalog: Catalog,
    aliases: Mapping[str, str] | None = None,
) -> Slot:
    name = request.model.strip()
    klass = request_class(headers)
    target = (aliases or {}).get(name, name)
    target = DASH_SLOTS.get(target, target)
    if target in SLOTS:
        return Slot(name, SLOTS[target] or inferred_step(request, klass), None, klass)
    worker = catalog.find(target) if target else None
    if worker is not None:
        return Slot(name, inferred_step(request, klass), worker.spec.id, klass)
    note = f"unknown model {name!r}: routed as {AUTO}" if name else f"no model: routed as {AUTO}"
    return Slot(name, inferred_step(request, klass), None, klass, note)


def listed_models(catalog: Catalog) -> list[str]:
    """What `/v1/models` and `/api/tags` list: the slots, then every worker ID."""
    return [*SLOTS, *sorted(w.spec.id for w in catalog)]
