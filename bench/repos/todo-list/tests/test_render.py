from todo.model import Task
from todo.render import render, render_all


def test_open_and_done_tasks():
    assert render(Task("Water the plants")) == "[ ] Water the plants"
    assert render(Task("Pay rent", done=True)) == "[x] Pay rent"


def test_high_priority_is_marked():
    assert render(Task("Pay rent", priority="high")) == "[ ] ! Pay rent"
    assert render(Task("Pay rent", done=True, priority="high")) == "[x] ! Pay rent"


def test_low_and_normal_priorities_are_not_marked():
    assert render(Task("Nap", priority="low")) == "[ ] Nap"
    assert render(Task("Read", priority="normal")) == "[ ] Read"


def test_render_all_puts_open_tasks_first():
    tasks = [Task("Read", done=True), Task("Pay rent", priority="high"), Task("Nap")]
    assert render_all(tasks) == "[ ] ! Pay rent\n[ ] Nap\n[x] Read"
