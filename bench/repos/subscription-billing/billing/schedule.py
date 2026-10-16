"""Due dates of a monthly plan."""

from datetime import date

from billing.dates import add_months


def due_dates(start: date, count: int) -> list[date]:
    """The first `count` due dates of a plan that starts on `start`."""
    dates = []
    due = start
    for _ in range(count):
        dates.append(due)
        due = add_months(due, 1)
    return dates


def next_due(start: date, after: date) -> date:
    """The first due date of the plan that falls after `after`."""
    due = start
    while due <= after:
        due = add_months(due, 1)
    return due
