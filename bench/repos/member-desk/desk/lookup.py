"""Look members up by e-mail address."""

from __future__ import annotations

from desk.members import Register


def _canonical(address: str) -> str:
    return address.lower()


def find_member(register: Register, email: str) -> str | None:
    """The member's name, or None when the address is not registered."""
    return register.members.get(_canonical(email))


def is_member(register: Register, email: str) -> bool:
    return find_member(register, email) is not None
