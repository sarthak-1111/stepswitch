"""Hash-anchored line edits (spec/edit-format.md)."""

from __future__ import annotations

import codecs
import os
import stat
import sys
from pathlib import Path

import pytest

from stepswitch.context import edits as E


def anchors(path: Path) -> list[str]:
    doc = E.TextDoc.decode(path.read_bytes())
    return [f"{n}:{h}" for n, h in enumerate(E.line_hashes(doc.lines), start=1)]


def write(path: Path, data: str | bytes) -> Path:
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


# -- hashes --------------------------------------------------------------------------------


def test_hash_is_two_letters_and_ignores_trailing_whitespace() -> None:
    assert E.line_hash("def f():") == "rc"  # golden values pin the algorithm (spec §2)
    assert E.line_hash("    return 1") == "wn"
    assert E.line_hash("") == "fy"
    assert E.line_hash("", 2) == "vy"
    assert E.line_hash("}", 3) == "pg"
    assert E.line_hash("x = 2") == "zq"
    assert E.line_hash("x = 2 \t\r") == "zq"
    assert E.line_hash("  x = 2") != "zq"  # leading whitespace counts
    for text in ("a", "b", "longer line", "ünïcødé"):
        digest = E.line_hash(text)
        assert len(digest) == E.HASH_LEN and digest.isalpha() and digest.islower()


def test_repeated_lines_get_different_anchors() -> None:
    hashes = E.line_hashes(["}", "x", "}", "", "", "}"])
    assert hashes[0] != hashes[2] != hashes[5]
    assert hashes[3] != hashes[4]
    assert hashes[0] == E.line_hash("}") and hashes[2] == E.line_hash("}", 2)


def test_render_shows_line_hash_and_text() -> None:
    lines = ["def f():", "    return 1"]
    seen: dict[tuple[int, str], str] = {}
    text = E.render(lines, E.line_hashes(lines), seen=seen)
    assert text == "1:rc|def f():\n2:wn|    return 1"
    assert seen == {(1, "rc"): "def f():", (2, "wn"): "    return 1"}
    long = E.render(["x" * 30], ["aa"], max_line_chars=10)
    assert long == "1:aa|xxxxxxxxxx... [20 more chars]"


def test_parse_anchor_forms() -> None:
    assert E.parse_anchor("12:ab") == E.Anchor(12, "ab")
    assert E.parse_anchor(" 12:AB|    return x") == E.Anchor(12, "ab")
    assert E.parse_anchor({"line": "7", "hash": "qz"}) == E.Anchor(7, "qz")
    assert E.parse_anchor("L3:xy") == E.Anchor(3, "xy")
    with pytest.raises(E.EditError, match="has no hash"):
        E.parse_anchor(12)
    with pytest.raises(E.EditError, match="not an anchor"):
        E.parse_anchor("12:abc")


# -- operations ----------------------------------------------------------------------------


def test_replace_insert_and_delete_in_one_call(tmp_path: Path) -> None:
    path = write(tmp_path / "m.py", "a\nb\nc\nd\ne\n")
    a = anchors(path)
    result = E.edit_file(
        path,
        display="m.py",
        edits=[
            {"op": "replace", "start": a[1], "end": a[2], "text": "B\nC\nC2\n"},
            {"op": "insert_before", "start": a[0], "text": "top"},
            {"op": "insert_after", "start": a[4], "text": ["tail1", "tail2"]},
            {"op": "delete", "start": a[3]},
        ],
    )
    assert path.read_text(encoding="utf-8") == "top\na\nB\nC\nC2\ne\ntail1\ntail2\n"
    assert result.changed and not result.created
    assert (result.added, result.removed) == (6, 3)
    assert result.diff.startswith("--- a/m.py\n+++ b/m.py\n@@")
    assert "1:" in result.preview and "|top" in result.preview


def test_all_anchors_refer_to_the_file_before_the_call(tmp_path: Path) -> None:
    path = write(tmp_path / "m.py", "1\n2\n3\n")
    a = anchors(path)
    E.edit_file(
        path,
        edits=[
            {"op": "insert_after", "start": a[0], "text": "1.5\n1.6"},
            {"op": "replace", "start": a[2], "text": "THREE"},
        ],
    )
    assert path.read_text(encoding="utf-8") == "1\n1.5\n1.6\n2\nTHREE\n"


def test_several_inserts_at_one_place_keep_their_order(tmp_path: Path) -> None:
    path = write(tmp_path / "m.py", "x\n")
    a = anchors(path)
    E.edit_file(path, edits=[{"after": a[0], "text": "1"}, {"after": a[0], "text": "2"}])
    assert path.read_text(encoding="utf-8") == "x\n1\n2\n"


