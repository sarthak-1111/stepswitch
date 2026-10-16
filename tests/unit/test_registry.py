from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from stepswitch import registry

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_DIR = ROOT / "registry"
# Keys `ssw models list --json` must report (scripts/phase_check.py, Phase 1).
LISTING_KEYS = {
    "hub_id",
    "quant",
    "file_size_gb",
    "ram_gb",
    "context",
    "tool_calls",
    "roles",
    "license",
    "engines",
    "verified",
    "installed",
}


def test_packaged_entries_load_and_cover_the_cpu_tier() -> None:
    entries = registry.load([REGISTRY_DIR])
    cpu = registry.for_tier("cpu", [REGISTRY_DIR])
    assert len(cpu) >= 3
    roles = {role for entry in cpu for role in entry.roles}
    assert {"router", "summarizer", "embeddings", "fim"} <= roles
    for entry in entries.values():
        assert entry.sha256 and len(entry.sha256) == 64
        assert entry.size_bytes and entry.file and entry.revision
        assert entry.verified is not None
        assert abs(entry.file_size_gb - entry.size_bytes / 1e9) < 0.01, entry.id
        assert "llamacpp" in entry.engines


def test_default_cpu_set_fits_the_disk_cap_and_ram_budget() -> None:
    required = [e for e in registry.for_tier("t0", [REGISTRY_DIR]) if not e.optional]
    everything = registry.for_tier("cpu", [REGISTRY_DIR])
    assert sum(e.file_size_gb for e in everything) <= 8.0
    assert sum(e.ram_gb for e in everything) <= 6.0  # ADR 0009: about 5.7 GB resident
    assert all(e.commercial_use == "permitted" for e in required)


def test_listing_has_the_reported_keys(tmp_path: Path) -> None:
    entry = registry.for_tier("cpu", [REGISTRY_DIR])[0]
    row = entry.listing(tmp_path)
    assert set(row) >= LISTING_KEYS
    assert row["installed"] is False
    assert isinstance(row["engines"], list)


def _entry_for(tmp_path: Path, content: bytes) -> registry.ModelEntry:
    return registry.ModelEntry(
        id="tiny",
        name="Tiny",
        hub_id="org/tiny-GGUF",
        file="tiny.gguf",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        quant="Q8_0",
        file_size_gb=0.0,
        ram_gb=0.1,
        context=512,
        license="apache-2.0",
    )


def test_installed_requires_size_and_matching_stamp(tmp_path: Path) -> None:
    content = b"GGUF" + b"\0" * 60
    entry = _entry_for(tmp_path, content)
    path = entry.local_path(tmp_path)
    assert path is not None
    assert path == tmp_path / "org--tiny-GGUF" / "tiny.gguf"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    assert not entry.installed(tmp_path), "no stamp yet"
    registry.write_stamp(entry, tmp_path, "0" * 64)
    assert not entry.installed(tmp_path), "stamp with the wrong hash"
    registry.write_stamp(entry, tmp_path, registry.sha256_file(path))
    assert entry.installed(tmp_path)
    path.write_bytes(content[:-1])
    assert not entry.installed(tmp_path), "size changed"


def test_user_entries_override_packaged_ones(tmp_path: Path) -> None:
    source = REGISTRY_DIR / "qwen3-embedding-0.6b.toml"
    text = source.read_text(encoding="utf-8").replace('quant = "Q8_0"', 'quant = "F16"')
    (tmp_path / "override.toml").write_text(text, encoding="utf-8")
    entries = registry.load([REGISTRY_DIR, tmp_path])
    assert entries["qwen3-embedding-0.6b"].quant == "F16"


def test_find_by_any_name() -> None:
    dirs = [REGISTRY_DIR]
    by_id = registry.find("qwen3-4b-instruct-2507", dirs)
    assert by_id is not None
    assert registry.find("unsloth/Qwen3-4B-Instruct-2507-GGUF", dirs) == by_id
    assert registry.find("QWEN3:4B-INSTRUCT-2507-Q4_K_M", dirs) == by_id
    assert registry.find("no-such-model", dirs) is None


def test_unknown_tier_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown tier"):
        registry.normalize_tier("t9")
