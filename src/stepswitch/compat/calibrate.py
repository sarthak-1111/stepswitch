"""`ssw calibrate`: prefill and decode throughput and time to first token (SPEC §14 RN11).

`run` measures one endpoint with streaming requests sent through `ModelClient` (working
rule 2); the probe's throughput check reuses the same primitives:

* prefill: a prompt of about 1.5K tokens that asks for a one-word answer, with a fresh nonce at
  its start so that no server-side prefix cache can serve it;
* decode: a short prompt that asks for a long answer, capped at 128 output tokens;
* time to first token (TTFT): from sending the request to the first delta that carries
  generated content (text, reasoning, a refusal or a tool call), reported for the short decode
  prompts.

Server-reported timings win when a response's raw usage carries them, and every rate names its
source ("server" or "client"):

* Ollama native API: `prompt_eval_count` (the whole prompt, cached tokens included),
  `prompt_eval_cached_count`, `prompt_eval_duration` (uncached tokens only), `eval_count` and
  `eval_duration`, durations in nanoseconds (docs/RESEARCH.md "Ollama"; Ollama 0.34.4 server,
  checked 2026-10-16).
* llama.cpp native API: `timings.prompt_n`, `prompt_ms`, `predicted_n` and `predicted_ms`
  (https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md, checked
  2026-10-16).

Otherwise the client clock is used: prefill = uncached input tokens / TTFT and decode =
(output tokens - 1) / (total time - TTFT). The clock is injectable, so the arithmetic is tested
with deterministic timings.
"""

from __future__ import annotations

import random
import statistics
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, get_args

import httpx

from stepswitch import net
from stepswitch.compat.client import ModelClient
from stepswitch.compat.errors import ErrorKind, ModelError
from stepswitch.compat.ir import (
    Delta,
    Message,
    ReasoningDelta,
    RefusalDelta,
    Request,
    Response,
    TextDelta,
    ToolCallStartDelta,
    Usage,
)
from stepswitch.compat.stream import StreamAssembler
from stepswitch.config import EndpointConfig

Clock = Callable[[], float]

PREFILL_TOKENS = 1_536
PREFILL_ANSWER_TOKENS = 8
DECODE_TOKENS = 128
# Rough characters per token of the English filler text; measured counts come from usage.
CHARS_PER_TOKEN = 4.0
DECODE_PROMPT = (
    "Write a long, detailed story about a lighthouse keeper who spends a winter alone on a "
    "rocky island. Describe the weather, the sea and the daily routine. Keep writing; do not "
    "stop early."
)
PREFILL_QUESTION = "In one word, what kind of text are these notes?"

_KINDS: dict[str, ErrorKind] = {kind: kind for kind in get_args(ErrorKind)}

# Building blocks of the filler text (prefill prompts, prefix-cache prompts, needle haystacks).
_SUBJECTS = (
    "The river",
    "A lighthouse",
    "The old library",
    "A mountain trail",
    "The market square",
    "A quiet harbor",
    "The northern forest",
    "An abandoned mill",
    "The stone bridge",
    "A narrow valley",
)
_VERBS = (
    "winds past",
    "looks over",
    "stands beside",
    "leads toward",
    "borders",
    "faces",
    "shelters",
    "overlooks",
)
_OBJECTS = (
    "a row of stone cottages",
    "the eastern cliffs",
    "a field of sunflowers",
    "the railway station",
    "a small chapel",
    "the fishing boats",
    "a narrow canal",
    "the clock tower",
    "an orchard of pear trees",
    "the salt marshes",
)
_TIMES = (
    "in early spring.",
    "after the evening rain.",
    "under a pale sky.",
    "during the summer fair.",
    "on quiet afternoons.",
    "before the first snow.",
    "at sunrise.",
    "when the tide is low.",
)


def filler(chars: int, seed: int = 0) -> str:
    """Deterministic, varied prose of at least `chars` characters."""
    rng = random.Random(seed)
    sentences: list[str] = []
    size = 0
    while size < chars:
        sentence = (
            f"{rng.choice(_SUBJECTS)} {rng.choice(_VERBS)} "
            f"{rng.choice(_OBJECTS)} {rng.choice(_TIMES)}"
        )
        sentences.append(sentence)
        size += len(sentence) + 1
    return " ".join(sentences)


def prefill_prompt(nonce: str, tokens: int = PREFILL_TOKENS, seed: int = 1) -> str:
    """A prompt of about `tokens` tokens; the nonce at its start defeats prefix caches."""
    notes = filler(int(tokens * CHARS_PER_TOKEN), seed=seed)
    return f"Notes {nonce}:\n{notes}\n\n{PREFILL_QUESTION}"


# --------------------------------------------------------------------------------------------
# Measurements
# --------------------------------------------------------------------------------------------


