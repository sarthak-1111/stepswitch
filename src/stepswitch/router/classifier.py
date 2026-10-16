"""Task classifier (SPEC §7.1 item 2, ADR 0005).

A tiny (T0) model answers the SPEC §7.1 questions with JSON-Schema-constrained output. Without a
usable model, or when its answer does not validate, a deterministic heuristic answers instead
(file types, keywords and repository size) with moderate confidence, so routing works with no
model installed at all. Answers are cached by a hash of the inputs.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from collections.abc import MutableMapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import jsonschema

from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Message, Request, ResponseFormat, Sampling, TextPart
from stepswitch.router.features import CLASSIFIER_SCHEMA, RISKS, TASK_TYPES, TaskFeatures
from stepswitch.workers.base import StepModel

HEURISTIC_CONFIDENCE = 0.5
SKIP_DIRS = {".git", ".stepswitch", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bugfix": ("fix", "bug", "error", "crash", "fails", "failing", "broken", "wrong", "regression"),
    "test": ("add test", "write test", "unit test", "coverage", "test for"),
    "refactor": (
        "refactor",
        "rename",
        "clean up",
        "cleanup",
        "simplify",
        "vectoriz",
        "restructure",
    ),
    "docs": ("docstring", "readme", "document", "comment", "changelog"),
    "analysis": ("explain", "analyse", "analyze", "why does", "investigate", "profile"),
    "feature": ("add", "implement", "support", "create", "new ", "introduce"),
}
_FRONTEND = ("css", "html", "react", "component", "button", "page", "layout", "frontend", "ui ")
_RISKY = ("migration", "delete", "drop table", "security", "auth", "password", "production")
_SCIENCE_DOMAINS: dict[str, tuple[str, ...]] = {
    "crystallography": ("xrd", "diffraction", "lattice", "unit cell", "rietveld", "bragg"),
    "spectroscopy": ("spectrum", "spectra", "raman", "infrared", "baseline", "peak fit"),
    "scattering": ("scattering", "pair distribution", "structure factor", "saxs", "sans"),
    "signal-processing": ("fft", "filter", "convolution", "sampling rate", "wavelet", "noise"),
}
_LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".rb": "ruby",
}


def _repo_profile(repo: Path, limit: int = 4000) -> tuple[str | None, int]:
    """(main language, approximate source size in tokens) from a bounded walk."""
    counts: dict[str, int] = {}
    size = 0
    seen = 0
    for directory, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if seen >= limit:
                break
            seen += 1
            language = _LANGUAGES.get(Path(name).suffix.lower())
            if language:
                counts[language] = counts.get(language, 0) + 1
                with contextlib.suppress(OSError):
                    size += (Path(directory) / name).stat().st_size
        if seen >= limit:
            break
    language = max(sorted(counts), key=lambda k: counts[k]) if counts else None
    return language, size // 4


def heuristic(instruction: str, repo: Path, *, track: str = "general") -> TaskFeatures:
    text = f" {instruction.lower()} "
    task_type = "other"
    for name, words in _KEYWORDS.items():
        if any(word in text for word in words):
            task_type = name
            break
    language, size_tokens = _repo_profile(repo)
    domain = "general"
    if track == "science":
        domain = "science"
        for name, words in _SCIENCE_DOMAINS.items():
            if any(word in text for word in words):
                domain = name
                break
    frontend = any(word in text for word in _FRONTEND)
    if frontend and domain == "general":
        domain = "frontend"
    risk = "high" if any(word in text for word in _RISKY) else "low"
    files_named = len(re.findall(r"[\w./-]+\.\w{1,5}\b", instruction))
    difficulty = 1 if task_type in ("docs",) else 2
    if task_type in ("feature", "refactor") or len(instruction) > 400:
        difficulty += 1
    if size_tokens > 200_000:
        difficulty += 1
    if files_named > 3:
        difficulty += 1
    return TaskFeatures(
        task_type=task_type,
        difficulty=max(1, min(5, difficulty)),
        domain=domain,
        risk=risk,
        est_context_tokens=int(min(size_tokens, 200_000) // 20 + 1500),
        needs_new_code=task_type in ("feature", "test"),
        frontend=frontend,
        confidence=HEURISTIC_CONFIDENCE,
        source="heuristic",
        language=language,
        track=track,
    )


PROMPT = """\
You classify software tasks for a router. Answer with one JSON object and nothing else.
Fields: task_type (one of {types}), difficulty (1 trivial .. 5 very hard), domain (short word,
e.g. general, frontend, backend, data, spectroscopy), risk (one of {risks}), est_context_tokens
(tokens of code the task needs to read), needs_new_code (true if new functions or files are
needed), frontend (true for UI work), confidence (0..1, how sure you are)."""


def _cache_key(instruction: str, track: str, worker: str) -> str:
    """Answers depend on the task text, the track and the classifier, not on the checkout."""
    data = json.dumps([instruction.strip(), track, worker], sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def classify(
    instruction: str,
    repo: Path,
    *,
    track: str = "general",
    offline: bool = False,
    model: StepModel | None = None,
    worker_id: str | None = None,
    cache: MutableMapping[str, dict[str, Any]] | None = None,
) -> tuple[TaskFeatures, dict[str, Any]]:
    """(features, details). Details say which classifier answered and why."""
    base = heuristic(instruction, repo, track=track)
    base = replace(base, offline=offline)
    if model is None:
        return base, {"classifier": "heuristic", "reason": "no classifier model available"}
    key = _cache_key(instruction, track, worker_id or "")
    if cache is not None and key in cache:
        cached = TaskFeatures(**{**cache[key], "source": "cache"})
        return replace(cached, offline=offline), {"classifier": "cache", "key": key}
    request = Request(
        model="",
        system=[TextPart(text=PROMPT.format(types=", ".join(TASK_TYPES), risks=", ".join(RISKS)))],
        messages=[
            Message(
                role="user",
                content=[
                    TextPart(
                        text=f"Track: {track}. Main language: {base.language or 'unknown'}.\n"
                        f"Task: {instruction.strip()}"
                    )
                ],
            )
        ],
        response_format=ResponseFormat(
            type="json_schema", name="task_features", json_schema=CLASSIFIER_SCHEMA, strict=True
        ),
        max_output_tokens=200,
        sampling=Sampling(temperature=0.0, seed=0),
    )
    try:
        completion = model.complete(request)
        text = completion.response.message.text_content().strip()
        data = json.loads(_first_object(text))
        jsonschema.validate(data, CLASSIFIER_SCHEMA)
    except (ModelError, ValueError, jsonschema.ValidationError) as exc:
        return base, {"classifier": "heuristic", "reason": f"model answer unusable: {exc}"}
    features = TaskFeatures(
        **data,
        source=f"model:{worker_id or 'classifier'}",
        language=base.language,
        track=track,
        offline=offline,
    )
    if cache is not None:
        cache[key] = {k: v for k, v in features.as_dict().items() if k != "source"}
    usage = completion.response.usage
    return features, {
        "classifier": "model",
        "worker": worker_id,
        "usage": usage.model_dump(exclude={"raw"}) if usage else None,
    }


def _first_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    return text[start : end + 1]
