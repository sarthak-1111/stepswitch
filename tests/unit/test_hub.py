"""Model hub client: metadata, file listings and verified, resumable downloads (stepswitch.hub).

Every test runs against an in-process mock transport; nothing leaves the machine. The one live
test lists the registry's files on the real hub and only runs with STEPSWITCH_LIVE_HUB=1.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from stepswitch import hub, net, registry

HUB = "https://hub.example.test"
CDN = "https://cdn.example.test"
REPO = "example-org/Tiny-Model-GGUF"
SHA = "0123456789abcdef0123456789abcdef01234567"
CONTENT = bytes(range(256)) * 40  # 10,240 bytes
DIGEST = hashlib.sha256(CONTENT).hexdigest()

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _hub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hub.HUB_URL_ENV, HUB)
    monkeypatch.delenv(hub.HUB_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(hub.TOKEN_ENV, raising=False)


class Recorder:
    """A mock transport that keeps every request it saw."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def ranged(request: httpx.Request, content: bytes = CONTENT) -> httpx.Response:
    """Serve `content`, honouring a `Range: bytes=N-` header like the hub's content host."""
    header = request.headers.get("range")
    if header:
        start = int(header.removeprefix("bytes=").rstrip("-"))
        if start >= len(content):
            return httpx.Response(416)
        body = content[start:]
        headers = {"content-range": f"bytes {start}-{len(content) - 1}/{len(content)}"}
        return httpx.Response(206, content=body, headers=headers)
    return httpx.Response(200, content=content)


# --------------------------------------------------------------------------------------------
# Settings and URLs
# --------------------------------------------------------------------------------------------


def test_hub_url_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(hub.HUB_URL_ENV)
    assert hub.hub_url() == hub.HUB_URL
    monkeypatch.setenv(hub.HUB_ENDPOINT_ENV, "https://mirror.example.test/")
    assert hub.hub_url() == "https://mirror.example.test"
    monkeypatch.setenv(hub.HUB_URL_ENV, "https://own.example.test")
    assert hub.hub_url() == "https://own.example.test"


def test_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert hub.token() is None
    monkeypatch.setenv(hub.TOKEN_ENV, "  tok-123  ")
    assert hub.token() == "tok-123"


def test_file_url_quotes_revision_and_file() -> None:
    assert hub.file_url(REPO, "main", "a.gguf") == f"{HUB}/{REPO}/resolve/main/a.gguf"
    url = hub.file_url(REPO, "refs/pr/1", "Q4_K_M/model part.gguf")
    assert url == f"{HUB}/{REPO}/resolve/refs%2Fpr%2F1/Q4_K_M/model%20part.gguf"


@pytest.mark.parametrize(
    "repo", ["no-slash", "a/b/c", "org/../x", "org/a--b", "-org/name", "org/name-", "", "o r/g"]
)
def test_invalid_repo_ids(repo: str) -> None:
    with pytest.raises(ValueError, match="repository id"):
        hub.validate_repo(repo)


@pytest.mark.parametrize("path", ["../x.gguf", "/abs.gguf", "a/../../b", "C:/x.gguf", "a\\b", ""])
def test_unsafe_file_paths(path: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        hub.file_url(REPO, "main", path)


# --------------------------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------------------------

INFO = {
    "id": REPO,
    "sha": SHA,
    "gated": False,
    "cardData": {"license": "apache-2.0"},
    "gguf": {"architecture": "qwen3", "context_length": 32768, "total": 4_000_000},
}


def test_model_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hub.TOKEN_ENV, "secret-token")
    recorder = Recorder(lambda request: httpx.Response(200, json=INFO))
    egress = net.Egress()
    info = hub.model_info(REPO, egress=egress, transport=recorder.transport)
    assert info["sha"] == SHA
    request = recorder.requests[0]
    assert str(request.url) == f"{HUB}/api/models/{REPO}"
    assert request.headers["authorization"] == "Bearer secret-token"
    (row,) = list(egress.memory)
    assert row.purpose == hub.METADATA_PURPOSE and row.host == "hub.example.test"

    hub.model_info(REPO, revision=SHA, egress=egress, transport=recorder.transport)
    assert recorder.requests[1].url.path == f"/api/models/{REPO}/revision/{SHA}"


