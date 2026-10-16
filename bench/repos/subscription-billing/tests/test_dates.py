from datetime import date

import pytest
from billing.dates import add_months


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2024, 1, 15), 1, date(2024, 2, 15)),
        (date(2024, 1, 10), -1, date(2023, 12, 10)),
        (date(2024, 5, 31), 12, date(2025, 5, 31)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2023, 1, 31), 1, date(2023, 2, 28)),
        (date(2024, 3, 31), 1, date(2024, 4, 30)),
        (date(2024, 3, 31), -1, date(2024, 2, 29)),
        (date(2024, 11, 30), 3, date(2025, 2, 28)),
    ],
)
def test_add_months(day, months, expected):
    assert add_months(day, months) == expected
