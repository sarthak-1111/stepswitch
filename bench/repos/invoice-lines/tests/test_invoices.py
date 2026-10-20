import pytest
from invoices.invoice import format_cents, invoice_total
from invoices.lines import Line, line_total


def test_a_line_without_a_discount() -> None:
    assert line_total(Line("pen", 250, 4)) == 1000


def test_discounted_totals_round_half_up_to_the_cent() -> None:
    assert line_total(Line("lamp", 999, 1, 15)) == 849  # 849.15 cents
    assert line_total(Line("cups", 333, 3, 10)) == 899  # 899.1 cents
    assert line_total(Line("clip", 3, 1, 50)) == 2  # 1.5 cents
    assert line_total(Line("tape", 125, 2, 20)) == 200


def test_invoice_total_and_format() -> None:
    lines = [Line("lamp", 999, 1, 15), Line("pen", 250, 4)]
    assert invoice_total(lines) == 1849
    assert format_cents(invoice_total(lines)) == "18.49"
    assert format_cents(5) == "0.05"


def test_bad_lines_are_refused() -> None:
    with pytest.raises(ValueError):
        Line("pen", 250, 0)
    with pytest.raises(ValueError):
        Line("pen", 250, 1, 120)