@pytest.mark.parametrize(
    ("status", "message"), [(401, "access denied"), (404, "not found"), (500, "HTTP 500")]
)
def test_model_info_errors(status: int, message: str) -> None:
    recorder = Recorder(lambda request: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(hub.HubError, match=message):
        hub.model_info(REPO, egress=net.Egress(), transport=recorder.transport)


def test_model_info_rejects_non_json_and_offline() -> None:
    recorder = Recorder(lambda request: httpx.Response(200, text="<html>"))
    with pytest.raises(hub.HubError, match="JSON"):
        hub.model_info(REPO, egress=net.Egress(), transport=recorder.transport)
    offline = net.Egress(net.EgressPolicy(offline=True))
    with pytest.raises(hub.HubError, match="offline"):
        hub.model_info(REPO, egress=offline, transport=recorder.transport)
    assert len(recorder.requests) == 1  # the offline call never reached the transport


TREE_PAGE_1 = [
    {"type": "directory", "oid": "d" * 40, "size": 0, "path": "Q8_0"},
    {"type": "file", "oid": "a" * 40, "size": 1652, "path": ".gitattributes"},
    {
        "type": "file",
        "oid": "b" * 40,
        "size": 2000,
        "path": "Tiny-Model-Q4_K_M.gguf",
        "lfs": {"oid": "C" * 64, "size": 2000, "pointerSize": 134},
        "xetHash": "e" * 64,
    },
]
TREE_PAGE_2 = [
    {
        "type": "file",
        "oid": "f" * 40,
        "size": 9000,
        "path": "Q8_0/Tiny-Model-Q8_0-00001-of-00002.gguf",
        "lfs": {"oid": "1" * 64, "size": 9000, "pointerSize": 135},
    },
    {"type": "file", "oid": "9" * 40, "size": 10, "path": "README.md"},
]


def test_list_files_follows_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "2":
            return httpx.Response(200, json=TREE_PAGE_2)
        next_url = f"{HUB}/api/models/{REPO}/tree/main?recursive=true&cursor=2"
        return httpx.Response(200, json=TREE_PAGE_1, headers={"link": f'<{next_url}>; rel="next"'})

    recorder = Recorder(handler)
    files = hub.list_files(REPO, egress=net.Egress(), transport=recorder.transport)
    assert [f.path for f in files] == [
        ".gitattributes",
        "Tiny-Model-Q4_K_M.gguf",
        "Q8_0/Tiny-Model-Q8_0-00001-of-00002.gguf",
        "README.md",
    ]
    by_path = {f.path: f for f in files}
    assert by_path["Tiny-Model-Q4_K_M.gguf"] == hub.HubFile(
        "Tiny-Model-Q4_K_M.gguf", 2000, "c" * 64
    )
    assert by_path["README.md"].sha256 is None  # small files have only a git blob id
    first = recorder.requests[0]
    assert first.url.path == f"/api/models/{REPO}/tree/main"
    assert first.url.params["recursive"] == "true"
    assert len(recorder.requests) == 2


def test_list_files_refuses_pagination_to_another_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hub.TOKEN_ENV, "secret-token")
    link = {"link": f'<{CDN}/steal?cursor=2>; rel="next"'}
    recorder = Recorder(lambda request: httpx.Response(200, json=TREE_PAGE_1, headers=link))
    with pytest.raises(hub.HubError, match="another host"):
        hub.list_files(REPO, egress=net.Egress(), transport=recorder.transport)
    assert all(r.url.host == "hub.example.test" for r in recorder.requests)


