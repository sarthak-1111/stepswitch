"""HANDOFF v0.1 briefs (SPEC §9, TK7; the open spec is spec/handoff-v0.1.md).

A brief hands a task from one worker to another when re-reading the transcript would cost more
than reading a summary (SPEC §7.2), and replaces old turns when a transcript reaches 60% of its
budget (TK6). This module builds briefs from facts the agent collected, lets a T0 summarizer
write the free-text state with a JSON-Schema-constrained answer (TK8), validates every brief
against the schema, renders it as Markdown and trims it until the receiving model's tokenizer
counts at most `MAX_TOKENS` (`fit`).

Nothing here talks to a model except `write_state`, which takes the summarizer's step model.
"""

from __future__ import annotations

import copy
import functools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from stepswitch.context.tokens import Counter, TokenCount, count

VERSION = "0.1"
MAX_TOKENS = 1500
SCHEMA_FILE = "handoff-v0.1.schema.json"
# An estimated count (no exact tokenizer) is held this far below the limit.
ESTIMATE_MARGIN = 0.8
# Items the deterministic builder keeps before `fit` trims further.
MAX_DONE = 20
MAX_FILES = 15
MAX_SYMBOLS = 8
MAX_FAILING = 3
EXCERPT_CHARS = 1200

# The summarizer's answer: the free-text part of `state` (TK8 keeps it to the grammar subset).
WRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "done": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "decisions": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "what": {"type": "string", "minLength": 1, "maxLength": 300},
                    "why": {"type": "string", "maxLength": 500},
                },
                "required": ["what", "why"],
                "additionalProperties": False,
            },
        },
        "open_questions": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
    },
    "required": ["done", "decisions", "open_questions"],
    "additionalProperties": False,
}

WRITER_PROMPT = """\
You write the state section of a handoff brief: another model continues this coding task from
your summary alone. From the work log below, list what was done, the decisions taken with their
reasons, and open questions. Keep file paths, function names, error messages and numbers
exactly as they appear. Be brief. Answer with one JSON object with the keys "done" (list of
strings), "decisions" (list of {"what", "why"}) and "open_questions" (list of strings)."""


class HandoffError(ValueError):
    pass


def schema_path() -> Path:
    """The schema shipped with the package, or spec/ in a source checkout."""
    package = Path(__file__).resolve().parents[1]
    for candidate in (package / "_spec" / SCHEMA_FILE, package.parents[1] / "spec" / SCHEMA_FILE):
        if candidate.is_file():
            return candidate
    raise HandoffError(f"{SCHEMA_FILE} not found")


@functools.cache
def load_schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(schema_path().read_text(encoding="utf-8"))
    return data


@functools.cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema())


def validate(brief: Mapping[str, Any]) -> list[str]:
    """Schema errors of `brief` (empty when it is valid). Offline: the schema has no $ref."""
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or 'brief'}: {error.message}"
        for error in sorted(_validator().iter_errors(dict(brief)), key=lambda e: list(e.path))
    ]


# --------------------------------------------------------------------------------------------
# Facts and building
# --------------------------------------------------------------------------------------------


@dataclass
class FileNote:
    path: str
    symbols: list[str] = field(default_factory=list)
    why: str = ""


@dataclass
class Failure:
    check: str
    excerpt: str


@dataclass
class HandoffFacts:
    """What the agent knows when it hands a task over (collected by the agent loop)."""

    task_id: str
    goal: str
    track: str = "general"
    constraints: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    golden: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    decisions: list[dict[str, str]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    files: list[FileNote] = field(default_factory=list)
    failing: list[Failure] = field(default_factory=list)
    tokens_left: int | None = None
    seconds_left: float | None = None
    from_worker: str = "unknown"
    step: int = 0


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split()) if limit <= 500 else str(text).strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def _strings(items: Sequence[Any], limit: int, max_items: int) -> list[str]:
    out = []
    for item in items:
        text = _clip(str(item), limit)
        if text and text not in out:
            out.append(text)
    return out[-max_items:] if max_items else out