@dataclass
class StreamResult:
    """One streamed reply with client-side timings (seconds from sending the request)."""

    response: Response
    deltas: list[Delta]
    ttft_s: float | None
    total_s: float

    @property
    def text_deltas(self) -> int:
        return sum(1 for delta in self.deltas if isinstance(delta, TextDelta))


# Deltas that carry generated content; the first one marks the time to first token.
_GENERATED = (TextDelta, ReasoningDelta, RefusalDelta, ToolCallStartDelta)


def stream_once(
    client: ModelClient, request: Request, clock: Clock = time.perf_counter
) -> StreamResult:
    """Stream `request` and assemble the reply. An error inside the stream raises ModelError."""
    assembler = StreamAssembler()
    deltas: list[Delta] = []
    started = clock()
    first: float | None = None
    for delta in client.stream(request):
        if first is None and isinstance(delta, _GENERATED):
            first = clock()
        deltas.append(delta)
        assembler.feed(delta)
    total = clock() - started
    error = assembler.error
    if error is not None:
        raise ModelError(_KINDS.get(error.kind, "server"), error.message, status=error.status)
    ttft = None if first is None else first - started
    return StreamResult(assembler.result(), deltas, ttft, total)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def server_timings(usage: Usage | None) -> dict[str, float]:
    """Prompt and generation counts with their durations (seconds) from a raw usage object."""
    raw = usage.raw if usage is not None and isinstance(usage.raw, dict) else {}
    out: dict[str, float] = {}
    # Ollama native: nanoseconds; prompt_eval_duration covers the uncached prompt tokens only.
    prompt_ns = _number(raw.get("prompt_eval_duration"))
    prompt_count = _number(raw.get("prompt_eval_count"))
    if prompt_ns and prompt_count is not None:
        evaluated = prompt_count - (_number(raw.get("prompt_eval_cached_count")) or 0.0)
        if evaluated > 0:
            out["prompt_n"], out["prompt_s"] = evaluated, prompt_ns / 1e9
    eval_ns = _number(raw.get("eval_duration"))
    eval_count = _number(raw.get("eval_count"))
    if eval_ns and eval_count:
        out["predicted_n"], out["predicted_s"] = eval_count, eval_ns / 1e9
    # llama.cpp native: milliseconds.
    timings = raw.get("timings")
    if isinstance(timings, dict):
        prompt_n = _number(timings.get("prompt_n"))
        prompt_ms = _number(timings.get("prompt_ms"))
        if prompt_n and prompt_ms:
            out["prompt_n"], out["prompt_s"] = prompt_n, prompt_ms / 1000
        predicted_n = _number(timings.get("predicted_n"))
        predicted_ms = _number(timings.get("predicted_ms"))
        if predicted_n and predicted_ms:
            out["predicted_n"], out["predicted_s"] = predicted_n, predicted_ms / 1000
    return out


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 1)