def test_list_files_rejects_unsafe_paths() -> None:
    bad = [{"type": "file", "oid": "a" * 40, "size": 1, "path": "../escape.gguf"}]
    recorder = Recorder(lambda request: httpx.Response(200, json=bad))
    with pytest.raises(hub.HubError, match="unsafe"):
        hub.list_files(REPO, egress=net.Egress(), transport=recorder.transport)


# --------------------------------------------------------------------------------------------
# Downloads
# --------------------------------------------------------------------------------------------

URL = f"{HUB}/{REPO}/resolve/{SHA}/tiny.gguf"


def test_fresh_download(tmp_path: Path) -> None:
    recorder = Recorder(ranged)
    dest = tmp_path / "models" / "tiny.gguf"
    seen: list[tuple[int, int | None]] = []
    digest = hub.download(
        URL,
        dest,
        size=len(CONTENT),
        sha256=DIGEST.upper(),
        egress=net.Egress(),
        transport=recorder.transport,
        progress=lambda done, total: seen.append((done, total)),
        chunk=4096,
    )
    assert digest == DIGEST
    assert dest.read_bytes() == CONTENT
    assert not hub.part_path(dest).exists()
    assert seen[0] == (0, len(CONTENT)) and seen[-1] == (len(CONTENT), len(CONTENT))
    request = recorder.requests[0]
    assert "range" not in request.headers
    assert request.headers["accept-encoding"] == "identity"


def test_resume_with_partial_content(tmp_path: Path) -> None:
    dest = tmp_path / "tiny.gguf"
    hub.part_path(dest).write_bytes(CONTENT[:3000])
    recorder = Recorder(ranged)
    digest = hub.download(
        URL,
        dest,
        size=len(CONTENT),
        sha256=DIGEST,
        egress=net.Egress(),
        transport=recorder.transport,
    )
    assert digest == DIGEST  # the existing part was re-hashed before appending
    assert dest.read_bytes() == CONTENT
    assert recorder.requests[0].headers["range"] == "bytes=3000-"


def test_server_ignoring_range_restarts_cleanly(tmp_path: Path) -> None:
    dest = tmp_path / "tiny.gguf"
    hub.part_path(dest).write_bytes(b"stale bytes from another file")
    recorder = Recorder(lambda request: httpx.Response(200, content=CONTENT))
    digest = hub.download(
        URL,
        dest,
        size=len(CONTENT),
        sha256=DIGEST,
        egress=net.Egress(),
        transport=recorder.transport,
    )
    assert digest == DIGEST
    assert dest.read_bytes() == CONTENT
    assert recorder.requests[0].headers["range"].startswith("bytes=")


def test_range_not_satisfiable_starts_over(tmp_path: Path) -> None:
    dest = tmp_path / "tiny.gguf"
    hub.part_path(dest).write_bytes(b"x" * 100)

    def handler(request: httpx.Request) -> httpx.Response:
        if "range" in request.headers:
            return httpx.Response(416)
        return httpx.Response(200, content=CONTENT)

    recorder = Recorder(handler)
    assert (
        hub.download(
            URL, dest, size=None, sha256=DIGEST, egress=net.Egress(), transport=recorder.transport
        )
        == DIGEST
    )
    assert len(recorder.requests) == 2


def test_complete_part_needs_no_request(tmp_path: Path) -> None:
    dest = tmp_path / "tiny.gguf"
    hub.part_path(dest).write_bytes(CONTENT)
    recorder = Recorder(ranged)
    digest = hub.download(
        URL,
        dest,
        size=len(CONTENT),
        sha256=DIGEST,
        egress=net.Egress(),
        transport=recorder.transport,
    )
    assert digest == DIGEST and dest.read_bytes() == CONTENT
    assert recorder.requests == []


