"""Context pruning (SPEC §13 TK6).

Two mechanisms keep a task's transcript small:

* **Superseded results.** A tool result that a later result makes obsolete is replaced by a
  one-line stub: a read covered by a later read of the same file, an edit's report covered by a
  later full read of that file, a test or command run again with the same arguments, a search,
  grep or map repeated with the same arguments. The newest results are never touched. Changing
  an earlier message invalidates the servers' prefix caches from that message on, so stubs are
  applied in batches, only when they save at least `PRUNE_MIN_TOKENS`.
* **Rolling summary.** When a prompt reaches `COMPACT_AT` (60%) of its context budget, the
  older turns are replaced by a HANDOFF brief (`context.handoff`) and the latest exchange is
  kept verbatim (the agent does this; `should_compact` decides when).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

COMPACT_AT = 0.6
PRUNE_MIN_TOKENS = 800
KEEP_RECENT = 2
REPEATABLE = frozenset({"test", "run", "grep", "search", "repo_map", "lsp"})


@dataclass
class ResultRecord:
    """One tool result in the transcript."""

    index: int  # message index in the transcript
    step: int
    tool: str
    arguments: str  # canonical JSON
    tokens: int
    path: str | None = None
    start: int | None = None
    end: int | None = None
    total_lines: int | None = None
    pruned: bool = False
    is_error: bool = False


def _covers(later: ResultRecord, earlier: ResultRecord) -> bool:
    if later.start is None or later.end is None or earlier.start is None or earlier.end is None:
        return False
    return later.start <= earlier.start and later.end >= earlier.end


def _whole(record: ResultRecord) -> bool:
    return record.start == 1 and record.total_lines is not None and record.end == record.total_lines


def superseded(
    records: Sequence[ResultRecord], *, keep_recent: int = KEEP_RECENT
) -> list[tuple[ResultRecord, str]]:
    """(record, stub) for every unpruned result that a later result makes obsolete."""
    live = [r for r in records if not r.pruned]
    protected = {r.index for r in live[-keep_recent:]} if keep_recent else set()
    out: list[tuple[ResultRecord, str]] = []
    for i, earlier in enumerate(live):
        if earlier.index in protected:
            continue
        for later in live[i + 1 :]:
            stub = _stub(earlier, later)
            if stub is not None:
                out.append((earlier, stub))
                break
    return out


def _stub(earlier: ResultRecord, later: ResultRecord) -> str | None:
    same_file = earlier.path is not None and earlier.path == later.path and not later.is_error
    if earlier.tool == "read" and later.tool == "read" and same_file and _covers(later, earlier):
        return (
            f"[superseded: {earlier.path} was read again in step {later.step}; this older"
            " result was removed to save tokens]"
        )
    if earlier.tool == "edit" and later.tool == "read" and same_file and _whole(later):
        return (
            f"[superseded: {earlier.path} was read in full in step {later.step}, after this"
            " edit; the edit report was removed to save tokens]"
        )
    repeated = later.tool == earlier.tool and later.arguments == earlier.arguments
    if earlier.tool in REPEATABLE and repeated:
        return (
            f"[superseded: {earlier.tool} ran again with the same arguments in step"
            f" {later.step}; this older result was removed to save tokens]"
        )
    return None


def plan(
    records: Sequence[ResultRecord],
    *,
    min_tokens: int = PRUNE_MIN_TOKENS,
    keep_recent: int = KEEP_RECENT,
) -> list[tuple[ResultRecord, str]]:
    """The stubs to apply now: all of them once they save `min_tokens`, else none."""
    found = superseded(records, keep_recent=keep_recent)
    saved = sum(max(0, record.tokens - len(stub) // 4) for record, stub in found)
    return found if found and saved >= min_tokens else []


def should_compact(prompt_tokens: int, budget: int | None, *, at: float = COMPACT_AT) -> bool:
    return budget is not None and budget > 0 and prompt_tokens >= at * budget
