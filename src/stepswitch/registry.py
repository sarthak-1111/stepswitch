"""The model registry: data files describing models, their files and how engines serve them.

Entries live in `registry/*.toml` (shipped with the package) and in the user's registry
directory, which `ssw models add` writes to and which overrides packaged entries with the same
`id` (SPEC §5, §20). One file holds one entry under a `[model]` table, plus `[engines.<name>]`
recipes. The registry is data: no model has to be listed here to work (SPEC §4.3).
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stepswitch import paths

TIERS = ("cpu", "t1", "t2", "t3")
TIER_ALIASES = {"t0": "cpu", "cpu": "cpu", "t1": "t1", "t2": "t2", "t3": "t3"}
STAMP_SUFFIX = ".sha256"

ToolCalls = Literal["native", "fallback", "none"]
ToolParsing = Literal["server", "client"]
CommercialUse = Literal["permitted", "conditional", "prohibited"]


class EngineRecipe(BaseModel):
    """How one engine serves the model (SPEC §5.3: exact flags, parsers, minimum versions)."""

    model_config = ConfigDict(extra="allow")

    min_version: str | None = None
    args: list[str] = Field(default_factory=list)
    # Engine-side model name: an Ollama tag, or the model path or cached model id a Python
    # server (vLLM, SGLang, MLX-LM, transformers) loads.
    name: str | None = None
    # Engine-side parser names (they differ per engine, ADR 0008).
    tool_parser: str | None = None
    reasoning_parser: str | None = None
    # Who reads tool calls out of the model's output: the engine, or Stepswitch from the reply
    # text with the entry's `tool_syntax`, where the engine's parser misses it (ADR 0022).
    tool_parsing: ToolParsing = "server"
    notes: str | None = None


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    family: str | None = None
    hub_id: str
    source_model: str | None = None
    revision: str | None = None
    file: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    quant: str
    format: str = "gguf"
    architecture: str | None = None
    params_b: float | None = None
    file_size_gb: float
    ram_gb: float
    ram_ctx: int | None = None
    context: int
    # The context size the server gets when the model drives agent turns (Phase 5); the
    # engine recipe's own size applies otherwise.
    agent_ctx: int | None = None
    # A tokenizer name from registry/tokenizers/tokenizers.toml (SPEC §9 counts).
    tokenizer: str | None = None
    tool_calls: ToolCalls = "fallback"
    tool_syntax: str | None = None
    reasoning: str = "none"
    roles: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)
    optional: bool = False
    license: str
    commercial_use: CommercialUse = "permitted"
    template_source: str | None = None
    # sha256 of the chat template, so the tool-call parser is chosen per template version.
    template_sha256: str | None = None
    # How the entry was made: packaged, or by `ssw models add` from a hub repo, a local file
    # (hub_id `local/<id>` with an absolute `file`) or an endpoint.
    source: str | None = None
    verified: date | str | None = None
    engines: dict[str, EngineRecipe] = Field(default_factory=dict)
    notes: str | None = None
    source_path: Path | None = Field(default=None, exclude=True)

    def text_tool_parser(self, engine: str) -> str | None:
        """The catalog syntax Stepswitch parses this model's tool calls with on `engine`."""
        recipe = self.engines.get(engine)
        if recipe is None or recipe.tool_parsing != "client":
            return None
        return self.tool_syntax

    # -- files -------------------------------------------------------------------------------

    def local_dir(self, models_dir: Path) -> Path:
        return models_dir / self.hub_id.replace("/", "--")

    def local_path(self, models_dir: Path) -> Path | None:
        return self.local_dir(models_dir) / self.file if self.file else None

    def stamp_path(self, models_dir: Path) -> Path | None:
        path = self.local_path(models_dir)
        return path.with_name(path.name + STAMP_SUFFIX) if path else None

    def installed(self, models_dir: Path) -> bool:
        """The file is present with the registered size and a verification stamp that matches.

        `ssw models pull` writes the stamp after checking the file's sha256, so a partial or
        corrupted download never counts as installed.
        """
        path = self.local_path(models_dir)
        stamp = self.stamp_path(models_dir)
        if path is None or stamp is None or not path.is_file():
            return False
        if self.size_bytes is not None and path.stat().st_size != self.size_bytes:
            return False
        if self.sha256 is None:
            return True
        try:
            recorded = stamp.read_text(encoding="utf-8").split()[0].lower()
        except (OSError, IndexError):
            return False
        return recorded == self.sha256.lower()

    def listing(self, models_dir: Path) -> dict[str, Any]:
        """The row `ssw models list --json` prints."""
        data = self.model_dump(mode="json", exclude={"source_path"})
        data["installed"] = self.installed(models_dir)
        path = self.local_path(models_dir)
        data["path"] = str(path) if path else None
        data["verified"] = str(self.verified) if self.verified is not None else None
        data["engines"] = sorted(self.engines)
        data["recipes"] = {
            name: recipe.model_dump(mode="json", exclude_none=True)
            for name, recipe in self.engines.items()
        }
        return data


def normalize_tier(tier: str) -> str:
    try:
        return TIER_ALIASES[tier.lower()]
    except KeyError:
        raise ValueError(f"unknown tier {tier!r}; use one of {', '.join(TIERS)}") from None


def read_entry(path: Path) -> ModelEntry:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    model = dict(data.get("model", {}))
    model["engines"] = data.get("engines", {})
    entry = ModelEntry.model_validate(model)
    entry.source_path = path
    return entry


def load(dirs: Iterable[Path] | None = None) -> dict[str, ModelEntry]:
    """All entries by id; later directories override earlier ones."""
    entries: dict[str, ModelEntry] = {}
    for directory in paths.registry_dirs() if dirs is None else dirs:
        for path in sorted(directory.glob("*.toml")):
            entry = read_entry(path)
            entries[entry.id] = entry
    return entries


def for_tier(tier: str, dirs: Iterable[Path] | None = None) -> list[ModelEntry]:
    wanted = normalize_tier(tier)
    return [entry for entry in load(dirs).values() if wanted in entry.tiers]


def find(name: str, dirs: Iterable[Path] | None = None) -> ModelEntry | None:
    """Look an entry up by id, hub id, display name or engine-side name (case-insensitive)."""
    wanted = name.lower()
    for entry in load(dirs).values():
        names = {entry.id, entry.hub_id, entry.name, entry.source_model or ""}
        names |= {recipe.name for recipe in entry.engines.values() if recipe.name}
        if wanted in {n.lower() for n in names if n}:
            return entry
    return None


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def write_stamp(entry: ModelEntry, models_dir: Path, digest: str) -> None:
    stamp = entry.stamp_path(models_dir)
    path = entry.local_path(models_dir)
    if stamp is None or path is None:
        raise ValueError(f"{entry.id} has no file to stamp")
    stamp.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
