from datetime import date

from billing.schedule import due_dates, next_due


def test_due_dates_in_the_middle_of_the_month():
    assert due_dates(date(2024, 1, 15), 3) == [
        date(2024, 1, 15),
        date(2024, 2, 15),
        date(2024, 3, 15),
    ]


def test_due_dates_of_a_plan_that_starts_on_the_31st():
    assert due_dates(date(2024, 1, 31), 5) == [
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 31),
        date(2024, 4, 30),
        date(2024, 5, 31),
    ]


def test_due_dates_from_the_30th_across_the_new_year():
    assert due_dates(date(2023, 11, 30), 4) == [
        date(2023, 11, 30),
        date(2023, 12, 30),
        date(2024, 1, 30),
        date(2024, 2, 29),
    ]


def test_no_due_dates():
    assert due_dates(date(2024, 1, 31), 0) == []


def test_next_due_in_the_middle_of_the_month():
    assert next_due(date(2024, 1, 15), date(2024, 3, 20)) == date(2024, 4, 15)


def test_next_due_on_a_due_date_is_the_following_one():
    assert next_due(date(2024, 1, 15), date(2024, 2, 15)) == date(2024, 3, 15)


def test_next_due_after_a_short_month():
    assert next_due(date(2024, 1, 31), date(2024, 3, 1)) == date(2024, 3, 31)


def test_next_due_before_the_plan_starts():
    assert next_due(date(2024, 1, 31), date(2023, 12, 25)) == date(2024, 1, 31)
