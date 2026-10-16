"""Configuration: packaged defaults < stepswitch.toml < .stepswitch/local.toml < env < CLI flags.

`stepswitch.toml` is committed and holds project policy (tracks, budgets, privacy defaults).
`.stepswitch/local.toml` is gitignored and holds everything machine-specific: endpoints, keys,
hardware facts and the model directory (working rule 5).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stepswitch import paths
from stepswitch.net import is_local_url

ENV_PREFIX = "STEPSWITCH_"
AuthKind = Literal["none", "bearer", "header"]


class EndpointConfig(BaseModel):
    """A reachable model server or remote API (SPEC §2 "Endpoint")."""

    model_config = ConfigDict(extra="allow")

    name: str
    url: str
    engine: str | None = None
    format: str | None = None
    formats: list[str] = Field(default_factory=list)
    auth: AuthKind = "none"
    api_key: str | None = Field(default=None, repr=False)
    api_key_env: str | None = None
    auth_header: str | None = None
    models: list[str] = Field(default_factory=list)
    local: bool | None = None
    # A tool-call syntax from the parser catalog, for a server that does not parse its models'
    # tool calls: Stepswitch then parses them from the reply text (SPEC §4.4, ADR 0022).
    tool_parser: str | None = None

    @field_validator("tool_parser")
    @classmethod
    def _known_tool_parser(cls, value: str | None) -> str | None:
        from stepswitch.compat.toolcall import parsers

        if value is not None and value not in parsers.PARSERS:
            raise ValueError(f"unknown tool parser {value!r}; known: {', '.join(parsers.PARSERS)}")
        return value

    def is_local(self) -> bool:
        return self.local if self.local is not None else is_local_url(self.url)

    def resolved_api_key(self) -> str | None:
        if self.api_key_env:
            return os.environ.get(self.api_key_env) or self.api_key
        return self.api_key

    def primary_format(self) -> str:
        if self.format:
            return self.format
        if self.formats:
            return self.formats[0]
        return {
            "ollama": "ollama-native",
            "llamacpp": "chat-completions",
        }.get(self.engine or "", "chat-completions")


class PrivacyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: Literal["default", "local"] = "default"
    deny_paths: list[str] = Field(default_factory=list)


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    models_dir: str | None = None
    max_gb: float = 8.0


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    tokens: int | None = None
    wall_seconds: float | None = None
    gpu_seconds: float | None = None
    escalations: int | None = None


class Config(BaseModel):
    """The merged configuration. Unknown sections are kept for later phases."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = Field(default=1, alias="schema")
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    endpoints: list[EndpointConfig] = Field(default_factory=list)
    host: dict[str, Any] = Field(default_factory=dict)
    models: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @property
    def offline(self) -> bool:
        return self.privacy.mode == "local"

    def models_dir(self) -> Path:
        configured = self.storage.models_dir or self.host.get("model_dir")
        return Path(configured).expanduser() if configured else paths.default_models_dir()

    def endpoint(self, name: str) -> EndpointConfig | None:
        return next((e for e in self.endpoints if e.name == name), None)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge `override` into a copy of `base`; tables merge, lists and scalars replace."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def env_overrides(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """`STEPSWITCH_OFFLINE=1`, `STEPSWITCH_MODELS_DIR=...`, `STEPSWITCH_MAX_GB=...`."""
    env = os.environ if environ is None else environ
    data: dict[str, Any] = {}
    if env.get(f"{ENV_PREFIX}OFFLINE", "").lower() in {"1", "true", "yes", "on"}:
        data.setdefault("privacy", {})["mode"] = "local"
    if env.get(f"{ENV_PREFIX}MODELS_DIR"):
        data.setdefault("storage", {})["models_dir"] = env[f"{ENV_PREFIX}MODELS_DIR"]
    if env.get(f"{ENV_PREFIX}MAX_GB"):
        data.setdefault("storage", {})["max_gb"] = float(env[f"{ENV_PREFIX}MAX_GB"])
    return data


def load(
    root: Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> Config:
    project = root or paths.find_project_root()
    data: dict[str, Any] = {}
    for layer in (
        read_toml(paths.project_config_path(project)),
        read_toml(paths.local_config_path(project)),
        env_overrides(environ),
        overrides or {},
    ):
        data = deep_merge(data, layer)
    return Config.model_validate(data)


PROJECT_TEMPLATE = """\
# Stepswitch project configuration (committed). Machine-specific settings, endpoints and keys
# belong in .stepswitch/local.toml, which is gitignored.
schema = 1

[privacy]
# "local" blocks every non-local connection (SPEC §15).
mode = "default"
deny_paths = []

[budgets]
# Per-task limits (SPEC §7.4). Leave a key out for no limit.
tokens = 400000
wall_seconds = 1800

[tracks.general]
gates = ["format", "lint", "typecheck", "impacted_tests", "full_tests"]

[tracks.science]
gates = ["format", "lint", "protected", "impacted_tests", "golden", "full_tests"]
offline = true
"""

LOCAL_TEMPLATE_HEADER = """\
# Machine-specific Stepswitch settings. Never commit this file (working rule 5).
schema = 1
"""
