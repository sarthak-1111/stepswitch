"""Calendar arithmetic."""

from datetime import date


def add_months(day: date, months: int) -> date:
    """The date `months` months after `day`, or before it when `months` is negative."""
    index = day.month - 1 + months
    return day.replace(year=day.year + index // 12, month=index % 12 + 1)