def build(
    facts: HandoffFacts, *, tokenizer: str = "unknown", exact: bool = False
) -> dict[str, Any]:
    """A brief from `facts`, clipped to the schema's limits (`fit` trims to the token budget)."""
    decisions = [
        {"what": _clip(d.get("what", ""), 300), "why": _clip(d.get("why", ""), 500)}
        for d in facts.decisions
        if str(d.get("what", "")).strip()
    ][-20:]
    files = []
    for note in facts.files[:MAX_FILES]:
        if not note.path.strip():
            continue
        files.append(
            {
                "path": _clip(note.path, 500),
                "symbols": _strings(note.symbols, 200, 0)[:MAX_SYMBOLS],
                "why": _clip(note.why, 300),
            }
        )
    failing = [
        {"check": _clip(f.check, 200) or "check", "excerpt": _clip(f.excerpt, EXCERPT_CHARS)}
        for f in facts.failing[-MAX_FAILING:]
    ]
    brief = {
        "version": VERSION,
        "task_id": _clip(facts.task_id, 200) or "task",
        "goal": _clip(facts.goal, 4000) or "(no goal given)",
        "track": facts.track if facts.track in {"general", "science"} else "general",
        "constraints": _strings(facts.constraints, 500, 20),
        "acceptance": {
            "commands": _strings(facts.commands, 500, 20),
            "golden": _strings(facts.golden, 500, 50),
        },
        "state": {
            "done": _strings(facts.done, 300, MAX_DONE),
            "decisions": decisions,
            "open_questions": _strings(facts.open_questions, 300, 10),
        },
        "files": files,
        "failing": failing,
        "budget_left": {
            "tokens": max(0, int(facts.tokens_left)) if facts.tokens_left is not None else None,
            "seconds": round(max(0.0, facts.seconds_left), 1)
            if facts.seconds_left is not None
            else None,
        },
        "provenance": {
            "from_worker": _clip(facts.from_worker, 300) or "unknown",
            "step": max(0, int(facts.step)),
            "tokenizer": _clip(tokenizer, 300) or "unknown",
            "exact": bool(exact),
        },
    }
    return brief


# --------------------------------------------------------------------------------------------
# Rendering (normative: spec/handoff-v0.1.md, section 3)
# --------------------------------------------------------------------------------------------


def _fence(text: str) -> str:
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return f"{ticks}text\n{text}\n{ticks}"


def render_markdown(brief: Mapping[str, Any]) -> str:
    """The brief as the receiving model reads it. Empty sections are left out; provenance is
    metadata for the logs and is not rendered."""
    lines = [f"# Handoff: {brief.get('task_id', '')}", "", f"Goal: {brief.get('goal', '')}"]
    lines.append(f"Track: {brief.get('track', 'general')}")

    def bullets(title: str, items: Sequence[str]) -> None:
        if items:
            lines.extend(["", f"## {title}", *(f"- {item}" for item in items)])

    bullets("Constraints", list(brief.get("constraints") or []))
    acceptance = brief.get("acceptance") or {}
    commands = list(acceptance.get("commands") or [])
    golden = list(acceptance.get("golden") or [])
    if commands or golden:
        lines.extend(["", "## Acceptance"])
        lines.extend(f"- command: `{cmd}`" for cmd in commands)
        lines.extend(f"- golden output: {path}" for path in golden)
    state = brief.get("state") or {}
    bullets("Done so far", list(state.get("done") or []))
    decisions = [
        f"{d.get('what', '')}" + (f": {d['why']}" if d.get("why") else "")
        for d in state.get("decisions") or []
    ]
    bullets("Decisions", decisions)
    bullets("Open questions", list(state.get("open_questions") or []))
    files = [
        f"`{f.get('path', '')}`"
        + (f" ({', '.join(f.get('symbols') or [])})" if f.get("symbols") else "")
        + (f": {f['why']}" if f.get("why") else "")
        for f in brief.get("files") or []
    ]
    bullets("Files", files)
    failing = list(brief.get("failing") or [])
    if failing:
        lines.extend(["", "## Failing checks"])
        for item in failing:
            lines.append(f"### {item.get('check', '')}")
            if item.get("excerpt"):
                lines.append(_fence(str(item["excerpt"])))
    budget = brief.get("budget_left") or {}
    tokens, seconds = budget.get("tokens"), budget.get("seconds")
    if tokens is not None or seconds is not None:
        parts = []
        if tokens is not None:
            parts.append(f"{tokens} tokens")
        if seconds is not None:
            parts.append(f"{seconds} seconds")
        lines.extend(["", f"Budget left: {', '.join(parts)}"])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------
