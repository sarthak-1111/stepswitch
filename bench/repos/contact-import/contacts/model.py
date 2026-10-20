"""A contact from an address-book export."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Contact:
    name: str
    email: str
    phone: str = ""
