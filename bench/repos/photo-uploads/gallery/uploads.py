"""Checks on uploaded photos."""

from pathlib import PurePath

from gallery.settings import UploadSettings, load_settings


class UploadRejected(Exception):
    """An upload that breaks the gallery's rules."""


def check_upload(filename: str, size_bytes: int, settings: UploadSettings | None = None) -> None:
    """Raise UploadRejected unless the file may be uploaded."""
    rules = settings or load_settings()
    extension = PurePath(filename).suffix.lower()
    if extension not in rules.allowed_extensions:
        raise UploadRejected(f"{filename}: {extension or 'no extension'} files are not allowed")
    if size_bytes > rules.max_size_bytes:
        raise UploadRejected(f"{filename}: larger than {rules.max_size_mb} MB")
