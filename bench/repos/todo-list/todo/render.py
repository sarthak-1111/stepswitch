"""Show tasks as text."""

from collections.abc import Iterable

from todo.model import Task


def render(task: Task) -> str:
    """A line such as '[ ] Water the plants' or '[x] Pay rent'."""
    box = "x" if task.done else " "
    return f"[{box}] {task.title}"


def render_all(tasks: Iterable[Task]) -> str:
    """Open tasks first, then finished ones, each group in its original order."""
    ordered = sorted(tasks, key=lambda task: task.done)
    return "\n".join(render(task) for task in ordered)
