"""A whole training log."""

from runlog.entry import Entry, parse_line


def load(text: str) -> list[Entry]:
    """The entries in a log; blank lines and lines starting with '#' are skipped."""
    entries = []
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            entries.append(parse_line(line))
    return entries


def total_km(entries: list[Entry]) -> float:
    return sum(entry.distance_km for entry in entries)


def longest(entries: list[Entry]) -> Entry | None:
    return max(entries, key=lambda entry: entry.distance_km, default=None)
