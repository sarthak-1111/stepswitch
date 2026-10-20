"""Invoices: their total and how amounts are shown."""

from __future__ import annotations

from collections.abc import Iterable

from invoices.lines import Line, line_total


def invoice_total(lines: Iterable[Line]) -> int:
    """The sum of the line totals, in cents."""
    return sum(line_total(line) for line in lines)


def format_cents(cents: int) -> str:
    """Cents as a decimal amount: 1849 becomes "18.49"."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"
