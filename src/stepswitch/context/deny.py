"""Per-path deny lists: `privacy.deny_paths` (SPEC §15).

Each pattern is a gitignore-style glob relative to the repository root: `data/**`, `*.pem`,
`/secrets.toml`, `private/`. A denied path is kept from every agent:

* the built-in agent's tools refuse it (`workers.tools`);
* the ACP client refuses it in `fs/read_text_file` and `fs/write_text_file`, and refuses
  permission requests whose locations name it (`workers.acp`);
* harnesses with a permission system of their own get the patterns as deny rules (Claude Code
  `Read(...)` and `Edit(...)` rules, OpenCode `read` and `edit` maps), and the Pi and Oh My Pi
  extensions block their file tools on it (`extension_rules`).

A path is denied when it or any directory above it matches a pattern. `dir/**` also denies
`dir` itself, so a listing does not show it. The deny list applies whatever the run's
permission is.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from stepswitch.context.walk import GlobRule, compile_glob


def _expanded(patterns: Sequence[str]) -> list[str]:
    out = list(patterns)
    # `dir/**` denies everything under `dir`; deny the directory itself as well. A trailing
    # "/" makes the rule directories-only, and the leading "/" keeps it anchored like the
    # original pattern.
    for pattern in patterns:
        base = pattern[:-3] if pattern.endswith("/**") else ""
        if base.strip("/"):
            out.append((base if "/" in base else "/" + base) + "/")
    return out


class DenyList:
    """The compiled `privacy.deny_paths` patterns."""

    def __init__(self, patterns: Iterable[str] = ()) -> None:
        self.patterns: tuple[str, ...] = tuple(p.strip() for p in map(str, patterns) if p.strip())
        rules = [compile_glob(p, allow_negation=False) for p in _expanded(self.patterns)]
        self.rules: tuple[GlobRule, ...] = tuple(rule for rule in rules if rule is not None)

    @classmethod
    def from_config(cls, config: Any) -> DenyList:
        privacy = getattr(config, "privacy", None)
        return cls(getattr(privacy, "deny_paths", None) or [])

    def __bool__(self) -> bool:
        return bool(self.rules)

    def denied(self, rel: str, *, is_dir: bool = False) -> bool:
        """Whether a root-relative "/"-separated path (a directory when `is_dir`) is denied."""
        if not self.rules:
            return False
        parts = [part for part in rel.split("/") if part not in ("", ".")]
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            prefix_is_dir = depth < len(parts) or is_dir
            if any(rule.matches(prefix, prefix_is_dir) for rule in self.rules):
                return True
        return False

    def denied_path(self, path: Path, root: Path) -> bool:
        """Whether a path inside `root` (absolute, or relative to `root`) is denied; a path
        outside `root` is not this list's business."""
        if not self.rules:
            return False
        full = path if path.is_absolute() else root / path
        try:
            rel = full.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            return False
        return rel not in ("", ".") and self.denied(rel, is_dir=full.is_dir())

    def extension_rules(self) -> list[dict[str, Any]]:
        """The rules for the Pi and Oh My Pi extensions: each one's regular expression (valid
        in JavaScript without the `u` flag) and whether it matches directories only."""
        return [{"regex": rule.regex.pattern, "dirOnly": rule.dir_only} for rule in self.rules]
