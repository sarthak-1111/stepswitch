"""Totals per project."""

from collections import defaultdict
from collections.abc import Iterable

from timesheet.entries import Entry
from timesheet.fmt import fmt_dur, fmt_money


def minutes_per_project(entries: Iterable[Entry]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for entry in entries:
        totals[entry.project] += entry.minutes
    return dict(sorted(totals.items()))


def summary_lines(entries: Iterable[Entry], hourly_rate: float) -> list[str]:
    """One line per project with its total time and what that time costs."""
    lines = []
    for project, minutes in minutes_per_project(entries).items():
        cost = fmt_money(minutes / 60 * hourly_rate)
        lines.append(f"{project}: {fmt_dur(minutes)} ({cost})")
    return lines