# Fitting to the token budget
# --------------------------------------------------------------------------------------------


def _trims() -> list[tuple[str, Callable[[dict[str, Any]], bool]]]:
    """Deterministic trimming steps, least important content first. Each returns whether it
    changed the brief."""

    def cut_excerpts(limit: int) -> Callable[[dict[str, Any]], bool]:
        def step(brief: dict[str, Any]) -> bool:
            changed = False
            for item in brief["failing"]:
                if len(item["excerpt"]) > limit:
                    item["excerpt"] = item["excerpt"][: limit - 3].rstrip() + "..."
                    changed = True
            return changed

        return step

    def keep_last(path: tuple[str, ...], n: int) -> Callable[[dict[str, Any]], bool]:
        def step(brief: dict[str, Any]) -> bool:
            node = brief
            for key in path[:-1]:
                node = node[key]
            items = node[path[-1]]
            if len(items) > n:
                node[path[-1]] = items[-n:] if n else []
                return True
            return False

        return step

    def keep_first(path: tuple[str, ...], n: int) -> Callable[[dict[str, Any]], bool]:
        def step(brief: dict[str, Any]) -> bool:
            node = brief
            for key in path[:-1]:
                node = node[key]
            items = node[path[-1]]
            if len(items) > n:
                node[path[-1]] = items[:n]
                return True
            return False

        return step

    def symbols(n: int) -> Callable[[dict[str, Any]], bool]:
        def step(brief: dict[str, Any]) -> bool:
            changed = False
            for item in brief["files"]:
                if len(item["symbols"]) > n:
                    item["symbols"] = item["symbols"][:n]
                    changed = True
            return changed

        return step

    def goal(limit: int) -> Callable[[dict[str, Any]], bool]:
        def step(brief: dict[str, Any]) -> bool:
            if len(brief["goal"]) > limit:
                brief["goal"] = brief["goal"][: limit - 3].rstrip() + "..."
                return True
            return False

        return step

    return [
        ("excerpts to 600 characters", cut_excerpts(600)),
        ("done to the last 12", keep_last(("state", "done"), 12)),
        ("symbols to 4 per file", symbols(4)),
        ("files to the first 10", keep_first(("files",), 10)),
        ("failing to the last 2", keep_last(("failing",), 2)),
        ("excerpts to 300 characters", cut_excerpts(300)),
        ("decisions to the last 5", keep_last(("state", "decisions"), 5)),
        ("open questions to 3", keep_first(("state", "open_questions"), 3)),
        ("done to the last 6", keep_last(("state", "done"), 6)),
        ("files to the first 5", keep_first(("files",), 5)),
        ("constraints to the first 8", keep_first(("constraints",), 8)),
        ("goal to 1500 characters", goal(1500)),
        ("symbols dropped", symbols(0)),
        ("failing to the last 1", keep_last(("failing",), 1)),
        ("excerpts to 150 characters", cut_excerpts(150)),
        ("done to the last 3", keep_last(("state", "done"), 3)),
        ("goal to 800 characters", goal(800)),
        ("golden files to the first 10", keep_first(("acceptance", "golden"), 10)),
        ("decisions to the last 2", keep_last(("state", "decisions"), 2)),
        ("files to the first 2", keep_first(("files",), 2)),
        ("goal to 400 characters", goal(400)),
        ("done dropped", keep_last(("state", "done"), 0)),
        ("files dropped", keep_first(("files",), 0)),
        ("decisions dropped", keep_last(("state", "decisions"), 0)),
        ("open questions dropped", keep_first(("state", "open_questions"), 0)),
        ("failing dropped", keep_last(("failing",), 0)),
        ("constraints to the first 3", keep_first(("constraints",), 3)),
        ("goal to 200 characters", goal(200)),
    ]


