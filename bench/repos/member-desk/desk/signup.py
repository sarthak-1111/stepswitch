"""Sign-up: add a member to the register."""

from __future__ import annotations

from desk.members import Register


def _clean_email(email: str) -> str:
    return email.strip().lower()


def sign_up(register: Register, name: str, email: str) -> str:
    """Add a member and return the address they are filed under."""
    key = _clean_email(email)
    if "@" not in key:
        raise ValueError(f"not an e-mail address: {email!r}")
    if key in register.members:
        raise ValueError(f"{key} is already a member")
    register.members[key] = name.strip()
    return key
