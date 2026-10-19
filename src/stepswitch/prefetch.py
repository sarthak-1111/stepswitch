"""The install-time prefetch of what context services use (SPEC §15): `ssw prefetch`.

Nothing a context service uses may be downloaded during a task, by Stepswitch or by a library
it calls, so whatever needs fetching is fetched once, before any task, and the check says what
is there:

* **tree-sitter grammars** (`context.treesitter`): pinned per-language wheels installed with the
  package. Nothing to fetch; the check confirms each grammar loads.
* **tokenizer files** (`context.tokens`): the files of `registry/tokenizers`, downloaded into
  `<models dir>/tokenizers/<name>/` and verified by size and sha256
  (`modelstore.pull_tokenizers`). Reading them needs the `tokens` extra; without it, counts come
  from a llama.cpp server's tokenize route or are flagged estimates.
* **language servers** (`context.lsp`): installed by the user, never downloaded. The check
  reports whether each configured command is found.
* **search** (`context.search`): ripgrep when it is installed, otherwise the built-in walker.

`prefetch` downloads what is missing, which needs the network (offline mode refuses it), then
checks; `check` never opens a connection.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from stepswitch import net

Status = Literal["ready", "missing", "optional", "downloaded", "failed"]
SERVICES = ("tree-sitter", "tokenizers", "language servers", "search")


@dataclass(frozen=True)
class Item:
    service: str
    name: str
    status: Status
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _grammars() -> list[Item]:
    from stepswitch.context import treesitter

    ready = set(treesitter.available_languages())
    return [
        Item(
            "tree-sitter",
            language,
            "ready" if language in ready else "missing",
            module if language in ready else f"{module} does not load (reinstall stepswitch)",
        )
        for language, (module, _function) in sorted(treesitter.GRAMMARS.items())
    ]


def _tokenizers(models_dir: Path) -> list[Item]:
    from stepswitch.context import tokens

    items: list[Item] = []
    if not tokens.tokenizers_available():
        items.append(
            Item(
                "tokenizers",
                "tokenizers package",
                "optional",
                "not installed: install the `tokens` extra for exact token counts",
            )
        )
    for name, spec in sorted(tokens.load_specs().items()):
        path = spec.local_path(models_dir)
        present = path.is_file() and (
            spec.size_bytes is None or path.stat().st_size == spec.size_bytes
        )
        items.append(
            Item(
                "tokenizers",
                name,
                "ready" if present else "missing",
                str(path) if present else f"run `ssw prefetch` to download {spec.file}",
            )
        )
    return items


def _language_servers(config: Any) -> list[Item]:
    from stepswitch.context import lsp
    from stepswitch.context.proc import which

    items = []
    for language, spec in sorted(lsp.load_specs(config).items()):
        found = which(spec.command[0])
        items.append(
            Item(
                "language servers",
                language,
                "ready" if found else "optional",
                found or f"{spec.command[0]} is not installed (it is never downloaded)",
            )
        )
    return items


def _search() -> list[Item]:
    from stepswitch.context import search

    found = search.ripgrep_path()
    if found:
        return [Item("search", "ripgrep", "ready", found)]
    return [Item("search", "ripgrep", "optional", "not installed: the built-in walker is used")]


def check(config: Any, models_dir: Path) -> list[Item]:
    """What each context service has locally. Opens no connection."""
    return [
        *_grammars(),
        *_tokenizers(models_dir),
        *_language_servers(config),
        *_search(),
    ]


def prefetch(
    config: Any,
    models_dir: Path,
    *,
    egress: net.Egress | None = None,
    echo: Callable[[str], None] = print,
    names: Iterable[str] = (),
) -> list[Item]:
    """Download the missing tokenizer files (all of them, or `names`), then check."""
    from stepswitch import modelstore

    pulled = modelstore.pull_tokenizers(
        list(names), models_dir=models_dir, egress=egress, echo=echo
    )
    failed = dict(pulled.failed)
    items = []
    for item in check(config, models_dir):
        if item.service == "tokenizers" and item.name in pulled.downloaded:
            item = Item(item.service, item.name, "downloaded", item.detail)
        elif item.service == "tokenizers" and item.name in failed:
            item = Item(item.service, item.name, "failed", failed[item.name])
        items.append(item)
    return items


def ready(items: Iterable[Item]) -> bool:
    """True when nothing is missing or failed (optional pieces may be absent)."""
    return all(item.status not in ("missing", "failed") for item in items)
