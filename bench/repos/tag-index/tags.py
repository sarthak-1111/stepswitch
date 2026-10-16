"""Tags for a note-taking app."""

from collections.abc import Iterable


def clean(tag: str) -> str:
    """The tag as typed, without surrounding or repeated spaces."""
    return " ".join(tag.split())


def tag_key(tag: str) -> str:
    """The form used to decide whether two tags are the same tag."""
    return clean(tag).lower()


def unique_tags(tags: Iterable[str]) -> list[str]:
    """The tags without duplicates, keeping the first spelling of each."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        key = tag_key(tag)
        if key and key not in seen:
            seen.add(key)
            result.append(clean(tag))
    return result
