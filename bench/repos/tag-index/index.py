"""Look up notes by tag."""

from collections.abc import Mapping

from tags import tag_key, unique_tags


def build_index(notes: Mapping[str, list[str]]) -> dict[str, list[str]]:
    """Map each tag key to the sorted titles of the notes that carry the tag."""
    index: dict[str, list[str]] = {}
    for title, tags in notes.items():
        for tag in unique_tags(tags):
            index.setdefault(tag_key(tag), []).append(title)
    return {key: sorted(titles) for key, titles in sorted(index.items())}


def notes_tagged(index: Mapping[str, list[str]], tag: str) -> list[str]:
    """Titles of the notes that carry `tag`."""
    return list(index.get(tag_key(tag), []))
