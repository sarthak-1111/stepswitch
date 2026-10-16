"""Capability probe: `ssw probe <endpoint>` (SPEC §4.6, ADR 0003 item 4).

`run` sends a short battery of checks to one (endpoint, model, API format) and returns a
JSON-serializable capability profile, which the CLI stores in SQLite for the router. `export`
writes the shareable result file of `ssw probe --export` (SPEC §4.7).

How the battery behaves:

* Model requests go through `ModelClient` (working rule 2). The read-only discovery requests
  (`GET /v1/models`, `/api/version`, `/api/tags`, `/api/ps`, `/props`, `/health`, and Ollama's
  read-only `POST /api/show`) go through `stepswitch.net` with purpose "probe".
* Each check records `{"ok", "seconds", "detail"}`, plus `error` and `data` when it has them.
  `ok` is True when the check passed, False when it ran and failed, and None when it was
  skipped or did not apply. Each check has its own time limit, and a failing check never stops
  the battery; only when two checks in a row cannot reach the endpoint are the remaining ones
  recorded as not run.
* Budgets stay small (a few dozen output tokens, more once a model is seen reasoning), so the
  full battery fits in three minutes for a 4B model on CPU. The slow checks (needle in a
  haystack, prefix cache, throughput) are skipped with `quick=True`; in full mode the needle
  lengths adapt to the measured prefill speed and the time left.
* Ollama: `num_ctx` is always set on the native API (SPEC §4.2): a configured `num_ctx` on the
  endpoint, else the context the model is loaded with (`/api/ps`), else its Modelfile
  `num_ctx`, else a default, so probing does not force a reload. `/v1` cannot set the context,
  so there the needle test measures the server's own setting.
* Reasoning retention is measured from prompt sizes: the same history is sent with and
  without the model's own reasoning, and a larger `input_tokens` means the chat template kept
  it. `checks.reasoning_retention.data.policy` holds the resulting policy (`drop`,
  `keep-for-tool-turns` or `keep-all`; ADR 0003 item 8).
* Usage semantics compare a cold and a warm run of one prompt: if the raw input count stays
  the same while cached tokens appear, the server's input count includes cached tokens.
* Privacy: profiles and exports carry no API keys, headers, URLs, host or user names, or home
  paths. Every string is scrubbed before it is returned, an endpoint named by its URL is
  labelled by scheme and locality only, and model names that are file paths (llama.cpp reports
  its `-m` path as the model id) are cut to the file name.

Facts the discovery relies on, checked 2026-10-16: the llama.cpp server README
(https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md: `GET /props` has
`build_info`, `model_path` and `default_generation_settings.n_ctx`; `GET /v1/models` ids
default to the `-m` path, with `meta.n_ctx_train`; `/health` answers 503 while loading);
Ollama 0.34.4 on the workstation and docs/RESEARCH.md "Ollama" (`/api/version`; `/api/tags`
with `digest`, `details.context_length` and `capabilities`; `/api/ps` with `context_length`;
`/api/show` with `capabilities`, `thinking.values`, `parameters` and `model_info`; responses
format since 0.13.3, messages format since 0.14.0; `tool_choice` unsupported on `/v1`).
"""

from __future__ import annotations

import dataclasses
import getpass
import hashlib
import importlib
import json
import random
import re
import socket
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx

from stepswitch import net
from stepswitch.compat import calibrate
from stepswitch.compat.client import Completion, Endpoint, ModelClient, join_url
from stepswitch.compat.codecs import format_key
from stepswitch.compat.codecs.base import CodecOptions
from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import (
    FORMATS,
    Effort,
    Message,
    NamedToolChoice,
    Origin,
    ReasoningPart,
    Request,
    Response,
    ResponseFormat,
    Sampling,
    TextPart,
    ToolCallArgsDelta,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
)
from stepswitch.config import EndpointConfig

T = TypeVar("T")

# jsonschema ships no type information; reach it through the module object.
_JSONSCHEMA = importlib.import_module("jsonschema")

SCHEMA = 1
EXPORT_KIND = "stepswitch-probe-result"

# Profile order (SPEC §4.6).
CHECKS: tuple[str, ...] = (
    "basic_chat",
    "system_prompt",
    "native_tool_call",
    "parallel_tool_calls",
    "fallback_tool_protocol",
    "json_schema",
    "streaming",
    "usage",
    "reasoning_field",
    "context_length",
    "prefix_cache",
    "throughput",
    "tool_call_ids",
    "forced_tool_choice",
    "assistant_prefill",
    "streamed_tool_args",
    "structured_output_under_reasoning",
    "reasoning_retention",
    "usage_semantics",
    "formats_served",
)
SLOW_CHECKS = frozenset({"context_length", "prefix_cache", "throughput"})
# Run order: later checks reuse what earlier ones learned. formats_served comes from discovery.
RUN_ORDER: tuple[str, ...] = (
    "basic_chat",
    "system_prompt",
    "streaming",
    "usage",
    "native_tool_call",
    "parallel_tool_calls",
    "streamed_tool_args",
    "tool_call_ids",
    "forced_tool_choice",
    "json_schema",
    "fallback_tool_protocol",
    "assistant_prefill",
    "reasoning_field",
    "structured_output_under_reasoning",
    "reasoning_retention",
    "usage_semantics",
    "prefix_cache",
    "throughput",
    "context_length",
)
# Checks that only read earlier replies.
DERIVED = frozenset({"usage", "tool_call_ids"})
# Time limit per check as a multiple of the request timeout (`run(timeout=...)`).
CHECK_LIMITS: dict[str, float] = {
    "basic_chat": 3.0,
    "fallback_tool_protocol": 3.0,
    "reasoning_retention": 4.0,
    "usage_semantics": 2.0,
    "prefix_cache": 2.0,
    "throughput": 2.0,
}
DEFAULT_CHECK_LIMIT = 2.0

FULL_BUDGET_S = 170.0  # soft limit for the whole battery in full mode (SPEC §4.6: < 3 min)
CONTEXT_BUDGET_S = 60.0  # at most this long on needle requests
MIN_CONTEXT_BUDGET_S = 15.0
CONTEXT_CAP = 16_384
NEEDLE_LENGTHS = (2_048, 4_096, 8_192, 16_384)
ANSWER_ROOM = 256  # tokens kept free for the answer when sizing needle prompts
PREFIX_TOKENS = 1_536
REASONING_ALLOWANCE = 1_024  # extra output tokens once a model is known to reason
QUICK_NUM_CTX = 4_096
DISCOVERY_TIMEOUT_S = 5.0
RETENTION_MIN_DELTA = 4  # prompt tokens; smaller differences are template noise

STATUS = {True: "ok", False: "FAIL", None: "skip"}
DEAD_KINDS = frozenset({"connection", "blocked", "timeout"})

# Where reasoning arrives, by format. chat-completions servers differ, so it is read from
# the reply; llama.cpp native and text completion carry think tags that the codec parses.
REASONING_FIELDS: dict[str, str] = {
    "ollama-native": "thinking",
    "messages": "thinking",
    "responses": "reasoning",
    "generate-content": "thought",
    "llamacpp-native": "think-tags",
    "text-completion": "think-tags",
}
CHAT_REASONING_FIELDS = ("reasoning_content", "reasoning")
# Raw usage fields with the server's own input-token count, by format family.
RAW_INPUT_FIELDS = (
    "prompt_tokens",
    "input_tokens",
    "prompt_eval_count",
    "tokens_evaluated",
    "promptTokenCount",
)

WEATHER = ToolSpec(
    name="get_weather",
    description="Get the current weather for a city.",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
)
CLOCK = ToolSpec(
    name="get_time",
    description="Get the current local time in a city.",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
)
WEATHER_QUESTION = "What is the weather in Paris right now? Use the get_weather tool."
PARALLEL_QUESTION = (
    "I need two things at once: the current weather in Paris and the local time in Tokyo. "
    "Call both tools in this reply."
)
CODE_WORD = "MANGO-17"
# An opening a fresh reply would not start with, so a reply that repeats it can only be an
# echo of the prefill (the llama.cpp server b11669 returns prefix plus continuation; Ollama
# 0.34.4 returns the continuation only).
PREFILL = "Counting upward as requested: 1, 2, 3, 4, 5,"
CITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "population_millions": {"type": "number"},
        "landmarks": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["city", "population_millions", "landmarks"],
    "additionalProperties": False,
}
NEEDLE_WORDS = ("orchid", "granite", "lantern", "harbor", "saffron", "quartz", "willow", "comet")


def action_schema(tools: Sequence[str]) -> dict[str, Any]:
    """The fallback protocol's action object: a tool call or a final answer (SPEC §4.4)."""
    return {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": list(tools)},
                    "arguments": {"type": "object"},
                },
                "required": ["tool", "arguments"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"final": {"type": "string"}},
                "required": ["final"],
                "additionalProperties": False,
            },
        ]
    }


