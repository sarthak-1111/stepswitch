"""Model hub access: repository metadata, file listings and verified downloads.

Used by `ssw models add` (metadata only, SPEC §4.3) and `ssw models pull` (downloads, SPEC §5.2).
Every request goes through `stepswitch.net` with purpose "hub-metadata" or "model-download", so
it is audited and refused in offline mode, and nothing is fetched unless the user asked for it.

Wire format (checked 2026-10-16 against the hub client library source and read-only requests):

- `GET {hub}/api/models/{repo}` (or `.../revision/{rev}`): JSON with `sha` (the commit),
  `cardData`, `tags`, `gated`, `siblings` and, for GGUF repositories, `gguf` with
  `architecture`, `context_length`, `chat_template` and `total` (parameter count).
- `GET {hub}/api/models/{repo}/tree/{rev}?recursive=true`: a JSON list of entries
  `{type, path, size, oid, lfs?}`; for large files `lfs.oid` is the sha256 of the content and
  `lfs.size` its size. Further pages are announced in a `Link: <url>; rel="next"` header.
- `GET {hub}/{repo}/resolve/{rev}/{file}`: a redirect to a content host, which honours
  `Range` requests (206 with `Content-Range`). An unknown repository answers 401 when the
  request is unauthenticated, an unknown revision or file 404.

The token from `HF_TOKEN` is sent only to the hub host. Redirects to another host drop the
`Authorization` header (the HTTP client strips it on cross-origin redirects), and pagination
links that leave the hub are refused.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from stepswitch import net

HUB_URL = "https://huggingface.co"
HUB_URL_ENV = "STEPSWITCH_HUB_URL"
HUB_ENDPOINT_ENV = "HF_ENDPOINT"
TOKEN_ENV = "HF_TOKEN"

METADATA_PURPOSE = "hub-metadata"
DOWNLOAD_PURPOSE = "model-download"
PART_SUFFIX = ".part"
MAX_TREE_PAGES = 100
METADATA_TIMEOUT = httpx.Timeout(30.0)
# The read timeout applies between chunks: a stalled transfer fails, a slow one does not.
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=30.0)

# Repository ids as the hub accepts them: two parts of 1-96 characters from [A-Za-z0-9_.-],
# each starting and ending with a letter, digit or underscore, without "--" or "..".
_REPO_PART = r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,94}[A-Za-z0-9_])?"
REPO_RE = re.compile(rf"{_REPO_PART}/{_REPO_PART}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CONTENT_RANGE_RE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")

Progress = Callable[[int, int | None], None]


class HubError(RuntimeError):
    """The hub could not be reached, refused the request or answered something unexpected."""


class DownloadError(Exception):
    """A download did not complete or did not match its expected size and sha256."""


@dataclass
class HubFile:
    path: str
    size: int
    sha256: str | None


# --------------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------------


def hub_url() -> str:
    """The hub base URL: STEPSWITCH_HUB_URL, then HF_ENDPOINT, then the public hub."""
    for name in (HUB_URL_ENV, HUB_ENDPOINT_ENV):
        value = os.environ.get(name, "").strip()
        if value:
            return value.rstrip("/")
    return HUB_URL


def token() -> str | None:
    """The hub access token from HF_TOKEN, if set (needed for gated or private repositories)."""
    value = os.environ.get(TOKEN_ENV, "").strip()
    return value or None


def validate_repo(repo: str) -> str:
    if not REPO_RE.fullmatch(repo) or "--" in repo or ".." in repo:
        raise ValueError(f"not a model hub repository id: {repo!r} (expected org/name)")
    return repo


def safe_relative_path(path: str) -> str:
    """Reject repository paths that are absolute or could climb out of a directory."""
    pure = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or ":" in path
        or "\0" in path
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError(f"unsafe file path in a model repository: {path!r}")
    return path


def is_sha256(value: object) -> bool:
    """True for a sha256 hex digest (64 hex digits, either case)."""
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value.lower()))


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = httpx.URL(url)
    return parsed.scheme, (parsed.host or "").lower(), parsed.port


def _auth_headers(url: str) -> dict[str, str]:
    secret = token()
    if secret and _origin(url) == _origin(hub_url()):
        return {"Authorization": f"Bearer {secret}"}
    return {}


def _client(
    purpose: str,
    url: str,
    egress: net.Egress | None,
    transport: httpx.BaseTransport | None,
    timeout: httpx.Timeout,
) -> httpx.Client:
    layer = egress or net.default()
    return layer.client(
        purpose,
        headers=_auth_headers(url),
        timeout=timeout,
        transport=transport,
        follow_redirects=True,
    )


# --------------------------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------------------------


def _status_problem(status: int, what: str) -> str:
    if status in (401, 403):
        return (
            f"{what}: access denied (HTTP {status}). The repository may not exist, or it is "
            f"private or gated: accept its terms on the model hub and set {TOKEN_ENV}"
        )
    if status == 404:
        return f"{what}: not found (HTTP 404); check the repository id, revision and file name"
    return f"{what}: the model hub answered HTTP {status}"


def _get_json(client: httpx.Client, url: str, what: str, **kwargs: Any) -> tuple[Any, str | None]:
    """GET a JSON document; returns it with the URL of the next page, if any."""
    try:
        response = client.get(url, **kwargs)
    except httpx.HTTPError as exc:
        raise HubError(f"{what}: cannot reach the model hub: {exc}") from exc
    if response.status_code >= 400:
        raise HubError(_status_problem(response.status_code, what))
    try:
        data = response.json()
    except ValueError as exc:
        raise HubError(f"{what}: the model hub did not answer with JSON") from exc
    next_url = response.links.get("next", {}).get("url")
    return data, next_url


def model_info(
    repo: str,
    *,
    revision: str | None = None,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Repository metadata, including the `gguf` summary and the commit `sha`."""
    validate_repo(repo)
    url = f"{hub_url()}/api/models/{repo}"
    if revision is not None:
        url += f"/revision/{quote(revision, safe='')}"
    with _client(METADATA_PURPOSE, url, egress, transport, METADATA_TIMEOUT) as client:
        data, _ = _get_json(client, url, repo)
    if not isinstance(data, dict):
        raise HubError(f"{repo}: unexpected repository metadata from the model hub")
    return data


