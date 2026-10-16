"""Time entries."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Entry:
    day: date
    project: str
    minutes: int
    note: str = ""
