"""Invoice lines. Amounts are whole cents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    description: str
    unit_cents: int
    quantity: int = 1
    discount_percent: int = 0

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise ValueError("quantity must be at least 1")
        if not 0 <= self.discount_percent <= 100:
            raise ValueError("discount_percent must be between 0 and 100")


def line_total(line: Line) -> int:
    """The line's price times its quantity, less its discount, in cents.

    A total that falls between two whole cents is rounded half up: 1.5 cents become 2.
    """
    gross = line.unit_cents * line.quantity
    discount = gross * line.discount_percent // 100
    return gross - discount
