from datetime import date

import pytest
from runlog import entry
from runlog.entry import Entry


def test_from_line():
    run = Entry.from_line("2024-05-01; 12.5; easy run")
    assert run == Entry(date(2024, 5, 1), 12.5, "easy run")


def test_from_line_without_a_note():
    assert Entry.from_line("2024-05-03;8") == Entry(date(2024, 5, 3), 8.0)


@pytest.mark.parametrize(
    "line", ["2024-05-01", "2024-05-01;5;a;b", "yesterday;5", "2024-05-01;far", "2024-05-01;-3"]
)
def test_bad_lines_are_rejected(line):
    with pytest.raises(ValueError):
        Entry.from_line(line)


def test_from_line_builds_subclasses():
    class Race(Entry):
        pass

    assert type(Race.from_line("2024-06-09; 21.1; half marathon")) is Race


def test_the_module_function_is_gone():
    assert not hasattr(entry, "parse_line")