@pytest.mark.parametrize(
    "handler",
    [
        # More bytes than the registry says.
        lambda request: httpx.Response(200, content=CONTENT + b"extra"),
        # A Content-Length that disagrees with the registry.
        lambda request: httpx.Response(
            200, content=CONTENT[:-1], headers={"content-length": str(len(CONTENT) - 1)}
        ),
    ],
)
def test_size_mismatch_deletes_the_part(tmp_path: Path, handler: Handler) -> None:
    dest = tmp_path / "tiny.gguf"
    recorder = Recorder(handler)
    with pytest.raises(hub.DownloadError, match="size mismatch"):
        hub.download(
            URL,
            dest,
            size=len(CONTENT),
            sha256=DIGEST,
            egress=net.Egress(),
            transport=recorder.transport,
        )
    assert not dest.exists() and not hub.part_path(dest).exists()


def test_resumed_size_mismatch_deletes_the_part(tmp_path: Path) -> None:
    dest = tmp_path / "tiny.gguf"
    hub.part_path(dest).write_bytes(CONTENT[:100])
    bigger = CONTENT + b"more"
    recorder = Recorder(lambda request: ranged(request, bigger))
    with pytest.raises(hub.DownloadError, match="size mismatch"):
        hub.download(
            URL,
            dest,
            size=len(CONTENT),
            sha256=DIGEST,
            egress=net.Egress(),
            transport=recorder.transport,
        )
    assert not hub.part_path(dest).exists()


def test_sha256_mismatch_deletes_the_part(tmp_path: Path) -> None:
    dest = tmp_path / "tiny.gguf"
    corrupted = b"\xff" + CONTENT[1:]  # same size, different content
    recorder = Recorder(lambda request: httpx.Response(200, content=corrupted))
    with pytest.raises(hub.DownloadError, match="sha256 mismatch"):
        hub.download(
            URL,
            dest,
            size=len(CONTENT),
            sha256=DIGEST,
            egress=net.Egress(),
            transport=recorder.transport,
        )
    assert not dest.exists() and not hub.part_path(dest).exists()


class _Dropping(httpx.SyncByteStream):
    """A body without a length that stops after `keep` bytes, with or without an error."""

    def __init__(self, content: bytes, keep: int, *, error: bool = True) -> None:
        self._content = content
        self._keep = keep
        self._error = error

    def __iter__(self) -> Iterator[bytes]:
        yield self._content[: self._keep]
        if self._error:
            raise httpx.ReadError("connection reset")


def test_interrupted_download_keeps_the_part_and_resumes(tmp_path: Path) -> None:
    dest = tmp_path / "tiny.gguf"
    broken = Recorder(lambda request: httpx.Response(200, stream=_Dropping(CONTENT, 4000)))
    with pytest.raises(hub.DownloadError, match="resume"):
        hub.download(
            URL,
            dest,
            size=len(CONTENT),
            sha256=DIGEST,
            egress=net.Egress(),
            transport=broken.transport,
        )
    assert hub.part_path(dest).read_bytes() == CONTENT[:4000]
    working = Recorder(ranged)
    assert (
        hub.download(
            URL,
            dest,
            size=len(CONTENT),
            sha256=DIGEST,
            egress=net.Egress(),
            transport=working.transport,
        )
        == DIGEST
    )
    assert working.requests[0].headers["range"] == "bytes=4000-"


def test_short_body_is_incomplete_not_a_mismatch(tmp_path: Path) -> None:
    dest = tmp_path / "tiny.gguf"
    short = _Dropping(CONTENT, 5000, error=False)  # a chunked body that ends early
    recorder = Recorder(lambda request: httpx.Response(200, stream=short))
    with pytest.raises(hub.DownloadError, match="incomplete"):
        hub.download(
            URL,
            dest,
            size=len(CONTENT),
            sha256=DIGEST,
            egress=net.Egress(),
            transport=recorder.transport,
        )
    assert hub.part_path(dest).stat().st_size == 5000