def test_stale_anchor_rejects_everything_and_shows_fresh_anchors(tmp_path: Path) -> None:
    path = write(tmp_path / "m.py", "alpha\nbeta\ngamma\n")
    a = anchors(path)
    path.write_text("zero\nalpha\nbeta\ngamma\n", encoding="utf-8")  # changed behind our back
    before = path.read_bytes()
    with pytest.raises(E.EditError) as caught:
        E.edit_file(
            path,
            edits=[
                {"op": "replace", "start": a[0], "text": "ALPHA"},
                {"op": "replace", "start": a[1], "text": "BETA"},
            ],
        )
    message = str(caught.value)
    assert message.startswith(f"stale anchor {a[0]}")
    assert "Nothing was changed" in message
    assert f"may now be 2:{a[0].split(':')[1]}" in message  # the moved line is found
    assert "2:" in message and "|alpha" in message
    assert path.read_bytes() == before


def test_stale_anchor_in_a_short_file_shows_all_of_it_and_offers_text(tmp_path: Path) -> None:
    lines = [f"line {n}" for n in range(1, 21)]
    path = write(tmp_path / "m.py", "\n".join(lines) + "\n")
    with pytest.raises(E.EditError) as caught:
        E.edit_file(path, edits=[{"op": "replace", "start": "13:zz", "text": "X"}])
    message = str(caught.value)
    # every line with its fresh anchor, not only the lines around the stale one
    assert "Current lines 1-20:" in message and "|line 1\n" in message and "|line 20" in message
    assert "whole new content as `text`" in message
    long = write(tmp_path / "long.py", "\n".join(f"row {n}" for n in range(1, 201)) + "\n")
    with pytest.raises(E.EditError) as caught_long:
        E.edit_file(long, edits=[{"op": "replace", "start": "100:zz", "text": "X"}])
    assert "Current lines 97-103:" in str(caught_long.value)
    assert "`text`" not in str(caught_long.value)


def test_anchor_past_the_end_is_stale(tmp_path: Path) -> None:
    path = write(tmp_path / "m.py", "a\n")
    with pytest.raises(E.EditError, match="the file has 1 lines"):
        E.edit_file(path, edits=[{"op": "delete", "start": "5:aa"}])


def test_overlapping_edits_are_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "m.py", "a\nb\nc\nd\n")
    a = anchors(path)
    with pytest.raises(E.EditError, match="overlap"):
        E.edit_file(
            path,
            edits=[
                {"op": "replace", "start": a[0], "end": a[2], "text": "x"},
                {"op": "delete", "start": a[2], "end": a[3]},
            ],
        )
    with pytest.raises(E.EditError, match="inserts inside"):
        E.edit_file(
            path,
            edits=[
                {"op": "replace", "start": a[0], "end": a[2], "text": "x"},
                {"op": "insert_after", "start": a[1], "text": "y"},
            ],
        )
    assert path.read_text(encoding="utf-8") == "a\nb\nc\nd\n"


def test_seen_content_catches_a_hash_collision(tmp_path: Path) -> None:
    path = write(tmp_path / "m.py", "a\nb\n")
    a = anchors(path)
    line, digest = a[1].split(":")
    seen = {(int(line), digest): "something else"}  # what the model was shown for 2:xx
    with pytest.raises(E.EditError, match="changed since it was shown"):
        E.edit_file(path, edits=[{"op": "delete", "start": a[1]}], seen=seen)


# -- forgiving input -----------------------------------------------------------------------


def test_forgiving_edit_shapes(tmp_path: Path) -> None:
    path = write(tmp_path / "m.py", "a\nb\nc\n")
    a = anchors(path)
    edits = (
        f'[{{"action": "Insert-After", "anchor": "{a[0]}|a", "content": "after a"}},'
        f' {{"before": {{"line": 3, "hash": "{a[2].split(":")[1].upper()}"}}, "lines": ["pre c"]}},'
        f' {{"range": "{a[1]}-{a[1]}", "new_text": "B"}}]'
    )
    E.edit_file(path, edits=edits)  # a JSON string of edits, aliases, inferred ops
    assert path.read_text(encoding="utf-8") == "a\nafter a\nB\npre c\nc\n"


def test_text_with_and_without_trailing_newline() -> None:
    assert E.text_lines("x\ny\n") == E.text_lines("x\ny") == ["x", "y"]
    assert E.text_lines("") == []
    assert E.text_lines("\n") == [""]
    assert E.text_lines("x\r\ny\r\n") == ["x", "y"]
    assert E.text_lines(["a\nb", "c"]) == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ({"start": "1:aa"}, "say what to do"),
        ({"op": "teleport", "start": "1:aa"}, "unknown op"),
        ({"op": "replace", "start": "1:aa"}, "use op delete"),
        ({"op": "insert"}, "needs `after` or `before`"),
        ({"op": "replace", "start": "3:aa", "end": "1:aa", "text": "x"}, "comes before"),
        ("not an object", "expected an object"),
    ],
)
def test_bad_edits_explain_themselves(edit: object, message: str) -> None:
    with pytest.raises(E.EditError, match=message):
        E.parse_edits([edit])


