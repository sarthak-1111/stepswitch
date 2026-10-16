"""Engine install: release assets, asset choice by platform, verified download, safe extraction."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from stepswitch import net
from stepswitch.serve import install
from stepswitch.serve.base import EngineError

FIXTURES = Path(__file__).parent / "fixtures" / "serve"
RELEASE = json.loads((FIXTURES / "release-b11669.json").read_text(encoding="utf-8"))
RELEASE_URL = install.RELEASE_API.format(tag="b11669")
DOWNLOADS = "https://downloads.example/b11669"
CDN = "https://cdn.example/blob"


def real_assets() -> list[install.Asset]:
    return [install._asset(item) for item in RELEASE["assets"]]


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def tar_bytes(members: list[tarfile.TarInfo | tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for member in members:
            if isinstance(member, tarfile.TarInfo):
                bundle.addfile(member)
            else:
                name, data = member
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755
                bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def link(name: str, target: str, kind: bytes = tarfile.SYMTYPE) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info


class FakeFeed:
    """The release API plus a download that redirects to a second host, as the real one does."""

    def __init__(
        self, archives: dict[str, bytes], *, digests: dict[str, str | None] | None = None
    ) -> None:
        self.archives = archives
        self.calls: list[str] = []
        overrides = digests or {}
        self.release = {
            "tag_name": "b11669",
            "assets": [
                {
                    "name": name,
                    "size": len(data),
                    "digest": overrides.get(name, "sha256:" + hashlib.sha256(data).hexdigest()),
                    "browser_download_url": f"{DOWNLOADS}/{name}",
                }
                for name, data in archives.items()
            ],
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if url == RELEASE_URL:
            return httpx.Response(200, json=self.release)
        for name, data in self.archives.items():
            if url == f"{DOWNLOADS}/{name}":
                return httpx.Response(302, headers={"Location": f"{CDN}/{name}"})
            if url == f"{CDN}/{name}":
                return httpx.Response(200, content=data)
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def run_install(
    feed: FakeFeed, dest: Path, *, system: str = "Windows", machine: str = "AMD64", **kw: Any
) -> Path:
    return install.install_llamacpp(
        dest=dest,
        transport=feed.transport,
        egress=kw.pop("egress", net.Egress()),
        system=system,
        machine=machine,
        echo=kw.pop("echo", lambda message: None),
        **kw,
    )


# -- release feed -------------------------------------------------------------------------------


def test_release_assets_reads_names_sizes_and_sha256_digests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == RELEASE_URL
        return httpx.Response(200, json=RELEASE)

    egress = net.Egress()
    assets = install.release_assets("b11669", egress=egress, transport=httpx.MockTransport(handler))
    assert len(assets) == 36
    cpu = next(a for a in assets if a.name == "llama-b11669-bin-win-cpu-x64.zip")
    assert cpu.size == 19537886
    assert cpu.sha256 == "37b3efcf962847ca869745fd4ccf7f5e7e8dacfeff9fa3523b4ca8cdc66d632b"
    assert cpu.url.endswith("/b11669/llama-b11669-bin-win-cpu-x64.zip")
    assert all(a.sha256 and len(a.sha256) == 64 for a in assets)
    assert [row.purpose for row in egress.memory] == ["engine-install"]


def test_release_assets_without_digest_and_errors() -> None:
    release = copy.deepcopy(RELEASE)
    release["assets"][0]["digest"] = None
    release["assets"][1]["digest"] = "md5:abc"
    feed = httpx.MockTransport(lambda request: httpx.Response(200, json=release))
    assets = install.release_assets("b11669", egress=net.Egress(), transport=feed)
    assert assets[0].sha256 is None and assets[1].sha256 is None and assets[2].sha256

    missing = httpx.MockTransport(lambda request: httpx.Response(404, json={}))
    with pytest.raises(EngineError, match="not found"):
        install.release_assets("b11669", egress=net.Egress(), transport=missing)
    with pytest.raises(EngineError, match="invalid release tag"):
        install.release_assets("../b1", egress=net.Egress(), transport=missing)


# -- asset choice -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "machine", "backend", "expected"),
    [
        ("Windows", "AMD64", "cpu", "llama-b11669-bin-win-cpu-x64.zip"),
        ("Windows", "ARM64", "cpu", "llama-b11669-bin-win-cpu-arm64.zip"),
        ("Linux", "x86_64", "cpu", "llama-b11669-bin-ubuntu-x64.tar.gz"),
        ("Linux", "aarch64", "cpu", "llama-b11669-bin-ubuntu-arm64.tar.gz"),
        ("Darwin", "arm64", "cpu", "llama-b11669-bin-macos-arm64.tar.gz"),
        ("Darwin", "arm64", "metal", "llama-b11669-bin-macos-arm64.tar.gz"),
        ("Darwin", "x86_64", "cpu", "llama-b11669-bin-macos-x64.tar.gz"),
        ("Windows", "AMD64", "cuda", "llama-b11669-bin-win-cuda-13.4-x64.zip"),
        ("Windows", "AMD64", "cuda-12", "llama-b11669-bin-win-cuda-12.4-x64.zip"),
        ("Linux", "x86_64", "cuda", "llama-b11669-bin-ubuntu-cuda-13.4-x64.tar.gz"),
        ("Linux", "x86_64", "cuda-12.8", "llama-b11669-bin-ubuntu-cuda-12.8-x64.tar.gz"),
        ("Linux", "aarch64", "cuda", "llama-b11669-bin-ubuntu-cuda-13.4-arm64.tar.gz"),
        ("Linux", "x86_64", "vulkan", "llama-b11669-bin-ubuntu-vulkan-x64.tar.gz"),
        ("Windows", "AMD64", "vulkan", "llama-b11669-bin-win-vulkan-x64.zip"),
        ("Linux", "x86_64", "rocm", "llama-b11669-bin-ubuntu-rocm-10.0-x64.tar.gz"),
        ("Windows", "AMD64", "rocm", "llama-b11669-bin-win-rocm-10.0-x64.zip"),
    ],
)
def test_select_asset_with_the_real_b11669_names(
    system: str, machine: str, backend: str, expected: str
) -> None:
    chosen = install.select_asset(real_assets(), system=system, machine=machine, backend=backend)
    assert chosen.name == expected


@pytest.mark.parametrize(
    ("system", "machine", "backend", "message"),
    [
        ("Linux", "x86_64", "metal", "Metal"),
        ("Darwin", "x86_64", "metal", "Metal"),
        ("Darwin", "arm64", "cuda", "no llama.cpp cuda build"),
        ("Windows", "AMD64", "cuda-11", "no llama.cpp cuda-11 build"),
        ("Windows", "AMD64", "tpu", "unknown backend"),
        ("Plan9", "x86_64", "cpu", "OS"),
        ("Linux", "riscv64", "cpu", "CPU architecture"),
    ],
)
def test_select_asset_refuses_unsupported_combinations(
    system: str, machine: str, backend: str, message: str
) -> None:
    with pytest.raises(EngineError, match=message):
        install.select_asset(real_assets(), system=system, machine=machine, backend=backend)


def test_cuda_builds_come_with_their_runtime_archive() -> None:
    assets = real_assets()

    def runtime(name: str) -> str | None:
        build = next(a for a in assets if a.name == name)
        found = install.runtime_asset(assets, build)
        return found.name if found else None

    assert runtime("llama-b11669-bin-win-cuda-13.4-x64.zip") == (
        "cudart-llama-bin-win-cuda-13.4-x64.zip"
    )
    assert runtime("llama-b11669-bin-win-cuda-12.4-x64.zip") == (
        "cudart-llama-bin-win-cuda-12.4-x64.zip"
    )
    assert runtime("llama-b11669-bin-ubuntu-cuda-12.8-x64.tar.gz") == (
        "cudart-llama-b11669-bin-ubuntu-cuda-12.8-x64.tar.gz"
    )
    assert runtime("llama-b11669-bin-win-cpu-x64.zip") is None


def test_default_install_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STEPSWITCH_HOME", str(tmp_path))
    base = tmp_path / "engines" / "llamacpp"
    assert install.default_install_dir() == base / install.PINNED_LLAMACPP
    assert install.default_install_dir("b11669", "cuda") == base / "b11669-cuda"
    assert install.PINNED_LLAMACPP == "b11669"


# -- install ------------------------------------------------------------------------------------


def test_install_downloads_verifies_and_is_idempotent(tmp_path: Path) -> None:
    archive = zip_bytes({"llama-server.exe": b"MZ fake server", "ggml.dll": b"lib"})
    feed = FakeFeed({"llama-b11669-bin-win-cpu-x64.zip": archive})
    egress = net.Egress()
    questions: list[str] = []
    messages: list[str] = []

    def confirm(question: str) -> bool:
        questions.append(question)
        return True

    dest = tmp_path / "engines" / "b11669"
    server = run_install(feed, dest, egress=egress, confirm=confirm, echo=messages.append)

    assert server == dest / "llama-server.exe"
    assert server.read_bytes() == b"MZ fake server"
    assert (dest / "ggml.dll").is_file()
    marker = json.loads((dest / install.MARKER).read_text(encoding="utf-8"))
    assert marker["tag"] == "b11669" and marker["server"] == "llama-server.exe"
    assert marker["assets"][0]["sha256"] == hashlib.sha256(archive).hexdigest()
    assert len(questions) == 1 and "b11669" in questions[0] and "0.0 MB" in questions[0]
    assert messages[-1].startswith("Installed llama.cpp b11669")
    assert [row.purpose for row in egress.memory] == ["engine-install"] * 3  # API, 302, blob
    assert sorted(p.name for p in dest.parent.iterdir()) == ["b11669"]  # staging removed

    calls = len(feed.calls)
    again = run_install(feed, dest, confirm=lambda question: pytest.fail("asked again"))
    assert again == server and len(feed.calls) == calls  # no network for a finished install
    assert install.installed_server(dest, "b11669") == server


def test_install_strips_the_top_level_directory_of_tarballs(tmp_path: Path) -> None:
    members: list[tarfile.TarInfo | tuple[str, bytes]] = [
        ("llama-b11669/llama-server", b"\x7fELF fake"),
        ("llama-b11669/libllama.so.0", b"lib"),
    ]
    if sys.platform != "win32":
        members.append(link("llama-b11669/libllama.so", "libllama.so.0"))
    feed = FakeFeed({"llama-b11669-bin-ubuntu-x64.tar.gz": tar_bytes(members)})
    server = run_install(feed, tmp_path / "eng", system="Linux", machine="x86_64")
    assert server == tmp_path / "eng" / "llama-server"
    assert (tmp_path / "eng" / "libllama.so.0").read_bytes() == b"lib"
    if sys.platform != "win32":
        assert server.stat().st_mode & 0o111
        assert (tmp_path / "eng" / "libllama.so").resolve().name == "libllama.so.0"


def test_install_adds_the_cuda_runtime_next_to_the_server(tmp_path: Path) -> None:
    feed = FakeFeed(
        {
            "llama-b11669-bin-win-cuda-13.4-x64.zip": zip_bytes({"llama-server.exe": b"MZ"}),
            "cudart-llama-bin-win-cuda-13.4-x64.zip": zip_bytes({"cudart64_13.dll": b"rt"}),
        }
    )
    server = run_install(feed, tmp_path / "cuda", backend="cuda")
    assert (server.parent / "cudart64_13.dll").read_bytes() == b"rt"
    marker = json.loads((tmp_path / "cuda" / install.MARKER).read_text(encoding="utf-8"))
    assert [a["name"] for a in marker["assets"]] == [
        "llama-b11669-bin-win-cuda-13.4-x64.zip",
        "cudart-llama-bin-win-cuda-13.4-x64.zip",
    ]


def test_install_refuses_a_sha256_mismatch(tmp_path: Path) -> None:
    name = "llama-b11669-bin-win-cpu-x64.zip"
    archive = zip_bytes({"llama-server.exe": b"tampered"})
    feed = FakeFeed({name: archive}, digests={name: "sha256:" + "0" * 64})
    with pytest.raises(EngineError, match="sha256 mismatch"):
        run_install(feed, tmp_path / "eng")
    assert not (tmp_path / "eng").exists()
    assert list(tmp_path.iterdir()) == []  # the download and the staging directory are gone


def test_install_refuses_a_size_mismatch(tmp_path: Path) -> None:
    name = "llama-b11669-bin-win-cpu-x64.zip"
    feed = FakeFeed({name: zip_bytes({"llama-server.exe": b"MZ"})})
    feed.release["assets"][0]["size"] += 10
    with pytest.raises(EngineError, match="expected"):
        run_install(feed, tmp_path / "eng")
    assert not (tmp_path / "eng").exists()


def test_install_refuses_an_archive_without_a_published_digest(tmp_path: Path) -> None:
    name = "llama-b11669-bin-win-cpu-x64.zip"
    feed = FakeFeed({name: zip_bytes({"llama-server.exe": b"MZ"})}, digests={name: None})
    with pytest.raises(EngineError, match="no sha256 digest"):
        run_install(feed, tmp_path / "eng")
    assert feed.calls == [RELEASE_URL]  # nothing was downloaded


def test_install_refuses_path_traversal_in_a_zip(tmp_path: Path) -> None:
    archive = zip_bytes({"llama-server.exe": b"MZ", "../escaped.txt": b"x"})
    feed = FakeFeed({"llama-b11669-bin-win-cpu-x64.zip": archive})
    with pytest.raises(EngineError, match="unsafe path"):
        run_install(feed, tmp_path / "eng")
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "eng").exists()


@pytest.mark.parametrize(
    "name", ["../evil", "/abs/evil", "a/../../evil", "C:/evil", "..\\evil", "x:stream"]
)
def test_extract_refuses_unsafe_member_names(tmp_path: Path, name: str) -> None:
    for archive_name, data in (
        ("bad.zip", zip_bytes({name: b"x"})),
        ("bad.tar.gz", tar_bytes([(name, b"x")])),
    ):
        archive = tmp_path / archive_name
        archive.write_bytes(data)
        dest = tmp_path / f"out-{archive_name}"
        dest.mkdir()
        with pytest.raises(EngineError, match="unsafe path"):
            install.extract_archive(archive, dest)
        assert list(dest.iterdir()) == []
    assert not (tmp_path / "evil").exists()


@pytest.mark.parametrize(
    "member",
    [
        link("dir/escape", "../../outside"),
        link("dir/absolute", "/etc/passwd"),
        link("dir/hard", "../outside", tarfile.LNKTYPE),
        link("dir/device", "", tarfile.CHRTYPE),
    ],
)
def test_extract_refuses_links_and_devices_that_escape(
    tmp_path: Path, member: tarfile.TarInfo
) -> None:
    archive = tmp_path / "bad.tar.gz"
    archive.write_bytes(tar_bytes([("dir/llama-server", b"x"), member]))
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(EngineError):
        install.extract_archive(archive, dest)
    assert list(dest.iterdir()) == []


def test_install_can_be_cancelled_before_downloading(tmp_path: Path) -> None:
    feed = FakeFeed({"llama-b11669-bin-win-cpu-x64.zip": zip_bytes({"llama-server.exe": b"MZ"})})
    with pytest.raises(EngineError, match="cancelled"):
        run_install(feed, tmp_path / "eng", confirm=lambda question: False)
    assert feed.calls == [RELEASE_URL]
    assert not (tmp_path / "eng").exists()


def test_offline_mode_blocks_the_engine_download(tmp_path: Path) -> None:
    feed = FakeFeed({"llama-b11669-bin-win-cpu-x64.zip": zip_bytes({"llama-server.exe": b"MZ"})})
    offline = net.Egress(net.EgressPolicy(offline=True))
    with pytest.raises(EngineError, match="offline"):
        run_install(feed, tmp_path / "eng", egress=offline)
    assert feed.calls == []
    assert [row.allowed for row in offline.memory] == [False]


def test_install_never_overwrites_a_foreign_directory(tmp_path: Path) -> None:
    dest = tmp_path / "eng"
    dest.mkdir()
    (dest / "notes.txt").write_text("mine", encoding="utf-8")
    feed = FakeFeed({"llama-b11669-bin-win-cpu-x64.zip": zip_bytes({"llama-server.exe": b"MZ"})})
    with pytest.raises(EngineError, match="choose another destination"):
        run_install(feed, dest)
    assert feed.calls == [] and (dest / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_install_finds_no_server_in_the_archive(tmp_path: Path) -> None:
    feed = FakeFeed({"llama-b11669-bin-win-cpu-x64.zip": zip_bytes({"README": b"empty"})})
    with pytest.raises(EngineError, match="contains no llama-server.exe"):
        run_install(feed, tmp_path / "eng")
    assert not (tmp_path / "eng").exists()


def _mock_fail(message: str) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(message, request=request)

    return handler


def test_unreachable_release_feed_is_an_engine_error(tmp_path: Path) -> None:
    transport = httpx.MockTransport(_mock_fail("no route"))
    with pytest.raises(EngineError, match="no route"):
        install.install_llamacpp(
            dest=tmp_path / "eng", egress=net.Egress(), transport=transport, echo=lambda m: None
        )