def test_redirect_hop_is_audited_and_the_token_stays_on_the_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(hub.TOKEN_ENV, "secret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hub.example.test":
            location = f"{CDN}/blobs/{DIGEST}?signature=abc"
            return httpx.Response(302, headers={"location": location})
        return ranged(request)

    dest = tmp_path / "tiny.gguf"
    hub.part_path(dest).write_bytes(CONTENT[:1000])
    recorder = Recorder(handler)
    egress = net.Egress()
    digest = hub.download(
        URL, dest, size=len(CONTENT), sha256=DIGEST, egress=egress, transport=recorder.transport
    )
    assert digest == DIGEST
    first, second = recorder.requests
    assert first.url.host == "hub.example.test" and second.url.host == "cdn.example.test"
    assert first.headers["authorization"] == "Bearer secret-token"
    assert "authorization" not in second.headers
    assert second.headers["range"] == "bytes=1000-"  # the range survives the redirect
    rows = list(egress.memory)
    assert [(r.host, r.status, r.purpose) for r in rows] == [
        ("hub.example.test", 302, hub.DOWNLOAD_PURPOSE),
        ("cdn.example.test", 206, hub.DOWNLOAD_PURPOSE),
    ]


def test_token_is_not_sent_to_other_hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hub.TOKEN_ENV, "secret-token")
    recorder = Recorder(ranged)
    hub.download(
        f"{CDN}/direct/tiny.gguf",
        tmp_path / "tiny.gguf",
        size=len(CONTENT),
        sha256=DIGEST,
        egress=net.Egress(),
        transport=recorder.transport,
    )
    assert "authorization" not in recorder.requests[0].headers


@pytest.mark.parametrize(("status", "message"), [(401, "access denied"), (404, "not found")])
def test_download_http_errors(tmp_path: Path, status: int, message: str) -> None:
    recorder = Recorder(lambda request: httpx.Response(status))
    with pytest.raises(hub.DownloadError, match=message):
        hub.download(
            URL,
            tmp_path / "x.gguf",
            size=10,
            sha256=None,
            egress=net.Egress(),
            transport=recorder.transport,
        )


def test_offline_mode_blocks_downloads(tmp_path: Path) -> None:
    recorder = Recorder(ranged)
    egress = net.Egress(net.EgressPolicy(offline=True))
    with pytest.raises(hub.DownloadError, match="offline"):
        hub.download(
            URL,
            tmp_path / "x.gguf",
            size=None,
            sha256=None,
            egress=egress,
            transport=recorder.transport,
        )
    assert recorder.requests == []
    (row,) = list(egress.memory)
    assert not row.allowed


# --------------------------------------------------------------------------------------------
# Live check of the packaged registry against the real hub (opt-in)
# --------------------------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("STEPSWITCH_LIVE_HUB") != "1",
    reason="set STEPSWITCH_LIVE_HUB=1 to check the registry against the real model hub",
)
def test_registry_files_match_the_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(hub.HUB_URL_ENV)
    packaged = Path(__file__).resolve().parents[2] / "registry"
    entries = registry.load([packaged])
    assert entries
    for entry in entries.values():
        assert entry.file and entry.revision
        try:
            files = hub.list_files(entry.hub_id, revision=entry.revision, egress=net.Egress())
        except hub.HubError as exc:
            pytest.skip(f"model hub not reachable: {exc}")
        listed = {f.path: f for f in files}
        assert entry.file in listed, f"{entry.id}: {entry.file} not in {entry.hub_id}"
        assert listed[entry.file].size == entry.size_bytes, entry.id
        assert listed[entry.file].sha256 == entry.sha256, entry.id


def test_packaged_entries_have_downloadable_files() -> None:
    """Offline counterpart of the live test: every packaged file maps to a pinned hub URL."""
    packaged = Path(__file__).resolve().parents[2] / "registry"
    for entry in registry.load([packaged]).values():
        if not entry.file:
            continue  # served through an engine recipe only; nothing to download
        url = hub.file_url(entry.hub_id, entry.revision or "main", entry.file)
        assert url.startswith(f"{HUB}/{entry.hub_id}/resolve/"), entry.id
        assert entry.revision and len(entry.revision) == 40, entry.id
        if entry.sha256 is not None:
            assert len(entry.sha256) == 64 and int(entry.sha256, 16) >= 0, entry.id
