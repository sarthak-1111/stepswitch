"""Times of day on a 24-hour clock, written as 'H:MM'."""

MINUTES_PER_DAY = 24 * 60


def to_minutes(clock: str) -> int:
    """Minutes after midnight, e.g. '9:30' -> 570."""
    hours, _, minutes = clock.partition(":")
    if not hours.isdigit() or len(minutes) != 2 or not minutes.isdigit():
        raise ValueError(f"not a time: {clock!r}")
    value = int(hours) * 60 + int(minutes)
    if int(minutes) >= 60 or value >= MINUTES_PER_DAY:
        raise ValueError(f"not a time: {clock!r}")
    return value


def from_minutes(value: int) -> str:
    """The clock time `value` minutes after midnight, e.g. 570 -> '9:30'."""
    if not 0 <= value < MINUTES_PER_DAY:
        raise ValueError(f"outside the day: {value}")
    hours, minutes = divmod(value, 60)
    return f"{hours}:{minutes:02d}"