def _hub_file(item: Any) -> HubFile | None:
    if not isinstance(item, dict) or item.get("type") != "file":
        return None
    path = item.get("path")
    if not isinstance(path, str):
        return None
    safe_relative_path(path)
    lfs = item.get("lfs")
    size: Any = item.get("size")
    sha256: Any = None
    if isinstance(lfs, dict):
        size = lfs.get("size", size)
        sha256 = lfs.get("oid")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256.lower()):
        sha256 = None
    return HubFile(path=path, size=size, sha256=sha256.lower() if sha256 else None)


def list_files(
    repo: str,
    *,
    revision: str = "main",
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[HubFile]:
    """Every file of the repository at `revision`, with size and (for large files) sha256."""
    validate_repo(repo)
    base = hub_url()
    url = f"{base}/api/models/{repo}/tree/{quote(revision, safe='')}"
    params: dict[str, str] | None = {"recursive": "true"}
    files: list[HubFile] = []
    with _client(METADATA_PURPOSE, url, egress, transport, METADATA_TIMEOUT) as client:
        for _ in range(MAX_TREE_PAGES):
            data, next_url = _get_json(client, url, f"{repo}@{revision}", params=params)
            if not isinstance(data, list):
                raise HubError(f"{repo}: unexpected file listing from the model hub")
            try:
                files.extend(f for f in map(_hub_file, data) if f is not None)
            except ValueError as exc:
                raise HubError(f"{repo}: {exc}") from exc
            if not next_url:
                return files
            if _origin(next_url) != _origin(base):
                raise HubError(f"{repo}: the file listing points to another host; refused")
            url, params = next_url, None
    raise HubError(f"{repo}: the file listing has more than {MAX_TREE_PAGES} pages")


def file_url(repo: str, revision: str, filename: str) -> str:
    """The download URL of one file: {hub}/{repo}/resolve/{revision}/{filename}."""
    validate_repo(repo)
    safe_relative_path(filename)
    return f"{hub_url()}/{repo}/resolve/{quote(revision, safe='')}/{quote(filename)}"


# --------------------------------------------------------------------------------------------
# Downloads
# --------------------------------------------------------------------------------------------


def part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + PART_SUFFIX)


def _resume_point(part: Path, size: int | None, chunk: int) -> tuple[Any, int]:
    """The hash state and length of an existing partial file (re-hashed from disk)."""
    digest = hashlib.sha256()
    if part.is_symlink():
        part.unlink()
    if not part.is_file():
        return digest, 0
    offset = part.stat().st_size
    if size is not None and offset > size:
        part.unlink()
        return digest, 0
    with part.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest, offset


