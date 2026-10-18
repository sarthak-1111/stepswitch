"""Tracks (SPEC §6): what differs between general and science tasks.

A `TrackPolicy` collects, for one task, the gates that verify it, whether it runs offline by
default, the project interpreter and test command the agent's `test` tool uses, and the notes
the agent's system prompt carries: for science tasks the protected constants and golden cases
from science.toml, and the glossary of the domain the classifier detected (SPEC §6.2).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stepswitch.router.features import TaskFeatures

PACKAGE_DIR = Path(__file__).resolve().parents[1]
# Installed: shipped inside the package; source checkout: the repository's science/ directory.
BUNDLED_GLOSSARIES = (
    PACKAGE_DIR / "_science" / "glossaries",
    PACKAGE_DIR.parents[1] / "science" / "glossaries",
)
# Classifier domains -> glossary file names (science/glossaries/<name>.md).
GLOSSARY_ALIASES: dict[str, str] = {
    "crystallography": "crystallography",
    "xrd": "crystallography",
    "diffraction": "crystallography",
    "spectroscopy": "spectroscopy",
    "raman": "spectroscopy",
    "infrared": "spectroscopy",
    "scattering": "scattering",
    "pdf": "scattering",
    "saxs": "scattering",
    "signal-processing": "signal-processing",
    "signal processing": "signal-processing",
    "dsp": "signal-processing",
    "filtering": "signal-processing",
}
GLOSSARY_MAX_CHARS = 4000


@dataclass
class TrackPolicy:
    name: str
    gates: list[str]
    offline: bool
    python: str
    test_command: list[str] | None
    notes: list[str] = field(default_factory=list)
    glossary: str | None = None
    # Golden cases of the science track (science.toml), for HANDOFF briefs.
    golden: list[str] = field(default_factory=list)

    def system_notes(self) -> str:
        parts = list(self.notes)
        if self.glossary:
            parts.append("Domain glossary:\n" + self.glossary)
        return "\n\n".join(parts)


def _science_toml(repo: Path) -> dict[str, Any]:
    path = repo / "science.toml"
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def glossary_dirs(repo: Path) -> list[Path]:
    """Project glossaries first (science/glossaries/ in the repository), then bundled ones."""
    return [d for d in (repo / "science" / "glossaries", *BUNDLED_GLOSSARIES) if d.is_dir()]


def glossary_for(domain: str, repo: Path) -> str | None:
    name = GLOSSARY_ALIASES.get(domain.strip().lower())
    if name is None:
        return None
    for directory in glossary_dirs(repo):
        path = directory / f"{name}.md"
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            return text[:GLOSSARY_MAX_CHARS]
    return None


def science_notes(science: dict[str, Any]) -> list[str]:
    notes = []
    protected = [
        f"{item.get('file')}: {', '.join(str(n) for n in item.get('names') or [])}"
        for item in science.get("protected") or []
        if isinstance(item, dict)
    ]
    if protected:
        notes.append(
            "Protected values (changing them fails verification unless the user allowed it):\n"
            + "\n".join(f"- {line}" for line in protected)
        )
    cases = [
        str(item.get("case")) for item in science.get("golden") or [] if isinstance(item, dict)
    ]
    if cases:
        notes.append(
            "Numerical outputs are compared with golden references in tests/golden/ ("
            + ", ".join(cases)
            + ") within the tolerances in science.toml; keep results within them."
        )
    return notes


def track_policy(
    track: str, repo: Path, config: Any = None, features: TaskFeatures | None = None
) -> TrackPolicy:
    from stepswitch.verify import commands, gates, impact

    science = _science_toml(repo) if track == "science" else {}
    science_python = (science.get("science") or {}).get("python")
    python = commands.project_python(
        repo,
        config,
        track,
        science_python=science_python if isinstance(science_python, str) else None,
    )
    settings = commands.track_settings(config, track)
    override = (settings.get("commands") or {}).get("tests")
    test_command: list[str] | None
    if isinstance(override, list) and override:
        test_command = [str(part).replace("{python}", python) for part in override]
    elif impact.has_tests(repo):
        test_command = [python, "-m", "pytest", "-q", "--tb=short"]
    else:
        test_command = None
    offline_default = bool(settings.get("offline", track == "science"))
    notes = science_notes(science) if track == "science" else []
    glossary = glossary_for(features.domain, repo) if features is not None else None
    golden = [
        str(item.get("case"))
        for item in science.get("golden") or []
        if isinstance(item, dict) and item.get("case")
    ]
    return TrackPolicy(
        name=track,
        gates=gates.gates_for(track, config),
        offline=offline_default,
        python=python,
        test_command=test_command,
        notes=notes,
        glossary=glossary if track == "science" else None,
        golden=golden,
    )
