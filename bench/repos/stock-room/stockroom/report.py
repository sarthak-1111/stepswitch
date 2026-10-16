"""Reports on stock levels."""

from collections.abc import Iterable

from stockroom.items import Item


def needs_restock(item: Item) -> bool:
    """Whether the item belongs on the next purchase order."""
    return item.quantity < item.reorder_level


def is_overstocked(item: Item) -> bool:
    """Whether the item holds more than its maximum level."""
    return item.quantity > item.max_level


def restock_report(items: Iterable[Item]) -> list[str]:
    """One line per item to reorder, lowest quantity first."""
    low = sorted((item for item in items if needs_restock(item)), key=lambda item: item.quantity)
    return [
        f"{item.sku} {item.name}: {item.quantity} left (reorder level {item.reorder_level})"
        for item in low
    ]


def overstock_report(items: Iterable[Item]) -> list[str]:
    """One line per item above its maximum level."""
    return [
        f"{item.sku} {item.name}: {item.quantity - item.max_level} over"
        for item in items
        if is_overstocked(item)
    ]
