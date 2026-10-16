"""Products kept in the stock room."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    sku: str
    name: str
    quantity: int
    reorder_level: int
    max_level: int

    def __post_init__(self) -> None:
        if self.quantity < 0:
            raise ValueError(f"{self.sku}: quantity cannot be negative")
        if self.reorder_level > self.max_level:
            raise ValueError(f"{self.sku}: reorder level is above the maximum level")
