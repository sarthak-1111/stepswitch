from pathlib import Path

from contacts.importer import emails, read_contacts
from contacts.model import Contact


def write(tmp_path: Path, text: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / "contacts.csv"
    path.write_text(text, encoding=encoding)
    return path


def test_plain_rows_and_blank_lines(tmp_path: Path) -> None:
    text = "name,email,phone\nAnn Lee,ann@example.com,555-0100\n\nBo Kim,bo@example.com,\n"
    assert read_contacts(write(tmp_path, text)) == [
        Contact("Ann Lee", "ann@example.com", "555-0100"),
        Contact("Bo Kim", "bo@example.com", ""),
    ]


def test_quoted_fields_keep_their_commas_and_quotes(tmp_path: Path) -> None:
    text = 'name,email,phone\n"Lee, Ann",ann@example.com,555-0100\n"Kim ""Bo""",bo@example.com,\n'
    assert read_contacts(write(tmp_path, text)) == [
        Contact("Lee, Ann", "ann@example.com", "555-0100"),
        Contact('Kim "Bo"', "bo@example.com", ""),
    ]


def test_exports_that_start_with_a_byte_order_mark(tmp_path: Path) -> None:
    text = "name,email,phone\nAnn Lee,ann@example.com,555-0100\n"
    path = write(tmp_path, text, encoding="utf-8-sig")
    assert read_contacts(path) == [Contact("Ann Lee", "ann@example.com", "555-0100")]


def test_emails_without_duplicates() -> None:
    people = [
        Contact("A", "a@example.com"),
        Contact("B", "b@example.com"),
        Contact("A2", "a@example.com"),
    ]
    assert emails(people) == ["a@example.com", "b@example.com"]
