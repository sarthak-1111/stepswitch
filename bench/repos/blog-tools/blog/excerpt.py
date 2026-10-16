"""Short excerpts for lists of posts."""


def excerpt(text: str, limit: int = 160) -> str:
    """The start of `text`, cut at a word boundary and ending in '...' when shortened."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 3].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "..."
