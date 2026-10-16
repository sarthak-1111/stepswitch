from __future__ import annotations

import tomllib
from datetime import date, datetime
from pathlib import Path

import pytest

from stepswitch import tomlw


def test_dumps_round_trips_values_tables_and_arrays() -> None:
    data = {
        "schema": 1,
        "name": 'quote " backslash \\ newline \n tab \t',
        "ratio": 2.5,
        "tiny": 1e-05,
        "big": 1e16,
        "flag": False,
        "when": date(2026, 10, 16),
        "stamp": datetime(2026, 10, 16, 12, 30, 0),
        "list": [1, 2, 3],
        "inline": [{"a": 1}, {"b": "x"}][0],
        "skip": None,
        "model": {"id": "x", "engines": {"llamacpp": {"args": ["--ctx-size", "4096"]}}},
        "endpoints": [{"name": "a", "url": "http://127.0.0.1:1"}, {"name": "b", "models": []}],
        "weird key": {"dotted.name": 1},
    }
    text = tomlw.dumps(data, header="# generated")
    assert text.startswith("# generated\n\n")
    parsed = tomllib.loads(text)
    expected = {k: v for k, v in data.items() if v is not None}
    assert parsed == expected


def test_tables_without_own_keys_get_no_header() -> None:
    text = tomlw.dumps({"host": {"detected": {"os": "linux"}}})
    assert "[host]" not in text
    assert "[host.detected]" in text


def test_dumps_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError):
        tomlw.dumps({"x": object()})


USER_FILE = """\
# user comment kept
schema = 1

[host]
model_dir = "D:/models"  # inline comment kept

[[endpoints]]
name = "a"
url = "http://127.0.0.1:11434"
"""


def test_write_block_preserves_user_content_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text(USER_FILE, encoding="utf-8")
    data = {"host": {"detected": {"ram_gb": 16.0}}, "profile": {"tier": "cpu"}}
    tomlw.write_block(path, "doctor", data)
    first = path.read_text(encoding="utf-8")
    tomlw.write_block(path, "doctor", data)
    second = path.read_text(encoding="utf-8")
    assert first == second
    assert first.startswith(USER_FILE)
    assert first.count(">>> doctor") == 1
    parsed = tomllib.loads(second)
    assert parsed["host"]["model_dir"] == "D:/models"
    assert parsed["host"]["detected"]["ram_gb"] == 16.0
    assert parsed["endpoints"][0]["name"] == "a"
    assert parsed["profile"]["tier"] == "cpu"


def test_write_block_replaces_old_block_content(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    tomlw.write_block(path, "doctor", {"profile": {"tier": "cpu", "old": 1}})
    tomlw.write_block(path, "doctor", {"profile": {"tier": "t1"}})
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed == {"profile": {"tier": "t1"}}


def test_write_block_refuses_conflicts_and_keeps_file(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text("[profile]\ntier = 'mine'\n", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        tomlw.write_block(path, "doctor", {"profile": {"tier": "cpu"}})
    assert path.read_text(encoding="utf-8") == "[profile]\ntier = 'mine'\n"


def test_block_body_must_start_with_a_table() -> None:
    with pytest.raises(ValueError, match="table header"):
        tomlw.replace_block("", "x", "key = 1\n")
