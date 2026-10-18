"""Read dedup (TK3): an unchanged range that is still visible is answered with a stub."""

from __future__ import annotations

from stepswitch.context.dedup import ReadCache, read_key, stub

READ = {"path": "a.py", "sha256": "0" * 64, "start": 1, "end": 40}


def test_identical_visible_reads_hit() -> None:
    cache = ReadCache()
    assert cache.lookup(READ, lambda _i: True) is None
    cache.remember(READ, index=5, step=3)
    assert cache.lookup(READ, lambda _i: True) == 3
    assert cache.lookup(READ, lambda index: index != 5) is None  # pruned since


def test_changed_content_or_range_misses() -> None:
    cache = ReadCache()
    cache.remember(READ, index=5, step=3)
    assert cache.lookup({**READ, "sha256": "1" * 64}, lambda _i: True) is None
    assert cache.lookup({**READ, "end": 41}, lambda _i: True) is None
    cache.reset()
    assert cache.lookup(READ, lambda _i: True) is None


def test_directories_and_errors_have_no_key() -> None:
    assert read_key({"path": "src", "directory": True}) is None
    assert read_key({}) is None


def test_stub_points_to_the_earlier_step() -> None:
    text = stub(READ, 3)
    assert "a.py lines 1-40" in text and "step 3" in text


def test_a_stub_is_served_once_per_range_and_content() -> None:
    cache = ReadCache()
    cache.remember(READ, index=5, step=3)
    assert cache.lookup(READ, lambda _i: True) == 3
    cache.mark_stubbed(READ)
    assert cache.lookup(READ, lambda _i: True) is None
    cache.remember(READ, index=9, step=6)
    assert cache.lookup(READ, lambda _i: True) is None  # still shown in full
    changed = {**READ, "sha256": "2" * 64}
    cache.remember(changed, index=12, step=8)
    assert cache.lookup(changed, lambda _i: True) == 8  # new content: a stub again
    assert "anchors" in stub(READ, 3)
