import pytest
from units.convert import convert, describe, kind_of


@pytest.mark.parametrize(
    ("value", "source", "target", "expected"),
    [
        (1, "km", "m", 1000),
        (12, "in", "ft", 1),
        (1, "mi", "km", 1.609344),
        (1, "lb", "oz", 16),
        (2, "cup", "ml", 473.176473),
        (5, "m", "m", 5),
    ],
)
def test_convert(value, source, target, expected):
    assert convert(value, source, target) == pytest.approx(expected)


def test_kind_of():
    assert kind_of("cm") == "length"
    assert kind_of("oz") == "mass"
    assert kind_of("l") == "volume"


def test_unknown_unit():
    with pytest.raises(ValueError, match="unknown unit"):
        convert(1, "league", "m")


def test_kinds_do_not_mix():
    with pytest.raises(ValueError):
        convert(1, "kg", "m")


def test_describe():
    assert describe(5, "km", "mi") == "5 km = 3.10686 mi"
