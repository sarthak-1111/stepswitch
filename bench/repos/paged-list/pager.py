"""Split a list of search results into numbered pages."""

from collections.abc import Sequence


def page_count(total: int, per_page: int) -> int:
    """Return how many pages are needed to show `total` results."""
    if per_page < 1:
        raise ValueError("per_page must be at least 1")
    return -(-total // per_page)


def get_page(results: Sequence[str], number: int, per_page: int) -> list[str]:
    """Return the results shown on page `number`."""
    if number < 1:
        raise ValueError("page numbers start at 1")
    if per_page < 1:
        raise ValueError("per_page must be at least 1")
    start = number * per_page
    return list(results[start : start + per_page])


def page_label(number: int, total: int, per_page: int) -> str:
    """Return a label such as 'Page 2 of 5'; an empty result list still has one page."""
    pages = max(page_count(total, per_page), 1)
    return f"Page {number} of {pages}"
