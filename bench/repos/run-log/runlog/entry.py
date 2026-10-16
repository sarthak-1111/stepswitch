"""One logged run."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Entry:
    day: date
    distance_km: float
    note: str = ""

    def __post_init__(self) -> None:
        if self.distance_km <= 0:
            raise ValueError("a run has a positive distance")


def parse_line(line: str) -> Entry:
    """Parse a line such as '2024-05-01; 12.5; easy run' (the note is optional)."""
    parts = [part.strip() for part in line.split(";")]
    if len(parts) not in (2, 3):
        raise ValueError(f"expected 2 or 3 fields separated by ';': {line!r}")
    note = parts[2] if len(parts) == 3 else ""
    return Entry(date.fromisoformat(parts[0]), float(parts[1]), note)
