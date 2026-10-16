import json

from todo.model import Task
from todo.storage import from_dict, load, save, to_dict


def test_a_missing_file_is_an_empty_list(tmp_path):
    assert load(tmp_path / "todo.json") == []


def test_round_trip(tmp_path):
    path = tmp_path / "todo.json"
    tasks = [
        Task("Pay rent", priority="high"),
        Task("Read", done=True),
        Task("Nap", priority="low"),
    ]
    save(path, tasks)
    assert load(path) == tasks


def test_the_priority_is_written(tmp_path):
    path = tmp_path / "todo.json"
    save(path, [Task("Pay rent", priority="high")])
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"title": "Pay rent", "done": False, "priority": "high"}
    ]


def test_files_without_priorities_still_load(tmp_path):
    path = tmp_path / "todo.json"
    path.write_text('[{"title": "Pay rent", "done": true}]', encoding="utf-8")
    tasks = load(path)
    assert tasks == [Task("Pay rent", done=True)]
    assert tasks[0].priority == "normal"


def test_dict_round_trip():
    task = Task("Call the plumber", priority="high")
    assert from_dict(to_dict(task)) == task
