"""The bench's canary check (ADR 0037).

Before the first task, `ssw bench run` sends every local worker a contestant uses one request
that asks for a single word. The request goes through the worker's own client: same endpoint,
format, tool protocol and context (`num_ctx`), so the server sees what the tasks will send.
A worker that answers anything else, answers nothing or fails stops the bench. Without the
check, a model that had stopped working would pass for a weak one: in Phase 11 a model that
answered nothing reviewed every change.

The manifest records the check (`run.json` `preflight`): whether every worker passed, and for
each worker the seconds, the start of its reply or the error. The claims that compare
contestants on local models accept only runs whose check passed.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from stepswitch.compat.errors import ModelError
from stepswitch.compat.ir import Message, Request

WORD = "READY"
PROMPT = f"Reply with the single word {WORD} and nothing else."
# Reasoning models spend part of the budget thinking before they answer.
MAX_OUTPUT_TOKENS = 512
REPLY_CHARS = 60


class PreflightFailed(RuntimeError):
    """A worker failed the canary check; the bench stopped before its first task."""


def healthy(text: str) -> bool:
    """The reply contains the canary word as a word (case, punctuation and markup aside)."""
    return WORD in re.findall(r"[A-Z]+", text.upper())


def check_worker(worker: Any, *, clock: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    """Ask one worker for the canary word; the record of its answer."""
    request = Request(
        model=worker.spec.model,
        messages=[Message.text("user", PROMPT)],
        max_output_tokens=MAX_OUTPUT_TOKENS,
        effort="low" if worker.spec.reasoning else None,
    )
    started = clock()
    try:
        completion = worker.get_model().complete(request)
    except ModelError as exc:
        return {
            "ok": False,
            "seconds": round(clock() - started, 1),
            "error": f"{exc.kind}: {exc.message[:200]}",
        }
    text = completion.response.message.text_content().strip()
    return {
        "ok": healthy(text),
        "seconds": round(clock() - started, 1),
        "reply": text[:REPLY_CHARS],
    }


def check_workers(catalog: Any, worker_ids: Sequence[str]) -> dict[str, Any]:
    """Check each worker once, in order; `ok` is True when every one passed."""
    workers = {wid: check_worker(catalog.get(wid)) for wid in dict.fromkeys(worker_ids)}
    return {
        "ok": all(record["ok"] for record in workers.values()),
        "prompt": PROMPT,
        "workers": workers,
    }


def failure_message(report: dict[str, Any]) -> str:
    failed = [
        f"{wid} ({record.get('error') or repr(record.get('reply', ''))})"
        for wid, record in report.get("workers", {}).items()
        if not record.get("ok")
    ]
    return (
        "the canary check failed, so no task was run: "
        + "; ".join(failed)
        + f". Each worker was asked: {PROMPT!r}. Check the model server (a context size the"
        " model cannot use can make it answer garbage), or run with --no-preflight."
    )
