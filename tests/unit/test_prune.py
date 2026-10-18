"""Context pruning (TK6): superseded results become stubs, in batches; compaction at 60%."""

from __future__ import annotations

from stepswitch.context.prune import ResultRecord, plan, should_compact, superseded


def record(index: int, tool: str, **fields: object) -> ResultRecord:
    base: dict[str, object] = {
        "index": index,
        "step": index,
        "tool": tool,
        "arguments": "{}",
        "tokens": 500,
    }
    base.update(fields)
    return ResultRecord(**base)  # type: ignore[arg-type]


def test_a_covering_read_supersedes_an_older_read() -> None:
    records = [
        record(1, "read", path="a.py", start=1, end=40, total_lines=80),
        record(2, "read", path="a.py", start=1, end=80, total_lines=80),
        record(3, "read", path="b.py", start=1, end=10),
        record(4, "test", arguments='{"selector": "x"}'),
        record(5, "test", arguments='{"selector": "x"}'),
    ]
    found = superseded(records, keep_recent=0)
    assert [(r.index, "read again in step 2" in s) for r, s in found[:1]] == [(1, True)]
    assert [r.index for r, _ in found] == [1, 4]


def test_partial_reads_and_other_files_are_kept() -> None:
    records = [
        record(1, "read", path="a.py", start=1, end=80),
        record(2, "read", path="a.py", start=20, end=40),
        record(3, "read", path="c.py", start=1, end=80),
    ]
    assert superseded(records, keep_recent=0) == []


def test_an_edit_report_is_superseded_by_a_full_read_only() -> None:
    records = [
        record(1, "edit", path="a.py"),
        record(2, "read", path="a.py", start=1, end=30, total_lines=80),
        record(3, "read", path="a.py", start=1, end=80, total_lines=80),
    ]
    found = superseded(records, keep_recent=0)
    assert [r.index for r, _ in found] == [1, 2]
    assert "edit report was removed" in found[0][1]


def test_failed_reads_supersede_nothing_and_recent_results_are_protected() -> None:
    records = [
        record(1, "read", path="a.py", start=1, end=10),
        record(2, "read", path="a.py", start=1, end=10, is_error=True),
    ]
    assert superseded(records, keep_recent=0) == []
    runs = [record(i, "run", arguments='{"command": "make"}') for i in range(1, 4)]
    assert [r.index for r, _ in superseded(runs, keep_recent=3)] == []
    assert [r.index for r, _ in superseded(runs, keep_recent=2)] == [1]
    assert [r.index for r, _ in superseded(runs, keep_recent=0)] == [1, 2]


def test_stubs_are_applied_in_batches_worth_the_cache_miss() -> None:
    small = [record(1, "grep", tokens=100), record(2, "grep", tokens=100), record(3, "x")]
    assert plan(small, min_tokens=800, keep_recent=0) == []
    big = [record(1, "grep", tokens=2000), record(2, "grep", tokens=100)]
    assert [r.index for r, _ in plan(big, min_tokens=800, keep_recent=0)] == [1]
    pruned = [record(1, "grep", tokens=2000, pruned=True), record(2, "grep")]
    assert plan(pruned, min_tokens=1, keep_recent=0) == []


def test_compaction_starts_at_sixty_percent_of_the_budget() -> None:
    assert not should_compact(5999, 10000)
    assert should_compact(6000, 10000)
    assert not should_compact(10**6, None)
