"""Read contacts from a CSV export with the header `name,email,phone`."""

from __future__ import annotations

from pathlib import Path

from contacts.model import Contact


def read_contacts(path: Path) -> list[Contact]:
    """The contacts in the file, in file order. Blank lines are skipped."""
    rows = path.read_text(encoding="utf-8").splitlines()
    header = [field.strip() for field in rows[0].split(",")]
    contacts = []
    for row in rows[1:]:
        if not row.strip():
            continue
        values = dict(zip(header, (value.strip() for value in row.split(",")), strict=False))
        contacts.append(
            Contact(name=values["name"], email=values["email"], phone=values.get("phone", ""))
        )
    return contacts


def emails(contacts: list[Contact]) -> list[str]:
    """The contacts' e-mail addresses, without duplicates, in their first order."""
    seen: dict[str, None] = {}
    for contact in contacts:
        seen.setdefault(contact.email, None)
    return list(seen)
