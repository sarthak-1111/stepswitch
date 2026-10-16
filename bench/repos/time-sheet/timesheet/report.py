"""Day-by-day report."""

from collections.abc import Iterable

from timesheet.entries import Entry
from timesheet.fmt import fmt_dur


def report_lines(entries: Iterable[Entry]) -> list[str]:
    """One line per entry, in date order."""
    lines = []
    for entry in sorted(entries, key=lambda item: item.day):
        line = f"{entry.day.isoformat()}  {entry.project:<10} {fmt_dur(entry.minutes):>6}"
        lines.append(f"{line}  {entry.note}".rstrip())
    return lines
