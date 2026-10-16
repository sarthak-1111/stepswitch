"""Keep tasks in a JSON file."""

import json
from pathlib import Path
from typing import Any

from todo.model import Task


def to_dict(task: Task) -> dict[str, Any]:
    return {"title": task.title, "done": task.done}


def from_dict(data: dict[str, Any]) -> Task:
    return Task(title=data["title"], done=data.get("done", False))


def save(path: Path, tasks: list[Task]) -> None:
    text = json.dumps([to_dict(task) for task in tasks], indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def load(path: Path) -> list[Task]:
    if not path.exists():
        return []
    return [from_dict(item) for item in json.loads(path.read_text(encoding="utf-8"))]
