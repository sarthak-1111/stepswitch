from datetime import date

import pytest
from runlog.log import load, longest, total_km

LOG = """\
# May
2024-05-01; 12.5; easy run

2024-05-03; 8
2024-05-05; 21.1; long run
"""


def test_load():
    entries = load(LOG)
    assert [entry.day for entry in entries] == [
        date(2024, 5, 1),
        date(2024, 5, 3),
        date(2024, 5, 5),
    ]
    assert entries[0].note == "easy run"


def test_total_and_longest():
    entries = load(LOG)
    assert total_km(entries) == pytest.approx(41.6)
    assert longest(entries).note == "long run"


def test_an_empty_log():
    assert load("") == []
    assert longest([]) is None


def test_a_bad_line_is_reported():
    with pytest.raises(ValueError):
        load("2024-05-01; fast")
