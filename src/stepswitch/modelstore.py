"""`ssw models pull`: plan, confirm and run verified model downloads (SPEC §5.2, ADR 0009).

A pull is planned first: each selected registry entry is classified as installed, present but
unverified ("verify"), to download, blocked by its license, or left out by the disk cap. The
cap applies to the on-disk footprint of the whole selected set (installed plus to download);
when the set is over it, optional models are left out first, then the largest, and every entry
left out says why. Nothing is downloaded before the user confirms, flagged licenses need an
explicit acceptance (`--yes` never accepts one), and a download only counts as installed once
its size and sha256 match the registry (`registry.write_stamp`).

Downloads come from the original source on the model hub through `stepswitch.net`, into
`<models dir>/<org>--<name>/<file>`; paths that would leave the models directory are refused.
"""

from __future__ import annotations

import importlib
import math
import os
import shutil
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from stepswitch import hub, net, registry
from stepswitch.registry import ModelEntry

GB = 1_000_000_000  # registry sizes are in GB of 10^9 bytes (docs/RESEARCH.md conventions)
ENGINES = ("llamacpp", "ollama")
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_HOST_ENV = "OLLAMA_HOST"
LOCAL_HUB_PREFIX = "local/"

Status = Literal["installed", "verify", "download", "over-cap", "license"]
Progress = Callable[[int, int | None], None]


@dataclass
class PullItem:
    entry: ModelEntry
    path: Path
    status: Status
    download_bytes: int
    reason: str = ""

    @property
    def id(self) -> str:
        return self.entry.id

    @property
    def disk_bytes(self) -> int:
        """The size of the model file once installed."""
        if self.entry.size_bytes is not None:
            return self.entry.size_bytes
        return round(self.entry.file_size_gb * GB)


@dataclass
class PullPlan:
    items: list[PullItem]
    models_dir: Path
    max_gb: float
    total_disk_bytes: int
    download_bytes: int
    ram_gb: float
    # Entries with nothing to pull (no file, a missing local file, an unsafe path or optional
    # models left out on request), as (id, reason).
    excluded: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class PullResult:
    installed: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------------------------
# Selection and planning
# --------------------------------------------------------------------------------------------


def select(
    tier: str | None, names: Sequence[str], *, dirs: Iterable[Path] | None = None
) -> list[ModelEntry]:
    """The entries of a tier's default set and/or the named models, without duplicates."""
    if not tier and not names:
        raise ValueError("give a tier (for example --tier cpu) or at least one model name")
    search = list(dirs) if dirs is not None else None
    chosen: dict[str, ModelEntry] = {}
    if tier:
        for entry in registry.for_tier(tier, search):
            chosen.setdefault(entry.id, entry)
    for name in names:
        found = registry.find(name, search)
        if found is None:
            raise ValueError(
                f"unknown model {name!r}: not in the registry "
                "(see `ssw models list`, or add it with `ssw models add`)"
            )
        chosen.setdefault(found.id, found)
    return list(chosen.values())


def is_local_entry(entry: ModelEntry) -> bool:
    """An entry for a file the user registered in place (`ssw models add <path>`)."""
    return entry.hub_id.startswith(LOCAL_HUB_PREFIX) or bool(
        entry.file and Path(entry.file).is_absolute()
    )


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _unpullable(entry: ModelEntry, models_dir: Path) -> str | None:
    """Why the entry cannot be pulled into the models directory, if it cannot."""
    if not entry.file:
        return "its registry entry names no model file; nothing to download"
    try:
        hub.validate_repo(entry.hub_id)
        hub.safe_relative_path(entry.file)
    except ValueError as exc:
        return f"refused: {exc}"
    path = entry.local_path(models_dir)
    if path is None or not _within(path, models_dir):
        return "refused: its file path would leave the models directory"
    return _unverifiable(entry)


def _unverifiable(entry: ModelEntry) -> str | None:
    """Why the entry's file could not be verified, if it could not: no pinned size or sha256."""
    if hub.is_sha256(entry.sha256) and entry.size_bytes is not None and entry.size_bytes > 0:
        return None
    return (
        "refused: its registry entry does not pin the file's size and sha256, so the file "
        "could not be verified; register it with `ssw models add`"
    )


