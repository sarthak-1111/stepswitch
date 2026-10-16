"""Install a pinned llama.cpp server build from the project's release feed (SPEC §5.3, ADR 0008).

The archive is picked by OS, CPU architecture and backend (cpu, cuda, rocm, metal, vulkan),
downloaded through `stepswitch.net` with the purpose "engine-install", checked against the size
and the sha256 digest the release API publishes for it, and extracted only after every member
path was checked, so nothing can land outside the install directory. Nothing is downloaded
unless this function is called.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from stepswitch import net, paths
from stepswitch.serve.base import EngineError, http_client

# v0.7.0 is published on the build tag b11669 (the v0.7.0 release's nightly-tag.txt says so).
PINNED_LLAMACPP = "b11669"
RELEASE_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{tag}"
PURPOSE = "engine-install"
MARKER = "stepswitch-install.json"
BACKENDS = ("cpu", "cuda", "rocm", "metal", "vulkan")

_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_BUILD_RE = re.compile(
    r"^llama-(?P<tag>b\d+)-bin-(?P<os>win|ubuntu|macos)-(?P<rest>[a-z0-9.-]+)\.(?:zip|tar\.gz)$"
)
_RUNTIME_RE = re.compile(
    r"^cudart-llama-(?:(?P<tag>b\d+)-)?bin-(?P<os>win|ubuntu)-cuda-(?P<version>[\d.]+)"
    r"-(?P<arch>x64|arm64)\.(?:zip|tar\.gz)$"
)
_ARCHES = ("x64", "arm64", "s390x")
_OS_TOKENS = {"windows": "win", "linux": "ubuntu", "darwin": "macos"}
_ARCH_TOKENS = {"amd64": "x64", "x86_64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64"}


@dataclass(frozen=True)
class Asset:
    """One downloadable file of a release, as the release API lists it."""

    name: str
    url: str
    size: int
    sha256: str | None


@dataclass(frozen=True)
class _Build:
    asset: Asset
    os: str
    arch: str
    family: str  # cpu, cuda, vulkan, rocm, sycl, openvino, ...
    version: tuple[int, ...]  # backend version, e.g. (13, 4) for cuda-13.4


def server_name(system: str | None = None) -> str:
    """The server binary's file name on `system` (default: this OS)."""
    windows = (system or platform.system()).lower() == "windows"
    return "llama-server.exe" if windows else "llama-server"


def default_install_dir(tag: str = PINNED_LLAMACPP, backend: str = "cpu") -> Path:
    """`<engines>/llamacpp/<tag>` for the CPU build, `<tag>-<backend>` for the others."""
    name = tag if backend.lower() in ("cpu", "metal") else f"{tag}-{backend.lower()}"
    return paths.engines_dir() / "llamacpp" / name


# -- release feed -------------------------------------------------------------------------------