@dataclass
class Fitted:
    brief: dict[str, Any]
    markdown: str
    count: TokenCount
    trims: list[str]
    errors: list[str]

    @property
    def valid(self) -> bool:
        return not self.errors


def fit(brief: Mapping[str, Any], counter: Counter, *, limit: int = MAX_TOKENS) -> Fitted:
    """Trim `brief` until its rendering counts at most `limit` tokens with `counter` (an
    estimate must stay under `limit * ESTIMATE_MARGIN`). The provenance records the counter.
    The result is validated; `errors` lists what the schema rejected (none for briefs built
    here)."""
    work = copy.deepcopy(dict(brief))
    work["provenance"] = {
        **work.get("provenance", {}),
        "tokenizer": counter.name[:300] or "unknown",
        "exact": bool(counter.exact),
    }
    trims: list[str] = []
    steps = iter(_trims())
    while True:
        markdown = render_markdown(work)
        measured = count(markdown, counter)
        if measured.tokenizer != work["provenance"]["tokenizer"]:
            # The counter failed and the count fell back to an estimate: say so.
            work["provenance"]["tokenizer"] = measured.tokenizer[:300]
            work["provenance"]["exact"] = measured.exact
            markdown = render_markdown(work)
            measured = count(markdown, counter)
        target = limit if measured.exact else int(limit * ESTIMATE_MARGIN)
        if measured.tokens <= target:
            break
        for name, step in steps:
            if step(work):
                trims.append(name)
                break
        else:
            raise HandoffError(f"brief still has {measured.tokens} tokens after every trim")
    return Fitted(work, markdown, measured, trims, validate(work))


# --------------------------------------------------------------------------------------------
# The T0 summarizer (TK8: JSON-Schema-constrained answer)
# --------------------------------------------------------------------------------------------


def write_state(
    model: Any, digest: str, *, max_output_tokens: int = 700, seed: int | None = 0
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Ask the summarizer `model` (`complete(request) -> Completion`) for the brief's state.

    Returns (state or None, details). A reply that is not valid JSON for WRITER_SCHEMA gives
    None, and the caller keeps the state it built from the work log."""
    import jsonschema

    from stepswitch.compat.errors import ModelError
    from stepswitch.compat.ir import Message, Request, ResponseFormat, Sampling, TextPart

    request = Request(
        model="",
        system=[TextPart(text=WRITER_PROMPT)],
        messages=[Message.text("user", digest)],
        response_format=ResponseFormat(
            type="json_schema", name="handoff_state", json_schema=WRITER_SCHEMA, strict=True
        ),
        max_output_tokens=max_output_tokens,
        sampling=Sampling(temperature=0.0, seed=seed),
    )
    try:
        completion = model.complete(request)
    except ModelError as exc:
        return None, {"writer": "fallback", "reason": f"model error: {exc}"}
    text = completion.response.message.text_content().strip()
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start : end + 1]) if 0 <= start < end else None
        jsonschema.validate(data, WRITER_SCHEMA)
    except (ValueError, jsonschema.ValidationError) as exc:
        return None, {"writer": "fallback", "reason": f"answer unusable: {str(exc)[:200]}"}
    usage = completion.response.usage
    state: dict[str, Any] = dict(data or {})
    return state, {
        "writer": "model",
        "usage": usage.model_dump(exclude={"raw"}) if usage else None,
        "completion": completion,
    }