def _part_bytes(path: Path) -> int:
    part = hub.part_path(path)
    try:
        return part.stat().st_size if part.is_file() else 0
    except OSError:
        return 0


def _classify(entry: ModelEntry, path: Path, models_dir: Path) -> PullItem:
    item = PullItem(entry=entry, path=path, status="download", download_bytes=0)
    if entry.installed(models_dir):
        item.status = "installed"
        return item
    if path.is_file() and (entry.size_bytes is None or path.stat().st_size == entry.size_bytes):
        item.status = "verify"
        return item
    resumed = _part_bytes(path)
    if entry.size_bytes is None or resumed > entry.size_bytes:
        resumed = 0
    item.download_bytes = max(item.disk_bytes - resumed, 0)
    if entry.commercial_use != "permitted":
        item.status = "license"
        item.reason = _license_reason(entry)
    return item


def _license_reason(entry: ModelEntry) -> str:
    return (
        f"license {entry.license} has {entry.commercial_use} commercial use; "
        f"accept it with --accept-license {entry.id}"
    )


def _local_item(entry: ModelEntry, models_dir: Path) -> PullItem | str:
    path = entry.local_path(models_dir)
    if path is None or not path.is_file():
        return f"its local file is missing ({entry.file}); register it again with `ssw models add`"
    problem = _unverifiable(entry)
    if problem is not None:
        return problem
    if entry.installed(models_dir):
        return PullItem(entry=entry, path=path, status="installed", download_bytes=0)
    if entry.size_bytes is not None and path.stat().st_size != entry.size_bytes:
        return "its local file changed size since it was added; add it again with `ssw models add`"
    return PullItem(entry=entry, path=path, status="verify", download_bytes=0)


def _apply_cap(items: list[PullItem], max_gb: float) -> None:
    """Leave out downloads until the set fits the cap: optional ones first, then the largest."""
    cap = int(max_gb * GB)
    need = sum(item.disk_bytes for item in items)
    if need <= cap:
        return
    total = need
    candidates = [item for item in items if item.status in ("download", "license")]
    candidates.sort(key=lambda item: (not item.entry.optional, -item.disk_bytes))
    dropped: list[PullItem] = []
    for item in candidates:
        if total <= cap:
            break
        total -= item.disk_bytes
        dropped.append(item)
    # Optional models stay out even if one fits again after a larger required model was left
    # out: the user should not get the optional model instead of the required one.
    for item in dropped:
        first = "optional model, left out first" if item.entry.optional else "left out"
        item.reason = (
            f"{first}: the selected set needs {need / GB:.2f} GB on disk, over the "
            f"{max_gb:g} GB cap; raise --max-gb to include it"
        )
        item.status = "over-cap"
        item.download_bytes = 0


def plan(
    entries: Iterable[ModelEntry],
    *,
    models_dir: Path,
    max_gb: float = 8.0,
    include_optional: bool = True,
) -> PullPlan:
    """Classify each entry and apply the disk cap; touches nothing and makes no requests."""
    if not math.isfinite(max_gb) or max_gb <= 0:
        raise ValueError(f"the disk cap must be a positive number of GB, got {max_gb}")
    models_dir = Path(models_dir)
    items: list[PullItem] = []
    excluded: list[tuple[str, str]] = []
    for entry in entries:
        if entry.optional and not include_optional:
            excluded.append((entry.id, "optional model, left out on request (--no-optional)"))
            continue
        if is_local_entry(entry):
            local = _local_item(entry, models_dir)
            if isinstance(local, str):
                excluded.append((entry.id, local))
            else:
                items.append(local)
            continue
        problem = _unpullable(entry, models_dir)
        path = entry.local_path(models_dir)
        if problem is not None or path is None:
            excluded.append((entry.id, problem or "no file"))
            continue
        items.append(_classify(entry, path, models_dir))
    _apply_cap(items, max_gb)
    kept = [item for item in items if item.status != "over-cap"]
    return PullPlan(
        items=items,
        models_dir=models_dir,
        max_gb=max_gb,
        total_disk_bytes=sum(item.disk_bytes for item in kept),
        download_bytes=sum(item.download_bytes for item in kept if item.status == "download"),
        ram_gb=round(sum(item.entry.ram_gb for item in kept), 2),
        excluded=excluded,
    )


