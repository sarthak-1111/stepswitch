"""Blog posts."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Post:
    title: str
    published: date
    body: str

    def word_count(self) -> int:
        return len(self.body.split())

    def reading_minutes(self, words_per_minute: int = 200) -> int:
        """Whole minutes needed to read the post, at least one."""
        return max(1, -(-self.word_count() // words_per_minute))
