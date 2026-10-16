"""Internal representation (IR) shared by every codec (ADR 0003, ARCHITECTURE §5).

Rules that every codec follows:

* `Usage.input_tokens` always includes cached input tokens. Formats that report them
  separately (messages format) are normalized. `Usage.raw` keeps the server's own object.
* Tool-call IDs are never empty. When a server omits them, codecs synthesize deterministic IDs
  with `synthetic_call_id(turn, index)` and keep the mapping in `ToolCallPart.synthetic`.
* Reasoning keeps its origin. Opaque payloads (signatures, encrypted content) are replayed only
  to the same origin; elsewhere they are dropped and reported in the `ConversionReport`.
* Anything a target format cannot carry is listed in the `ConversionReport`, never dropped
  silently (SPEC §4.1).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant", "tool"]
Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
EFFORT_LEVELS: tuple[Effort, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
FinishReason = Literal["stop", "length", "tool_calls", "content_filter", "error", "other"]

# Public names of the formats (SPEC §4.1, claim C-004) and their codec module keys.
FORMATS: dict[str, str] = {
    "chat-completions": "chat",
    "responses": "responses",
    "messages": "messages",
    "generate-content": "generate_content",
    "ollama-native": "ollama",
    "llamacpp-native": "llamacpp",
    "text-completion": "text",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------------------------
# Content parts
# --------------------------------------------------------------------------------------------


class TextPart(_Model):
    type: Literal["text"] = "text"
    text: str


class ImagePart(_Model):
    """An image by URL (http(s) or data: URI) or by base64 data plus media type."""

    type: Literal["image"] = "image"
    url: str | None = None
    data: str | None = None
    media_type: str | None = None
    detail: str | None = None


class FilePart(_Model):
    """A document by reference (URL, provider file ID or base64 data)."""

    type: Literal["file"] = "file"
    url: str | None = None
    file_id: str | None = None
    data: str | None = None
    media_type: str | None = None
    name: str | None = None


class ToolCallPart(_Model):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    # Arguments exactly as the model produced them: a JSON object serialized as a string.
    arguments: str = "{}"
    synthetic: bool = False

    def parsed_arguments(self) -> dict[str, Any]:
        value = json.loads(self.arguments or "{}")
        if not isinstance(value, dict):
            raise ValueError(f"tool call {self.name!r} arguments are not a JSON object")
        return value


class ToolResultPart(_Model):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str | None = None
    content: list[TextPart | ImagePart] = Field(default_factory=list)
    is_error: bool = False

    def text(self) -> str:
        return "".join(p.text for p in self.content if isinstance(p, TextPart))


class Origin(_Model):
    """Where an opaque reasoning payload came from; it may only be replayed there."""

    format: str
    endpoint: str | None = None
    model: str | None = None

    def matches(self, other: Origin | None) -> bool:
        return other is not None and (self.format, self.endpoint, self.model) == (
            other.format,
            other.endpoint,
            other.model,
        )


class ReasoningPart(_Model):
    type: Literal["reasoning"] = "reasoning"
    text: str | None = None
    summary: str | None = None
    opaque: str | None = None
    opaque_kind: Literal["signature", "encrypted", "redacted"] | None = None
    item_id: str | None = None
    origin: Origin | None = None


class RefusalPart(_Model):
    type: Literal["refusal"] = "refusal"
    text: str


Part = Annotated[
    TextPart | ImagePart | FilePart | ToolCallPart | ToolResultPart | ReasoningPart | RefusalPart,
    Field(discriminator="type"),
]


class Message(_Model):
    role: Role
    content: list[Part] = Field(default_factory=list)
    name: str | None = None
    # Format-specific extras that some targets can carry (harmony channel and recipient, ...).
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def text(cls, role: Role, text: str) -> Message:
        return cls(role=role, content=[TextPart(text=text)])

    def text_content(self) -> str:
        return "".join(p.text for p in self.content if isinstance(p, TextPart))

    def tool_calls(self) -> list[ToolCallPart]:
        return [p for p in self.content if isinstance(p, ToolCallPart)]

    def tool_results(self) -> list[ToolResultPart]:
        return [p for p in self.content if isinstance(p, ToolResultPart)]

    def reasoning(self) -> list[ReasoningPart]:
        return [p for p in self.content if isinstance(p, ReasoningPart)]


# --------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------


class ToolSpec(_Model):
    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    strict: bool | None = None


class NamedToolChoice(_Model):
    name: str


ToolChoice = Literal["auto", "none", "required"] | NamedToolChoice


class ResponseFormat(_Model):
    type: Literal["text", "json_object", "json_schema"] = "text"
    name: str | None = None
    json_schema: dict[str, Any] | None = None
    strict: bool | None = None


class Sampling(_Model):
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    seed: int | None = None
    stop: list[str] = Field(default_factory=list)
    presence_penalty: float | None = None
    frequency_penalty: float | None = None


class Request(_Model):
    model: str
    system: list[TextPart] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    tool_choice: ToolChoice | None = None
    parallel_tool_calls: bool | None = None
    response_format: ResponseFormat | None = None
    max_output_tokens: int | None = None
    sampling: Sampling = Field(default_factory=Sampling)
    effort: Effort | None = None
    budget_tokens: int | None = None
    stream: bool = False
    # Pre-rendered prompt for prompt-based formats (text-completion, llama.cpp native).
    prompt: str | None = None
    # Extensions passed through for one specific format; never interpreted by other formats.
    extensions: dict[str, Any] = Field(default_factory=dict)

    def system_text(self) -> str:
        return "\n\n".join(p.text for p in self.system)


# --------------------------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------------------------


class Usage(_Model):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    raw: dict[str, Any] | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class Response(_Model):
    id: str | None = None
    model: str | None = None
    message: Message = Field(default_factory=lambda: Message(role="assistant"))
    finish_reason: FinishReason = "stop"
    raw_finish_reason: str | None = None
    usage: Usage | None = None
    created: int | None = None


# --------------------------------------------------------------------------------------------
# Streaming deltas
# --------------------------------------------------------------------------------------------


class StartDelta(_Model):
    type: Literal["start"] = "start"
    id: str | None = None
    model: str | None = None


class TextDelta(_Model):
    type: Literal["text"] = "text"
    text: str


class ReasoningDelta(_Model):
    type: Literal["reasoning"] = "reasoning"
    text: str = ""
    opaque: str | None = None
    opaque_kind: Literal["signature", "encrypted", "redacted"] | None = None


class ToolCallStartDelta(_Model):
    type: Literal["tool_call_start"] = "tool_call_start"
    index: int
    id: str
    name: str
    synthetic: bool = False


class ToolCallArgsDelta(_Model):
    type: Literal["tool_call_args"] = "tool_call_args"
    index: int
    delta: str


class ToolCallEndDelta(_Model):
    type: Literal["tool_call_end"] = "tool_call_end"
    index: int


class UsageDelta(_Model):
    type: Literal["usage"] = "usage"
    usage: Usage


class FinishDelta(_Model):
    type: Literal["finish"] = "finish"
    reason: FinishReason
    raw: str | None = None


class ErrorDelta(_Model):
    type: Literal["error"] = "error"
    kind: str
    message: str
    status: int | None = None


Delta = Annotated[
    StartDelta
    | TextDelta
    | ReasoningDelta
    | ToolCallStartDelta
    | ToolCallArgsDelta
    | ToolCallEndDelta
    | UsageDelta
    | FinishDelta
    | ErrorDelta,
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------------------------
# Conversion reports
# --------------------------------------------------------------------------------------------


class Loss(_Model):
    field: str
    detail: str
    action: Literal["dropped", "approximated", "rejected"] = "dropped"


class ConversionReport(_Model):
    target: str | None = None
    losses: list[Loss] = Field(default_factory=list)

    def add(self, field: str, detail: str, action: str = "dropped") -> None:
        self.losses.append(Loss(field=field, detail=detail, action=action))  # type: ignore[arg-type]

    @property
    def lossless(self) -> bool:
        return not self.losses


class ConversionError(ValueError):
    """Raised in strict mode when a conversion would lose information."""


def synthetic_call_id(turn: int, index: int) -> str:
    """Deterministic ID for a tool call whose server sent none (ADR 0003 item 1)."""
    return f"call_t{turn}_{index}"