def _gb(size: int) -> str:
    return f"{size / GB:.2f} GB"


def _status_text(item: PullItem) -> str:
    if item.status == "installed":
        return "installed"
    if item.status == "verify":
        return "on disk, not yet verified: will check its sha256"
    if item.status == "download":
        resumed = item.disk_bytes - item.download_bytes
        text = f"download {_gb(item.download_bytes)}"
        return text + (f" (resuming; {_gb(resumed)} already on disk)" if resumed else "")
    if item.status == "license":
        return f"needs license acceptance: {item.reason}"
    return f"skipped: {item.reason}"


def describe(plan: PullPlan) -> str:
    """The plan as text: per model status and sizes, totals, the RAM estimate and the cap."""
    lines = [f"Models directory: {plan.models_dir}"]
    width = max([len(item.id) for item in plan.items] + [len(i) for i, _ in plan.excluded] + [8])
    for item in plan.items:
        lines.append(
            f"  {item.id:<{width}}  {item.entry.quant:<8} {_gb(item.disk_bytes):>9}  "
            f"RAM {item.entry.ram_gb:5.2f} GB  {_status_text(item)}"
        )
    for entry_id, reason in plan.excluded:
        lines.append(f"  {entry_id:<{width}}  skipped: {reason}")
    licensed = sum(item.download_bytes for item in plan.items if item.status == "license")
    download = f"to download: {_gb(plan.download_bytes)}"
    if licensed:
        download += f" (plus {_gb(licensed)} if the flagged licenses are accepted)"
    lines.append(
        f"Disk: {_gb(plan.total_disk_bytes)} for the selected set, cap {plan.max_gb:g} GB; "
        f"{download}."
    )
    lines.append(
        f"RAM: about {plan.ram_gb:.2f} GB if every selected model runs at once (estimate at "
        "the per-role context sizes; servers start lazily and idle ones are evicted)."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------------


def _free_bytes(directory: Path) -> int | None:
    probe = directory
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def default_ollama_url() -> str:
    """The local Ollama server: OLLAMA_HOST when set, else 127.0.0.1:11434."""
    value = os.environ.get(OLLAMA_HOST_ENV, "").strip()
    if not value:
        return OLLAMA_URL
    try:
        url = httpx.URL(value if "://" in value else "http://" + value)
        host = url.host or "127.0.0.1"
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"  # a wildcard bind address is reached through loopback
        return str(url.copy_with(host=host, port=url.port or 11434, path="/")).rstrip("/")
    except (httpx.InvalidURL, ValueError, TypeError):
        return OLLAMA_URL


def _text_progress(echo: Callable[[str], None], label: str) -> Progress:
    """Report a download every tenth of the way (or every GB when the size is unknown)."""
    state = {"next": 0}

    def report(done: int, total: int | None) -> None:
        if total:
            step = max(total // 10, 1)
            if done >= state["next"] or done >= total:
                echo(f"  {label}: {done / total:4.0%} of {_gb(total)}")
                state["next"] = (done // step + 1) * step
        elif done >= state["next"]:
            echo(f"  {label}: {_gb(done)}")
            state["next"] = done + GB

    return report


def _accepted(item: PullItem, accepted: set[str]) -> bool:
    names = {item.entry.id, item.entry.hub_id, item.entry.name}
    return any(name.lower() in accepted for name in names)


def execute(
    plan: PullPlan,
    *,
    yes: bool = False,
    accept_licenses: Collection[str] = (),
    confirm: Callable[[str], bool] | None = None,
    echo: Callable[[str], None] = print,
    engine: str = "llamacpp",
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
    dry_run: bool = False,
    progress: Progress | None = None,
    ollama_url: str | None = None,
) -> PullResult:
    """Carry out a plan; `describe(plan)` is expected to have been shown already.

    Local files marked "verify" are hashed first (no network, no question asked). Downloads
    need one confirmation (`confirm`, or `yes`); without either, nothing is downloaded. Flagged
    licenses need their model id in `accept_licenses`. `dry_run` reports what would happen and
    makes no request at all. With engine "ollama", the models are pulled by their Ollama names
    through the local Ollama server instead of being downloaded as files.
    """
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}; use one of {', '.join(ENGINES)}")
    result = PullResult()
    accepted = {name.lower() for name in accept_licenses}
    result.skipped.extend(plan.excluded)
    to_verify: list[PullItem] = []
    to_fetch: list[PullItem] = []
    for item in plan.items:
        if item.status == "over-cap":
            result.skipped.append((item.id, item.reason or "over the disk cap"))
        elif item.status == "license" and not _accepted(item, accepted):
            result.skipped.append((item.id, item.reason))
        elif engine == "ollama":
            to_fetch.append(item)
        elif item.status == "installed":
            result.installed.append(item.id)
        elif item.status == "verify":
            to_verify.append(item)
        else:
            to_fetch.append(item)

    if dry_run:
        result.skipped.extend((item.id, "dry run: would verify") for item in to_verify)
        verb = "pull with Ollama" if engine == "ollama" else "download"
        result.skipped.extend((item.id, f"dry run: would {verb}") for item in to_fetch)
        echo(
            f"Dry run: would verify {len(to_verify)} and {verb} {len(to_fetch)} model(s); "
            "nothing was changed."
        )
        return result

    if engine == "ollama":
        _pull_ollama(to_fetch, result, yes, confirm, echo, egress, transport, progress, ollama_url)
        return result

    for item in to_verify:
        if _verify(item, plan.models_dir, result, echo):
            continue
        # A complete file with the wrong content: fetch it again with the downloads, behind
        # the same license gate as any other download.
        if item.entry.commercial_use != "permitted" and not _accepted(item, accepted):
            result.skipped.append(
                (item.id, f"the file on disk failed verification; {_license_reason(item.entry)}")
            )
            continue
        item.download_bytes = item.disk_bytes
        to_fetch.append(item)
    if not to_fetch:
        return result
    _download_all(plan, to_fetch, result, yes, confirm, echo, egress, transport, progress)
    return result


def _verify(
    item: PullItem, models_dir: Path, result: PullResult, echo: Callable[[str], None]
) -> bool:
    """Hash a file already on disk; True when it is settled (verified or failed)."""
    echo(f"Verifying {item.id} ({_gb(item.disk_bytes)}, sha256)...")
    try:
        digest = registry.sha256_file(item.path)
    except OSError as exc:
        result.failed.append((item.id, f"cannot read {item.path}: {exc}"))
        return True
    expected = (item.entry.sha256 or "").lower()
    if not hub.is_sha256(expected):
        result.failed.append((item.id, "its registry entry has no sha256 to verify against"))
        return True
    if digest != expected:
        if is_local_entry(item.entry):
            result.failed.append(
                (item.id, "sha256 mismatch: the local file changed since it was added")
            )
            return True
        echo(
            f"  {item.id}: the file on disk does not match its sha256; it will be downloaded again"
        )
        return False
    try:
        registry.write_stamp(item.entry, models_dir, digest)
    except OSError as exc:
        result.failed.append((item.id, f"verified, but the stamp could not be written: {exc}"))
        return True
    result.verified.append(item.id)
    return True


def _confirmed(
    question: str,
    items: list[PullItem],
    result: PullResult,
    yes: bool,
    confirm: Callable[[str], bool] | None,
    echo: Callable[[str], None],
) -> bool:
    if yes:
        return True
    if confirm is None:
        echo("Nothing downloaded: this needs a confirmation; run again with --yes to proceed.")
        result.skipped.extend((item.id, "not confirmed") for item in items)
        return False
    if not confirm(question):
        echo("Nothing downloaded.")
        result.skipped.extend((item.id, "declined") for item in items)
        return False
    return True


def _offline(egress: net.Egress | None) -> bool:
    return (egress or net.default()).policy.offline


def _download_all(
    plan: PullPlan,
    items: list[PullItem],
    result: PullResult,
    yes: bool,
    confirm: Callable[[str], bool] | None,
    echo: Callable[[str], None],
    egress: net.Egress | None,
    transport: httpx.BaseTransport | None,
    progress: Progress | None,
) -> None:
    need = sum(item.download_bytes for item in items)
    if _offline(egress) and not net.is_local_url(hub.hub_url()):
        reason = "offline mode (privacy.mode = local) blocks downloads from the model hub"
        result.failed.extend((item.id, reason) for item in items)
        echo(f"Nothing downloaded: {reason}.")
        return
    free = _free_bytes(plan.models_dir)
    if free is not None and free < need:
        reason = (
            f"not enough free disk space: {_gb(need)} needed, {_gb(free)} free at {plan.models_dir}"
        )
        result.failed.extend((item.id, reason) for item in items)
        echo(f"Nothing downloaded: {reason}.")
        return
    question = (
        f"Download {len(items)} model file(s), {_gb(need)}, from the model hub into "
        f"{plan.models_dir}?"
    )
    if not _confirmed(question, items, result, yes, confirm, echo):
        return
    for item in items:
        entry = item.entry
        echo(f"Downloading {item.id} ({_gb(item.download_bytes)})...")
        try:
            url = hub.file_url(entry.hub_id, entry.revision or "main", entry.file or "")
            digest = hub.download(
                url,
                item.path,
                size=entry.size_bytes,
                sha256=entry.sha256,
                egress=egress,
                transport=transport,
                progress=progress or _text_progress(echo, item.id),
            )
            registry.write_stamp(entry, plan.models_dir, digest)
        except (hub.DownloadError, ValueError, OSError) as exc:
            result.failed.append((item.id, str(exc)))
            echo(f"  {item.id}: failed: {exc}")
            continue
        result.downloaded.append(item.id)
        echo(f"  {item.id}: verified (sha256 {digest[:12]}...) and installed")


def _ollama_events(
    echo: Callable[[str], None], label: str, progress: Progress | None
) -> Callable[[dict[str, Any]], None]:
    """Turn Ollama's pull events into status lines and (completed, total) progress calls."""
    last = {"status": ""}

    def handle(event: dict[str, Any]) -> None:
        status = event.get("status")
        if isinstance(status, str) and status and status != last["status"]:
            last["status"] = status
            echo(f"  {label}: {status}")
        completed, total = event.get("completed"), event.get("total")
        if progress is not None and isinstance(completed, int):
            progress(completed, total if isinstance(total, int) else None)

    return handle


def _pull_ollama(
    items: list[PullItem],
    result: PullResult,
    yes: bool,
    confirm: Callable[[str], bool] | None,
    echo: Callable[[str], None],
    egress: net.Egress | None,
    transport: httpx.BaseTransport | None,
    progress: Progress | None,
    ollama_url: str | None,
) -> None:
    named: list[tuple[PullItem, str]] = []
    for item in items:
        recipe = item.entry.engines.get("ollama")
        if recipe is None or not recipe.name:
            result.skipped.append((item.id, "its registry entry has no Ollama model name"))
        else:
            named.append((item, recipe.name))
    if not named:
        return
    base_url = ollama_url or default_ollama_url()
    pulled = [item for item, _ in named]
    if _offline(egress):
        # The local server would fetch from its own remote registry: egress behind localhost.
        reason = "offline mode (privacy.mode = local) blocks Ollama pulls"
        result.failed.extend((item.id, reason) for item in pulled)
        echo(f"Nothing pulled: {reason}.")
        return
    question = (
        f"Pull {len(named)} model(s) with Ollama at {base_url} "
        f"({', '.join(name for _, name in named)})?"
    )
    if not _confirmed(question, pulled, result, yes, confirm, echo):
        return
    # Imported lazily: the engine adapter is only needed for this path.
    ollama_engine: Any = importlib.import_module("stepswitch.serve.ollama")
    for item, name in named:
        echo(f"Pulling {name} with Ollama...")
        try:
            ollama_engine.pull_model(
                base_url,
                name,
                egress=egress,
                transport=transport,
                progress=_ollama_events(echo, item.id, progress),
            )
        except Exception as exc:  # noqa: BLE001 - one failed pull must not stop the others
            result.failed.append((item.id, f"Ollama pull of {name} failed: {exc}"))
            continue
        result.downloaded.append(item.id)
