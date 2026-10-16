import pytest
from stockroom.items import Item
from stockroom.report import is_overstocked, needs_restock, overstock_report, restock_report


def item(sku, quantity, reorder_level=5, max_level=40, name="part"):
    return Item(sku, name, quantity, reorder_level, max_level)


def test_below_the_reorder_level_needs_restock():
    assert needs_restock(item("A1", 2))


def test_at_the_reorder_level_needs_restock():
    assert needs_restock(item("A1", 5))


def test_above_the_reorder_level_does_not():
    assert not needs_restock(item("A1", 6))


def test_restock_report_lists_the_lowest_first():
    items = [
        item("B2", 5, name="bolts"),
        item("N1", 9, name="nuts"),
        item("S3", 1, name="screws"),
        item("W4", 0, reorder_level=0, name="washers"),
    ]
    assert restock_report(items) == [
        "W4 washers: 0 left (reorder level 0)",
        "S3 screws: 1 left (reorder level 5)",
        "B2 bolts: 5 left (reorder level 5)",
    ]


def test_restock_report_is_empty_when_everything_is_stocked():
    assert restock_report([item("N1", 9), item("N2", 30)]) == []


def test_overstock():
    assert is_overstocked(item("C1", 41))
    assert not is_overstocked(item("C1", 40))
    assert overstock_report([item("C1", 45, name="clips"), item("C2", 10)]) == ["C1 clips: 5 over"]


def test_negative_quantity_is_rejected():
    with pytest.raises(ValueError):
        item("X1", -1)