def fallback_system_prompt(tools: Sequence[ToolSpec]) -> str:
    """Describes the tools in the prompt for models driven without native tool calling."""
    lines = [
        "You act by replying with exactly one JSON object and nothing else.",
        "Available tools:",
    ]
    for tool in tools:
        schema = json.dumps(tool.parameters, separators=(",", ":"))
        lines.append(f"- {tool.name}: {tool.description or ''} Arguments (JSON Schema): {schema}")
    lines += [
        'To call a tool, reply {"tool": "<tool name>", "arguments": {...}}.',
        'To answer the user directly, reply {"final": "<answer>"}.',
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _preview(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return repr(flat if len(flat) <= limit else flat[: limit - 3] + "...")


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 1)


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _error_text(exc: ModelError) -> str:
    status = f" (HTTP {exc.status})" if exc.status else ""
    return f"{exc.kind}{status}: {exc.message}"[:500]


def _lookup(table: Mapping[str, T], name: str) -> T | None:
    """A per-model value, also when one side spells Ollama's implicit `:latest` tag."""
    if name in table:
        return table[name]
    tail = name.rsplit("/", 1)[-1]
    if ":" not in tail:
        return table.get(f"{name}:latest")
    if name.endswith(":latest"):
        return table.get(name[: -len(":latest")])
    return None


def _version(text: str) -> tuple[int, ...] | None:
    match = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", text.strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def public_format(name: str) -> str:
    """The public format name ("chat" -> "chat-completions"); ValueError for unknown names."""
    try:
        key = format_key(name)
    except KeyError as exc:
        raise ValueError(str(exc.args[0]) if exc.args else str(exc)) from None
    return next(public for public, module in FORMATS.items() if module == key)


_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def safe_model_name(name: str) -> str:
    """Model names that are file paths (llama.cpp reports its `-m` path) become the file name.

    Hub ids such as `org/model` and Ollama names such as `hf.co/org/repo:Q4_K_M` stay as they
    are.
    """
    path_like = (
        name.startswith(("/", "~", "\\", "./", "../"))
        or bool(_WINDOWS_PATH.match(name))
        or "\\" in name
        or (name.lower().endswith(".gguf") and "/" in name)
    )
    if not path_like:
        return name
    return re.split(r"[\\/]", name.rstrip("\\/"))[-1] or name


def _url_parts(url: str) -> tuple[str, str]:
    try:
        parsed = httpx.URL(url)
    except Exception:  # noqa: BLE001 - an unparsable URL has no usable parts
        return "", ""
    return parsed.scheme or "", parsed.host or ""


def endpoint_label(config: EndpointConfig) -> str:
    """The endpoint's name, or a neutral label when the name is its URL."""
    if "://" not in config.name:
        return config.name
    scheme = _url_parts(config.url)[0] or "http"
    return f"{scheme}-{'local' if config.is_local() else 'remote'}-endpoint"


def secret_values(config: EndpointConfig | None) -> list[str]:
    """Strings that must never appear in a profile or export."""
    values: list[str] = []
    home = Path.home()
    values += [str(home), home.as_posix()]
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name is fine
        user = ""
    if user and not user.isalpha():  # plain alphabetic names are too common to scrub
        values.append(user)
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    if host and host.lower() != "localhost":
        values.append(host)
    if config is not None:
        key = config.resolved_api_key()
        if key:
            values.append(key)
        values.append(config.url.rstrip("/"))
        host_name = _url_parts(config.url)[1]
        if host_name and not net.is_local_host(host_name):
            values.append(host_name)
    return [value for value in values if len(value) >= 4]


class Scrubber:
    """Replaces URLs and known secret strings in every string of a JSON-like tree."""

    URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>]+")

    def __init__(self, secrets: Iterable[str]) -> None:
        words = sorted({value for value in secrets if value}, key=len, reverse=True)
        self._patterns = [re.compile(re.escape(word), re.IGNORECASE) for word in words]

    def text(self, value: str) -> str:
        value = self.URL.sub("<url>", value)
        for pattern in self._patterns:
            value = pattern.sub("<redacted>", value)
        return value

    def tree(self, node: Any) -> Any:
        if isinstance(node, str):
            return self.text(node)
        if isinstance(node, dict):
            return {key: self.tree(value) for key, value in node.items()}
        if isinstance(node, list | tuple):
            return [self.tree(value) for value in node]
        return node


# --------------------------------------------------------------------------------------------
# JSON replies
# --------------------------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def parse_json_reply(text: str) -> tuple[Any, bool]:
    """The JSON value in a reply and whether the reply was exactly that JSON document.

    Code fences and surrounding prose are tolerated (exact=False); ValueError if no JSON.
    """
    stripped = text.strip()
    try:
        return json.loads(stripped), True
    except ValueError:
        pass
    fenced = _FENCE.match(stripped)
    if fenced:
        return json.loads(fenced.group(1)), False
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        return json.loads(stripped[start : end + 1]), False
    raise ValueError("no JSON object in the reply")


def schema_errors(value: Any, schema: Mapping[str, Any]) -> list[str]:
    """JSON Schema violations of `value` (empty when valid)."""
    validator = _JSONSCHEMA.Draft202012Validator(dict(schema))
    return sorted(str(error.message) for error in validator.iter_errors(value))


def parse_action(text: str, schema: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """A fallback-protocol action object from a reply, or the reason there is none."""
    try:
        value, _exact = parse_json_reply(text)
    except ValueError as exc:
        return None, f"not JSON ({exc})"[:200]
    errors = schema_errors(value, schema)
    if errors or not isinstance(value, dict):
        return None, ("; ".join(errors) or "not a JSON object")[:200]
    return value, None


def _arguments(call: ToolCallPart) -> dict[str, Any] | None:
    try:
        return call.parsed_arguments()
    except ValueError:
        return None


def _check_call(calls: Sequence[ToolCallPart], name: str, city: str) -> str | None:
    """None when `calls` holds a call of `name` whose `city` argument mentions `city`."""
    if not calls:
        return "no tool call"
    for call in calls:
        if call.name != name:
            continue
        arguments = _arguments(call)
        if arguments is None:
            return f"{name} arguments are not a JSON object: {_preview(call.arguments)}"
        if city not in str(arguments.get("city", "")).lower():
            return f"unexpected {name} arguments: {_preview(call.arguments)}"
        return None
    return f"called {', '.join(c.name for c in calls)} instead of {name}"


def _call_text(call: ToolCallPart) -> str:
    return f"{call.name}({' '.join(call.arguments.split())[:60]})"


def _has_reasoning(response: Response) -> bool:
    return any(
        isinstance(part, ReasoningPart) and bool(part.text or part.opaque or part.summary)
        for part in response.message.content
    )


def _reasoning_text(response: Response) -> str:
    return "".join(part.text or "" for part in response.message.reasoning())


def _starved(response: Response) -> bool:
    """The reply hit the token limit after spending tokens on reasoning, so its answer is
    missing or cut short."""
    return response.finish_reason == "length" and _has_reasoning(response)


def _without_reasoning(message: Message) -> Message:
    parts = [part for part in message.content if not isinstance(part, ReasoningPart)]
    return message.model_copy(update={"content": parts})


def _usage_ok(usage: Usage | None) -> bool:
    return usage is not None and (usage.input_tokens or 0) > 0 and (usage.output_tokens or 0) > 0


def _unexpected(response: Response, wanted: str) -> str:
    text = response.message.text_content()
    if text.strip():
        return f"the reply lacks {wanted}: {_preview(text)}"
    if response.message.tool_calls():
        return f"a tool call instead of {wanted}"
    if _has_reasoning(response):
        return "no answer: the token budget ran out while reasoning"
    return "an empty reply"


def _no_call(response: Response) -> str:
    text = response.message.text_content()
    return f"no tool call; answered in text: {_preview(text)}" if text.strip() else "no tool call"


# --------------------------------------------------------------------------------------------
# Discovery (read-only)
# --------------------------------------------------------------------------------------------


@dataclass
class Discovery:
    """What the read-only discovery requests found. Holds no URLs, paths or keys."""

    formats: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)
    engine: str | None = None
    engine_version: str | None = None
    models: list[str] = field(default_factory=list)
    digests: dict[str, str] = field(default_factory=dict)
    model_contexts: dict[str, int] = field(default_factory=dict)
    loaded_contexts: dict[str, int] = field(default_factory=dict)
    model_capabilities: dict[str, list[str]] = field(default_factory=dict)
    server_context: int | None = None
    health: int | None = None
    seconds: float = 0.0

    def add_format(self, fmt: str, evidence: str) -> None:
        if fmt not in self.formats:
            self.formats.append(fmt)
            self.evidence[fmt] = evidence


