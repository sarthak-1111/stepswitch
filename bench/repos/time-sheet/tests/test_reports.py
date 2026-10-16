from datetime import date

from timesheet.entries import Entry
from timesheet.report import report_lines
from timesheet.summary import summary_lines

ENTRIES = [
    Entry(date(2024, 3, 5), "garden", 95, "planting"),
    Entry(date(2024, 3, 4), "website", 30),
    Entry(date(2024, 3, 6), "website", 120),
]


def test_report_lines():
    assert report_lines(ENTRIES) == [
        "2024-03-04  website      0:30",
        "2024-03-05  garden       1:35  planting",
        "2024-03-06  website      2:00",
    ]


def test_summary_lines():
    assert summary_lines(ENTRIES, hourly_rate=40) == [
        "garden: 1:35 (63.33 EUR)",
        "website: 2:30 (100.00 EUR)",
    ]
