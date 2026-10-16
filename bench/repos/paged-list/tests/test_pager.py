import pytest
from pager import get_page, page_count, page_label

RESULTS = [f"result {n}" for n in range(1, 24)]


def test_first_page_starts_with_the_first_result():
    assert get_page(RESULTS, 1, 10) == RESULTS[:10]


def test_second_page():
    assert get_page(RESULTS, 2, 10) == RESULTS[10:20]


def test_last_page_holds_the_remainder():
    assert get_page(RESULTS, 3, 10) == ["result 21", "result 22", "result 23"]


def test_page_after_the_last_is_empty():
    assert get_page(RESULTS, 4, 10) == []


def test_pages_cover_every_result_once():
    numbers = range(1, page_count(len(RESULTS), 4) + 1)
    assert [item for number in numbers for item in get_page(RESULTS, number, 4)] == RESULTS


@pytest.mark.parametrize(("number", "per_page"), [(0, 10), (-1, 10), (1, 0)])
def test_invalid_arguments(number, per_page):
    with pytest.raises(ValueError):
        get_page(RESULTS, number, per_page)


def test_page_count():
    assert page_count(23, 10) == 3
    assert page_count(20, 10) == 2
    assert page_count(0, 10) == 0


def test_page_label():
    assert page_label(2, 23, 10) == "Page 2 of 3"
    assert page_label(1, 0, 10) == "Page 1 of 1"