def server_root(base_url: str) -> str:
    """The server root of a base URL (a trailing /v1 or /v1beta removed)."""
    base = base_url.rstrip("/")
    for suffix in ("/v1beta", "/v1"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


class _Reader:
    """Read-only requests that never raise; after a transport error the server counts as down."""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http
        self.down = False

    def json(self, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
        if self.down:
            return None
        try:
            response = self.http.request(method, url, json=body)
        except httpx.TransportError:  # includes net.EgressBlocked
            self.down = True
            return None
        except (httpx.HTTPError, httpx.InvalidURL):
            return None
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def status(self, url: str) -> int | None:
        if self.down:
            return None
        try:
            return self.http.get(url).status_code
        except httpx.TransportError:
            self.down = True
            return None
        except (httpx.HTTPError, httpx.InvalidURL):
            return None


def _reader_client(
    headers: Mapping[str, str] | None,
    egress: net.Egress | None,
    transport: httpx.BaseTransport | None,
    timeout: float,
) -> httpx.Client:
    return (egress or net.default()).client(
        "probe",
        headers={"user-agent": "stepswitch", **(headers or {})},
        timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
        transport=transport,
    )


def discover(
    base_url: str,
    *,
    headers: Mapping[str, str] | None = None,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DISCOVERY_TIMEOUT_S,
) -> Discovery:
    """Formats, engine, version and models from read-only requests (never raises)."""
    found = Discovery()
    started = time.perf_counter()
    root = server_root(base_url)
    with _reader_client(headers, egress, transport, timeout) as http:
        reader = _Reader(http)
        _discover_ollama(reader, root, found)
        _discover_model_list(reader, base_url, found)
        _discover_llamacpp(reader, root, found)
        found.health = reader.status(root + "/health")
    _add_documented_formats(found)
    found.seconds = round(time.perf_counter() - started, 3)
    return found


def _discover_ollama(reader: _Reader, root: str, found: Discovery) -> None:
    version = _dict(reader.json("GET", root + "/api/version"))
    if not _str(version.get("version")):
        return
    found.engine = "ollama"
    found.engine_version = str(version["version"])
    found.add_format("ollama-native", "GET /api/version")
    for item in _list(_dict(reader.json("GET", root + "/api/tags")).get("models")):
        entry = _dict(item)
        name = _str(entry.get("name")) or _str(entry.get("model"))
        if not name:
            continue
        if entry.get("remote_host") or entry.get("remote_model"):
            continue  # served by a hosted service behind the local server; never a default
        found.models.append(name)
        digest = _str(entry.get("digest"))
        if digest:
            found.digests[name] = digest
        context = _int(_dict(entry.get("details")).get("context_length"))
        if context:
            found.model_contexts[name] = context
        capabilities = [c for c in _list(entry.get("capabilities")) if isinstance(c, str)]
        if capabilities:
            found.model_capabilities[name] = capabilities
    _read_loaded(reader, root, found)


def _read_loaded(reader: _Reader, root: str, found: Discovery) -> None:
    for item in _list(_dict(reader.json("GET", root + "/api/ps")).get("models")):
        entry = _dict(item)
        name = _str(entry.get("name")) or _str(entry.get("model"))
        context = _int(entry.get("context_length"))
        if name and context:
            found.loaded_contexts[name] = context


def _discover_model_list(reader: _Reader, base_url: str, found: Discovery) -> None:
    listing = _dict(reader.json("GET", join_url(base_url, "/v1/models")))
    data = listing.get("data")
    if not isinstance(data, list):
        return
    found.add_format("chat-completions", "GET /v1/models")
    if found.engine == "ollama":
        return  # /api/tags already listed the local models
    for item in data:
        entry = _dict(item)
        name = _str(entry.get("id"))
        if not name:
            continue
        if name not in found.models:
            found.models.append(name)
        context = _int(_dict(entry.get("meta")).get("n_ctx_train"))
        if context:
            found.model_contexts[name] = context


def _discover_llamacpp(reader: _Reader, root: str, found: Discovery) -> None:
    props = _dict(reader.json("GET", root + "/props"))
    if not ({"default_generation_settings", "build_info"} & props.keys()):
        return
    if found.engine is None:
        found.engine = "llamacpp"
    build = _str(props.get("build_info"))
    if build and found.engine == "llamacpp":
        found.engine_version = build
    context = _int(_dict(props.get("default_generation_settings")).get("n_ctx"))
    if context:
        found.server_context = context
    found.add_format("llamacpp-native", "GET /props")


def _add_documented_formats(found: Discovery) -> None:
    """Formats the identified engine documents for its version (marked as documented)."""
    if found.engine == "ollama":
        version = _version(found.engine_version or "")
        if "chat-completions" in found.formats:
            found.add_format("text-completion", "documented for Ollama (/v1/completions)")
        for fmt, since in (("responses", (0, 13, 3)), ("messages", (0, 14, 0))):
            if version is not None and version >= since:
                label = ".".join(str(part) for part in since)
                found.add_format(fmt, f"documented for Ollama >= {label}")
    elif found.engine == "llamacpp" and "chat-completions" in found.formats:
        for fmt in ("text-completion", "responses", "messages"):
            found.add_format(fmt, "documented for the llama.cpp server")


def ollama_show(
    base_url: str,
    model: str,
    *,
    headers: Mapping[str, str] | None = None,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DISCOVERY_TIMEOUT_S,
) -> dict[str, Any]:
    """Safe facts from Ollama's read-only `POST /api/show`.

    The Modelfile, license and template text are never kept (the Modelfile names a local
    path); the template is reduced to its SHA-256.
    """
    with _reader_client(headers, egress, transport, timeout) as http:
        shown = _dict(
            _Reader(http).json("POST", server_root(base_url) + "/api/show", {"model": model})
        )
    info: dict[str, Any] = {}
    capabilities = [c for c in _list(shown.get("capabilities")) if isinstance(c, str)]
    if capabilities:
        info["capabilities"] = capabilities
    values = [
        v for v in _list(_dict(shown.get("thinking")).get("values")) if isinstance(v, str | bool)
    ]
    if values:
        info["think_values"] = values
    model_info = _dict(shown.get("model_info"))
    arch = _str(model_info.get("general.architecture"))
    context = _int(model_info.get(f"{arch}.context_length")) if arch else None
    if context:
        info["context_length"] = context
    for line in (_str(shown.get("parameters")) or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "num_ctx" and parts[1].isdigit():
            info["num_ctx"] = int(parts[1])
    details = _dict(shown.get("details"))
    for key, name in (
        ("family", "family"),
        ("parameter_size", "parameter_size"),
        ("quantization_level", "quantization"),
    ):
        value = _str(details.get(key))
        if value:
            info[name] = value
    template = _str(shown.get("template"))
    if template:
        info["template_sha256"] = hashlib.sha256(template.encode("utf-8")).hexdigest()
    return info


def ollama_loaded_context(
    base_url: str,
    model: str,
    *,
    headers: Mapping[str, str] | None = None,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DISCOVERY_TIMEOUT_S,
) -> int | None:
    """The context a model is loaded with right now (`GET /api/ps`), if it is loaded."""
    found = Discovery()
    with _reader_client(headers, egress, transport, timeout) as http:
        _read_loaded(_Reader(http), server_root(base_url), found)
    return _lookup(found.loaded_contexts, model)


def ollama_num_ctx(
    config: EndpointConfig,
    discovery: Discovery,
    info: Mapping[str, Any],
    model: str,
    *,
    quick: bool,
) -> int:
    """`num_ctx` for Ollama native requests: configured, else loaded, else Modelfile, else a
    default capped by the model's context."""
    configured = _int((config.model_extra or {}).get("num_ctx"))
    if configured:
        return configured
    loaded = _lookup(discovery.loaded_contexts, model)
    if loaded:
        return loaded
    modelfile = _int(info.get("num_ctx"))
    if modelfile:
        return modelfile
    model_context = _int(info.get("context_length")) or _lookup(discovery.model_contexts, model)
    default = QUICK_NUM_CTX if quick else CONTEXT_CAP
    return min(model_context, default) if model_context else default


# --------------------------------------------------------------------------------------------
# Target: one resolved (endpoint, model, format)
# --------------------------------------------------------------------------------------------


@dataclass
class Target:
    """An endpoint resolved for one model and format, with what discovery learned."""

    config: EndpointConfig
    endpoint: Endpoint
    model: str
    format: str
    discovery: Discovery
    model_info: dict[str, Any]
    extensions: dict[str, Any]
    codec_extra: dict[str, Any]
    scrubber: Scrubber

    @property
    def endpoint_label(self) -> str:
        return endpoint_label(self.config)

    @property
    def model_label(self) -> str:
        return safe_model_name(self.model)

    @property
    def label(self) -> str:
        return f"{self.endpoint_label} / {self.model_label}"

    @property
    def engine(self) -> str | None:
        return self.config.engine or self.discovery.engine

    def scrub_text(self, text: str) -> str:
        return self.scrubber.text(text)

    def scrub(self, tree: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = self.scrubber.tree(tree)
        return cleaned

    def request(
        self,
        messages: Sequence[Message],
        max_tokens: int,
        *,
        system: str | None = None,
        **fields: Any,
    ) -> Request:
        """A request with the probe's defaults: temperature 0, the format's extensions."""
        return Request(
            model=self.model,
            system=[TextPart(text=system)] if system else [],
            messages=list(messages),
            max_output_tokens=max_tokens,
            sampling=Sampling(temperature=0.0),
            extensions=dict(self.extensions),
            **fields,
        )


def resolve(
    config: EndpointConfig,
    *,
    model: str | None = None,
    fmt: str | None = None,
    quick: bool = False,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DISCOVERY_TIMEOUT_S,
) -> Target:
    """Discover the endpoint and pick the model and format to talk to.

    Model: `model`, else the endpoint's first configured model, else the first local model the
    server lists. ValueError when there is none or the format is unknown.
    """
    public = public_format(fmt or config.primary_format())
    headers = Endpoint.from_config(config, fmt=public).headers
    discovery = discover(
        config.url, headers=headers, egress=egress, transport=transport, timeout=timeout
    )
    chosen = model or (config.models[0] if config.models else None)
    chosen = chosen or (discovery.models[0] if discovery.models else None)
    if not chosen:
        raise ValueError(
            f"no model to probe on endpoint {endpoint_label(config)!r}: pass --model, list"
            " models in the endpoint's configuration, or start a server that lists one"
        )
    info: dict[str, Any] = {}
    if discovery.engine == "ollama":
        info = ollama_show(
            config.url, chosen, headers=headers, egress=egress, transport=transport, timeout=timeout
        )
    model_info: dict[str, Any] = {
        "context_length": info.get("context_length") or _lookup(discovery.model_contexts, chosen),
        "loaded_context": _lookup(discovery.loaded_contexts, chosen),
        "server_context": discovery.server_context,
        "capabilities": info.get("capabilities") or _lookup(discovery.model_capabilities, chosen),
        "think_values": info.get("think_values"),
        "num_ctx": info.get("num_ctx"),
        "family": info.get("family"),
        "parameter_size": info.get("parameter_size"),
        "quantization": info.get("quantization"),
        "template_sha256": info.get("template_sha256"),
    }
    model_info = {key: value for key, value in model_info.items() if value is not None}
    extensions: dict[str, Any] = {}
    codec_extra: dict[str, Any] = {}
    if public == "ollama-native":
        extensions["num_ctx"] = ollama_num_ctx(config, discovery, info, chosen, quick=quick)
        if info.get("think_values"):
            codec_extra["think_values"] = list(info["think_values"])
    endpoint = Endpoint.from_config(config, model=chosen, fmt=public)
    endpoint.options = CodecOptions(extra=dict(codec_extra))
    return Target(
        config=config,
        endpoint=endpoint,
        model=chosen,
        format=public,
        discovery=discovery,
        model_info=model_info,
        extensions=extensions,
        codec_extra=codec_extra,
        scrubber=Scrubber(secret_values(config)),
    )


def model_hash(target: Target) -> tuple[str | None, str | None]:
    """The served model's digest (Ollama) or its registry sha256, and where it came from."""
    digest = _lookup(target.discovery.digests, target.model)
    if digest:
        return (digest if ":" in digest else f"sha256:{digest}"), "ollama-digest"
    try:
        from stepswitch import registry

        entry = registry.find(target.model_label)
    except Exception:  # noqa: BLE001 - an unreadable registry only means no hash
        entry = None
    if entry is not None and entry.sha256:
        return f"sha256:{entry.sha256}", "registry"
    return None, None


# --------------------------------------------------------------------------------------------
# The battery
# --------------------------------------------------------------------------------------------


@dataclass
class Outcome:
    ok: bool | None
    detail: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class _Skip(Exception):
    """The check does not apply (recorded with ok=None)."""


class _CheckTimeout(Exception):
    """The check used up its time limit."""


class _SharedTransport(httpx.BaseTransport):
    """One transport for every request of a run.

    It keeps the last JSON body it saw (the reasoning-field check reads the raw reply) and is
    closed once by the run, not by each client, so connections are reused across checks.
    """

    def __init__(self, inner: httpx.BaseTransport | None) -> None:
        self._owned = inner is None
        self._inner = inner or httpx.HTTPTransport(retries=0)
        self.last_json: Any = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.last_json = None
        response = self._inner.handle_request(request)
        if "application/json" in response.headers.get("content-type", ""):
            response.read()
            try:
                self.last_json = json.loads(response.content)
            except ValueError:
                self.last_json = None
        return response

    def take(self) -> Any:
        body, self.last_json = self.last_json, None
        return body

    def close(self) -> None:
        """Clients close their transport when they exit; the run calls `shutdown` instead."""

    def shutdown(self) -> None:
        if self._owned:
            self._inner.close()


class _Battery:
    def __init__(
        self,
        target: Target,
        *,
        quick: bool,
        egress: net.Egress,
        shared: _SharedTransport,
        timeout: float,
        echo: Callable[[str], None],
    ) -> None:
        self.target = target
        self.quick = quick
        self.egress = egress
        self.shared = shared
        self.timeout = timeout
        self.echo = echo
        self.results: dict[str, dict[str, Any]] = {}
        self.started = time.perf_counter()
        self.deadline = self.started + FULL_BUDGET_S
        self._check_deadline = float("inf")
        self._dead_streak = 0
        capabilities = target.model_info.get("capabilities")
        # Known up front for Ollama models; otherwise learned from the first reasoning reply.
        self.reasoning = isinstance(capabilities, list) and "thinking" in capabilities
        self.reasoning_effort: Effort | None = None
        self.reasoning_field: str | None = None
        self.reasoning_bodies: list[Any] = []
        self.replies: list[Response] = []
        self.basic: Response | None = None
        self.streamed: Response | None = None
        self.tool_replies: list[list[ToolCallPart]] = []
        self.tool_turn: tuple[Request, Response] | None = None
        self.chars_per_token = calibrate.CHARS_PER_TOKEN
        self.prefill: calibrate.Sample | None = None
        self.prefill_tps: float | None = None
        self.throughput: dict[str, Any] = {}
        self.checks: dict[str, Callable[[], Outcome]] = {
            "basic_chat": self.check_basic_chat,
            "system_prompt": self.check_system_prompt,
            "streaming": self.check_streaming,
            "usage": self.check_usage,
            "native_tool_call": self.check_native_tool_call,
            "parallel_tool_calls": self.check_parallel_tool_calls,
            "streamed_tool_args": self.check_streamed_tool_args,
            "tool_call_ids": self.check_tool_call_ids,
            "forced_tool_choice": self.check_forced_tool_choice,
            "json_schema": self.check_json_schema,
            "fallback_tool_protocol": self.check_fallback_tool_protocol,
            "assistant_prefill": self.check_assistant_prefill,
            "reasoning_field": self.check_reasoning_field,
            "structured_output_under_reasoning": self.check_structured_output_under_reasoning,
            "reasoning_retention": self.check_reasoning_retention,
            "usage_semantics": self.check_usage_semantics,
            "prefix_cache": self.check_prefix_cache,
            "throughput": self.check_throughput,
            "context_length": self.check_context_length,
        }

    # ---- running ------------------------------------------------------------------------

    def run(self) -> None:
        for name in RUN_ORDER:
            if self.quick and name in SLOW_CHECKS:
                self.record(name, Outcome(None, "skipped in quick mode"), 0.0)
            elif self._dead_streak >= 2 and name not in DERIVED:
                error = "not run: the endpoint did not answer the two previous checks"
                self.record(name, Outcome(False, "not run", error=error), 0.0)
            else:
                self._run_check(name, self.checks[name])

    def _run_check(self, name: str, check: Callable[[], Outcome]) -> None:
        started = time.perf_counter()
        limit = self._limit(name)
        self._check_deadline = started + limit
        kind: str | None = None
        try:
            outcome = check()
        except _Skip as skip:
            outcome = Outcome(None, str(skip))
        except _CheckTimeout:
            kind = "timeout"
            outcome = Outcome(False, "timed out", error=f"the check used up its {limit:.0f} s")
        except ModelError as exc:
            kind = exc.kind
            outcome = Outcome(False, "request failed", error=_error_text(exc))
        except Exception as exc:  # noqa: BLE001 - one broken check never stops the battery
            outcome = Outcome(False, "check crashed", error=f"{type(exc).__name__}: {exc}")
        finally:
            self._check_deadline = float("inf")
        if name not in DERIVED:
            self._dead_streak = self._dead_streak + 1 if kind in DEAD_KINDS else 0
        self.record(name, outcome, time.perf_counter() - started)

    def _limit(self, name: str) -> float:
        if name == "context_length":
            return self._context_budget() + 2 * self.timeout
        return CHECK_LIMITS.get(name, DEFAULT_CHECK_LIMIT) * self.timeout

    def record(self, name: str, outcome: Outcome, seconds: float) -> None:
        entry: dict[str, Any] = {
            "ok": outcome.ok,
            "seconds": round(seconds, 3),
            "detail": outcome.detail,
        }
        if outcome.error:
            entry["error"] = outcome.error[:500]
        if outcome.data:
            entry["data"] = outcome.data
        entry = self.target.scrub(entry)
        self.results[name] = entry
        line = f"  {name:<34} {STATUS[outcome.ok]:<4} {seconds:6.1f}s  {entry['detail']}"
        if "error" in entry:
            line += f" [{entry['error'][:160]}]"
        self.echo(line)

    # ---- requests -----------------------------------------------------------------------

    def request(
        self,
        messages: Sequence[Message],
        max_tokens: int,
        *,
        room: bool = True,
        system: str | None = None,
        **fields: Any,
    ) -> Request:
        """A request; models known to reason get extra output tokens unless `room` is off."""
        budget = max_tokens + (REASONING_ALLOWANCE if room and self.reasoning else 0)
        return self.target.request(messages, budget, system=system, **fields)

    def client(
        self, *, timeout: float | None = None, extra: Mapping[str, Any] | None = None
    ) -> ModelClient:
        remaining = self._check_deadline - time.perf_counter()
        if remaining < 0.5:
            raise _CheckTimeout
        options = CodecOptions(extra={**self.target.codec_extra, **(extra or {})})
        endpoint = dataclasses.replace(self.target.endpoint, options=options)
        return ModelClient(
            endpoint,
            egress=self.egress,
            transport=self.shared,
            timeout=max(1.0, min(timeout or self.timeout, remaining)),
            max_retries=0,
            purpose="probe",
        )

    def complete(
        self,
        request: Request,
        *,
        timeout: float | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> Completion:
        """Send a request; a reply starved by unexpected reasoning is retried with more room."""
        had_room = self.reasoning
        done = self.client(timeout=timeout, extra=extra).complete(request)
        self._note(done.response, self.shared.take())
        if not had_room and self.reasoning and _starved(done.response):
            bigger = (request.max_output_tokens or 0) + REASONING_ALLOWANCE
            retry = request.model_copy(update={"max_output_tokens": bigger})
            done = self.client(timeout=timeout, extra=extra).complete(retry)
            self._note(done.response, self.shared.take())
        return done

    def stream(self, request: Request) -> calibrate.StreamResult:
        result = calibrate.stream_once(self.client(), request)
        self._note(result.response, None)
        return result

    def sample(self, request: Request, kind: str) -> calibrate.Sample:
        return calibrate.sample(self.client(), request, kind)

    def measure(self, request: Request, kind: str) -> calibrate.Sample:
        """A timed request: streamed for the time to first token, or non-streamed when the
        server's streams carry no usage (the whole latency then stands in for it)."""
        if self.streamed is None or self.streamed.usage is not None:
            return self.sample(request, kind)
        done = self.complete(request)
        usage = done.response.usage
        return calibrate.Sample(
            kind=kind,
            input_tokens=usage.input_tokens if usage else None,
            cached_tokens=usage.cached_input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            ttft_s=done.latency_s,
            total_s=done.latency_s,
            server=calibrate.server_timings(usage),
        )

    def losses(self, request: Request, prefix: str, extra: Mapping[str, Any] | None = None) -> str:
        """What the format's codec cannot carry for fields under `prefix` (nothing is sent)."""
        _call, report = self.client(extra=extra).prepare(request)
        found = [f"{loss.field}: {loss.detail}" for loss in report.losses]
        return "; ".join(item for item in found if item.startswith(prefix))

    def _note(self, response: Response, body: Any) -> None:
        self.replies.append(response)
        if _has_reasoning(response):
            self.reasoning = True
            if body is not None:
                self.reasoning_bodies.append(body)

    def _reasoned(self) -> bool:
        return any(_has_reasoning(reply) for reply in self.replies)

    def _may_reason(self) -> bool:
        capabilities = self.target.model_info.get("capabilities")
        return "thinking" in capabilities if isinstance(capabilities, list) else True

    def _learn_ratio(self, chars: int, tokens: int | None) -> None:
        if tokens and tokens > 50:
            self.chars_per_token = max(1.5, min(8.0, chars / tokens))

    def _own(self, message: Message) -> Message:
        """The message with its reasoning labelled as this endpoint's and model's own."""
        origin = Origin(
            format=self.target.format, endpoint=self.target.endpoint.name, model=self.target.model
        )
        parts = [
            part.model_copy(update={"origin": origin}) if isinstance(part, ReasoningPart) else part
            for part in message.content
        ]
        return message.model_copy(update={"content": parts})

    # ---- basic checks -------------------------------------------------------------------

    def check_basic_chat(self) -> Outcome:
        request = self.request([Message.text("user", "Reply with the single word: pong")], 32)
        done = self.complete(request, timeout=self.timeout * 3)  # may include a cold load
        reply = done.response
        self.basic = reply
        text = reply.message.text_content()
        data = {"latency_s": round(done.latency_s, 3), "finish_reason": reply.finish_reason}
        if "pong" in text.lower():
            return Outcome(True, f"replied {_preview(text)}", data)
        return Outcome(False, _unexpected(reply, "'pong'"), data)

    def check_system_prompt(self) -> Outcome:
        system = (
            f"The code word is {CODE_WORD}. When the user asks for the code word, reply with the"
            " code word only."
        )
        request = self.request([Message.text("user", "What is the code word?")], 32, system=system)
        reply = self.complete(request).response
        text = reply.message.text_content()
        if _squash(CODE_WORD) in _squash(text):
            return Outcome(True, f"followed the system prompt: {_preview(text)}")
        return Outcome(False, _unexpected(reply, "the code word from the system prompt"))

    def check_streaming(self) -> Outcome:
        request = self.request(
            [Message.text("user", "Count from 1 to 10, separated by spaces.")], 48
        )
        result = self.stream(request)
        reply = result.response
        self.streamed = reply
        data = {
            "ttft_ms": _ms(result.ttft_s),
            "text_deltas": result.text_deltas,
            "finish_reason": reply.finish_reason,
        }
        if not reply.message.text_content().strip():
            return Outcome(False, _unexpected(reply, "text"), data)
        chunks = (
            "one text chunk" if result.text_deltas == 1 else f"{result.text_deltas} text chunks"
        )
        return Outcome(True, f"{chunks}, first after {data['ttft_ms']} ms", data)

    def check_usage(self) -> Outcome:
        if self.basic is None and self.streamed is None:
            raise _Skip("no successful reply to inspect")
        plain = None if self.basic is None else _usage_ok(self.basic.usage)
        streamed = None if self.streamed is None else _usage_ok(self.streamed.usage)
        data: dict[str, Any] = {"non_streaming": plain, "streaming": streamed}
        example = (self.basic.usage if self.basic else None) or (
            self.streamed.usage if self.streamed else None
        )
        if example is not None:
            data["example"] = {
                "input_tokens": example.input_tokens,
                "output_tokens": example.output_tokens,
                "cached_input_tokens": example.cached_input_tokens,
            }
        labels = (("non-streaming", plain), ("streaming", streamed))
        missing = [label for label, value in labels if value is False]
        if missing:
            return Outcome(False, f"no token counts in {' or '.join(missing)} replies", data)
        present = [label for label, value in labels if value]
        return Outcome(True, f"token counts in {' and '.join(present)} replies", data)

    # ---- tool calling -------------------------------------------------------------------

    def check_native_tool_call(self) -> Outcome:
        request = self.request(
            [Message.text("user", WEATHER_QUESTION)], 128, tools=[WEATHER], tool_choice="auto"
        )
        reply = self.complete(request).response
        calls = reply.message.tool_calls()
        if calls:
            self.tool_replies.append(calls)
        data = {"calls": [call.name for call in calls], "finish_reason": reply.finish_reason}
        problem = _check_call(calls, WEATHER.name, "paris")
        if problem:
            return Outcome(False, problem if calls else _no_call(reply), data)
        self.tool_turn = (request, reply)
        return Outcome(True, f"called {_call_text(calls[0])}", data)

    def check_parallel_tool_calls(self) -> Outcome:
        request = self.request(
            [Message.text("user", PARALLEL_QUESTION)],
            192,
            tools=[WEATHER, CLOCK],
            tool_choice="auto",
        )
        reply = self.complete(request).response
        calls = reply.message.tool_calls()
        if calls:
            self.tool_replies.append(calls)
        data = {"calls": [call.name for call in calls]}
        if not calls:
            return Outcome(False, _no_call(reply), data)
        problems = [
            problem
            for problem in (
                _check_call(calls, WEATHER.name, "paris"),
                _check_call(calls, CLOCK.name, "tokyo"),
            )
            if problem
        ]
        if problems and len(calls) == 1:
            return Outcome(False, f"one call per reply ({_call_text(calls[0])})", data)
        if problems:
            return Outcome(False, "; ".join(problems), data)
        return Outcome(True, f"{len(calls)} calls in one reply", data)

    def check_streamed_tool_args(self) -> Outcome:
        request = self.request(
            [Message.text("user", WEATHER_QUESTION)], 128, tools=[WEATHER], tool_choice="auto"
        )
        result = self.stream(request)
        calls = result.response.message.tool_calls()
        if calls:
            self.tool_replies.append(calls)
        fragments = sum(1 for delta in result.deltas if isinstance(delta, ToolCallArgsDelta))
        data = {
            "calls": [call.name for call in calls],
            "argument_fragments": fragments,
            "incremental": fragments > len(calls),
        }
        problem = _check_call(calls, WEATHER.name, "paris")
        if problem:
            return Outcome(False, problem if calls else _no_call(result.response), data)
        how = "in fragments" if data["incremental"] else "whole"
        return Outcome(True, f"streamed {_call_text(calls[0])} ({how})", data)

    def check_tool_call_ids(self) -> Outcome:
        calls = [call for reply in self.tool_replies for call in reply]
        if not calls:
            raise _Skip("no tool calls to inspect")
        synthetic = sum(1 for call in calls if call.synthetic)
        repeated = any(len({call.id for call in reply}) < len(reply) for reply in self.tool_replies)
        source = "synthetic" if synthetic or repeated else "server"
        data = {"source": source, "calls": len(calls), "synthesized": synthetic}
        if synthetic:
            return Outcome(
                False, f"no server id on {synthetic} of {len(calls)} calls; ids synthesized", data
            )
        if repeated:
            return Outcome(False, "server ids repeat within one reply", data)
        return Outcome(True, f"server ids on all {len(calls)} calls", data)

    def check_forced_tool_choice(self) -> Outcome:
        request = self.request(
            [Message.text("user", "What is the weather in Paris?")],
            128,
            tools=[WEATHER, CLOCK],
            tool_choice=NamedToolChoice(name=CLOCK.name),
        )
        lost = self.losses(request, "tool_choice")
        if lost:
            return Outcome(False, f"the {self.target.format} format cannot carry it ({lost})")
        reply = self.complete(request).response
        calls = reply.message.tool_calls()
        if calls:
            self.tool_replies.append(calls)
        data = {"forced": CLOCK.name, "calls": [call.name for call in calls]}
        if any(call.name == CLOCK.name for call in calls):
            return Outcome(True, f"the named tool {CLOCK.name} was called", data)
        if calls:
            names = ", ".join(call.name for call in calls)
            return Outcome(False, f"tool_choice ignored: called {names}", data)
        return Outcome(False, f"tool_choice ignored: {_no_call(reply)}", data)

    # ---- structured output and the fallback protocol ------------------------------------

    def check_json_schema(self) -> Outcome:
        response_format = ResponseFormat(
            type="json_schema", name="city_facts", json_schema=CITY_SCHEMA
        )
        request = self.request(
            [Message.text("user", "Tell me briefly about Paris.")],
            192,
            response_format=response_format,
        )
        lost = self.losses(request, "response_format")
        reply = self.complete(request).response
        text = reply.message.text_content()
        data: dict[str, Any] = {"constrained": not lost}
        try:
            value, exact = parse_json_reply(text)
        except ValueError:
            return Outcome(False, f"the reply is not JSON: {_preview(text)}", data)
        data["exact"] = exact
        errors = schema_errors(value, CITY_SCHEMA)
        if errors:
            return Outcome(False, "the reply breaks the schema: " + "; ".join(errors[:3]), data)
        note = " (the format only describes the schema in the prompt)" if lost else ""
        return Outcome(True, "schema-valid JSON" + note, data)

    def check_fallback_tool_protocol(self) -> Outcome:
        """One tool described in the prompt, the reply constrained to an action object."""
        schema = action_schema([WEATHER.name])
        messages = [Message.text("user", "What is the weather in Paris right now?")]
        constraint = ResponseFormat(type="json_schema", name="action", json_schema=schema)
        request = self.request(
            messages, 192, system=fallback_system_prompt([WEATHER]), response_format=constraint
        )
        constrained = not self.losses(request, "response_format")
        rejected: str | None = None
        try:
            reply = self.complete(request).response
        except ModelError as exc:
            if exc.kind not in ("bad_request", "unsupported"):
                raise
            rejected, constrained = _error_text(exc), False
            request = request.model_copy(update={"response_format": None})
            reply = self.complete(request).response
        text = reply.message.text_content()
        action, problem = parse_action(text, schema)
        first_try = problem is None
        repaired = False
        if problem is not None:  # validate and repair, once
            note = (
                f"That reply was not one valid JSON action object ({problem}). Reply again with"
                " only the JSON object."
            )
            history = [
                *messages,
                Message.text("assistant", text or "(empty)"),
                Message.text("user", note),
            ]
            reply = self.complete(request.model_copy(update={"messages": history})).response
            action, problem = parse_action(reply.message.text_content(), schema)
            repaired = problem is None
        data: dict[str, Any] = {
            "constrained": constrained,
            "honoured": constrained and first_try,
            "valid_first_try": first_try,
            "repaired": repaired,
        }
        if rejected:
            data["constraint_rejected"] = rejected
        if action is None:
            return Outcome(False, f"no valid action object: {problem}", data)
        if "final" in action:
            return Outcome(False, "a valid action, but a final answer instead of the tool", data)
        call = ToolCallPart(
            id="fallback", name=str(action["tool"]), arguments=json.dumps(action["arguments"])
        )
        problem = _check_call([call], WEATHER.name, "paris")
        if problem:
            return Outcome(False, problem, data)
        if data["honoured"]:
            how = "constrained decoding honoured"
        elif repaired:
            how = "valid after one repair"
        else:
            how = "valid without a constraint"
        return Outcome(True, f"called {_call_text(call)} through a JSON action ({how})", data)

    def check_assistant_prefill(self) -> Outcome:
        messages = [
            Message.text("user", "Write the numbers from 1 to 10, separated by commas."),
            Message.text("assistant", PREFILL),
        ]
        request = self.request(messages, 32)
        lost = self.losses(request, "messages")
        if lost:
            return Outcome(False, f"the format cannot end on an assistant message ({lost})")
        reply = self.complete(request).response
        text = reply.message.text_content()
        squashed = re.sub(r"\s", "", text).lower()
        prefix = re.sub(r"\s", "", PREFILL).lower()
        echoes = squashed.startswith(prefix)
        data: dict[str, Any] = {"reply": _preview(text, 60), "echoes_prefix": echoes}
        if squashed.removeprefix(prefix).startswith("6"):
            if echoes:
                return Outcome(True, "continued the assistant prefix (the reply repeats it)", data)
            return Outcome(True, "continued the assistant prefix", data)
        if squashed.startswith("1,2,3,4,5"):
            return Outcome(False, "started a new reply instead of continuing", data)
        return Outcome(False, _unexpected(reply, "a continuation from 6"), data)

    # ---- reasoning ----------------------------------------------------------------------

    def check_reasoning_field(self) -> Outcome:
        tried = False
        data: dict[str, Any] = {"field": None}
        if not self._reasoned() and self._may_reason():
            tried = True
            question = "What is 17 * 23? Think it through, then reply with the number only."
            request = self.request([Message.text("user", question)], 512, effort="medium")
            try:
                reply = self.complete(request).response
            except ModelError as exc:
                if exc.kind not in ("bad_request", "unsupported"):
                    raise
                data["tried_effort"] = True
                return Outcome(
                    False, "no reasoning; an effort setting was rejected", data, _error_text(exc)
                )
            if _has_reasoning(reply):
                self.reasoning_effort = "medium"
        if not self._reasoned():
            data["tried_effort"] = tried
            inline = self._inline_reasoning()
            if inline:
                data["inline"] = inline
                return Outcome(
                    False,
                    f"reasoning arrives inside the reply text ({inline}), not separated",
                    data,
                )
            if not tried and not self._may_reason():
                return Outcome(False, "the model does not report a thinking capability", data)
            return Outcome(False, "the model returned no reasoning", data)
        name = self._reasoning_field_name()
        self.reasoning_field = name
        data.update(field=name, needs_effort=self.reasoning_effort is not None)
        how = " when an effort is set" if self.reasoning_effort else ""
        if name is None:
            return Outcome(True, f"reasoning seen{how}; its field name is unknown", data)
        return Outcome(True, f"reasoning in {name!r}{how}", data)

    def _reasoning_field_name(self) -> str | None:
        fixed = REASONING_FIELDS.get(self.target.format)
        if fixed:
            return fixed
        for body in self.reasoning_bodies:
            choices = _list(_dict(body).get("choices"))
            message = _dict(_dict(choices[0]).get("message")) if choices else {}
            for key in CHAT_REASONING_FIELDS:
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    return key
        return None

    def _inline_reasoning(self) -> str | None:
        texts = [reply.message.text_content() for reply in self.replies]
        if any("<think>" in text for text in texts):
            return "think tags"
        if any("<|channel|>" in text for text in texts):
            return "harmony channels"
        return None

    def check_structured_output_under_reasoning(self) -> Outcome:
        if not self._reasoned():
            raise _Skip("the model returned no reasoning")
        response_format = ResponseFormat(
            type="json_schema", name="city_facts", json_schema=CITY_SCHEMA
        )
        request = self.request(
            [Message.text("user", "Tell me briefly about Paris.")],
            192,
            response_format=response_format,
            effort=self.reasoning_effort,
        )
        reply = self.complete(request).response
        reasoned = _has_reasoning(reply)
        text = reply.message.text_content()
        data: dict[str, Any] = {"reasoning_kept": reasoned}
        try:
            value, _exact = parse_json_reply(text)
        except ValueError:
            return Outcome(False, f"no JSON after reasoning: {_preview(text)}", data)
        errors = schema_errors(value, CITY_SCHEMA)
        data["schema_valid"] = not errors
        if errors:
            return Outcome(False, "the JSON breaks the schema: " + "; ".join(errors[:3]), data)
        if not reasoned:
            return Outcome(
                False, "schema-valid JSON, but the constraint suppressed reasoning", data
            )
        return Outcome(True, "reasoned, then answered with schema-valid JSON", data)

    def _replay_extra(self) -> dict[str, Any]:
        if (
            self.target.format == "chat-completions"
            and self.reasoning_field in CHAT_REASONING_FIELDS
        ):
            return {"reasoning_field": self.reasoning_field}
        return {}

    def _prompt_tokens(self, request: Request, extra: Mapping[str, Any]) -> int:
        usage = self.complete(request, extra=extra).response.usage
        if usage is None or usage.input_tokens is None:
            raise _Skip("no usage to compare prompt sizes")
        return usage.input_tokens

    def _prompt_delta(
        self,
        kept: list[Message],
        stripped: list[Message],
        extra: Mapping[str, Any],
        tools: Sequence[ToolSpec],
    ) -> int:
        """How many more prompt tokens the history costs with the model's reasoning in it."""
        with_reasoning = self.request(kept, 1, room=False, tools=list(tools))
        lost = [
            item
            for item in self.losses(with_reasoning, "messages", extra).split("; ")
            if "reasoning" in item
        ]
        if lost:
            raise _Skip(f"the codec does not send reasoning back ({lost[0]})")
        without = self.request(stripped, 1, room=False, tools=list(tools))
        return self._prompt_tokens(with_reasoning, extra) - self._prompt_tokens(without, extra)

    def check_reasoning_retention(self) -> Outcome:
        if not self._reasoned():
            raise _Skip("the model returned no reasoning")
        extra = self._replay_extra()
        first = self.request(
            [Message.text("user", "What is 12 + 30? Reply with the number only.")],
            256,
            effort=self.reasoning_effort,
        )
        reply = self.complete(first, extra=extra).response
        if not _reasoning_text(reply):
            raise _Skip("the model did not reason on the first turn")
        answer = self._own(reply.message)
        follow_up = Message.text("user", "Now add 8 to it. Reply with the number only.")
        after_user = self._prompt_delta(
            [*first.messages, answer, follow_up],
            [*first.messages, _without_reasoning(answer), follow_up],
            extra,
            [],
        )
        in_tool_turn: int | None = None
        if self.tool_turn is not None and _reasoning_text(self.tool_turn[1]):
            request, tool_reply = self.tool_turn
            message = self._own(tool_reply.message)
            results = [
                Message(
                    role="tool",
                    content=[
                        ToolResultPart(
                            call_id=call.id, name=call.name, content=[TextPart(text="18 C, sunny")]
                        )
                    ],
                )
                for call in message.tool_calls()
            ]
            in_tool_turn = self._prompt_delta(
                [*request.messages, message, *results],
                [*request.messages, _without_reasoning(message), *results],
                extra,
                request.tools,
            )
        policy = retention_policy(in_tool_turn, after_user)
        data = {
            "policy": policy,
            "prompt_delta_after_user": after_user,
            "prompt_delta_in_tool_turn": in_tool_turn,
        }
        if policy is None:
            return Outcome(
                None,
                "earlier reasoning is dropped after a new user message; no tool turn with"
                " reasoning to test",
                data,
            )
        return Outcome(True, f"policy {policy}", data)

    # ---- usage and caching --------------------------------------------------------------

    def check_usage_semantics(self) -> Outcome:
        system = f"Reference {uuid.uuid4().hex[:12]}. " + calibrate.filler(1_200, seed=7)
        request = self.request(
            [Message.text("user", "Reply with the word: done")], 1, room=False, system=system
        )
        cold = self.complete(request).response.usage
        warm = self.complete(request).response.usage
        if cold is None or warm is None or cold.input_tokens is None or warm.input_tokens is None:
            raise _Skip("no usage reported")
        self._learn_ratio(len(system), cold.input_tokens)
        return judge_usage_semantics(cold, warm)

    def check_prefix_cache(self) -> Outcome:
        prefix = f"Reference notes {uuid.uuid4().hex[:12]}.\n" + calibrate.filler(
            int(PREFIX_TOKENS * self.chars_per_token), seed=11
        )
        questions = (
            "In one word, what kind of text are these notes?",
            "In one word, name a place the notes mention.",
        )
        cold, warm = (
            self.measure(
                self.request([Message.text("user", question)], 8, room=False, system=prefix),
                "prefill",
            )
            for question in questions
        )
        self.prefill = cold
        self._learn_ratio(len(prefix), cold.input_tokens)
        return judge_prefix_cache(cold, warm)

    def check_throughput(self) -> Outcome:
        decode = self.sample(
            self.request(
                [Message.text("user", calibrate.DECODE_PROMPT)], calibrate.DECODE_TOKENS, room=False
            ),
            "decode",
        )
        prefill = self.prefill
        if prefill is None or prefill.prefill_rate()[0] is None:
            prompt = calibrate.prefill_prompt(uuid.uuid4().hex[:12])
            prefill = self.sample(
                self.request([Message.text("user", prompt)], 8, room=False), "prefill"
            )
        prefill_tps, prefill_source = prefill.prefill_rate()
        decode_tps, decode_source = decode.decode_rate()
        self.prefill_tps = prefill_tps
        self.throughput = {
            "prefill_tps": _round(prefill_tps),
            "decode_tps": _round(decode_tps),
            "ttft_ms": _ms(decode.ttft_s),
            "sources": {"prefill_tps": prefill_source, "decode_tps": decode_source},
            "prefill_tokens": prefill.input_tokens,
            "decode_tokens": decode.output_tokens,
        }
        missing = [
            label
            for label, value in (("prefill", prefill_tps), ("decode", decode_tps))
            if value is None
        ]
        if missing:
            return Outcome(
                False, f"could not measure {' or '.join(missing)}", dict(self.throughput)
            )
        detail = (
            f"prefill {prefill_tps:.0f} tok/s ({prefill_source}), decode {decode_tps:.1f} tok/s"
            f" ({decode_source})"
        )
        return Outcome(True, detail, dict(self.throughput))

    # ---- context length -----------------------------------------------------------------

    def _context_budget(self) -> float:
        remaining = self.deadline - time.perf_counter() - 5.0
        return max(MIN_CONTEXT_BUDGET_S, min(CONTEXT_BUDGET_S, remaining))

    def _context_limit(self) -> tuple[int | None, str | None]:
        num_ctx = _int(self.target.extensions.get("num_ctx"))
        if num_ctx:
            return num_ctx, "num_ctx sent with every request"
        info = self.target.model_info
        if self.target.discovery.engine == "ollama":
            loaded = ollama_loaded_context(
                self.target.config.url,
                self.target.model,
                headers=self.target.endpoint.headers,
                egress=self.egress,
                transport=self.shared,
            )
            if loaded:
                return loaded, "loaded context (/api/ps)"
            if _int(info.get("num_ctx")):
                return int(info["num_ctx"]), "Modelfile num_ctx"
        if self.target.discovery.server_context:
            return self.target.discovery.server_context, "server n_ctx (/props)"
        if _int(info.get("context_length")):
            return int(info["context_length"]), "model context length"
        return None, None

    def _prefill_rate(self) -> float | None:
        if self.prefill_tps:
            return self.prefill_tps
        return self.prefill.prefill_rate()[0] if self.prefill is not None else None

    def _needle(self, target: int, timeout: float) -> tuple[bool, int | None]:
        rng = random.Random(target)
        secret = f"{rng.choice(NEEDLE_WORDS)}-{rng.randint(1000, 9999)}"
        haystack = calibrate.filler(int(target * self.chars_per_token * 0.95), seed=target)
        cut = haystack.find(". ", len(haystack) // 6) + 2  # about one sixth of the way in
        text = f"{haystack[:cut]}The secret passphrase is {secret}. {haystack[cut:]}"
        question = (
            "What is the secret passphrase mentioned in the text above? Reply with the"
            " passphrase only."
        )
        request = self.request([Message.text("user", f"{text}\n\n{question}")], 24)
        reply = self.complete(request, timeout=timeout).response
        found = _squash(secret) in _squash(reply.message.text_content())
        return found, reply.usage.input_tokens if reply.usage else None

    def check_context_length(self) -> Outcome:
        limit, source = self._context_limit()
        cap = CONTEXT_CAP if limit is None else min(CONTEXT_CAP, limit - ANSWER_ROOM)
        if cap < 256:
            raise _Skip(f"the context ({limit} tokens) is too small for a needle test")
        lengths = sorted({length for length in NEEDLE_LENGTHS if length < cap} | {cap})
        budget = self._context_budget()
        rate = self._prefill_rate()
        spent = 0.0
        results: list[dict[str, Any]] = []
        ok_tokens: int | None = None
        stopped: str | None = None
        for length in lengths:
            predicted = None if rate is None else length / rate * 1.5 + 2.0
            if predicted is not None and spent + predicted > budget:
                stopped = f"stopped before {length} tokens: about {predicted:.0f} s, over budget"
                break
            started = time.perf_counter()
            try:
                found, used = self._needle(length, max(self.timeout, (predicted or 0.0) * 2))
            except ModelError as exc:
                results.append({"target": length, "found": False, "error": _error_text(exc)})
                break
            seconds = time.perf_counter() - started
            spent += seconds
            entry: dict[str, Any] = {
                "target": length,
                "input_tokens": used,
                "found": found,
                "seconds": round(seconds, 2),
            }
            if used is not None and used < 0.6 * length:
                entry["truncated"] = True
            results.append(entry)
            if not found:
                break
            ok_tokens = used or length
            if rate is None and used:
                rate = used / max(seconds, 1e-3)
        data: dict[str, Any] = {
            "limit": limit,
            "limit_source": source,
            "results": results,
            "ok_tokens": ok_tokens,
        }
        if stopped:
            data["stopped"] = stopped
        if not results:
            return Outcome(None, stopped or "nothing was tested", data)
        failed = next((entry for entry in results if not entry["found"]), None)
        if failed is not None:
            why = failed.get("error") or "the needle was lost"
            if failed.get("truncated"):
                why += " (the server cut the prompt)"
            reached = f"found up to {ok_tokens} tokens; " if ok_tokens else ""
            return Outcome(False, f"{reached}failed at about {failed['target']}: {why}", data)
        note = f" ({stopped})" if stopped else ""
        return Outcome(True, f"found the needle at {ok_tokens} prompt tokens{note}", data)

    # ---- summary ------------------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        def ok(name: str) -> bool | None:
            value = self.results.get(name, {}).get("ok")
            return value if isinstance(value, bool) else None

        def data(name: str, key: str) -> Any:
            return _dict(self.results.get(name, {}).get("data")).get(key)

        return {
            "native_tools": ok("native_tool_call"),
            "parallel_tools": ok("parallel_tool_calls"),
            "streamed_tool_args": ok("streamed_tool_args"),
            "tool_call_ids": data("tool_call_ids", "source"),
            "forced_tool_choice": ok("forced_tool_choice"),
            "assistant_prefill": ok("assistant_prefill"),
            "json_schema": ok("json_schema"),
            "fallback_tools": ok("fallback_tool_protocol"),
            "streaming": ok("streaming"),
            "usage": ok("usage"),
            "usage_includes_cached": data("usage_semantics", "includes_cached"),
            "reasoning_field": data("reasoning_field", "field"),
            "reasoning_retention": ok("reasoning_retention"),
            "context_ok_tokens": data("context_length", "ok_tokens"),
            "prefix_cache": ok("prefix_cache"),
        }


def retention_policy(in_tool_turn: int | None, after_user: int) -> str | None:
    """The retention policy from prompt-size differences (ADR 0003 item 8).

    None when reasoning is dropped after a new user message and no tool turn was measured,
    which leaves `drop` and `keep-for-tool-turns` apart.
    """
    if after_user >= RETENTION_MIN_DELTA:
        return "keep-all"
    if in_tool_turn is None:
        return None
    return "keep-for-tool-turns" if in_tool_turn >= RETENTION_MIN_DELTA else "drop"


def _raw_input(usage: Usage) -> tuple[str | None, int | None]:
    raw = usage.raw or {}
    for name in RAW_INPUT_FIELDS:
        value = raw.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return name, value
    return None, None


def judge_usage_semantics(cold: Usage, warm: Usage) -> Outcome:
    """Whether the server's raw input count includes cached tokens, from a repeated prompt."""
    gain = (warm.cached_input_tokens or 0) - (cold.cached_input_tokens or 0)
    data: dict[str, Any] = {
        "cold_input": cold.input_tokens,
        "warm_input": warm.input_tokens,
        "cold_cached": cold.cached_input_tokens,
        "warm_cached": warm.cached_input_tokens,
        "includes_cached": None,
    }
    if gain < 4:
        return Outcome(None, "no cached tokens reported for a repeated prompt; unknown", data)
    name, raw_cold = _raw_input(cold)
    _name, raw_warm = _raw_input(warm)
    if name is None or raw_cold is None or raw_warm is None:
        return Outcome(None, "no raw input-token count to compare; unknown", data)
    data.update(raw_field=name, raw_cold=raw_cold, raw_warm=raw_warm)
    tolerance = max(2, round(0.02 * raw_cold))
    drop = raw_cold - raw_warm
    if abs(drop) <= tolerance:
        includes = True
    elif abs(drop - gain) <= tolerance:
        includes = False
    else:
        detail = f"{name} fell by {drop} while {gain} more tokens were cached; unclear"
        return Outcome(None, detail, data)
    data["includes_cached"] = includes
    consistent = abs((cold.input_tokens or 0) - (warm.input_tokens or 0)) <= tolerance
    data["normalized_consistent"] = consistent
    verb = "includes" if includes else "excludes"
    if not consistent:
        detail = (
            f"{name} {verb} cached tokens, but the normalized input counts differ (cold"
            f" {cold.input_tokens}, warm {warm.input_tokens})"
        )
        return Outcome(False, detail, data)
    return Outcome(
        True,
        f"{name} {verb} cached tokens ({warm.cached_input_tokens} of {warm.input_tokens})",
        data,
    )


def judge_prefix_cache(cold: calibrate.Sample, warm: calibrate.Sample) -> Outcome:
    """Whether a repeated long prefix was served from the server's cache."""
    data: dict[str, Any] = {
        "cold_input": cold.input_tokens,
        "warm_input": warm.input_tokens,
        "warm_cached": warm.cached_tokens,
        "cold_ttft_ms": _ms(cold.ttft_s),
        "warm_ttft_ms": _ms(warm.ttft_s),
    }
    if cold.ttft_s and warm.ttft_s:
        data["speedup"] = round(cold.ttft_s / warm.ttft_s, 2)
    if warm.cached_tokens is not None and warm.input_tokens:
        share = warm.cached_tokens / warm.input_tokens
        data.update(source="usage", cached_share=round(share, 3))
        counts = f"{warm.cached_tokens} of {warm.input_tokens} prompt tokens"
        if share >= 0.5:
            return Outcome(True, f"{counts} came from the cache", data)
        return Outcome(False, f"only {counts} came from the cache", data)
    if cold.ttft_s and warm.ttft_s and cold.ttft_s >= 0.05:
        data["source"] = "timing"
        speedup = cold.ttft_s / warm.ttft_s
        if speedup >= 1.6:
            return Outcome(True, f"time to first token fell {speedup:.1f}x on the repeat", data)
        return Outcome(False, f"time to first token barely changed ({speedup:.1f}x)", data)
    return Outcome(None, "no cached-token counts, and the prompt was too fast to time", data)


# --------------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------------


def run(
    config: EndpointConfig,
    *,
    model: str | None = None,
    fmt: str | None = None,
    quick: bool = False,
    egress: net.Egress | None = None,
    echo: Callable[[str], None] = print,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Run the capability battery against one endpoint and return its profile (schema 1).

    `timeout` is the per-request timeout in seconds; each check's limit is a small multiple of
    it. ValueError when no model can be determined or the format is unknown.
    """
    started = time.perf_counter()
    created = datetime.now(UTC).isoformat(timespec="seconds")
    egress = egress or net.default()
    shared = _SharedTransport(transport)
    try:
        target = resolve(
            config,
            model=model,
            fmt=fmt,
            quick=quick,
            egress=egress,
            transport=shared,
            timeout=min(timeout, DISCOVERY_TIMEOUT_S),
        )
        discovery = target.discovery
        mode = ", quick" if quick else ""
        echo(target.scrub_text(f"Probing {target.label} ({target.format}{mode})"))
        engine = " ".join(item for item in (discovery.engine, discovery.engine_version) if item)
        echo(
            target.scrub_text(
                f"  discovery: engine {engine or 'unknown'};"
                f" formats {', '.join(discovery.formats) or 'none found'}"
            )
        )
        battery = _Battery(
            target, quick=quick, egress=egress, shared=shared, timeout=timeout, echo=echo
        )
        battery.run()
    finally:
        shared.shutdown()
    if battery.results.get("basic_chat", {}).get("ok") and target.format not in discovery.formats:
        discovery.add_format(target.format, "answered the probe")
    formats = Outcome(
        bool(discovery.formats),
        ", ".join(discovery.formats) or "no format identified by the read-only requests",
        {
            "evidence": dict(discovery.evidence),
            "engine": discovery.engine,
            "health": discovery.health,
        },
    )
    battery.record("formats_served", formats, discovery.seconds)
    hash_value, hash_source = model_hash(target)
    engine_name = target.engine
    profile: dict[str, Any] = {
        "schema": SCHEMA,
        "endpoint": target.endpoint_label,
        "model": target.model_label,
        "format": target.format,
        "engine": engine_name,
        "engine_version": discovery.engine_version if discovery.engine == engine_name else None,
        "scheme": _url_parts(config.url)[0] or None,
        "local": config.is_local(),
        "created": created,
        "duration_s": round(time.perf_counter() - started, 2),
        "quick": quick,
        "formats_served": list(discovery.formats),
        "model_hash": hash_value,
        "model_hash_source": hash_source,
        "model_info": dict(target.model_info),
        "settings": dict(target.extensions),
        "checks": {name: battery.results[name] for name in CHECKS},
        "capabilities": battery.capabilities(),
        "throughput": {} if quick else dict(battery.throughput),
    }
    counts = [entry["ok"] for entry in profile["checks"].values()]
    echo(
        f"Done in {profile['duration_s']:.1f}s: {counts.count(True)} passed,"
        f" {counts.count(False)} failed, {counts.count(None)} skipped"
    )
    return target.scrub(profile)


def hardware_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """The few hardware facts an export shares, from a `stepswitch.doctor.collect()` report.

    Counts, sizes and the GPU backend only: processor and GPU product names are left out,
    because they carry manufacturer names and exports are committed (ADR 0007)."""
    system = _dict(report.get("os"))
    cpu = _dict(report.get("cpu"))
    gpus = [
        {
            "vram_gb": _num(_dict(gpu).get("vram_gb")),
            "backend": _str(_dict(gpu).get("backend")),
        }
        for gpu in _list(report.get("gpus"))
    ]
    return {
        "os": {"system": _str(system.get("system")), "machine": _str(system.get("machine"))},
        "cpu": {
            "cores_physical": _int(cpu.get("cores_physical")),
            "cores_logical": _int(cpu.get("cores_logical")),
        },
        "ram_gb": _num(report.get("ram_gb")),
        "gpus": gpus,
    }


def export(profile: dict[str, Any], path: Path, *, hardware: dict[str, Any]) -> Path:
    """Write a shareable probe result (SPEC §4.7) and return its path.

    The result is meant for public pull requests: it carries the model, its hash, the engine
    and its version, the format, a compact hardware summary, the date, the checks, the
    capabilities and the throughput. Of the endpoint it keeps only the scheme and whether it is
    local.
    """
    created = str(profile.get("created") or "")
    date = created[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", created) else None
    model = profile.get("model")
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": EXPORT_KIND,
        "date": date or datetime.now(UTC).date().isoformat(),
        "model": safe_model_name(model) if isinstance(model, str) else None,
        "model_hash": profile.get("model_hash"),
        "model_hash_source": profile.get("model_hash_source"),
        "engine": profile.get("engine"),
        "engine_version": profile.get("engine_version"),
        "format": profile.get("format"),
        "endpoint": {"scheme": profile.get("scheme"), "local": profile.get("local")},
        "quick": profile.get("quick"),
        "hardware": hardware_summary(hardware),
        "checks": profile.get("checks") or {},
        "capabilities": profile.get("capabilities") or {},
        "throughput": profile.get("throughput") or {},
    }
    cleaned = Scrubber(secret_values(None)).tree(result)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return destination
