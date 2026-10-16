"""Turn a day's events into an agenda."""

from collections.abc import Iterable
from dataclasses import dataclass

from planner.clock import from_minutes, to_minutes


@dataclass(frozen=True)
class Event:
    title: str
    start: str
    minutes: int


def end_of(event: Event) -> str:
    """The clock time at which the event ends."""
    return from_minutes(to_minutes(event.start) + event.minutes)


def agenda(events: Iterable[Event]) -> list[str]:
    """One line per event, such as '9:30-10:00 Stand-up'."""
    ordered = sorted(events, key=lambda event: event.start)
    return [f"{event.start}-{end_of(event)} {event.title}" for event in ordered]


def clashes(first: Event, second: Event) -> bool:
    """Whether two events overlap in time."""
    first_start, second_start = to_minutes(first.start), to_minutes(second.start)
    return (
        first_start < second_start + second.minutes and second_start < first_start + first.minutes
    )
