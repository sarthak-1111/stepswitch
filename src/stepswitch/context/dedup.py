"""Read dedup (SPEC §13 TK3).

Re-reading a range of a file whose content has not changed, while the earlier result is still
in the transcript, returns a short stub that points to that result. Content is compared by the
file's sha256 (the `read` tool reports it), so an edit, or any other change, makes the next read
a full one. When the earlier result has been pruned or replaced by a brief (TK6), the read is
shown in full again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

ReadKey = tuple[str, str, int, int]


def read_key(data: Mapping[str, Any]) -> ReadKey | None:
    """(path, sha256, start, end) of a `read` result, or None for a directory or an error."""
    try:
        return (str(data["path"]), str(data["sha256"]), int(data["start"]), int(data["end"]))
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class ReadCache:
    # key -> (transcript index of the result, step that produced it)
    seen: dict[ReadKey, tuple[int, int]] = field(default_factory=dict)

    def lookup(self, data: Mapping[str, Any], visible: Callable[[int], bool]) -> int | None:
        """The step of an identical earlier result that is still visible, or None."""
        key = read_key(data)
        if key is None or key not in self.seen:
            return None
        index, step = self.seen[key]
        return step if visible(index) else None

    def remember(self, data: Mapping[str, Any], index: int, step: int) -> None:
        key = read_key(data)
        if key is not None:
            self.seen[key] = (index, step)

    def reset(self) -> None:
        self.seen.clear()


def stub(data: Mapping[str, Any], step: int) -> str:
    return (
        f"[{data.get('path')} lines {data.get('start')}-{data.get('end')} are unchanged since"
        f" the read in step {step}; that result above still applies. Edit with its anchors, or"
        " read another range.]"
    )
