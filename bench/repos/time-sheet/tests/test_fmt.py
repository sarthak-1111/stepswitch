import pytest
from timesheet import fmt
from timesheet.fmt import fmt_money, format_duration


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(0, "0:00"), (5, "0:05"), (60, "1:00"), (95, "1:35"), (600, "10:00")],
)
def test_format_duration(minutes, text):
    assert format_duration(minutes) == text


def test_a_negative_duration_is_rejected():
    with pytest.raises(ValueError):
        format_duration(-1)


def test_the_old_name_is_gone():
    assert not hasattr(fmt, "fmt_dur")


def test_fmt_money():
    assert fmt_money(1234.5) == "1,234.50 EUR"
