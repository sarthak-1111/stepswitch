import pytest
from todo.model import Task


def test_the_title_is_trimmed():
    assert Task("  Water the plants ").title == "Water the plants"


def test_a_title_is_required():
    with pytest.raises(ValueError):
        Task("   ")


def test_complete():
    task = Task("Pay rent")
    task.complete()
    assert task.done


def test_priority_defaults_to_normal():
    assert Task("Pay rent").priority == "normal"


@pytest.mark.parametrize("priority", ["low", "normal", "high"])
def test_known_priorities(priority):
    assert Task("Pay rent", priority=priority).priority == priority


@pytest.mark.parametrize("priority", ["urgent", "HIGH", ""])
def test_unknown_priorities_are_rejected(priority):
    with pytest.raises(ValueError):
        Task("Pay rent", priority=priority)
