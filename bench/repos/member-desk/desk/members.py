"""The member register: members' names by their e-mail address."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Register:
    members: dict[str, str] = field(default_factory=dict)  # e-mail address -> name

    def count(self) -> int:
        return len(self.members)
