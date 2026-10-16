"""Prompt rendering for prompt-based formats (text-completion, llama.cpp native).

When a server applies no chat template, Stepswitch renders the conversation itself. The default
is the ChatML layout with tools described in a `<tools>` block and calls written as
`<tool_call>{...}</tool_call>`, which the T0 models in the registry use. Other models get their
template from the registry (template source: Jinja template, encoder script or engine mode);
the llama.cpp server can also render its own template through `/apply-template`, which
`ModelClient` uses when configured to.
"""

from __future__ import annotations

import json

from stepswitch.compat.ir import (
    ImagePart,
    Message,
    ReasoningPart,
    Request,
    TextPart,
    ToolCallPart,
    ToolResultPart,
)

TOOLS_PREAMBLE = (
    "# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
    "You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n"
)
TOOLS_SUFFIX = (
    "\n</tools>\n\nFor each function call, return a json object with function name and arguments "
    'within <tool_call></tool_call> XML tags:\n<tool_call>\n{"name": <function-name>, '
    '"arguments": <args-json-object>}\n</tool_call>'
)


def _tool_line(request: Request) -> str:
    specs = [
        json.dumps(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.parameters,
                },
            },
            ensure_ascii=False,
        )
        for tool in request.tools
    ]
    return TOOLS_PREAMBLE + "\n".join(specs) + TOOLS_SUFFIX


def _message_body(message: Message) -> str:
    chunks: list[str] = []
    for part in message.content:
        if isinstance(part, TextPart):
            chunks.append(part.text)
        elif isinstance(part, ToolCallPart):
            call = {"name": part.name, "arguments": json.loads(part.arguments or "{}")}
            chunks.append(f"<tool_call>\n{json.dumps(call, ensure_ascii=False)}\n</tool_call>")
        elif isinstance(part, ToolResultPart):
            chunks.append(f"<tool_response>\n{part.text()}\n</tool_response>")
        elif isinstance(part, ImagePart):
            chunks.append("[image]")
        elif isinstance(part, ReasoningPart):
            continue  # reasoning is not replayed into prompt renders
    return "\n".join(chunks)


def render_chatml(request: Request, *, add_generation_prompt: bool = True) -> str:
    system = request.system_text()
    if request.tools:
        system = f"{system}\n\n{_tool_line(request)}" if system else _tool_line(request)
    out: list[str] = []
    if system:
        out.append(f"<|im_start|>system\n{system}<|im_end|>\n")
    for message in request.messages:
        role = "user" if message.role == "tool" else message.role
        if message.role == "system":
            out.append(f"<|im_start|>system\n{_message_body(message)}<|im_end|>\n")
            continue
        out.append(f"<|im_start|>{role}\n{_message_body(message)}<|im_end|>\n")
    if add_generation_prompt:
        out.append("<|im_start|>assistant\n")
    return "".join(out)


CHATML_STOP = ["<|im_end|>", "<|im_start|>"]


def prompt_for(request: Request) -> str:
    """The prompt to send: a pre-rendered one if present, else the ChatML render."""
    return request.prompt if request.prompt is not None else render_chatml(request)
