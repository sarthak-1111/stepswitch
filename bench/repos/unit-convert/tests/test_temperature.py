import pytest
from units.convert import convert, describe, kind_of


@pytest.mark.parametrize(
    ("value", "source", "target", "expected"),
    [
        (100, "C", "F", 212),
        (32, "F", "C", 0),
        (-40, "C", "F", -40),
        (0, "C", "K", 273.15),
        (0, "K", "C", -273.15),
        (300, "K", "F", 80.33),
        (98.6, "F", "C", 37),
        (20, "C", "C", 20),
    ],
)
def test_temperature_conversions(value, source, target, expected):
    assert convert(value, source, target) == pytest.approx(expected)


def test_temperature_is_a_kind():
    assert kind_of("C") == kind_of("F") == kind_of("K") == "temperature"


@pytest.mark.parametrize(("source", "target"), [("C", "m"), ("kg", "K"), ("F", "l")])
def test_temperatures_do_not_mix_with_other_kinds(source, target):
    with pytest.raises(ValueError):
        convert(10, source, target)


def test_describe_a_temperature():
    assert describe(100, "C", "F") == "100 C = 212 F"
