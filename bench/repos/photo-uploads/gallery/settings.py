"""Gallery settings, read from settings.toml at the project root."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "settings.toml"


@dataclass(frozen=True)
class UploadSettings:
    max_size_mb: int
    allowed_extensions: frozenset[str]

    @property
    def max_size_bytes(self) -> int:
        return self.max_size_mb * 1024 * 1024


def load_settings(path: Path = SETTINGS_FILE) -> UploadSettings:
    data = tomllib.loads(path.read_text(encoding="utf-8"))["uploads"]
    extensions = frozenset(extension.lower() for extension in data["allowed_extensions"])
    return UploadSettings(int(data["max_size_mb"]), extensions)
