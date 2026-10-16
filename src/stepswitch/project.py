"""`ssw init`: create the project files and record the hardware profile (SPEC §19, §20)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from stepswitch import config, paths, state, tomlw

GITIGNORE_LINE = ".stepswitch/"


def _ensure_gitignored(root: Path) -> bool:
    """Add .stepswitch/ to .gitignore in a git checkout. True when the file was changed."""
    if not (root / ".git").exists():
        return False
    gitignore = root / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    entries = {line.strip() for line in text.splitlines()}
    if entries & {GITIGNORE_LINE, "/.stepswitch/", ".stepswitch", "/.stepswitch"}:
        return False
    addition = (
        "" if not text or text.endswith("\n") else "\n"
    ) + f"# Stepswitch machine-specific state\n{GITIGNORE_LINE}\n"
    tomlw.atomic_write(gitignore, text + addition)
    return True


def init(root: Path, *, force: bool = False, detect: bool = True) -> dict[str, Any]:
    """Create stepswitch.toml, .stepswitch/local.toml and the state database.

    Existing files are kept unless `force` is set, which rewrites stepswitch.toml only:
    .stepswitch/local.toml holds the user's endpoints and keys and is never overwritten.
    """
    messages: list[str] = []
    created: list[str] = []
    root.mkdir(parents=True, exist_ok=True)

    project_config = paths.project_config_path(root)
    if force or not project_config.is_file():
        tomlw.atomic_write(project_config, config.PROJECT_TEMPLATE)
        created.append(str(project_config))
        messages.append(f"Wrote {project_config}")
    else:
        messages.append(f"Kept {project_config}")

    local_config = paths.local_config_path(root)
    if not local_config.is_file():
        tomlw.atomic_write(local_config, config.LOCAL_TEMPLATE_HEADER)
        created.append(str(local_config))
        messages.append(f"Wrote {local_config} (gitignored: endpoints, keys, hardware)")

    if _ensure_gitignored(root):
        messages.append(f"Added {GITIGNORE_LINE} to .gitignore")

    db = state.open_project_state(root)
    messages.append(f"State database: {db.path} (journal mode {db.journal_mode})")
    db.close()

    profile: dict[str, Any] | None = None
    if detect:
        from stepswitch import doctor

        loaded = config.load(root)
        report = doctor.collect(models_dir=loaded.models_dir(), project_root=root)
        profile = report["profile"]
        try:
            doctor.write_profile(local_config, report)
        except tomllib.TOMLDecodeError as exc:
            messages.append(f"Hardware profile not recorded ({local_config} unchanged): {exc}")
        messages.append(
            f"Hardware profile: tier {profile['tier']} ({profile.get('reason', '')}); "
            "details with `ssw doctor`"
        )
        messages += [f"Problem: {problem}" for problem in report.get("problems", [])]
    messages.append("Next: `ssw models pull --tier cpu` installs the CPU model set.")
    return {"root": str(root), "created": created, "profile": profile, "messages": messages}