def release_assets(
    tag: str,
    *,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[Asset]:
    """The assets of one llama.cpp release with their sha256 digests (None when not listed)."""
    _check_tag(tag)
    url = RELEASE_API.format(tag=tag)
    try:
        with http_client(PURPOSE, egress=egress, transport=transport, timeout=30.0) as client:
            response = client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise EngineError(f"cannot read the llama.cpp release {tag}: {exc}") from exc
    if response.status_code == 404:
        raise EngineError(f"llama.cpp release {tag} not found")
    if response.status_code != 200:
        raise EngineError(f"the release feed answered HTTP {response.status_code} for {tag}")
    try:
        items = response.json().get("assets", [])
        return [_asset(item) for item in items]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise EngineError(f"unexpected release data for llama.cpp {tag}: {exc}") from exc


def _asset(item: dict[str, Any]) -> Asset:
    digest = str(item.get("digest") or "")
    algorithm, _, value = digest.partition(":")
    sha = value.lower() if algorithm == "sha256" and _SHA_RE.match(value.lower()) else None
    return Asset(
        name=str(item["name"]),
        url=str(item["browser_download_url"]),
        size=int(item["size"]),
        sha256=sha,
    )


def _check_tag(tag: str) -> None:
    if not _TAG_RE.match(tag):
        raise EngineError(f"invalid release tag {tag!r}")


# -- asset selection ----------------------------------------------------------------------------


def _os_token(system: str) -> str:
    try:
        return _OS_TOKENS[system.lower()]
    except KeyError:
        raise EngineError(f"no llama.cpp builds for the OS {system!r}") from None


def _arch_token(machine: str) -> str:
    try:
        return _ARCH_TOKENS[machine.lower()]
    except KeyError:
        raise EngineError(f"no llama.cpp builds for the CPU architecture {machine!r}") from None


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def _parse_build(asset: Asset) -> _Build | None:
    match = _BUILD_RE.match(asset.name)
    if match is None:
        return None
    tokens = match["rest"].split("-")
    arches = [token for token in tokens if token in _ARCHES]
    if len(arches) != 1:
        return None
    tokens.remove(arches[0])
    variant = [token for token in tokens if token != "cpu"]
    family = variant[0] if variant else "cpu"
    return _Build(asset, match["os"], arches[0], family, _version_tuple("-".join(variant[1:])))


def _split_backend(backend: str) -> tuple[str, tuple[int, ...]]:
    family, _, wanted = backend.lower().partition("-")
    if family not in BACKENDS:
        raise EngineError(f"unknown backend {backend!r}; use one of {', '.join(BACKENDS)}")
    return family, _version_tuple(wanted)


def select_asset(
    assets: Iterable[Asset],
    *,
    system: str,
    machine: str,
    backend: str = "cpu",
) -> Asset:
    """The server archive for an OS (`platform.system()`), CPU (`platform.machine()`) and backend.

    `backend` is cpu, cuda, rocm, metal or vulkan, optionally with a version prefix such as
    `cuda-12` (for drivers older than the CUDA 13 branch or GPUs it no longer supports). Without
    one, the newest backend version wins. The macOS arm64 build carries Metal and also runs on
    the CPU alone, so `metal` and `cpu` pick the same archive there.
    """
    os_name, arch = _os_token(system), _arch_token(machine)
    family, wanted = _split_backend(backend)
    if family == "metal":
        if os_name != "macos" or arch != "arm64":
            raise EngineError("Metal builds exist only for arm64 macOS")
        family = "cpu"
    builds = [
        build
        for build in map(_parse_build, assets)
        if build is not None and build.os == os_name and build.arch == arch
    ]
    matches = [
        build
        for build in builds
        if build.family == family and build.version[: len(wanted)] == wanted
    ]
    if not matches:
        offered = sorted({build.asset.name for build in builds}) or ["none"]
        raise EngineError(
            f"no llama.cpp {backend} build for {system}/{machine}; offered: {', '.join(offered)}"
        )
    return max(matches, key=lambda build: build.version).asset


def runtime_asset(assets: Iterable[Asset], build_asset: Asset) -> Asset | None:
    """The CUDA runtime archive that goes with a CUDA build (None for other backends)."""
    build = _parse_build(build_asset)
    if build is None or build.family != "cuda":
        return None
    tag = _BUILD_RE.match(build_asset.name)
    for asset in assets:
        match = _RUNTIME_RE.match(asset.name)
        if (
            match is not None
            and match["os"] == build.os
            and match["arch"] == build.arch
            and _version_tuple(match["version"]) == build.version
            and match["tag"] in (None, tag["tag"] if tag else None)
        ):
            return asset
    return None


# -- install ------------------------------------------------------------------------------------


def installed_server(directory: Path, tag: str | None = None) -> Path | None:
    """The server binary of a finished Stepswitch install in `directory` (optionally of `tag`)."""
    marker = read_marker(directory)
    if marker is None or (tag is not None and marker.get("tag") != tag):
        return None
    relative = marker.get("server")
    if not isinstance(relative, str):
        return None
    server = directory / relative
    return server if server.is_file() else None


def read_marker(directory: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((directory / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def install_llamacpp(
    tag: str = PINNED_LLAMACPP,
    *,
    backend: str = "cpu",
    dest: Path | None = None,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
    confirm: Callable[[str], bool] | None = None,
    echo: Callable[[str], object] = print,
    system: str | None = None,
    machine: str | None = None,
) -> Path:
    """Download, verify and unpack a llama.cpp build; return the path of `llama-server`.

    Idempotent: a finished install of the same tag in the destination is returned without any
    network access. `confirm`, when given, is asked before anything is downloaded; a False
    answer aborts with EngineError. `system` and `machine` default to this host.
    """
    _check_tag(tag)
    system = system or platform.system()
    machine = machine or platform.machine()
    _split_backend(backend)
    target = dest if dest is not None else default_install_dir(tag, backend)
    existing = installed_server(target, tag)
    if existing is not None:
        echo(f"llama.cpp {tag} is already installed in {target}")
        return existing
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        other = (read_marker(target) or {}).get("tag") if target.is_dir() else None
        what = f"holds llama.cpp {other}" if other else "is not a llama.cpp install by Stepswitch"
        raise EngineError(f"{target} {what}; remove it or choose another destination")

    assets = release_assets(tag, egress=egress, transport=transport)
    build = select_asset(assets, system=system, machine=machine, backend=backend)
    downloads = [build]
    runtime = runtime_asset(assets, build)
    if runtime is not None:
        downloads.append(runtime)
    for asset in downloads:
        _check_asset(asset)
    total_mb = sum(asset.size for asset in downloads) / 1e6
    names = ", ".join(asset.name for asset in downloads)
    question = f"Download llama.cpp {tag} ({names}, {total_mb:.1f} MB) and install it in {target}?"
    if confirm is not None and not confirm(question):
        raise EngineError("llama.cpp installation cancelled")

    binary = server_name(system)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        unpacked = staging / "unpacked"
        unpacked.mkdir()
        for asset in downloads:
            archive = staging / asset.name
            echo(f"Downloading {asset.name} ({asset.size / 1e6:.1f} MB)")
            download(asset, archive, egress=egress, transport=transport)
            extract_archive(archive, unpacked)
            archive.unlink()
        server = _locate(unpacked, binary)
        if server is None:
            raise EngineError(f"{build.name} contains no {binary}")
        if sys.platform != "win32":
            server.chmod(server.stat().st_mode | 0o755)
        relative = server.relative_to(unpacked).as_posix()
        marker = {
            "engine": "llamacpp",
            "tag": tag,
            "backend": backend.lower(),
            "system": system,
            "machine": machine,
            "server": relative,
            "installed_at": time.time(),
            "assets": [
                {"name": a.name, "size": a.size, "sha256": a.sha256, "url": a.url}
                for a in downloads
            ],
        }
        (unpacked / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        try:
            if target.exists():
                target.rmdir()
            unpacked.replace(target)
        except OSError:
            winner = installed_server(target, tag)
            if winner is None:
                raise
            return winner  # another process finished the same install first
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    echo(f"Installed llama.cpp {tag} ({build.name}) in {target}")
    return target / relative


def _check_asset(asset: Asset) -> None:
    if not _NAME_RE.match(asset.name) or ".." in asset.name:
        raise EngineError(f"refusing an asset with an unsafe name: {asset.name!r}")
    if not asset.url.startswith("https://"):
        raise EngineError(f"refusing a non-https download URL for {asset.name}")
    if asset.sha256 is None:
        raise EngineError(
            f"the release feed lists no sha256 digest for {asset.name}; "
            "refusing to install an archive that cannot be verified"
        )


def download(
    asset: Asset,
    path: Path,
    *,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Stream an asset to `path` (following redirects) and verify its size and sha256."""
    _check_asset(asset)
    digest = hashlib.sha256()
    received = 0
    timeout = httpx.Timeout(30.0, read=120.0)
    try:
        with (
            http_client(
                PURPOSE,
                egress=egress,
                transport=transport,
                timeout=timeout,
                follow_redirects=True,
            ) as client,
            client.stream("GET", asset.url) as response,
        ):
            if response.status_code != 200:
                raise EngineError(f"download of {asset.name} failed: HTTP {response.status_code}")
            with path.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    received += len(chunk)
                    if received > asset.size:
                        raise EngineError(
                            f"{asset.name} is larger than the {asset.size} bytes listed"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
    except httpx.HTTPError as exc:
        path.unlink(missing_ok=True)
        raise EngineError(f"download of {asset.name} failed: {exc}") from exc
    except EngineError:
        path.unlink(missing_ok=True)
        raise
    if received != asset.size:
        path.unlink(missing_ok=True)
        raise EngineError(f"{asset.name}: received {received} bytes, expected {asset.size}")
    actual = digest.hexdigest()
    if actual != asset.sha256:
        path.unlink(missing_ok=True)
        raise EngineError(
            f"{asset.name}: sha256 mismatch (expected {asset.sha256}, got {actual}); "
            "the download was discarded"
        )


# -- safe extraction ----------------------------------------------------------------------------


def extract_archive(archive: Path, dest: Path) -> None:
    """Extract a .zip or .tar.gz into `dest`, refusing any member that would escape it.

    A single top-level directory shared by every member is stripped, so the server binary and
    any runtime libraries from a companion archive end up side by side.
    """
    name = archive.name.lower()
    if name.endswith(".zip"):
        _extract_zip(archive, dest)
    elif name.endswith((".tar.gz", ".tgz")):
        _extract_tar(archive, dest)
    else:
        raise EngineError(f"unsupported archive type: {archive.name}")


def _parts(name: str) -> list[str]:
    """Validated path components of an archive member name."""
    text = name.replace("\\", "/")
    unsafe = (
        not text
        or "\x00" in text
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:", text) is not None
        or ":" in text
    )
    parts = [part for part in PurePosixPath(text).parts if part not in ("", ".")]
    if unsafe or ".." in parts:
        raise EngineError(f"refusing unsafe path in archive: {name!r}")
    return parts


def _common_root(names: Sequence[str]) -> str | None:
    tops: set[str] = set()
    nested = False
    for name in names:
        parts = _parts(name)
        if not parts:
            continue
        tops.add(parts[0])
        nested = nested or len(parts) > 1
    return tops.pop() if len(tops) == 1 and nested else None


def _target(dest: Path, parts: Sequence[str]) -> Path:
    root = dest.resolve()
    target = root.joinpath(*parts).resolve() if parts else root
    if target != root and root not in target.parents:
        raise EngineError(f"refusing archive member outside the install directory: {target}")
    return target


def _strip(parts: list[str], root: str | None) -> list[str]:
    return parts[1:] if root is not None and parts and parts[0] == root else parts


def _extract_zip(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        root = _common_root([info.filename for info in members])
        plan: list[tuple[zipfile.ZipInfo, Path, int]] = []
        for info in members:
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise EngineError(f"refusing a link in the archive: {info.filename!r}")
            parts = _strip(_parts(info.filename), root)
            plan.append((info, _target(dest, parts), mode))
        for info, target, mode in plan:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, 1 << 20)
            if sys.platform != "win32" and mode & 0o111:
                target.chmod(0o755)


def _extract_tar(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:*") as bundle:
        members = bundle.getmembers()
        root = _common_root([member.name for member in members])
        selected: list[tarfile.TarInfo] = []
        for member in members:
            parts = _strip(_parts(member.name), root)
            if not parts:
                continue  # the stripped top-level directory itself
            _target(dest, parts)
            if member.issym():
                link = member.linkname.replace("\\", "/")
                if not link or link.startswith("/") or re.match(r"^[A-Za-z]:", link):
                    raise EngineError(f"refusing an absolute link: {member.name!r}")
                _target(dest, [*parts[:-1], *PurePosixPath(link).parts])
            elif member.islnk():
                member.linkname = "/".join(_strip(_parts(member.linkname), root))
                _target(dest, _parts(member.linkname))
            elif not (member.isfile() or member.isdir()):
                raise EngineError(f"refusing a special file in the archive: {member.name!r}")
            member.name = "/".join(parts)
            selected.append(member)
        if hasattr(tarfile, "data_filter"):
            bundle.extractall(dest, members=selected, filter="data")
        else:  # Python < 3.11.4: every member was checked above
            bundle.extractall(dest, members=selected)


def _locate(root: Path, binary: str) -> Path | None:
    direct = root / binary
    if direct.is_file():
        return direct
    found = sorted(root.rglob(binary), key=lambda path: len(path.parts))
    return next((path for path in found if path.is_file()), None)
