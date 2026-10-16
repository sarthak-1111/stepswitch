"""To-do tasks."""

from dataclasses import dataclass


@dataclass
class Task:
    title: str
    done: bool = False

    def __post_init__(self) -> None:
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("a task needs a title")

    def complete(self) -> None:
        self.done = True
