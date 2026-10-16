"""Deterministic scripted workers for tests, the bench and replay (ARCHITECTURE §7).

A `ScriptedModel` answers each turn from a script: a list of actions (cycled) or a function of
the request and the turn index. Usage, wall time and GPU-seconds per turn are fixed, so budget
and accounting behaviour can be checked exactly. A request without tools always gets a text
answer, since a model cannot call tools it was not offered.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from stepswitch.compat.client import Completion
from stepswitch.compat.ir import (
    ConversionReport,
    Message,
    Part,
    Request,
    Response,
    TextPart,
    ToolCallPart,
    Usage,
)


@dataclass(frozen=True)
class Action:
    """One scripted turn: a tool call (`tool` set) or a text answer."""

    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    text: str = ""


class FakeClock:
    """A monotonic clock that only moves when told to."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


Script = Sequence[Action] | Callable[[Request, int], Action]


class ScriptedModel:
    def __init__(
        self,
        script: Script,
        *,
        usage: Usage | None = None,
        seconds_per_step: float = 0.0,
        gpu_seconds_per_step: float = 0.0,
        clock: FakeClock | None = None,
        model: str = "scripted",
    ) -> None:
        self.script = script
        self.usage = usage or Usage(input_tokens=0, output_tokens=0)
        self.seconds_per_step = seconds_per_step
        self.gpu_seconds_per_step = gpu_seconds_per_step
        self.clock = clock
        self.model = model
        self.calls = 0
        self.requests: list[Request] = []

    def _action(self, request: Request) -> Action:
        if callable(self.script):
            return self.script(request, self.calls)
        if not self.script:
            return Action(text="Nothing to do.")
        return self.script[self.calls % len(self.script)]

    def complete(self, request: Request) -> Completion:
        action = self._action(request)
        self.requests.append(request)
        index = self.calls
        self.calls += 1
        if self.clock is not None and self.seconds_per_step:
            self.clock.advance(self.seconds_per_step)
        offered = {tool.name for tool in request.tools}
        parts: list[Part] = []
        if action.tool and action.tool in offered:
            if action.text:
                parts.append(TextPart(text=action.text))
            parts.append(
                ToolCallPart(
                    id=f"fake_{index}", name=action.tool, arguments=json.dumps(action.arguments)
                )
            )
            finish = "tool_calls"
        else:
            parts.append(TextPart(text=action.text or "Done."))
            finish = "stop"
        raw: dict[str, Any] = {"source": "scripted"}
        if self.gpu_seconds_per_step:
            raw["gpu_seconds"] = self.gpu_seconds_per_step
        usage = self.usage.model_copy(update={"raw": raw})
        response = Response(
            id=f"scripted-{index}",
            model=self.model,
            message=Message(role="assistant", content=parts),
            finish_reason=finish,  # type: ignore[arg-type]
            usage=usage,
        )
        return Completion(response, ConversionReport(target="scripted"), self.seconds_per_step)