def _rate(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


@dataclass
class Sample:
    """One measured request: token counts, client timings and server timings."""

    kind: str
    input_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
    ttft_s: float | None = None
    total_s: float | None = None
    server: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    def prefill_rate(self) -> tuple[float | None, str | None]:
        """Prompt tokens per second and the source of the timing."""
        count, seconds = self.server.get("prompt_n"), self.server.get("prompt_s")
        if count and seconds:
            return count / seconds, "server"
        if self.input_tokens and self.ttft_s:
            uncached = self.input_tokens - (self.cached_tokens or 0)
            if uncached > 0:
                return uncached / self.ttft_s, "client"
        return None, None

    def decode_rate(self) -> tuple[float | None, str | None]:
        """Generated tokens per second and the source of the timing."""
        count, seconds = self.server.get("predicted_n"), self.server.get("predicted_s")
        if count and seconds:
            return count / seconds, "server"
        if (
            self.output_tokens
            and self.output_tokens > 1
            and self.ttft_s is not None
            and self.total_s is not None
            and self.total_s > self.ttft_s
        ):
            return (self.output_tokens - 1) / (self.total_s - self.ttft_s), "client"
        return None, None

    def as_dict(self) -> dict[str, Any]:
        prefill, prefill_source = self.prefill_rate()
        decode, decode_source = self.decode_rate()
        out: dict[str, Any] = {
            "kind": self.kind,
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "output_tokens": self.output_tokens,
            "ttft_ms": _ms(self.ttft_s),
            "total_ms": _ms(self.total_s),
            "server": {key: round(value, 6) for key, value in self.server.items()} or None,
        }
        if self.kind == "prefill":
            out.update(prefill_tps=_rate(prefill), prefill_source=prefill_source)
        else:
            out.update(decode_tps=_rate(decode), decode_source=decode_source)
        if self.error:
            out["error"] = self.error
        return out


def sample(
    client: ModelClient, request: Request, kind: str, clock: Clock = time.perf_counter
) -> Sample:
    """Stream one request and keep its counts and timings."""
    result = stream_once(client, request, clock)
    usage = result.response.usage
    return Sample(
        kind=kind,
        input_tokens=usage.input_tokens if usage else None,
        cached_tokens=usage.cached_input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        ttft_s=result.ttft_s,
        total_s=result.total_s,
        server=server_timings(usage),
    )


def median_rate(rates: list[tuple[float | None, str | None]]) -> tuple[float | None, str | None]:
    """Median of the measured rates, with "server", "client" or "mixed" as the source."""
    values = [value for value, _source in rates if value is not None]
    sources = {source for value, source in rates if value is not None and source}
    if not values:
        return None, None
    source = sources.pop() if len(sources) == 1 else "mixed"
    return statistics.median(values), source


# --------------------------------------------------------------------------------------------
# ssw calibrate
# --------------------------------------------------------------------------------------------


def run(
    config: EndpointConfig,
    *,
    model: str | None = None,
    egress: net.Egress | None = None,
    echo: Callable[[str], None] = print,
    transport: httpx.BaseTransport | None = None,
    repeats: int = 2,
    clock: Clock = time.perf_counter,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Measure prefill and decode throughput and TTFT of one endpoint (SPEC §14 RN11).

    Model: `model`, else the endpoint's first configured model, else the first one the server
    lists (ValueError if none). A failed warm-up request raises ModelError; a failed sample is
    recorded in `errors` and the others still count.
    """
    from stepswitch.compat import probe  # probe imports this module

    egress = egress or net.default()
    target = probe.resolve(config, model=model, quick=True, egress=egress, transport=transport)
    client = ModelClient(
        target.endpoint,
        egress=egress,
        transport=transport,
        timeout=timeout,
        max_retries=1,
        purpose="calibrate",
    )
    echo(target.scrub_text(f"Calibrating {target.label} ({target.format})"))
    # Warm-up: loads the model if needed, so the first sample does not time the load.
    client.complete(target.request([Message.text("user", "Reply with the word: ready")], 1))
    samples: list[Sample] = []
    errors: list[str] = []
    rounds = max(1, repeats)
    for index in range(rounds):
        nonce = uuid.uuid4().hex[:12]
        planned = (
            (
                "prefill",
                target.request(
                    [Message.text("user", prefill_prompt(nonce, seed=index + 1))],
                    PREFILL_ANSWER_TOKENS,
                ),
            ),
            ("decode", target.request([Message.text("user", DECODE_PROMPT)], DECODE_TOKENS)),
        )
        for kind, request in planned:
            try:
                measured = sample(client, request, kind, clock)
            except ModelError as exc:
                message = target.scrub_text(f"{kind} {index + 1}: {exc.kind}: {exc.message}")
                measured = Sample(kind=kind, error=message[:300])
                errors.append(message[:300])
            samples.append(measured)
            echo(target.scrub_text(_describe(measured, index + 1, rounds)))
    prefill, prefill_source = median_rate(
        [s.prefill_rate() for s in samples if s.kind == "prefill"]
    )
    decode, decode_source = median_rate([s.decode_rate() for s in samples if s.kind == "decode"])
    ttfts = [s.ttft_s for s in samples if s.kind == "decode" and s.ttft_s is not None]
    ttft = statistics.median(ttfts) if ttfts else None
    result: dict[str, Any] = {
        "endpoint": target.endpoint_label,
        "model": target.model_label,
        "format": target.format,
        "engine": config.engine or target.discovery.engine,
        "engine_version": target.discovery.engine_version,
        "prefill_tps": _rate(prefill),
        "decode_tps": _rate(decode),
        "ttft_ms": _ms(ttft),
        "sources": {
            "prefill_tps": prefill_source,
            "decode_tps": decode_source,
            "ttft_ms": "client" if ttft is not None else None,
        },
        "repeats": rounds,
        "samples": [s.as_dict() for s in samples],
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if target.extensions:
        result["settings"] = dict(target.extensions)
    if errors:
        result["errors"] = errors
    echo(
        target.scrub_text(
            f"  prefill {_show(result['prefill_tps'])} tok/s ({prefill_source or 'n/a'}), "
            f"decode {_show(result['decode_tps'])} tok/s ({decode_source or 'n/a'}), "
            f"TTFT {_show(result['ttft_ms'])} ms"
        )
    )
    scrubbed: dict[str, Any] = target.scrub(result)
    return scrubbed


def _show(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _describe(measured: Sample, index: int, rounds: int) -> str:
    label = f"  {measured.kind} {index}/{rounds}:"
    if measured.error:
        return f"{label} failed ({measured.error})"
    if measured.kind == "prefill":
        rate, source = measured.prefill_rate()
        return (
            f"{label} {measured.input_tokens} prompt tokens, TTFT {_ms(measured.ttft_s)} ms, "
            f"{_show(_rate(rate))} tok/s ({source or 'n/a'})"
        )
    rate, source = measured.decode_rate()
    return (
        f"{label} {measured.output_tokens} tokens in {_ms(measured.total_s)} ms, "
        f"{_show(_rate(rate))} tok/s ({source or 'n/a'})"
    )
