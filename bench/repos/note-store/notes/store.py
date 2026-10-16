"""Load and save notes as a JSON list."""

import json
from pathlib import Path

from notes.model import Note


class StoreError(Exception):
    """A notes file that cannot be used."""


def load_notes(path: Path) -> list[Note]:
    """All notes in the file at `path`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        raise StoreError(f"{path}: expected a list of notes")
    return [Note.from_dict(item) for item in data]


def save_notes(path: Path, notes: list[Note]) -> None:
    text = json.dumps([note.to_dict() for note in notes], indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def add_note(path: Path, note: Note) -> list[Note]:
    """Add a note to the file and return all notes."""
    notes = load_notes(path)
    notes.append(note)
    save_notes(path, notes)
    return notes


def tagged(path: Path, tag: str) -> list[Note]:
    """The notes in the file that carry `tag`."""
    return [note for note in load_notes(path) if tag in note.tags]
