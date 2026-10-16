"""The note record."""

from dataclasses import dataclass, field
from typing import Any, Self


@dataclass
class Note:
    title: str
    body: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "body": self.body, "tags": list(self.tags)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(data["title"], data.get("body", ""), list(data.get("tags", [])))
