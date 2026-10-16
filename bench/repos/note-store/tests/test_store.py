import json

import pytest
from notes.model import Note
from notes.store import StoreError, add_note, load_notes, save_notes, tagged


def test_a_missing_file_holds_no_notes(tmp_path):
    assert load_notes(tmp_path / "notes.json") == []


def test_round_trip(tmp_path):
    path = tmp_path / "notes.json"
    notes = [Note("Groceries", "milk, eggs", ["home"]), Note("Ideas")]
    save_notes(path, notes)
    assert load_notes(path) == notes


def test_add_note_creates_the_file(tmp_path):
    path = tmp_path / "notes.json"
    add_note(path, Note("First"))
    add_note(path, Note("Second", tags=["work"]))
    assert [note.title for note in load_notes(path)] == ["First", "Second"]
    assert [note.title for note in tagged(path, "work")] == ["Second"]


def test_a_corrupt_file_raises_store_error(tmp_path):
    path = tmp_path / "notes.json"
    path.write_text('[{"title": "Groceries", ', encoding="utf-8")
    with pytest.raises(StoreError) as excinfo:
        load_notes(path)
    assert "notes.json" in str(excinfo.value)


def test_a_file_that_is_not_utf8_raises_store_error(tmp_path):
    path = tmp_path / "notes.json"
    path.write_bytes(b"\xff\xfe\x00\x81 not text")
    with pytest.raises(StoreError):
        load_notes(path)


def test_add_note_leaves_a_corrupt_file_alone(tmp_path):
    path = tmp_path / "notes.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError):
        add_note(path, Note("New"))
    assert path.read_text(encoding="utf-8") == "{not json"


def test_a_file_with_the_wrong_shape_raises_store_error(tmp_path):
    path = tmp_path / "notes.json"
    path.write_text(json.dumps({"title": "Groceries"}), encoding="utf-8")
    with pytest.raises(StoreError):
        load_notes(path)