def test_edits_must_be_a_list(tmp_path: Path) -> None:
    with pytest.raises(E.EditError, match="non-empty list"):
        E.parse_edits([])
    with pytest.raises(E.EditError, match="list of objects"):
        E.parse_edits("[not json")


# -- newlines, encodings, whole files ------------------------------------------------------


def test_crlf_and_missing_final_newline_are_kept(tmp_path: Path) -> None:
    path = write(tmp_path / "w.txt", b"one\r\ntwo\r\nthree")
    a = anchors(path)
    E.edit_file(path, edits=[{"op": "insert_after", "start": a[2], "text": "four\n"}])
    assert path.read_bytes() == b"one\r\ntwo\r\nthree\r\nfour"


def test_mixed_line_endings_are_kept_per_line(tmp_path: Path) -> None:
    path = write(tmp_path / "w.txt", b"a\r\nb\nc\n")
    a = anchors(path)
    E.edit_file(path, edits=[{"op": "replace", "start": a[2], "text": "C"}])
    assert path.read_bytes() == b"a\r\nb\nC\n"


def test_bom_is_kept(tmp_path: Path) -> None:
    path = write(tmp_path / "b.txt", codecs.BOM_UTF8 + b"x\n")
    a = anchors(path)
    E.edit_file(path, edits=[{"op": "replace", "start": a[0], "text": "y"}])
    assert path.read_bytes() == codecs.BOM_UTF8 + b"y\n"


def test_binary_and_non_utf8_files_are_refused(tmp_path: Path) -> None:
    binary = write(tmp_path / "x.bin", b"\x00\x01\x02")
    latin = write(tmp_path / "l.txt", "caf\xe9\n".encode("latin-1"))
    with pytest.raises(E.EditError, match="binary"):
        E.edit_file(binary, edits=[{"op": "delete", "start": "1:aa"}])
    with pytest.raises(E.EditError, match="not valid UTF-8"):
        E.edit_file(latin, edits=[{"op": "delete", "start": "1:aa"}])


def test_create_and_whole_file_replacement(tmp_path: Path) -> None:
    new = tmp_path / "pkg" / "new.py"
    result = E.edit_file(new, display="pkg/new.py", text="x = 1", create=True)
    assert result.created and new.read_text(encoding="utf-8") == "x = 1\n"
    assert result.diff.startswith("--- /dev/null\n+++ b/pkg/new.py")
    with pytest.raises(E.EditError, match="already exists"):
        E.edit_file(new, text="y", create=True)
    E.edit_file(new, text="y = 2\n")  # small file: whole replacement allowed
    assert new.read_text(encoding="utf-8") == "y = 2\n"
    unchanged = E.edit_file(new, text="y = 2")
    assert not unchanged.changed
    big = write(tmp_path / "big.py", "".join(f"x{i} = {i}\n" for i in range(150)))
    with pytest.raises(E.EditError, match="only allowed up to 100 lines"):
        E.edit_file(big, text="x = 0\n")


def test_missing_file_and_conflicting_arguments(tmp_path: Path) -> None:
    with pytest.raises(E.EditError, match="does not exist"):
        E.edit_file(tmp_path / "nope.py", edits=[{"op": "delete", "start": "1:aa"}])
    path = write(tmp_path / "m.py", "a\n")
    with pytest.raises(E.EditError, match="not both"):
        E.edit_file(path, edits=[{"op": "delete", "start": "1:aa"}], text="x")


def test_write_is_atomic_and_leaves_no_temporary_files(tmp_path: Path) -> None:
    path = write(tmp_path / "m.py", "a\n")
    a = anchors(path)
    E.edit_file(path, edits=[{"op": "replace", "start": a[0], "text": "b"}])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.py"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_file_mode_is_kept(tmp_path: Path) -> None:
    path = write(tmp_path / "run.sh", "echo a\n")
    path.chmod(0o755)
    a = anchors(path)
    E.edit_file(path, edits=[{"op": "replace", "start": a[0], "text": "echo b"}])
    assert stat.S_IMODE(path.stat().st_mode) == 0o755


def test_read_only_files_are_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "ro.txt", "a\n")
    path.chmod(stat.S_IREAD)
    try:
        if os.access(path, os.W_OK):
            pytest.skip("running with privileges that ignore read-only bits")
        with pytest.raises(E.EditError, match="read-only"):
            E.edit_file(path, edits=[{"op": "delete", "start": anchors(path)[0]}])
    finally:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_deleting_every_line_leaves_an_empty_file(tmp_path: Path) -> None:
    path = write(tmp_path / "m.py", "a\nb\n")
    a = anchors(path)
    result = E.edit_file(path, edits=[{"op": "delete", "start": a[0], "end": a[1]}])
    assert path.read_bytes() == b""
    assert result.preview == "(the file is now empty)"
