import pytest
from planner.clock import from_minutes, to_minutes


def test_to_minutes():
    assert to_minutes("9:30") == 570
    assert to_minutes("13:05") == 785


def test_round_trip():
    for text in ["0:00", "7:05", "9:30", "23:59"]:
        assert from_minutes(to_minutes(text)) == text


@pytest.mark.parametrize("text", ["24:00", "9:60", "9", "nine:30", "9:5"])
def test_invalid_times(text):
    with pytest.raises(ValueError):
        to_minutes(text)
