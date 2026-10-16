"""Wheel build hook: ship the built dashboard as `stepswitch/_dashboard` (ADR 0023).

The dashboard is built with npm (`cd dashboard && npm ci && npm run build`) and its output is
not committed, so the hook includes it only when it exists. Editable installs serve
`dashboard/dist` from the source checkout directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class DashboardBuildHook(BuildHookInterface):  # type: ignore[misc]
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable":
            return
        dist = Path(self.root) / "dashboard" / "dist"
        if (dist / "index.html").is_file():
            build_data["force_include"][str(dist)] = "stepswitch/_dashboard"