def _mismatch(part: Path, message: str) -> DownloadError:
    part.unlink(missing_ok=True)
    return DownloadError(message)


def _content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("content-length", "")
    return int(value) if value.isdigit() else None


def _check_range(response: httpx.Response, offset: int, size: int | None, part: Path) -> None:
    match = _CONTENT_RANGE_RE.fullmatch(response.headers.get("content-range", "").strip())
    if match is None or int(match.group(1)) != offset:
        raise _mismatch(
            part, "the server resumed at the wrong offset; the partial file was removed"
        )
    total = match.group(3)
    if size is not None and total != "*" and int(total) != size:
        raise _mismatch(part, f"size mismatch: the server has {total} bytes, expected {size}")


def _stream(
    response: httpx.Response,
    part: Path,
    *,
    append: bool,
    digest: Any,
    offset: int,
    size: int | None,
    total: int | None,
    progress: Progress | None,
    chunk: int,
) -> int:
    with part.open("ab" if append else "wb") as handle:
        for block in response.iter_bytes():  # as received: an interruption loses nothing
            offset += len(block)
            if size is not None and offset > size:
                handle.close()
                raise _mismatch(
                    part, f"size mismatch: received more than the expected {size} bytes"
                )
            handle.write(block)
            digest.update(block)
            if progress is not None:
                progress(offset, total)
    return offset


def download(
    url: str,
    dest: Path,
    *,
    size: int | None,
    sha256: str | None,
    egress: net.Egress | None = None,
    transport: httpx.BaseTransport | None = None,
    progress: Progress | None = None,
    chunk: int = 1 << 20,
) -> str:
    """Download `url` to `dest` through `<dest>.part`, verify it and return its sha256.

    An existing part is re-hashed and resumed with a Range request; when the server ignores the
    range (200), the part is rewritten from the start. The file must match `size` and `sha256`
    (when given) before it is renamed into place. A mismatch deletes the part and raises
    `DownloadError`; an interrupted transfer keeps the part so the next run resumes it.
    """
    part = part_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = sha256.lower() if sha256 else None
    for _attempt in range(2):
        digest, offset = _resume_point(part, size, chunk)
        if size is not None and offset == size:
            break  # complete from an earlier run: verify below, no request needed
        headers = {"Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with (
                _client(DOWNLOAD_PURPOSE, url, egress, transport, DOWNLOAD_TIMEOUT) as client,
                client.stream("GET", url, headers=headers) as response,
            ):
                status = response.status_code
                if status == 416 and offset:
                    part.unlink(missing_ok=True)  # the range is unusable: start over once
                    continue
                if status == 206 and offset:
                    _check_range(response, offset, size, part)
                    append = True
                elif status == 200:
                    append, offset, digest = False, 0, hashlib.sha256()
                    length = _content_length(response)
                    if size is not None and length is not None and length != size:
                        raise _mismatch(
                            part, f"size mismatch: the server sends {length} bytes, expected {size}"
                        )
                else:
                    raise DownloadError(_status_problem(status, dest.name))
                total = size if size is not None else _content_length(response)
                if total is not None and append and size is None:
                    total += offset
                if progress is not None:
                    progress(offset, total)
                offset = _stream(
                    response,
                    part,
                    append=append,
                    digest=digest,
                    offset=offset,
                    size=size,
                    total=total,
                    progress=progress,
                    chunk=chunk,
                )
        except net.EgressBlocked as exc:
            raise DownloadError(f"{dest.name}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise DownloadError(
                f"{dest.name}: download interrupted ({exc}); run the command again to resume"
            ) from exc
        break
    else:
        raise DownloadError(f"{dest.name}: the server refused to resume the download")
    if size is not None and offset < size:
        raise DownloadError(
            f"{dest.name}: incomplete download ({offset} of {size} bytes); "
            "run the command again to resume"
        )
    actual: str = digest.hexdigest()
    if expected is not None and actual != expected:
        raise _mismatch(part, f"{dest.name}: sha256 mismatch: got {actual}, expected {expected}")
    try:
        part.replace(dest)  # atomic on one volume
    except OSError as exc:
        raise DownloadError(f"cannot move the download into place at {dest}: {exc}") from exc
    return actual
